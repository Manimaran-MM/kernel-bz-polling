"""
Logging configuration module.
Provides structured logging with file and console handlers.
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

import colorlog


class LoggerManager:
    """Manages application logging configuration."""
    
    def __init__(
        self,
        name: str = "kernel-bz-polling",
        log_level: str = "INFO",
        log_dir: Optional[str] = None,
        log_file: str = "application.log",
        max_bytes: int = 10485760,  # 10MB
        backup_count: int = 5,
        console_output: bool = True,
        file_output: bool = True,
    ):
        """
        Initialize the logger manager.
        
        Args:
            name: Logger name
            log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
            log_dir: Directory for log files
            log_file: Log file name
            max_bytes: Maximum size of log file before rotation
            backup_count: Number of backup files to keep
            console_output: Enable console output
            file_output: Enable file output
        """
        self.name = name
        self.log_level = getattr(logging, log_level.upper(), logging.INFO)
        self.log_dir = Path(log_dir) if log_dir else Path("./data/logs")
        self.log_file = log_file
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self.console_output = console_output
        self.file_output = file_output
        
        # Create log directory if it doesn't exist
        if self.file_output:
            self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize logger
        self.logger = self._setup_logger()
    
    def _setup_logger(self) -> logging.Logger:
        """
        Set up and configure the logger.
        
        Returns:
            Configured logger instance
        """
        logger = logging.getLogger(self.name)
        logger.setLevel(self.log_level)
        
        # Remove existing handlers to avoid duplicates
        logger.handlers.clear()
        
        # Create formatters
        file_formatter = logging.Formatter(
            fmt='%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # Colored console formatter
        console_formatter = colorlog.ColoredFormatter(
            fmt='%(log_color)s%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
            log_colors={
                'DEBUG': 'cyan',
                'INFO': 'green',
                'WARNING': 'yellow',
                'ERROR': 'red',
                'CRITICAL': 'red,bg_white',
            }
        )
        
        # Add console handler
        if self.console_output:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(self.log_level)
            console_handler.setFormatter(console_formatter)
            logger.addHandler(console_handler)
        
        # Add file handler with rotation
        if self.file_output:
            log_file_path = self.log_dir / self.log_file
            file_handler = RotatingFileHandler(
                log_file_path,
                maxBytes=self.max_bytes,
                backupCount=self.backup_count
            )
            file_handler.setLevel(self.log_level)
            file_handler.setFormatter(file_formatter)
            logger.addHandler(file_handler)
        
        return logger
    
    def get_logger(self, module_name: Optional[str] = None) -> logging.Logger:
        """
        Get a logger instance for a specific module.
        
        Args:
            module_name: Name of the module requesting the logger
            
        Returns:
            Logger instance
        """
        if module_name:
            return logging.getLogger(f"{self.name}.{module_name}")
        return self.logger
    
    def set_level(self, level: str) -> None:
        """
        Change the logging level.
        
        Args:
            level: New logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        """
        new_level = getattr(logging, level.upper(), logging.INFO)
        self.logger.setLevel(new_level)
        
        for handler in self.logger.handlers:
            handler.setLevel(new_level)
    
    def add_file_handler(self, log_file: str, level: Optional[str] = None) -> None:
        """
        Add an additional file handler.
        
        Args:
            log_file: Name of the log file
            level: Logging level for this handler (optional)
        """
        log_file_path = self.log_dir / log_file
        
        file_handler = RotatingFileHandler(
            log_file_path,
            maxBytes=self.max_bytes,
            backupCount=self.backup_count
        )
        
        handler_level = getattr(logging, level.upper(), self.log_level) if level else self.log_level
        file_handler.setLevel(handler_level)
        
        file_formatter = logging.Formatter(
            fmt='%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_formatter)
        
        self.logger.addHandler(file_handler)


# Global logger manager instance
_logger_manager: Optional[LoggerManager] = None


def setup_logging(
    log_level: str = "INFO",
    log_dir: Optional[str] = None,
    console_output: bool = True,
    file_output: bool = True,
) -> LoggerManager:
    """
    Set up global logging configuration.
    
    Args:
        log_level: Logging level
        log_dir: Directory for log files
        console_output: Enable console output
        file_output: Enable file output
        
    Returns:
        LoggerManager instance
    """
    global _logger_manager
    
    if _logger_manager is None:
        _logger_manager = LoggerManager(
            log_level=log_level,
            log_dir=log_dir,
            console_output=console_output,
            file_output=file_output,
        )
    
    return _logger_manager


def get_logger(module_name: Optional[str] = None) -> logging.Logger:
    """
    Get a logger instance.
    
    Args:
        module_name: Name of the module requesting the logger
        
    Returns:
        Logger instance
    """
    global _logger_manager
    
    if _logger_manager is None:
        _logger_manager = setup_logging()
    
    return _logger_manager.get_logger(module_name)

# Made with Bob
