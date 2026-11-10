#!/bin/bash
set -e

echo "=========================================="
echo "Kernel BZ Polling System - Starting"
echo "=========================================="

# ── SSH setup (optional guest access; log upload uses rclone → COS) ──
# The key is mounted read-only, so copy it to a writable location and fix perms
if [ -f /root/.ssh/id_rsa ]; then
    cp /root/.ssh/id_rsa /tmp/ssh_key
    chmod 600 /tmp/ssh_key
    mkdir -p /root/.ssh_rw
    mv /tmp/ssh_key /root/.ssh_rw/id_rsa

    # Point SSH to the writable copy
    cat > /root/.ssh/config 2>/dev/null <<SSHEOF || true
IdentityFile /root/.ssh_rw/id_rsa
StrictHostKeyChecking no
SSHEOF
    chmod 644 /root/.ssh/config 2>/dev/null || true

    echo "SSH key configured at /root/.ssh_rw/id_rsa"
fi

# ── .cephci.yaml setup ──
# Mount to /tmp/.cephci.yaml.in (read-only) and copy where CephCI looks.
# UBI python images set HOME=/opt/app-root/src even as root; CephCI resolves
# ~/.cephci.yaml via expanduser("~"), so install under $HOME (not only /root).
CEPHCI_CFG="${HOME:-/root}/.cephci.yaml"
install_cephci_cfg() {
    local src="$1"
    mkdir -p "$(dirname "$CEPHCI_CFG")"
    cp "$src" "$CEPHCI_CFG"
    chmod 644 "$CEPHCI_CFG"
    if [ "$CEPHCI_CFG" != "/root/.cephci.yaml" ]; then
        cp "$src" /root/.cephci.yaml
        chmod 644 /root/.cephci.yaml
    fi
}

if [ -f /tmp/.cephci.yaml.in ]; then
    install_cephci_cfg /tmp/.cephci.yaml.in
    echo ".cephci.yaml installed from mount -> $CEPHCI_CFG"
elif [ -f "$CEPHCI_CFG" ]; then
    echo ".cephci.yaml already present at $CEPHCI_CFG"
elif [ -f /root/.cephci.yaml ]; then
    install_cephci_cfg /root/.cephci.yaml
    echo ".cephci.yaml copied from /root -> $CEPHCI_CFG"
else
    echo "WARNING: No .cephci.yaml found (expected at $CEPHCI_CFG)"
fi

# ── CephCI repository ──
CEPHCI_DIR="${CEPHCI_LOCAL_PATH:-./cephci}"

if [ -n "$CEPHCI_REPO_URL" ]; then
    if [ ! -d "$CEPHCI_DIR/.git" ]; then
        echo "Cloning CephCI from $CEPHCI_REPO_URL ..."
        git clone "$CEPHCI_REPO_URL" "$CEPHCI_DIR"
    else
        echo "CephCI repo already present at $CEPHCI_DIR"
        cd "$CEPHCI_DIR"
        git fetch origin
        cd /app
    fi

    if [ -n "$CEPHCI_BRANCH" ]; then
        echo "Checking out branch: $CEPHCI_BRANCH"
        cd "$CEPHCI_DIR"
        git checkout "$CEPHCI_BRANCH" 2>/dev/null || git checkout -b "$CEPHCI_BRANCH" "origin/$CEPHCI_BRANCH"
        git pull origin "$CEPHCI_BRANCH" || true
        cd /app
    fi

    # Apply patches to cephci (OpenStack Glance patch skipped unless APPLY_OPENSTACK_PATCH=1)
    python3 /app/docker/patches/apply_patches.py "$CEPHCI_DIR"
fi

# ── Ensure data dirs ──
mkdir -p /app/data/logs/kernel-logs \
         /app/data/jira_cache \
         /app/data/generated_configs

# ── Show config summary ──
echo "=========================================="
echo "Configuration:"
echo "  JIRA Server  : ${JIRA_SERVER:-Not set}"
echo "  CephCI Repo  : ${CEPHCI_REPO_URL:-Not set}"
echo "  CephCI Branch: ${CEPHCI_BRANCH:-Not set}"
echo "  CephCI Path  : ${CEPHCI_LOCAL_PATH:-./cephci}"
echo "  Log Storage  : ${COS_REMOTE:-ceph-qe-cos}:${COS_BUCKET:-Not set}/${COS_PATH_PREFIX:-}"
echo "  Log Public   : ${LOG_PUBLIC_BASE_URL:-http://9.11.121.102/logs}"
echo "  Log Level    : ${LOG_LEVEL:-INFO}"
echo "=========================================="

echo "Launching: $@"
exec "$@"
