# System Architecture

## Overview

The Kernel BZ Polling System is a containerized Python application that automates kernel CephFS bug verification. It polls JIRA for tickets, resolves RHOS-D baseline images, builds and executes CephCI test commands, and publishes results to a GitHub Pages dashboard.

## High-Level Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                              VM Host                                     │
│                                                                          │
│  ┌─────────────────┐    ┌──────────────────────────────────────────────┐ │
│  │  systemd timer   │───>│           Podman Container                  │ │
│  │  (every 2 days)  │    │                                              │ │
│  └─────────────────┘    │  ┌──────────────────────────────────────┐    │ │
│                          │  │           src/main.py                │    │ │
│  Mounted from host:      │  │        (Phases 1-8 pipeline)        │    │ │
│  ┌───────────────┐       │  └──────┬───────┬───────┬───────┬──────┘    │ │
│  │ .env          │──────>│         │       │       │       │           │ │
│  │ ~/.ssh/id_rsa │──────>│    ┌────▼──┐ ┌──▼───┐ ┌▼────┐ ┌▼────────┐  │ │
│  │ ~/.cephci.yaml│──────>│    │ jira/ │ │cephci│ │rhosd│ │dashboard│  │ │
│  │ data/         │<─────>│    └───┬───┘ └──┬───┘ └──┬──┘ └────┬────┘  │ │
│  └───────────────┘       │        │        │        │          │       │ │
│                          └────────┼────────┼────────┼──────────┼───────┘ │
└───────────────────────────────────┼────────┼────────┼──────────┼─────────┘
                                    │        │        │          │
                         ┌──────────▼──┐ ┌───▼────┐ ┌─▼────────┐ ┌▼──────────┐
                         │   JIRA      │ │ CephCI │ │  RHOS-D  │ │  GitHub   │
                         │ Atlassian   │ │  Repo  │ │  Glance  │ │  Pages    │
                         │ (REST API)  │ │  (git) │ │  (REST)  │ │  (API)    │
                         └─────────────┘ └───┬────┘ └──────────┘ └───────────┘
                                             │
                                      ┌──────▼──────┐
                                      │   magna002   │
                                      │ (SCP logs)   │
                                      └──────────────┘
```

## Pipeline Workflow

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Container Starts                              │
│  entrypoint.sh: SSH setup → .cephci.yaml copy → CephCI clone/pull  │
│                 → Apply runtime patches                              │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Phase 1: JIRA Integration                                          │
│  Fetch issues via JQL query from JIRA Atlassian                     │
│  Output: list of JIRA issues with kernel versions, fix versions     │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼
              ┌────────────────────────────┐
              │  Filter: Exclusion list     │
              │  (config/exclusion_list.yaml)│
              │  Excluded JIRAs recorded    │
              └────────────┬───────────────┘
                           │
                           ▼
              ┌────────────────────────────┐
              │  Filter: --verification-   │
              │  type (pre/post) if given  │
              └────────────┬───────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Phase 2: Pre-Validation                                            │
│  Fetch dashboard history from GitHub Pages                          │
│  Skip JIRAs already passed (jira_id:verification_type match)        │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Phase 3: CephCI Command Builder                                    │
│  Build python3 run.py commands per JIRA                             │
│  Derive: platform, rhbuild, suite, conf, inventory, custom-config   │
│  RHEL-8: override platform to rhel-9, use RHEL-8 suite/conf        │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Phase 4: Inventory File Validation                                 │
│  Clone/update CephCI repo, re-apply patches                        │
│  Validate all referenced files exist (suite, conf, inventory)       │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Phase 5: Inventory File Generation                                 │
│  Generate any missing inventory YAML files from templates           │
│  Re-validate after generation                                       │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Phase 6: Image Information Extraction                              │
│  Parse inventory YAML files for image-name, vm-size, RHEL version   │
│  Output: image_info_map keyed by inventory path                     │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Phase 7: RHOS-D Kernel Resolution                                  │
│  Authenticate with RHOS-D via Keystone                              │
│  Search Glance for baseline images per JIRA                         │
│  Fetch kernel from compose URLs (with mirror fallback)              │
│  Select baseline: highest kernel older than JIRA kernel             │
│  RHEL-8: search RHEL-8 images despite RHEL-9 inventory             │
│  Output: per-JIRA baseline image + kernel                           │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Phase 8: Command Execution                                         │
│                                                                      │
│  For each JIRA:                                                      │
│  ┌───────────────────────────────────────────────────────────────┐   │
│  │ 1. Update baseline image in inventory (or global conf if     │   │
│  │    RHEL-8, updating node4+node5 openstack image-name)        │   │
│  │ 2. pip install -r requirements.txt (once)                    │   │
│  │ 3. Populate OSP credentials in osp/osp-cred-ci-2.yaml       │   │
│  │ 4. Execute: subprocess.run() with 4-hour timeout             │   │
│  │ 5. Save terminal log to data/logs/kernel-logs/<ID>_<type>/   │   │
│  │ 6. Run CephCI cleanup command                                │   │
│  │ 7. SCP logs to magna002                                      │   │
│  │ 8. Publish results to GitHub Pages dashboard                 │   │
│  └───────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  JIRAs with no RHOS-D kernel: recorded as status=skipped            │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼
                  ┌─────────────────┐
                  │ Container Exits  │
                  └─────────────────┘
```

