# Setup Guide

Complete setup instructions for the Kernel BZ Polling System.

## Prerequisites

### Required
- **Python 3.10+** (3.11 recommended)
- **Git**
- **SSH access** to magna002.ceph.redhat.com
- **JIRA API Token** from https://redhat.atlassian.net
- **OpenStack credentials** for RHOS-D

### Optional
- **Docker** and **Docker Compose** (for containerized deployment)
- **Virtual environment** tool (venv, virtualenv, conda)

## Installation Methods

### Method 1: Automated Setup (Recommended)

```bash
# Clone the repository
git clone <repository-url>
cd kernel-bz-polling

# Run setup script
chmod +x scripts/setup.sh
./scripts/setup.sh

# Follow the prompts to:
# - Create virtual environment
# - Install dependencies
# - Create .env file
# - Set up data directories
```

### Method 2: Manual Setup

#### 1. Clone Repository
```bash
git clone <repository-url>
cd kernel-bz-polling
```

#### 2. Create Virtual Environment
```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

#### 3. Install Dependencies
```bash
pip install --upgrade pip
pip install -r requirements.txt

# For development
pip install -r requirements-dev.txt
```

#### 4. Configure Environment
```bash
cp .env.example .env
```

Edit `.env` with your credentials:
```bash
# JIRA Configuration
JIRA_SERVER=https://redhat.atlassian.net
JIRA_API_TOKEN=your_actual_token_here
JIRA_EMAIL=your_email@redhat.com

# OpenStack Configuration
OPENSTACK_USERNAME=your_username
OPENSTACK_PASSWORD=your_password

# SSH Configuration
SSH_KEY_PATH=/path/to/your/ssh/key
```

#### 5. Create Data Directories
```bash
mkdir -p data/jira_cache
mkdir -p data/logs
mkdir -p data/generated_configs
```

### Method 3: Docker Setup

#### 1. Prerequisites
```bash
# Verify Docker installation
docker --version
docker-compose --version
```

#### 2. Configure Environment
```bash
cp .env.example .env
# Edit .env with your credentials
```

#### 3. Build and Run
```bash
cd docker
docker-compose up -d
```

#### 4. View Logs
```bash
docker-compose logs -f kernel-bz-polling
```

#### 5. Stop Container
```bash
docker-compose down
```

## Configuration

### JIRA API Token

1. Go to https://id.atlassian.com/manage-profile/security/api-tokens
2. Click "Create API token"
3. Give it a name (e.g., "kernel-bz-polling")
4. Copy the token and add to `.env` file

### SSH Key Setup

1. **Generate SSH key** (if you don't have one):
   ```bash
   ssh-keygen -t rsa -b 4096 -C "your_email@redhat.com"
   ```

2. **Copy public key to log storage server**:
   ```bash
   ssh-copy-id magna002.ceph.redhat.com
   ```

3. **Test connection**:
   ```bash
   ssh magna002.ceph.redhat.com
   ```

4. **Update .env**:
   ```bash
   SSH_KEY_PATH=~/.ssh/id_rsa
   ```

### OpenStack Credentials

Your OpenStack credentials are already configured in `.env.example`:
```bash
OPENSTACK_AUTH_URL=https://rhos-d.infra.prod.upshift.rdu2.redhat.com:13000
OPENSTACK_USERNAME=73cf50f58d7a4ebf8c7d8dc9ac930483
OPENSTACK_PASSWORD=just_the_work
OPENSTACK_PROJECT_NAME=ceph-sys-test
```

Verify access:
```bash
# Install OpenStack CLI
pip install python-openstackclient

# Test connection
openstack --os-auth-url $OPENSTACK_AUTH_URL \
          --os-username $OPENSTACK_USERNAME \
          --os-password $OPENSTACK_PASSWORD \
          --os-project-name $OPENSTACK_PROJECT_NAME \
          image list
```

## Running the Application

### Local Development

```bash
# Activate virtual environment
source venv/bin/activate

# Run application
python -m src.main

# Or with custom log level
LOG_LEVEL=DEBUG python -m src.main
```

### Docker Deployment

```bash
cd docker
docker-compose up -d

# View logs
docker-compose logs -f

# Restart
docker-compose restart

# Stop
docker-compose down
```

### Background Execution

```bash
# Using nohup
nohup python -m src.main > app.log 2>&1 &

