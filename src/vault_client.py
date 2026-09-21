"""
Veeva Vault REST API Client
===========================
A Python client for interacting with the Veeva Vault REST API.
Handles authentication, session management, and core CRUD operations
for documents and object records.

Reference: https://developer.veevavault.com/api/
"""

import requests
import logging
import time
from typing import Optional, Dict, Any, List
from pathlib import Path

logger = logging.getLogger(__name__)


class VaultAuthError(Exception):
    """Raised when Vault authentication fails."""
    pass


class VaultAPIError(Exception):
    """Raised when a Vault API call returns an error response."""
    def __init__(self, message: str, status: str = "", errors: list = None):
        self.status = status
        self.errors = errors or []
        super().__init__(message)


class VaultClient:
    """
    Client for the Veeva Vault REST API.

    Usage:
        client = VaultClient(
            vault_url="https://novapharma.veevavault.com",
            api_version="v24.1"
        )
        client.authenticate(username="user@novapharma.com", password="password")

        # Create a document
        doc = client.create_document(
            doc_type="SOP",
            doc_subtype="Quality SOP",
            metadata={"name__v": "SOP-QA-0001", "department__c": "Quality"},
            file_path="path/to/document.pdf"
        )
    """

    # Vault API rate limit: 1000 requests per minute (burst), sustained ~200/min
    RATE_LIMIT_DELAY = 0.1  # seconds between requests

    def __init__(self, vault_url: str, api_version: str = "v24.1"):
        """
        Initialize the Vault client.

        Args:
            vault_url: Base URL of the Vault instance (e.g., https://novapharma.veevavault.com)
            api_version: Vault API version string (e.g., v24.1)
        """
        self.vault_url = vault_url.rstrip("/")
        self.api_version = api_version
        self.base_url = f"{self.vault_url}/api/{self.api_version}"
        self.session_id: Optional[str] = None
        self._session = requests.Session()
        self._last_request_time = 0

    def _throttle(self):
        """Simple rate limiting to avoid hitting Vault API limits."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.RATE_LIMIT_DELAY:
            time.sleep(self.RATE_LIMIT_DELAY - elapsed)
        self._last_request_time = time.time()

    def _get_headers(self) -> Dict[str, str]:
        """Return headers with authentication session ID."""
        if not self.session_id:
            raise VaultAuthError("Not authenticated. Call authenticate() first.")
        return {
            "Authorization": self.session_id,
            "Accept": "application/json"
        }

    def _handle_response(self, response: requests.Response) -> Dict[str, Any]:
        """
        Parse Vault API response and raise errors if needed.

        Vault API returns JSON with 'responseStatus' field:
        - SUCCESS: operation completed
        - FAILURE: operation failed, check 'errors' array
        """
        try:
            data = response.json()
        except ValueError:
            raise VaultAPIError(
                f"Invalid JSON response (HTTP {response.status_code})",
                status="PARSE_ERROR"
            )

        if data.get("responseStatus") == "FAILURE":
            errors = data.get("errors", [])
            error_messages = [
                f"{e.get('type', 'UNKNOWN')}: {e.get('message', 'No message')}"
                for e in errors
            ]
            raise VaultAPIError(
                f"Vault API error: {'; '.join(error_messages)}",
                status="FAILURE",
                errors=errors
            )

        return data

    # =========================================================================
    # AUTHENTICATION
    # =========================================================================

    def authenticate(self, username: str, password: str) -> str:
        """
        Authenticate with Vault and obtain a session ID.

        Uses the Vault authentication endpoint:
        POST /api/{version}/auth

        Args:
            username: Vault username (email)
            password: Vault password

        Returns:
            Session ID string

        Raises:
            VaultAuthError: If authentication fails
        """
        url = f"{self.base_url}/auth"
        payload = {
            "username": username,
            "password": password
        }

        logger.info(f"Authenticating user {username} against {self.vault_url}")

        try:
            response = self._session.post(
                url,
                headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
                data=payload
            )
            data = self._handle_response(response)
            self.session_id = data["sessionId"]
            logger.info(f"Authentication successful. User ID: {data.get('userId')}")
            return self.session_id

        except (VaultAPIError, KeyError) as e:
            raise VaultAuthError(f"Authentication failed: {e}")

    def authenticate_with_discovery(self, username: str, password: str) -> str:
        """
        Authenticate using Vault's OAuth 2.0 / SAML discovery endpoint.
        This is the preferred method for production environments with SSO.

        POST https://login.veevavault.com/auth/discovery
        """
        discovery_url = "https://login.veevavault.com/auth/discovery"
        response = self._session.post(
            discovery_url,
            headers={"Accept": "application/json"},
            data={"username": username}
        )
        data = self._handle_response(response)

        auth_type = data.get("data", {}).get("auth_type", "")
        logger.info(f"Discovery auth type for {username}: {auth_type}")

        if auth_type == "sso":
            logger.info("SSO detected — redirect to IdP required (not handled in CLI)")
            raise VaultAuthError("SSO authentication requires browser-based login flow")

        # For basic auth, proceed with standard authentication
        return self.authenticate(username, password)

    def keep_alive(self) -> bool:
        """
        Send a keep-alive to extend the current session.

        POST /api/{version}/keep-alive
        """
        url = f"{self.base_url}/keep-alive"
        self._throttle()
        response = self._session.post(url, headers=self._get_headers())
        data = self._handle_response(response)
        return data.get("responseStatus") == "SUCCESS"

    # =========================================================================
    # DOCUMENT OPERATIONS (CRUD)
    # =========================================================================

    def create_document(
        self,
        doc_type: str,
        doc_subtype: str,
        metadata: Dict[str, Any],
        file_path: Optional[str] = None,
        lifecycle: str = "general_lifecycle__v"
    ) -> Dict[str, Any]:
        """
        Create a new document in Vault.

        POST /api/{version}/objects/documents

        Args:
            doc_type: Document type (e.g., "sop__c")
            doc_subtype: Document subtype (e.g., "quality_sop__c")
            metadata: Dictionary of metadata field name-value pairs
            file_path: Path to the source file to upload
            lifecycle: Lifecycle name (default: general_lifecycle__v)

        Returns:
            Dict with document ID and major/minor version numbers
        """
        url = f"{self.base_url}/objects/documents"

        # Build the metadata payload
        form_data = {
            "type__v": doc_type,
            "subtype__v": doc_subtype,
            "lifecycle__v": lifecycle,
            **metadata
        }

        files = None
        if file_path:
            file_obj = Path(file_path)
            if not file_obj.exists():
                raise FileNotFoundError(f"Source file not found: {file_path}")
            files = {"file": (file_obj.name, open(file_path, "rb"))}

        self._throttle()
        logger.info(f"Creating document: {metadata.get('name__v', 'unnamed')}")

        response = self._session.post(
            url,
            headers=self._get_headers(),
            data=form_data,
            files=files
        )

        data = self._handle_response(response)
        doc_id = data.get("id")
        logger.info(f"Document created successfully. ID: {doc_id}")
        return data

    def get_document(self, doc_id: int) -> Dict[str, Any]:
        """
        Retrieve document metadata.

        GET /api/{version}/objects/documents/{doc_id}
        """
        url = f"{self.base_url}/objects/documents/{doc_id}"
        self._throttle()
        response = self._session.get(url, headers=self._get_headers())
        return self._handle_response(response)

    def get_document_content(self, doc_id: int, major_version: int = 0,
                              minor_version: int = 1) -> bytes:
        """
        Download the source file of a document version.

        GET /api/{version}/objects/documents/{doc_id}/versions/{major}/{minor}/file
        """
        url = (f"{self.base_url}/objects/documents/{doc_id}"
               f"/versions/{major_version}/{minor_version}/file")
        self._throttle()
        response = self._session.get(url, headers=self._get_headers(), stream=True)

        if response.status_code != 200:
            raise VaultAPIError(f"Failed to download content (HTTP {response.status_code})")

        return response.content

    def update_document(self, doc_id: int, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update document metadata.

        PUT /api/{version}/objects/documents/{doc_id}
        """
        url = f"{self.base_url}/objects/documents/{doc_id}"
        self._throttle()
        logger.info(f"Updating document {doc_id}")
        response = self._session.put(
            url,
            headers=self._get_headers(),
            data=metadata
        )
        return self._handle_response(response)

    def delete_document(self, doc_id: int) -> Dict[str, Any]:
        """
        Delete a document (only works for documents in Draft state).

        DELETE /api/{version}/objects/documents/{doc_id}
        """
        url = f"{self.base_url}/objects/documents/{doc_id}"
        self._throttle()
        logger.warning(f"Deleting document {doc_id}")
        response = self._session.delete(url, headers=self._get_headers())
        return self._handle_response(response)

    # =========================================================================
    # DOCUMENT LIFECYCLE OPERATIONS
    # =========================================================================

    def get_document_lifecycle_actions(self, doc_id: int) -> List[Dict]:
        """
        Retrieve available lifecycle actions for a document.

        GET /api/{version}/objects/documents/{doc_id}/versions/{major}/{minor}/lifecycle_actions
        """
        url = f"{self.base_url}/objects/documents/{doc_id}/versions/0/1/lifecycle_actions"
        self._throttle()
        response = self._session.get(url, headers=self._get_headers())
        data = self._handle_response(response)
        return data.get("lifecycle_actions__v", [])

    def execute_lifecycle_action(self, doc_id: int, action_name: str,
                                  major: int = 0, minor: int = 1) -> Dict[str, Any]:
        """
        Execute a lifecycle state change on a document.

        PUT /api/{version}/objects/documents/{doc_id}/versions/{major}/{minor}/lifecycle_actions/{action}
        """
        url = (f"{self.base_url}/objects/documents/{doc_id}"
               f"/versions/{major}/{minor}/lifecycle_actions/{action_name}")
        self._throttle()
        logger.info(f"Executing lifecycle action '{action_name}' on document {doc_id}")
        response = self._session.put(url, headers=self._get_headers())
        return self._handle_response(response)

    # =========================================================================
    # VQL (VAULT QUERY LANGUAGE)
    # =========================================================================

    def query(self, vql: str) -> List[Dict]:
        """
        Execute a VQL query and return all results (handles pagination).

        POST /api/{version}/query

        VQL is an SQL-like query language for Vault data:
            SELECT id, name__v, status__v FROM documents
            WHERE type__v = 'sop__c' AND status__v = 'Effective'

        Args:
            vql: VQL query string

        Returns:
            List of result records
        """
        url = f"{self.base_url}/query"
        all_results = []
        offset = 0
        page_size = 200  # Vault default page size

        logger.info(f"Executing VQL: {vql[:100]}...")

        while True:
            paginated_vql = f"{vql} LIMIT {page_size} OFFSET {offset}"
            self._throttle()
            response = self._session.post(
                url,
                headers={**self._get_headers(), "Content-Type": "application/x-www-form-urlencoded"},
                data={"q": paginated_vql}
            )
            data = self._handle_response(response)

            results = data.get("data", [])
            all_results.extend(results)

            # Check if there are more pages
            response_details = data.get("responseDetails", {})
            total = response_details.get("total", 0)

            if len(all_results) >= total or len(results) == 0:
                break

            offset += page_size

        logger.info(f"Query returned {len(all_results)} records")
        return all_results

    # =========================================================================
    # BULK OPERATIONS (for migration)
    # =========================================================================

    def bulk_create_documents(self, documents: List[Dict[str, Any]]) -> List[Dict]:
        """
        Create multiple documents in a single API call using Vault's bulk API.

        POST /api/{version}/objects/documents/batch

        Vault supports up to 500 documents per batch request.

        Args:
            documents: List of document metadata dictionaries

        Returns:
            List of results per document
        """
        url = f"{self.base_url}/objects/documents/batch"
        batch_size = 500
        all_results = []

        for i in range(0, len(documents), batch_size):
            batch = documents[i:i + batch_size]
            logger.info(f"Processing batch {i // batch_size + 1} "
                       f"({len(batch)} documents)")

            # Vault bulk API expects CSV or JSON array
            self._throttle()
            response = self._session.post(
                url,
                headers={**self._get_headers(), "Content-Type": "application/json"},
                json=batch
            )
            data = self._handle_response(response)
            all_results.extend(data.get("data", []))

        return all_results

    def bulk_update_metadata(self, updates: List[Dict[str, Any]]) -> List[Dict]:
        """
        Update metadata for multiple documents in a single API call.

        PUT /api/{version}/objects/documents/batch

        Args:
            updates: List of dicts, each containing 'id' and metadata fields to update
        """
        url = f"{self.base_url}/objects/documents/batch"
        batch_size = 500
        all_results = []

        for i in range(0, len(updates), batch_size):
            batch = updates[i:i + batch_size]
            self._throttle()
            response = self._session.put(
                url,
                headers={**self._get_headers(), "Content-Type": "application/json"},
                json=batch
            )
            data = self._handle_response(response)
            all_results.extend(data.get("data", []))

        return all_results

    # =========================================================================
    # OBJECT RECORD OPERATIONS
    # =========================================================================

    def get_object_record(self, object_name: str, record_id: str) -> Dict[str, Any]:
        """
        Retrieve a Vault object record.

        GET /api/{version}/vobjects/{object_name}/{record_id}
        """
        url = f"{self.base_url}/vobjects/{object_name}/{record_id}"
        self._throttle()
        response = self._session.get(url, headers=self._get_headers())
        return self._handle_response(response)

    def create_object_record(self, object_name: str,
                              data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new Vault object record.

        POST /api/{version}/vobjects/{object_name}
        """
        url = f"{self.base_url}/vobjects/{object_name}"
        self._throttle()
        response = self._session.post(
            url,
            headers=self._get_headers(),
            data=data
        )
        return self._handle_response(response)

    # =========================================================================
    # UTILITY
    # =========================================================================

    def get_api_versions(self) -> List[str]:
        """
        Retrieve all supported API versions for this Vault.

        GET /api/
        """
        url = f"{self.vault_url}/api/"
        response = self._session.get(url, headers={"Accept": "application/json"})
        data = self._handle_response(response)
        return [v.get("url", "") for v in data.get("values", [])]

    def get_vault_info(self) -> Dict[str, Any]:
        """
        Retrieve metadata about the current Vault.

        GET /api/{version}/metadata/vaults
        """
        url = f"{self.base_url}/metadata/vaults"
        self._throttle()
        response = self._session.get(url, headers=self._get_headers())
        return self._handle_response(response)

    def close(self):
        """End the session and close connections."""
        self._session.close()
        self.session_id = None
        logger.info("Vault session closed")
