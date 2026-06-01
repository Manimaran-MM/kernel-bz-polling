# Phase 3: CephCI Command Builder

## Overview

Phase 3 implements the CephCI command builder that takes JIRA issue data and constructs properly formatted cephci commands with all necessary parameters.

## Features Implemented

### 1. Command Builder Module (`src/cephci/command_builder.py`)

**CephCICommandBuilder Class:**
- Builds cephci commands from JIRA data
- Supports custom branches
- Handles additional parameters
- Batch command building
- Formatted command printing

**Key Methods:**

```python
build_command(jira_data, branch=None, additional_params=None)
# Returns: {
#     'command': str,      # Full cephci command
#     'params': dict,      # Parameters used
#     'log_dir': str,      # Log directory path
#     'jira_id': str,      # JIRA issue ID
#     'branch': str        # Branch used
# }

build_commands_batch(jira_issues, branch=None)
# Build commands for multiple JIRA issues

print_command(command_data)
# Print formatted command with all details
```

### 2. Configuration Updates

Added to `config/config.yaml`:

```yaml
cephci:
  repo_url: "${CEPHCI_REPO_URL}"
  branch: "${CEPHCI_BRANCH}"
  default_branch: "master"
  local_path: "${CEPHCI_LOCAL_PATH}"
  repo_path: "/ceph/cephci"
  log_dir: "/ceph/cephci-jenkins"
  python_executable: "python3"
  default_params:
    log_level: "DEBUG"
    cloud: "ibmc"
    skip_sos_report: true
    store: true
  timeout: 14400  # 4 hours
  branches:
    - "master"
    - "rhel-8"
    - "rhel-9"
    - "pacific"
    - "quincy"
    - "reef"
```

### 3. Command Structure

Generated commands follow this format:

```bash
python3 run.py \
  --instances-name RHEL-142094 \
  --custom-config pre=http://download.eng.bos.redhat.com/rhel-9/.../os/ \
  --log-dir /ceph/ceph-qe-logs/manim/logs/kernel-logs/RHEL-142094 \
  --log-level DEBUG \
  --cloud openstack \
  --rhbuild 9.1
```

**Parameters Extracted from JIRA:**
- `--instances-name`: JIRA ID (e.g., RHEL-142094)
- `--custom-config pre=<url>`: Repo URL from "Testable Build" field
- `--log-dir`: Centralized log directory based on JIRA ID
- `--log-level`: Always DEBUG
- `--cloud`: Always openstack
- `--rhbuild`: Determined from fix versions (9.1 for RHEL 9/10, 5.3 for RHEL 8 and below)

**RHEL Version to rhbuild Mapping:**
- RHEL 10.x → rhbuild 9.1
- RHEL 9.x → rhbuild 9.1
- RHEL 8.x → rhbuild 5.3
- RHEL 7.x → rhbuild 5.3

This mapping is configurable in `config/config.yaml` under `cephci.rhbuild_mapping`.

**Additional Parameters (Optional):**
- `--suite`: Test suite path
- `--global-conf`: Global configuration file
- `--build`: Ceph build version
- Any other cephci parameters

### 4. Branch Support

The command builder supports multiple branches:
- Default branch from configuration
- Custom branch per command
- Supported branches: master, rhel-8, rhel-9, pacific, quincy, reef

## Usage Examples

### Example 1: Build Single Command

```python
from src.cephci.command_builder import CephCICommandBuilder

builder = CephCICommandBuilder()

jira_data = {
    'jira_id': 'RHEL-142094',
    'title': 'kernel-6.11.0 - cephfs',
    'repo_url': 'http://download.eng.bos.redhat.com/rhel-9/.../os/',
    'status': 'VERIFIED'
}

# Build with default branch
command_data = builder.build_command(jira_data)
builder.print_command(command_data)
```

### Example 2: Custom Branch

```python
# Build with specific branch
command_data = builder.build_command(jira_data, branch='rhel-9')
```

### Example 3: Additional Parameters

```python
additional_params = {
    '--suite': 'suites/reef/cephfs/tier-0.yaml',
    '--global-conf': 'conf/reef/cephfs/tier-0.yaml',
    '--build': 'reef'
}

command_data = builder.build_command(
    jira_data,
    branch='reef',
    additional_params=additional_params
)
```

