# System Architecture

## Overview

The Kernel BZ Polling System is a containerized Python application designed to automate the testing workflow for kernel CephFS bug reports tracked in JIRA.

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Kernel BZ Polling System                     │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────┐
        │         Daily Scheduler (APScheduler)    │
        │         - Polls JIRA at configured time  │
        │         - Manages job queue              │
        └─────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────┐
        │         JIRA Integration Module          │
        │         - Execute JQL queries            │
        │         - Parse ticket content           │
        │         - Extract test requirements      │
        └─────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────┐
        │         RHOS-D Image Manager             │
        │         - Fetch available images         │
        │         - Match requirements             │
        │         - Generate configurations        │
        └─────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────┐
        │         CephCI Command Builder           │
        │         - Build test commands            │
        │         - Handle branch selection        │
        │         - Generate config files          │
        └─────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────┐
        │         CephCI Executor                  │
        │         - Execute test commands          │
        │         - Monitor execution              │
        │         - Capture output                 │
        └─────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────┐
        │         Log Manager                      │
        │         - Collect test logs              │
        │         - Transfer via SSH               │
        │         - Organize by JIRA ID            │
        └─────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────┐
        │         Dashboard Backend (Flask)        │
        │         - Collect statistics             │
        │         - Provide REST API               │
        │         - Update GitHub Pages data       │
        └─────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────┐
        │         GitHub Pages Dashboard           │
        │         - Display statistics             │
        │         - Show job pipeline              │
        │         - Real-time status updates       │
        └─────────────────────────────────────────┘
