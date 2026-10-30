"""
OCPVirt baseline selection: RHEL → datasource map + compose kernel gate.

1. Map fix version to datasource://… (config)
2. Fetch guest-image URL from GitLab DataVolume YAML
3. Scrape compose Packages for baseline kernel NVR
4. Require baseline_kernel < JIRA CKI kernel (same gate as RHOS-D)
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
        self.compose = ComposeKernelResolver()
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

    def build_baselines_for_commands(
        self,
        commands: List[Dict[str, Any]],
        jira_by_id: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """
        Build per-JIRA baseline records for Phase 8.

        Included only when:
        - datasource mapping exists
        - inventory exists (non-RHEL-8)
        - compose baseline kernel is found
        - JIRA CKI kernel is present
        - baseline_kernel < jira_kernel
        """
        jira_by_id = jira_by_id or {}
        baselines: Dict[str, Dict[str, Any]] = {}

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

            datasource, matched_key = self.resolve_datasource(fix_versions)
            if not datasource:
                self.logger.warning(
                    f"  ⚠️  {jira_id}: No OCPVirt datasource mapping for "
                    f"RHEL {fix_versions or 'unknown'}"
                )
                continue

            inventory = cmd.get("inventory") or self.resolve_inventory_for_version(
                fix_versions, is_rhel8=is_rhel8
            )
            if not inventory and not is_rhel8:
                self.logger.warning(
                    f"  ⚠️  {jira_id}: datasource {datasource} mapped but no "
                    f"ocpvirt inventory for {fix_versions}"
                )
                continue

            if not jira_kernel or jira_kernel.upper() == "N/A":
                self.logger.warning(
                    f"  ⚠️  {jira_id}: No JIRA CKI kernel — cannot validate baseline"
                )
                continue

            self.logger.info(
                f"  🔍 {jira_id}: resolving kernel for {datasource} "
                f"(JIRA kernel: {jira_kernel})"
            )
            guest_url, baseline_kernel = self.resolve_baseline_kernel(datasource)
            if not baseline_kernel:
                self.logger.warning(
                    f"  ⚠️  {jira_id}: No baseline kernel from compose "
                    f"(guest URL: {guest_url or 'N/A'})"
                )
                continue

            cmp = self.compose.compare_kernel_versions(baseline_kernel, jira_kernel)
            if cmp >= 0:
                self.logger.warning(
                    f"  ⚠️  {jira_id}: baseline kernel {baseline_kernel} "
                    f">= JIRA kernel {jira_kernel} — skipping "
                    f"(need strictly older baseline)"
                )
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
        )
        return baselines
