"""
JIRA content parser for extracting specific fields and data from REST API responses.
"""

import re
from typing import Dict, Any, List, Optional, Union
from src.utils.logger import get_logger


def normalize_fixed_in_build(value: str) -> str:
    """Normalize Fixed in Build to a single kernel NVR.

    JIRA may list multiple builds separated by ``|`` (e.g. RT and non-RT).
    When multiple values are present, prefer the non-RT ``kernel-`` build.
    """
    if not value or not str(value).strip():
        return ""

    parts = [p.strip() for p in str(value).split("|") if p.strip()]
    if len(parts) <= 1:
        return parts[0] if parts else str(value).strip()

    non_rt = [p for p in parts if not p.startswith("kernel-rt-")]
    if non_rt:
        return non_rt[0]

    return parts[0]


class JiraParser:
    """Parser for extracting data from JIRA issue dictionaries."""
    
    def __init__(self):
        """Initialize the parser."""
        self.logger = get_logger(__name__)
    
    def parse_issue(self, issue: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parse a JIRA issue dictionary and extract all required fields.
        
        Args:
            issue: JIRA issue dictionary from REST API
            
        Returns:
            Dictionary with extracted fields
        """
        issue_key = issue.get('key', 'Unknown')
        self.logger.info(f"Parsing JIRA issue: {issue_key}")
        
        fields = issue.get('fields', {})
        
        status = self._get_status(fields)
        preliminary_testing = self._get_preliminary_testing(fields)
        fixed_in_build = self._get_fixed_in_build(fields)

        parsed_data = {
            'jira_id': issue_key,
            'jira_title': self._get_field(fields, 'summary', ''),
            'status': status,
            'preliminary_testing': preliminary_testing,
            'fix_versions': self._get_fix_versions(fields),
            'affects_versions': self._get_affects_versions(fields),
            'qa_contact': self._get_qa_contact(fields),
            'fixed_in_build': fixed_in_build,
            'verification_type': self._determine_verification_type(
                status, preliminary_testing, fixed_in_build
            ),
            'repo_url': None,
            'repo_kernel_version': None,
            'description': self._get_field(fields, 'description', ''),
            'assignee': self._get_assignee(fields),
            'created': self._get_field(fields, 'created', ''),
            'updated': self._get_field(fields, 'updated', ''),
        }
        
        self.logger.info(f"Successfully parsed issue: {parsed_data['jira_id']}")
        return parsed_data
    
    def _determine_verification_type(
        self, status: str, preliminary_testing: str, fixed_in_build: str
    ) -> str:
        """Determine verification type based on JIRA status and field values.

        Returns 'pre', 'post', or 'N/A'.
        """
        if (
            status.lower() == "in progress"
            and preliminary_testing.lower() == "requested"
        ):
            return "pre"
        if status.lower() == "integration" and fixed_in_build.strip():
            return "post"
        return "N/A"

    def _get_field(self, fields: Dict[str, Any], field_name: str, default: Any = None) -> Any:
        """Safely get a field value."""
        return fields.get(field_name, default)
    
    def _get_status(self, fields: Dict[str, Any]) -> str:
        """Extract status."""
        try:
            status = fields.get('status', {})
            if isinstance(status, dict):
                return status.get('name', '')
            return str(status) if status else ''
        except Exception as e:
            self.logger.error(f"Error extracting status: {e}")
            return ""
    
    def _get_preliminary_testing(self, fields: Dict[str, Any]) -> str:
        """
        Extract Preliminary Testing field.
        This is typically a custom field in Red Hat JIRA.
        """
        try:
            # Common custom field names for Preliminary Testing
            field_names = [
                'customfield_10879',     # Red Hat JIRA Preliminary Testing field
                'customfield_12316243',  # Alternative field
                'customfield_12313440',
                'preliminary_testing',
            ]
            
            for field_name in field_names:
                value = fields.get(field_name)
                if value:
                    # Handle different value types
                    if isinstance(value, dict):
                        return value.get('value', value.get('name', str(value)))
                    elif isinstance(value, str):
                        return value
                    else:
                        return str(value)
            
            # If not found in custom fields, try to find in description
            description = fields.get('description', '')
            if description:
                match = re.search(r'Preliminary Testing[:\s]+([^\n]+)', str(description), re.IGNORECASE)
                if match:
                    return match.group(1).strip()
            
            return ""
            
        except Exception as e:
            self.logger.error(f"Error extracting preliminary testing: {e}")
            return ""
    
    def extract_kernel_from_repo_url(self, repo_url: str) -> Optional[str]:
        """
        Extract kernel version from Repo URL.
        
        Examples:
            https://artifacts.internal.cki-project.org/.../repo/5.14.0-284.170.1.6010_2522579297.el9_2.x86_64/
            -> 5.14.0-284.170.1.6010_2522579297.el9_2.x86_64
            
            https://s3.amazonaws.com/.../repo/6.12.0-191.2046_2283707585.el10.x86_64/
            -> 6.12.0-191.2046_2283707585.el10.x86_64
            
            https://artifacts.internal.cki-project.org/.../repo/4.18.0-477.141.1.el8_8.9749_2521991333.x86_64/
            -> 4.18.0-477.141.1.el8_8.9749_2521991333.x86_64
        
        Args:
            repo_url: Repository URL containing kernel version
            
        Returns:
            Kernel version string or None if not found
        """
        if not repo_url:
            return None
        
        try:
            # Pattern to match kernel version in URL path
            # Simply captures everything between /repo/ and the trailing slash
            # This handles all kernel version formats
            pattern = r'/repo/([^/]+)/?$'
            
            match = re.search(pattern, repo_url)
            if match:
                kernel_version = match.group(1)
                self.logger.debug(f"Extracted kernel version from repo URL: {kernel_version}")
                return kernel_version
            
            self.logger.warning(f"Could not extract kernel version from repo URL: {repo_url}")
            return None
            
        except Exception as e:
            self.logger.error(f"Error extracting preliminary testing: {e}")
            return ""
    
    def _get_fix_versions(self, fields: Dict[str, Any]) -> List[str]:
        """Extract Fix Versions."""
        try:
            fix_versions = fields.get('fixVersions', [])
            if isinstance(fix_versions, list):
                return [v.get('name', str(v)) if isinstance(v, dict) else str(v) for v in fix_versions]
            return []
        except Exception as e:
            self.logger.error(f"Error extracting fix versions: {e}")
            return []
    
    def _get_affects_versions(self, fields: Dict[str, Any]) -> List[str]:
        """Extract Affects Versions."""
        try:
            versions = fields.get('versions', [])
            if isinstance(versions, list):
                return [v.get('name', str(v)) if isinstance(v, dict) else str(v) for v in versions]
            return []
        except Exception as e:
            self.logger.error(f"Error extracting affects versions: {e}")
            return []
    
    def _get_fixed_in_build(self, fields: Dict[str, Any]) -> str:
        """
        Extract Fixed in Build version.
        This is stored in customfield_10578.
        """
        try:
            fixed_in_build = fields.get('customfield_10578')
            if fixed_in_build:
                if isinstance(fixed_in_build, str):
                    raw = fixed_in_build
                elif isinstance(fixed_in_build, dict):
                    raw = fixed_in_build.get('value', str(fixed_in_build))
                else:
                    raw = str(fixed_in_build)

                normalized = normalize_fixed_in_build(raw)
                if normalized != raw.strip():
                    self.logger.info(
                        f"Normalized Fixed in Build: '{raw}' -> '{normalized}'"
                    )
                return normalized
            return ""
        except Exception as e:
            self.logger.error(f"Error extracting fixed in build: {e}")
            return ""
    
    def _get_qa_contact(self, fields: Dict[str, Any]) -> str:
        """
        Extract QA Contact.
        This is typically a custom field in Red Hat JIRA or in description.
        """
        try:
            # Common custom field names for QA Contact
            field_names = [
                'customfield_10470',     # Red Hat JIRA QA Contact field
                'customfield_12313140',  # Alternative field
                'customfield_12310220',
                'customfield_10880',
                'qa_contact',
            ]
            
            for field_name in field_names:
                value = fields.get(field_name)
                if value:
                    # Handle user object
                    if isinstance(value, dict):
                        return value.get('displayName', value.get('name', str(value)))
                    elif isinstance(value, str):
                        return value
                    else:
                        return str(value)
            
            # Try to extract from description
            description = self._extract_text_from_description(fields.get('description'))
            if description:
                # Look for "QA Contact: email@redhat.com" pattern
                match = re.search(r'QA Contact[:\s]+([^\s,\n]+@redhat\.com)', description, re.IGNORECASE)
                if match:
                    return match.group(1)
                
                # Look for "QA Contact: Name" pattern
                match = re.search(r'QA Contact[:\s]+([^\n]+)', description, re.IGNORECASE)
                if match:
                    return match.group(1).strip()
            
            return ""
        
        except Exception as e:
            self.logger.error(f"Error extracting QA contact: {e}")
            return ""
    
    def _extract_text_from_description(self, description: Any) -> str:
        """
        Extract plain text from JIRA description (which can be in various formats).
        
        Args:
            description: Description field (can be string, dict, or complex object)
            
        Returns:
            Plain text string
        """
        if not description:
            return ""
        
        # If it's already a string, return it
        if isinstance(description, str):
            return description
        
        # If it's a dict (Atlassian Document Format), extract text
        if isinstance(description, dict):
            text_parts = []
            self._extract_text_recursive(description, text_parts)
            return ' '.join(text_parts)
        
        return str(description)
    
    def _extract_text_recursive(self, obj: Any, text_parts: List[str]) -> None:
        """
        Recursively extract text from nested Atlassian Document Format.
        
        Args:
            obj: Object to extract text from
            text_parts: List to append text parts to
        """
        if isinstance(obj, dict):
            # Check for text content
            if 'text' in obj:
                text_parts.append(obj['text'])
            
            # Recurse into content array
            if 'content' in obj and isinstance(obj['content'], list):
                for item in obj['content']:
                    self._extract_text_recursive(item, text_parts)
        
        elif isinstance(obj, list):
            for item in obj:
                self._extract_text_recursive(item, text_parts)
    
    def _get_assignee(self, fields: Dict[str, Any]) -> str:
        """Extract assignee."""
        try:
            assignee = fields.get('assignee')
            if assignee and isinstance(assignee, dict):
                return assignee.get('displayName', assignee.get('name', ''))
            return str(assignee) if assignee else ''
        except Exception as e:
            self.logger.error(f"Error extracting assignee: {e}")
            return ""
    
    def _extract_all_hrefs(self, data: Union[Dict, List, Any]) -> List[str]:
        """
        Recursively extract all href values and URLs from text fields in nested JSON structure.
        
        This mimics: jq -r '.. | .href? // empty' but also extracts URLs from text fields
        
        Args:
            data: JSON data (dict, list, or primitive)
            
        Returns:
            List of all URLs found (from href attributes and text fields)
        """
        import re
        hrefs = []
        
        if isinstance(data, dict):
            # Check if this dict has an 'href' key
            if 'href' in data:
                href_value = data['href']
                if isinstance(href_value, str):
                    hrefs.append(href_value)
            
            # Also check 'text' fields for URLs (for customfield_10894)
            if 'text' in data:
                text_value = data['text']
                if isinstance(text_value, str):
                    # Extract URLs from text using regex
                    url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
                    found_urls = re.findall(url_pattern, text_value)
                    hrefs.extend(found_urls)
            
            # Recursively search all values
            for value in data.values():
                hrefs.extend(self._extract_all_hrefs(value))
                
        elif isinstance(data, list):
            # Recursively search all items
            for item in data:
                hrefs.extend(self._extract_all_hrefs(item))
        
        return hrefs
    
    def parse_development_tab(
        self,
        issue_key: str,
        issue_data: Dict[str, Any]
    ) -> tuple[Optional[str], Optional[str]]:
        """
        Extract repository URL and kernel version from JIRA issue.
        
        Mimics: jq -r '.. | .href? // empty' | grep "/publish_x86_64/" | grep -v "index.html" | grep -v "\.rt"
        
        Filters for:
        - URLs containing /publish_x86_64/
        - Excludes index.html
        - Excludes .rt files
        
        Args:
            issue_key: JIRA issue key
            issue_data: Complete JIRA issue data dictionary
            
        Returns:
            Tuple of (repo_url, kernel_version) - both can be None if not found
        """
        self.logger.info(f"Extracting repo URL for: {issue_key}")
        
        try:
            # Extract all hrefs from the entire JSON structure
            all_hrefs = self._extract_all_hrefs(issue_data)
            self.logger.debug(f"Found {len(all_hrefs)} total hrefs in issue data")
            
            # Filter for publish_x86_64 URLs
            for href in all_hrefs:
                if '/publish_x86_64/' in href:
                    # Exclude index.html and .rt files
                    if 'index.html' not in href and not href.endswith('.rt'):
                        self.logger.info(f"Found matching Repo URL: {href}")
                        
                        # Extract kernel version from the URL
                        kernel_version = self.extract_kernel_from_repo_url(href)
                        if kernel_version:
                            self.logger.info(f"Extracted kernel version: {kernel_version}")
                        
                        return href, kernel_version
            
            self.logger.warning(f"No matching Repo URL found for: {issue_key}")
            return None, None
            
        except Exception as e:
            self.logger.error(f"Error extracting repo URL: {e}")
            return None, None
        
# Made with Bob