### Example 4: Batch Processing

```python
# Build commands for multiple issues
jira_issues = [
    {'jira_id': 'RHEL-142094', 'repo_url': 'http://...'},
    {'jira_id': 'RHEL-142095', 'repo_url': 'http://...'},
    {'jira_id': 'RHEL-142096', 'repo_url': 'http://...'}
]

commands = builder.build_commands_batch(jira_issues, branch='master')

for cmd_data in commands:
    builder.print_command(cmd_data)
```

## Testing

Run the Phase 3 test script:

```bash
python test_phase3.py
```

**Test Coverage:**
1. ✅ Single command building
2. ✅ Custom branch support
3. ✅ Additional parameters
4. ✅ Batch command building
5. ✅ Integration with real JIRA data

**Sample Output:**

```
================================================================================
CephCI Command for RHEL-142094
================================================================================

📋 Parameters:
   --instances-name: RHEL-142094
   --custom-config pre: http://download.eng.bos.redhat.com/rhel-9/.../os/
   --log-dir: /ceph/ceph-qe-logs/manim/logs/kernel-logs/RHEL-142094
   --log-level: DEBUG
   --cloud: openstack
   --rhbuild: 9.1

🚀 Command:
   python3 run.py --instances-name RHEL-142094 --custom-config pre=http://download.eng.bos.redhat.com/rhel-9/.../os/ --log-dir /ceph/ceph-qe-logs/manim/logs/kernel-logs/RHEL-142094 --log-level DEBUG --cloud openstack --rhbuild 9.1

📁 Log Directory:
   /ceph/ceph-qe-logs/manim/logs/kernel-logs/RHEL-142094

🌿 Branch:
   master
================================================================================
```

## Integration with Phase 2

The command builder seamlessly integrates with Phase 2 (JIRA Integration):

```python
from src.jira.query import JiraQueryExecutor
from src.cephci.command_builder import CephCICommandBuilder

# Fetch JIRA issues
jira_query = JiraQueryExecutor()
issues = jira_query.execute_query('kernel_cephfs')

# Build commands
builder = CephCICommandBuilder()
commands = builder.build_commands_batch(issues)

# Print all commands
for cmd_data in commands:
    builder.print_command(cmd_data)
```

## Log Directory Structure

Commands are configured to store logs in:

```
/ceph/ceph-qe-logs/manim/logs/kernel-logs/
├── RHEL-142094/
│   ├── run-<timestamp>/
│   │   ├── cephci.log
│   │   ├── test-results/
│   │   └── ...
├── RHEL-142095/
│   └── ...
└── RHEL-142096/
    └── ...
```

## Error Handling

The command builder includes robust error handling:

- **Missing Repo URL**: Logs warning, continues with command building
- **Invalid JIRA Data**: Logs error, skips issue in batch processing
- **Configuration Errors**: Falls back to defaults

## Next Steps (Phase 4)

Phase 4 will implement:
1. **Command Execution**: Run cephci commands
2. **Output Capture**: Capture stdout/stderr
3. **Process Management**: Handle long-running processes
4. **Status Tracking**: Monitor command execution status
5. **Error Recovery**: Handle failures and retries

## Files Modified/Created

### Created:
- `src/cephci/__init__.py`
- `src/cephci/command_builder.py`
- `test_phase3.py`
- `docs/PHASE3_CEPHCI_COMMAND_BUILDER.md`

### Modified:
- `config/config.yaml` (added cephci configuration)

## Dependencies

No new dependencies required. Uses existing:
- `src.utils.logger`
- `src.utils.config`
- `src.jira.query` (for integration testing)

## Configuration Required

Update `.env` file if needed:

```bash
# CephCI Configuration (optional, has defaults)
CEPHCI_REPO_URL=https://github.com/red-hat-storage/cephci.git
CEPHCI_BRANCH=master
CEPHCI_LOCAL_PATH=/ceph/cephci
```

## Summary

✅ **Phase 3 Complete!**

- Command builder module implemented
- Configuration updated
- Test script created
- Documentation complete
- Ready for Phase 4: Command Execution

The command builder successfully:
- Parses JIRA data
- Extracts Repo URLs
- Builds properly formatted cephci commands
- Supports multiple branches
- Handles batch processing
- Provides formatted output