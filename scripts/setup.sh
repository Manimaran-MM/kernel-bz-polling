#!/bin/bash
# Setup script for Kernel BZ Polling System

set -e

echo "=========================================="
echo "Kernel BZ Polling System - Setup"
echo "=========================================="

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if running from project root
if [ ! -f "pyproject.toml" ]; then
    echo -e "${RED}Error: Please run this script from the project root directory${NC}"
    exit 1
fi

echo -e "${GREEN}✓${NC} Running from project root"

# Check Python version
echo ""
echo "Checking Python version..."
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
REQUIRED_VERSION="3.10"

if [ "$(printf '%s\n' "$REQUIRED_VERSION" "$PYTHON_VERSION" | sort -V | head -n1)" != "$REQUIRED_VERSION" ]; then
    echo -e "${RED}✗ Python 3.10 or higher is required. Found: $PYTHON_VERSION${NC}"
    exit 1
fi

echo -e "${GREEN}✓${NC} Python version: $PYTHON_VERSION"

# Create virtual environment
echo ""
echo "Creating virtual environment..."
if [ -d "venv" ]; then
    echo -e "${YELLOW}! Virtual environment already exists${NC}"
    read -p "Do you want to recreate it? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        rm -rf venv
        python3 -m venv venv
        echo -e "${GREEN}✓${NC} Virtual environment recreated"
    fi
else
    python3 -m venv venv
    echo -e "${GREEN}✓${NC} Virtual environment created"
fi

# Activate virtual environment
echo ""
echo "Activating virtual environment..."
source venv/bin/activate
echo -e "${GREEN}✓${NC} Virtual environment activated"

# Upgrade pip
echo ""
echo "Upgrading pip..."
pip install --upgrade pip > /dev/null 2>&1
echo -e "${GREEN}✓${NC} pip upgraded"

# Install dependencies
echo ""
echo "Installing dependencies..."
pip install -r requirements.txt > /dev/null 2>&1
echo -e "${GREEN}✓${NC} Production dependencies installed"

read -p "Install development dependencies? (y/N): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    pip install -r requirements-dev.txt > /dev/null 2>&1
    echo -e "${GREEN}✓${NC} Development dependencies installed"
fi

# Create .env file if it doesn't exist
echo ""
if [ ! -f ".env" ]; then
    echo "Creating .env file from template..."
    cp .env.example .env
    echo -e "${GREEN}✓${NC} .env file created"
    echo -e "${YELLOW}! Please edit .env file with your credentials${NC}"
else
    echo -e "${YELLOW}! .env file already exists${NC}"
fi

# Create data directories
echo ""
echo "Creating data directories..."
mkdir -p data/jira_cache
mkdir -p data/logs
mkdir -p data/generated_configs
echo -e "${GREEN}✓${NC} Data directories created"

# Check SSH key
echo ""
echo "Checking SSH configuration..."
if [ -f "$HOME/.ssh/id_rsa" ]; then
    echo -e "${GREEN}✓${NC} SSH key found at $HOME/.ssh/id_rsa"
else
    echo -e "${YELLOW}! SSH key not found at $HOME/.ssh/id_rsa${NC}"
    echo "  You may need to generate an SSH key or update SSH_KEY_PATH in .env"
fi

# Check Docker
echo ""
echo "Checking Docker installation..."
if command -v docker &> /dev/null; then
    DOCKER_VERSION=$(docker --version | awk '{print $3}' | sed 's/,//')
    echo -e "${GREEN}✓${NC} Docker installed: $DOCKER_VERSION"
    
    if command -v docker-compose &> /dev/null; then
        COMPOSE_VERSION=$(docker-compose --version | awk '{print $4}' | sed 's/,//')
        echo -e "${GREEN}✓${NC} Docker Compose installed: $COMPOSE_VERSION"
    else
        echo -e "${YELLOW}! Docker Compose not found${NC}"
    fi
else
    echo -e "${YELLOW}! Docker not found${NC}"
    echo "  Install Docker to use containerized deployment"
fi

# Summary
echo ""
echo "=========================================="
echo "Setup Complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "1. Edit .env file with your credentials:"
echo "   - JIRA_API_TOKEN"
echo "   - JIRA_EMAIL"
echo "   - OPENSTACK credentials"
echo ""
echo "2. Run locally:"
echo "   source venv/bin/activate"
echo "   python -m src.main"
echo ""
echo "3. Or run with Docker:"
echo "   cd docker"
echo "   docker-compose up -d"
echo ""
echo "4. View logs:"
echo "   tail -f data/logs/application.log"
echo ""
echo "=========================================="

# Made with Bob
