"""
CephCI Command Builder

Builds cephci commands from JIRA issue data.
"""

import os
import re
from typing import Dict, Any, List, Optional
from pathlib import Path

from src.utils.logger import get_logger
from src.utils.config import get_config
from src.jira.parser import normalize_fixed_in_build


class CephCICommandBuilder:
    """Builds cephci commands from JIRA issue data."""
    
    def __init__(self):
        """Initialize the command builder."""
        self.logger = get_logger(__name__)
        self.config = get_config()
        
        # Get cephci configuration
        self.cephci_repo = self.config.get('cephci.repo_path', '/ceph/cephci')
        self.cephci_branch = self.config.get('cephci.default_branch', 'master')
        self.log_base_dir = self.config.get('cephci.log_dir', '/ceph/ceph-qe-logs/manim/logs/kernel-logs')
        
        # RHEL version to rhbuild mapping
        self.rhbuild_mapping = self.config.get('cephci.rhbuild_mapping', {
            '10': '9.1',
            '9': '9.1',
            '8': '7.1',
            '7': '5.3'
        })
        
        # RHEL version to inventory mapping
        self.inventory_mapping = self.config.get('cephci.inventory_mapping', {
            '10': 'conf/inventory/rhel-10.0-server-x86_64.yaml',
            '9': 'conf/inventory/rhel-9.4-server-x86_64.yaml',
            '8': 'conf/inventory/rhel-8.10-server-x86_64.yaml',
            '7': 'conf/inventory/rhel-7.9-server-x86_64.yaml'
        })
        
        # Kernel verification parameters
        self.instance_name_prefix = self.config.get(
            'cephci.instance_name_prefix', 'auto-kernel-jira-'
        )

        self.kernel_verification = self.config.get('cephci.kernel_verification', {
            'global_conf': 'conf/tentacle/cephfs/kernel-validation/kernel_verification.yaml',
            'global_conf_rhel8': 'conf/tentacle/cephfs/kernel-validation/kernel_verification_rhel8_client.yaml',
            'suite_pre': 'suites/tentacle/cephfs/kernel-validation/kernel_pre_verification.yaml',
            'suite_post': 'suites/tentacle/cephfs/kernel-validation/kernel_post_verification.yaml',
            'suite_pre_rhel8': 'suites/tentacle/cephfs/kernel-validation/kernel_pre_verification_rhel8_client.yaml',
            'suite_post_rhel8': 'suites/tentacle/cephfs/kernel-validation/kernel_post_verification_rhel8_client.yaml',
            'osp_cred': 'osp/osp-cred-ci-2.yaml',
            'build': 'stable'
        })
        
    def build_command(
        self,
        jira_data: Dict[str, Any],
        branch: Optional[str] = None,
        additional_params: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Build a cephci command from JIRA issue data.
        
        Args:
            jira_data: Parsed JIRA issue data
            branch: Optional cephci branch to use (defaults to config)
            additional_params: Optional additional parameters
            
        Returns:
            Dictionary containing:
                - command: The full cephci command string
                - params: Dictionary of parameters used
                - log_dir: Directory where logs will be stored
        """
        jira_id = jira_data.get('jira_id', 'UNKNOWN')
        self.logger.info(f"Building cephci command for {jira_id}")
        
        # Use provided branch or default
        effective_branch = branch or self.cephci_branch
        
        # Build parameters
        params = self._build_parameters(jira_data, effective_branch, additional_params)
        
        # Build the command
        command = self._construct_command(params)
        
        # Determine log directory
        verification_type = jira_data.get('verification_type', 'pre')
        log_dir = self._get_log_directory(jira_id, verification_type)
        
        fix_versions = jira_data.get('fix_versions', [])
        rhel_major = self._get_rhel_major(fix_versions)
        is_rhel8 = rhel_major == '8'

        result = {
            'command': command,
            'params': params,
            'log_dir': log_dir,
            'jira_id': jira_id,
            'branch': effective_branch,
            'inventory': params.get('--inventory'),
            'is_rhel8': is_rhel8,
            'global_conf': params.get('--global-conf'),
        }
        
        self.logger.info(f"Built command for {jira_id}")
        self.logger.debug(f"Command: {command}")
        
        return result
    
    def _get_instance_name(self, jira_id: str) -> str:
        """Build cephci --instances-name from JIRA key (e.g. auto-kernel-jira-RHEL-182254)."""
        prefix = self.instance_name_prefix or ''
        if prefix and jira_id.startswith(prefix):
            return jira_id
        return f"{prefix}{jira_id}"

    def _get_rhel_major(self, fix_versions: List[str]) -> Optional[str]:
        """Extract the RHEL major version from fix_versions (e.g. '8', '9', '10')."""
        for version in fix_versions:
            clean = version.replace('rhel-', '').replace('RHEL-', '')
            major = clean.split('.')[0] if '.' in clean else clean
            if major.isdigit():
                return major
        return None

    def _build_parameters(
        self,
        jira_data: Dict[str, Any],
        branch: str,
        additional_params: Optional[Dict[str, str]] = None
    ) -> Dict[str, str]:
        """
        Build cephci command parameters from JIRA data.
        
        Args:
            jira_data: Parsed JIRA issue data
            branch: CephCI branch to use
            additional_params: Optional additional parameters
            
        Returns:
            Dictionary of parameter name to value
        """
        jira_id = jira_data.get('jira_id', 'UNKNOWN')
        repo_url = jira_data.get('repo_url')
        fix_versions = jira_data.get('fix_versions', [])
        
        params = {}
        
        # Required parameters
        instance_name = self._get_instance_name(jira_id)
        params['--instances-name'] = instance_name
        self.logger.info(f"Using instance name: {instance_name}")
        
        # Log directory (suffixed with verification type to avoid overwrites)
        verification_type = jira_data.get('verification_type', 'pre')
        log_dir = self._get_log_directory(jira_id, verification_type)
        params['--log-dir'] = log_dir
        
        # Required parameters
        params['--log-level'] = 'DEBUG'
        params['--cloud'] = 'openstack'
        
        # Detect RHEL-8 for special handling
        rhel_major = self._get_rhel_major(fix_versions)
        is_rhel8 = rhel_major == '8'
        
        # Determine rhbuild based on fix versions
        rhbuild = self._get_rhbuild(fix_versions)
        if rhbuild:
            params['--rhbuild'] = rhbuild
            self.logger.info(f"Using rhbuild: {rhbuild} for fix versions: {fix_versions}")
        
        # Determine platform — RHEL-8 runs on RHEL-9 VMs
        if is_rhel8:
            params['--platform'] = 'rhel-9'
            self.logger.info(f"RHEL-8 JIRA: overriding platform to rhel-9")
        else:
            platform = self._get_platform(fix_versions)
            if platform:
                params['--platform'] = platform
                self.logger.info(f"Using platform: {platform} for fix versions: {fix_versions}")
        
        # Kernel verification parameters — RHEL-8 uses separate conf/suite files
        if is_rhel8:
            params['--global-conf'] = self.kernel_verification.get('global_conf_rhel8')
            if verification_type == 'post':
                params['--suite'] = self.kernel_verification.get('suite_post_rhel8')
            else:
                params['--suite'] = self.kernel_verification.get('suite_pre_rhel8')
            self.logger.info(f"RHEL-8 JIRA: using rhel8 global-conf and suite")
        else:
            params['--global-conf'] = self.kernel_verification.get('global_conf')
            if verification_type == 'post':
                params['--suite'] = self.kernel_verification.get('suite_post')
            else:
                params['--suite'] = self.kernel_verification.get('suite_pre')
        params['--osp-cred'] = self.kernel_verification.get('osp_cred')
        
        # Determine inventory — RHEL-8 uses RHEL-9 inventory (VMs are RHEL-9)
        if is_rhel8:
            inventory = self.inventory_mapping.get('9', 'conf/inventory/rhel-9.4-server-x86_64.yaml')
            params['--inventory'] = inventory
            self.logger.info(f"RHEL-8 JIRA: using RHEL-9 inventory {inventory}")
        else:
            inventory = self._get_inventory(fix_versions)
            if inventory:
                params['--inventory'] = inventory
                self.logger.info(f"Using inventory: {inventory} for fix versions: {fix_versions}")
        
        # Custom config: pre or post depending on verification type
        verification_type = jira_data.get('verification_type', 'pre')
        fixed_in_build = jira_data.get('fixed_in_build', '')

        if verification_type == 'post' and fixed_in_build:
            brew_url = self._build_brew_rpm_url(fixed_in_build)
            if brew_url:
                params['--custom-config post'] = brew_url
                self.logger.info(f"Using post verification with brew URL: {brew_url}")
            else:
                self.logger.warning(f"Failed to build brew URL from '{fixed_in_build}' for {jira_id}")
        elif repo_url:
            params['--custom-config pre'] = repo_url
            self.logger.info(f"Using pre verification with Repo URL: {repo_url}")
        else:
            self.logger.warning(f"No Repo URL or Fixed in Build found for {jira_id}")
        
        # Build parameter
        params['--build'] = self.kernel_verification.get('build', 'stable')
        
        # Flags without values
        params['--skip-sos-report'] = ''
        params['--store'] = ''
        
        # Add any additional parameters
        if additional_params:
            params.update(additional_params)
        
        return params
    
    def _get_rhbuild(self, fix_versions: List[str]) -> Optional[str]:
        """
        Determine rhbuild based on fix versions.
        
        Args:
            fix_versions: List of fix version strings (e.g., ['9.6.0', '10.0.0'])
            
        Returns:
            rhbuild value (e.g., '9.1' or '5.3') or None
        """
        if not fix_versions:
            self.logger.warning("No fix versions provided, cannot determine rhbuild")
            return None
        
        # Extract major version from first fix version
        for version in fix_versions:
            # Remove 'rhel-' prefix if present (e.g., 'rhel-10.1.z' -> '10.1.z')
            clean_version = version.replace('rhel-', '').replace('RHEL-', '')
            
            # Extract major version number (e.g., '9' from '9.6.0' or '10' from '10.1.z')
            major_version = clean_version.split('.')[0] if '.' in clean_version else clean_version
            
            # Look up rhbuild in mapping
            if major_version in self.rhbuild_mapping:
                rhbuild = self.rhbuild_mapping[major_version]
                self.logger.debug(f"Mapped version {version} (major: {major_version}) to rhbuild {rhbuild}")
                return str(rhbuild)
        
        # Default to 9.1 if no mapping found
        self.logger.warning(f"No rhbuild mapping found for versions {fix_versions}, defaulting to 9.1")
        return '9.1'
    
    def _get_inventory(self, fix_versions: List[str]) -> Optional[str]:
        """
        Determine inventory file based on fix versions.
        Extracts full minor version (e.g., 9.2, 8.8) and maps to exact inventory file.
        
        Args:
            fix_versions: List of fix version strings (e.g., ['rhel-9.2.0.z', 'rhel-8.8.0.z'])
            
        Returns:
            inventory file path or None
        """
        if not fix_versions:
            self.logger.warning("No fix versions provided, cannot determine inventory")
            return None
        
        # Extract full minor version from first fix version
        for version in fix_versions:
            # Remove 'rhel-' prefix if present (e.g., 'rhel-10.1.z' -> '10.1.z')
            clean_version = version.replace('rhel-', '').replace('RHEL-', '')
            
            # Extract major.minor version (e.g., '9.2' from '9.2.0.z' or '10.1' from '10.1.z')
            version_parts = clean_version.split('.')
            if len(version_parts) >= 2:
                # Full minor version (e.g., '9.2', '8.8', '10.1')
                minor_version = f"{version_parts[0]}.{version_parts[1]}"
                
                # Build inventory file path
                inventory = f"conf/inventory/rhel-{minor_version}-server-x86_64.yaml"
                self.logger.debug(f"Mapped version {version} to inventory {inventory}")
                return inventory
            elif len(version_parts) == 1:
                # Only major version, use default mapping
                major_version = version_parts[0]
                if major_version in self.inventory_mapping:
                    inventory = self.inventory_mapping[major_version]
                    self.logger.debug(f"Mapped version {version} (major: {major_version}) to inventory {inventory}")
                    return inventory
        
        # Default to RHEL 9 inventory if no mapping found
        default_inventory = 'conf/inventory/rhel-9.4-server-x86_64.yaml'
        self.logger.warning(f"No inventory mapping found for versions {fix_versions}, defaulting to {default_inventory}")
        return default_inventory
    
    def _get_platform(self, fix_versions: List[str]) -> Optional[str]:
        """
        Determine platform based on fix versions.
        
        Args:
            fix_versions: List of fix version strings (e.g., ['9.6.0', '10.0.0'])
            
        Returns:
            platform string (e.g., 'rhel-9', 'rhel-10') or None
        """
        if not fix_versions:
            self.logger.warning("No fix versions provided, cannot determine platform")
            return None
        
        # Extract major version from first fix version
        for version in fix_versions:
            # Remove 'rhel-' prefix if present (e.g., 'rhel-10.1.z' -> '10.1.z')
            clean_version = version.replace('rhel-', '').replace('RHEL-', '')
            
            # Extract major version number (e.g., '9' from '9.6.0' or '10' from '10.1.z')
            major_version = clean_version.split('.')[0] if '.' in clean_version else clean_version
            
            # Build platform string
            platform = f"rhel-{major_version}"
            self.logger.debug(f"Mapped version {version} (major: {major_version}) to platform {platform}")
            return platform
        
        # Default to rhel-9 if no mapping found
        self.logger.warning(f"No platform determined for versions {fix_versions}, defaulting to rhel-9")
        return 'rhel-9'
    
    def _build_brew_rpm_url(self, fixed_in_build: str) -> Optional[str]:
        """Build a brew RPM URL from the Fixed in Build version string.

        Example:
            Input:  kernel-5.14.0-687.4.1.el9_8
            Output: https://download2.engineering.redhat.com/brewroot/vol/rhel-9/packages/kernel/5.14.0/687.4.1.el9_8/x86_64/kernel-5.14.0-687.4.1.el9_8.x86_64.rpm

        Returns None if the string cannot be parsed.
        """
        if not fixed_in_build:
            return None

        nvr = normalize_fixed_in_build(fixed_in_build)
        if not nvr:
            return None

        is_rt = nvr.startswith("kernel-rt-")
        if is_rt:
            version_str = nvr[len("kernel-rt-"):]
            pkg_dir = "kernel/rt"
            rpm_prefix = "kernel-rt"
        elif nvr.startswith("kernel-"):
            version_str = nvr[len("kernel-"):]
            pkg_dir = "kernel"
            rpm_prefix = "kernel"
        else:
            self.logger.warning(f"Cannot parse Fixed in Build: {fixed_in_build}")
            return None

        parts = version_str.split("-", 1)
        if len(parts) != 2:
            self.logger.warning(f"Cannot parse Fixed in Build: {fixed_in_build}")
            return None

        base_ver, suffix = parts

        el_match = re.search(r"el(\d+)", suffix)
        if not el_match:
            self.logger.warning(f"No 'el' tag found in: {fixed_in_build}")
            return None

        rhel_tag = f"rhel-{el_match.group(1)}"
        rpm_name = f"{rpm_prefix}-{base_ver}-{suffix}.x86_64.rpm"

        url = (
            f"https://download2.engineering.redhat.com/brewroot/vol/{rhel_tag}"
            f"/packages/{pkg_dir}/{base_ver}/{suffix}/x86_64/{rpm_name}"
        )

        self.logger.info(f"Built brew RPM URL: {url}")
        return url

    @staticmethod
    def _shell_quote(value) -> str:
        """Quote a value for safe use in shell=True subprocess commands."""
        text = str(value)
        return "'" + text.replace("'", "'\"'\"'") + "'"

    def _construct_command(self, params: Dict[str, str]) -> str:
        """
        Construct the full cephci command string.
        
        Args:
            params: Dictionary of parameters
            
        Returns:
            Full command string
        """
        # Start with the base command: python3 run.py
        cmd_parts = ['python3', 'run.py']
        
        # Add parameters
        for param_name, param_value in params.items():
            # Handle parameters with spaces (like --custom-config pre=<url>)
            if ' ' in param_name:
                parts = param_name.split(' ', 1)
                quoted = self._shell_quote(param_value)
                cmd_parts.append(f"{parts[0]} {parts[1]}={quoted}")
            elif param_value == '':
                cmd_parts.append(param_name)
            elif param_value:
                cmd_parts.append(f"{param_name} {self._shell_quote(param_value)}")
        
        return ' '.join(cmd_parts)
    
    def _get_log_directory(self, jira_id: str, verification_type: str = "pre") -> str:
        """
        Get the log directory path for a JIRA issue.
        
        Args:
            jira_id: JIRA issue ID
            verification_type: 'pre', 'post', or 'N/A'
            
        Returns:
            Log directory path
        """
        vtype = (verification_type or "pre").lower().replace("/", "-")
        log_dir = os.path.join(self.log_base_dir, f"{jira_id}_{vtype}")
        return log_dir
    
    def print_command(self, command_data: Dict[str, Any]) -> None:
        """
        Print the cephci command in a formatted way.
        
        Args:
            command_data: Command data from build_command()
        """
        print("\n" + "="*80)
        print(f"CephCI Command for {command_data['jira_id']}")
        print("="*80)
        print()
        print("📋 Parameters:")
        for param, value in command_data['params'].items():
            print(f"   {param}: {value}")
        print()
        print("🚀 Command:")
        print(f"   {command_data['command']}")
        print()
        print("📁 Log Directory:")
        print(f"   {command_data['log_dir']}")
        print()
        print("🌿 Branch:")
        print(f"   {command_data['branch']}")
        print("="*80)
        print()
    
    def build_commands_batch(
        self,
        jira_issues: List[Dict[str, Any]],
        branch: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Build cephci commands for multiple JIRA issues.
        
        Args:
            jira_issues: List of parsed JIRA issue data
            branch: Optional cephci branch to use
            
        Returns:
            List of command data dictionaries
        """
        self.logger.info(f"Building commands for {len(jira_issues)} issues")
        
        commands = []
        for jira_data in jira_issues:
            try:
                command_data = self.build_command(jira_data, branch)
                commands.append(command_data)
            except Exception as e:
                jira_id = jira_data.get('jira_id', 'UNKNOWN')
                self.logger.error(f"Error building command for {jira_id}: {e}")
                continue
        
        self.logger.info(f"Successfully built {len(commands)} commands")
        return commands

# Made with Bob
