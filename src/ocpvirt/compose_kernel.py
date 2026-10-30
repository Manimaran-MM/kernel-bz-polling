"""
Resolve kernel NVR from a RHEL compose guest-image URL (Packages listing).

Mirrors the RHOS-D Glance compose scrape used previously in src/rhosd/image_api.py.
"""

from __future__ import annotations

import re
from typing import List, Optional

import requests
import urllib3

from src.utils.logger import get_logger

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

KERNEL_RPM_RE = re.compile(
    r"kernel-(\d+\.\d+\.\d+-\d+(?:\.\d+)*\.el\d+(?:_\d+)?)"
)


class ComposeKernelResolver:
    """Fetch and compare kernel versions from compose Packages directories."""

    def __init__(self):
        self.logger = get_logger(__name__)

    @staticmethod
    def compose_root_from_guest_url(guest_url: str) -> Optional[str]:
        """
        Derive compose root from a guest-image URL.

        Example guest:
          .../compose/BaseOS/x86_64/images/rhel-guest-image-9.4-....qcow2
        → compose root:
          .../compose/
        """
        if not guest_url:
            return None
        marker = "/compose/"
        idx = guest_url.lower().find(marker)
        if idx < 0:
            return None
        return guest_url[: idx + len(marker)]

    def packages_urls(self, compose_root: str) -> List[str]:
        """Candidate Packages listing URLs (mirrors + http/https)."""
        is_rhel7 = "rhel-7" in compose_root.lower()
        dir_path = (
            "Server/x86_64/os/Packages/"
            if is_rhel7
            else "BaseOS/x86_64/os/Packages/"
        )
        root = compose_root if compose_root.endswith("/") else compose_root + "/"

        # Prefer devel (often reachable) then pnq, keep original host first
        hosts = [root]
        if "download.eng.pnq.redhat.com" in root:
            hosts.append(
                root.replace(
                    "download.eng.pnq.redhat.com", "download.devel.redhat.com"
                )
            )
        elif "download.devel.redhat.com" in root:
            hosts.append(
                root.replace(
                    "download.devel.redhat.com", "download.eng.pnq.redhat.com"
                )
            )

        urls: List[str] = []
        for h in hosts:
            urls.append(h + dir_path)
            if h.startswith("https://"):
                urls.append(h.replace("https://", "http://", 1) + dir_path)
        # dedupe
        seen = set()
        out = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                out.append(u)
        return out

    def fetch_kernel_from_guest_url(self, guest_url: str) -> Optional[str]:
        """Return kernel version string from Packages listing for a guest image URL."""
        compose_root = self.compose_root_from_guest_url(guest_url)
        if not compose_root:
            self.logger.warning(
                f"  ⚠️  Cannot derive compose root from guest URL: {guest_url}"
            )
            return None

        self.logger.info(f"  🔍 Compose root: {compose_root}")
        for packages_url in self.packages_urls(compose_root):
            try:
                self.logger.info(f"     Trying Packages: {packages_url}")
                resp = requests.get(packages_url, timeout=20, verify=False)
                if resp.status_code != 200:
                    self.logger.info(f"     HTTP {resp.status_code}")
                    continue
                matches = KERNEL_RPM_RE.findall(resp.text)
                if matches:
                    kernel = matches[0]
                    self.logger.info(
                        f"  ✅ Baseline kernel from compose: {kernel}"
                    )
                    return kernel
                self.logger.info("     No kernel RPM matches in listing")
            except requests.exceptions.Timeout:
                self.logger.warning(f"     Timeout: {packages_url}")
            except requests.exceptions.ConnectionError as e:
                self.logger.warning(f"     Connection error: {e}")
            except Exception as e:
                self.logger.warning(f"     Error: {e}")

        self.logger.warning("  ❌ Could not fetch kernel from any compose mirror")
        return None

    def compare_kernel_versions(self, kernel1: str, kernel2: str) -> int:
        """
        Compare two kernel versions.

        Returns -1 if kernel1 < kernel2, 0 if equal, 1 if kernel1 > kernel2.
        """
        try:

            def parse_version(v: str) -> tuple:
                v = v.replace(".x86_64", "")
                parts = v.split("-")
                if len(parts) < 2:
                    return (0, 0, 0, 0, 0, 0, 0, 0)

                main_parts = parts[0].split(".")
                major = int(main_parts[0]) if len(main_parts) > 0 else 0
                minor = int(main_parts[1]) if len(main_parts) > 1 else 0
                patch = int(main_parts[2]) if len(main_parts) > 2 else 0

                release_parts = parts[1].split(".el")[0].split(".")
                rel_major = int(release_parts[0]) if len(release_parts) > 0 else 0
                rel_minor = int(release_parts[1]) if len(release_parts) > 1 else 0
                rel_patch = int(release_parts[2]) if len(release_parts) > 2 else 0

                rel_build1 = 0
                rel_build2 = 0
                if len(release_parts) > 3:
                    fourth_part = release_parts[3]
                    if "_" in fourth_part:
                        build_parts = fourth_part.split("_")
                        rel_build1 = (
                            int(build_parts[0]) if build_parts[0].isdigit() else 0
                        )
                        rel_build2 = (
                            int(build_parts[1])
                            if len(build_parts) > 1 and build_parts[1].isdigit()
                            else 0
                        )
                    else:
                        rel_build1 = int(fourth_part) if fourth_part.isdigit() else 0

                return (
                    major,
                    minor,
                    patch,
                    rel_major,
                    rel_minor,
                    rel_patch,
                    rel_build1,
                    rel_build2,
                )

            v1 = parse_version(kernel1)
            v2 = parse_version(kernel2)
            if v1 < v2:
                return -1
            if v1 > v2:
                return 1
            return 0
        except Exception as e:
            self.logger.warning(f"Failed to compare kernel versions: {e}")
            return 0
