"""
Choose runtime inventory image-name: datasource:// first, HTTPS guest URL fallback.

cephci ocpvirt accepts both:
  - datasource://<ns>/<name>  (clone existing DataSource)
  - https://...               (CDI HTTP import of guest qcow2)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests
import urllib3
import yaml

from src.utils.config import get_config
from src.utils.logger import get_logger

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class RuntimeImageResolver:
    """Resolve whether to use datasource:// or validated guest HTTPS URL."""

    def __init__(self, cephci_root: str = "cephci"):
        self.logger = get_logger(__name__)
        self.config = get_config()
        self.cephci_root = Path(cephci_root)
        self.http_fallback = bool(
            self.config.get("ocpvirt.http_image_fallback", True)
        )
        self.prefer_http = bool(
            self.config.get("ocpvirt.prefer_http_guest_image", False)
        )
        self.check_datasource = bool(
            self.config.get("ocpvirt.check_datasource_exists", True)
        )
        self._ns_config_cache: Optional[Dict[str, Any]] = None
        self._ds_exists_cache: Dict[str, Optional[bool]] = {}

    def _namespace_template_name(self) -> str:
        return self.config.get("ocpvirt.namespace", "rdu3_ceph_sys_test")

    def _load_namespace_config(self) -> Dict[str, Any]:
        if self._ns_config_cache is not None:
            return self._ns_config_cache
        name = self._namespace_template_name()
        path = self.cephci_root / "conf" / "ocpvirt" / f"{name}.yaml"
        if not path.exists():
            self.logger.debug(f"OCPVirt namespace template not found: {path}")
            self._ns_config_cache = {}
            return self._ns_config_cache
        try:
            with open(path) as f:
                self._ns_config_cache = yaml.safe_load(f) or {}
        except Exception as e:
            self.logger.warning(f"Failed to load {path}: {e}")
            self._ns_config_cache = {}
        return self._ns_config_cache

    @staticmethod
    def _parse_datasource_url(url: str) -> Tuple[Optional[str], Optional[str]]:
        """datasource://ns/name or datasource://alias → (ns, name) or (None, alias)."""
        if not url or not str(url).startswith("datasource://"):
            return None, None
        rest = str(url)[len("datasource://") :].strip("/")
        if "/" in rest:
            ns, name = rest.split("/", 1)
            return ns, name
        return None, rest

    def _expand_datasource(self, image: str) -> Optional[str]:
        """Expand datasource://rhel94 via conf/ocpvirt datasources map."""
        if not image or not image.startswith("datasource://"):
            return image
        ns, rest = self._parse_datasource_url(image)
        if ns and rest:
            return image  # already full
        alias = rest
        datasources = self._load_namespace_config().get("datasources") or {}
        full = datasources.get(alias)
        if full:
            return str(full)
        return image

    def _ocp_headers(self) -> Dict[str, str]:
        token = (
            os.environ.get("OCPVIRT_TOKEN")
            or self.config.get("ocpvirt.token")
            or ""
        )
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _ocp_verify(self):
        ca_file = (
            os.environ.get("OCPVIRT_SSL_CA_CERT")
            or self.config.get("ocpvirt.ssl_ca_cert")
            or ""
        )
        if ca_file and Path(ca_file).expanduser().exists():
            return str(Path(ca_file).expanduser())
        # certificate_authority_data is base64; requests needs a file — skip verify
        # when only inline CA is configured (cluster may use private CA).
        return False

    def datasource_exists(self, datasource_url: str) -> Optional[bool]:
        """
        Check CDI DataSource exists on the cluster.

        Returns True/False, or None if the check could not be performed.
        """
        if datasource_url in self._ds_exists_cache:
            return self._ds_exists_cache[datasource_url]

        full = self._expand_datasource(datasource_url) or datasource_url
        ns, name = self._parse_datasource_url(full)
        if not ns or not name:
            self.logger.debug(
                f"Cannot check DataSource existence for short/invalid URL: {datasource_url}"
            )
            self._ds_exists_cache[datasource_url] = None
            return None

        server = (self._load_namespace_config().get("server") or "").rstrip("/")
        if not server:
            self._ds_exists_cache[datasource_url] = None
            return None

        api = (
            f"{server}/apis/cdi.kubevirt.io/v1beta1/namespaces/"
            f"{ns}/datasources/{name}"
        )
        try:
            resp = requests.get(
                api,
                headers=self._ocp_headers(),
                timeout=15,
                verify=self._ocp_verify(),
            )
            if resp.status_code == 200:
                self._ds_exists_cache[datasource_url] = True
                return True
            if resp.status_code == 404:
                self.logger.warning(
                    f"  ⚠️  DataSource not found: {ns}/{name} (HTTP 404)"
                )
                self._ds_exists_cache[datasource_url] = False
                return False
            self.logger.debug(
                f"DataSource check HTTP {resp.status_code} for {ns}/{name}"
            )
            self._ds_exists_cache[datasource_url] = None
            return None
        except Exception as e:
            self.logger.debug(f"DataSource existence check failed: {e}")
            self._ds_exists_cache[datasource_url] = None
            return None

    def resolve(
        self,
        baseline_image: Optional[str],
        guest_image_url: Optional[str],
    ) -> Tuple[Optional[str], str]:
        """
        Pick runtime image-name.

        Returns:
            (image_name, reason) where reason is datasource|http_fallback|http_preferred|none
        """
        ds = (baseline_image or "").strip() or None
        http = (guest_image_url or "").strip() or None

        if self.prefer_http and http:
            self.logger.info(
                "  📎 prefer_http_guest_image: using validated guest HTTPS URL"
            )
            return http, "http_preferred"

        if ds and ds.startswith("datasource://"):
            if self.http_fallback and http and self.check_datasource:
                exists = self.datasource_exists(ds)
                if exists is False:
                    self.logger.info(
                        f"  ↩️  DataSource unavailable — HTTP fallback: {http}"
                    )
                    return http, "http_fallback"
            return ds, "datasource"

        if ds:
            return ds, "datasource"

        if self.http_fallback and http:
            self.logger.info(f"  ↩️  No datasource:// — HTTP fallback: {http}")
            return http, "http_fallback"

        if http:
            return http, "http_fallback"

        return None, "none"
