"""
CephCI Inventory File Parser

Parses inventory YAML files to extract image-name and vm-size information.
"""

import os
import yaml
from typing import Dict, Optional, List
from pathlib import Path

from src.utils.logger import get_logger


class InventoryParser:
    """Parses CephCI inventory files to extract image and VM size info."""
    
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
        Parse an inventory file and extract image-name and vm-size.
        
        Args:
            inventory_path: Relative path to inventory file (e.g., conf/inventory/rhel-9.4-server-x86_64.yaml)
            
        Returns:
            Dictionary with 'image_name' and 'vm_size' keys, or None if parsing fails
        """
        full_path = os.path.join(self.repo_path, inventory_path)
        
        if not os.path.exists(full_path):
            self.logger.warning(f"Inventory file not found: {full_path}")
            return None
        
        try:
            with open(full_path, 'r') as f:
                data = yaml.safe_load(f)
            
            # Extract image-name and vm-size from the YAML structure
            # Structure: instance -> create -> image-name, vm-size
            if not data or not isinstance(data, dict):
                self.logger.warning(f"Invalid YAML structure in {inventory_path}")
                return None
            
            instance = data.get('instance', {})
            create = instance.get('create', {})
            
            image_name = create.get('image-name')
            vm_size = create.get('vm-size')
            
            if not image_name or not vm_size:
                self.logger.warning(f"Missing image-name or vm-size in {inventory_path}")
                return None
            
            result = {
                'image_name': image_name,
                'vm_size': vm_size,
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
        Get image info for a specific RHEL version using the standard inventory mapping.
        
        Args:
            rhel_version: RHEL version (e.g., "9.4", "10.0", "8.10")
            
        Returns:
            Dictionary with image info or None
        """
        # Standard inventory mapping (from config.yaml)
        inventory_mapping = {
            "10": "conf/inventory/rhel-10.0-server-x86_64.yaml",
            "9": "conf/inventory/rhel-9.4-server-x86_64.yaml",
            "8": "conf/inventory/rhel-8.10-server-x86_64.yaml",
            "7": "conf/inventory/rhel-7.9-server-x86_64.yaml"
        }
        
        # Extract major version
        major_version = rhel_version.split('.')[0]
        
        inventory_path = inventory_mapping.get(major_version)
        if not inventory_path:
            self.logger.warning(f"No inventory mapping for RHEL {rhel_version}")
            return None
        
        return self.parse_inventory_file(inventory_path)
    
    def extract_kernel_version_from_image_name(self, image_name: str) -> Optional[str]:
        """
        Extract kernel version hint from image name.
        
        Args:
            image_name: Image name (e.g., "RHEL-9.4-x86_64-ga-latest")
            
        Returns:
            Kernel version hint or None
        """
        # Image names typically contain RHEL version which correlates to kernel version
        # Examples:
        # - RHEL-9.4-x86_64-ga-latest -> RHEL 9.4
        # - RHEL-10.0-x86_64-latest -> RHEL 10.0
        
        parts = image_name.split('-')
        if len(parts) >= 2 and parts[0].upper() == 'RHEL':
            return parts[1]  # Return version like "9.4" or "10.0"
        
        return None


# Made with Bob