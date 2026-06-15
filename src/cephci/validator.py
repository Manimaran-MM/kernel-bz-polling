"""
CephCI Validator

Validates cephci command parameters and file existence.
"""

import os
import subprocess
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path

from src.utils.logger import get_logger
from src.utils.config import get_config


class CephCIValidator:
    """Validates cephci commands and required files."""
    
    def __init__(self):
        """Initialize the validator."""
        self.logger = get_logger(__name__)
        self.config = get_config()
        
        # Get cephci configuration
        self.cephci_repo = self.config.get('cephci.local_path', './cephci')
        self.cephci_branch = self.config.get('cephci.default_branch', 'master')
        
    def validate_inventory_file(
        self,
        inventory_path: str,
        branch: Optional[str] = None
    ) -> Tuple[bool, str]:
        """
        Validate if inventory file exists in cephci repo.
        
        Args:
            inventory_path: Path to inventory file (e.g., conf/inventory/rhel-9.4-server-x86_64.yaml)
            branch: Optional branch to check (defaults to config)
            
        Returns:
            Tuple of (exists: bool, message: str)
        """
        branch = branch or self.cephci_branch
        
        # Check if cephci repo exists
        if not os.path.exists(self.cephci_repo):
            msg = f"CephCI repo not found at {self.cephci_repo}"
            self.logger.warning(msg)
            return False, msg
        
        # Full path to inventory file
        full_path = os.path.join(self.cephci_repo, inventory_path)
        
        # Check if file exists
        if os.path.exists(full_path):
            self.logger.debug(f"✓ Inventory file exists: {inventory_path}")
            return True, f"File exists: {full_path}"
        else:
            msg = f"File not found: {full_path}"
            self.logger.warning(f"✗ {msg}")
            return False, msg
    
    def validate_command_files(
        self,
        command_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Validate all files referenced in a command.
        
        Args:
            command_data: Command data from CephCICommandBuilder
            
        Returns:
            Dictionary with validation results:
            {
                'jira_id': str,
                'inventory_valid': bool,
                'inventory_message': str,
                'global_conf_valid': bool,
                'global_conf_message': str,
                'suite_valid': bool,
                'suite_message': str,
                'osp_cred_valid': bool,
                'osp_cred_message': str,
                'all_valid': bool
            }
        """
        jira_id = command_data.get('jira_id', 'UNKNOWN')
        params = command_data.get('params', {})
        branch = command_data.get('branch', self.cephci_branch)
        
        self.logger.info(f"Validating files for {jira_id}")
        
        results = {
            'jira_id': jira_id,
            'branch': branch
        }
        
        # Validate inventory file
        inventory = params.get('--inventory')
        if inventory:
            valid, msg = self.validate_inventory_file(inventory, branch)
            results['inventory_valid'] = valid
            results['inventory_message'] = msg
        else:
            results['inventory_valid'] = False
            results['inventory_message'] = "No inventory specified"
        
        # Validate global-conf file
        global_conf = params.get('--global-conf')
        if global_conf:
            valid, msg = self._validate_file(global_conf, branch)
            results['global_conf_valid'] = valid
            results['global_conf_message'] = msg
        else:
            results['global_conf_valid'] = False
            results['global_conf_message'] = "No global-conf specified"
        
        # Validate suite file
        suite = params.get('--suite')
        if suite:
            valid, msg = self._validate_file(suite, branch)
            results['suite_valid'] = valid
            results['suite_message'] = msg
        else:
            results['suite_valid'] = False
            results['suite_message'] = "No suite specified"
        
        # Validate osp-cred file
        osp_cred = params.get('--osp-cred')
        if osp_cred:
            valid, msg = self._validate_file(osp_cred, branch)
            results['osp_cred_valid'] = valid
            results['osp_cred_message'] = msg
        else:
            results['osp_cred_valid'] = False
            results['osp_cred_message'] = "No osp-cred specified"
        
        # Overall validation
        results['all_valid'] = all([
            results.get('inventory_valid', False),
            results.get('global_conf_valid', False),
            results.get('suite_valid', False),
            results.get('osp_cred_valid', False)
        ])
        
        if results['all_valid']:
            self.logger.info(f"✓ All files valid for {jira_id}")
        else:
            self.logger.warning(f"✗ Some files missing for {jira_id}")
        
        return results
    
    def _validate_file(
        self,
        file_path: str,
        branch: Optional[str] = None
    ) -> Tuple[bool, str]:
        """
        Validate if a file exists in cephci repo.
        
        Args:
            file_path: Path to file relative to repo root
            branch: Optional branch to check
            
        Returns:
            Tuple of (exists: bool, message: str)
        """
        branch = branch or self.cephci_branch
        
        # Check if cephci repo exists
        if not os.path.exists(self.cephci_repo):
            msg = f"CephCI repo not found at {self.cephci_repo}"
            return False, msg
        
        # Full path to file
        full_path = os.path.join(self.cephci_repo, file_path)
        
        # Check if file exists
        if os.path.exists(full_path):
            self.logger.debug(f"✓ File exists: {file_path}")
            return True, f"File exists: {full_path}"
        else:
            msg = f"File not found: {full_path}"
            self.logger.debug(f"✗ {msg}")
            return False, msg
    
    def validate_commands_batch(
        self,
        commands: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Validate files for multiple commands.
        
        Args:
            commands: List of command data from CephCICommandBuilder
            
        Returns:
            List of validation results
        """
        self.logger.info(f"Validating files for {len(commands)} commands")
        
        results = []
        for command_data in commands:
            try:
                result = self.validate_command_files(command_data)
                results.append(result)
            except Exception as e:
                jira_id = command_data.get('jira_id', 'UNKNOWN')
                self.logger.error(f"Error validating {jira_id}: {e}")
                results.append({
                    'jira_id': jira_id,
                    'all_valid': False,
                    'error': str(e)
                })
        
        # Summary
        valid_count = sum(1 for r in results if r.get('all_valid', False))
        self.logger.info(f"Validation complete: {valid_count}/{len(results)} commands have all files")
        
        return results

# Made with Bob
