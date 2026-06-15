"""
CephCI Command Executor
Executes CephCI commands in the cloned repository
"""

import subprocess
import os
import json
import yaml
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime

from src.utils.logger import get_logger
from src.utils.config import get_config


class CephCIExecutor:
    """Executes CephCI commands and manages execution state."""
    
    def __init__(self, skip_dashboard: bool = False):
        """Initialize the CephCI executor."""
        self.logger = get_logger(__name__)
        self.config = get_config()
        self.skip_dashboard = skip_dashboard
        
        # Get cephci repo path - try local_path first, then repo_path, then default
        local_path = self.config.get('cephci.local_path')
        repo_path = self.config.get('cephci.repo_path')
        
        if local_path:
            self.cephci_repo_path = Path(local_path)
        elif repo_path:
            self.cephci_repo_path = Path(repo_path)
        else:
            self.cephci_repo_path = Path('cephci')
        
        self.logger.info(f"CephCI repository path: {self.cephci_repo_path}")
        
        # Execution tracking file
        self.execution_log_file = Path('data/command_executions.json')
        self.execution_log = self._load_execution_log()
        
    def _load_execution_log(self) -> Dict:
        """Load execution log from file."""
        if self.execution_log_file.exists():
            try:
                with open(self.execution_log_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                self.logger.warning(f"Failed to load execution log: {e}")
        return {}
    
    def _save_execution_log(self):
        """Save execution log to file."""
        try:
            self.execution_log_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.execution_log_file, 'w') as f:
                json.dump(self.execution_log, f, indent=2)
        except Exception as e:
            self.logger.warning(f"Failed to save execution log: {e}")
    
    def install_dependencies(self) -> bool:
        """Run pip install -r requirements.txt in the cephci repo.

        Returns True on success, False on failure.
        """
        req_file = self.cephci_repo_path / "requirements.txt"
        if not req_file.exists():
            self.logger.warning(f"No requirements.txt found at {req_file}, skipping dependency install")
            return True

        self.logger.info(f"Installing cephci dependencies from {req_file} ...")
        try:
            result = subprocess.run(
                ["pip", "install", "-r", "requirements.txt"],
                cwd=str(self.cephci_repo_path),
                capture_output=True,
                text=True,
                timeout=600,
            )
            if result.returncode == 0:
                self.logger.info("✅ cephci dependencies installed successfully")
                return True
            else:
                self.logger.error(f"❌ pip install failed (rc={result.returncode})")
                self.logger.error(result.stderr[:1000])
                return False
        except subprocess.TimeoutExpired:
            self.logger.error("❌ pip install timed out after 10 minutes")
            return False
        except Exception as e:
            self.logger.error(f"❌ pip install error: {e}")
            return False

    def populate_osp_credentials(self) -> bool:
        """Populate the osp-cred YAML file with OpenStack credentials from .env.

        Reads OPENSTACK_* environment variables (loaded via dotenv) and writes
        them into the cephci osp credentials file so that run.py can authenticate.

        Returns True on success, False on failure.
        """
        osp_cred_rel = self.config.get(
            "cephci.kernel_verification.osp_cred", "osp/osp-cred-ci-2.yaml"
        )
        osp_cred_path = self.cephci_repo_path / osp_cred_rel

        username = os.environ.get("OPENSTACK_USERNAME")
        password = os.environ.get("OPENSTACK_PASSWORD")
        auth_url = os.environ.get("OPENSTACK_AUTH_URL")
        project_name = os.environ.get("OPENSTACK_PROJECT_NAME")
        region = os.environ.get("OPENSTACK_REGION", "regionOne")
        domain = os.environ.get("OPENSTACK_DOMAIN", "redhat.com")
        tenant_domain_id = os.environ.get("OPENSTACK_TENANT_DOMAIN_ID")

        if not all([username, password, auth_url, project_name]):
            self.logger.error(
                "❌ Missing required OPENSTACK_* env vars "
                "(OPENSTACK_USERNAME, OPENSTACK_PASSWORD, OPENSTACK_AUTH_URL, OPENSTACK_PROJECT_NAME)"
            )
            return False

        # Build the credentials structure preserving the existing file layout
        try:
            cred_data = {}
            if osp_cred_path.exists():
                with open(osp_cred_path) as f:
                    cred_data = yaml.safe_load(f) or {}

            if "globals" not in cred_data:
                cred_data["globals"] = {}

            cred_data["globals"]["openstack-credentials"] = {
                "username": username,
                "password": password,
                "auth-url": auth_url,
                "auth-version": "3.x_appcred",
                "tenant-name": project_name,
                "service-region": region,
                "domain": domain,
                "tenant-domain-id": tenant_domain_id,
            }

            osp_cred_path.parent.mkdir(parents=True, exist_ok=True)
            with open(osp_cred_path, "w") as f:
                yaml.dump(cred_data, f, default_flow_style=False, sort_keys=False)

            self.logger.info(f"✅ OSP credentials written to {osp_cred_path}")
            return True

        except Exception as e:
            self.logger.error(f"❌ Failed to write OSP credentials: {e}")
            return False

    def verify_and_fix_inventory_images(self, commands: List[Dict]) -> None:
        """Ensure each inventory file's image-name matches the Phase 3.6 analysis.

        Reads data/inventory_image_info.json for the expected image names,
        compares with the actual inventory YAML files in the cephci repo,
        and updates any that differ.

        Args:
            commands: List of command dicts, each having an 'inventory' key
                      (relative path like 'conf/inventory/rhel-9.2-server-x86_64.yaml')
                      or an 'command' string from which the inventory is extracted.
        """
        image_info_path = Path("data/inventory_image_info.json")
        if not image_info_path.exists():
            self.logger.warning(
                f"⚠️  {image_info_path} not found, skipping inventory image verification"
            )
            return

        try:
            with open(image_info_path) as f:
                image_info_map = json.load(f)
        except Exception as e:
            self.logger.warning(f"⚠️  Failed to load {image_info_path}: {e}")
            return

        # Collect unique inventory paths from commands
        inventory_paths = set()
        for cmd in commands:
            inv = cmd.get("inventory")
            if not inv and "command" in cmd:
                cmd_str = cmd["command"]
                if "--inventory " in cmd_str:
                    inv = cmd_str.split("--inventory ")[1].split(" ")[0]
            if inv:
                inventory_paths.add(inv)

        updated = 0
        checked = 0
        for inv_path in sorted(inventory_paths):
            expected_info = image_info_map.get(inv_path)
            if not expected_info:
                continue

            expected_image = expected_info.get("image_name")
            if not expected_image:
                continue

            full_path = self.cephci_repo_path / inv_path
            if not full_path.exists():
                self.logger.warning(f"  ⚠️  Inventory file missing: {full_path}")
                continue

            try:
                with open(full_path) as f:
                    data = yaml.safe_load(f)

                actual_image = (
                    data.get("instance", {}).get("create", {}).get("image-name")
                )
                checked += 1

                if actual_image == expected_image:
                    self.logger.debug(f"  ✅ {inv_path}: image-name OK ({actual_image})")
                    continue

                self.logger.info(
                    f"  🔄 {inv_path}: updating image-name "
                    f"'{actual_image}' -> '{expected_image}'"
                )
                data["instance"]["create"]["image-name"] = expected_image

                with open(full_path, "w") as f:
                    yaml.dump(data, f, default_flow_style=False, sort_keys=False)
                updated += 1

            except Exception as e:
                self.logger.warning(f"  ⚠️  Error checking {inv_path}: {e}")

        self.logger.info(
            f"✅ Inventory image verification: {checked} checked, {updated} updated"
        )

    def _rewrite_log_dir(self, command: str, jira_id: str, verification_type: str = "pre") -> Tuple[str, str]:
        """Replace any remote --log-dir path with a local one.

        Returns (updated_command, local_log_dir_path).
        """
        local_base = self.config.get("cephci.log_dir", "./data/logs/kernel-logs")
        vtype = (verification_type or "pre").lower().replace("/", "-")
        log_key = f"{jira_id}_{vtype}"
        local_log_dir = os.path.abspath(os.path.join(local_base, log_key))

        if "--log-dir " in command:
            old_val = command.split("--log-dir ")[1].split(" ")[0]
            command = command.replace(
                f"--log-dir {old_val}", f"--log-dir {local_log_dir}"
            )
        return command, local_log_dir

    def transfer_logs_to_remote(self, local_log_dir: str, jira_id: str, verification_type: str = "pre") -> bool:
        """Copy logs to the remote log storage location.

        Strategy:
          1. If LOG_STORAGE_PATH is directly accessible (local/NFS mount),
             do a simple cp.
          2. Otherwise fall back to scp via LOG_STORAGE_HOST.

        Returns True on success, False on failure.
        """
        remote_base = os.environ.get(
            "LOG_STORAGE_PATH",
            self.config.get("storage.log_path", ""),
        )
        if not remote_base:
            self.logger.warning(
                "⚠️  LOG_STORAGE_PATH not set, skipping log transfer"
            )
            return False

        local_path = Path(local_log_dir)
        if not local_path.exists():
            self.logger.warning(f"⚠️  Local log directory does not exist: {local_log_dir}")
            return False

        vtype = (verification_type or "pre").lower().replace("/", "-")
        log_key = f"{jira_id}_{vtype}"
        dest_dir = os.path.join(remote_base, log_key)

        # --- Try local copy first (works when path is a local/NFS mount) ---
        if os.path.isdir(remote_base) or os.path.isdir(os.path.dirname(remote_base)):
            try:
                os.makedirs(dest_dir, exist_ok=True)
                import shutil
                for item in local_path.iterdir():
                    src = str(item)
                    dst = os.path.join(dest_dir, item.name)
                    if item.is_dir():
                        shutil.copytree(src, dst, dirs_exist_ok=True)
                    else:
                        shutil.copy2(src, dst)
                self.logger.info(f"✅ Logs copied locally to {dest_dir}")
                return True
            except Exception as e:
                self.logger.warning(f"⚠️  Local copy failed ({e}), trying scp...")

        # --- Fall back to scp ---
        host = os.environ.get(
            "LOG_STORAGE_HOST",
            self.config.get("storage.log_host", ""),
        )
        if not host:
            self.logger.warning("⚠️  LOG_STORAGE_HOST not set and local copy failed, skipping")
            return False

        user = os.environ.get(
            "LOG_STORAGE_USER",
            self.config.get("storage.log_user", ""),
        )
        ssh_key = os.environ.get(
            "SSH_KEY_PATH",
            self.config.get("storage.ssh_key_path", "~/.ssh/id_rsa"),
        )
        ssh_key = os.path.expanduser(ssh_key)

        remote_dest = f"{remote_base}/{log_key}/"
        remote_target = f"{user}@{host}:{remote_dest}" if user else f"{host}:{remote_dest}"

        ssh_prefix = ["ssh"]
        if os.path.isfile(ssh_key):
            ssh_prefix += ["-i", ssh_key]
        ssh_prefix += ["-o", "StrictHostKeyChecking=no"]

        mkdir_target = f"{user}@{host}" if user else host
        mkdir_cmd = ssh_prefix + [mkdir_target, f"mkdir -p {remote_dest}"]

        self.logger.info(f"📤 Transferring logs to {remote_target}")

        try:
            subprocess.run(mkdir_cmd, capture_output=True, text=True, timeout=30)

            scp_cmd = ["scp", "-r", "-o", "StrictHostKeyChecking=no"]
            if os.path.isfile(ssh_key):
                scp_cmd += ["-i", ssh_key]
            scp_cmd += [f"{local_log_dir}/.", remote_target]

            result = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=600)
            if result.returncode == 0:
                self.logger.info(f"✅ Logs transferred to {remote_target}")
                return True
            else:
                self.logger.warning(
                    f"⚠️  scp failed (rc={result.returncode}): {result.stderr[:500]}"
                )
                return False
        except subprocess.TimeoutExpired:
            self.logger.warning("⚠️  Log transfer timed out")
            return False
        except Exception as e:
            self.logger.warning(f"⚠️  Log transfer error: {e}")
            return False

    def _extract_osp_cred(self, command: str) -> str:
        """Extract --osp-cred value from a command string, falling back to config default."""
        if "--osp-cred " in command:
            return command.split("--osp-cred ")[1].split(" ")[0]
        return self.config.get(
            "cephci.kernel_verification.osp_cred", "osp/osp-cred-ci-2.yaml"
        )

    def cleanup_instance(self, instance_name: str, osp_cred: str) -> bool:
        """Run the cephci cleanup command for an instance.

        Always called after execution regardless of pass/fail.

        Returns True on success, False on failure.
        """
        cleanup_cmd = (
            f"python3 run.py --cloud openstack --log-level DEBUG "
            f"--osp-cred {osp_cred} --cleanup {instance_name}"
        )
        self.logger.info(f"🧹 Running cleanup for {instance_name}")
        self.logger.info(f"📝 Cleanup command: {cleanup_cmd}")

        try:
            result = subprocess.run(
                cleanup_cmd,
                shell=True,
                cwd=str(self.cephci_repo_path),
                capture_output=True,
                text=True,
                timeout=600,
            )
            if result.returncode == 0:
                self.logger.info(f"✅ Cleanup succeeded for {instance_name}")
                return True
            else:
                self.logger.warning(
                    f"⚠️  Cleanup returned rc={result.returncode} for {instance_name}"
                )
                self.logger.warning(f"stderr: {result.stderr[:500]}")
                return False
        except subprocess.TimeoutExpired:
            self.logger.warning(f"⚠️  Cleanup timed out for {instance_name}")
            return False
        except Exception as e:
            self.logger.warning(f"⚠️  Cleanup error for {instance_name}: {e}")
            return False

    def execute_command(
        self,
        jira_id: str,
        command: str,
        rhosd_kernel: Optional[str] = None,
        dry_run: bool = False,
        verification_type: Optional[str] = None,
        jira_kernel: Optional[str] = None,
        fix_version: Optional[str] = None,
    ) -> Tuple[bool, str, str]:
        """
        Execute a CephCI command, then run cleanup regardless of outcome.
        
        Args:
            jira_id: JIRA issue ID
            command: Full CephCI command to execute
            rhosd_kernel: RHOS-D kernel version (optional, for logging)
            dry_run: If True, only log the command without executing
            verification_type: 'pre', 'post', or 'N/A'
            jira_kernel: Kernel version from the JIRA repo
            fix_version: JIRA fix version string (e.g., 'rhel-8.8.0.z')
            
        Returns:
            Tuple of (success, stdout, stderr)
        """
        self.logger.info(f"\n{'='*80}")
        self.logger.info(f"🚀 Executing command for {jira_id}")
        self.logger.info(f"{'='*80}")
        
        if rhosd_kernel:
            self.logger.info(f"📦 RHOS-D Kernel: {rhosd_kernel}")
        
        # Check if cephci repo exists
        if not self.cephci_repo_path.exists():
            error_msg = f"CephCI repository not found at: {self.cephci_repo_path}"
            self.logger.error(f"❌ {error_msg}")
            return False, "", error_msg
        
        # Rewrite --log-dir to a local path and ensure the directory exists
        vtype = (verification_type or "pre").lower().replace("/", "-")
        log_key = f"{jira_id}_{vtype}"
        command, local_log_dir = self._rewrite_log_dir(command, jira_id, verification_type)
        Path(local_log_dir).mkdir(parents=True, exist_ok=True)
        self.logger.info(f"📁 Local log directory: {local_log_dir}")

        # Log the command
        self.logger.info(f"📝 Command: {command}")
        
        if dry_run:
            self.logger.info("🔍 DRY RUN - Command not executed")
            return True, "DRY RUN", ""

        osp_cred = self._extract_osp_cred(command)

        def _extract_param(cmd, flag):
            if f"{flag} " in cmd:
                return cmd.split(f"{flag} ")[1].split(" ")[0].strip("'\"")
            return None

        instance_name = _extract_param(command, "--instances-name") or jira_id
        inventory = _extract_param(command, "--inventory")
        platform = _extract_param(command, "--platform")
        rhbuild = _extract_param(command, "--rhbuild")

        # Build the remote log path for the dashboard
        remote_base = os.environ.get(
            "LOG_STORAGE_PATH",
            self.config.get("storage.log_path", ""),
        )
        remote_log_dir = f"{remote_base}/{log_key}" if remote_base else local_log_dir

        # Record execution start
        execution_record = {
            'jira_id': jira_id,
            'command': command,
            'rhosd_kernel': rhosd_kernel,
            'jira_kernel': jira_kernel,
            'fix_version': fix_version,
            'inventory': inventory,
            'platform': platform,
            'rhbuild': rhbuild,
            'verification_type': verification_type or 'N/A',
            'log_dir': remote_log_dir,
            'start_time': datetime.now().isoformat(),
            'status': 'running'
        }
        
        # Log file for the full terminal output of this command
        log_dir = Path("data")
        log_dir.mkdir(parents=True, exist_ok=True)
        terminal_log = log_dir / f"terminal_output_{log_key}.log"

        try:
            self.logger.info(f"📂 Working directory: {self.cephci_repo_path}")
            self.logger.info(f"📄 Terminal output: {terminal_log}")
            self.logger.info("⏳ Executing... (this may take several minutes)")

            with open(terminal_log, "a") as log_fh:
                log_fh.write(f"\n{'='*80}\n")
                log_fh.write(f"JIRA: {jira_id}\n")
                log_fh.write(f"Start: {execution_record['start_time']}\n")
                log_fh.write(f"Command: {command}\n")
                log_fh.write(f"{'='*80}\n\n")
                log_fh.flush()

                cmd_timeout = int(self.config.get('cephci.timeout', 21600))
                result = subprocess.run(
                    command,
                    shell=True,
                    cwd=str(self.cephci_repo_path),
                    stdout=log_fh,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=cmd_timeout,
                )

            end_time = datetime.now()
            execution_record['end_time'] = end_time.isoformat()
            execution_record['return_code'] = result.returncode
            execution_record['status'] = 'success' if result.returncode == 0 else 'failed'
            if result.returncode != 0:
                execution_record['reason'] = f"Command exited with return code {result.returncode}"
            execution_record['terminal_log'] = str(terminal_log)
            start_dt = datetime.fromisoformat(execution_record['start_time'])
            execution_record['duration_seconds'] = round((end_time - start_dt).total_seconds())

            self.execution_log[jira_id] = execution_record
            self._save_execution_log()

            if result.returncode == 0:
                self.logger.info(f"✅ Command executed successfully for {jira_id}")
                self.logger.info(f"📊 Return code: {result.returncode}")
                self.logger.info(f"📄 Full output: {terminal_log}")
                return True, "", ""
            else:
                self.logger.error(f"❌ Command failed for {jira_id}")
                self.logger.error(f"📊 Return code: {result.returncode}")
                self.logger.error(f"📄 Check full output: {terminal_log}")
                return False, "", ""

        except subprocess.TimeoutExpired:
            timeout_hrs = int(self.config.get('cephci.timeout', 21600)) / 3600
            error_msg = f"Command timed out after {timeout_hrs:.0f} hours"
            self.logger.error(f"⏰ {error_msg}")
            end_time = datetime.now()
            execution_record['end_time'] = end_time.isoformat()
            execution_record['status'] = 'timeout'
            execution_record['reason'] = error_msg
            execution_record['error'] = error_msg
            execution_record['terminal_log'] = str(terminal_log)
            start_dt = datetime.fromisoformat(execution_record['start_time'])
            execution_record['duration_seconds'] = round((end_time - start_dt).total_seconds())
            self.execution_log[jira_id] = execution_record
            self._save_execution_log()
            return False, "", error_msg

        except Exception as e:
            error_msg = f"Execution error: {str(e)}"
            self.logger.error(f"❌ {error_msg}")
            end_time = datetime.now()
            execution_record['end_time'] = end_time.isoformat()
            execution_record['status'] = 'error'
            execution_record['reason'] = error_msg
            execution_record['error'] = error_msg
            execution_record['terminal_log'] = str(terminal_log)
            start_dt = datetime.fromisoformat(execution_record['start_time'])
            execution_record['duration_seconds'] = round((end_time - start_dt).total_seconds())
            self.execution_log[jira_id] = execution_record
            self._save_execution_log()
            return False, "", error_msg

        finally:
            self.cleanup_instance(instance_name, osp_cred)
            self.transfer_logs_to_remote(local_log_dir, jira_id, verification_type)
            if not self.skip_dashboard:
                try:
                    from src.dashboard.publisher import publish_dashboard
                    publish_dashboard()
                except Exception as e:
                    self.logger.warning(f"Dashboard publish skipped: {e}")
    
    def get_execution_status(self, jira_id: str) -> Optional[Dict]:
        """Get execution status for a JIRA ID."""
        return self.execution_log.get(jira_id)
    
    def list_executions(self) -> Dict:
        """List all executions."""
        return self.execution_log
