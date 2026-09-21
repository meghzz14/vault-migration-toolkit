# Data Migration Plan: SharePoint to Veeva Vault QualityDocs

**Document ID:** NP-VAULT-MP-001  
**Version:** 1.0  
**Prepared By:** [Your Name]  
**Date:** September 2026  
**Client:** NovaPharma Inc.

---

## 1. Executive Summary

This plan describes the approach for migrating approximately 8,000 controlled documents from NovaPharma's SharePoint-based document management system to Veeva Vault QualityDocs. The migration will be executed in three waves over a 6-week period, with full data reconciliation and validation at each stage.

## 2. Migration Scope

| Category | Count (Approx.) | Priority |
|---|---|---|
| SOPs (Quality, Manufacturing, Laboratory) | 2,500 | Wave 1 |
| Work Instructions | 1,200 | Wave 1 |
| Policies | 300 | Wave 1 |
| Forms (Batch Records, Deviation, Change Control) | 1,800 | Wave 2 |
| Specifications (Raw Material, Finished Product) | 800 | Wave 2 |
| Reports (Validation, Annual Product Review) | 1,400 | Wave 3 |
| **Total** | **~8,000** | |

### In Scope
- All documents currently in Effective state (migrated as Effective)
- All documents in Obsolete state (migrated as Obsolete for retention)
- Version history: current version + 2 prior versions
- Document metadata (mapped per field_mapping.json configuration)
- Legacy document numbers preserved in custom field

### Out of Scope
- Documents in Draft state (authors will re-create in Vault)
- Documents in archive/deleted folders (unless required for compliance)
- Non-controlled departmental documents
- File attachments linked to SharePoint list items

## 3. Migration Approach

### 3.1 Tools

| Tool | Purpose |
|---|---|
| **Python Migration Toolkit** (this project) | Automated document upload via Vault REST API |
| **Vault Loader** | Bulk metadata updates and corrections post-migration |
| **VQL Queries** | Post-migration verification and reconciliation |
| **SharePoint PnP PowerShell** | Source data extraction from SharePoint |

### 3.2 Migration Steps

**Step 1: Source Data Extraction**  
Export document metadata from SharePoint into CSV format. Download all source files to a staging directory organized by document type.

**Step 2: Data Cleansing**  
Review and clean source data for inconsistencies: standardize department names, normalize date formats, resolve missing metadata, and flag duplicate documents.

**Step 3: Field Mapping Validation**  
Run the migration toolkit in `--dry-run` mode to validate that all source records can be successfully transformed using the field_mapping.json configuration.

**Step 4: Sandbox Migration (Wave 1)**  
Execute migration against the Vault Sandbox environment for Wave 1 documents (SOPs, WIs, Policies). Validate results using VQL queries and reconciliation report.

**Step 5: UAT Verification**  
QA team verifies a sample of migrated documents in the Pre-Production environment, checking metadata accuracy, file content, and version history.

**Step 6: Production Cutover**  
Execute final migration against Production Vault during a scheduled maintenance window. Verify with reconciliation report.

## 4. Field Mapping Summary

| Source Field (SharePoint) | Target Field (Vault) | Transformation |
|---|---|---|
| Document ID | legacy_doc_number__c | Direct copy |
| Title | name__v | Direct copy |
| Department | department__c | Value map (QA→Quality, QC→Laboratory, etc.) |
| Document Owner | document_owner__c | Direct copy |
| GxP Classification | gxp_classification__c | Value map (GMP/GLP→GxP) |
| Effective Date | effective_date__c | Date format: YYYY-MM-DD |
| Review Cycle | review_cycle_months__c | Numeric (months) |
| Product | product__c | Direct copy |
| Site | site__c | Value map (HQ→NovaPharma HQ - Boston, etc.) |
| Confidentiality | confidentiality__c | Direct copy |
| Training Required | training_required__c | Value map (Y→Yes, N→No) |
| Change Control # | change_control_number__c | Direct copy |

## 5. Validation and Reconciliation

### 5.1 Pre-Migration Checks
- Source document count by type matches manifest
- All source files exist and are accessible
- No duplicate document IDs in manifest
- All required metadata fields populated

### 5.2 Post-Migration Checks
- **Count Reconciliation**: Target document count = Source document count (per type)
- **Metadata Accuracy**: Sample 10% of documents, verify all metadata fields match
- **Content Integrity**: MD5 checksum comparison between source and uploaded files
- **Version History**: Verify version chain is intact for sampled documents
- **Lifecycle State**: Confirm documents are in the correct lifecycle state
- **Access Control**: Verify documents are accessible to correct roles

### 5.3 VQL Verification Queries

```sql
-- Count migrated documents by type
SELECT type__v, COUNT(id) as doc_count
FROM documents
WHERE legacy_doc_number__c != ''
GROUP BY type__v

-- Verify all SOPs are in Effective state
SELECT id, name__v, legacy_doc_number__c, status__v
FROM documents
WHERE type__v = 'sop__c'
  AND legacy_doc_number__c != ''
  AND status__v != 'Effective'

-- Check for missing metadata
SELECT id, name__v, legacy_doc_number__c
FROM documents
WHERE legacy_doc_number__c != ''
  AND (department__c = '' OR document_owner__c = '' OR site__c = '')
```

## 6. Rollback Strategy

If critical issues are discovered during migration:

1. **Document-level rollback**: Delete individual failed documents from Vault (Draft state only) and re-migrate
2. **Wave-level rollback**: If >5% failure rate in a wave, halt migration, delete all wave documents, fix root cause, and re-execute
3. **Full rollback**: In worst case, Veeva can restore the Vault to a pre-migration snapshot (coordinate with Veeva Support, requires 24–48 hours)

## 7. Timeline

| Week | Activity | Environment |
|---|---|---|
| Week 1 | Source extraction, data cleansing, dry runs | Local |
| Week 2 | Wave 1 migration + verification | Sandbox |
| Week 3 | Wave 2 migration + verification | Sandbox |
| Week 4 | Wave 3 migration + UAT | Pre-Production |
| Week 5 | UAT sign-off, cutover preparation | Pre-Production |
| Week 6 | Production cutover + post-migration verification | Production |

## 8. Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Source data quality issues | Metadata mapping failures | Extensive dry-run testing; dedicated data cleansing phase |
| Vault API rate limits during bulk upload | Slow migration, timeouts | Built-in rate limiting (0.1s delay); batch processing (500/batch) |
| Network interruption during migration | Partial upload, data inconsistency | Idempotent design; resume from last successful record |
| Incorrect field mapping | Wrong metadata in Vault | Dry-run validation; 10% sample verification at each wave |
| Version history loss | Compliance gap | Migrate last 3 versions; verify version chain in UAT |

## 9. Approval

| Role | Name | Signature | Date |
|---|---|---|---|
| Project Manager | | | |
| QA Manager | | | |
| IT Lead | | | |
| Validation Lead | | | |
