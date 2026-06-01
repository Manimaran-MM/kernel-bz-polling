"""
Configuration management module.
Loads configuration from YAML files and environment variables.
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from dotenv import load_dotenv


class ConfigManager:
    """Manages application configuration from YAML files and environment variables."""
    
    def __init__(self, config_dir: Optional[str] = None):
        """
        Initialize the configuration manager.
        
        Args:
            config_dir: Path to configuration directory. Defaults to ./config
        """
        # Load environment variables from .env file
        load_dotenv()
        
        # Set config directory
        if config_dir is None:
            config_dir = os.getenv('CONFIG_DIR', './config')
        self.config_dir = Path(config_dir)
        
        # Load configurations
        self._config: Dict[str, Any] = {}
        self._jira_queries: Dict[str, Any] = {}
        
        self._load_config()
        self._load_jira_queries()
    
    def _load_config(self) -> None:
        """Load main configuration from config.yaml."""
        config_file = self.config_dir / 'config.yaml'
        
        if not config_file.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_file}")
        
        with open(config_file, 'r') as f:
            raw_config = yaml.safe_load(f)
        
        # Substitute environment variables
        self._config = self._substitute_env_vars(raw_config)
    
    def _load_jira_queries(self) -> None:
        """Load JIRA queries from jira_queries.yaml."""
        queries_file = self.config_dir / 'jira_queries.yaml'
        
        if not queries_file.exists():
            raise FileNotFoundError(f"JIRA queries file not found: {queries_file}")
        
        with open(queries_file, 'r') as f:
            self._jira_queries = yaml.safe_load(f)
    
    def _substitute_env_vars(self, config: Any) -> Any:
        """
        Recursively substitute environment variables in configuration.
        
        Supports ${VAR_NAME} syntax.
        
        Args:
            config: Configuration dictionary or value
            
        Returns:
            Configuration with environment variables substituted
        """
        if isinstance(config, dict):
            return {k: self._substitute_env_vars(v) for k, v in config.items()}
        elif isinstance(config, list):
            return [self._substitute_env_vars(item) for item in config]
        elif isinstance(config, str):
            # Match ${VAR_NAME} pattern
            pattern = r'\$\{([^}]+)\}'
            matches = re.findall(pattern, config)
            
            result = config
            for var_name in matches:
                env_value = os.getenv(var_name, '')
                result = result.replace(f'${{{var_name}}}', env_value)
            
            # Try to convert to appropriate type
            if result.isdigit():
                return int(result)
            elif result.replace('.', '', 1).isdigit():
                return float(result)
            elif result.lower() in ('true', 'false'):
                return result.lower() == 'true'
            
            return result
        else:
            return config
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        Get configuration value by dot-notation key.
        
        Args:
            key: Configuration key in dot notation (e.g., 'jira.server')
            default: Default value if key not found
            
        Returns:
            Configuration value or default
        """
        keys = key.split('.')
        value = self._config
        
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        
        return value
    
    def get_jira_query(self, query_name: str) -> Optional[Dict[str, Any]]:
        """
        Get JIRA query configuration by name.
        
        Args:
            query_name: Name of the query
            
        Returns:
            Query configuration dictionary or None
        """
        return self._jira_queries.get('queries', {}).get(query_name)
    
    def get_jira_field_mappings(self) -> Dict[str, str]:
        """Get JIRA field mappings."""
        return self._jira_queries.get('field_mappings', {})
    
    def get_jira_section_identifiers(self) -> Dict[str, Any]:
        """Get JIRA section identifiers for parsing."""
        return self._jira_queries.get('section_identifiers', {})
    
    def get_jira_defaults(self) -> Dict[str, Any]:
        """Get default values for JIRA fields."""
        return self._jira_queries.get('defaults', {})
    
    def get_all_jira_queries(self) -> Dict[str, Any]:
        """Get all JIRA queries."""
        return self._jira_queries.get('queries', {})
    
    @property
    def config(self) -> Dict[str, Any]:
        """Get the entire configuration dictionary."""
        return self._config
    
    @property
    def jira_queries(self) -> Dict[str, Any]:
        """Get the entire JIRA queries configuration."""
        return self._jira_queries
    
    def reload(self) -> None:
        """Reload configuration from files."""
        self._load_config()
        self._load_jira_queries()


# Global configuration instance
_config_manager: Optional[ConfigManager] = None


def get_config() -> ConfigManager:
    """
    Get the global configuration manager instance.
    
    Returns:
        ConfigManager instance
    """
    global _config_manager
    
    if _config_manager is None:
        _config_manager = ConfigManager()
    
    return _config_manager


def reload_config() -> None:
    """Reload the global configuration."""
    global _config_manager
    
    if _config_manager is not None:
        _config_manager.reload()

# Made with Bob