## Phase Details

### Phase 1: JIRA Integration

Fetches JIRA issues matching the configured JQL query.

```
src/jira/client.py      — REST API client (requests-based, not jira package)
src/jira/query.py        — JQL query executor, loads queries from jira_queries.yaml
src/jira/parser.py       — Parses ADF content, extracts kernel versions, repo URLs, fix versions
```

**Input:** JQL query from `config/jira_queries.yaml`
**Output:** List of JIRA issue dicts with `jira_id`, `fix_versions`, `repo_kernel_version`, `repo_url`, `verification_type`, `status`, `qa_contact`

### Phase 2: Pre-Validation

Fetches `executions.json` from the GitHub Pages dashboard repo and skips JIRAs that already have `status: success` for the same `jira_id:verification_type` combination.

```
src/dashboard/publisher.py  — fetch_passed_jiras() downloads remote history
```

**Input:** JIRA issues list, remote dashboard history
**Output:** Filtered JIRA issues list (already-passed removed)

### Phase 3: CephCI Command Builder

Builds `python3 run.py` commands for each JIRA issue.

```
src/cephci/command_builder.py  — Builds command string with all parameters
```

**Key logic:**
- Derives `--platform` from fix versions (e.g., `rhel-9`, `rhel-10`)
- Maps RHEL version to `--rhbuild` (e.g., RHEL 9/10 -> `9.0`, RHEL 8 -> `7.1`)
- Selects suite and global conf based on verification type (`pre` / `post`)
- RHEL-8 JIRAs: overrides platform to `rhel-9`, uses RHEL-8 specific suite/conf files, hardcodes RHEL-9 inventory
- Constructs `--custom-config` with `pre=` or `post=` Brew RPM URLs
- Suffixes log directory with verification type (`<JIRA-ID>_pre` / `<JIRA-ID>_post`)

**Input:** JIRA issues list
**Output:** List of command dicts with `command`, `params`, `inventory`, `jira_id`, `is_rhel8`, `global_conf`

### Phase 4: Inventory File Validation

Validates that all CephCI files referenced in commands actually exist in the cloned repo.

```
src/cephci/repo_manager.py  — Clones/updates CephCI repo, re-applies patches after git pull
src/cephci/validator.py      — Checks existence of suite, conf, inventory files
```

**Input:** Commands list, CephCI repo
**Output:** Validation results per command

### Phase 5: Inventory File Generation

Generates any missing inventory YAML files based on RHEL version patterns.

```
src/cephci/inventory_generator.py  — Creates inventory files from templates
```

**Input:** Missing inventory paths from validation
**Output:** Generated inventory files in CephCI repo

### Phase 6: Image Information Extraction

Parses inventory YAML files to extract the current `image-name`, `vm-size`, and RHEL version.

```
src/cephci/inventory_parser.py  — Reads inventory YAML, extracts image metadata
```

**Input:** Inventory file paths
**Output:** `image_info_map` dict keyed by inventory path

### Phase 7: RHOS-D Kernel Resolution

Searches RHOS-D (OpenStack Glance) for baseline images whose kernel version is older than the JIRA's kernel, selecting the most recent match.

```
src/rhosd/image_api.py      — Direct Glance REST API calls, compose URL kernel fetching
src/rhosd/image_manager.py   — Image search and matching logic
```