```

## Component Details

### 1. Scheduler Module (`src/scheduler/`)

**Purpose**: Manages periodic JIRA polling and job execution

**Components**:
- `daily_poller.py`: Implements daily polling logic using APScheduler
- `job_queue.py`: Manages job pipeline and concurrent execution

**Key Features**:
- Configurable polling interval
- Job prioritization
- Concurrent job execution with limits
- Job status tracking

### 2. JIRA Integration Module (`src/jira/`)

**Purpose**: Interfaces with JIRA API to fetch and parse tickets

**Components**:
- `client.py`: JIRA API client with authentication
- `query.py`: JQL query execution
- `parser.py`: Parse ticket content and extract test requirements

**Key Features**:
- JQL query templates
- Regex-based content parsing
- Field mapping and validation
- Caching mechanism

### 3. RHOS-D Image Manager (`src/rhosd/`)

**Purpose**: Manages OpenStack image discovery and matching

**Components**:
- `image_fetcher.py`: Fetch images from RHOS-D OpenStack
- `image_matcher.py`: Match images to requirements
- `config_generator.py`: Generate inventory configurations

**Key Features**:
- OpenStack SDK integration
- Version matching algorithms
- Configuration file generation

### 4. CephCI Command Builder (`src/cephci/`)

**Purpose**: Constructs CephCI test commands from JIRA data

**Components**:
- `command_builder.py`: Build command strings
- `executor.py`: Execute commands and capture output
- `branch_manager.py`: Handle different branch checkouts

**Key Features**:
- Template-based command generation
- Parameter validation
- Branch switching support
- Custom configuration injection

### 5. Log Manager (`src/storage/`)

**Purpose**: Manages test log collection and storage

**Components**:
- `log_manager.py`: SSH-based log transfer
- `jira_cache.py`: Maintain JIRA ticket cache

**Key Features**:
- SSH/SCP file transfer
- Automatic directory creation
- Log organization by JIRA ID
- Transfer retry logic

### 6. Dashboard Backend (`src/dashboard/`)

**Purpose**: Provides API and data for the web dashboard

**Components**:
- `data_collector.py`: Collect system statistics
- `api.py`: Flask REST API endpoints

**Key Features**:
- RESTful API
- Real-time statistics
- Job status tracking
- Historical data retention

### 7. Utilities (`src/utils/`)

**Purpose**: Common utilities used across modules

**Components**:
- `config.py`: Configuration management
- `logger.py`: Structured logging
- `validators.py`: Input validation

## Data Flow

### 1. Polling Phase
```
Scheduler → JIRA Client → Query Execution → Parse Results → Cache
```

### 2. Test Preparation Phase
```
JIRA Data → Image Fetcher → Image Matcher → Config Generator → Command Builder
```

### 3. Execution Phase
```
Command Builder → CephCI Executor → Monitor → Capture Output
```

### 4. Post-Execution Phase
```
Executor → Log Manager → SSH Transfer → Update Dashboard → Notify
```

## Configuration Management

### Configuration Hierarchy
1. **Environment Variables** (`.env`) - Highest priority
2. **YAML Configuration** (`config/config.yaml`)
3. **Query Templates** (`config/jira_queries.yaml`)
4. **Default Values** - Lowest priority

### Configuration Loading
```python
ConfigManager → Load .env → Parse YAML → Substitute Variables → Validate
```

## Logging Strategy

### Log Levels
- **DEBUG**: Detailed diagnostic information
- **INFO**: General informational messages
- **WARNING**: Warning messages for potential issues
- **ERROR**: Error messages for failures
- **CRITICAL**: Critical failures requiring immediate attention

### Log Destinations
1. **Console**: Colored output for development
2. **File**: Rotating file handler for production
3. **Module-specific**: Separate logs for each module

## Security Considerations

### Credentials Management
- All sensitive data in `.env` file (gitignored)
- SSH keys stored securely with proper permissions
- API tokens never logged or exposed

### Network Security
- HTTPS for all external API calls
- SSH key-based authentication for log transfer
- OpenStack application credentials (not user passwords)

## Scalability

### Current Design
- Single container deployment
- Sequential job execution with concurrency limits
- Local caching for JIRA data

### Future Enhancements
- Multi-container deployment with job workers
- Distributed job queue (Celery + Redis)
- Centralized caching (Redis)
- Load balancing for dashboard

## Error Handling

### Retry Strategy
- Exponential backoff for API calls
- Configurable retry attempts
- Circuit breaker pattern for external services

### Failure Recovery
- Job state persistence
- Automatic retry of failed jobs
- Graceful degradation

## Monitoring

### Metrics Collected
- JIRA polling frequency and success rate
- Job execution time and status
- Log transfer success rate
- System resource usage

### Dashboard Metrics
- Total jobs processed
- Success/failure rates
- Average execution time
- Current pipeline status

## Deployment

### Container Architecture
```
Docker Container
├── Python 3.11 Runtime
├── Application Code
├── Configuration Files
├── Data Volumes (mounted)
│   ├── JIRA Cache
│   ├── Logs
│   └── Generated Configs
└── SSH Keys (mounted)
```

### Volume Mounts
- `/app/data`: Persistent data storage
- `/app/config`: Configuration files (read-only)
- `/root/.ssh`: SSH keys for log transfer
- `/opt/cephci`: CephCI repository

## Technology Stack

### Core
- **Python 3.11**: Primary language
- **Docker**: Containerization
- **Docker Compose**: Multi-container orchestration

### Libraries
- **JIRA**: JIRA API client
- **OpenStack SDK**: RHOS-D integration
- **Paramiko/SCP**: SSH file transfer
- **Flask**: Dashboard backend
- **APScheduler**: Job scheduling
- **GitPython**: Git operations
- **PyYAML**: Configuration parsing

### Development
- **pytest**: Testing framework
- **black**: Code formatting
- **flake8/pylint**: Linting
- **mypy**: Type checking

## Future Architecture Considerations

### Phase 2-10 Additions
- WebSocket support for real-time dashboard updates
- Database for persistent job history (SQLite/PostgreSQL)
- Message queue for distributed processing
- Prometheus metrics export
- Grafana dashboards
- Email/Slack notifications
- API authentication and rate limiting