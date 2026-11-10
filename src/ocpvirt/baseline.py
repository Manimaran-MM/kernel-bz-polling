"""
OCPVirt baseline selection: RHEL → datasource map + compose kernel gate.

1. Map fix version to datasource://… (config)
2. Fetch guest-image URL from GitLab DataVolume YAML
3. Scrape compose Packages for baseline kernel NVR
4. Require baseline_kernel < JIRA CKI kernel (same gate as RHOS-D)
5. If the mapped DataSource is too new, do NOT fall back to HTTP import —
   report baseline unmet and the expected older guest URL for validation.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from src.ocpvirt.compose_kernel import ComposeKernelResolver
from src.ocpvirt.gitlab_client import GitLabDataVolumeClient
from src.utils.config import get_config
from src.utils.logger import get_logger


class BaselineMapper:
    """Map JIRA fix versions to ocpvirt baselines with kernel validation."""

    def __init__(self):
        self.logger = get_logger(__name__)
        self.config = get_config()
        self.datasource_mapping: Dict[str, str] = self.config.get(
            "cephci.datasource_mapping", {}
        ) or {}
        self.inventory_mapping: Dict[str, str] = self.config.get(
            "cephci.inventory_mapping", {}
        ) or {}
        self.gitlab = GitLabDataVolumeClient()
        self.compose = ComposeKernelResolver(
            compose_releng=self.config.get("ocpvirt.compose_releng", {}) or {}
        )
        # Cache: datasource → (guest_url, kernel) so we don't re-fetch per JIRA
        self._kernel_cache: Dict[str, Tuple[Optional[str], Optional[str]]] = {}

    @staticmethod
    def parse_rhel_keys(fix_versions: List[str]) -> List[str]:
        """Return lookup keys for fix versions: major.minor then major."""
        keys: List[str] = []
        for version in fix_versions or []:
            clean = version.replace("rhel-", "").replace("RHEL-", "")
            parts = clean.split(".")
            if len(parts) >= 2 and parts[0].isdigit():
                minor = f"{parts[0]}.{parts[1]}"
                if minor not in keys:
                    keys.append(minor)
                if parts[0] not in keys:
                    keys.append(parts[0])
            elif parts and parts[0].isdigit():
                if parts[0] not in keys:
                    keys.append(parts[0])
        return keys

    def resolve_datasource(
        self, fix_versions: List[str]
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Resolve baseline datasource for fix versions.

        Returns:
            (datasource_url, matched_key) or (None, None) if unmapped.
        """
        for key in self.parse_rhel_keys(fix_versions):
            if key in self.datasource_mapping:
                return self.datasource_mapping[key], key
        return None, None

    def resolve_inventory_for_version(
        self, fix_versions: List[str], is_rhel8: bool = False
    ) -> Optional[str]:
        """Inventory path for the fix version (RHEL-8 → first RHEL-9 map entry)."""
        if is_rhel8:
            for key in ("9.4", "9.6", "9"):
                if key in self.inventory_mapping:
                    return self.inventory_mapping[key]
            return None
        for key in self.parse_rhel_keys(fix_versions):
            if key in self.inventory_mapping:
                return self.inventory_mapping[key]
        return None

    def resolve_baseline_kernel(
        self, datasource: str
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Return (guest_image_url, baseline_kernel) for a datasource alias.

        Results are cached per datasource string.
        """
        if datasource in self._kernel_cache:
            return self._kernel_cache[datasource]

        guest_url = self.gitlab.get_guest_image_url(datasource)
        if not guest_url:
            self._kernel_cache[datasource] = (None, None)
            return None, None

        kernel = self.compose.fetch_kernel_from_guest_url(guest_url)
        self._kernel_cache[datasource] = (guest_url, kernel)
        return guest_url, kernel

    def _lookup_expected_older_url(
        self, guest_url: Optional[str], jira_kernel: str
    ) -> Tuple[Optional[str], Optional[str]]:
        """Find an older compose guest URL/kernel for operator validation (not for CDI)."""
        if not guest_url:
            return None, None
        return self.compose.find_older_baseline(
            guest_url, jira_kernel, skip_guest_url=guest_url
        )

    def build_baselines_for_commands(
        self,
        commands: List[Dict[str, Any]],
        jira_by_id: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
        """
        Build per-JIRA baseline records for Phase 8.

        Returns:
            (accepted, unmet) where:
            - accepted: JIRAs whose mapped DataSource kernel is < JIRA kernel
              (baseline_image is always datasource://…)
            - unmet: JIRAs that cannot run; includes expected_baseline_url when
              an older compose guest image was found for manual validation
        """
        jira_by_id = jira_by_id or {}
        baselines: Dict[str, Dict[str, Any]] = {}
        unmet: Dict[str, Dict[str, Any]] = {}

        for cmd in commands or []:
            jira_id = cmd.get("jira_id")
            if not jira_id:
                continue

            jira_data = jira_by_id.get(jira_id, {})
            fix_versions = jira_data.get("fix_versions") or []
            if not fix_versions and cmd.get("fix_version"):
                fix_versions = [cmd["fix_version"]]

            is_rhel8 = bool(cmd.get("is_rhel8"))
            jira_kernel = (
                jira_data.get("repo_kernel_version")
                or cmd.get("jira_kernel")
                or ""
            )
            jira_kernel = str(jira_kernel).strip() if jira_kernel else ""

            rhel_keys = self.parse_rhel_keys(fix_versions)
            datasource, matched_key = self.resolve_datasource(fix_versions)
            if not datasource:
                self.logger.warning(
                    f"  ⚠️  {jira_id}: No OCPVirt datasource mapping for "
                    f"RHEL {fix_versions or 'unknown'}"
                )
                # Still try compose_releng for an older same-stream guest URL so
                # Phase 7.5 can provision a *-custom DV + runtime inventory.
                expected_url, expected_kernel = None, None
                if jira_kernel and jira_kernel.upper() != "N/A":
                    expected_url, expected_kernel = self.compose.find_older_baseline(
                        None, jira_kernel
                    )
                    if expected_url:
                        self.logger.info(
                            f"  📎 {jira_id}: no shared datasource — will try "
                            f"custom DV from {expected_url}"
                            + (
                                f" (kernel {expected_kernel})"
                                if expected_kernel
                                else ""
                            )
                        )
                    else:
                        self.logger.warning(
                            f"  📎 {jira_id}: no older compose guest URL found "
                            f"for unmapped RHEL {fix_versions}"
                        )
                rhel_key = rhel_keys[0] if rhel_keys else None
                # Prefer major.minor from kernel el tag when fix version is fuzzy
                stream = self.compose.rhel_stream_from_kernel(jira_kernel)
                if stream:
                    rhel_key = f"{stream[0]}.{stream[1]}"
                unmet[jira_id] = {
                    "reason": "no_datasource_mapping",
                    "fix_versions": fix_versions,
                    "jira_kernel": jira_kernel or None,
                    "rhel_key": rhel_key,
                    "is_rhel8": is_rhel8,
                    "expected_baseline_url": expected_url,
                    "expected_baseline_kernel": expected_kernel,
                }
                continue

            inventory = cmd.get("inventory") or self.resolve_inventory_for_version(
                fix_versions, is_rhel8=is_rhel8
            )
            if not inventory and not is_rhel8:
                self.logger.warning(
                    f"  ⚠️  {jira_id}: datasource {datasource} mapped but no "
                    f"ocpvirt inventory for {fix_versions}"
                )
                unmet[jira_id] = {
                    "reason": "no_inventory",
                    "baseline_datasource": datasource,
                    "fix_versions": fix_versions,
                    "jira_kernel": jira_kernel or None,
                    "expected_baseline_url": None,
                    "expected_baseline_kernel": None,
                }
                continue

            if not jira_kernel or jira_kernel.upper() == "N/A":
                self.logger.warning(
                    f"  ⚠️  {jira_id}: No JIRA CKI kernel — cannot validate baseline"
                )
                unmet[jira_id] = {
                    "reason": "no_jira_kernel",
                    "baseline_datasource": datasource,
                    "inventory": inventory,
                    "expected_baseline_url": None,
                    "expected_baseline_kernel": None,
                }
                continue

            self.logger.info(
                f"  🔍 {jira_id}: resolving kernel for {datasource} "
                f"(JIRA kernel: {jira_kernel})"
            )
            guest_url, baseline_kernel = self.resolve_baseline_kernel(datasource)

            if not baseline_kernel:
                self.logger.warning(
                    f"  ⚠️  {jira_id}: No baseline kernel from compose "
                    f"(guest URL: {guest_url or 'N/A'}) — baseline not met"
                )
                expected_url, expected_kernel = self._lookup_expected_older_url(
                    guest_url, jira_kernel
                )
                if expected_url:
                    self.logger.warning(
                        f"  ❌ {jira_id}: baseline not met for {datasource}. "
                        f"Expected baseline URL for validation: {expected_url}"
                        + (
                            f" (kernel {expected_kernel})"
                            if expected_kernel
                            else ""
                        )
                    )
                else:
                    self.logger.warning(
                        f"  ❌ {jira_id}: baseline not met for {datasource}; "
                        f"could not resolve an older guest URL for validation"
                    )
                unmet[jira_id] = {
                    "reason": "no_compose_kernel",
                    "baseline_datasource": datasource,
                    "guest_image_url": guest_url,
                    "inventory": inventory,
                    "jira_kernel": jira_kernel,
                    "rhel_key": matched_key,
                    "is_rhel8": is_rhel8,
                    "expected_baseline_url": expected_url,
                    "expected_baseline_kernel": expected_kernel,
                }
                continue

            # Same RHEL base release (ignoring CKI brew suffix) is not usable
            cmp = self.compose.compare_kernel_base_versions(
                baseline_kernel, jira_kernel
            )
            if cmp >= 0:
                expected_url, expected_kernel = self._lookup_expected_older_url(
                    guest_url, jira_kernel
                )
                self.logger.warning(
                    f"  ❌ {jira_id}: baseline not met — {datasource} kernel "
                    f"{baseline_kernel} >= JIRA kernel {jira_kernel}"
                )
                if expected_url:
                    self.logger.warning(
                        f"  📎 {jira_id}: expected baseline URL for validation: "
                        f"{expected_url}"
                        + (
                            f" (kernel {expected_kernel})"
                            if expected_kernel
                            else ""
                        )
                    )
                else:
                    self.logger.warning(
                        f"  📎 {jira_id}: no older compose guest URL found "
                        f"with kernel < {jira_kernel}"
                    )
                unmet[jira_id] = {
                    "reason": "baseline_kernel_too_new",
                    "baseline_datasource": datasource,
                    "baseline_kernel": baseline_kernel,
                    "guest_image_url": guest_url,
                    "inventory": inventory,
                    "jira_kernel": jira_kernel,
                    "rhel_key": matched_key,
                    "is_rhel8": is_rhel8,
                    "expected_baseline_url": expected_url,
                    "expected_baseline_kernel": expected_kernel,
                }
                continue

            record = {
                "inventory": inventory,
                "baseline_image": datasource,
                "baseline_kernel": baseline_kernel,
                "baseline_datasource": datasource,
                "guest_image_url": guest_url,
                "jira_kernel": jira_kernel,
                "is_rhel8": is_rhel8,
                "rhel_key": matched_key,
                "baseline_source": "datasource",
            }
            if is_rhel8:
                record["global_conf"] = cmd.get("global_conf")

            baselines[jira_id] = record
            self.logger.info(
                f"  ✅ {jira_id}: baseline {datasource} "
                f"(kernel {baseline_kernel} < {jira_kernel})"
            )

        self.logger.info(
            f"✅ Validated baselines for {len(baselines)}/{len(commands or [])} JIRAs"
            f" ({len(unmet)} unmet)"
        )
        return baselines, unmet
