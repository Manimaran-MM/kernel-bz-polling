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
        
        # Parse target version
        try:
            major, minor = target_version.split('.')
            target_file = f"rhel-{target_version}-server-x86_64.yaml"
            target_path = self.inventory_dir / target_file
            
            # Check if file already exists
            if target_path.exists():
                self.logger.info(f"✅ Inventory file already exists: {target_path}")
                return str(target_path)
            
            # Find template file
            if template_version:
                template_file = f"rhel-{template_version}-server-x86_64.yaml"
                template_path = self.inventory_dir / template_file
            else:
                # Auto-detect closest template
                template_path = self._find_closest_template(major, minor)
            
            if not template_path or not template_path.exists():
                self.logger.error(f"❌ Template file not found: {template_path}")
                return None
            
            # Read template
            self.logger.info(f"📄 Using template: {template_path}")
            with open(template_path, 'r') as f:
                template_content = f.read()
            
            # Generate new content by replacing version references
            new_content = self._replace_version_references(
                template_content,
                str(template_version) if template_version else self._extract_version_from_path(template_path),
                target_version
            )
            
            # Create directory if it doesn't exist
            target_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Write new inventory file
            with open(target_path, 'w') as f:
                f.write(new_content)
            
            self.logger.info(f"✅ Generated inventory file: {target_path}")
            return str(target_path)
            
        except Exception as e:
            self.logger.error(f"❌ Failed to generate inventory file: {e}")
            return None
    
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
        match = re.search(r'rhel-(\d+\.\d+)', path.name)
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
        
        # Replace rhel-X.Y patterns
        content = re.sub(
            rf'rhel-{re.escape(old_version)}',
            f'rhel-{new_version}',
            content,
            flags=re.IGNORECASE
        )
        
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
            inventory_path: Relative path to inventory file (e.g., 'conf/inventory/rhel-9.2-server-x86_64.yaml')
        
        Returns:
            True if file exists or was generated successfully
        """
        full_path = Path(self.cephci_repo) / inventory_path
        
        if full_path.exists():
            return True
        
        # Extract version from path
        match = re.search(r'rhel-(\d+\.\d+)', inventory_path)
        if not match:
            self.logger.error(f"Cannot extract version from path: {inventory_path}")
            return False
        
        version = match.group(1)
        result = self.generate_inventory_file(version)
        return result is not None

# Made with Bob
