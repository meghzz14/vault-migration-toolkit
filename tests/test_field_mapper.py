"""
Unit tests for the FieldMapper class.
Tests metadata transformation from source system fields to Vault fields.
"""

import unittest
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from migration_engine import FieldMapper


class TestFieldMapper(unittest.TestCase):
    """Tests for FieldMapper metadata transformation."""

    @classmethod
    def setUpClass(cls):
        """Create a temporary field mapping config for tests."""
        cls.config = {
            "field_mappings": {
                "source_id": "legacy_doc_number__c",
                "department": "department__c",
                "classification": "gxp_classification__c",
                "owner_name": "document_owner__c",
                "training_needed": "training_required__c"
            },
            "value_mappings": {
                "department__c": {
                    "QA": "Quality",
                    "QC": "Laboratory",
                    "Mfg": "Manufacturing"
                },
                "gxp_classification__c": {
                    "GMP": "GxP",
                    "GLP": "GxP",
                    "Non-GxP": "Non-GxP"
                },
                "training_required__c": {
                    "Y": "Yes",
                    "N": "No"
                }
            },
            "default_values": {
                "gxp_classification__c": "GxP",
                "review_cycle_months__c": "24"
            }
        }

        cls.temp_file = tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False
        )
        json.dump(cls.config, cls.temp_file)
        cls.temp_file.close()
        cls.mapper = FieldMapper(cls.temp_file.name)

    @classmethod
    def tearDownClass(cls):
        os.unlink(cls.temp_file.name)

    def test_basic_field_mapping(self):
        """Source fields should map to correct Vault field names."""
        source = {"source_id": "SP-001", "owner_name": "Jane Smith"}
        result = self.mapper.transform(source)
        self.assertEqual(result["legacy_doc_number__c"], "SP-001")
        self.assertEqual(result["document_owner__c"], "Jane Smith")

    def test_value_transformation(self):
        """Mapped values should be transformed (e.g., QA -> Quality)."""
        source = {"department": "QA", "classification": "GMP"}
        result = self.mapper.transform(source)
        self.assertEqual(result["department__c"], "Quality")
        self.assertEqual(result["gxp_classification__c"], "GxP")

    def test_unmapped_value_passes_through(self):
        """Values without a mapping entry should pass through unchanged."""
        source = {"department": "R&D"}
        result = self.mapper.transform(source)
        self.assertEqual(result["department__c"], "R&D")

    def test_default_values_applied(self):
        """Default values should fill in missing required fields."""
        source = {"source_id": "SP-002"}
        result = self.mapper.transform(source)
        self.assertEqual(result["review_cycle_months__c"], "24")
        self.assertEqual(result["gxp_classification__c"], "GxP")

    def test_explicit_value_overrides_default(self):
        """An explicit source value should override the default."""
        source = {"classification": "Non-GxP"}
        result = self.mapper.transform(source)
        self.assertEqual(result["gxp_classification__c"], "Non-GxP")

    def test_training_flag_mapping(self):
        """Boolean-like flags should map correctly."""
        source_yes = {"training_needed": "Y"}
        source_no = {"training_needed": "N"}
        self.assertEqual(self.mapper.transform(source_yes)["training_required__c"], "Yes")
        self.assertEqual(self.mapper.transform(source_no)["training_required__c"], "No")

    def test_empty_source_gets_defaults(self):
        """An empty source record should get all default values."""
        result = self.mapper.transform({})
        self.assertEqual(result["gxp_classification__c"], "GxP")
        self.assertEqual(result["review_cycle_months__c"], "24")

    def test_unmapped_source_fields_ignored(self):
        """Source fields not in field_mappings should not appear in output."""
        source = {"random_field": "random_value", "department": "QC"}
        result = self.mapper.transform(source)
        self.assertNotIn("random_field", result)
        self.assertEqual(result["department__c"], "Laboratory")

    def test_multiple_departments(self):
        """All department value mappings should work correctly."""
        test_cases = [
            ("QA", "Quality"),
            ("QC", "Laboratory"),
            ("Mfg", "Manufacturing"),
            ("EHS", "EHS"),  # No mapping, passes through
        ]
        for source_val, expected in test_cases:
            result = self.mapper.transform({"department": source_val})
            self.assertEqual(
                result["department__c"], expected,
                f"Failed for department={source_val}"
            )


if __name__ == "__main__":
    unittest.main()
