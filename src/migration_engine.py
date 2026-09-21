"""
Vault Document Migration Engine
================================
Orchestrates the end-to-end migration of documents from a legacy DMS
(e.g., SharePoint, Documentum, OpenText) to Veeva Vault QualityDocs.

This engine handles:
- Reading source data from CSV mapping files
- Transforming metadata to Vault field names
- Uploading documents via Vault REST API
- Tracking migration status and generating reconciliation reports
"""

import csv
import json
import logging
import hashlib
import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from pathlib import Path
from dataclasses import dataclass, field, asdict

from vault_client import VaultClient, VaultAPIError

logger = logging.getLogger(__name__)


@dataclass
class MigrationRecord:
    """Tracks the status of a single document migration."""
    source_id: str
    source_path: str
    source_name: str
    target_doc_type: str
    target_doc_subtype: str
    vault_doc_id: Optional[int] = None
    vault_doc_number: Optional[str] = None
    status: str = "PENDING"  # PENDING, IN_PROGRESS, SUCCESS, FAILED, SKIPPED
    error_message: str = ""
    source_checksum: str = ""
    target_checksum: str = ""
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    metadata_mapped: Dict = field(default_factory=dict)


@dataclass
class MigrationSummary:
    """Summary statistics for a migration run."""
    total_documents: int = 0
    successful: int = 0
    failed: int = 0
    skipped: int = 0
    start_time: str = ""
    end_time: str = ""
    duration_seconds: float = 0
    errors: List[Dict] = field(default_factory=list)


class FieldMapper:
    """
    Maps source system metadata fields to Vault target fields.

    Reads from a field mapping configuration file that defines:
    - Source field name -> Vault field name
    - Value transformations (e.g., department name mapping)
    - Default values for required Vault fields
    """

    def __init__(self, mapping_config_path: str):
        with open(mapping_config_path, 'r') as f:
            self.config = json.load(f)

        self.field_map = self.config.get("field_mappings", {})
        self.value_maps = self.config.get("value_mappings", {})
        self.defaults = self.config.get("default_values", {})

    def transform(self, source_record: Dict) -> Dict:
        """
        Transform a source record's metadata to Vault field names and values.

        Args:
            source_record: Dict of source field name -> value

        Returns:
            Dict of Vault field name -> transformed value
        """
        vault_metadata = {}

        # Apply field mappings
        for source_field, vault_field in self.field_map.items():
            if source_field in source_record:
                value = source_record[source_field]

                # Apply value transformation if defined
                if vault_field in self.value_maps:
                    value = self.value_maps[vault_field].get(value, value)

                vault_metadata[vault_field] = value

        # Apply default values for missing required fields
        for vault_field, default_value in self.defaults.items():
            if vault_field not in vault_metadata or not vault_metadata[vault_field]:
                vault_metadata[vault_field] = default_value

        return vault_metadata


