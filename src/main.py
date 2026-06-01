"""
Main entry point for the Kernel BZ Polling System.

Usage:
    python -m src.main                          # Full pipeline (Phases 2-4)
    python -m src.main --execute-only           # Re-run Phase 4 from saved data
    python -m src.main --execute-only --retry-failed
    python -m src.main --execute-only --jira-ids RHEL-142095 RHEL-143896
    python -m src.main --execute-only --dry-run
"""

import sys
import os
import json
import argparse
import yaml
from pathlib import Path
from datetime import datetime

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.config import get_config
from src.utils.logger import setup_logging, get_logger


EXECUTIONS_FILE = "data/command_executions.json"
RHOSD_KERNELS_FILE = "data/rhosd_kernel_versions.json"
EXCLUSION_LIST_FILE = "config/exclusion_list.yaml"


def load_exclusion_list(logger):
    """Load the JIRA exclusion list from config/exclusion_list.yaml.

    Returns a dict mapping JIRA ID -> reason string.
    """
    path = Path(EXCLUSION_LIST_FILE)
    if not path.exists():
        logger.debug(f"No exclusion list found at {EXCLUSION_LIST_FILE}")
        return {}

    try:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning(f"Failed to read exclusion list: {e}")
        return {}

    excluded = {}
    for entry in data.get("excluded_jiras", []) or []:
        jira_id = entry.get("id")
        if jira_id:
            excluded[jira_id] = entry.get("reason", "No reason provided")

    if excluded:
        logger.info(f"Loaded {len(excluded)} JIRAs from exclusion list")
    return excluded


