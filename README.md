# Kernel BZ Polling System

Automated JIRA polling and CephCI testing system for kernel BZ verification on **OCP Virtualization** (`--cloud ocpvirt`).

## Overview

This system automates the process of:
- Polling JIRA for kernel CephFS bug reports
- Extracting test requirements (pre/post verification) from JIRA tickets
- Mapping RHEL fix versions to OCPVirt baseline datasources (`datasource://…`)
- Building and executing CephCI test commands (including RHEL-8 specific handling)
- Managing test logs via SCP to centralized storage
- Publishing results to a GitHub Pages dashboard

## Workflow

The pipeline runs in 8 sequential phases:

```
Phase 1: JIRA Integration           — Fetch issues from JIRA via JQL query
Phase 2: Pre-Validation             — Skip JIRAs already passed in dashboard
Phase 3: CephCI Command Builder     — Build run.py commands (--cloud ocpvirt)
Phase 4: Inventory File Validation  — Validate required CephCI files exist
Phase 5: Inventory File Generation  — Generate missing ocpvirt inventory files
Phase 6: Image Information Extract  — Parse inventory image-name (datasource://)
Phase 7: OCPVirt Baseline Mapping   — Static RHEL → datasource:// map; skip unmapped
Phase 8: Command Execution          — Apply baseline, run, cleanup, publish dashboard
```

## Project Structure

```
kernel-bz-polling/
├── src/
│   ├── main.py               # Entry point — orchestrates Phases 1-8
│   ├── jira/                  # JIRA REST API client and parser
│   ├── cephci/                # Command builder, executor, inventory, repo manager
│   ├── ocpvirt/               # Static RHEL → datasource baseline mapper
│   ├── rhosd/                 # Legacy RHOS-D Glance client (unused after cutover)
│   ├── dashboard/             # GitHub Pages dashboard publisher
│   └── utils/                 # Config manager, logging
├── config/
│   ├── config.yaml            # App settings (mappings, ocpvirt, suites)
│   ├── jira_queries.yaml      # JQL query definitions
│   └── exclusion_list.yaml    # JIRAs to skip from execution
├── docker/
│   ├── Dockerfile
│   ├── entrypoint.sh
│   └── patches/               # Optional OpenStack patches (disabled by default)
├── systemd/                   # Timer / service for scheduled runs
├── .env.example
├── requirements.txt
└── data/                      # Runtime data (gitignored)
```

## Prerequisites

- Podman (or Docker for building)
- SSH key for remote log storage and optional guest SSH
- CephCI config file (`~/.cephci.yaml`)
- JIRA API token
- **OCPVirt ServiceAccount token** (+ CA) for tenant `ceph-sys-test--runtime-int`
- GitHub token for dashboard publishing
- Network access to the OCP API (`conf/ocpvirt/rdu3_ceph_sys_test.yaml` → server URL)

## Setup

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd kernel-bz-polling
   ```

2. **Create environment file**
   ```bash
   cp .env.example .env
   # Edit .env: JIRA_*, OCPVIRT_TOKEN, OCPVIRT_CERTIFICATE_AUTHORITY_DATA, …
   ```

3. **Build the container image**
   ```bash
   docker build --platform linux/amd64 -t kernel-bz-polling:amd64 -f docker/Dockerfile .
   ```

## Running

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
python3 -m src.main --yes --verification-type pre
python3 -m src.main --yes --verification-type post
```

## Configuration

- **`config/config.yaml`**
  - `cephci.inventory_mapping` — RHEL → `conf/inventory/ocpvirt-rhel-*.yaml`
  - `cephci.datasource_mapping` — RHEL → `datasource://rhel94` etc.
  - `ocpvirt.namespace` — `rdu3_ceph_sys_test` (runtime ns `ceph-sys-test--runtime-int`)
  - `ocpvirt.profile` / `profile_post` — VirtualMachineClusterInstancetype
- **`.env`** — `OCPVIRT_TOKEN`, optional CA / SSH key path (API server comes from cephci `conf/ocpvirt/`)

Unmapped RHEL versions are **skipped** (no baseline datasource), same gate as the old “no RHOS-D kernel” path.

## Documentation

- [Architecture and Workflow](docs/architecture.md)

## Author

Manimaran (manim@redhat.com)