# Using screen
screen -S kernel-bz-polling
python -m src.main
# Press Ctrl+A, then D to detach

# Using systemd (create service file)
sudo systemctl start kernel-bz-polling
```

## Verification

### 1. Check Configuration Loading
```bash
python -c "from src.utils.config import get_config; c = get_config(); print(c.get('jira.server'))"
```

### 2. Check Logging
```bash
python -c "from src.utils.logger import get_logger; logger = get_logger('test'); logger.info('Test message')"
```

### 3. Check Data Directories
```bash
ls -la data/
```

### 4. Check Docker Container
```bash
docker ps | grep kernel-bz-polling
docker exec -it kernel-bz-polling python -c "from src.utils.config import get_config; print('OK')"
```

## Troubleshooting

### Issue: Import Errors

**Problem**: `ModuleNotFoundError: No module named 'src'`

**Solution**:
```bash
# Ensure you're in the project root
cd /path/to/kernel-bz-polling

# Run with module syntax
python -m src.main
```

### Issue: Configuration Not Found

**Problem**: `FileNotFoundError: Configuration file not found`

**Solution**:
```bash
# Check config directory exists
ls -la config/

# Verify CONFIG_DIR in .env
echo $CONFIG_DIR

# Set explicitly
export CONFIG_DIR=./config
```

### Issue: SSH Connection Failed

**Problem**: Cannot connect to log storage server

**Solution**:
```bash
# Test SSH connection
ssh magna002.ceph.redhat.com

# Check SSH key permissions
chmod 600 ~/.ssh/id_rsa

# Add to known_hosts
ssh-keyscan magna002.ceph.redhat.com >> ~/.ssh/known_hosts
```

### Issue: JIRA Authentication Failed

**Problem**: `401 Unauthorized` from JIRA

**Solution**:
1. Verify API token is correct
2. Check email address matches JIRA account
3. Ensure token hasn't expired
4. Generate new token if needed

### Issue: Docker Build Failed

**Problem**: Docker build errors

**Solution**:
```bash
# Clean Docker cache
docker system prune -a

# Rebuild without cache
docker-compose build --no-cache

# Check Dockerfile syntax
docker build -f docker/Dockerfile .
```

### Issue: Permission Denied on Scripts

**Problem**: Cannot execute setup.sh or entrypoint.sh

**Solution**:
```bash
chmod +x scripts/setup.sh
chmod +x docker/entrypoint.sh
```

## Development Setup

### Install Development Dependencies
```bash
pip install -r requirements-dev.txt
```

### Run Tests
```bash
# All tests
pytest

# With coverage
pytest --cov=src --cov-report=html

# Specific test file
pytest tests/test_utils/test_config.py
```

### Code Formatting
```bash
# Format code
black src/ tests/

# Check formatting
black --check src/ tests/
```

### Linting
```bash
# Flake8
flake8 src/ tests/

# Pylint
pylint src/

# Type checking
mypy src/
```

## Next Steps

After successful setup:

1. **Verify Configuration**: Ensure all credentials are correct
2. **Test JIRA Connection**: Will be implemented in Phase 2
3. **Test OpenStack Access**: Will be implemented in Phase 4
4. **Configure Scheduler**: Adjust polling time in `config/config.yaml`
5. **Monitor Logs**: Check `data/logs/application.log`

## Support

For issues or questions:
- Check [Architecture Documentation](architecture.md)
- Review [README.md](../README.md)
- Check application logs in `data/logs/`

## Security Notes

⚠️ **Important Security Practices**:

1. **Never commit `.env` file** - Contains sensitive credentials
2. **Protect SSH keys** - Keep private keys secure (chmod 600)
3. **Rotate API tokens** - Regularly update JIRA tokens
4. **Use application credentials** - For OpenStack (not user passwords)
5. **Secure log storage** - Ensure proper permissions on log server

## Maintenance

### Update Dependencies
```bash
pip install --upgrade -r requirements.txt
```

### Update Docker Image
```bash
docker-compose pull
docker-compose up -d
```

### Backup Configuration
```bash
# Backup .env (store securely)
cp .env .env.backup

# Backup data
tar -czf data-backup-$(date +%Y%m%d).tar.gz data/
```

### Clean Up
```bash
# Remove old logs
find data/logs -name "*.log.*" -mtime +30 -delete

# Clean JIRA cache
rm -rf data/jira_cache/*

# Clean Docker
docker system prune -a