def record_excluded_jiras(exclusion_map, logger):
    """Update command_executions.json for excluded JIRAs.

    If a prior execution record exists, all existing fields (log paths, platform,
    duration, etc.) are preserved — only ``status`` and ``reason`` are overwritten.
    If no prior record exists, a minimal entry is created.
    """
    if not exclusion_map:
        return

    exec_path = Path(EXECUTIONS_FILE)
    exec_data = {}
    if exec_path.exists():
        try:
            with open(exec_path) as f:
                exec_data = json.load(f)
        except Exception as e:
            logger.warning(f"Failed to read {EXECUTIONS_FILE}: {e}")

    for jira_id, reason in exclusion_map.items():
        existing = exec_data.get(jira_id, {})
        existing["jira_id"] = jira_id
        existing["reason"] = reason
        if "status" not in existing:
            existing["status"] = "excluded"
        if "start_time" not in existing:
            existing["start_time"] = datetime.now().isoformat()
        exec_data[jira_id] = existing

    try:
        exec_path.parent.mkdir(parents=True, exist_ok=True)
        with open(exec_path, "w") as f:
            json.dump(exec_data, f, indent=2)
        logger.info(f"Updated {EXECUTIONS_FILE} with {len(exclusion_map)} excluded JIRAs")
    except Exception as e:
        logger.warning(f"Failed to write excluded entries: {e}")


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Kernel BZ Polling System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m src.main                                        Full pipeline
  python -m src.main --verification-type pre                Only pre-verification JIRAs
  python -m src.main --verification-type post               Only post-verification JIRAs
  python -m src.main --execute-only                         Re-execute all with RHOS-D kernels
  python -m src.main --execute-only --retry-failed          Re-execute only previously failed
  python -m src.main --execute-only --jira-ids RHEL-142095  Execute specific JIRAs
  python -m src.main --execute-only --dry-run               Preview without executing
        """,
    )
    parser.add_argument(
        "--execute-only",
        action="store_true",
        help="Skip Phases 2-3.7; load commands from data/command_executions.json and run Phase 4 directly",
    )
    parser.add_argument(
        "--jira-ids",
        nargs="+",
        metavar="ID",
        help="Only execute commands for these JIRA IDs (use with --execute-only)",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Only re-execute commands that previously failed (use with --execute-only)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview commands without executing them",
    )
    parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help="Auto-confirm execution prompts (required for non-interactive/Docker runs)",
    )
    parser.add_argument(
        "--publish-dashboard",
        action="store_true",
        help="Publish dashboard from existing data and exit (no tests run)",
    )
    parser.add_argument(
        "--skip-dashboard",
        action="store_true",
        help="Disable automatic dashboard publishing after each test",
    )
    parser.add_argument(
        "--verification-type",
        choices=["pre", "post"],
        metavar="TYPE",
        help="Only run JIRAs matching this verification type (pre or post)",
    )
    return parser.parse_args()


def load_saved_executions(logger):
    """Load previously saved command executions from disk.

    Returns:
        List of executable command dicts [{jira_id, command, rhosd_kernel, inventory, status}, ...]
        or None if the file is missing/invalid.
    """
    path = Path(EXECUTIONS_FILE)
    if not path.exists():
        logger.error(f"Executions file not found: {EXECUTIONS_FILE}")
        logger.error("Run the full pipeline first to generate this file.")
        return None

    try:
        with open(path) as f:
            data = json.load(f)
    except Exception as e:
        logger.error(f"Failed to read {EXECUTIONS_FILE}: {e}")
        return None

    commands = []
    for jira_id, record in data.items():
        cmd_str = record.get("command")
        if not cmd_str:
            logger.warning(f"Skipping {jira_id}: no command found in execution record")
            continue

        # Extract inventory path from the command string
        inventory = None
        if "--inventory " in cmd_str:
            inv_part = cmd_str.split("--inventory ")[1]
            inventory = inv_part.split(" ")[0]

        commands.append({
            "jira_id": record.get("jira_id", jira_id),
            "command": cmd_str,
            "rhosd_kernel": record.get("rhosd_kernel"),
            "inventory": inventory,
            "prev_status": record.get("status"),
            "verification_type": record.get("verification_type", "N/A"),
        })

    logger.info(f"Loaded {len(commands)} commands from {EXECUTIONS_FILE}")
    return commands


def run_execute_only(args, logger):
    """Run Phase 4 directly from persisted data, skipping Phases 2-3.7."""
    from src.cephci.command_executor import CephCIExecutor

    logger.info("=" * 60)
    logger.info("EXECUTE-ONLY MODE")
    logger.info("=" * 60)

    # Load saved commands
    all_commands = load_saved_executions(logger)
    if not all_commands:
        logger.error("No commands available. Run the full pipeline first.")
        return

    # Apply exclusion list
    exclusion_map = load_exclusion_list(logger)
    if exclusion_map:
        before = len(all_commands)
        excluded = [(c["jira_id"], exclusion_map[c["jira_id"]]) for c in all_commands if c["jira_id"] in exclusion_map]
        all_commands = [c for c in all_commands if c["jira_id"] not in exclusion_map]
        if excluded:
            logger.info(f"Exclusion list: filtering out {len(excluded)} JIRAs:")
            for jid, reason in excluded:
                logger.info(f"  - {jid}: {reason}")
            logger.info(f"Remaining commands: {before} -> {len(all_commands)}")
            record_excluded_jiras(
                {jid: reason for jid, reason in excluded}, logger
            )

    # Apply verification type filter
    if args.verification_type:
        target = args.verification_type
        before = len(all_commands)
        all_commands = [c for c in all_commands if c.get("verification_type") == target]
        logger.info(f"--verification-type {target}: {before} -> {len(all_commands)} commands")

    # Apply filters
    executable_commands = all_commands

    if args.jira_ids:
        requested = set(args.jira_ids)
        executable_commands = [c for c in executable_commands if c["jira_id"] in requested]
        missing = requested - {c["jira_id"] for c in executable_commands}
        if missing:
            logger.warning(f"JIRA IDs not found in executions file: {', '.join(sorted(missing))}")
        logger.info(f"Filtered to {len(executable_commands)} commands matching --jira-ids")

    if args.retry_failed:
        executable_commands = [c for c in executable_commands if c.get("prev_status") != "success"]
        logger.info(f"Filtered to {len(executable_commands)} previously non-successful commands")

    if not executable_commands:
        logger.warning("No commands match the given filters.")
        return

    # Display commands to execute
    logger.info(f"Commands to execute: {len(executable_commands)}")
    logger.info("")
    for i, cmd in enumerate(executable_commands, 1):
        kernel_info = cmd.get("rhosd_kernel") or "N/A"
        prev = cmd.get("prev_status", "unknown")
        logger.info(f"  {i}. {cmd['jira_id']}  |  RHOS-D: {kernel_info}  |  prev: {prev}")

    if args.dry_run:
        logger.info("")
        logger.info("DRY RUN - showing commands without executing:")
        logger.info("")
        for cmd in executable_commands:
            logger.info(f"  [{cmd['jira_id']}] {cmd['command']}")
        logger.info("")
        logger.info("DRY RUN complete. No commands were executed.")
        return

    # Confirm execution
    logger.info("")
    logger.info("=" * 60)
    logger.info("Commands will be executed ONE BY ONE in the cephci repository")
    logger.info("Each command may take 30-60 minutes to complete")
    logger.info("")

    if args.yes:
        logger.info("Auto-confirmed via --yes flag")
        response = "yes"
    else:
        response = input("Do you want to execute these commands? (yes/no): ").strip().lower()
    if response != "yes":
        logger.info("Execution cancelled by user.")
        return

    # Execute
    executor = CephCIExecutor(skip_dashboard=args.skip_dashboard)

    if not executor.install_dependencies():
        logger.error("Failed to install cephci dependencies. Aborting execution.")
        return

    if not executor.populate_osp_credentials():
        logger.error("Failed to populate OSP credentials. Aborting execution.")
        return

    # Load per-JIRA baseline data for inventory updates
    rhosd_baselines = {}
    rhosd_file = Path("data/rhosd_kernel_versions.json")
    if rhosd_file.exists():
        try:
            with open(rhosd_file) as f:
                rhosd_baselines = json.load(f)
            logger.info(f"Loaded per-JIRA baselines from {rhosd_file}")
        except Exception as e:
            logger.warning(f"Could not load baselines: {e}")

    logger.info("Starting command execution...")

    success_count = 0
    failed_count = 0

    for i, exec_cmd in enumerate(executable_commands, 1):
        logger.info("")
        logger.info(f"{'=' * 80}")
        logger.info(f"Executing {i}/{len(executable_commands)}: {exec_cmd['jira_id']}")
        logger.info(f"{'=' * 80}")

        # Update baseline image for THIS JIRA
        jira_id = exec_cmd["jira_id"]
        baseline_info = rhosd_baselines.get(jira_id, {})
        baseline_image = baseline_info.get("baseline_image")
        is_rhel8 = baseline_info.get("is_rhel8", False)

        if is_rhel8 and baseline_image:
            # RHEL-8: update openstack image-name in global conf (node4 + node5)
            gc_path = baseline_info.get("global_conf")
            if gc_path:
                gc_full_path = Path("cephci") / gc_path
                if gc_full_path.exists():
                    try:
                        with open(gc_full_path) as f:
                            gc_data = yaml.safe_load(f)
                        updated = 0
                        for glob_entry in gc_data.get("globals", []):
                            cluster = glob_entry.get("ceph-cluster", {})
                            for node_key, node_val in cluster.items():
                                if not isinstance(node_val, dict):
                                    continue
                                img_block = node_val.get("image-name")
                                if isinstance(img_block, dict) and "openstack" in img_block:
                                    old_img = img_block["openstack"]
                                    if old_img != baseline_image:
                                        img_block["openstack"] = baseline_image
                                        updated += 1
                        if updated:
                            with open(gc_full_path, "w") as f:
                                yaml.dump(gc_data, f, default_flow_style=False, sort_keys=False)
                            logger.info(f"  🔄 Global conf updated: {updated} openstack image(s) -> '{baseline_image}'")
                        else:
                            logger.info(f"  ✅ Global conf already using correct baseline")
                    except Exception as e:
                        logger.warning(f"  ⚠️  Failed to update global conf {gc_path}: {e}")
        elif baseline_image:
            inv_path = exec_cmd.get("inventory")
            if inv_path:
                inv_full_path = Path("cephci") / inv_path
                if inv_full_path.exists():
                    try:
                        with open(inv_full_path) as f:
                            inv_data = yaml.safe_load(f)
                        current_image = inv_data.get("instance", {}).get("create", {}).get("image-name")
                        if current_image != baseline_image:
                            inv_data["instance"]["create"]["image-name"] = baseline_image
                            with open(inv_full_path, "w") as f:
                                yaml.dump(inv_data, f, default_flow_style=False, sort_keys=False)
                            logger.info(f"  🔄 Inventory updated: '{current_image}' -> '{baseline_image}'")
                        else:
                            logger.info(f"  ✅ Inventory already using correct baseline: '{baseline_image}'")
                    except Exception as e:
                        logger.warning(f"  ⚠️  Failed to update inventory {inv_path}: {e}")

        success, stdout, stderr = executor.execute_command(
            jira_id=exec_cmd["jira_id"],
            command=exec_cmd["command"],
            rhosd_kernel=exec_cmd.get("rhosd_kernel"),
            dry_run=False,
            verification_type=exec_cmd.get("verification_type"),
            jira_kernel=exec_cmd.get("jira_kernel"),
            fix_version=exec_cmd.get("fix_version"),
        )

        if success:
            success_count += 1
            logger.info(f"✅ {exec_cmd['jira_id']} completed successfully")
        else:
            failed_count += 1
            logger.error(f"❌ {exec_cmd['jira_id']} failed")

        logger.info(f"Progress: {i}/{len(executable_commands)} ({success_count} success, {failed_count} failed)")

    logger.info("")
    logger.info("=" * 60)
    logger.info("EXECUTION SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Total commands: {len(executable_commands)}")
    logger.info(f"✅ Successful: {success_count}")
    logger.info(f"❌ Failed: {failed_count}")
    logger.info(f"📄 Execution log: {EXECUTIONS_FILE}")
    logger.info("=" * 60)


def write_table_to_file(filepath, content):
    """Write table content to a file."""
    with open(filepath, 'w') as f:
        f.write(content)


def create_jira_table(jira_issues, commands=None, validations=None, rhosd_kernels=None):
    """
    Create a formatted table with JIRA issues, commands, validation status, and RHOS-D kernel versions.
    
    Args:
        jira_issues: List of JIRA issue dictionaries
        commands: Optional list of command dictionaries
        validations: Optional list of validation result dictionaries
        rhosd_kernels: Optional dict mapping JIRA IDs to RHOS-D kernel versions
        
    Returns:
        String containing the formatted table
    """
    lines = []
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    lines.append("=" * 450)
    lines.append(f"KERNEL BZ POLLING - JIRA ISSUES, COMMANDS, AND VALIDATION STATUS")
    lines.append(f"Generated: {timestamp}")
    lines.append("=" * 450)
    lines.append("")
    
    # Create command and validation mappings if provided
    command_map = {}
    validation_map = {}
    if commands:
        command_map = {cmd['jira_id']: cmd for cmd in commands}
    if validations:
        validation_map = {val['jira_id']: val for val in validations}
    
    # Create command mapping for easy lookup
    command_map_by_jira = {}
    if commands:
        command_map_by_jira = {cmd['jira_id']: cmd for cmd in commands}
    
    # Table header
    if validations:
        header = f"{'#':<4} | {'JIRA ID':<15} | {'Fix Version':<20} | {'Inventory':<45} | {'Status':<15} | {'Repo':<6} | {'Valid':<7} | {'Repo Kernel':<50} | {'RHOS-D Kernel':<50} | {'QA Contact':<25} | {'CephCI Command'}"
    elif commands:
        header = f"{'#':<4} | {'JIRA ID':<15} | {'Fix Version':<20} | {'Inventory':<45} | {'Status':<15} | {'Repo':<6} | {'Repo Kernel':<50} | {'RHOS-D Kernel':<50} | {'QA Contact':<25} | {'CephCI Command'}"
    else:
        header = f"{'#':<4} | {'JIRA ID':<15} | {'Fix Version':<20} | {'Inventory':<45} | {'Status':<15} | {'Repo':<6} | {'Repo Kernel':<50} | {'RHOS-D Kernel':<50} | {'QA Contact':<25} | {'Title':<80}"
    
    lines.append(header)
    lines.append("-" * 450)
    
    # Table rows
    for idx, issue in enumerate(jira_issues, 1):
        jira_id = issue.get('jira_id', 'N/A')
        
        # Get fix version (first one if multiple)
        fix_versions = issue.get('fix_versions', [])
        fix_version = fix_versions[0][:19] if fix_versions else 'N/A'
        
        # Get inventory from command
        inventory = 'N/A'
        if jira_id in command_map_by_jira:
            inv = command_map_by_jira[jira_id].get('inventory', 'N/A')
            # Show just the filename for brevity
            inventory = inv.split('/')[-1][:44] if inv != 'N/A' else 'N/A'
        
        status = issue.get('status', 'N/A')[:14]
        repo = '✓' if issue.get('repo_url') else '✗'
        repo_kernel = issue.get('repo_kernel_version', 'N/A')[:49]
        qa = issue.get('qa_contact', 'N/A')[:24]
        
        # Get RHOS-D kernel version for this JIRA
        rhosd_kernel = 'N/A'
        if rhosd_kernels and jira_id in rhosd_kernels:
            rhosd_kernel = rhosd_kernels[jira_id][:49]
        
        # Get validation status
        valid_status = ''
        if validations and jira_id in validation_map:
            val = validation_map[jira_id]
            valid_status = '✓ PASS' if val.get('all_valid', False) else '✗ FAIL'
        
        if commands and jira_id in command_map:
            # Show full command with validation
            cmd = command_map[jira_id]['command']
            if validations:
                row = f"{idx:<4} | {jira_id:<15} | {fix_version:<20} | {inventory:<45} | {status:<15} | {repo:<6} | {valid_status:<7} | {repo_kernel:<50} | {rhosd_kernel:<50} | {qa:<25} | {cmd}"
            else:
                row = f"{idx:<4} | {jira_id:<15} | {fix_version:<20} | {inventory:<45} | {status:<15} | {repo:<6} | {repo_kernel:<50} | {rhosd_kernel:<50} | {qa:<25} | {cmd}"
        else:
            # Show title
            title = issue.get('jira_title', 'N/A')[:79]
            row = f"{idx:<4} | {jira_id:<15} | {fix_version:<20} | {inventory:<45} | {status:<15} | {repo:<6} | {repo_kernel:<50} | {rhosd_kernel:<50} | {qa:<25} | {title:<80}"
        
        lines.append(row)
    
    lines.append("=" * 450)
    lines.append("")
    lines.append(f"Total Issues: {len(jira_issues)}")
    if commands:
        lines.append(f"Total Commands Built: {len(commands)}")
    if validations:
        valid_count = sum(1 for v in validations if v.get('all_valid', False))
        lines.append(f"Valid Commands: {valid_count}/{len(validations)}")
    lines.append("")
    
    return "\n".join(lines)


def main():
    """Main application entry point."""
    args = parse_args()

    try:
        # Load configuration
        config = get_config()
        
        # Setup logging
        log_level = config.get('logging.level', 'INFO')
        log_dir = config.get('logging.log_dir', './data/logs')
        console_output = config.get('logging.console_output', True)
        file_output = config.get('logging.file_output', True)
        
        setup_logging(
            log_level=log_level,
            log_dir=log_dir,
            console_output=console_output,
            file_output=file_output,
        )
        
        logger = get_logger(__name__)
        logger.info("=" * 60)
        logger.info("Kernel BZ Polling System - Starting")
        logger.info("=" * 60)
        
        # Display configuration
        logger.info(f"Application: {config.get('application.name')}")
        logger.info(f"Version: {config.get('application.version')}")
        logger.info(f"Environment: {config.get('application.environment')}")
        logger.info(f"JIRA Server: {config.get('jira.server')}")
        logger.info(f"CephCI Repo: {config.get('cephci.repo_url')}")
        logger.info(f"CephCI Branch: {config.get('cephci.branch')}")
        logger.info(f"Log Level: {log_level}")
        logger.info(f"Scheduler Enabled: {config.get('scheduler.enable_scheduler')}")
        logger.info(f"Poll Interval: {config.get('scheduler.poll_interval_hours')} hours")
        logger.info("=" * 60)

        # --publish-dashboard: publish from existing data and exit
        if args.publish_dashboard:
            from src.dashboard.publisher import publish_dashboard
            logger.info("Publishing dashboard from existing execution data...")
            if publish_dashboard():
                logger.info("Dashboard published successfully")
            else:
                logger.error("Dashboard publish failed")
            return

        # --execute-only: skip Phases 2-3.7, jump straight to execution
        if args.execute_only:
            run_execute_only(args, logger)
            return
        
        # Phase 2 - JIRA Integration: Fetch Real Issues
        logger.info("=" * 60)
        logger.info("Phase 2: JIRA Integration - Fetching Issues")
        logger.info("=" * 60)
        
        jira_issues = []
        try:
            from src.jira.query import JiraQueryExecutor
            
            executor = JiraQueryExecutor()
            logger.info("✅ JIRA Query Executor initialized")
            
            # Execute the kernel_cephfs_integration query to get real issues (no cache)
            logger.info("Executing JIRA query: kernel_cephfs_integration (fetching fresh data)")
            jira_issues = executor.execute_query('kernel_cephfs_integration', use_cache=False)
            
            if jira_issues:
                logger.info(f"✅ Found {len(jira_issues)} JIRA issues")
                
                # Create table and write to file
                table_content = create_jira_table(jira_issues)
                table_file = "data/jira_issues_table.txt"
                
                # Ensure data directory exists
                Path("data").mkdir(exist_ok=True)
                
                write_table_to_file(table_file, table_content)
                logger.info(f"📄 JIRA issues table saved to: {table_file}")
                logger.info(f"   Total issues: {len(jira_issues)}")
                logger.info(f"   View the file for complete details")
            else:
                logger.warning("⚠️  No JIRA issues found matching the query")
            
            logger.info("Phase 2: JIRA Integration - COMPLETED ✅")
            
        except Exception as e:
            logger.error(f"Phase 2 error: {e}", exc_info=True)
            logger.warning("Phase 2: JIRA Integration - Failed to fetch issues")
        
        logger.info("=" * 60)

        # Verification type filtering
        if args.verification_type and jira_issues:
            target = args.verification_type
            before = len(jira_issues)
            jira_issues = [i for i in jira_issues if i.get('verification_type') == target]
            logger.info(f"--verification-type {target}: {before} -> {len(jira_issues)} JIRAs")

        # Exclusion list filtering
        exclusion_map = load_exclusion_list(logger)
        if exclusion_map and jira_issues:
            before = len(jira_issues)
            excluded_ids = []
            remaining = []
            for issue in jira_issues:
                jid = issue.get("jira_id")
                if jid in exclusion_map:
                    excluded_ids.append((jid, exclusion_map[jid]))
                else:
                    remaining.append(issue)
            if excluded_ids:
                logger.info(f"Exclusion list: filtering out {len(excluded_ids)} JIRAs:")
                for jid, reason in excluded_ids:
                    logger.info(f"  - {jid}: {reason}")
                jira_issues = remaining
                logger.info(f"Remaining JIRAs: {before} -> {len(jira_issues)}")
                record_excluded_jiras(
                    {jid: reason for jid, reason in excluded_ids}, logger
                )

        # Phase 2.5 - Pre-validation: skip JIRAs already passed in dashboard
        logger.info("=" * 60)
        logger.info("Phase 2.5: Pre-Validation — Skip Already-Passed JIRAs")
        logger.info("=" * 60)

        try:
            from src.dashboard.publisher import fetch_passed_jiras

            passed_set = fetch_passed_jiras()
            if passed_set:
                logger.info(f"Found {len(passed_set)} passed entries in dashboard history")
                before_count = len(jira_issues)
                skipped = []
                remaining = []
                for issue in jira_issues:
                    key = f"{issue.get('jira_id')}:{issue.get('verification_type', 'N/A')}"
                    if key in passed_set:
                        skipped.append(key)
                    else:
                        remaining.append(issue)
                if skipped:
                    for s in skipped:
                        logger.info(f"  Skipping {s} (already passed)")
                    jira_issues = remaining
                    logger.info(
                        f"Filtered: {before_count} -> {len(jira_issues)} JIRAs "
                        f"({len(skipped)} skipped)"
                    )
                else:
                    logger.info("No JIRAs to skip — all need execution")
            else:
                logger.info("No passed entries found in dashboard (first run or fetch failed)")

            logger.info("Phase 2.5: Pre-Validation - COMPLETED ✅")
        except Exception as e:
            logger.warning(f"Phase 2.5 error (non-fatal, continuing): {e}")

        logger.info("=" * 60)
        
        # Phase 3 - CephCI Command Builder: Build Commands for All Issues
        logger.info("=" * 60)
        logger.info("Phase 3: CephCI Command Builder - Building Commands")
        logger.info("=" * 60)
        
        commands = []
        try:
            from src.cephci.command_builder import CephCICommandBuilder
            
            builder = CephCICommandBuilder()
            logger.info("✅ CephCI Command Builder initialized")
            
            if jira_issues:
                logger.info(f"Building commands for {len(jira_issues)} JIRA issues...")
                commands = builder.build_commands_batch(jira_issues)
                
                logger.info(f"✅ Successfully built {len(commands)} commands")
                
                # Update table with commands and write to file
                table_content = create_jira_table(jira_issues, commands)
                table_file = "data/jira_issues_table.txt"
                
                write_table_to_file(table_file, table_content)
                logger.info(f"📄 Updated table with commands saved to: {table_file}")
                logger.info(f"   Total issues: {len(jira_issues)}")
                logger.info(f"   Total commands: {len(commands)}")
                logger.info(f"   View the file for complete command details")
                
                # Print first command as example in console
                if commands:
                    logger.info("")
                    logger.info("EXAMPLE: First Command Details:")
                    builder.print_command(commands[0])
            else:
                logger.warning("⚠️  No JIRA issues available to build commands")
            
            logger.info("Phase 3: CephCI Command Builder - COMPLETED ✅")
            
        except Exception as e:
            logger.error(f"Phase 3 error: {e}", exc_info=True)
            logger.warning("Phase 3: CephCI Command Builder - Failed to build commands")
        
        logger.info("=" * 60)
        
        # Phase 3.5 - Inventory File Validation
        logger.info("=" * 60)
        logger.info("Phase 3.5: Inventory File Validation")
        logger.info("=" * 60)
        
        validations = []
        repo_manager = None  # Initialize outside try block for Phase 3.6
        try:
            from src.cephci.repo_manager import CephCIRepoManager
            from src.cephci.validator import CephCIValidator
            
            # Initialize cephci repo
            repo_manager = CephCIRepoManager()
            logger.info("Ensuring cephci repository is available...")
            
            success, msg = repo_manager.ensure_repo_exists()
            if success:
                logger.info(f"✅ {msg}")
                # Re-apply patches after git pull (which resets working tree)
                patches_script = Path("docker/patches/apply_patches.py")
                if patches_script.exists():
                    import subprocess as _sp
                    _sp.run(
                        ["python3", str(patches_script), repo_manager.local_path],
                        capture_output=False,
                    )
            else:
                logger.warning(f"⚠️  {msg}")
                logger.warning("Validation may fail due to missing repository")
            
            validator = CephCIValidator()
            logger.info("✅ CephCI Validator initialized")
            
            if commands:
                logger.info(f"Validating files for {len(commands)} commands...")
                validations = validator.validate_commands_batch(commands)
                
                # Count valid commands
                valid_count = sum(1 for v in validations if v.get('all_valid', False))
                logger.info(f"✅ Validation complete: {valid_count}/{len(validations)} commands have all required files")
                
                # Update table with validation status and write to file
                table_content = create_jira_table(jira_issues, commands, validations)
                table_file = "data/jira_issues_table.txt"
                
                write_table_to_file(table_file, table_content)
                logger.info(f"📄 Updated table with validation status saved to: {table_file}")
                logger.info(f"   Total issues: {len(jira_issues)}")
                logger.info(f"   Total commands: {len(commands)}")
                logger.info(f"   Valid commands: {valid_count}/{len(validations)}")
                logger.info(f"   View the file for complete details")
                
                # Show validation summary
                if valid_count < len(validations):
                    logger.warning(f"⚠️  {len(validations) - valid_count} commands have missing files")
                    logger.info("Check the table file for details on missing files")
            else:
                logger.warning("⚠️  No commands available to validate")
            
            logger.info("Phase 3.5: Inventory File Validation - COMPLETED ✅")
            
        except Exception as e:
            logger.error(f"Phase 3.5 error: {e}", exc_info=True)
            logger.warning("Phase 3.5: Inventory File Validation - Failed")
        
        # Phase 3.8 - Generate Missing Inventory Files
        logger.info("=" * 60)
        logger.info("Phase 3.8: Generate Missing Inventory Files")
        logger.info("=" * 60)
        
        try:
            from src.cephci.inventory_generator import InventoryGenerator
            
            if commands and repo_manager:
                generator = InventoryGenerator(repo_manager.local_path)
                logger.info("✅ Inventory Generator initialized")
                
                # Extract unique inventory paths from commands
                inventory_paths = set()
                for cmd in commands:
                    params = cmd.get('params', {})
                    inv_path = params.get('--inventory')
                    if inv_path:
                        inventory_paths.add(inv_path)
                
                logger.info(f"Checking {len(inventory_paths)} inventory files...")
                
                # Check and generate missing inventory files
                generated_count = 0
                existing_count = 0
                failed_count = 0
                
                for inv_path in inventory_paths:
                    if generator.ensure_inventory_exists(inv_path):
                        full_path = Path(repo_manager.local_path) / inv_path
                        if full_path.exists():
                            # Check if it was just created
                            import os
                            import time
                            file_age = time.time() - os.path.getmtime(full_path)
                            if file_age < 5:  # Created in last 5 seconds
                                logger.info(f"  ✅ Generated: {inv_path}")
                                generated_count += 1
                            else:
                                existing_count += 1
                    else:
                        logger.warning(f"  ❌ Failed to ensure: {inv_path}")
                        failed_count += 1
                
                logger.info(f"✅ Inventory file check complete:")
                logger.info(f"   Existing: {existing_count}")
                logger.info(f"   Generated: {generated_count}")
                if failed_count > 0:
                    logger.warning(f"   Failed: {failed_count}")
                
                # Re-validate after generating files
                if generated_count > 0:
                    logger.info("Re-validating commands after generating inventory files...")
                    from src.cephci.validator import CephCIValidator
                    validator = CephCIValidator()
                    validations = validator.validate_commands_batch(commands)
                    
                    valid_count = sum(1 for v in validations if v.get('all_valid', False))
                    logger.info(f"✅ Re-validation complete: {valid_count}/{len(validations)} commands valid")
                    
                    # Update table with new validation status
                    table_content = create_jira_table(jira_issues, commands, validations)
                    table_file = "data/jira_issues_table.txt"
                    write_table_to_file(table_file, table_content)
                    logger.info(f"📄 Updated table saved to: {table_file}")
            else:
                logger.warning("⚠️  No commands or repo manager available")
            
            logger.info("Phase 3.8: Generate Missing Inventory Files - COMPLETED ✅")
            
        except Exception as e:
            logger.error(f"Phase 3.8 error: {e}", exc_info=True)
            logger.warning("Phase 3.8: Generate Missing Inventory Files - Failed")
        
        logger.info("=" * 60)
        
        logger.info("=" * 60)
        
        # Phase 3.6 - Parse Inventory Files for Image Info
        logger.info("=" * 60)
        logger.info("Phase 3.6: Inventory Image Information Extraction")
        logger.info("=" * 60)
        
        image_info_map = {}
        try:
            from src.cephci.inventory_parser import InventoryParser
            
            if commands and repo_manager:
                parser = InventoryParser(repo_manager.local_path)
                logger.info("✅ Inventory Parser initialized")
                
                # Extract unique inventory paths from commands
                # Inventory is in params['--inventory']
                inventory_paths = set()
                for cmd in commands:
                    params = cmd.get('params', {})
                    inv_path = params.get('--inventory')
                    if inv_path:
                        inventory_paths.add(inv_path)
                
                logger.info(f"Parsing {len(inventory_paths)} unique inventory files...")
                
                # Parse all inventory files
                for inv_path in inventory_paths:
                    image_info = parser.parse_inventory_file(inv_path)
                    if image_info:
                        image_info_map[inv_path] = image_info
                        logger.info(f"  ✅ {inv_path}")
                        logger.info(f"     Image: {image_info['image_name']}")
                        logger.info(f"     VM Size: {image_info['vm_size']}")
                        logger.info(f"     RHEL Version: {image_info['version_id']}")
                    else:
                        logger.warning(f"  ⚠️  Failed to parse {inv_path}")
                
                logger.info(f"✅ Parsed {len(image_info_map)}/{len(inventory_paths)} inventory files")
                
                # Save image info to JSON file for later use
                import json
                image_info_file = "data/inventory_image_info.json"
                os.makedirs(os.path.dirname(image_info_file), exist_ok=True)
                with open(image_info_file, 'w') as f:
                    json.dump(image_info_map, f, indent=2)
                logger.info(f"📄 Image information saved to: {image_info_file}")
                
            else:
                logger.warning("⚠️  No commands or repo manager available")
            
            logger.info("Phase 3.6: Inventory Image Information Extraction - COMPLETED ✅")
            
        except Exception as e:
            logger.error(f"Phase 3.6 error: {e}", exc_info=True)
            logger.warning("Phase 3.6: Inventory Image Information Extraction - Failed")
        
        logger.info("=" * 60)
        
        # Phase 3.7 - Fetch RHOS-D Kernel Versions
        logger.info("=" * 60)
        logger.info("Phase 3.7: RHOS-D Kernel Version Fetching")
        logger.info("=" * 60)
        
        rhosd_kernel_map = {}
        rhosd_jira_baselines = {}
        try:
            from src.rhosd.image_api import RHOSDImageAPI
            
            if image_info_map and jira_issues and commands:
                # Build per-JIRA list: each JIRA needs its own baseline search
                jira_search_list = []
                for issue in jira_issues:
                    jira_id = issue.get('jira_id')
                    repo_kernel = issue.get('repo_kernel_version')
                    for cmd in commands:
                        if cmd.get('jira_id') == jira_id:
                            inventory = cmd.get('inventory')
                            is_rhel8 = cmd.get('is_rhel8', False)
                            if inventory and repo_kernel:
                                if is_rhel8:
                                    # RHEL-8: search RHOS-D using RHEL-8 image pattern
                                    fv = issue.get('fix_versions', [])
                                    rhel8_minor = None
                                    for v in fv:
                                        cv = v.replace('rhel-', '').replace('RHEL-', '')
                                        parts = cv.split('.')
                                        if len(parts) >= 2 and parts[0] == '8':
                                            rhel8_minor = f"{parts[0]}.{parts[1]}"
                                            break
                                    if rhel8_minor:
                                        rhel8_inv_key = f"conf/inventory/rhel-{rhel8_minor}-server-x86_64.yaml"
                                        rhel8_image = f"RHEL-{rhel8_minor}.0-x86_64-ga-latest"
                                        if rhel8_inv_key not in image_info_map:
                                            image_info_map[rhel8_inv_key] = {
                                                'image_name': rhel8_image,
                                                'version_id': rhel8_minor,
                                                'vm_size': 'ci.standard.medium',
                                            }
                                            logger.info(f"  Added synthetic RHEL-8 image info: {rhel8_image}")
                                        jira_search_list.append({
                                            'jira_id': jira_id,
                                            'inv_path': rhel8_inv_key,
                                            'jira_kernel': repo_kernel,
                                            'is_rhel8': True,
                                            'global_conf': cmd.get('global_conf'),
                                        })
                                    else:
                                        logger.warning(f"  Could not derive RHEL-8 minor version for {jira_id}")
                                else:
                                    jira_search_list.append({
                                        'jira_id': jira_id,
                                        'inv_path': inventory,
                                        'jira_kernel': repo_kernel,
                                        'is_rhel8': False,
                                        'global_conf': cmd.get('global_conf'),
                                    })
                            break
                
                logger.info(f"Built per-JIRA search list: {len(jira_search_list)} JIRAs")
                
                # Group by unique (inv_path, jira_kernel) to avoid redundant API calls
                from collections import defaultdict
                unique_searches = {}
                for item in jira_search_list:
                    key = (item['inv_path'], item['jira_kernel'])
                    unique_searches[key] = item
                logger.info(f"Unique (inventory, kernel) pairs to search: {len(unique_searches)}")
                
                rhosd_api = RHOSDImageAPI()
                logger.info("✅ RHOS-D Image API initialized")
                
                if rhosd_api.authenticate():
                    logger.info("✅ Authenticated with RHOS-D")
                    
                    # Search for baseline image per unique (inventory, jira_kernel) pair
                    search_cache = {}
                    for (inv_path, jira_kernel), item in unique_searches.items():
                        if inv_path not in image_info_map:
                            logger.warning(f"  ⚠️  {inv_path}: not in image_info_map, skipping")
                            continue
                        
                        logger.info(f"  🔍 Searching baseline for {inv_path} (JIRA kernel: {jira_kernel})")
                        single_inv_map = {inv_path: image_info_map[inv_path]}
                        single_kernel_map = {inv_path: jira_kernel}
                        
                        rhosd_results = rhosd_api.search_images_for_inventory(
                            single_inv_map,
                            max_images_per_inventory=1,
                            jira_kernel_versions=single_kernel_map
                        )
                        
                        if inv_path in rhosd_results and rhosd_results[inv_path]:
                            img = rhosd_results[inv_path][0]
                            kernel_ver = img.get('kernel_version')
                            baseline_name = img.get('name')
                            if kernel_ver and baseline_name:
                                search_cache[(inv_path, jira_kernel)] = {
                                    'baseline_image': baseline_name,
                                    'baseline_kernel': kernel_ver,
                                }
                                logger.info(f"    ✅ Baseline: {baseline_name} (kernel: {kernel_ver})")
                            else:
                                logger.warning(f"    ⚠️  No valid baseline found")
                        else:
                            logger.warning(f"    ⚠️  No matching images found")
                    
                    # Map results back to individual JIRAs
                    for item in jira_search_list:
                        key = (item['inv_path'], item['jira_kernel'])
                        if key in search_cache:
                            cached = search_cache[key]
                            rhosd_jira_baselines[item['jira_id']] = {
                                'inv_path': item['inv_path'],
                                'jira_kernel': item['jira_kernel'],
                                'baseline_image': cached['baseline_image'],
                                'baseline_kernel': cached['baseline_kernel'],
                                'is_rhel8': item.get('is_rhel8', False),
                                'global_conf': item.get('global_conf'),
                            }
                            rhosd_kernel_map[item['jira_id']] = cached['baseline_kernel']
                    
                    logger.info(f"✅ Found baselines for {len(rhosd_jira_baselines)}/{len(jira_search_list)} JIRAs")
                    
                    # Save per-JIRA baseline data
                    rhosd_file = "data/rhosd_kernel_versions.json"
                    with open(rhosd_file, 'w') as f:
                        json.dump(rhosd_jira_baselines, f, indent=2)
                    logger.info(f"📄 Per-JIRA baseline data saved to: {rhosd_file}")
                    
                    # Regenerate table with per-JIRA RHOS-D kernel versions
                    if commands and validations:
                        logger.info("Regenerating table with RHOS-D kernel versions...")
                        table_content = create_jira_table(jira_issues, commands, validations, rhosd_kernel_map)
                        table_file = "data/jira_issues_table.txt"
                        write_table_to_file(table_file, table_content)
                        logger.info(f"📄 Updated table with RHOS-D kernels saved to: {table_file}")
                    
                else:
                    logger.error("❌ Failed to authenticate with RHOS-D")
            else:
                logger.warning("⚠️  No inventory image info available")
            
            logger.info("Phase 3.7: RHOS-D Kernel Version Fetching - COMPLETED ✅")
            
        except Exception as e:
            logger.error(f"Phase 3.7 error: {e}", exc_info=True)
            logger.warning("Phase 3.7: RHOS-D Kernel Version Fetching - Failed")
        
        logger.info("=" * 60)
        
        # Phase 4 - Command Execution for JIRAs with RHOS-D Kernels
        logger.info("=" * 60)
        logger.info("Phase 4: Command Execution")
        logger.info("=" * 60)
        
        try:
            from src.cephci.command_executor import CephCIExecutor
            
            executor = CephCIExecutor(skip_dashboard=args.skip_dashboard)
            logger.info("✅ CephCI Executor initialized")
            
            # Filter commands that have RHOS-D baselines (not N/A)
            if commands and rhosd_jira_baselines:
                vtype_map = {issue.get('jira_id'): issue.get('verification_type', 'N/A') for issue in jira_issues}
                jira_kernel_map = {issue.get('jira_id'): issue.get('repo_kernel_version', 'N/A') for issue in jira_issues}
                fix_version_map = {}
                for issue in jira_issues:
                    fv = issue.get('fix_versions', [])
                    fix_version_map[issue.get('jira_id')] = fv[0] if fv else 'N/A'

                # Extract platform from command string
                def _platform_from_cmd(cmd_str):
                    if cmd_str and "--platform " in cmd_str:
                        return cmd_str.split("--platform ")[1].split(" ")[0]
                    return None

                executable_commands = []
                skipped_commands = []
                for cmd in commands:
                    jira_id = cmd.get('jira_id')
                    params = cmd.get('params', {})
                    inventory = params.get('--inventory')
                    vtype = vtype_map.get(jira_id, 'N/A')
                    jira_kernel = jira_kernel_map.get(jira_id, 'N/A')
                    platform = _platform_from_cmd(cmd.get('command'))

                    baseline = rhosd_jira_baselines.get(jira_id)

                    fix_version = fix_version_map.get(jira_id, 'N/A')

                    if baseline and baseline.get('baseline_kernel'):
                        executable_commands.append({
                            'jira_id': jira_id,
                            'command': cmd.get('command'),
                            'rhosd_kernel': baseline['baseline_kernel'],
                            'baseline_image': baseline['baseline_image'],
                            'inventory': inventory,
                            'verification_type': vtype,
                            'jira_kernel': jira_kernel,
                            'fix_version': fix_version,
                            'platform': platform,
                            'is_rhel8': cmd.get('is_rhel8', False),
                            'global_conf': cmd.get('global_conf'),
                        })
                    else:
                        skipped_commands.append({
                            'jira_id': jira_id,
                            'inventory': inventory,
                            'verification_type': vtype,
                            'jira_kernel': jira_kernel,
                            'fix_version': fix_version,
                            'platform': platform,
                        })

                # Record skipped JIRAs in the execution log
                if skipped_commands:
                    logger.info(f"Skipping {len(skipped_commands)} JIRAs with no RHOS-D kernel:")
                    for sc in skipped_commands:
                        logger.info(f"  - {sc['jira_id']} (verification: {sc['verification_type']})")
                        executor.execution_log[sc['jira_id']] = {
                            'jira_id': sc['jira_id'],
                            'status': 'skipped',
                            'reason': 'No RHOS-D kernel available (N/A)',
                            'inventory': sc.get('inventory'),
                            'platform': sc.get('platform'),
                            'jira_kernel': sc.get('jira_kernel'),
                            'fix_version': sc.get('fix_version'),
                            'verification_type': sc['verification_type'],
                            'start_time': datetime.now().isoformat(),
                        }
                    executor._save_execution_log()
                
                if executable_commands:
                    logger.info(f"Found {len(executable_commands)} JIRAs with RHOS-D kernels ready for execution")
                    logger.info("")
                    logger.info("JIRAs to execute:")
                    for i, exec_cmd in enumerate(executable_commands, 1):
                        logger.info(f"  {i}. {exec_cmd['jira_id']} - Baseline: {exec_cmd.get('baseline_image', 'N/A')} (kernel: {exec_cmd['rhosd_kernel']})")
                    
                    logger.info("")
                    logger.info("=" * 60)
                    logger.info("⚠️  EXECUTION MODE")
                    logger.info("=" * 60)
                    logger.info("Commands will be executed ONE BY ONE in the cephci repository")
                    logger.info("Each command may take 30-60 minutes to complete")
                    logger.info("")
                    
                    # Ask for confirmation (skip prompt in dry-run mode)
                    if args.dry_run:
                        logger.info("DRY RUN - showing commands without executing:")
                        for exec_cmd in executable_commands:
                            logger.info(f"  [{exec_cmd['jira_id']}] {exec_cmd['command']}")
                        logger.info("DRY RUN complete. No commands were executed.")
                        response = 'no'
                    elif args.yes:
                        logger.info("Auto-confirmed via --yes flag")
                        response = 'yes'
                    else:
                        response = input("Do you want to execute these commands? (yes/no): ").strip().lower()
                    
                    if response == 'yes':
                        if not executor.install_dependencies():
                            logger.error("Failed to install cephci dependencies. Aborting execution.")
                            response = 'no'

                    if response == 'yes':
                        if not executor.populate_osp_credentials():
                            logger.error("Failed to populate OSP credentials. Aborting execution.")
                            response = 'no'

                    if response == 'yes':
                        logger.info("✅ Starting command execution...")
                        
                        success_count = 0
                        failed_count = 0
                        
                        for i, exec_cmd in enumerate(executable_commands, 1):
                            logger.info("")
                            logger.info(f"{'='*80}")
                            logger.info(f"Executing {i}/{len(executable_commands)}: {exec_cmd['jira_id']}")
                            logger.info(f"{'='*80}")
                            
                            # Update baseline image for THIS JIRA
                            baseline_image = exec_cmd.get('baseline_image')
                            if exec_cmd.get('is_rhel8') and baseline_image:
                                # RHEL-8: update openstack image-name in global conf (node4 + node5)
                                gc_path = exec_cmd.get('global_conf')
                                if gc_path:
                                    gc_full_path = Path("cephci") / gc_path
                                    if gc_full_path.exists():
                                        try:
                                            with open(gc_full_path) as f:
                                                gc_data = yaml.safe_load(f)
                                            updated = 0
                                            for glob_entry in gc_data.get("globals", []):
                                                cluster = glob_entry.get("ceph-cluster", {})
                                                for node_key, node_val in cluster.items():
                                                    if not isinstance(node_val, dict):
                                                        continue
                                                    img_block = node_val.get("image-name")
                                                    if isinstance(img_block, dict) and "openstack" in img_block:
                                                        old_img = img_block["openstack"]
                                                        if old_img != baseline_image:
                                                            img_block["openstack"] = baseline_image
                                                            updated += 1
                                            if updated:
                                                with open(gc_full_path, "w") as f:
                                                    yaml.dump(gc_data, f, default_flow_style=False, sort_keys=False)
                                                logger.info(f"  🔄 Global conf updated: {updated} openstack image(s) -> '{baseline_image}'")
                                            else:
                                                logger.info(f"  ✅ Global conf already using correct baseline")
                                        except Exception as e:
                                            logger.warning(f"  ⚠️  Failed to update global conf {gc_path}: {e}")
                                    else:
                                        logger.warning(f"  ⚠️  Global conf not found: {gc_full_path}")
                            elif baseline_image:
                                # Non-RHEL-8: update inventory image-name as before
                                inv_path = exec_cmd.get('inventory')
                                if inv_path:
                                    inv_full_path = Path("cephci") / inv_path
                                    if inv_full_path.exists():
                                        try:
                                            with open(inv_full_path) as f:
                                                inv_data = yaml.safe_load(f)
                                            current_image = inv_data.get("instance", {}).get("create", {}).get("image-name")
                                            if current_image != baseline_image:
                                                inv_data["instance"]["create"]["image-name"] = baseline_image
                                                with open(inv_full_path, "w") as f:
                                                    yaml.dump(inv_data, f, default_flow_style=False, sort_keys=False)
                                                logger.info(f"  🔄 Inventory updated: '{current_image}' -> '{baseline_image}'")
                                            else:
                                                logger.info(f"  ✅ Inventory already using correct baseline: '{baseline_image}'")
                                        except Exception as e:
                                            logger.warning(f"  ⚠️  Failed to update inventory {inv_path}: {e}")
                            
                            success, stdout, stderr = executor.execute_command(
                                jira_id=exec_cmd['jira_id'],
                                command=exec_cmd['command'],
                                rhosd_kernel=exec_cmd['rhosd_kernel'],
                                dry_run=False,
                                verification_type=exec_cmd.get('verification_type'),
                                jira_kernel=exec_cmd.get('jira_kernel'),
                                fix_version=exec_cmd.get('fix_version'),
                            )
                            
                            if success:
                                success_count += 1
                                logger.info(f"✅ {exec_cmd['jira_id']} completed successfully")
                            else:
                                failed_count += 1
                                logger.error(f"❌ {exec_cmd['jira_id']} failed")
                            
                            logger.info(f"Progress: {i}/{len(executable_commands)} ({success_count} success, {failed_count} failed)")
                        
                        logger.info("")
                        logger.info("=" * 60)
                        logger.info("EXECUTION SUMMARY")
                        logger.info("=" * 60)
                        logger.info(f"Total commands: {len(executable_commands)}")
                        logger.info(f"✅ Successful: {success_count}")
                        logger.info(f"❌ Failed: {failed_count}")
                        logger.info(f"📄 Execution log: data/command_executions.json")
                        logger.info("=" * 60)
                        
                    else:
                        logger.info("❌ Execution cancelled by user")
                        logger.info("To execute later, run the script again and choose 'yes'")
                else:
                    logger.warning("⚠️  No JIRAs with RHOS-D kernels found for execution")
                    logger.info("All JIRAs either have N/A for RHOS-D kernel or no matching images found")
            else:
                logger.warning("⚠️  No commands or RHOS-D kernel data available")
            
            logger.info("Phase 4: Command Execution - COMPLETED ✅")
            
        except Exception as e:
            logger.error(f"Phase 4 error: {e}", exc_info=True)
            logger.warning("Phase 4: Command Execution - Failed")
        
        logger.info("=" * 60)
        
        # TODO: Phase 5 - Initialize log manager
        logger.info("Phase 5: Log Management - Not yet implemented")
        
        # TODO: Phase 6 - Initialize log manager
        logger.info("Phase 6: Log Management - Not yet implemented")
        
        # TODO: Phase 7 - Initialize scheduler
        logger.info("Phase 7: Scheduler & Job Queue - Not yet implemented")
        
        # TODO: Phase 8 - Initialize dashboard backend
        logger.info("Phase 8: Dashboard Backend - Not yet implemented")
        
        # TODO: Phase 9 - Start dashboard server
        logger.info("Phase 9: GitHub Pages Dashboard - Not yet implemented")
        
        logger.info("=" * 60)
        logger.info("IMPLEMENTATION STATUS:")
        logger.info("✅ Phase 1: Project Foundation - COMPLETED")
        logger.info("✅ Phase 2: JIRA Integration - COMPLETED")
        logger.info("✅ Phase 3: CephCI Command Builder - COMPLETED")
        logger.info("✅ Phase 3.5: Inventory File Validation - COMPLETED")
        logger.info("✅ Phase 3.6: Inventory Image Info Extraction - COMPLETED")
        logger.info("✅ Phase 3.7: RHOS-D Kernel Version Fetching - COMPLETED")
        logger.info("✅ Phase 3.8: Inventory File Generation - COMPLETED")
        logger.info("✅ Phase 4: Command Execution - COMPLETED")
        logger.info("⏳ Phase 5: Log Management - Pending")
        logger.info("⏳ Phase 6: Scheduler & Job Queue - Pending")
        logger.info("⏳ Phase 7: Dashboard Backend - Pending")
        logger.info("=" * 60)
        
        # Keep the application running
        logger.info("Application initialized successfully")
        logger.info("Press Ctrl+C to stop")
        
        # TODO: Replace with actual application loop
        import time
        while True:
            time.sleep(60)
            logger.debug("Application heartbeat...")
            
    except KeyboardInterrupt:
        logger = get_logger(__name__)
        logger.info("Received shutdown signal")
        logger.info("Shutting down gracefully...")
        sys.exit(0)
        
    except Exception as e:
        logger = get_logger(__name__)
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

# Made with Bob
