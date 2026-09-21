"""
Unit tests for the VaultClient class.
Tests API response handling, error management, and VQL query construction.
These tests use mock responses and do NOT require a live Vault connection.
"""

import unittest
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from vault_client import VaultClient, VaultAPIError, VaultAuthError


class TestVaultClientInit(unittest.TestCase):
    """Tests for VaultClient initialization."""

    def test_url_construction(self):
        """Base URL should combine vault_url and api_version."""
        client = VaultClient(
            vault_url="https://novapharma.veevavault.com",
            api_version="v24.1"
        )
        self.assertEqual(
            client.base_url,
            "https://novapharma.veevavault.com/api/v24.1"
        )

    def test_trailing_slash_stripped(self):
        """Trailing slash on vault_url should be removed."""
        client = VaultClient(
            vault_url="https://novapharma.veevavault.com/",
            api_version="v24.1"
        )
        self.assertEqual(
            client.base_url,
            "https://novapharma.veevavault.com/api/v24.1"
        )

    def test_default_api_version(self):
        """Default API version should be v24.1."""
        client = VaultClient(vault_url="https://test.veevavault.com")
        self.assertIn("v24.1", client.base_url)

    def test_no_session_on_init(self):
        """Client should not have a session ID before authentication."""
        client = VaultClient(vault_url="https://test.veevavault.com")
        self.assertIsNone(client.session_id)


class TestVaultClientHeaders(unittest.TestCase):
    """Tests for authentication header management."""

    def test_headers_require_auth(self):
        """Getting headers without auth should raise VaultAuthError."""
        client = VaultClient(vault_url="https://test.veevavault.com")
        with self.assertRaises(VaultAuthError):
            client._get_headers()

    def test_headers_with_session(self):
        """Headers should include session ID after manual assignment."""
        client = VaultClient(vault_url="https://test.veevavault.com")
        client.session_id = "TEST_SESSION_ID_123"
        headers = client._get_headers()
        self.assertEqual(headers["Authorization"], "TEST_SESSION_ID_123")
        self.assertEqual(headers["Accept"], "application/json")


class TestVaultResponseHandling(unittest.TestCase):
    """Tests for API response parsing and error handling."""

    def setUp(self):
        self.client = VaultClient(vault_url="https://test.veevavault.com")

    def test_success_response(self):
        """Successful responses should return the data dict."""

        class MockResponse:
            status_code = 200
            def json(self):
                return {
                    "responseStatus": "SUCCESS",
                    "data": [{"id": 1, "name__v": "Test Doc"}]
                }

        result = self.client._handle_response(MockResponse())
        self.assertEqual(result["responseStatus"], "SUCCESS")
        self.assertEqual(len(result["data"]), 1)

    def test_failure_response(self):
        """Failed responses should raise VaultAPIError with details."""

        class MockResponse:
            status_code = 400
            def json(self):
                return {
                    "responseStatus": "FAILURE",
                    "errors": [{
                        "type": "INVALID_DATA",
                        "message": "Missing required field: name__v"
                    }]
                }

        with self.assertRaises(VaultAPIError) as ctx:
            self.client._handle_response(MockResponse())

        self.assertIn("INVALID_DATA", str(ctx.exception))
        self.assertIn("name__v", str(ctx.exception))

    def test_invalid_json_response(self):
        """Non-JSON responses should raise VaultAPIError."""

        class MockResponse:
            status_code = 500
            def json(self):
                raise ValueError("No JSON")

        with self.assertRaises(VaultAPIError) as ctx:
            self.client._handle_response(MockResponse())

        self.assertIn("Invalid JSON", str(ctx.exception))

    def test_multiple_errors(self):
        """Multiple errors should all be captured in the exception."""

        class MockResponse:
            status_code = 400
            def json(self):
                return {
                    "responseStatus": "FAILURE",
                    "errors": [
                        {"type": "INVALID_DATA", "message": "Field A is invalid"},
                        {"type": "MISSING_FIELD", "message": "Field B is required"}
                    ]
                }

        with self.assertRaises(VaultAPIError) as ctx:
            self.client._handle_response(MockResponse())

        error = ctx.exception
        self.assertEqual(len(error.errors), 2)


class TestVaultClientClose(unittest.TestCase):
    """Tests for session cleanup."""

    def test_close_clears_session(self):
        """Closing should clear the session ID."""
        client = VaultClient(vault_url="https://test.veevavault.com")
        client.session_id = "ACTIVE_SESSION"
        client.close()
        self.assertIsNone(client.session_id)


class TestVQLQuerySamples(unittest.TestCase):
    """Tests to verify VQL query samples are well-formed."""

    def setUp(self):
        from migration_engine import SAMPLE_VQL_QUERIES
        self.queries = SAMPLE_VQL_QUERIES

    def test_all_queries_have_select(self):
        """Every sample VQL query should start with SELECT."""
        for name, query in self.queries.items():
            self.assertIn("SELECT", query.upper(),
                         f"Query '{name}' missing SELECT clause")

    def test_all_queries_have_from(self):
        """Every sample VQL query should have a FROM clause."""
        for name, query in self.queries.items():
            self.assertIn("FROM", query.upper(),
                         f"Query '{name}' missing FROM clause")

    def test_query_count(self):
        """Should have at least 8 sample queries."""
        self.assertGreaterEqual(len(self.queries), 8)

    def test_migration_verification_query_exists(self):
        """There should be a query for migration verification."""
        self.assertIn("migration_verification", self.queries)
        self.assertIn("legacy_doc_number__c",
                      self.queries["migration_verification"])


if __name__ == "__main__":
    unittest.main()
