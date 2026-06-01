"""
JIRA REST API Client using direct HTTP requests.
"""

import time
import requests
from typing import Optional, Dict, Any, List

from src.utils.logger import get_logger
from src.utils.config import get_config


class JiraClient:
    """JIRA REST API client with authentication and retry logic."""
    
    def __init__(self):
        """Initialize JIRA client with configuration."""
        self.logger = get_logger(__name__)
        self.config = get_config()
        
        # Get JIRA configuration
        self.server = self.config.get('jira.server')
        self.api_token = self.config.get('jira.api_token')
        self.email = self.config.get('jira.email')
        self.timeout = self.config.get('jira.timeout', 30)
        self.max_retries = self.config.get('jira.retry_attempts', 3)
        self.retry_delay = self.config.get('jira.retry_delay', 5)
        
        # Setup session with authentication
        self.session = requests.Session()
        self.session.auth = (self.email, self.api_token)
        self.session.headers.update({
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        })
        
        # Base API URL
        self.api_base = f"{self.server}/rest/api/3"
        
        self._test_connection()
    
    def _test_connection(self) -> None:
        """
        Test connection to JIRA server.
        
        Raises:
            Exception: If connection fails after retries
        """
        self.logger.info(f"Testing connection to JIRA server: {self.server}")
        
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.session.get(
                    f"{self.api_base}/myself",
                    timeout=self.timeout
                )
                
                if response.status_code == 200:
                    user_data = response.json()
                    self.logger.info(f"Connected to JIRA as: {user_data.get('displayName', 'Unknown')}")
                    self.logger.info(f"Email: {user_data.get('emailAddress', 'Unknown')}")
                    return
                else:
                    self.logger.error(f"Connection test failed with status {response.status_code}")
                    self.logger.error(f"Response: {response.text}")
                    
                    if attempt < self.max_retries:
                        self.logger.info(f"Retrying in {self.retry_delay} seconds...")
                        time.sleep(self.retry_delay)
                    else:
                        raise Exception(f"Connection failed: {response.status_code} - {response.text}")
                
            except requests.exceptions.RequestException as e:
                self.logger.error(f"Connection attempt {attempt}/{self.max_retries} failed: {e}")
                
                if attempt < self.max_retries:
                    self.logger.info(f"Retrying in {self.retry_delay} seconds...")
                    time.sleep(self.retry_delay)
                else:
                    raise
    
    def test_connection(self) -> bool:
        """
        Test if JIRA connection is active.
        
        Returns:
            True if connection is active, False otherwise
        """
        try:
            response = self.session.get(
                f"{self.api_base}/myself",
                timeout=self.timeout
            )
            return response.status_code == 200
        except Exception as e:
            self.logger.error(f"Connection test failed: {e}")
            return False
    
    def get_issue(self, issue_key: str, fields: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Get a single JIRA issue by key.
        
        Args:
            issue_key: JIRA issue key (e.g., 'RHEL-142095')
            fields: Comma-separated list of fields to retrieve (None for all fields)
            
        Returns:
            JIRA issue dictionary or None if not found
        """
        self.logger.info(f"Fetching JIRA issue: {issue_key}")
        
        url = f"{self.api_base}/issue/{issue_key}"
        params = {}
        if fields:
            params['fields'] = fields
        else:
            # Request ALL fields including custom fields
            params['fields'] = '*all'
        
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.session.get(
                    url,
                    params=params,
                    timeout=self.timeout
                )
                
                if response.status_code == 200:
                    self.logger.info(f"Successfully fetched issue: {issue_key}")
                    return response.json()
                elif response.status_code == 404:
                    self.logger.error(f"Issue not found: {issue_key}")
                    return None
                else:
                    self.logger.error(f"Error fetching issue (attempt {attempt}/{self.max_retries}): {response.status_code}")
                    self.logger.error(f"Response: {response.text}")
                    
                    if attempt < self.max_retries:
                        time.sleep(self.retry_delay)
                    else:
                        raise Exception(f"Failed to fetch issue: {response.status_code}")
                
            except requests.exceptions.RequestException as e:
                self.logger.error(f"Request error (attempt {attempt}/{self.max_retries}): {e}")
                
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay)
                else:
                    raise
        
        return None
    
    def search_issues(
        self,
        jql: str,
        max_results: int = 100,
        fields: Optional[str] = None,
        start_at: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Search for issues using JQL query.
        
        Args:
            jql: JQL query string
            max_results: Maximum number of results to return
            fields: Comma-separated list of fields to retrieve (None for all fields)
            start_at: Starting index for pagination
            
        Returns:
            List of JIRA issue dictionaries
        """
        self.logger.info(f"Executing JQL query: {jql[:100]}...")
        self.logger.info(f"Max results: {max_results}, Start at: {start_at}")
        
        # Use API v3 endpoint: POST /rest/api/3/search/jql
        # Match the exact format from the working curl command
        url = f"{self.api_base}/search/jql"
        
        # Build payload - fields must be a list of strings
        field_list = ['key', 'summary', 'status', 'assignee', 'description',
                     'created', 'updated', 'fixVersions', 'versions',
                     'customfield_10879',  # Preliminary Testing
                     'customfield_10470',  # QA Contact
                     'customfield_10894',  # Testable Build (contains Repo URL)
                     'customfield_10578']  # Fixed in Build
        
        if fields:
            field_list = fields.split(',') if isinstance(fields, str) else fields
        
        payload = {
            'jql': jql,
            'fields': field_list,
            'maxResults': max_results
        }
        
        # Only add startAt if it's not 0 (to match curl example)
        if start_at > 0:
            payload['startAt'] = start_at
        
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.session.post(
                    url,
                    json=payload,
                    timeout=self.timeout
                )
                
                if response.status_code == 200:
                    data = response.json()
                    issues = data.get('issues', [])
                    total = data.get('total', 0)
                    
                    self.logger.info(f"Found {len(issues)} issues (total: {total})")
                    return issues
                else:
                    self.logger.error(f"JQL query failed (attempt {attempt}/{self.max_retries}): {response.status_code}")
                    self.logger.error(f"Response: {response.text}")
                    
                    if attempt < self.max_retries:
                        time.sleep(self.retry_delay)
                    else:
                        raise Exception(f"JQL query failed: {response.status_code}")
                
            except requests.exceptions.RequestException as e:
                self.logger.error(f"Request error (attempt {attempt}/{self.max_retries}): {e}")
                
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay)
                else:
                    raise
        
        return []
    
    def get_issue_remote_links(self, issue_key: str) -> List[Dict[str, Any]]:
        """
        Get remote links for an issue (used for Development tab data).
        
        Args:
            issue_key: JIRA issue key
            
        Returns:
            List of remote link dictionaries
        """
        self.logger.debug(f"Fetching remote links for: {issue_key}")
        
        url = f"{self.api_base}/issue/{issue_key}/remotelink"
        
        try:
            response = self.session.get(url, timeout=self.timeout)
            
            if response.status_code == 200:
                remote_links = response.json()
                self.logger.debug(f"Found {len(remote_links)} remote links")
                return remote_links
            else:
                self.logger.warning(f"Could not fetch remote links: {response.status_code}")
                return []
            
        except Exception as e:
            self.logger.error(f"Error fetching remote links: {e}")
            return []
    
    def get_issue_development_info(self, issue_key: str) -> Dict[str, Any]:
        """
        Get development information for an issue.
        This includes build information from the Development tab.
        
        Args:
            issue_key: JIRA issue key
            
        Returns:
            Dictionary containing development information
        """
        self.logger.debug(f"Fetching development info for: {issue_key}")
        
        dev_info = {}
        
        try:
            # Get the full issue with all fields
            issue = self.get_issue(issue_key)
            
            if not issue:
                return {}
            
            # Extract development-related fields
            fields = issue.get('fields', {})
            
            # Look for development status fields
            for field_name, field_value in fields.items():
                if field_value and ('dev' in field_name.lower() or 'build' in field_name.lower()):
                    dev_info[field_name] = field_value
            
            # Also get remote links which may contain build info
            remote_links = self.get_issue_remote_links(issue_key)
            if remote_links:
                dev_info['remote_links'] = remote_links
            
            return dev_info
            
        except Exception as e:
            self.logger.error(f"Error fetching development info: {e}")
            return {}
    
    def get_myself(self) -> Dict[str, Any]:
        """
        Get information about the authenticated user.
        
        Returns:
            Dictionary with user information
        """
        try:
            response = self.session.get(
                f"{self.api_base}/myself",
                timeout=self.timeout
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                self.logger.error(f"Error getting user info: {response.status_code}")
                return {}
                
        except Exception as e:
            self.logger.error(f"Error getting user info: {e}")
            return {}
    
    def close(self) -> None:
        """Close the JIRA client session."""
        if self.session:
            self.logger.info("Closing JIRA session")
            self.session.close()
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()

# Made with Bob
