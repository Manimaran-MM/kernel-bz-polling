"""
Inventory File Generator

Generates missing inventory files for specific RHEL versions based on templates.
"""

import os
import re
from pathlib import Path
from typing import Optional, Dict, Any
import yaml

from src.utils.logger import get_logger
from src.utils.config import get_config


class InventoryGenerator:
    """Generates inventory files for missing RHEL versions."""
    
    def __init__(self, cephci_repo_path: Optional[str] = None):
        """
        Initialize the inventory generator.
        
        Args:
            cephci_repo_path: Path to cephci repository
        """
        self.logger = get_logger(__name__)
        self.config = get_config()
        self.cephci_repo = cephci_repo_path or self.config.get('cephci.repo_path', '/ceph/cephci')
        self.inventory_dir = Path(self.cephci_repo) / 'conf' / 'inventory'
        
    def generate_inventory_file(
        self,
        target_version: str,
        template_version: Optional[str] = None
    ) -> Optional[str]:
        """
        Generate an inventory file for a specific RHEL version.
        
        Args:
            target_version: Target RHEL version (e.g., '9.2', '8.8')
            template_version: Template version to use (e.g., '9.4', '8.10')
                            If None, will auto-detect closest version
        
        Returns:
            Path to generated inventory file or None if failed
        """
        self.logger.info(f"Generating inventory file for RHEL {target_version}")

        try:
            major, minor = target_version.split('.')
            # Prefer ocpvirt inventory naming
            ocp_target = self.inventory_dir / f"ocpvirt-rhel-{target_version}.yaml"
            if ocp_target.exists():
                self.logger.info(f"✅ Inventory file already exists: {ocp_target}")
                return str(ocp_target)

            ocp_template = self._find_closest_ocpvirt_template(major)
            if ocp_template and ocp_template.exists():
                self.logger.info(f"📄 Using ocpvirt template: {ocp_template}")
                with open(ocp_template, 'r') as f:
                    template_content = f.read()
                old_ver = self._extract_version_from_path(ocp_template) or (
                    str(template_version) if template_version else f"{major}.0"
                )
                new_content = self._replace_version_references(
                    template_content, old_ver, target_version
                )
                # datasource://rhel94 style alias from major.minor
                ds_alias = f"rhel{major}{minor}"
                new_content = re.sub(
                    r'image-name:\s*datasource://\S+',
                    f'image-name: datasource://{ds_alias}',
                    new_content,
                )
                ocp_target.parent.mkdir(parents=True, exist_ok=True)
                with open(ocp_target, 'w') as f:
                    f.write(new_content)
                self.logger.info(f"✅ Generated inventory file: {ocp_target}")
                return str(ocp_target)

            # Legacy OpenStack-style fallback (should not be needed on ocpvirt)
            target_file = f"rhel-{target_version}-server-x86_64.yaml"
            target_path = self.inventory_dir / target_file

            if target_path.exists():
                self.logger.info(f"✅ Inventory file already exists: {target_path}")
                return str(target_path)

            if template_version:
                template_file = f"rhel-{template_version}-server-x86_64.yaml"
                template_path = self.inventory_dir / template_file
            else:
                template_path = self._find_closest_template(major, minor)

            if not template_path or not template_path.exists():
                self.logger.error(f"❌ Template file not found: {template_path}")
                return None

            self.logger.info(f"📄 Using template: {template_path}")
            with open(template_path, 'r') as f:
                template_content = f.read()

            new_content = self._replace_version_references(
                template_content,
                str(template_version) if template_version else self._extract_version_from_path(template_path),
                target_version
            )

            target_path.parent.mkdir(parents=True, exist_ok=True)
            with open(target_path, 'w') as f:
                f.write(new_content)

            self.logger.info(f"✅ Generated inventory file: {target_path}")
            return str(target_path)

        except Exception as e:
            self.logger.error(f"❌ Failed to generate inventory file: {e}")
            return None

    def _find_closest_ocpvirt_template(self, major: str) -> Optional[Path]:
        """Find closest ocpvirt-rhel-*.yaml for the same major version."""
        matching = list(self.inventory_dir.glob(f"ocpvirt-rhel-{major}*.yaml"))
        if not matching:
            matching = list(self.inventory_dir.glob("ocpvirt-rhel-*.yaml"))
        if not matching:
            return None

        def sort_key(path: Path) -> float:
            match = re.search(r'ocpvirt-rhel-(\d+)(?:\.(\d+))?', path.name)
            if not match:
                return 0.0
            maj = float(match.group(1))
            minor = float(match.group(2) or 0)
            return maj + minor / 100.0

        matching.sort(key=sort_key, reverse=True)
        return matching[0]
    
    def _find_closest_template(self, major: str, minor: str) -> Optional[Path]:
        """
        Find the closest existing inventory file to use as template.
        
        Args:
            major: Major version (e.g., '9')
            minor: Minor version (e.g., '2')
        
        Returns:
            Path to template file or None
        """
        # List all inventory files for the same major version
        pattern = f"rhel-{major}.*.yaml"
        matching_files = list(self.inventory_dir.glob(pattern))
        
        if not matching_files:
            self.logger.warning(f"No inventory files found for RHEL {major}.x")
            return None
        
        # Sort by version number (descending) to get the latest
        def extract_minor_version(path: Path) -> float:
            match = re.search(r'rhel-\d+\.(\d+)', path.name)
            return float(match.group(1)) if match else 0.0
        
        matching_files.sort(key=extract_minor_version, reverse=True)
        template = matching_files[0]
        
        self.logger.info(f"Selected template: {template.name}")
        return template
    
    def _extract_version_from_path(self, path: Path) -> str:
        """
        Extract version from inventory file path.
        
        Args:
            path: Path to inventory file
        
        Returns:
            Version string (e.g., '9.4')
        """
        match = re.search(r'(?:ocpvirt-)?rhel-(\d+\.\d+)', path.name)
        return match.group(1) if match else ''
    
    def _replace_version_references(
        self,
        content: str,
        old_version: str,
        new_version: str
    ) -> str:
        """
        Replace version references in inventory file content.
        
        Args:
            content: Original file content
            old_version: Old version string (e.g., '9.4')
            new_version: New version string (e.g., '9.2')
        
        Returns:
            Updated content
        """
        # Replace version in image names and other references
        # Be careful to only replace version numbers, not other numbers
        
        # Replace RHEL-X.Y patterns
        content = re.sub(
            rf'RHEL-{re.escape(old_version)}',
            f'RHEL-{new_version}',
            content,
            flags=re.IGNORECASE
        )

        # Replace rhel-X.Y / ocpvirt-rhel-X.Y patterns
        content = re.sub(
            rf'rhel-{re.escape(old_version)}',
            f'rhel-{new_version}',
            content,
            flags=re.IGNORECASE
        )

        # datasource://rhel94 style
        old_parts = old_version.split('.')
        new_parts = new_version.split('.')
        if len(old_parts) >= 2 and len(new_parts) >= 2:
            old_alias = f"rhel{old_parts[0]}{old_parts[1]}"
            new_alias = f"rhel{new_parts[0]}{new_parts[1]}"
            content = content.replace(f"datasource://{old_alias}", f"datasource://{new_alias}")
        
        # Replace X.Y patterns in image names (be more specific)
        old_major, old_minor = old_version.split('.')
        new_major, new_minor = new_version.split('.')
        
        # Only replace if major versions match
        if old_major == new_major:
            # Replace patterns like "9.4" with "9.2" in image names
            content = re.sub(
                rf'(\d+)\.{old_minor}(?=[-\s])',
                rf'\g<1>.{new_minor}',
                content
            )
        
        return content
    
    def ensure_inventory_exists(self, inventory_path: str) -> bool:
        """
        Ensure an inventory file exists, generating it if necessary.

        Args:
            inventory_path: Relative path (e.g. conf/inventory/ocpvirt-rhel-9.4.yaml)

        Returns:
            True if file exists or was generated successfully
        """
        full_path = Path(self.cephci_repo) / inventory_path

        if full_path.exists():
            return True

        match = re.search(r'(?:ocpvirt-)?rhel-(\d+\.\d+)', inventory_path)
        if not match:
            # e.g. ocpvirt-rhel-10-latest.yaml — do not invent; require upstream file
            self.logger.error(
                f"Cannot extract major.minor from path (add mapping/upstream file): "
                f"{inventory_path}"
            )
            return False

        version = match.group(1)
        result = self.generate_inventory_file(version)
        return result is not None

# Made with Bob
