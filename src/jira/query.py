"""
JIRA query executor for running JQL queries and processing results.
"""

import json
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime

from src.jira.client import JiraClient
from src.jira.parser import JiraParser
from src.utils.logger import get_logger
from src.utils.config import get_config


class JiraQueryExecutor:
    """Executes JIRA queries and processes results."""
    
    def __init__(self):
        """Initialize the query executor."""
        self.logger = get_logger(__name__)
        self.config = get_config()
        self.parser = JiraParser()
        
        # Get cache configuration
        self.cache_dir = Path(self.config.get('cache.jira_cache_dir', './data/jira_cache'))
        self.cache_enabled = self.config.get('cache.enable_cache', True)
        
        # Ensure cache directory exists
        if self.cache_enabled:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
    
    def execute_query(
        self,
        query_name: str,
        use_cache: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Execute a named JIRA query and return parsed results.
        
        Args:
            query_name: Name of the query from jira_queries.yaml
            use_cache: Whether to use cached results if available
            
        Returns:
            List of parsed JIRA issue dictionaries
        """
        self.logger.info(f"Executing JIRA query: {query_name}")
        
        # Get query configuration
        query_config = self.config.get_jira_query(query_name)
        if not query_config:
            self.logger.error(f"Query not found: {query_name}")
            return []
        
        # Check cache first
        if use_cache and self.cache_enabled:
            cached_results = self._load_from_cache(query_name)
            if cached_results:
                self.logger.info(f"Using cached results for query: {query_name}")
                return cached_results
        
        # Execute query
        jql = query_config.get('jql', '')
        max_results = query_config.get('max_results', 100)
        fields = query_config.get('fields', None)
        
        results = []
        
        try:
            with JiraClient() as client:
                # Search for issues
                issues = client.search_issues(
                    jql=jql,
                    max_results=max_results,
                    fields=fields
                )
                
                self.logger.info(f"Found {len(issues)} issues")
                
                # Parse each issue
                for issue in issues:
                    try:
                        parsed_issue = self._parse_issue_complete(client, issue)
                        results.append(parsed_issue)
                        
                    except Exception as e:
                        issue_key = issue.get('key', 'Unknown')
                        self.logger.error(f"Error parsing issue {issue_key}: {e}")
                        continue
                
                # Cache results
                if self.cache_enabled:
                    self._save_to_cache(query_name, results)
                
        except Exception as e:
            self.logger.error(f"Error executing query: {e}")
            return []
        
        self.logger.info(f"Successfully processed {len(results)} issues")
        return results
    
    def get_issue_details(
        self,
        issue_key: str,
        use_cache: bool = True
    ) -> Optional[Dict[str, Any]]:
        """
        Get detailed information for a specific JIRA issue.
        
        Args:
            issue_key: JIRA issue key (e.g., 'RHEL-142095')
            use_cache: Whether to use cached results if available
            
        Returns:
            Parsed issue dictionary or None
        """
        self.logger.info(f"Getting details for issue: {issue_key}")
        
        # Check cache first
        if use_cache and self.cache_enabled:
            cached_issue = self._load_issue_from_cache(issue_key)
            if cached_issue:
                self.logger.info(f"Using cached data for issue: {issue_key}")
                return cached_issue
        
        try:
            with JiraClient() as client:
                # Get the issue
                issue = client.get_issue(issue_key)
                
                if not issue:
                    self.logger.error(f"Issue not found: {issue_key}")
                    return None
                
                # Parse the issue
                parsed_issue = self._parse_issue_complete(client, issue)
                
                # Cache the result
                if self.cache_enabled:
                    self._save_issue_to_cache(issue_key, parsed_issue)
                
                return parsed_issue
                
        except Exception as e:
            self.logger.error(f"Error getting issue details: {e}")
            return None
    
    def _parse_issue_complete(self, client: JiraClient, issue: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parse an issue with all fields including development tab.
        
        Fetches the complete issue data to ensure all nested fields (like description ADF)
        are available for Repo URL extraction.
        
        Args:
            client: JIRA client instance
            issue: JIRA issue dictionary (may be limited from JQL query)
            
        Returns:
            Complete parsed issue dictionary
        """
        issue_key = issue.get('key', 'Unknown')
        
        # Fetch complete issue data to get full nested fields
        # JQL queries return limited data, but we need full description ADF for Repo URL
        self.logger.debug(f"Fetching complete data for {issue_key}")
        complete_issue = client.get_issue(issue_key)
        
        if not complete_issue:
            self.logger.warning(f"Could not fetch complete data for {issue_key}, using limited data")
            complete_issue = issue
        
        # Parse basic fields from complete data
        parsed_issue = self.parser.parse_issue(complete_issue)
        
        # Extract repo URL and kernel version using recursive href search on complete issue data
        try:
            repo_url, kernel_version = self.parser.parse_development_tab(issue_key, complete_issue)
            
            if repo_url:
                parsed_issue['repo_url'] = repo_url
                self.logger.info(f"Successfully extracted repo URL for {issue_key}")
                
                if kernel_version:
                    parsed_issue['repo_kernel_version'] = kernel_version
                    self.logger.info(f"Successfully extracted kernel version: {kernel_version}")
                else:
                    self.logger.warning(f"Could not extract kernel version from repo URL")
            else:
                self.logger.warning(f"No repo URL found for {issue_key}")
            
        except Exception as e:
            self.logger.error(f"Error extracting repo URL for {issue_key}: {e}")
        
        return parsed_issue
    
    def _load_from_cache(self, query_name: str) -> Optional[List[Dict[str, Any]]]:
        """
        Load query results from cache.
        
        Args:
            query_name: Name of the query
            
        Returns:
            Cached results or None
        """
        cache_file = self.cache_dir / f"{query_name}.json"
        
        if not cache_file.exists():
            return None
        
        try:
            with open(cache_file, 'r') as f:
                data = json.load(f)
            
            # Check if cache is expired
            cache_time = datetime.fromisoformat(data.get('timestamp', ''))
            cache_expiry_hours = self.config.get('cache.cache_expiry_hours', 24)
            
            age_hours = (datetime.now() - cache_time).total_seconds() / 3600
            
            if age_hours > cache_expiry_hours:
                self.logger.info(f"Cache expired for query: {query_name}")
                return None
            
            self.logger.info(f"Loaded {len(data.get('results', []))} results from cache")
            return data.get('results', [])
            
        except Exception as e:
            self.logger.error(f"Error loading cache: {e}")
            return None
    
    def _save_to_cache(self, query_name: str, results: List[Dict[str, Any]]) -> None:
        """
        Save query results to cache.
        
        Args:
            query_name: Name of the query
            results: Query results to cache
        """
        cache_file = self.cache_dir / f"{query_name}.json"
        
        try:
            data = {
                'timestamp': datetime.now().isoformat(),
                'query_name': query_name,
                'count': len(results),
                'results': results
            }
            
            with open(cache_file, 'w') as f:
                json.dump(data, f, indent=2)
            
            self.logger.info(f"Cached {len(results)} results for query: {query_name}")
            
        except Exception as e:
            self.logger.error(f"Error saving cache: {e}")
    
    def _load_issue_from_cache(self, issue_key: str) -> Optional[Dict[str, Any]]:
        """
        Load single issue from cache.
        
        Args:
            issue_key: JIRA issue key
            
        Returns:
            Cached issue or None
        """
        cache_file = self.cache_dir / f"issue_{issue_key}.json"
        
        if not cache_file.exists():
            return None
        
        try:
            with open(cache_file, 'r') as f:
                data = json.load(f)
            
            # Check if cache is expired
            cache_time = datetime.fromisoformat(data.get('timestamp', ''))
            cache_expiry_hours = self.config.get('cache.cache_expiry_hours', 24)
            
            age_hours = (datetime.now() - cache_time).total_seconds() / 3600
            
            if age_hours > cache_expiry_hours:
                self.logger.info(f"Cache expired for issue: {issue_key}")
                return None
            
            return data.get('issue', None)
            
        except Exception as e:
            self.logger.error(f"Error loading issue cache: {e}")
            return None
    
    def _save_issue_to_cache(self, issue_key: str, issue_data: Dict[str, Any]) -> None:
        """
        Save single issue to cache.
        
        Args:
            issue_key: JIRA issue key
            issue_data: Issue data to cache
        """
        cache_file = self.cache_dir / f"issue_{issue_key}.json"
        
        try:
            data = {
                'timestamp': datetime.now().isoformat(),
                'issue_key': issue_key,
                'issue': issue_data
            }
            
            with open(cache_file, 'w') as f:
                json.dump(data, f, indent=2)
            
            self.logger.info(f"Cached issue: {issue_key}")
            
        except Exception as e:
            self.logger.error(f"Error saving issue cache: {e}")
    
    def clear_cache(self, query_name: Optional[str] = None) -> None:
        """
        Clear cached query results.
        
        Args:
            query_name: Specific query to clear, or None to clear all
        """
        if query_name:
            cache_file = self.cache_dir / f"{query_name}.json"
            if cache_file.exists():
                cache_file.unlink()
                self.logger.info(f"Cleared cache for query: {query_name}")
        else:
            # Clear all cache files
            for cache_file in self.cache_dir.glob("*.json"):
                cache_file.unlink()
            self.logger.info("Cleared all cache files")

# Made with Bob
