# Environment Variables Configuration

This document explains how environment variables are used to configure the CephCI repository management.

## CephCI Configuration

The system uses environment variables from `.env` file to configure the CephCI repository:

### CEPHCI_REPO_URL
**Purpose**: URL of the CephCI git repository to clone

**Examples**:
```bash
# Use the main Red Hat Storage repo
CEPHCI_REPO_URL=https://github.com/red-hat-storage/cephci.git

# Use your personal fork
CEPHCI_REPO_URL=https://github.com/Manimaran-MM/cephci

# Use a different organization's fork
CEPHCI_REPO_URL=https://github.com/your-org/cephci.git
```

**Default**: `https://github.com/red-hat-storage/cephci.git`

---

### CEPHCI_BRANCH
**Purpose**: Git branch to checkout when cloning/updating the repository

**Examples**:
```bash
# Use the main branch
CEPHCI_BRANCH=master

# Use a custom PR branch
CEPHCI_BRANCH=pr-kernel-bz

# Use a release branch
CEPHCI_BRANCH=rhel-9

# Use a feature branch
CEPHCI_BRANCH=feature/new-tests
```

**Default**: `master`

**Note**: The system will automatically:
1. Clone this branch on first run
2. Checkout and pull latest changes on subsequent runs
3. Switch branches if you change this value

---

### CEPHCI_LOCAL_PATH
**Purpose**: Local filesystem path where the CephCI repository will be cloned

**Examples**:
```bash
# Relative path (inside project directory)
CEPHCI_LOCAL_PATH=./cephci

# Absolute path (system-wide location)
CEPHCI_LOCAL_PATH=/opt/cephci

# User home directory
CEPHCI_LOCAL_PATH=~/cephci

# Custom location
CEPHCI_LOCAL_PATH=/data/repos/cephci
```

**Default**: `./cephci`

**Important Notes**:
- Relative paths are relative to the project root directory
- Absolute paths can be anywhere on the filesystem
- The directory will be created automatically if it doesn't exist
- Make sure you have write permissions to the specified location
- The path is automatically added to `.gitignore`

---

## How It Works

### 1. Configuration Loading
The system loads environment variables from `.env` file:

```python
# In src/cephci/repo_manager.py
self.repo_url = self.config.get('cephci.repo_url')      # From CEPHCI_REPO_URL
self.local_path = self.config.get('cephci.local_path')  # From CEPHCI_LOCAL_PATH
self.default_branch = self.config.get('cephci.branch')  # From CEPHCI_BRANCH
```

### 2. Repository Initialization
On first run or when repo doesn't exist:

```bash
# The system executes:
git clone --branch <CEPHCI_BRANCH> --depth 1 <CEPHCI_REPO_URL> <CEPHCI_LOCAL_PATH>
```

Example with your `.env` values:
```bash
git clone --branch pr-kernel-bz --depth 1 \
  https://github.com/Manimaran-MM/cephci \
  /opt/cephci
```

### 3. Repository Updates
On subsequent runs:

```bash
# The system executes:
cd <CEPHCI_LOCAL_PATH>
git fetch origin <CEPHCI_BRANCH>
git checkout <CEPHCI_BRANCH>
git pull origin <CEPHCI_BRANCH>
```

---

## Configuration Flow

```
.env file
    ↓
Environment Variables
    ↓
config/config.yaml (substitutes ${VAR})
    ↓
src/utils/config.py (loads and parses)
    ↓
src/cephci/repo_manager.py (uses values)
    ↓
Git Operations (clone/checkout/pull)
```

---

## Your Current Configuration

Based on your `.env` file:

```bash
CEPHCI_REPO_URL=https://github.com/Manimaran-MM/cephci
CEPHCI_BRANCH=pr-kernel-bz
CEPHCI_LOCAL_PATH=/opt/cephci
```

**What this means**:
- ✅ Will clone from your personal fork: `Manimaran-MM/cephci`
- ✅ Will use the `pr-kernel-bz` branch
- ✅ Will clone to `/opt/cephci` (absolute path)
- ✅ All inventory files will be validated from `/opt/cephci/conf/inventory/`
- ✅ All suite files will be validated from `/opt/cephci/suites/`

---

## Switching Configurations

### To use a different branch:
1. Edit `.env`:
   ```bash
   CEPHCI_BRANCH=master
   ```
2. Run the application - it will automatically checkout the new branch

### To use a different fork:
1. Edit `.env`:
   ```bash
   CEPHCI_REPO_URL=https://github.com/other-user/cephci
   ```
2. Delete the existing repo: `rm -rf /opt/cephci`
3. Run the application - it will clone the new repo

### To use a different location:
1. Edit `.env`:
   ```bash
   CEPHCI_LOCAL_PATH=./cephci
   ```
2. Run the application - it will clone to the new location
3. Old location can be manually deleted if needed

---

## Troubleshooting

### Permission Denied
If you get permission errors with `/opt/cephci`:
```bash
# Option 1: Use sudo to create directory with correct permissions
sudo mkdir -p /opt/cephci
sudo chown $USER:$USER /opt/cephci

# Option 2: Use a relative path instead
CEPHCI_LOCAL_PATH=./cephci
```

### Branch Not Found
If the branch doesn't exist in the repo:
```bash
# Check available branches
git ls-remote --heads <CEPHCI_REPO_URL>

# Update .env with a valid branch name
CEPHCI_BRANCH=master
```

### Clone Timeout
If cloning takes too long (>5 minutes):
```bash
# The system uses --depth 1 for shallow clone
# If still timing out, check network connectivity
# Or use a local mirror/cache
```

---

## Best Practices

1. **Use Relative Paths for Development**:
   ```bash
   CEPHCI_LOCAL_PATH=./cephci
   ```
   - Keeps everything in project directory
   - Easy to clean up
   - Works across different machines

2. **Use Absolute Paths for Production**:
   ```bash
   CEPHCI_LOCAL_PATH=/opt/cephci
   ```
   - Consistent location
   - Shared across multiple projects
   - Survives project directory moves

3. **Pin to Specific Branches**:
   ```bash
   CEPHCI_BRANCH=pr-kernel-bz
   ```
   - Ensures consistent behavior
   - Avoids unexpected changes from master
   - Good for testing specific features

4. **Keep .env in .gitignore**:
   - Never commit `.env` file
   - Use `.env.example` as template
   - Each developer/environment has own config

---

## Made with Bob