"""
Provision custom RHEL guest-image DataVolumes via create-guest-image.sh.

Used when the shared OCPVirt DataSource kernel is too new for a JIRA: upload
an older compose qcow2 as ``rhelXY-iso-custom`` into the configured project
(e.g. ceph-jenkins), without touching shared DV names.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import yaml

from src.ocpvirt.gitlab_client import GitLabDataVolumeClient
from src.utils.config import get_config
from src.utils.logger import get_logger

GUEST_IMAGE_VERSION_RE = re.compile(
    r"rhel-guest-image-(\d+\.\d+)(?:-|\.|_)", re.IGNORECASE
)


class CustomDataVolumeProvisioner:
    """Run GitLab create-guest-image.sh generate/apply for *-custom DVs."""

    SCRIPT_REL = "guest-images/scripts/create-guest-image.sh"
    DV_TMPL_REL = "guest-images/templates/guest-iso.datavolume.yaml.tmpl"
    DS_TMPL_REL = "guest-images/templates/guest-iso.datasource.yaml.tmpl"

    def __init__(self, cephci_root: str = "cephci"):
        self.logger = get_logger(__name__)
        self.config = get_config()
        self.cephci_root = Path(cephci_root)
        self.gitlab = GitLabDataVolumeClient()

        cb = self.config.get("ocpvirt.custom_baseline", {}) or {}
        self.enabled = bool(cb.get("enabled", False))
        self.project = str(cb.get("project") or "ceph-jenkins").strip()
        self.name_suffix = str(cb.get("name_suffix") or "-custom")
        self.repo_path = Path(cb.get("repo_path") or "data/cephci-openshift")
        self.replace_on_url_mismatch = bool(cb.get("replace_on_url_mismatch", True))
        self.runtime_namespace = f"{self.project}--runtime-int"
        self._kubeconfig: Optional[Path] = None
        self._provision_cache: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Config / naming helpers
    # ------------------------------------------------------------------

    def log_configuration(self) -> None:
        """Display the custom_baseline configuration that will be used."""
        script = self.repo_path / self.SCRIPT_REL
        self.logger.info("=" * 60)
        self.logger.info("Custom baseline DataVolume configuration")
        self.logger.info("=" * 60)
        self.logger.info(f"  enabled:                 {self.enabled}")
        self.logger.info(f"  project:                 {self.project}")
        self.logger.info(f"  runtime namespace:        {self.runtime_namespace}")
        self.logger.info(f"  name_suffix:             {self.name_suffix}")
        self.logger.info(f"  repo_path:               {self.repo_path}")
        self.logger.info(f"  replace_on_url_mismatch: {self.replace_on_url_mismatch}")
        self.logger.info(f"  script:                  {script}")
        self.logger.info("=" * 60)

    @staticmethod
    def version_from_guest_url(url: str) -> Optional[str]:
        """Extract X.Y from rhel-guest-image-X.Y-… in a compose URL."""
        if not url:
            return None
        path = urlparse(url).path
        m = GUEST_IMAGE_VERSION_RE.search(path)
        if m:
            return m.group(1)
        # Fallback: …/latest-RHEL-10.2/…
        m2 = re.search(r"RHEL-(\d+\.\d+)", path, re.IGNORECASE)
        return m2.group(1) if m2 else None

    @staticmethod
    def version_to_base_name(version: str) -> str:
        """9.5 → rhel95-iso (same as create-guest-image.sh --distro rhel)."""
        nodot = version.replace(".", "")
        return f"rhel{nodot}-iso"

    def custom_name_for_version(self, version: str) -> str:
        return f"{self.version_to_base_name(version)}{self.name_suffix}"

    def baseline_image_for_name(self, custom_name: str) -> str:
        return f"datasource://{self.runtime_namespace}/{custom_name}"

    def ensure_runtime_inventory(
        self, version: str, baseline_image: str
    ) -> Optional[str]:
        """
        Ensure conf/inventory/ocpvirt-rhel-X.Y.yaml exists with baseline_image.

        Clones the closest ocpvirt inventory template when missing, then stamps
        image-name to the custom datasource. Returns the relative inventory path.
        """
        from src.cephci.inventory_generator import InventoryGenerator

        if not version or "." not in version:
            self.logger.error(
                f"Cannot generate inventory without major.minor version: {version!r}"
            )
            return None

        rel_path = f"conf/inventory/ocpvirt-rhel-{version}.yaml"
        gen = InventoryGenerator(str(self.cephci_root))
        generated = gen.generate_inventory_file(version)
        if not generated:
            self.logger.error(
                f"Failed to generate inventory for RHEL {version}"
            )
            return None

        inv_path = Path(generated)
        try:
            with open(inv_path) as f:
                data = yaml.safe_load(f) or {}
            create = data.setdefault("instance", {}).setdefault("create", {})
            current = create.get("image-name")
            if current != baseline_image:
                create["image-name"] = baseline_image
                with open(inv_path, "w") as f:
                    yaml.dump(data, f, default_flow_style=False, sort_keys=False)
                self.logger.info(
                    f"  🔄 Runtime inventory image-name: "
                    f"'{current}' -> '{baseline_image}' ({rel_path})"
                )
            else:
                self.logger.info(
                    f"  ✅ Runtime inventory already uses {baseline_image} "
                    f"({rel_path})"
                )
            return rel_path
        except Exception as e:
            self.logger.error(f"Failed to stamp inventory {inv_path}: {e}")
            return None

    def plan_for_url(
        self, guest_url: str, jira_ids: Optional[List[str]] = None
    ) -> Optional[Dict[str, Any]]:
        """Build a provision plan dict, or None if version cannot be derived."""
        version = self.version_from_guest_url(guest_url)
        if not version:
            return None
        base_name = self.version_to_base_name(version)
        custom_name = self.custom_name_for_version(version)
        # Absolute path required: _run() chdirs into repo_path, so a relative
        # data/cephci-openshift/... path would resolve to a nested missing file.
        script = (self.repo_path / self.SCRIPT_REL).resolve()
        generate_cmd = [
            str(script),
            "generate",
            "--distro",
            "rhel",
            "--version",
            version,
            "--url",
            guest_url,
        ]
        apply_cmd = [
            str(script),
            "apply",
            "--name",
            custom_name,
            "--project",
            self.project,
        ]
        return {
            "version": version,
            "base_name": base_name,
            "custom_name": custom_name,
            "guest_url": guest_url,
            "jira_ids": list(jira_ids or []),
            "baseline_image": self.baseline_image_for_name(custom_name),
            "generate_cmd": generate_cmd,
            "apply_cmd": apply_cmd,
            "runtime_namespace": self.runtime_namespace,
            "project": self.project,
        }

    def log_plan(self, plan: Dict[str, Any]) -> None:
        jiras = ", ".join(plan.get("jira_ids") or []) or "(none)"
        self.logger.info("-" * 60)
        self.logger.info("Custom DV planned action")
        self.logger.info(f"  JIRA IDs:          {jiras}")
        self.logger.info(f"  version:           {plan['version']}")
        self.logger.info(
            f"  name:              {plan['base_name']} → {plan['custom_name']}"
        )
        self.logger.info(f"  guest URL:         {plan['guest_url']}")
        self.logger.info(f"  project:           {plan['project']}")
        self.logger.info(f"  namespace:         {plan['runtime_namespace']}")
        self.logger.info(f"  baseline_image:    {plan['baseline_image']}")
        self.logger.info(
            "  generate:          " + " ".join(plan["generate_cmd"])
        )
        apply_display = list(plan["apply_cmd"])
        if plan.get("will_replace"):
            apply_display.append("--replace")
        self.logger.info("  apply:             " + " ".join(apply_display))
        self.logger.info("-" * 60)

    # ------------------------------------------------------------------
    # Workdir / script bootstrap
    # ------------------------------------------------------------------

    def ensure_script_workdir(self) -> bool:
        """Fetch script + templates from GitLab into repo_path (minimal tree)."""
        root = self.repo_path
        images = root / "guest-images"
        scripts = images / "scripts"
        templates = images / "templates"
        scripts.mkdir(parents=True, exist_ok=True)
        templates.mkdir(parents=True, exist_ok=True)

        files = {
            self.SCRIPT_REL: scripts / "create-guest-image.sh",
            self.DV_TMPL_REL: templates / "guest-iso.datavolume.yaml.tmpl",
            self.DS_TMPL_REL: templates / "guest-iso.datasource.yaml.tmpl",
        }
        for rel, dest in files.items():
            content = self.gitlab.fetch_file(rel)
            if not content:
                self.logger.error(f"Failed to fetch GitLab file: {rel}")
                return False
            dest.write_text(content)
            if dest.suffix == ".sh":
                dest.chmod(dest.stat().st_mode | 0o111)
            self.logger.info(f"  📥 Wrote {dest}")
        return True

    # ------------------------------------------------------------------
    # oc / kubeconfig
    # ------------------------------------------------------------------

    def _load_ocpvirt_server(self) -> Optional[str]:
        name = self.config.get("ocpvirt.namespace", "rdu3_ceph_jenkins")
        path = self.cephci_root / "conf" / "ocpvirt" / f"{name}.yaml"
        if not path.exists():
            self.logger.error(f"OCPVirt namespace template not found: {path}")
            return None
        try:
            with open(path) as f:
                data = yaml.safe_load(f) or {}
            server = (data.get("server") or "").rstrip("/")
            return server or None
        except Exception as e:
            self.logger.error(f"Failed to read OCPVirt server from {path}: {e}")
            return None

    def ensure_kubeconfig(self) -> bool:
        """Write a kubeconfig from OCPVIRT_TOKEN for the create script's oc calls."""
        if shutil.which("oc") is None:
            self.logger.error(
                "'oc' not found on PATH — required for create-guest-image.sh apply"
            )
            return False
        if shutil.which("envsubst") is None:
            self.logger.error("'envsubst' not found on PATH — required by the script")
            return False

        token = (
            os.environ.get("OCPVIRT_TOKEN")
            or self.config.get("ocpvirt.token")
            or ""
        ).strip()
        if not token:
            self.logger.error("OCPVIRT_TOKEN not set — cannot run oc apply")
            return False

        server = self._load_ocpvirt_server()
        if not server:
            return False

        ca_file = (
            os.environ.get("OCPVIRT_SSL_CA_CERT")
            or self.config.get("ocpvirt.ssl_ca_cert")
            or ""
        )
        ca_path = Path(ca_file).expanduser() if ca_file else None
        ca_data = (
            os.environ.get("OCPVIRT_CERTIFICATE_AUTHORITY_DATA")
            or self.config.get("ocpvirt.certificate_authority_data")
            or ""
        ).strip()

        kube_dir = Path("data")
        kube_dir.mkdir(parents=True, exist_ok=True)
        kubeconfig = kube_dir / "ocpvirt.kubeconfig"

        cluster_extra = ""
        if ca_path and ca_path.exists():
            cluster_extra = f"certificate-authority: {ca_path}"
        elif ca_data:
            cluster_extra = f"certificate-authority-data: {ca_data}"
        else:
            cluster_extra = "insecure-skip-tls-verify: true"

        kubeconfig.write_text(
            f"""apiVersion: v1
kind: Config
clusters:
- cluster:
    server: {server}
    {cluster_extra}
  name: ocpvirt
contexts:
- context:
    cluster: ocpvirt
    user: ocpvirt
    namespace: {self.runtime_namespace}
  name: ocpvirt
current-context: ocpvirt
users:
- name: ocpvirt
  user:
    token: {token}
"""
        )
        self._kubeconfig = kubeconfig
        os.environ["KUBECONFIG"] = str(kubeconfig.resolve())
        self.logger.info(f"  🔑 KUBECONFIG set to {kubeconfig} (server={server})")
        return True

    def _oc_env(self) -> Dict[str, str]:
        env = os.environ.copy()
        if self._kubeconfig:
            env["KUBECONFIG"] = str(self._kubeconfig.resolve())
        return env

    def _run(
        self, cmd: List[str], timeout: Optional[int] = None
    ) -> subprocess.CompletedProcess:
        self.logger.info(f"  ▶️  {' '.join(cmd)}")
        repo = self.repo_path.resolve()
        # Invoke via bash explicitly so UBI images without /bin/bash shebang
        # resolution quirks still run the GitLab .sh script.
        run_cmd = list(cmd)
        if run_cmd and run_cmd[0].endswith(".sh"):
            run_cmd = ["bash", *run_cmd]
        return subprocess.run(
            run_cmd,
            cwd=str(repo),
            env=self._oc_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def _log_proc(self, proc: subprocess.CompletedProcess) -> None:
        if proc.stdout:
            for line in proc.stdout.strip().splitlines():
                self.logger.info(f"     {line}")
        if proc.stderr:
            for line in proc.stderr.strip().splitlines():
                level = self.logger.error if proc.returncode else self.logger.warning
                level(f"     {line}")

    # ------------------------------------------------------------------
    # Cluster DV inspection
    # ------------------------------------------------------------------

    def existing_dv_url(self, custom_name: str) -> Optional[str]:
        """Return current DV HTTP URL if the resource exists, else None."""
        cmd = [
            "oc",
            "get",
            "dv",
            custom_name,
            "-n",
            self.runtime_namespace,
            "-o",
            "jsonpath={.spec.source.http.url}",
        ]
        try:
            proc = self._run(cmd, timeout=60)
        except Exception as e:
            self.logger.debug(f"oc get dv failed: {e}")
            return None
        if proc.returncode != 0:
            return None
        url = (proc.stdout or "").strip()
        return url or None

    def existing_dv_phase(self, custom_name: str) -> Optional[str]:
        cmd = [
            "oc",
            "get",
            "dv",
            custom_name,
            "-n",
            self.runtime_namespace,
            "-o",
            "jsonpath={.status.phase}",
        ]
        try:
            proc = self._run(cmd, timeout=60)
        except Exception:
            return None
        if proc.returncode != 0:
            return None
        return (proc.stdout or "").strip() or None

    # ------------------------------------------------------------------
    # generate / rename / apply
    # ------------------------------------------------------------------

    def _images_dir(self) -> Path:
        return self.repo_path / "guest-images"

    def _remove_yaml_pair(self, name: str) -> None:
        images = self._images_dir()
        for suffix in (".datavolume.yaml", ".datasource.yaml"):
            path = images / f"{name}{suffix}"
            if path.exists():
                path.unlink()
                self.logger.info(f"  🗑️  Removed {path.name}")

    def _rename_generated_to_custom(self, base_name: str, custom_name: str) -> bool:
        """Rewrite generated YAML names from base → custom (script has no --suffix)."""
        images = self._images_dir()
        for kind in ("datavolume", "datasource"):
            src = images / f"{base_name}.{kind}.yaml"
            dst = images / f"{custom_name}.{kind}.yaml"
            if not src.exists():
                self.logger.error(f"Missing generated file: {src}")
                return False
            text = src.read_text()
            # Replace exact resource name occurrences from the script output
            text = text.replace(f"name: {base_name}", f"name: {custom_name}")
            text = text.replace(f"app: {base_name}", f"app: {custom_name}")
            # DataSource PVC name must match DV/PVC name
            text = text.replace(f"name: {base_name}\n", f"name: {custom_name}\n")
            dst.write_text(text)
            src.unlink()
            self.logger.info(f"  📝 Renamed {src.name} → {dst.name}")
        return True

    def generate(self, plan: Dict[str, Any]) -> bool:
        base = plan["base_name"]
        custom = plan["custom_name"]
        # generate refuses overwrite of base files; custom files are ours to refresh
        self._remove_yaml_pair(base)
        self._remove_yaml_pair(custom)
        proc = self._run(plan["generate_cmd"], timeout=120)
        self._log_proc(proc)
        if proc.returncode != 0:
            self.logger.error("generate failed")
            return False
        if not self._rename_generated_to_custom(base, custom):
            return False
        return True

    def apply(self, plan: Dict[str, Any], replace: bool = False) -> bool:
        cmd = list(plan["apply_cmd"])
        if replace:
            cmd.append("--replace")
        # apply waits up to 30m for DV Succeeded
        proc = self._run(cmd, timeout=2000)
        self._log_proc(proc)
        if proc.returncode != 0:
            self.logger.error("apply failed")
            return False
        return True

    def provision(self, plan: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Ensure custom DV/DS exists for plan; return result dict or None on failure.
        """
        custom_name = plan["custom_name"]
        guest_url = plan["guest_url"]
        cache_key = f"{custom_name}|{guest_url}"
        if cache_key in self._provision_cache:
            self.logger.info(
                f"  ✅ Reusing provisioned custom DV from this run: {custom_name}"
            )
            return self._provision_cache[cache_key]

        existing_url = self.existing_dv_url(custom_name)
        phase = self.existing_dv_phase(custom_name) if existing_url else None
        replace = False
        if existing_url == guest_url and phase == "Succeeded":
            self.logger.info(
                f"  ✅ Custom DV already Succeeded with matching URL: {custom_name}"
            )
            result = {
                "custom_name": custom_name,
                "baseline_image": plan["baseline_image"],
                "guest_image_url": guest_url,
                "reused": True,
                "replaced": False,
            }
            self._provision_cache[cache_key] = result
            return result
        if existing_url and existing_url != guest_url:
            if not self.replace_on_url_mismatch:
                self.logger.error(
                    f"  ❌ Custom DV {custom_name} exists with different URL and "
                    f"replace_on_url_mismatch=false"
                )
                return None
            self.logger.warning(
                f"  ⚠️  Custom DV {custom_name} URL mismatch — will apply --replace"
            )
            replace = True
        elif existing_url and phase != "Succeeded":
            self.logger.warning(
                f"  ⚠️  Custom DV {custom_name} phase={phase} — will apply --replace"
            )
            replace = True

        plan = dict(plan)
        plan["will_replace"] = replace
        self.log_plan(plan)

        if not self.generate(plan):
            return None
        if not self.apply(plan, replace=replace):
            return None

        result = {
            "custom_name": custom_name,
            "baseline_image": plan["baseline_image"],
            "guest_image_url": guest_url,
            "reused": False,
            "replaced": replace,
        }
        self._provision_cache[cache_key] = result
        self.logger.info(
            f"  ✅ Custom DV ready: {custom_name} → {plan['baseline_image']}"
        )
        return result


def provision_custom_baselines_for_unmet(
    unmet: Dict[str, Dict[str, Any]],
    commands: List[Dict[str, Any]],
    logger=None,
    cephci_root: str = "cephci",
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """
    For eligible unmet JIRAs, provision *-custom DVs and return promoted baselines
    plus remaining unmet.

    Returns:
        (promoted_baselines, remaining_unmet)
    """
    log = logger or get_logger(__name__)
    provisioner = CustomDataVolumeProvisioner(cephci_root=cephci_root)
    provisioner.log_configuration()

    if not provisioner.enabled:
        log.info("custom_baseline.enabled=false — skipping custom DV provision")
        return {}, dict(unmet or {})

    # Group eligible unmet by guest URL
    # - baseline_kernel_too_new: shared DS too new, custom DV replaces image
    # - no_datasource_mapping: no shared DS/inventory — custom DV + runtime inventory
    eligible_reasons = {"baseline_kernel_too_new", "no_datasource_mapping"}
    groups: Dict[str, List[str]] = {}
    meta: Dict[str, Dict[str, Any]] = {}
    remaining: Dict[str, Dict[str, Any]] = {}

    for jira_id, urec in (unmet or {}).items():
        reason = urec.get("reason")
        expected_url = urec.get("expected_baseline_url")
        if reason not in eligible_reasons or not expected_url:
            remaining[jira_id] = urec
            continue
        groups.setdefault(expected_url, []).append(jira_id)
        meta[jira_id] = urec

    if not groups:
        log.info("No unmet JIRAs eligible for custom DV provision")
        return {}, remaining

    log.info(
        f"Eligible for custom DV: {sum(len(v) for v in groups.values())} JIRA(s), "
        f"{len(groups)} unique guest URL(s)"
    )

    if not provisioner.ensure_script_workdir():
        log.error("Could not prepare create-guest-image.sh workdir")
        remaining.update({jid: meta[jid] for ids in groups.values() for jid in ids})
        return {}, remaining

    if not provisioner.ensure_kubeconfig():
        log.error("Could not prepare oc kubeconfig for custom DV apply")
        remaining.update({jid: meta[jid] for ids in groups.values() for jid in ids})
        return {}, remaining

    cmd_by_id = {c.get("jira_id"): c for c in (commands or []) if c.get("jira_id")}
    promoted: Dict[str, Dict[str, Any]] = {}

    for guest_url, jira_ids in groups.items():
        plan = provisioner.plan_for_url(guest_url, jira_ids=jira_ids)
        if not plan:
            log.error(
                f"Could not derive RHEL version from expected URL for {jira_ids}: "
                f"{guest_url}"
            )
            for jid in jira_ids:
                remaining[jid] = meta[jid]
            continue

        # Log inventory targets before provision
        for jid in jira_ids:
            cmd = cmd_by_id.get(jid) or {}
            urec = meta[jid]
            inv = urec.get("inventory") or cmd.get("inventory")
            gc = cmd.get("global_conf")
            log.info(
                f"  📎 {jid}: after apply, inventory image-name will be "
                f"{plan['baseline_image']}"
                + (f" (inventory={inv})" if inv else "")
                + (f" (global_conf={gc})" if urec.get("is_rhel8") and gc else "")
            )

        result = provisioner.provision(plan)
        if not result:
            for jid in jira_ids:
                remaining[jid] = meta[jid]
            continue

        # Generate ocpvirt-rhel-X.Y.yaml when unmapped (no shared DS/inventory).
        # Mapped baseline_kernel_too_new keeps existing inventory; Phase 8 stamps
        # image-name via apply_baseline_to_cephci.
        needs_runtime_inv = any(
            meta[jid].get("reason") == "no_datasource_mapping"
            or not (
                meta[jid].get("inventory")
                or (cmd_by_id.get(jid) or {}).get("inventory")
            )
            for jid in jira_ids
        )
        runtime_inventory: Optional[str] = None
        if needs_runtime_inv:
            runtime_inventory = provisioner.ensure_runtime_inventory(
                plan["version"], result["baseline_image"]
            )
            if not runtime_inventory:
                log.error(
                    f"Custom DV ready but failed to create runtime inventory "
                    f"for RHEL {plan['version']} — not promoting {jira_ids}"
                )
                for jid in jira_ids:
                    remaining[jid] = meta[jid]
                continue

        for jid in jira_ids:
            urec = meta[jid]
            cmd = cmd_by_id.get(jid) or {}
            inventory = (
                runtime_inventory
                or urec.get("inventory")
                or cmd.get("inventory")
            )
            record = {
                "inventory": inventory,
                "baseline_image": result["baseline_image"],
                "baseline_kernel": urec.get("expected_baseline_kernel"),
                "baseline_datasource": urec.get("baseline_datasource")
                or result["baseline_image"],
                "guest_image_url": guest_url,
                "jira_kernel": urec.get("jira_kernel"),
                "is_rhel8": urec.get("is_rhel8", cmd.get("is_rhel8", False)),
                "rhel_key": urec.get("rhel_key") or plan["version"],
                "baseline_source": "custom_datavolume",
                "custom_dv_name": result["custom_name"],
                "custom_dv_project": provisioner.project,
            }
            if record["is_rhel8"]:
                record["global_conf"] = cmd.get("global_conf") or urec.get("global_conf")
            promoted[jid] = record
            log.info(
                f"  ✅ {jid}: promoted with custom baseline {result['baseline_image']}"
                + (f" inventory={inventory}" if inventory else "")
            )

    return promoted, remaining
