"""
RHOS-D Image API - Direct REST API Implementation

Uses direct REST API calls instead of openstacksdk for better performance and reliability.
"""

import requests
import re
import json
import os
from typing import Dict, List, Optional, Any
from datetime import datetime
from pathlib import Path
import urllib3

from src.utils.logger import get_logger
from src.utils.config import get_config

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class RHOSDImageAPI:
    """Direct REST API client for RHOS-D Glance (Image) service."""
    
    def __init__(self):
        """Initialize the RHOS-D image API client."""
        self.logger = get_logger(__name__)
        self.config = get_config()
        
        # OpenStack configuration
        self.auth_url = self.config.get('openstack.auth_url')
        self.app_cred_id = self.config.get('openstack.username')
        self.app_cred_secret = self.config.get('openstack.password')
        self.region = self.config.get('openstack.region', 'regionOne')
        
        self.token = None
        self.glance_endpoint = None
        
        # Kernel version cache
        self.kernel_cache_file = Path('data/image_kernel_cache.json')
        self.kernel_cache = self._load_kernel_cache()
        
    def authenticate(self) -> bool:
        """
        Authenticate with OpenStack and get token + Glance endpoint.
        
        Returns:
            True if authentication successful, False otherwise
        """
        try:
            self.logger.info("Authenticating with RHOS-D...")
            
            auth_data = {
                "auth": {
                    "identity": {
                        "methods": ["application_credential"],
                        "application_credential": {
                            "id": self.app_cred_id,
                            "secret": self.app_cred_secret
                        }
                    }
                }
            }
            
            response = requests.post(
                f"{self.auth_url}/v3/auth/tokens",
                json=auth_data,
                verify=False,
                timeout=30
            )
            
            if response.status_code != 201:
                self.logger.error(f"Authentication failed: HTTP {response.status_code}")
                self.logger.error(f"Response: {response.text}")
                return False
            
            # Get token from header
            self.token = response.headers.get('X-Subject-Token')
            
            # Parse service catalog to find Glance endpoint
            catalog = response.json()['token']['catalog']
            
            for service in catalog:
                if service['type'] == 'image' and service['name'] == 'glance':
                    for endpoint in service['endpoints']:
                        if endpoint['interface'] == 'public' and endpoint['region'] == self.region:
                            self.glance_endpoint = endpoint['url']
                            break
                    break
            
            if not self.glance_endpoint:
                self.logger.error("Glance endpoint not found in service catalog")
                return False
            
            self.logger.info(f"✅ Authenticated successfully")
            self.logger.info(f"Glance endpoint: {self.glance_endpoint}")
            return True
            
        except requests.exceptions.Timeout:
            self.logger.error("Authentication request timed out")
            return False
        except Exception as e:
            self.logger.error(f"Authentication error: {e}")
            return False
    def _load_kernel_cache(self) -> Dict[str, str]:
        """Load kernel version cache from file."""
        if self.kernel_cache_file.exists():
            try:
                with open(self.kernel_cache_file, 'r') as f:
                    cache = json.load(f)
                    self.logger.info(f"📦 Loaded kernel cache with {len(cache)} entries")
                    return cache
            except Exception as e:
                self.logger.warning(f"Failed to load kernel cache: {e}")
        return {}
    
    def _save_kernel_cache(self):
        """Save kernel version cache to file."""
        try:
            self.kernel_cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.kernel_cache_file, 'w') as f:
                json.dump(self.kernel_cache, f, indent=2)
            self.logger.debug(f"💾 Saved kernel cache with {len(self.kernel_cache)} entries")
        except Exception as e:
            self.logger.warning(f"Failed to save kernel cache: {e}")
    
    def _get_cached_kernel(self, image_name: str) -> Optional[str]:
        """Get kernel version from cache.

        Returns the cached kernel string, or None if missing or NO_KERNEL.
        NO_KERNEL entries are removed from the cache so the next run
        retries the fetch (the failure may have been transient).
        """
        cached = self.kernel_cache.get(image_name)
        if cached == "NO_KERNEL":
            del self.kernel_cache[image_name]
            self._save_kernel_cache()
            self.logger.info(
                f"Cleared stale NO_KERNEL cache for {image_name} — will retry"
            )
            return None
        return cached
    
    def _cache_kernel(self, image_name: str, kernel_version: Optional[str]):
        """Cache kernel version for an image."""
        if kernel_version:
            self.kernel_cache[image_name] = kernel_version
            self._save_kernel_cache()
    
    
    def list_images(self, name_filter: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """
        List images from Glance.
        
        Args:
            name_filter: Optional name filter (substring match)
            limit: Maximum number of images to return
            
        Returns:
            List of image dictionaries
        """
        if not self.token or not self.glance_endpoint:
            self.logger.error("Not authenticated. Call authenticate() first.")
            return []
        
        try:
            headers = {
                'X-Auth-Token': self.token,
                'Content-Type': 'application/json'
            }
            
            params = {'limit': limit}
            if name_filter:
                params['name'] = name_filter
            
            response = requests.get(
                f"{self.glance_endpoint}/v2/images",
                headers=headers,
                params=params,
                verify=False,
                timeout=30
            )
            
            if response.status_code != 200:
                self.logger.error(f"Failed to list images: HTTP {response.status_code}")
                return []
            
            images = response.json().get('images', [])
            
            # Filter by name if provided (case-insensitive substring match)
            if name_filter:
                images = [img for img in images if name_filter.lower() in img.get('name', '').lower()]
            
            return images
            
        except requests.exceptions.Timeout:
            self.logger.error("List images request timed out")
            return []
        except Exception as e:
            self.logger.error(f"Error listing images: {e}")
            return []
    
    def get_image_details(self, image_id: str) -> Optional[Dict[str, Any]]:
        """
        Get full details of a specific image.
        
        Args:
            image_id: Image ID
            
        Returns:
            Full image dictionary with all metadata
        """
        if not self.token or not self.glance_endpoint:
            self.logger.error("Not authenticated. Call authenticate() first.")
            return None
        
        try:
            headers = {
                'X-Auth-Token': self.token,
                'Content-Type': 'application/json'
            }
            
            response = requests.get(
                f"{self.glance_endpoint}/v2/images/{image_id}",
                headers=headers,
                verify=False,
                timeout=30
            )
            
            if response.status_code != 200:
                self.logger.debug(f"Failed to get image details: HTTP {response.status_code}")
                return None
            
            return response.json()
            
        except requests.exceptions.Timeout:
            self.logger.debug("Get image details request timed out")
            return None
        except Exception as e:
            self.logger.debug(f"Error getting image details: {e}")
            return None
    
    def get_kernel_version_from_image(self, image: Dict[str, Any]) -> Optional[str]:
        """
        Extract kernel version from image metadata.
        
        Fetches kernel version from the compose Packages directory.
        Uses cache to avoid re-fetching for same images.
        
        Args:
            image: Image dictionary from Glance API
            
        Returns:
            Kernel version string or None
        """
        image_name = image.get('name', '')
        
        # Check cache first
        cached_kernel = self._get_cached_kernel(image_name)
        if cached_kernel:
            self.logger.debug(f"💾 Using cached kernel for {image_name}: {cached_kernel}")
            return cached_kernel
        
        # Get meta_image_path and meta_compose_url
        meta_image_path = image.get('meta_image_path', '')
        meta_compose_url = image.get('meta_compose_url', '')
        meta_release_version = image.get('meta_release_version', '')
        
        if not meta_image_path and not meta_compose_url:
            self.logger.debug(f"No meta_image_path or meta_compose_url for {image_name}")
            return None
        
        # Try to get kernel from compose Packages directory
        kernel_version = None
        if meta_compose_url:
            kernel_version = self._fetch_kernel_from_compose(meta_compose_url, meta_release_version)
            if kernel_version:
                # Cache the result
                self._cache_kernel(image_name, kernel_version)
                return kernel_version
        
        # Fallback: try to extract from image name
        kernel_pattern = r'kernel[_-]?(\d+\.\d+\.\d+[-\.\d]*)'
        match = re.search(kernel_pattern, image_name, re.IGNORECASE)
        if match:
            kernel_version = match.group(1)
            self.logger.debug(f"Extracted kernel version from name: {kernel_version}")
            # Cache the result
            self._cache_kernel(image_name, kernel_version)
            return kernel_version
        
        # Cache negative result (no kernel found) to avoid re-checking
        self._cache_kernel(image_name, "NO_KERNEL")
        
        return None
    
    def _fetch_kernel_from_compose(self, compose_url: str, release_version: str) -> Optional[str]:
        """
        Fetch kernel version from compose Packages directory.
        Tries multiple directory structures (BaseOS, Server) for compatibility.
        
        Args:
            compose_url: Compose URL (e.g., http://download.devel.redhat.com/rhel-8/rel-eng/...)
            release_version: Release version (e.g., "8.10.0")
            
        Returns:
            Kernel version string or None
        """
        # Determine directory paths based on RHEL version
        is_rhel7 = 'rhel-7' in compose_url.lower() or (release_version and release_version.startswith('7'))
        dir_path = 'Server/x86_64/os/Packages/' if is_rhel7 else 'BaseOS/x86_64/os/Packages/'

        # Try multiple host mirrors — the original URL first, then the pnq mirror
        original_url = compose_url if compose_url.endswith('/') else compose_url + '/'
        pnq_url = original_url.replace('download.devel.redhat.com', 'download.eng.pnq.redhat.com')

        # Also try http:// if the original is https:// (some mirrors only serve http)
        base_urls = []
        for u in [original_url, pnq_url]:
            base_urls.append(u)
            if u.startswith('https://'):
                base_urls.append(u.replace('https://', 'http://', 1))

        self.logger.info(f"🔍 Attempting to fetch kernel from compose:")
        self.logger.info(f"   Compose URL: {compose_url}")
        self.logger.info(f"   Directory: {dir_path} ({'RHEL 7' if is_rhel7 else 'RHEL 8+'})")

        kernel_pattern = r'kernel-(\d+\.\d+\.\d+-\d+(?:\.\d+)*\.el\d+(?:_\d+)?)'

        for base_url in base_urls:
            packages_url = base_url + dir_path

            try:
                self.logger.info(f"   Trying: {packages_url}")
                response = requests.get(packages_url, timeout=15, verify=False)

                if response.status_code != 200:
                    self.logger.info(f"   HTTP {response.status_code} - trying next mirror")
                    continue

                matches = re.findall(kernel_pattern, response.text)
                if matches:
                    kernel_version = matches[0]
                    self.logger.info(f"✅ Found kernel version: {kernel_version}")
                    self.logger.info(f"   From: {packages_url}")
                    return kernel_version
                else:
                    self.logger.info(f"   No kernel RPMs found in listing - trying next mirror")

            except requests.exceptions.Timeout:
                self.logger.warning(f"   Timeout fetching {packages_url}")
                continue
            except requests.exceptions.ConnectionError as e:
                self.logger.warning(f"   Connection error for {packages_url}: {e}")
                continue
            except Exception as e:
                self.logger.warning(f"   Error fetching {packages_url}: {e}")
                continue
        
        # All paths failed
        self.logger.warning(f"❌ Could not fetch kernel from any directory structure")
        self.logger.warning(f"   Tried: {', '.join(u + dir_path for u in base_urls)}")
        return None
    
    def _compare_kernel_versions(self, kernel1: str, kernel2: str) -> int:
        """
        Compare two kernel versions.
        
        Args:
            kernel1: First kernel version (e.g., '5.14.0-427.13.1.el9_4')
            kernel2: Second kernel version (e.g., '5.14.0-427.20.1.el9_4')
        
        Returns:
            -1 if kernel1 < kernel2
             0 if kernel1 == kernel2
             1 if kernel1 > kernel2
        """
        try:
            # Extract version components
            # Format: X.Y.Z-A.B.C.D_E.elN_M or X.Y.Z-A.B.C.elN_M
            def parse_version(v: str) -> tuple:
                # Remove .x86_64 suffix if present
                v = v.replace('.x86_64', '')
                
                # Split by '-' to separate main version from release
                parts = v.split('-')
                if len(parts) < 2:
                    return (0, 0, 0, 0, 0, 0, 0, 0)
                
                # Parse main version (e.g., '5.14.0')
                main_parts = parts[0].split('.')
                major = int(main_parts[0]) if len(main_parts) > 0 else 0
                minor = int(main_parts[1]) if len(main_parts) > 1 else 0
                patch = int(main_parts[2]) if len(main_parts) > 2 else 0
                
                # Parse release version (e.g., '427.13.1' or '70.179.1.6011_2522553262')
                # Split by '.el' first to remove the distro suffix
                release_parts = parts[1].split('.el')[0].split('.')
                rel_major = int(release_parts[0]) if len(release_parts) > 0 else 0
                rel_minor = int(release_parts[1]) if len(release_parts) > 1 else 0
                rel_patch = int(release_parts[2]) if len(release_parts) > 2 else 0
                
                # Handle additional version components (e.g., .6011_2522553262)
                # Split by '_' to separate build numbers
                rel_build1 = 0
                rel_build2 = 0
                if len(release_parts) > 3:
                    # Fourth component might have underscore (e.g., '6011_2522553262')
                    fourth_part = release_parts[3]
                    if '_' in fourth_part:
                        build_parts = fourth_part.split('_')
                        rel_build1 = int(build_parts[0]) if build_parts[0].isdigit() else 0
                        rel_build2 = int(build_parts[1]) if len(build_parts) > 1 and build_parts[1].isdigit() else 0
                    else:
                        rel_build1 = int(fourth_part) if fourth_part.isdigit() else 0
                
                return (major, minor, patch, rel_major, rel_minor, rel_patch, rel_build1, rel_build2)
            
            v1 = parse_version(kernel1)
            v2 = parse_version(kernel2)
            
            if v1 < v2:
                return -1
            elif v1 > v2:
                return 1
            else:
                return 0
                
        except Exception as e:
            self.logger.warning(f"Failed to compare kernel versions: {e}")
            return 0
    
    def search_images_for_inventory(
        self,
        inventory_image_info: Dict[str, Dict[str, str]],
        max_images_per_inventory: int = 1,
        jira_kernel_versions: Optional[Dict[str, str]] = None
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Search for images matching inventory requirements.
        Optionally filters to find kernels lower than JIRA kernel version.
        
        Args:
            inventory_image_info: Dictionary from inventory_image_info.json
            max_images_per_inventory: Maximum images to return per inventory (default: 1 for testing)
            jira_kernel_versions: Optional dict mapping inventory path to JIRA kernel version
                                 If provided, will filter to find kernels lower than JIRA kernel
            
        Returns:
            Dictionary mapping inventory path to list of matching images with kernel versions
        """
        if not self.token:
            if not self.authenticate():
                return {}
        
        # Fetch all images once (optimization to avoid multiple API calls)
        self.logger.info("🔄 Fetching all images from RHOS-D (this may take 10-15 seconds)...")
        all_images = self.list_images(name_filter=None, limit=1000)
        self.logger.info(f"✅ Fetched {len(all_images)} total images from RHOS-D")
        
        results = {}
        
        for inv_path, info in inventory_image_info.items():
            image_name = info.get('image_name')
            if not image_name:
                continue
            
            self.logger.info(f"\nProcessing {inv_path}")
            self.logger.info(f"  Looking for image: {image_name}")
            
            # Extract base pattern (remove -latest, -ga, etc.)
            base_pattern = self._extract_base_pattern(image_name)
            self.logger.info(f"  Search pattern: {base_pattern}")
            
            # Find exact match (ga-latest) and pattern matches
            exact_matches = [img for img in all_images if img.get('name', '').lower() == image_name.lower()]
            
            # Get ALL images matching the pattern (for Priority 2)
            pattern_matches = [img for img in all_images if base_pattern.lower() in img.get('name', '').lower()]
            self.logger.info(f"  Found {len(pattern_matches)} images matching pattern '{base_pattern}'")
            
            # Sort pattern matches by created_at (newest first)
            pattern_matches.sort(key=lambda x: x.get('created_at', ''), reverse=True)
            
            if exact_matches:
                self.logger.info(f"  ✅ Found exact match: '{image_name}'")
            
            # Get JIRA kernel version for this inventory (if provided)
            jira_kernel = None
            if jira_kernel_versions and inv_path in jira_kernel_versions:
                jira_kernel = jira_kernel_versions[inv_path]
                self.logger.info(f"  JIRA Repo kernel version: {jira_kernel}")
                self.logger.info(f"  Strategy: Check ga-latest first, then try ALL images matching pattern until found")
            
            # Priority 1: Check exact match (ga-latest) first
            images_with_kernel = []
            ga_latest_checked = False
            ga_latest_id = None
            
            if exact_matches and jira_kernel:
                ga_latest_id = exact_matches[0].get('id')
                self.logger.info(f"  📌 Priority 1: Checking ga-latest image first")
                ga_img = exact_matches[0]
                image_id = ga_img.get('id')
                if image_id:
                    full_details = self.get_image_details(image_id)
                    if full_details:
                        ga_img = full_details
                
                kernel_version = self.get_kernel_version_from_image(ga_img)
                ga_img['kernel_version'] = kernel_version
                ga_latest_checked = True
                
                if kernel_version:
                    comparison = self._compare_kernel_versions(kernel_version, jira_kernel)
                    if comparison < 0:
                        # ga-latest kernel is lower than JIRA kernel - perfect!
                        self.logger.info(f"  ✅ ga-latest kernel {kernel_version} < JIRA kernel {jira_kernel}")
                        images_with_kernel.append(ga_img)
                    else:
                        self.logger.info(f"  ⚠️  ga-latest kernel {kernel_version} >= JIRA kernel {jira_kernel}")
                        self.logger.info(f"  📌 Priority 2: Trying ALL images sorted by creation date")
                else:
                    self.logger.warning(f"  ⚠️  ga-latest has no kernel version")
                    self.logger.info(f"  📌 Priority 2: Trying ALL images sorted by creation date")
            
            # Priority 2: If ga-latest didn't work, try ALL images matching pattern
            if not images_with_kernel and jira_kernel and pattern_matches:
                # Try ALL images from pattern matches (already sorted by creation date)
                max_attempts = len(pattern_matches)
                self.logger.info(f"  Will try all {max_attempts} images from pattern matches")
                
                for idx, img in enumerate(pattern_matches[:max_attempts]):
                    # Skip ga-latest if already checked
                    if ga_latest_checked and ga_latest_id and img.get('id') == ga_latest_id:
                        self.logger.debug(f"  Skipping ga-latest (already checked)")
                        continue
                    
                    # Get full image details
                    image_id = img.get('id')
                    if image_id:
                        self.logger.debug(f"  Attempt {idx+1}: Fetching details for {img.get('name')}")
                        full_details = self.get_image_details(image_id)
                        if full_details:
                            img = full_details
                    
                    kernel_version = self.get_kernel_version_from_image(img)
                    img['kernel_version'] = kernel_version
                    
                    if kernel_version:
                        comparison = self._compare_kernel_versions(kernel_version, jira_kernel)
                        if comparison < 0:
                            # Found a kernel lower than JIRA kernel
                            self.logger.info(f"  ✅ Image {idx+1} ({img.get('name')}) kernel {kernel_version} < JIRA kernel {jira_kernel}")
                            images_with_kernel.append(img)
                            break  # Found one, stop searching
                        else:
                            self.logger.info(f"  ⏭️  Image {idx+1} kernel {kernel_version} >= JIRA kernel, trying next")
                    else:
                        self.logger.warning(f"  ⚠️  Image {idx+1} has no kernel, trying next")
            
            # Fallback: If no JIRA kernel filtering, just get the first valid image
            if not images_with_kernel and not jira_kernel and pattern_matches:
                max_attempts = min(max_images_per_inventory * 3, len(pattern_matches))
                self.logger.info(f"  No JIRA kernel filtering - trying up to {max_attempts} images")
                
                for idx, img in enumerate(pattern_matches[:max_attempts]):
                    image_id = img.get('id')
                    if image_id:
                        full_details = self.get_image_details(image_id)
                        if full_details:
                            img = full_details
                    
                    kernel_version = self.get_kernel_version_from_image(img)
                    img['kernel_version'] = kernel_version
                    
                    if kernel_version:
                        images_with_kernel.append(img)
                        self.logger.info(f"  ✅ Successfully extracted kernel from image {idx+1}")
                        if len(images_with_kernel) >= max_images_per_inventory:
                            break
                    else:
                        self.logger.warning(f"  ⚠️  Image {idx+1} has no kernel, trying next")
            
            # If no images with kernel found, include the first image anyway (with None kernel)
            if not images_with_kernel and pattern_matches:
                self.logger.warning(f"  ⚠️ No images with valid kernel found, including first image anyway")
                img = pattern_matches[0]
                image_id = img.get('id')
                if image_id:
                    full_details = self.get_image_details(image_id)
                    if full_details:
                        img = full_details
                img['kernel_version'] = None
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
        # Extract RHEL-X.Y or RHEL-X.Y.Z pattern
        # Examples:
        # RHEL-9.4.0-x86_64-ga-latest -> RHEL-9.4
        # RHEL-10.0-x86_64-ga-latest -> RHEL-10.0
        # RHEL-8.10.0-x86_64-ga-latest -> RHEL-8.10
        
        match = re.match(r'(RHEL-\d+\.\d+)', image_name, re.IGNORECASE)
        if match:
            return match.group(1)
        
        return image_name


# Made with Bob