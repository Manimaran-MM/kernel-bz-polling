"""
CephCI Inventory File Parser

Parses inventory YAML files to extract image-name (and optional vm-size).
OCPVirt inventories have image-name but no vm-size.
"""

import os
import yaml
from typing import Dict, Optional, List

from src.utils.logger import get_logger
from src.utils.config import get_config


class InventoryParser:
    """Parses CephCI inventory files to extract image and optional VM size info."""

    def __init__(self, cephci_repo_path: str):
        """
        Initialize the inventory parser.

        Args:
            cephci_repo_path: Path to the cephci repository
        """
        self.logger = get_logger(__name__)
        self.repo_path = cephci_repo_path

    def parse_inventory_file(self, inventory_path: str) -> Optional[Dict[str, str]]:
        """
        Parse an inventory file and extract image-name (vm-size optional for ocpvirt).

        Args:
            inventory_path: Relative path to inventory file
                (e.g., conf/inventory/ocpvirt-rhel-9.4.yaml)

        Returns:
            Dictionary with 'image_name' and optional 'vm_size', or None if parsing fails
        """
        full_path = os.path.join(self.repo_path, inventory_path)

        if not os.path.exists(full_path):
            self.logger.warning(f"Inventory file not found: {full_path}")
            return None

        try:
            with open(full_path, 'r') as f:
                data = yaml.safe_load(f)

            if not data or not isinstance(data, dict):
                self.logger.warning(f"Invalid YAML structure in {inventory_path}")
                return None

            instance = data.get('instance', {})
            create = instance.get('create', {})

            image_name = create.get('image-name')
            vm_size = create.get('vm-size')  # absent on ocpvirt inventories

            if not image_name:
                self.logger.warning(f"Missing image-name in {inventory_path}")
                return None

            result = {
                'image_name': image_name,
                'vm_size': vm_size or '',
                'version_id': data.get('version_id', 'unknown'),
                'id': data.get('id', 'unknown')
            }

            self.logger.debug(f"Parsed {inventory_path}: {result}")
            return result

        except yaml.YAMLError as e:
            self.logger.error(f"YAML parsing error in {inventory_path}: {e}")
            return None
        except Exception as e:
            self.logger.error(f"Error parsing {inventory_path}: {e}")
            return None

    def parse_multiple_inventories(self, inventory_paths: List[str]) -> Dict[str, Dict[str, str]]:
        """
        Parse multiple inventory files.

        Args:
            inventory_paths: List of relative paths to inventory files

        Returns:
            Dictionary mapping inventory path to parsed data
        """
        results = {}

        for path in inventory_paths:
            parsed = self.parse_inventory_file(path)
            if parsed:
                results[path] = parsed

        return results

    def get_image_info_for_rhel_version(self, rhel_version: str) -> Optional[Dict[str, str]]:
        """
        Get image info for a specific RHEL version using config inventory_mapping.

        Args:
            rhel_version: RHEL version (e.g., "9.4", "10.0")

        Returns:
            Dictionary with image info or None
        """
        inventory_mapping = get_config().get('cephci.inventory_mapping', {}) or {}

        lookup_keys = [rhel_version]
        major = rhel_version.split('.')[0]
        if major not in lookup_keys:
            lookup_keys.append(major)

        inventory_path = None
        for key in lookup_keys:
            if key in inventory_mapping:
                inventory_path = inventory_mapping[key]
                break

        if not inventory_path:
            self.logger.warning(f"No inventory mapping for RHEL {rhel_version}")
            return None

        return self.parse_inventory_file(inventory_path)

    def extract_kernel_version_from_image_name(self, image_name: str) -> Optional[str]:
        """
        Extract version hint from image / datasource name.

        Args:
            image_name: e.g. "RHEL-9.4-x86_64-ga-latest" or "datasource://rhel94"

        Returns:
            Version hint or None
        """
        if image_name.startswith('datasource://'):
            alias = image_name.split('://', 1)[1]
            # rhel94 -> 9.4, rhel96 -> 9.6, rhel10 -> 10
            if alias.startswith('rhel') and alias[4:].isdigit():
                digits = alias[4:]
                if len(digits) == 2:
                    return f"{digits[0]}.{digits[1]}"
                return digits
            return alias

        parts = image_name.split('-')
        if len(parts) >= 2 and parts[0].upper() == 'RHEL':
            return parts[1]

        return None