**Key logic:**
- Authenticates with RHOS-D via Keystone
- Searches for images matching the inventory's image name pattern
- Fetches kernel version from each image's compose URL
- Selects the baseline image with the highest kernel version that is still older than the JIRA kernel
- For RHEL-8 JIRAs: searches using RHEL-8 image patterns despite the command using a RHEL-9 inventory
- Implements mirror fallback (original -> pnq mirror) for compose URL fetching
- Caches results but auto-removes `NO_KERNEL` entries on next run

**Input:** `image_info_map`, JIRA kernel versions
**Output:** `rhosd_jira_baselines` dict keyed by JIRA ID (baseline image name, kernel version, is_rhel8, global_conf)

### Phase 8: Command Execution

Executes CephCI commands one by one, with pre-execution setup and post-execution cleanup.

```
src/cephci/command_executor.py  — Subprocess execution, log capture, SCP, dashboard publish
```

**Per-JIRA execution flow:**

1. **Update baseline image:**
   - Non-RHEL-8: updates `image-name` in the inventory YAML
   - RHEL-8: updates `openstack` image-name in the global conf YAML (node4 + node5)
2. **Install CephCI dependencies:** `pip install -r requirements.txt` (once)
3. **Populate OSP credentials:** writes OpenStack creds to `osp/osp-cred-ci-2.yaml`
4. **Execute command:** `subprocess.run()` with 4-hour timeout
5. **Capture output:** terminal log saved to `data/logs/kernel-logs/<JIRA-ID>_<type>/`
6. **Run cleanup command** in CephCI repo
7. **Transfer logs:** SCP to `magna002:/ceph/ceph-qe-logs/.../`
8. **Publish dashboard:** updates `executions.json` and `index.html` on GitHub Pages

**Skipped JIRAs:** If RHOS-D returns no baseline kernel, the JIRA is recorded with `status: skipped` and `reason: No RHOS-D kernel available`.

## RHEL-8 Special Handling

RHEL-8 JIRAs follow a different path through the pipeline:

```
┌─────────────────────────────────────────────────────────────────────┐
│                    RHEL-8 JIRA Detected                              │
│                (fix_versions contains rhel-8.x)                      │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Command Builder Overrides:                                          │
│                                                                      │
│  --platform       → rhel-9  (not rhel-8)                            │
│  --rhbuild        → 7.1     (Ceph 7.1 for RHEL-8)                  │
│  --inventory      → conf/inventory/rhel-9.4-server-x86_64.yaml     │
│  --global-conf    → kernel_verification_rhel8_client.yaml           │
│  --suite          → kernel_pre/post_verification_rhel8_client.yaml  │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│  RHOS-D Search:                                                      │
│  Search for RHEL-8.x images (e.g., RHEL-8.8.0-x86_64-*)            │
│  even though the command uses RHEL-9 inventory                      │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Phase 8 Execution:                                                  │
│  Update global conf (not inventory)                                  │
│  Set node4.image-name.openstack = <baseline RHEL-8 image>          │
│  Set node5.image-name.openstack = <baseline RHEL-8 image>          │
└─────────────────────────────────────────────────────────────────────┘
```

**Key differences from standard flow:**
- Platform is forced to `rhel-9` (RHEL-8 tests run on RHEL-9 nodes)
- Uses RHEL-8 specific suite and global conf files
- RHOS-D searches for RHEL-8 images (not RHEL-9)
- Baseline image is written to the global conf (not the inventory)
- Ceph version is 7.1 (not 9.0)

## Module Structure

```
src/
├── main.py                         # Entry point, orchestrates Phases 1-8
├── jira/
│   ├── client.py                   # JIRA REST API client (requests-based)
│   ├── query.py                    # JQL query execution from jira_queries.yaml
│   └── parser.py                   # ADF content parser, field extraction
├── cephci/
│   ├── command_builder.py          # Builds CephCI run.py commands
│   ├── command_executor.py         # Executes commands, captures logs, SCP, dashboard
│   ├── inventory_generator.py      # Generates missing inventory YAML files
│   ├── inventory_parser.py         # Parses inventory files for image metadata
│   ├── repo_manager.py             # Git clone/pull, patch re-application
│   └── validator.py                # Validates CephCI file existence
├── rhosd/
│   ├── image_api.py                # RHOS-D Glance REST API, compose URL kernel fetch
│   └── image_manager.py            # Image search and baseline matching
├── dashboard/
│   └── publisher.py                # GitHub Pages HTML generation and publishing
└── utils/
    ├── config.py                   # ConfigManager (YAML + env var substitution)
    └── logger.py                   # Colored console + rotating file logging
```

