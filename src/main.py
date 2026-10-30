"""
Main entry point for the Kernel BZ Polling System.

Usage:
    python -m src.main                          # Full pipeline (Phases 1-8)
    python -m src.main --execute-only           # Re-run Phase 8 from saved data
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
BASELINE_IMAGES_FILE = "data/baseline_images.json"
# Legacy filename kept for execute-only fallback
RHOSD_KERNELS_FILE = "data/rhosd_kernel_versions.json"
EXCLUSION_LIST_FILE = "config/exclusion_list.yaml"


def load_baseline_images(logger):
    """Load per-JIRA baseline records (prefer baseline_images.json)."""
    for path in (Path(BASELINE_IMAGES_FILE), Path(RHOSD_KERNELS_FILE)):
        if not path.exists():
            continue
        try:
            with open(path) as f:
                data = json.load(f)
            logger.info(f"Loaded per-JIRA baselines from {path}")
            return data
        except Exception as e:
            logger.warning(f"Could not load baselines from {path}: {e}")
    return {}


def apply_baseline_to_cephci(exec_cmd, baseline_info, logger, cephci_root="cephci"):
    """Write baseline image into inventory or RHEL-8 global-conf.

    Prefers datasource://; falls back to validated guest HTTPS URL when the
    cluster DataSource is missing/unusable (ocpvirt.http_image_fallback).
    """
    from src.ocpvirt.image_source import RuntimeImageResolver

    ds_image = baseline_info.get("baseline_image") or exec_cmd.get("baseline_image")
    guest_url = baseline_info.get("guest_image_url") or exec_cmd.get("guest_image_url")
    resolver = RuntimeImageResolver(cephci_root=cephci_root)
    baseline_image, source_reason = resolver.resolve(ds_image, guest_url)
    if not baseline_image:
        logger.warning("  ⚠️  No datasource:// or guest HTTPS URL to apply")
        return

    if source_reason == "http_fallback":
        logger.info("  ↩️  Using HTTP guest-image fallback for provision")
    elif source_reason == "http_preferred":
        logger.info("  📎 Using preferred HTTP guest-image URL for provision")

    is_rhel8 = baseline_info.get("is_rhel8", exec_cmd.get("is_rhel8", False))
    root = Path(cephci_root)

    if is_rhel8:
        gc_path = baseline_info.get("global_conf") or exec_cmd.get("global_conf")
        if not gc_path:
            return
        gc_full_path = root / gc_path
        if not gc_full_path.exists():
            logger.warning(f"  ⚠️  Global conf not found: {gc_full_path}")
            return
        try:
            with open(gc_full_path) as f:
                gc_data = yaml.safe_load(f)
            updated = 0
            for glob_entry in gc_data.get("globals", []):
                cluster = glob_entry.get("ceph-cluster", {})
                for _node_key, node_val in cluster.items():
                    if not isinstance(node_val, dict):
                        continue
                    img_block = node_val.get("image-name")
                    if not isinstance(img_block, dict):
                        continue
                    for cloud_key in ("ocpvirt", "openstack"):
                        if cloud_key in img_block and img_block[cloud_key] != baseline_image:
                            img_block[cloud_key] = baseline_image
                            updated += 1
                    # Prefer ocpvirt key when only openstack existed
                    if "ocpvirt" not in img_block and "openstack" in img_block:
                        img_block["ocpvirt"] = baseline_image
                        updated += 1
            if updated:
                with open(gc_full_path, "w") as f:
                    yaml.dump(gc_data, f, default_flow_style=False, sort_keys=False)
                logger.info(
                    f"  🔄 Global conf updated: {updated} image(s) -> '{baseline_image}'"
                )
            else:
                logger.info("  ✅ Global conf already using correct baseline")
        except Exception as e:
            logger.warning(f"  ⚠️  Failed to update global conf {gc_path}: {e}")
        return

    inv_path = exec_cmd.get("inventory") or baseline_info.get("inventory")
    if not inv_path:
        return
    inv_full_path = root / inv_path
    if not inv_full_path.exists():
        logger.warning(f"  ⚠️  Inventory not found: {inv_full_path}")
        return
    try:
        with open(inv_full_path) as f:
            inv_data = yaml.safe_load(f)
        create = inv_data.setdefault("instance", {}).setdefault("create", {})
        current_image = create.get("image-name")
        if current_image != baseline_image:
            create["image-name"] = baseline_image
            with open(inv_full_path, "w") as f:
                yaml.dump(inv_data, f, default_flow_style=False, sort_keys=False)
            logger.info(
                f"  🔄 Inventory image: '{current_image}' -> '{baseline_image}'"
            )
        else:
            logger.info(
                f"  ✅ Inventory already using correct baseline: '{baseline_image}'"
            )
    except Exception as e:
        logger.warning(f"  ⚠️  Failed to update inventory {inv_path}: {e}")


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
        help="Skip Phases 1-7; load commands from data/command_executions.json and run Phase 8 directly",
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

        inventory = None
        if "--inventory " in cmd_str:
            inv_part = cmd_str.split("--inventory ")[1]
            inventory = inv_part.split(" ")[0]

        commands.append({
            "jira_id": record.get("jira_id", jira_id),
            "command": cmd_str,
            "rhosd_kernel": record.get("baseline_image") or record.get("rhosd_kernel"),
            "baseline_image": record.get("baseline_image") or record.get("rhosd_kernel"),
            "guest_image_url": record.get("guest_image_url"),
            "inventory": inventory,
            "prev_status": record.get("status"),
            "verification_type": record.get("verification_type", "N/A"),
            "jira_kernel": record.get("jira_kernel"),
            "fix_version": record.get("fix_version"),
        })

    logger.info(f"Loaded {len(commands)} commands from {EXECUTIONS_FILE}")
    return commands


def run_execute_only(args, logger):
    """Run Phase 8 (execution) directly from persisted data, skipping Phases 1-7."""
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
        baseline_info = cmd.get("baseline_image") or cmd.get("rhosd_kernel") or "N/A"
        prev = cmd.get("prev_status", "unknown")
        logger.info(f"  {i}. {cmd['jira_id']}  |  baseline: {baseline_info}  |  prev: {prev}")

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
        logger.error("Failed to populate OCPVirt credentials. Aborting execution.")
        return

    baselines = load_baseline_images(logger)

    logger.info("Starting command execution...")

    success_count = 0
    failed_count = 0

    for i, exec_cmd in enumerate(executable_commands, 1):
        logger.info("")
        logger.info(f"{'=' * 80}")
        logger.info(f"Executing {i}/{len(executable_commands)}: {exec_cmd['jira_id']}")
        logger.info(f"{'=' * 80}")

        jira_id = exec_cmd["jira_id"]
        baseline_info = baselines.get(jira_id, {})
        apply_baseline_to_cephci(exec_cmd, baseline_info, logger)

        success, stdout, stderr = executor.execute_command(
            jira_id=exec_cmd["jira_id"],
            command=exec_cmd["command"],
            baseline_image=exec_cmd.get("baseline_image") or exec_cmd.get("rhosd_kernel"),
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
    Create a formatted table with JIRA issues, commands, validation, and baselines.

    Args:
        jira_issues: List of JIRA issue dictionaries
        commands: Optional list of command dictionaries
        validations: Optional list of validation result dictionaries
        rhosd_kernels: Optional dict mapping JIRA IDs to baseline datasource / RHEL key

    Returns:
        String containing the formatted table
    """
    lines = []
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines.append("=" * 450)
    lines.append("KERNEL BZ POLLING - JIRA ISSUES, COMMANDS, AND VALIDATION STATUS")
    lines.append(f"Generated: {timestamp}")
    lines.append("=" * 450)
    lines.append("")

    command_map = {}
    validation_map = {}
    if commands:
        command_map = {cmd['jira_id']: cmd for cmd in commands}
    if validations:
        validation_map = {val['jira_id']: val for val in validations}

    command_map_by_jira = {}
    if commands:
        command_map_by_jira = {cmd['jira_id']: cmd for cmd in commands}

    if validations:
        header = f"{'#':<4} | {'JIRA ID':<15} | {'Fix Version':<20} | {'Inventory':<45} | {'Status':<15} | {'Repo':<6} | {'Valid':<7} | {'Repo Kernel':<50} | {'Baseline DS':<50} | {'QA Contact':<25} | {'CephCI Command'}"
    elif commands:
        header = f"{'#':<4} | {'JIRA ID':<15} | {'Fix Version':<20} | {'Inventory':<45} | {'Status':<15} | {'Repo':<6} | {'Repo Kernel':<50} | {'Baseline DS':<50} | {'QA Contact':<25} | {'CephCI Command'}"
    else:
        header = f"{'#':<4} | {'JIRA ID':<15} | {'Fix Version':<20} | {'Inventory':<45} | {'Status':<15} | {'Repo':<6} | {'Repo Kernel':<50} | {'Baseline DS':<50} | {'QA Contact':<25} | {'Title':<80}"

    lines.append(header)
    lines.append("-" * 450)

    for idx, issue in enumerate(jira_issues, 1):
        jira_id = issue.get('jira_id', 'N/A')

        fix_versions = issue.get('fix_versions', [])
        fix_version = fix_versions[0][:19] if fix_versions else 'N/A'

        inventory = 'N/A'
        if jira_id in command_map_by_jira:
            inv = command_map_by_jira[jira_id].get('inventory') or 'N/A'
            inventory = inv.split('/')[-1][:44] if inv != 'N/A' else 'N/A'

        status = issue.get('status', 'N/A')[:14]
        repo = '✓' if issue.get('repo_url') else '✗'
        repo_kernel = (issue.get('repo_kernel_version') or 'N/A')[:49]
        qa = (issue.get('qa_contact') or 'N/A')[:24]

        baseline_ds = 'N/A'
        if rhosd_kernels and jira_id in rhosd_kernels:
            baseline_ds = str(rhosd_kernels[jira_id])[:49]

        valid_status = ''
        if validations and jira_id in validation_map:
            val = validation_map[jira_id]
            valid_status = '✓ PASS' if val.get('all_valid', False) else '✗ FAIL'

        if commands and jira_id in command_map:
            cmd = command_map[jira_id]['command']
            if validations:
                row = f"{idx:<4} | {jira_id:<15} | {fix_version:<20} | {inventory:<45} | {status:<15} | {repo:<6} | {valid_status:<7} | {repo_kernel:<50} | {baseline_ds:<50} | {qa:<25} | {cmd}"
            else:
                row = f"{idx:<4} | {jira_id:<15} | {fix_version:<20} | {inventory:<45} | {status:<15} | {repo:<6} | {repo_kernel:<50} | {baseline_ds:<50} | {qa:<25} | {cmd}"
        else:
            title = (issue.get('jira_title') or 'N/A')[:79]
            row = f"{idx:<4} | {jira_id:<15} | {fix_version:<20} | {inventory:<45} | {status:<15} | {repo:<6} | {repo_kernel:<50} | {baseline_ds:<50} | {qa:<25} | {title:<80}"

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
        logger.info(f"JIRA Server: {config.get('jira.server')}")
        logger.info(f"CephCI Repo: {config.get('cephci.repo_url')}")
        logger.info(f"CephCI Branch: {config.get('cephci.branch')}")
        logger.info(f"Log Level: {log_level}")
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

        # --execute-only: skip Phases 1-7, jump straight to execution
        if args.execute_only:
            run_execute_only(args, logger)
            return
        
        # Phase 1 - JIRA Integration: Fetch Real Issues
        logger.info("=" * 60)
        logger.info("Phase 1: JIRA Integration - Fetching Issues")
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
            
            logger.info("Phase 1: JIRA Integration - COMPLETED ✅")
            
        except Exception as e:
            logger.error(f"Phase 1 error: {e}", exc_info=True)
            logger.warning("Phase 1: JIRA Integration - Failed to fetch issues")
        
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

        # Phase 2 - Pre-validation: skip JIRAs already passed in dashboard
        logger.info("=" * 60)
        logger.info("Phase 2: Pre-Validation — Skip Already-Passed JIRAs")
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

            logger.info("Phase 2: Pre-Validation - COMPLETED ✅")
        except Exception as e:
            logger.warning(f"Phase 2 error (non-fatal, continuing): {e}")

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
        
        # Phase 4 - Inventory File Validation
        logger.info("=" * 60)
        logger.info("Phase 4: Inventory File Validation")
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

                # Create osp-cred before validation (seed from example + inject .env)
                from src.cephci.command_executor import CephCIExecutor
                early_executor = CephCIExecutor(skip_dashboard=True)
                if early_executor.populate_osp_credentials():
                    logger.info("✅ osp-cred-ocpvirt.yaml ready for validation")
                else:
                    logger.warning(
                        "⚠️  Failed to populate osp-cred-ocpvirt.yaml; "
                        "validation may report it missing"
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
            
            logger.info("Phase 4: Inventory File Validation - COMPLETED ✅")
            
        except Exception as e:
            logger.error(f"Phase 4 error: {e}", exc_info=True)
            logger.warning("Phase 4: Inventory File Validation - Failed")
        
        # Phase 5 - Generate Missing Inventory Files
        logger.info("=" * 60)
        logger.info("Phase 5: Generate Missing Inventory Files")
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
            
            logger.info("Phase 5: Generate Missing Inventory Files - COMPLETED ✅")
            
        except Exception as e:
            logger.error(f"Phase 5 error: {e}", exc_info=True)
            logger.warning("Phase 5: Generate Missing Inventory Files - Failed")
        
        logger.info("=" * 60)
        
        logger.info("=" * 60)
        
        # Phase 6 - Parse Inventory Files for Image Info
        logger.info("=" * 60)
        logger.info("Phase 6: Inventory Image Information Extraction")
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
                        logger.info(f"     VM Size: {image_info.get('vm_size') or 'N/A (ocpvirt)'}")
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
            
            logger.info("Phase 6: Inventory Image Information Extraction - COMPLETED ✅")
            
        except Exception as e:
            logger.error(f"Phase 6 error: {e}", exc_info=True)
            logger.warning("Phase 6: Inventory Image Information Extraction - Failed")
        
        logger.info("=" * 60)
        
        # Phase 7 - Map RHEL fix versions to OCPVirt datasources
        logger.info("=" * 60)
        logger.info("Phase 7: OCPVirt Baseline Datasource Mapping")
        logger.info("=" * 60)

        rhosd_kernel_map = {}
        rhosd_jira_baselines = {}
        try:
            from src.ocpvirt.baseline import BaselineMapper

            if commands and jira_issues:
                jira_by_id = {issue.get("jira_id"): issue for issue in jira_issues}
                mapper = BaselineMapper()
                rhosd_jira_baselines = mapper.build_baselines_for_commands(
                    commands, jira_by_id=jira_by_id
                )
                for jid, rec in rhosd_jira_baselines.items():
                    # Prefer real compose kernel NVR for the summary table
                    rhosd_kernel_map[jid] = rec.get("baseline_kernel") or rec.get(
                        "baseline_image", "N/A"
                    )

                os.makedirs("data", exist_ok=True)
                with open(BASELINE_IMAGES_FILE, "w") as f:
                    json.dump(rhosd_jira_baselines, f, indent=2)
                logger.info(f"📄 Per-JIRA baseline data saved to: {BASELINE_IMAGES_FILE}")

                if commands and validations:
                    logger.info("Regenerating table with baseline datasources...")
                    table_content = create_jira_table(
                        jira_issues, commands, validations, rhosd_kernel_map
                    )
                    write_table_to_file("data/jira_issues_table.txt", table_content)
                    logger.info(
                        "📄 Updated table with baselines saved to: data/jira_issues_table.txt"
                    )
            else:
                logger.warning("⚠️  No commands/JIRAs available for baseline mapping")

            logger.info("Phase 7: OCPVirt Baseline Datasource Mapping - COMPLETED ✅")

        except Exception as e:
            logger.error(f"Phase 7 error: {e}", exc_info=True)
            logger.warning("Phase 7: OCPVirt Baseline Datasource Mapping - Failed")

        logger.info("=" * 60)

        # Phase 8 - Execute JIRAs that have a mapped baseline datasource
        logger.info("=" * 60)
        logger.info("Phase 8: Command Execution")
        logger.info("=" * 60)

        try:
            from src.cephci.command_executor import CephCIExecutor

            executor = CephCIExecutor(skip_dashboard=args.skip_dashboard)
            logger.info("✅ CephCI Executor initialized")

            if commands and rhosd_jira_baselines:
                vtype_map = {
                    issue.get("jira_id"): issue.get("verification_type", "N/A")
                    for issue in jira_issues
                }
                jira_kernel_map = {
                    issue.get("jira_id"): issue.get("repo_kernel_version", "N/A")
                    for issue in jira_issues
                }
                fix_version_map = {}
                for issue in jira_issues:
                    fv = issue.get("fix_versions", [])
                    fix_version_map[issue.get("jira_id")] = fv[0] if fv else "N/A"

                def _platform_from_cmd(cmd_str):
                    if cmd_str and "--platform " in cmd_str:
                        return cmd_str.split("--platform ")[1].split(" ")[0].strip("'\"")
                    return None

                executable_commands = []
                skipped_commands = []
                for cmd in commands:
                    jira_id = cmd.get("jira_id")
                    params = cmd.get("params", {})
                    inventory = params.get("--inventory") or cmd.get("inventory")
                    vtype = vtype_map.get(jira_id, "N/A")
                    jira_kernel = jira_kernel_map.get(jira_id, "N/A")
                    platform = _platform_from_cmd(cmd.get("command"))
                    baseline = rhosd_jira_baselines.get(jira_id)
                    fix_version = fix_version_map.get(jira_id, "N/A")

                    if baseline and baseline.get("baseline_image"):
                        executable_commands.append({
                            "jira_id": jira_id,
                            "command": cmd.get("command"),
                            "rhosd_kernel": baseline.get("baseline_kernel"),
                            "baseline_image": baseline["baseline_image"],
                            "guest_image_url": baseline.get("guest_image_url"),
                            "inventory": inventory,
                            "verification_type": vtype,
                            "jira_kernel": jira_kernel,
                            "fix_version": fix_version,
                            "platform": platform,
                            "is_rhel8": cmd.get("is_rhel8", False),
                            "global_conf": cmd.get("global_conf"),
                        })
                    else:
                        skipped_commands.append({
                            "jira_id": jira_id,
                            "inventory": inventory,
                            "verification_type": vtype,
                            "jira_kernel": jira_kernel,
                            "fix_version": fix_version,
                            "platform": platform,
                        })

                if skipped_commands:
                    logger.info(
                        f"Skipping {len(skipped_commands)} JIRAs with no OCPVirt datasource mapping:"
                    )
                    for sc in skipped_commands:
                        logger.info(
                            f"  - {sc['jira_id']} (verification: {sc['verification_type']})"
                        )
                        executor.execution_log[sc["jira_id"]] = {
                            "jira_id": sc["jira_id"],
                            "status": "skipped",
                            "reason": (
                                "No valid OCPVirt baseline "
                                "(unmapped RHEL, missing compose kernel, "
                                "or baseline kernel >= JIRA kernel)"
                            ),
                            "inventory": sc.get("inventory"),
                            "platform": sc.get("platform"),
                            "jira_kernel": sc.get("jira_kernel"),
                            "fix_version": sc.get("fix_version"),
                            "verification_type": sc["verification_type"],
                            "start_time": datetime.now().isoformat(),
                        }
                    executor._save_execution_log()

                if executable_commands:
                    logger.info(
                        f"Found {len(executable_commands)} JIRAs with OCPVirt baselines ready for execution"
                    )
                    logger.info("")
                    logger.info("JIRAs to execute:")
                    for i, exec_cmd in enumerate(executable_commands, 1):
                        logger.info(
                            f"  {i}. {exec_cmd['jira_id']} - Baseline: "
                            f"{exec_cmd.get('baseline_image', 'N/A')}"
                        )

                    logger.info("")
                    logger.info("=" * 60)
                    logger.info("⚠️  EXECUTION MODE")
                    logger.info("=" * 60)
                    logger.info("Commands will be executed ONE BY ONE in the cephci repository")
                    logger.info("Each command may take 30-60 minutes to complete")
                    logger.info("")

                    if args.dry_run:
                        logger.info("DRY RUN - showing commands without executing:")
                        for exec_cmd in executable_commands:
                            logger.info(f"  [{exec_cmd['jira_id']}] {exec_cmd['command']}")
                        logger.info("DRY RUN complete. No commands were executed.")
                        response = "no"
                    elif args.yes:
                        logger.info("Auto-confirmed via --yes flag")
                        response = "yes"
                    else:
                        response = input(
                            "Do you want to execute these commands? (yes/no): "
                        ).strip().lower()

                    if response == "yes":
                        if not executor.install_dependencies():
                            logger.error(
                                "Failed to install cephci dependencies. Aborting execution."
                            )
                            response = "no"

                    if response == "yes":
                        if not executor.populate_osp_credentials():
                            logger.error(
                                "Failed to populate OCPVirt credentials. Aborting execution."
                            )
                            response = "no"

                    if response == "yes":
                        logger.info("✅ Starting command execution...")

                        success_count = 0
                        failed_count = 0

                        for i, exec_cmd in enumerate(executable_commands, 1):
                            logger.info("")
                            logger.info(f"{'='*80}")
                            logger.info(
                                f"Executing {i}/{len(executable_commands)}: {exec_cmd['jira_id']}"
                            )
                            logger.info(f"{'='*80}")

                            baseline_info = rhosd_jira_baselines.get(
                                exec_cmd["jira_id"], {}
                            )
                            apply_baseline_to_cephci(exec_cmd, baseline_info, logger)

                            success, stdout, stderr = executor.execute_command(
                                jira_id=exec_cmd["jira_id"],
                                command=exec_cmd["command"],
                                baseline_image=exec_cmd.get("baseline_image"),
                                dry_run=False,
                                verification_type=exec_cmd.get("verification_type"),
                                jira_kernel=exec_cmd.get("jira_kernel"),
                                fix_version=exec_cmd.get("fix_version"),
                            )

                            if success:
                                success_count += 1
                                logger.info(
                                    f"✅ {exec_cmd['jira_id']} completed successfully"
                                )
                            else:
                                failed_count += 1
                                logger.error(f"❌ {exec_cmd['jira_id']} failed")

                            logger.info(
                                f"Progress: {i}/{len(executable_commands)} "
                                f"({success_count} success, {failed_count} failed)"
                            )

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
                    logger.warning(
                        "⚠️  No JIRAs with OCPVirt datasource mappings found for execution"
                    )
            else:
                logger.warning("⚠️  No commands or baseline mapping data available")

            logger.info("Phase 8: Command Execution - COMPLETED ✅")

        except Exception as e:
            logger.error(f"Phase 8 error: {e}", exc_info=True)
            logger.warning("Phase 8: Command Execution - Failed")
        
        logger.info("=" * 60)
        logger.info("All phases completed.")
        logger.info("=" * 60)

    except Exception as e:
        logger = get_logger(__name__)
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