class MigrationEngine:
    """
    Core migration engine that orchestrates document migration to Vault.

    Workflow:
    1. Load source data from CSV manifest
    2. For each document:
       a. Read source file and compute checksum
       b. Transform metadata using field mappings
       c. Upload to Vault via REST API
       d. Verify upload (checksum comparison)
       e. Log result to migration tracker
    3. Generate reconciliation report
    """

    def __init__(
        self,
        vault_client: VaultClient,
        field_mapper: FieldMapper,
        source_base_path: str,
        log_dir: str = "./migration_logs"
    ):
        self.client = vault_client
        self.mapper = field_mapper
        self.source_base_path = Path(source_base_path)
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.records: List[MigrationRecord] = []
        self.summary = MigrationSummary()

    def load_manifest(self, manifest_path: str) -> List[Dict]:
        """
        Load the migration manifest CSV file.

        The manifest contains one row per document to migrate with columns:
        - source_id: Unique ID in the source system
        - source_path: Relative file path
        - source_name: Document name/title
        - doc_type: Target Vault document type
        - doc_subtype: Target Vault document subtype
        - [additional source metadata columns mapped via FieldMapper]
        """
        documents = []
        with open(manifest_path, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                documents.append(row)

        logger.info(f"Loaded {len(documents)} documents from manifest")
        return documents

    @staticmethod
    def compute_checksum(file_path: str) -> str:
        """Compute MD5 checksum of a file for integrity verification."""
        md5 = hashlib.md5()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                md5.update(chunk)
        return md5.hexdigest()

    def _migrate_single_document(self, source_doc: Dict) -> MigrationRecord:
        """
        Migrate a single document from source to Vault.

        Args:
            source_doc: Dictionary from the manifest CSV

        Returns:
            MigrationRecord with result details
        """
        record = MigrationRecord(
            source_id=source_doc.get("source_id", ""),
            source_path=source_doc.get("source_path", ""),
            source_name=source_doc.get("source_name", ""),
            target_doc_type=source_doc.get("doc_type", ""),
            target_doc_subtype=source_doc.get("doc_subtype", ""),
            started_at=datetime.now().isoformat()
        )
        record.status = "IN_PROGRESS"

        try:
            # Step 1: Locate and validate source file
            file_path = self.source_base_path / record.source_path
            if not file_path.exists():
                record.status = "FAILED"
                record.error_message = f"Source file not found: {file_path}"
                return record

            # Step 2: Compute source checksum
            record.source_checksum = self.compute_checksum(str(file_path))

            # Step 3: Transform metadata
            vault_metadata = self.mapper.transform(source_doc)
            vault_metadata["name__v"] = record.source_name
            record.metadata_mapped = vault_metadata

            # Step 4: Create document in Vault
            result = self.client.create_document(
                doc_type=record.target_doc_type,
                doc_subtype=record.target_doc_subtype,
                metadata=vault_metadata,
                file_path=str(file_path)
            )

            record.vault_doc_id = result.get("id")
            record.vault_doc_number = result.get("document_number__v", "")
            record.status = "SUCCESS"

            logger.info(
                f"Migrated: {record.source_name} -> Vault ID {record.vault_doc_id}"
            )

        except VaultAPIError as e:
            record.status = "FAILED"
            record.error_message = str(e)
            logger.error(f"Failed to migrate {record.source_name}: {e}")

        except Exception as e:
            record.status = "FAILED"
            record.error_message = f"Unexpected error: {str(e)}"
            logger.error(f"Unexpected error migrating {record.source_name}: {e}")

        finally:
            record.completed_at = datetime.now().isoformat()

        return record

    def run_migration(self, manifest_path: str, dry_run: bool = False) -> MigrationSummary:
        """
        Execute the full migration from a manifest file.

        Args:
            manifest_path: Path to the CSV manifest
            dry_run: If True, validate and transform but don't upload

        Returns:
            MigrationSummary with statistics
        """
        self.summary.start_time = datetime.now().isoformat()
        documents = self.load_manifest(manifest_path)
        self.summary.total_documents = len(documents)

        logger.info(f"Starting migration of {len(documents)} documents"
                    f"{' (DRY RUN)' if dry_run else ''}")

        for i, doc in enumerate(documents):
            logger.info(f"Processing {i + 1}/{len(documents)}: "
                       f"{doc.get('source_name', 'unknown')}")

            if dry_run:
                # In dry run, only validate and transform
                record = MigrationRecord(
                    source_id=doc.get("source_id", ""),
                    source_path=doc.get("source_path", ""),
                    source_name=doc.get("source_name", ""),
                    target_doc_type=doc.get("doc_type", ""),
                    target_doc_subtype=doc.get("doc_subtype", ""),
                    metadata_mapped=self.mapper.transform(doc),
                    status="DRY_RUN"
                )
            else:
                record = self._migrate_single_document(doc)

            self.records.append(record)

            # Update summary counts
            if record.status == "SUCCESS":
                self.summary.successful += 1
            elif record.status == "FAILED":
                self.summary.failed += 1
                self.summary.errors.append({
                    "source_id": record.source_id,
                    "source_name": record.source_name,
                    "error": record.error_message
                })
            elif record.status == "SKIPPED":
                self.summary.skipped += 1

        self.summary.end_time = datetime.now().isoformat()

        # Calculate duration
        start = datetime.fromisoformat(self.summary.start_time)
        end = datetime.fromisoformat(self.summary.end_time)
        self.summary.duration_seconds = (end - start).total_seconds()

        # Save logs
        self._save_migration_log()
        self._save_reconciliation_report()

        logger.info(
            f"Migration complete: {self.summary.successful} succeeded, "
            f"{self.summary.failed} failed, {self.summary.skipped} skipped "
            f"({self.summary.duration_seconds:.1f}s)"
        )

        return self.summary

    def _save_migration_log(self):
        """Save detailed migration log as CSV."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = self.log_dir / f"migration_log_{timestamp}.csv"

        with open(log_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                "Source ID", "Source Name", "Source Path", "Target Type",
                "Target Subtype", "Vault Doc ID", "Vault Doc Number",
                "Status", "Error Message", "Source Checksum",
                "Started At", "Completed At"
            ])
            for r in self.records:
                writer.writerow([
                    r.source_id, r.source_name, r.source_path,
                    r.target_doc_type, r.target_doc_subtype,
                    r.vault_doc_id, r.vault_doc_number,
                    r.status, r.error_message, r.source_checksum,
                    r.started_at, r.completed_at
                ])

        logger.info(f"Migration log saved: {log_path}")

    def _save_reconciliation_report(self):
        """Save a reconciliation report comparing source and target counts."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = self.log_dir / f"reconciliation_report_{timestamp}.json"

        report = {
            "report_title": "Document Migration Reconciliation Report",
            "generated_at": datetime.now().isoformat(),
            "source_system": "SharePoint DMS",
            "target_system": "Veeva Vault QualityDocs",
            "summary": asdict(self.summary),
            "by_document_type": self._count_by_type(),
            "by_status": {
                "SUCCESS": self.summary.successful,
                "FAILED": self.summary.failed,
                "SKIPPED": self.summary.skipped,
                "PENDING": sum(1 for r in self.records if r.status == "PENDING"),
            },
            "failed_documents": self.summary.errors[:50],  # Cap at 50 for readability
            "checksum_validation": {
                "documents_checked": sum(
                    1 for r in self.records if r.source_checksum
                ),
                "mismatches": sum(
                    1 for r in self.records
                    if r.source_checksum and r.target_checksum
                    and r.source_checksum != r.target_checksum
                ),
            }
        }

        with open(report_path, 'w') as f:
            json.dump(report, f, indent=2)

        logger.info(f"Reconciliation report saved: {report_path}")

    def _count_by_type(self) -> Dict[str, Dict[str, int]]:
        """Count migration results grouped by document type."""
        type_counts = {}
        for r in self.records:
            key = f"{r.target_doc_type}/{r.target_doc_subtype}"
            if key not in type_counts:
                type_counts[key] = {"total": 0, "success": 0, "failed": 0}
            type_counts[key]["total"] += 1
            if r.status == "SUCCESS":
                type_counts[key]["success"] += 1
            elif r.status == "FAILED":
                type_counts[key]["failed"] += 1
        return type_counts


# =============================================================================
# VQL QUERY EXAMPLES (for reference and study)
# =============================================================================

SAMPLE_VQL_QUERIES = {
    "all_effective_sops": """
        SELECT id, name__v, document_number__v, status__v,
               department__c, document_owner__c, effective_date__c
        FROM documents
        WHERE type__v = 'sop__c'
          AND status__v = 'Effective'
        ORDER BY document_number__v ASC
    """,

    "documents_by_department": """
        SELECT id, name__v, type__v, subtype__v, status__v, department__c
        FROM documents
        WHERE department__c = 'Quality'
          AND status__v IN ('Draft', 'In Review', 'Effective')
        ORDER BY name__v ASC
    """,

    "overdue_periodic_reviews": """
        SELECT id, name__v, document_number__v, document_owner__c,
               review_cycle_months__c, effective_date__c
        FROM documents
        WHERE status__v = 'Effective'
          AND type__v IN ('sop__c', 'work_instruction__c', 'policy__c')
        ORDER BY effective_date__c ASC
    """,

    "documents_pending_approval": """
        SELECT id, name__v, document_number__v, status__v,
               department__c, document_owner__c
        FROM documents
        WHERE status__v = 'In Approval'
        ORDER BY status__v ASC
    """,

    "recently_superseded": """
        SELECT id, name__v, document_number__v, version_modified_date__v
        FROM documents
        WHERE status__v = 'Superseded'
        ORDER BY version_modified_date__v DESC
        LIMIT 50
    """,

    "document_count_by_type_and_status": """
        SELECT type__v, status__v, COUNT(id) as doc_count
        FROM documents
        GROUP BY type__v, status__v
        ORDER BY type__v, status__v
    """,

    "search_by_keyword": """
        SELECT id, name__v, document_number__v, type__v, status__v
        FROM documents
        WHERE name__v CONTAINS 'cleaning validation'
          AND status__v = 'Effective'
    """,

    "migration_verification": """
        SELECT id, name__v, document_number__v, legacy_doc_number__c,
               type__v, subtype__v, status__v, department__c
        FROM documents
        WHERE legacy_doc_number__c != ''
        ORDER BY legacy_doc_number__c ASC
    """
}
