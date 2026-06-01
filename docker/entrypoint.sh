#!/bin/bash
set -e

echo "=========================================="
echo "Kernel BZ Polling System - Starting"
echo "=========================================="

# ── SSH setup ──
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

    if [ -n "$LOG_STORAGE_HOST" ]; then
        echo "Adding $LOG_STORAGE_HOST to known_hosts..."
        ssh-keyscan -H "$LOG_STORAGE_HOST" >> /root/.ssh_rw/known_hosts 2>/dev/null || true
        export SSH_KNOWN_HOSTS=/root/.ssh_rw/known_hosts
    fi

    export SSH_KEY_PATH=/root/.ssh_rw/id_rsa
    echo "SSH key configured at /root/.ssh_rw/id_rsa"
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

    # Apply patches to cephci
    PATCH_DIR="/app/docker/patches"
    if [ -d "$PATCH_DIR" ] && ls "$PATCH_DIR"/*.patch 1>/dev/null 2>&1; then
        echo "Applying patches to CephCI..."
        cd "$CEPHCI_DIR"
        for patch_file in "$PATCH_DIR"/*.patch; do
            patch_name=$(basename "$patch_file")
            if git apply --check "$patch_file" 2>/dev/null; then
                git apply "$patch_file"
                echo "  ✅ Applied: $patch_name"
            else
                echo "  ⚠️  Skipped (already applied or conflict): $patch_name"
            fi
        done
        cd /app
    fi
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
echo "  Log Storage  : ${LOG_STORAGE_HOST:-Not set}:${LOG_STORAGE_PATH:-Not set}"
echo "  Log Level    : ${LOG_LEVEL:-INFO}"
echo "=========================================="

echo "Launching: $@"
exec "$@"
