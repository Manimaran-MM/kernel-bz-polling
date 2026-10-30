"""
Fetch RHEL guest-image DataVolume YAMLs from cephci-openshift on GitLab.

Used to resolve the compose HTTP URL for a datasource alias
(e.g. rhel94 → rhel94-iso.datavolume.yaml → download.eng…/compose/.../qcow2).
"""

from __future__ import annotations

import os
import urllib.parse
from typing import Any, Dict, List, Optional

import requests
import urllib3
import yaml

from src.utils.config import get_config
from src.utils.logger import get_logger

# gitlab.cee.redhat.com uses a corporate/self-signed CA not in the image trust store
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class GitLabDataVolumeClient:
    """Read DataVolume YAMLs from GitLab via PRIVATE-TOKEN."""

    def __init__(self):
        self.logger = get_logger(__name__)
        self.config = get_config()
        self.base_url = (
            os.environ.get("GITLAB_URL")
            or self.config.get("gitlab.url")
            or "https://gitlab.cee.redhat.com"
        ).rstrip("/")
        self.token = (
            os.environ.get("GITLAB_TOKEN")
            or self.config.get("gitlab.token")
            or ""
        )
        self.project = (
            os.environ.get("GITLAB_PROJECT")
            or self.config.get("gitlab.project")
            or "rhcs-qe/cephci-openshift"
        )
        self.branch = (
            os.environ.get("GITLAB_BRANCH")
            or self.config.get("gitlab.branch")
            or "main"
        )
        self.dv_path = (
            os.environ.get("GITLAB_DV_PATH")
            or self.config.get("gitlab.dv_path")
            or "rhel-guest-images"
        ).strip("/")
        self._session = requests.Session()
        # Match compose_kernel / OCP clients: internal GitLab CA is not in the image
        self._session.verify = False
        if self.token:
            self._session.headers["PRIVATE-TOKEN"] = self.token

    @property
    def project_encoded(self) -> str:
        return urllib.parse.quote(self.project, safe="")

    def _raw_file_url(self, file_path: str) -> str:
        encoded_path = urllib.parse.quote(file_path, safe="")
        return (
            f"{self.base_url}/api/v4/projects/{self.project_encoded}"
            f"/repository/files/{encoded_path}/raw?ref={urllib.parse.quote(self.branch)}"
        )

    @staticmethod
    def datasource_alias(datasource: str) -> Optional[str]:
        """datasource://rhel94 → rhel94; datasource://ns/rhel94-iso → rhel94-iso."""
        if not datasource:
            return None
        alias = datasource
        if "://" in alias:
            alias = alias.split("://", 1)[1]
        if "/" in alias:
            alias = alias.rsplit("/", 1)[-1]
        return alias or None

    def candidate_dv_filenames(self, alias: str) -> List[str]:
        """Possible DataVolume filenames for a datasource alias."""
        alias = (alias or "").strip()
        if not alias:
            return []
        names = []
        # rhel94 → rhel94-iso.datavolume.yaml
        if alias.endswith("-iso"):
            names.append(f"{alias}.datavolume.yaml")
        else:
            names.append(f"{alias}-iso.datavolume.yaml")
            names.append(f"{alias}.datavolume.yaml")
        # rhel10 → also try rhel103-iso (gitlab naming)
        if alias in ("rhel10", "rhel10-iso"):
            names.append("rhel103-iso.datavolume.yaml")
        # dedupe preserve order
        seen = set()
        out = []
        for n in names:
            if n not in seen:
                seen.add(n)
                out.append(n)
        return out

    def fetch_file(self, relative_path: str) -> Optional[str]:
        if not self.token:
            self.logger.error("GITLAB_TOKEN not set — cannot fetch DataVolume YAML")
            return None
        url = self._raw_file_url(relative_path)
        try:
            resp = self._session.get(url, timeout=30)
            if resp.status_code == 404:
                self.logger.debug(f"GitLab file not found: {relative_path}")
                return None
            if resp.status_code != 200:
                self.logger.warning(
                    f"GitLab fetch HTTP {resp.status_code} for {relative_path}"
                )
                return None
            return resp.text
        except Exception as e:
            self.logger.warning(f"GitLab fetch error for {relative_path}: {e}")
            return None

    def extract_http_url_from_yaml(self, content: str) -> Optional[str]:
        """Parse DataVolume YAML (possibly multi-doc) for spec.source.http.url."""
        try:
            for doc in yaml.safe_load_all(content):
                if not isinstance(doc, dict):
                    continue
                source = (doc.get("spec") or {}).get("source") or {}
                http = source.get("http") or {}
                url = http.get("url")
                if url:
                    return str(url)
        except Exception as e:
            self.logger.warning(f"Failed to parse DataVolume YAML: {e}")
        return None

    def get_guest_image_url(self, datasource: str) -> Optional[str]:
        """
        Resolve guest qcow2/ISO HTTP URL for a datasource:// alias via GitLab DV YAML.
        """
        alias = self.datasource_alias(datasource)
        if not alias:
            return None

        for filename in self.candidate_dv_filenames(alias):
            rel = f"{self.dv_path}/{filename}"
            self.logger.info(f"  📥 Fetching GitLab DV: {rel}")
            content = self.fetch_file(rel)
            if not content:
                continue
            url = self.extract_http_url_from_yaml(content)
            if url:
                self.logger.info(f"  ✅ Guest image URL: {url}")
                return url
            self.logger.warning(f"  ⚠️  No http.url in {rel}")

        self.logger.warning(
            f"  ⚠️  No DataVolume YAML found for datasource alias '{alias}'"
        )
        return None
