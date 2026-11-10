"""
Resolve kernel NVR from a RHEL compose guest-image URL (Packages listing).

Also walks older sibling composes under the same rel-eng / nightly trees
(same RHEL major.minor only) when the latest guest image's kernel is not
strictly older than the JIRA CKI kernel. Secondary search roots come from
``ocpvirt.compose_releng`` in config.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple, Union
from urllib.parse import urljoin

import requests
import urllib3

from src.utils.logger import get_logger

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

KERNEL_RPM_RE = re.compile(
    r"kernel-(\d+\.\d+\.\d+-\d+(?:\.\d+)*\.el\d+(?:_\d+)?)"
)
HREF_RE = re.compile(r'href="([^"]+)"', re.IGNORECASE)
# RHEL-10.3-20261011.1 / latest-RHEL-10.3 / RHEL-8.6.0-20220628.0
COMPOSE_DIR_RE = re.compile(
    r"^(?:latest-)?RHEL-(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:-(.+))?/$",
    re.IGNORECASE,
)
GUEST_QCOW_RE = re.compile(
    r'href="(rhel-guest-image-[^"]+\.qcow2)"', re.IGNORECASE
)

# major → URL or list of URLs (rel-eng tip, then nightly dated builds, …)
ComposeRelengMap = Dict[str, Union[str, List[str]]]


class ComposeKernelResolver:
    """Fetch and compare kernel versions from compose Packages directories."""

    def __init__(
        self,
        max_older_candidates: int = 30,
        compose_releng: Optional[ComposeRelengMap] = None,
    ):
        self.logger = get_logger(__name__)
        self.max_older_candidates = max_older_candidates
        self.compose_releng: ComposeRelengMap = compose_releng or {}
        # jira_kernel → (guest_url, kernel)
        self._older_cache: Dict[
            str, Tuple[Optional[str], Optional[str]]
        ] = {}

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

    @staticmethod
    def prefer_devel_mirror(url: str) -> str:
        """Rewrite pnq → devel (usually more reachable from lab runners)."""
        if not url:
            return url
        return url.replace(
            "download.eng.pnq.redhat.com", "download.devel.redhat.com"
        )

    @staticmethod
    def prefer_pnq_mirror(url: str) -> str:
        """Rewrite devel → pnq (CDI on OCPVirt trusts PNQ certs for HTTP import)."""
        if not url:
            return url
        return url.replace(
            "download.devel.redhat.com", "download.eng.pnq.redhat.com"
        )

    def packages_urls(self, compose_root: str) -> List[str]:
        """Candidate Packages listing URLs (mirrors + http/https)."""
        is_rhel7 = "rhel-7" in compose_root.lower()
        dir_path = (
            "Server/x86_64/os/Packages/"
            if is_rhel7
            else "BaseOS/x86_64/os/Packages/"
        )
        root = compose_root if compose_root.endswith("/") else compose_root + "/"

        # Prefer devel first (pnq Packages listings often time out)
        hosts = []
        devel = self.prefer_devel_mirror(root)
        if devel not in hosts:
            hosts.append(devel)
        if root not in hosts:
            hosts.append(root)
        if "download.devel.redhat.com" in root:
            pnq = root.replace(
                "download.devel.redhat.com", "download.eng.pnq.redhat.com"
            )
            if pnq not in hosts:
                hosts.append(pnq)

        urls: List[str] = []
        for h in hosts:
            urls.append(h + dir_path)
            if h.startswith("https://"):
                urls.append(h.replace("https://", "http://", 1) + dir_path)
        seen = set()
        out = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                out.append(u)
        return out

    def _get(self, url: str, timeout: int = 25) -> Optional[requests.Response]:
        try:
            resp = requests.get(url, timeout=timeout, verify=False)
            if resp.status_code == 200:
                return resp
            self.logger.debug(f"     HTTP {resp.status_code}: {url}")
            return None
        except requests.exceptions.Timeout:
            self.logger.warning(f"     Timeout: {url}")
            return None
        except requests.exceptions.ConnectionError as e:
            self.logger.warning(f"     Connection error: {e}")
            return None
        except Exception as e:
            self.logger.warning(f"     Error: {e}")
            return None

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
            self.logger.info(f"     Trying Packages: {packages_url}")
            resp = self._get(packages_url)
            if not resp:
                continue
            matches = KERNEL_RPM_RE.findall(resp.text)
            if matches:
                kernel = matches[0]
                self.logger.info(f"  ✅ Baseline kernel from compose: {kernel}")
                return kernel
            self.logger.info("     No kernel RPM matches in listing")

        self.logger.warning("  ❌ Could not fetch kernel from any compose mirror")
        return None

    @staticmethod
    def releng_parent_and_compose(guest_url: str) -> Tuple[Optional[str], Optional[str]]:
        """
        From a guest URL return (rel-eng parent URL, compose dir name).

        .../rhel-10/rel-eng/RHEL-10/latest-RHEL-10.3/compose/BaseOS/.../qcow2
        → ('.../rhel-10/rel-eng/RHEL-10/', 'latest-RHEL-10.3')
        """
        if not guest_url:
            return None, None
        marker = "/compose/"
        idx = guest_url.lower().find(marker)
        if idx < 0:
            return None, None
        before = guest_url[:idx].rstrip("/")
        parts = before.split("/")
        if len(parts) < 2:
            return None, None
        compose_name = parts[-1]
        parent = "/".join(parts[:-1]) + "/"
        return parent, compose_name

    @staticmethod
    def _compose_sort_key(name: str) -> tuple:
        """Sort key: higher version / newer date first."""
        n = name if name.endswith("/") else name + "/"
        m = COMPOSE_DIR_RE.match(n)
        if not m:
            return (0, 0, 0, 0, name)
        major = int(m.group(1))
        minor = int(m.group(2) or 0)
        patch = int(m.group(3) or 0)
        suffix = m.group(4) or ""
        # latest-* counts as tip of that stream
        is_latest = name.lower().startswith("latest-")
        date_num = 0
        build = 0
        if is_latest:
            date_num = 99_999_999
        else:
            # 20261011.1 or RC-1.2 → prefer numeric date when present
            dm = re.match(r"^(\d{8})(?:\.(\d+))?$", suffix)
            if dm:
                date_num = int(dm.group(1))
                build = int(dm.group(2) or 0)
            else:
                # RC / Alpha etc. — keep below dated GA tips but above empty
                date_num = 1
        return (major, minor, patch, date_num, build)

    def list_compose_dirs(self, releng_parent: str) -> List[str]:
        """List RHEL-* / latest-RHEL-* compose directory names under rel-eng parent."""
        parent = self.prefer_devel_mirror(releng_parent)
        if not parent.endswith("/"):
            parent += "/"
        resp = self._get(parent, timeout=30)
        if not resp:
            # try original host
            if parent != releng_parent:
                resp = self._get(
                    releng_parent if releng_parent.endswith("/") else releng_parent + "/",
                    timeout=30,
                )
        if not resp:
            self.logger.warning(f"  ⚠️  Cannot list composes under {parent}")
            return []

        names = []
        for href in HREF_RE.findall(resp.text):
            if "?" in href:
                continue
            name = href.rstrip("/")
            # strip leading path if present
            if "/" in name:
                name = name.rsplit("/", 1)[-1]
            if name.startswith("AppStream-"):
                continue
            if not (
                name.startswith("RHEL-") or name.startswith("latest-RHEL-")
            ):
                continue
            names.append(name)

        # Prefer stream tips (latest-RHEL-*) first so we jump 8.10→8.9→8.6
        # quickly; then dated builds. Each group newest-first.
        latest = sorted(
            {n for n in names if n.startswith("latest-RHEL-")},
            key=self._compose_sort_key,
            reverse=True,
        )
        dated = sorted(
            {n for n in names if not n.startswith("latest-RHEL-")},
            key=self._compose_sort_key,
            reverse=True,
        )
        return latest + dated

    @staticmethod
    def rhel_stream_from_kernel(kernel: str) -> Optional[Tuple[int, int]]:
        """Parse (major, minor) from el8_6 / el10_3 style tags in a kernel NVR."""
        if not kernel:
            return None
        m = re.search(r"\.el(\d+)(?:_(\d+))?", kernel)
        if not m:
            return None
        return int(m.group(1)), int(m.group(2) or 0)

    def _compose_stream(self, name: str) -> Optional[Tuple[int, int]]:
        n = name if name.endswith("/") else name + "/"
        m = COMPOSE_DIR_RE.match(n)
        if not m:
            return None
        return int(m.group(1)), int(m.group(2) or 0)

    def guest_image_url_for_compose(
        self, releng_parent: str, compose_name: str
    ) -> Optional[str]:
        """
        Resolve rhel-guest-image-*.qcow2 URL for a compose directory.

        Listing may use devel (reachable from runners), but the returned URL
        is always rewritten to download.eng.pnq.redhat.com for CDI import.
        """
        candidates: List[str] = []
        for parent in (
            self.prefer_devel_mirror(releng_parent),
            self.prefer_pnq_mirror(releng_parent),
            releng_parent,
        ):
            if parent and parent not in candidates:
                candidates.append(parent)

        for parent in candidates:
            if not parent.endswith("/"):
                parent += "/"
            images_url = (
                f"{parent}{compose_name}/compose/BaseOS/x86_64/images/"
            )
            resp = self._get(images_url, timeout=30)
            if not resp:
                continue
            matches = GUEST_QCOW_RE.findall(resp.text)
            if not matches:
                continue
            # Prefer the plain guest image (not lvm-oci etc. — regex already filters)
            filename = matches[0]
            # CDI must use PNQ — devel TLS fails on the OCPVirt cluster
            return self.prefer_pnq_mirror(urljoin(images_url, filename))
        return None

    def _fallback_parents_for_kernel(self, jira_kernel: str) -> List[str]:
        """Configured compose trees for the JIRA kernel's RHEL major."""
        stream = self.rhel_stream_from_kernel(jira_kernel)
        if not stream:
            return []
        major = str(stream[0])
        raw = self.compose_releng.get(major) or self.compose_releng.get(
            f"{stream[0]}.{stream[1]}"
        )
        if not raw:
            return []
        urls = raw if isinstance(raw, list) else [raw]
        out: List[str] = []
        for u in urls:
            if not u:
                continue
            u = u if str(u).endswith("/") else str(u) + "/"
            out.append(u)
        return out

    def _search_parent_for_older(
        self,
        parent: str,
        jira_kernel: str,
        skip: set,
        jira_stream: Optional[Tuple[int, int]],
        label: str,
    ) -> Tuple[Optional[str], Optional[str], int]:
        """
        Search one compose parent for kernel < jira_kernel (same stream only).

        Returns (guest_url, kernel, tried_count).
        """
        parent_n = self.prefer_devel_mirror(parent)
        if not parent_n.endswith("/"):
            parent_n += "/"

        self.logger.info(f"  📌 {label}: searching under {parent_n}")
        candidates = self.list_compose_dirs(parent_n)
        if not candidates:
            self.logger.warning(f"     No compose dirs under {parent_n}")
            return None, None, 0

        if jira_stream:
            major_j, minor_j = jira_stream
            same_stream = [
                name
                for name in candidates
                if self._compose_stream(name) == jira_stream
            ]
            skipped = len(candidates) - len(same_stream)
            if skipped:
                self.logger.info(
                    f"  📎 Restricting to RHEL {major_j}.{minor_j} only "
                    f"({len(same_stream)} candidates; skipped {skipped} "
                    f"other RHEL versions)"
                )
            if not same_stream:
                self.logger.info(
                    f"     No RHEL-{major_j}.{minor_j} composes under {parent_n}"
                )
                return None, None, 0
            candidates = same_stream

        tried = 0
        for name in candidates:
            if name in skip:
                continue
            if tried >= self.max_older_candidates:
                self.logger.warning(
                    f"  ⚠️  Reached max older-compose attempts "
                    f"({self.max_older_candidates}) under {parent_n}"
                )
                break
            tried += 1
            self.logger.info(f"  🔎 Older candidate {tried}: {name}")
            older_guest = self.guest_image_url_for_compose(parent_n, name)
            if not older_guest:
                self.logger.info(f"     No guest qcow2 under {name}")
                continue
            kernel = self.fetch_kernel_from_guest_url(older_guest)
            if not kernel:
                continue
            kernel_stream = self.rhel_stream_from_kernel(kernel)
            if jira_stream and kernel_stream and kernel_stream != jira_stream:
                self.logger.info(
                    f"     Skip {name}: kernel {kernel} is RHEL "
                    f"{kernel_stream[0]}.{kernel_stream[1]}, need "
                    f"{jira_stream[0]}.{jira_stream[1]}"
                )
                continue
            # Require a strictly older RHEL base release (ignore CKI brew suffix)
            cmp = self.compare_kernel_base_versions(kernel, jira_kernel)
            if cmp < 0:
                self.logger.info(
                    f"  ✅ Older compose {name}: kernel {kernel} < JIRA base "
                    f"{jira_kernel}"
                )
                return older_guest, kernel, tried
            if cmp == 0:
                self.logger.info(
                    f"     Skip {name}: kernel {kernel} is same base release "
                    f"as JIRA {jira_kernel}"
                )
            else:
                self.logger.info(
                    f"     Skip {name}: kernel {kernel} >= JIRA base {jira_kernel}"
                )
        return None, None, tried

    def find_older_baseline(
        self,
        guest_url: Optional[str],
        jira_kernel: str,
        skip_guest_url: Optional[str] = None,
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Walk older composes until base kernel < jira_kernel base.

        1. Primary: siblings under the guest URL's rel-eng/nightly parent
           (skipped when guest_url is missing — e.g. no_datasource_mapping)
        2. Secondary: ``ocpvirt.compose_releng`` trees for that RHEL major
           (e.g. rel-eng tip, then nightly dated builds)

        Only the same RHEL major.minor as the JIRA kernel is considered
        (el10_3 → RHEL-10.3*). Same base release (ignoring CKI brew suffix)
        is skipped. Cross-minor baselines are rejected.

        Returns (guest_image_url, baseline_kernel) or (None, None).
        """
        if jira_kernel in self._older_cache:
            cached = self._older_cache[jira_kernel]
            self.logger.info(
                f"  📎 Using cached older compose for {jira_kernel}: "
                f"{cached[1] or 'none'}"
            )
            return cached

        parent, current = (None, None)
        if guest_url:
            parent, current = self.releng_parent_and_compose(guest_url)
        skip = {current} if current else set()
        if skip_guest_url:
            _, skip_name = self.releng_parent_and_compose(skip_guest_url)
            if skip_name:
                skip.add(skip_name)

        jira_stream = self.rhel_stream_from_kernel(jira_kernel)
        searched: List[str] = []
        total_tried = 0

        def _norm(p: str) -> str:
            p = self.prefer_devel_mirror(p)
            return p if p.endswith("/") else p + "/"

        # 1) Primary: guest URL parent
        if parent:
            result_url, result_k, tried = self._search_parent_for_older(
                parent,
                jira_kernel,
                skip,
                jira_stream,
                label="Primary (guest URL parent)",
            )
            total_tried += tried
            searched.append(_norm(parent))
            if result_url:
                result = (result_url, result_k)
                self._older_cache[jira_kernel] = result
                return result
        elif not guest_url:
            self.logger.info(
                "  📌 No guest URL parent — searching ocpvirt.compose_releng "
                f"for older baseline than {jira_kernel}"
            )
        else:
            self.logger.warning(
                "  ⚠️  Cannot derive compose parent from guest URL; "
                "trying config compose_releng fallbacks"
            )

        # 2) Secondary: configured compose_releng trees for this major
        fallbacks = self._fallback_parents_for_kernel(jira_kernel)
        if not fallbacks:
            stream_note = (
                f" on RHEL {jira_stream[0]}.{jira_stream[1]}"
                if jira_stream
                else ""
            )
            self.logger.warning(
                f"  ❌ No older compose with kernel < {jira_kernel}{stream_note} "
                f"(tried {total_tried}; no ocpvirt.compose_releng fallbacks)"
            )
            self._older_cache[jira_kernel] = (None, None)
            return None, None

        for fb in fallbacks:
            fb_n = _norm(fb)
            if fb_n in searched:
                self.logger.info(
                    f"  📎 Secondary fallback already searched: {fb_n}"
                )
                continue
            result_url, result_k, tried = self._search_parent_for_older(
                fb_n,
                jira_kernel,
                skip,
                jira_stream,
                label="Secondary (compose_releng)",
            )
            total_tried += tried
            searched.append(fb_n)
            if result_url:
                result = (result_url, result_k)
                self._older_cache[jira_kernel] = result
                return result

        stream_note = (
            f" on RHEL {jira_stream[0]}.{jira_stream[1]}" if jira_stream else ""
        )
        self.logger.warning(
            f"  ❌ No older compose with kernel < {jira_kernel}{stream_note} "
            f"(tried {total_tried} candidates across {len(searched)} trees)"
        )
        self._older_cache[jira_kernel] = (None, None)
        return None, None

    @staticmethod
    def parse_kernel_version_tuple(v: str) -> tuple:
        """
        Parse kernel NVR into a comparable tuple.

        (major, minor, patch, rel_major, rel_minor, rel_patch, build1, build2)
        CKI brew suffixes like .2224_2852799666 become build1/build2.
        """
        v = (v or "").replace(".x86_64", "")
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

    def compare_kernel_versions(self, kernel1: str, kernel2: str) -> int:
        """
        Compare two kernel versions (including CKI brew build suffixes).

        Returns -1 if kernel1 < kernel2, 0 if equal, 1 if kernel1 > kernel2.
        """
        try:
            v1 = self.parse_kernel_version_tuple(kernel1)
            v2 = self.parse_kernel_version_tuple(kernel2)
            if v1 < v2:
                return -1
            if v1 > v2:
                return 1
            return 0
        except Exception as e:
            self.logger.warning(f"Failed to compare kernel versions: {e}")
            return 0

    def compare_kernel_base_versions(self, kernel1: str, kernel2: str) -> int:
        """
        Compare RHEL base release only (ignore CKI .NNNN_MMMM brew suffixes).

        So 6.12.0-264.4.1.el10_3 == 6.12.0-264.4.1.2224_….el10_3 for baseline
        selection — same base is not a usable older baseline.
        """
        try:
            v1 = self.parse_kernel_version_tuple(kernel1)[:6]
            v2 = self.parse_kernel_version_tuple(kernel2)[:6]
            if v1 < v2:
                return -1
            if v1 > v2:
                return 1
            return 0
        except Exception as e:
            self.logger.warning(f"Failed to compare kernel base versions: {e}")
            return 0