## Data Flow

```
                 ┌──────────────┐
                 │   JIRA API   │
                 └──────┬───────┘
                        │
                        ▼
              ┌─────────────────────┐
              │ jira_issues_table.txt│     (Phase 1 output)
              └─────────┬───────────┘
                        │
                        ▼
          ┌──────────────────────────┐
          │ command_executions.json   │    (Phase 3-8 — commands + results)
          └──────────┬───────────────┘
                     │
        ┌────────────┼─────────────────┐
        │            │                 │
        ▼            ▼                 ▼
  ┌──────────┐  ┌─────────┐   ┌──────────────┐
  │  GitHub  │  │ magna002│   │ rhosd_kernel_│
  │  Pages   │  │  (logs) │   │ versions.json│
  └──────────┘  └─────────┘   └──────────────┘
```

**Runtime artifacts in `data/`:**
- `jira_issues_table.txt` — formatted table of JIRA issues (Phase 1)
- `command_executions.json` — execution records per JIRA (Phases 3-8)
- `rhosd_kernel_versions.json` — per-JIRA baseline data (Phase 7)
- `inventory_image_info.json` — parsed inventory metadata (Phase 6)
- `logs/kernel-logs/<JIRA-ID>_<type>/` — terminal output per execution

## Configuration Hierarchy

```
.env                          # Highest priority — secrets, credentials, paths
    ↓ (env var substitution)
config/config.yaml            # App settings — rhbuild mapping, suite/conf paths
config/jira_queries.yaml      # JQL query definitions
config/exclusion_list.yaml    # JIRA IDs to skip
    ↓ (fallback)
Hardcoded defaults            # Lowest priority — in ConfigManager and command_builder
```

All config files are baked into the container image at build time. The `.env` file is passed at runtime via `--env-file`.

## Container Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  Container Image (kernel-bz-polling:amd64)                          │
│                                                                      │
│  Base: Python 3.11 slim                                             │
│  Packages: git, openssh-client, curl, rsync, build-essential        │
│                                                                      │
│  /app/                                                               │
│  ├── src/                    # Application source code               │
│  ├── config/                 # Baked-in configuration files          │
│  ├── requirements.txt        # Python dependencies (installed)       │
│  └── docker/patches/         # Runtime CephCI patches               │
│                                                                      │
│  /entrypoint.sh              # Container startup script              │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
          Volume Mounts at Runtime:
          ─────────────────────────
          ./data              → /app/data         (read-write)
          ~/.ssh/id_rsa       → /root/.ssh/id_rsa (read-only)
          ~/.cephci.yaml      → /tmp/.cephci.yaml.in (read-only)

          Entrypoint Actions:
          ────────────────────
          1. Copy SSH key to writable /root/.ssh_rw/id_rsa
          2. Copy .cephci.yaml from /tmp/ to /root/
          3. Clone CephCI repo (or fetch + pull if exists)
          4. Apply runtime patches to cephci/compute/openstack.py
          5. Create data directories
          6. Execute: python3 -m src.main --yes
```

## Execution Modes

| Mode | Command | Description |
|------|---------|-------------|
| Full pipeline | `python3 -m src.main --yes` | Runs all 8 phases |
| Pre-verification only | `python3 -m src.main --yes --verification-type pre` | Filters to pre-verification JIRAs |
| Post-verification only | `python3 -m src.main --yes --verification-type post` | Filters to post-verification JIRAs |
| Dashboard publish | `python3 -m src.main --publish-dashboard` | Publishes from existing data, no tests |
| Dry run | `python3 -m src.main --dry-run` | Shows commands without executing |

## Dashboard

The dashboard is a static HTML page hosted on GitHub Pages (`index.html` + `executions.json`). It is updated after each test execution via the GitHub Contents API.

**Features:**
- Sortable/filterable table
- Clickable JIRA ID links
- Status badges: passed, failed, timeout, running, skipped, excluded
- Verification type badges: pre, post
- Summary cards with counts
- Field-level merge: updates only non-None fields, preserving historical data

## Scheduled Execution

A systemd timer on the VM triggers the container every other day:

```
systemd timer (OnCalendar=*-*-1/2 02:00:00)
    → systemd service
        → podman run ... kernel-bz-polling:amd64
            → entrypoint.sh → python3 -m src.main --yes
                → Phases 1-8
                    → container exits
```

Each run creates a date-stamped data directory (`runs/YYYY-MM-DD/`) for isolation.
