# Systemd Timer — Scheduled Kernel BZ Polling

Runs the kernel-bz-polling container every other day at 2:00 AM on the RHEL VM.

## Files

- `kernel-bz-polling.service` — defines the podman run command (date-stamped data dirs)
- `kernel-bz-polling.timer` — triggers the service every alternate day

## Prerequisites

Before enabling the timer, ensure the following are in place on the VM:

### Required files under `~/kernel-bz-polling/`

```
~/kernel-bz-polling/
├── .env                    # Credentials: JIRA token, OCPVirt token/CA, GitHub token, SSH, etc.
```

> **Note:** The `config/` folder is baked into the container image at build time.
> To update config, rebuild the image.

### Required files under home directory

```
~/.ssh/id_rsa               # SSH private key (for SCP log transfer to magna002)
~/.cephci.yaml              # CephCI subscription manager config (used by cephci run.py)
```

### Container image

```bash
# Verify the image is loaded
podman images | grep kernel-bz-polling
# Should show: kernel-bz-polling   amd64   ...
```

### Network access

The container needs outbound access to:
- `redhat.atlassian.net` (JIRA API)
- OCP API server from `conf/ocpvirt/rdu3_ceph_sys_test.yaml` (e.g. `https://10.5.229.33:6443`)
- `api.github.com` (dashboard publishing)
- `magna002.ceph.redhat.com` (SCP log transfer)
- `github.com` (CephCI repo clone)

## One-Time Setup on the VM

### 1. Copy unit files

```bash
# As your regular user (user-level service)
mkdir -p ~/.config/systemd/user
cp systemd/kernel-bz-polling.service ~/.config/systemd/user/
cp systemd/kernel-bz-polling.timer   ~/.config/systemd/user/
```

### 2. Reload systemd and enable the timer

```bash
systemctl --user daemon-reload
systemctl --user enable --now kernel-bz-polling.timer
```

### 3. Verify

```bash
# Check the timer is active and see the next run time
systemctl --user status kernel-bz-polling.timer
systemctl --user list-timers
```

> **Note:** For user-level timers to run when you're not logged in, enable lingering:
> ```bash
> sudo loginctl enable-linger $USER
> ```

## Useful Commands

```bash
# Check timer status and next run
systemctl --user status kernel-bz-polling.timer
systemctl --user list-timers

# View logs from the last run
journalctl --user -u kernel-bz-polling.service

# Trigger a manual run right now
systemctl --user start kernel-bz-polling.service

# Disable the timer
systemctl --user disable --now kernel-bz-polling.timer
```

## Data Directory Structure

Each run creates a date-stamped directory under `runs/`:

```
kernel-bz-polling/
└── runs/
    ├── 2026-06-05/
    │   ├── command_executions.json
    │   ├── baseline_images.json
    │   ├── logs/
    │   └── ...
    ├── 2026-06-07/
    │   └── ...
    └── 2026-06-09/
        └── ...
```

This makes it easy to compare results across runs and avoids data from one run overwriting another.

## Customization

- **Schedule:** Edit `OnCalendar` in the `.timer` file. Examples:
  - `*-*-* 02:00:00` — daily at 2 AM
  - `*-*-1/2 02:00:00` — every other day at 2 AM (current)
  - `Mon,Wed,Fri 02:00:00` — specific days
- **Timeout:** Edit `TimeoutStartSec` in the `.service` file (default: 6h)
- **Working directory:** Edit `WorkingDirectory` to match where you cloned the repo on the VM
