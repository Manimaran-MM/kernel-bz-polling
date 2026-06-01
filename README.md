# Kernel BZ Polling System

Automated JIRA polling and CephCI testing system for kernel BZ verification.

## 🎯 Overview

This system automates the process of:
- Polling JIRA for kernel CephFS bug reports
- Extracting test requirements from JIRA tickets
- Fetching appropriate RHOS-D images
- Building and executing CephCI test commands
- Managing test logs in centralized storage
- Providing a dashboard for monitoring test status

## 📋 Features

- **Automated JIRA Polling**: Daily queries for kernel CephFS integration tickets
- **Intelligent Parsing**: Extracts kernel versions, build info, and test requirements from JIRA
- **RHOS-D Integration**: Automatically fetches and matches OpenStack images
- **CephCI Execution**: Builds and runs CephCI commands with custom configurations
- **Log Management**: Transfers logs to centralized storage via SSH
- **Dashboard**: Real-time monitoring of test pipeline and results
- **Containerized**: Fully containerized with Docker for easy deployment
- **Flexible Branch Support**: Run CephCI tests from different branches

## 🏗️ Architecture

```
kernel-bz-polling/
├── src/                    # Source code
│   ├── jira/              # JIRA integration
│   ├── cephci/            # CephCI command builder & executor
│   ├── rhosd/             # RHOS-D image management
│   ├── storage/           # Log management
│   ├── scheduler/         # Job scheduling
│   ├── dashboard/         # Dashboard backend
│   └── utils/             # Utilities (config, logging)
├── config/                # Configuration files
├── docker/                # Docker configuration
├── dashboard/             # GitHub Pages dashboard
└── data/                  # Runtime data (gitignored)
```

## 🚀 Quick Start

### Prerequisites

- Docker and Docker Compose
- SSH access to log storage server (magna002.ceph.redhat.com)
- JIRA API token
- OpenStack credentials for RHOS-D

### Installation

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd kernel-bz-polling
   ```

2. **Create environment file**
   ```bash
   cp .env.example .env
   ```

3. **Edit `.env` with your credentials**
   ```bash
   # Required: JIRA credentials
   JIRA_SERVER=https://redhat.atlassian.net
   JIRA_API_TOKEN=your_token_here
   JIRA_EMAIL=your_email@redhat.com
   
   # Required: OpenStack credentials
   OPENSTACK_USERNAME=your_username
   OPENSTACK_PASSWORD=your_password
   
   # Required: SSH key path for log storage
   SSH_KEY_PATH=~/.ssh/id_rsa
   ```

4. **Build and run with Docker**
   ```bash
   cd docker
   docker-compose up -d
   ```

5. **Check logs**
   ```bash
   docker-compose logs -f kernel-bz-polling
   ```

### Local Development

1. **Create virtual environment**
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   pip install -r requirements-dev.txt  # For development
   ```

3. **Run the application**
   ```bash
   python -m src.main
   ```

## 📝 Configuration

### Main Configuration (`config/config.yaml`)

Configure application settings including:
- JIRA connection parameters
- CephCI repository and branch
- OpenStack credentials
- Log storage settings
- Scheduler settings
- Dashboard configuration

### JIRA Queries (`config/jira_queries.yaml`)

Define JQL queries and parsing rules:
- Query templates for different ticket types
- Field mappings
- Section identifiers for parsing JIRA descriptions
- Default values

### Environment Variables (`.env`)

Sensitive credentials and environment-specific settings.

## 🔧 Usage

### Running Tests

The system automatically polls JIRA daily and executes tests. You can also trigger manually:

```bash
# TODO: Manual trigger commands will be added in Phase 7
```

### Viewing Dashboard

Access the dashboard at: `http://localhost:5000`

The dashboard shows:
- Current polling status
- Jobs in pipeline
- Running tests
- Test results and log locations
- Historical statistics

### Accessing Logs

Logs are stored in two locations:
1. **Local**: `./data/logs/`
2. **Centralized**: `magna002.ceph.redhat.com:/ceph/ceph-qe-logs/manim/logs/kernel-logs/`

## 🧪 Testing

Run tests with pytest:

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=src --cov-report=html

# Run specific test file
pytest tests/test_jira/test_client.py
```

## 📊 Implementation Phases

### ✅ Phase 1: Project Foundation (COMPLETED)
- Directory structure
- Configuration management
- Logging infrastructure
- Docker containerization

### 🔄 Phase 2: JIRA Integration (Next)
- JIRA client implementation
- Query execution
- Content parsing

### 📅 Upcoming Phases
- Phase 3: CephCI Command Builder
- Phase 4: RHOS-D Image Management
- Phase 5: CephCI Executor
- Phase 6: Log Management
- Phase 7: Scheduler & Job Queue
- Phase 8: Dashboard Backend
- Phase 9: GitHub Pages Dashboard
- Phase 10: Integration & Testing

## 🤝 Contributing

1. Create a feature branch
2. Make your changes
3. Run tests and linting
4. Submit a pull request

### Code Quality

```bash
# Format code
black src/ tests/

# Lint code
flake8 src/ tests/
pylint src/

# Type checking
mypy src/
```

## 📚 Documentation

- [Architecture](docs/architecture.md) - System architecture and design
- [Setup Guide](docs/setup.md) - Detailed setup instructions
- [API Documentation](docs/api.md) - API reference

## 🐛 Troubleshooting

### Common Issues

1. **SSH Connection Failed**
   - Ensure SSH key is properly mounted in Docker
   - Verify SSH key has correct permissions (600)
   - Check if log storage host is accessible

2. **JIRA Authentication Failed**
   - Verify JIRA API token is valid
   - Check JIRA email is correct
   - Ensure token has required permissions

3. **OpenStack Connection Failed**
   - Verify OpenStack credentials
   - Check network connectivity to RHOS-D
   - Ensure project/tenant exists

## 📄 License

MIT License - See LICENSE file for details

## 👥 Authors

- Manimaran (manim@redhat.com)

## 🔗 Links

- JIRA: https://redhat.atlassian.net
- CephCI: https://github.com/Manimaran-MM/cephci
- RHOS-D: https://rhos-d.infra.prod.upshift.rdu2.redhat.com

---

**Status**: Phase 1 Complete - Ready for Phase 2 Implementation