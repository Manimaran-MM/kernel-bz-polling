"""
RHOS-D Image Manager

Queries OpenStack (RHOS-D) to fetch image information and kernel versions.
"""

import re
import threading
from typing import Dict, List, Optional, Any
from datetime import datetime

from src.utils.logger import get_logger
from src.utils.config import get_config


class RHOSDImageManager:
    """Manages RHOS-D image queries and kernel version extraction."""
    
    def __init__(self):
        """Initialize the RHOS-D image manager."""
        self.logger = get_logger(__name__)
        self.config = get_config()
        
        # OpenStack configuration
        self.auth_url = self.config.get('openstack.auth_url')
        self.username = self.config.get('openstack.username')
        self.password = self.config.get('openstack.password')
        self.project_name = self.config.get('openstack.project_name')
        self.domain = self.config.get('openstack.domain')
        self.region = self.config.get('openstack.region')
        
        self.glance_client = None
        
    def connect(self) -> bool:
        """
        Connect to OpenStack Glance service.
        
        Returns:
            True if connection successful, False otherwise
        """
        try:
            from openstack import connection
            
            self.logger.info("Connecting to RHOS-D OpenStack...")
            
            # Check if using application credentials (username looks like an ID)
            auth_version = self.config.get('openstack.auth_version', '3')
            
            self.logger.info(f"Auth URL: {self.auth_url}")
            self.logger.info(f"Region: {self.region}")
            self.logger.info(f"Auth Version: {auth_version}")
            
            if auth_version == "3.x_appcred" or len(self.username) > 30:
                # Using application credentials
                self.logger.info("Using application credentials authentication")
                self.logger.info(f"App Credential ID: {self.username[:10]}...")
                
                conn = connection.Connection(
                    auth_url=self.auth_url,
                    auth_type='v3applicationcredential',
                    application_credential_id=self.username,
                    application_credential_secret=self.password,
                    region_name=self.region,
                    timeout=30,  # 30 second timeout
                    interface='public',  # Force public endpoint
                    verify=False  # Disable SSL verification
                )
            else:
                # Using username/password
                self.logger.info("Using username/password authentication")
                self.logger.info(f"Username: {self.username}")
                self.logger.info(f"Project: {self.project_name}")
                
                conn = connection.Connection(
                    auth_url=self.auth_url,
                    project_name=self.project_name,
                    username=self.username,
                    password=self.password,
                    user_domain_name=self.domain,
                    project_domain_name=self.domain,
                    region_name=self.region,
                    timeout=30,  # 30 second timeout
                    verify=False  # Disable SSL verification
                )
            
            self.logger.info("Testing connection with image list...")
            self.glance_client = conn.image
            
            # Test connection with threading timeout (works on macOS)
            result = [None]
            error = [None]
            
            def test_connection():
                try:
                    result[0] = list(self.glance_client.images(limit=1))
                except Exception as e:
                    error[0] = e
            
            thread = threading.Thread(target=test_connection)
            thread.daemon = True
            thread.start()
            thread.join(timeout=30)
            
            if thread.is_alive():
                self.logger.error("Connection test timed out after 30 seconds")
                return False
            
            if error[0]:
                self.logger.error(f"Connection test failed: {error[0]}")
                return False
            
            self.logger.info("✅ Successfully connected to RHOS-D")
            return True
            
        except ImportError:
            self.logger.error("openstacksdk not installed. Install with: pip install openstacksdk")
            return False
        except Exception as e:
            self.logger.error(f"Failed to connect to RHOS-D: {e}")
            return False
    
    def search_images(self, name_pattern: str) -> List[Dict[str, Any]]:
        """
        Search for images matching a name pattern.
        
        Args:
            name_pattern: Image name pattern (e.g., "RHEL-9.4")
            
        Returns:
            List of image dictionaries with metadata
        """
        if not self.glance_client:
            self.logger.error("Not connected to RHOS-D. Call connect() first.")
            return []
        
        try:
            self.logger.info(f"Searching for images matching: {name_pattern}")
            
            images = []
            for image in self.glance_client.images():
                if name_pattern.lower() in image.name.lower():
                    image_info = {
                        'id': image.id,
                        'name': image.name,
                        'status': image.status,
                        'size': image.size,
                        'created_at': image.created_at,
                        'updated_at': image.updated_at,
                        'properties': dict(image.properties) if image.properties else {}
                    }
                    images.append(image_info)
            
            self.logger.info(f"Found {len(images)} images matching '{name_pattern}'")
            return images
            
        except Exception as e:
            self.logger.error(f"Error searching images: {e}")
            return []
    
    def get_kernel_version_from_image(self, image_info: Dict[str, Any]) -> Optional[str]:
        """
        Extract kernel version from image metadata.
        
        Args:
            image_info: Image information dictionary
            
        Returns:
            Kernel version string or None
        """
        properties = image_info.get('properties', {})
        
        # Common property keys for kernel version
        kernel_keys = [
            'kernel_version',
            'kernel',
            'os_kernel_version',
            'kernel-version'
        ]
        
        for key in kernel_keys:
            if key in properties:
                kernel_version = properties[key]
                self.logger.debug(f"Found kernel version in property '{key}': {kernel_version}")
                return kernel_version
        
        # Try to extract from image name if not in properties
        # Example: RHEL-9.4.0-x86_64-ga-latest might have kernel in description
        image_name = image_info.get('name', '')
        
        # Check if kernel version is in the name (e.g., "kernel-5.14.0-427")
        kernel_pattern = r'kernel[_-]?(\d+\.\d+\.\d+[-\.\d]*)'
        match = re.search(kernel_pattern, image_name, re.IGNORECASE)
        if match:
            kernel_version = match.group(1)
            self.logger.debug(f"Extracted kernel version from name: {kernel_version}")
            return kernel_version
        
        self.logger.warning(f"No kernel version found for image: {image_name}")
        return None
    
    def get_kernel_versions_for_inventory(
        self,
        inventory_image_info: Dict[str, Dict[str, str]]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Get kernel versions for all images in inventory info.
        
        Args:
            inventory_image_info: Dictionary from inventory_image_info.json
            
        Returns:
            Dictionary mapping inventory path to list of matching images with kernel versions
        """
        if not self.glance_client:
            if not self.connect():
                return {}
        
        results = {}
        
        for inv_path, info in inventory_image_info.items():
            image_name = info.get('image_name')
            if not image_name:
                continue
            
            self.logger.info(f"Processing {inv_path}")
            self.logger.info(f"  Looking for image: {image_name}")
            
            # Extract base pattern (remove -latest, -ga, etc.)
            # RHEL-9.4.0-x86_64-ga-latest -> RHEL-9.4
            base_pattern = self._extract_base_pattern(image_name)
            
            # Search for matching images
            images = self.search_images(base_pattern)
            
            # Extract kernel versions
            images_with_kernel = []
            for img in images:
                kernel_version = self.get_kernel_version_from_image(img)
                img['kernel_version'] = kernel_version
                images_with_kernel.append(img)
            
            results[inv_path] = images_with_kernel
            
            if images_with_kernel:
                self.logger.info(f"  ✅ Found {len(images_with_kernel)} matching images")
                for img in images_with_kernel[:3]:  # Show first 3
                    kernel = img.get('kernel_version', 'unknown')
                    self.logger.info(f"     - {img['name']}: kernel {kernel}")
            else:
                self.logger.warning(f"  ⚠️  No matching images found")
        
        return results
    
    def _extract_base_pattern(self, image_name: str) -> str:
        """
        Extract base pattern from image name for searching.
        
        Args:
            image_name: Full image name (e.g., "RHEL-9.4.0-x86_64-ga-latest")
            
        Returns:
            Base pattern (e.g., "RHEL-9.4")
        """
        # Remove common suffixes
        suffixes = ['-latest', '-ga-latest', '-x86_64', '-aarch64', '-ga']
        pattern = image_name
        
        for suffix in suffixes:
            pattern = pattern.replace(suffix, '')
        
        # Extract RHEL-X.Y pattern
        match = re.match(r'(RHEL-\d+\.\d+)', pattern, re.IGNORECASE)
        if match:
            return match.group(1)
        
        return pattern
    
    def get_latest_image_with_kernel(
        self,
        image_name_pattern: str,
        required_kernel: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Get the latest image matching pattern, optionally with specific kernel version.
        
        Args:
            image_name_pattern: Image name pattern to search
            required_kernel: Optional required kernel version
            
        Returns:
            Image info dictionary or None
        """
        images = self.search_images(image_name_pattern)
        
        if not images:
            return None
        
        # Add kernel versions
        for img in images:
            img['kernel_version'] = self.get_kernel_version_from_image(img)
        
        # Filter by kernel version if specified
        if required_kernel:
            images = [img for img in images if img.get('kernel_version') == required_kernel]
        
        if not images:
            return None
        
        # Sort by created_at (newest first)
        images.sort(key=lambda x: x.get('created_at', ''), reverse=True)
        
        return images[0]


# Made with Bob