# Kernel BZ Polling System

Automated JIRA polling and CephCI testing system for kernel BZ verification.

## Overview

This system automates the process of:
- Polling JIRA for kernel CephFS bug reports
- Extracting test requirements (pre/post verification) from JIRA tickets
- Fetching appropriate RHOS-D baseline images and kernels
- Building and executing CephCI test commands (including RHEL-8 specific handling)
- Managing test logs via SCP to centralized storage
- Publishing results to a GitHub Pages dashboard

## Workflow

The pipeline runs in 8 sequential phases:

```
Phase 1: JIRA Integration           — Fetch issues from JIRA via JQL query
Phase 2: Pre-Validation             — Skip JIRAs already passed in dashboard
Phase 3: CephCI Command Builder     — Build CephCI run.py commands per JIRA
Phase 4: Inventory File Validation  — Validate required CephCI files exist
Phase 5: Inventory File Generation  — Generate missing inventory files
Phase 6: Image Information Extract  — Parse inventory files for RHOS-D image info
Phase 7: RHOS-D Kernel Resolution   — Find baseline images/kernels from RHOS-D
Phase 8: Command Execution          — Execute CephCI commands, publish dashboard
```

## Project Structure

```
kernel-bz-polling/
├── src/
│   ├── main.py               # Entry point — orchestrates Phases 1-8
│   ├── jira/                  # JIRA REST API client and parser
│   ├── cephci/                # Command builder, executor, inventory, repo manager
│   ├── rhosd/                 # RHOS-D Glance image API and kernel resolution
│   ├── dashboard/             # GitHub Pages dashboard publisher
│   └── utils/                 # Config manager, logging
├── config/
│   ├── config.yaml            # App settings (rhbuild, suites, paths)
│   ├── jira_queries.yaml      # JQL query definitions
│   └── exclusion_list.yaml    # JIRAs to skip from execution
├── docker/
│   ├── Dockerfile             # Container image definition
│   ├── entrypoint.sh          # Container startup (SSH, CephCI clone, patches)
│   └── patches/               # Runtime CephCI patches
├── systemd/
│   ├── kernel-bz-polling.service  # Systemd service unit for podman run
│   ├── kernel-bz-polling.timer    # Timer unit (alternate-day schedule)
│   └── README.md                  # VM setup instructions
├── .env.example               # Environment variable template
├── requirements.txt           # Python dependencies
└── data/                      # Runtime data — logs, caches, configs (gitignored)
```

## Prerequisites

- Podman (or Docker for building)
- SSH key for remote log storage (`~/.ssh/id_rsa`)
- CephCI config file (`~/.cephci.yaml`)
- JIRA API token
- OpenStack credentials for RHOS-D
- GitHub token for dashboard publishing

## Setup

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd kernel-bz-polling
   ```

2. **Create environment file**
   ```bash
   cp .env.example .env
   # Edit .env with your credentials
   ```

3. **Build the container image**
   ```bash
   docker build --platform linux/amd64 -t kernel-bz-polling:amd64 -f docker/Dockerfile .
   ```

4. **Transfer to the VM** (if building on a different machine)
   ```bash
   docker save kernel-bz-polling:amd64 | gzip > kernel-bz-polling-amd64.tar.gz
   scp kernel-bz-polling-amd64.tar.gz user@vm-host:~/
   ```
   On the VM:
   ```bash
   podman load -i kernel-bz-polling-amd64.tar.gz
   ```

## Running

Ensure the `.env` file exists in the current directory with your credentials before running. See `.env.example` for the template.

### Full pipeline (Phases 1-8)

```bash
podman run -d --name kernel-run \
  --env-file .env \
  -v ./data:/app/data:Z \
  -v ~/.ssh/id_rsa:/root/.ssh/id_rsa:ro,Z \
  -v ~/.cephci.yaml:/tmp/.cephci.yaml.in:ro,Z \
  kernel-bz-polling:amd64 \
  python3 -m src.main --yes
```

### Filter by verification type

```bash
# Run only pre-verification JIRAs
podman run -d --name pre-kernel-run \
  --env-file .env \
  -v ./data:/app/data:Z \
  -v ~/.ssh/id_rsa:/root/.ssh/id_rsa:ro,Z \
  -v ~/.cephci.yaml:/tmp/.cephci.yaml.in:ro,Z \
  kernel-bz-polling:amd64 \
  python3 -m src.main --yes --verification-type pre

# Run only post-verification JIRAs
podman run -d --name post-kernel-run \
  --env-file .env \
  -v ./data:/app/data:Z \
  -v ~/.ssh/id_rsa:/root/.ssh/id_rsa:ro,Z \
  -v ~/.cephci.yaml:/tmp/.cephci.yaml.in:ro,Z \
  kernel-bz-polling:amd64 \
  python3 -m src.main --yes --verification-type post
```

### View container logs

```bash
podman logs -f kernel-run
```

## Scheduled Runs (Systemd Timer)

A systemd timer is provided to run the pipeline automatically every other day on a RHEL VM.
See [systemd/README.md](systemd/README.md) for setup instructions.

Each scheduled run creates a date-stamped data directory:
```
~/kernel-bz-polling/runs/
├── 2026-06-05/
├── 2026-06-07/
└── 2026-06-09/
```

## Configuration

All config files are baked into the container image at build time. To update, edit and rebuild.

- **`config/config.yaml`** — CephCI rhbuild mappings, kernel verification suite/conf paths, logging
- **`config/jira_queries.yaml`** — JQL query templates for polling JIRA
- **`config/exclusion_list.yaml`** — JIRA IDs to skip from execution (known failures), with reasons shown on the dashboard
- **`.env`** — Secrets and environment-specific settings (JIRA token, OpenStack credentials, SSH, GitHub token)

## Logs

- **Container console**: `podman logs -f <container-name>`
- **Per-JIRA terminal logs**: `data/logs/kernel-logs/<JIRA-ID>_<pre|post>/`
- **Remote storage**: SCP'd to `magna002:/ceph/ceph-qe-logs/manim/logs/kernel-logs/`

## Dashboard

Results are published to GitHub Pages after each test execution:
- Sortable/filterable table of all JIRA executions
- Clickable JIRA ID links to the JIRA ticket
- Status indicators: passed, failed, running, excluded, skipped
- Fields: JIRA ID, Platform, Verification Type, JIRA Kernel, JIRA Fix Version, Status, Reason, Duration, Log Dir

## Documentation

- [Architecture and Workflow](docs/architecture.md) -- Detailed system architecture, phase descriptions, data flow diagrams, RHEL-8 handling, and module structure

## Author

Manimaran (manim@redhat.com)
