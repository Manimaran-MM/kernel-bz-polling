"""
CephCI Repository Manager

Manages the cephci git repository as a submodule.
"""

import os
import subprocess
from pathlib import Path
from typing import Optional, Tuple

from src.utils.logger import get_logger
from src.utils.config import get_config


class CephCIRepoManager:
    """Manages cephci repository cloning and updates."""
    
    def __init__(self):
        """Initialize the repo manager."""
        self.logger = get_logger(__name__)
        self.config = get_config()
        
        # Get cephci configuration
        self.repo_url = self.config.get('cephci.repo_url', 'https://github.com/red-hat-storage/cephci.git')
        self.local_path = self.config.get('cephci.local_path', './cephci')
        # Use CEPHCI_BRANCH from .env, fallback to default_branch
        self.default_branch = self.config.get('cephci.branch') or self.config.get('cephci.default_branch', 'master')
        
    def ensure_repo_exists(self, branch: Optional[str] = None, force_reclone: bool = False) -> Tuple[bool, str]:
        """
        Ensure cephci repo exists locally, clone if needed.
        
        Args:
            branch: Optional branch to checkout (defaults to config)
            force_reclone: If True, remove existing repo and clone fresh
            
        Returns:
            Tuple of (success: bool, message: str)
        """
        branch = branch or self.default_branch
        
        # If force_reclone, remove existing directory
        if force_reclone and os.path.exists(self.local_path):
            self.logger.info(f"Removing existing repo at {self.local_path} for fresh clone")
            try:
                import shutil
                shutil.rmtree(self.local_path)
            except Exception as e:
                msg = f"Failed to remove existing repo: {e}"
                self.logger.error(msg)
                return False, msg
        
        # Check if repo already exists
        if os.path.exists(self.local_path):
            if os.path.exists(os.path.join(self.local_path, '.git')):
                self.logger.info(f"CephCI repo already exists at {self.local_path}")
                # Update to specified branch
                effective_branch = branch or self.default_branch
                success, msg = self.checkout_branch(effective_branch)
                
                # If checkout/pull failed due to divergent branches, force reclone
                if not success and "divergent" in msg.lower():
                    self.logger.warning("Divergent branches detected, forcing fresh clone")
                    return self.ensure_repo_exists(branch, force_reclone=True)
                
                return success, msg
            else:
                msg = f"Directory exists but is not a git repo: {self.local_path}"
                self.logger.error(msg)
                return False, msg
        
        # Clone the repo
        self.logger.info(f"Cloning cephci repo from {self.repo_url}")
        return self.clone_repo(branch)
    
    def clone_repo(self, branch: Optional[str] = None) -> Tuple[bool, str]:
        """
        Clone the cephci repository.
        
        Args:
            branch: Optional branch to checkout
            
        Returns:
            Tuple of (success: bool, message: str)
        """
        branch = branch or self.default_branch
        
        try:
            # Create parent directory if needed
            parent_dir = os.path.dirname(self.local_path)
            if parent_dir:
                Path(parent_dir).mkdir(parents=True, exist_ok=True)
            
            # Clone with specific branch
            self.logger.info(f"Cloning branch '{branch}' to {self.local_path}")
            cmd = ['git', 'clone', '--branch', branch, '--depth', '1', self.repo_url, self.local_path]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300  # 5 minutes timeout
            )
            
            if result.returncode == 0:
                msg = f"Successfully cloned cephci repo (branch: {branch})"
                self.logger.info(msg)
                return True, msg
            else:
                msg = f"Failed to clone repo: {result.stderr}"
                self.logger.error(msg)
                return False, msg
                
        except subprocess.TimeoutExpired:
            msg = "Clone operation timed out after 5 minutes"
            self.logger.error(msg)
            return False, msg
        except Exception as e:
            msg = f"Error cloning repo: {e}"
            self.logger.error(msg)
            return False, msg
    
    def checkout_branch(self, branch: str) -> Tuple[bool, str]:
        """
        Checkout a specific branch.
        
        Args:
            branch: Branch name to checkout
            
        Returns:
            Tuple of (success: bool, message: str)
        """
        if not os.path.exists(self.local_path):
            return False, f"Repo not found at {self.local_path}"
        
        try:
            self.logger.info(f"Checking out branch '{branch}'")
            
            # Fetch latest
            fetch_cmd = ['git', '-C', self.local_path, 'fetch', 'origin', branch]
            subprocess.run(fetch_cmd, capture_output=True, text=True, timeout=60)
            
            # Checkout branch
            checkout_cmd = ['git', '-C', self.local_path, 'checkout', branch]
            result = subprocess.run(
                checkout_cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                # Pull latest changes with rebase to handle divergent branches
                pull_cmd = ['git', '-C', self.local_path, 'pull', '--rebase', 'origin', branch]
                pull_result = subprocess.run(
                    pull_cmd,
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                
                if pull_result.returncode == 0:
                    msg = f"Successfully checked out and updated branch '{branch}'"
                    self.logger.info(msg)
                    return True, msg
                else:
                    # Check if it's a divergent branch issue
                    error_msg = pull_result.stderr
                    if "divergent" in error_msg.lower() or "rebase" in error_msg.lower():
                        msg = f"Divergent branches detected: {error_msg}"
                        self.logger.error(msg)
                        return False, msg
                    else:
                        msg = f"Checked out branch but failed to pull: {error_msg}"
                        self.logger.warning(msg)
                        return True, msg  # Still return success as checkout worked
            else:
                msg = f"Failed to checkout branch: {result.stderr}"
                self.logger.error(msg)
                return False, msg
                
        except subprocess.TimeoutExpired:
            msg = "Checkout operation timed out"
            self.logger.error(msg)
            return False, msg
        except Exception as e:
            msg = f"Error checking out branch: {e}"
            self.logger.error(msg)
            return False, msg
    
    def get_current_branch(self) -> Optional[str]:
        """
        Get the current branch name.
        
        Returns:
            Branch name or None if error
        """
        if not os.path.exists(self.local_path):
            return None
        
        try:
            cmd = ['git', '-C', self.local_path, 'rev-parse', '--abbrev-ref', 'HEAD']
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                branch = result.stdout.strip()
                return branch
            else:
                return None
                
        except Exception as e:
            self.logger.error(f"Error getting current branch: {e}")
            return None
    
    def update_repo(self, branch: Optional[str] = None) -> Tuple[bool, str]:
        """
        Update the repository to latest version.
        
        Args:
            branch: Optional branch to update (defaults to current)
            
        Returns:
            Tuple of (success: bool, message: str)
        """
        if not os.path.exists(self.local_path):
            return self.ensure_repo_exists(branch)
        
        effective_branch = branch or self.get_current_branch() or self.default_branch
        return self.checkout_branch(effective_branch)

# Made with Bob
