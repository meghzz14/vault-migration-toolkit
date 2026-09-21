#!/usr/bin/env python3
"""
Vault Migration Toolkit — CLI Entry Point
==========================================
Command-line interface for running document migrations to Veeva Vault.

Usage:
    python main.py migrate --manifest ../sample_data/migration_manifest.csv --dry-run
    python main.py migrate --manifest ../sample_data/migration_manifest.csv
    python main.py query --vql "SELECT id, name__v FROM documents WHERE status__v = 'Effective'"
    python main.py verify --report ../migration_logs/reconciliation_report_*.json
"""

import argparse
import json
import logging
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from vault_client import VaultClient, VaultAuthError, VaultAPIError
from migration_engine import MigrationEngine, FieldMapper, SAMPLE_VQL_QUERIES

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("vault_migration.log")
    ]
)
logger = logging.getLogger(__name__)


def load_vault_config(config_path: str = "../config/vault_config.json") -> dict:
    """Load Vault connection configuration."""
    try:
        with open(config_path, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning(f"Config file not found: {config_path}. Using environment variables.")
        return {
            "vault_url": os.environ.get("VAULT_URL", "https://novapharma-sandbox.veevavault.com"),
            "api_version": os.environ.get("VAULT_API_VERSION", "v24.1"),
            "username": os.environ.get("VAULT_USERNAME", ""),
            "password": os.environ.get("VAULT_PASSWORD", "")
        }


def cmd_migrate(args):
    """Execute document migration."""
    config = load_vault_config(args.config)

    # Initialize Vault client
    client = VaultClient(
        vault_url=config["vault_url"],
        api_version=config.get("api_version", "v24.1")
    )

    if not args.dry_run:
        try:
            client.authenticate(
                username=config.get("username", ""),
                password=config.get("password", "")
            )
        except VaultAuthError as e:
            logger.error(f"Authentication failed: {e}")
            sys.exit(1)

    # Initialize field mapper
    mapper = FieldMapper(args.mapping or "../config/field_mapping.json")

    # Initialize and run migration engine
    engine = MigrationEngine(
        vault_client=client,
        field_mapper=mapper,
        source_base_path=args.source_path or "../sample_data/source_files",
        log_dir=args.log_dir or "../migration_logs"
    )

    summary = engine.run_migration(
        manifest_path=args.manifest,
        dry_run=args.dry_run
    )

    # Print summary
    print("\n" + "=" * 60)
    print("MIGRATION SUMMARY")
    print("=" * 60)
    print(f"  Total Documents:  {summary.total_documents}")
    print(f"  Successful:       {summary.successful}")
    print(f"  Failed:           {summary.failed}")
    print(f"  Skipped:          {summary.skipped}")
    print(f"  Duration:         {summary.duration_seconds:.1f} seconds")
    print("=" * 60)

    if summary.failed > 0:
        print(f"\n  ERRORS ({summary.failed}):")
        for err in summary.errors[:10]:
            print(f"    - {err['source_name']}: {err['error']}")
        if len(summary.errors) > 10:
            print(f"    ... and {len(summary.errors) - 10} more (see log file)")

    client.close()


def cmd_query(args):
    """Execute a VQL query."""
    config = load_vault_config(args.config)
    client = VaultClient(
        vault_url=config["vault_url"],
        api_version=config.get("api_version", "v24.1")
    )

    try:
        client.authenticate(
            username=config.get("username", ""),
            password=config.get("password", "")
        )
    except VaultAuthError as e:
        logger.error(f"Authentication failed: {e}")
        sys.exit(1)

    # Use provided VQL or select from samples
    vql = args.vql
    if args.sample:
        vql = SAMPLE_VQL_QUERIES.get(args.sample)
        if not vql:
            print(f"Unknown sample query: {args.sample}")
            print(f"Available: {', '.join(SAMPLE_VQL_QUERIES.keys())}")
            sys.exit(1)

    results = client.query(vql)
    print(json.dumps(results, indent=2))
    print(f"\nTotal records: {len(results)}")

    client.close()


def cmd_samples(args):
    """Display sample VQL queries for learning."""
    print("\n" + "=" * 60)
    print("SAMPLE VQL QUERIES FOR VEEVA VAULT")
    print("=" * 60)

    for name, query in SAMPLE_VQL_QUERIES.items():
        print(f"\n--- {name} ---")
        print(query.strip())
        print()


def cmd_verify(args):
    """Verify migration results from a reconciliation report."""
    with open(args.report, 'r') as f:
        report = json.load(f)

    summary = report.get("summary", {})
    print("\n" + "=" * 60)
    print("MIGRATION VERIFICATION REPORT")
    print("=" * 60)
    print(f"  Generated:     {report.get('generated_at', 'N/A')}")
    print(f"  Source:        {report.get('source_system', 'N/A')}")
    print(f"  Target:        {report.get('target_system', 'N/A')}")
    print(f"  Total:         {summary.get('total_documents', 0)}")
    print(f"  Successful:    {summary.get('successful', 0)}")
    print(f"  Failed:        {summary.get('failed', 0)}")
    print()

    # Document type breakdown
    print("  BY DOCUMENT TYPE:")
    for doc_type, counts in report.get("by_document_type", {}).items():
        status = "PASS" if counts["failed"] == 0 else "FAIL"
        print(f"    [{status}] {doc_type}: "
              f"{counts['success']}/{counts['total']} migrated")

    # Checksum validation
    checksums = report.get("checksum_validation", {})
    print(f"\n  CHECKSUM VALIDATION:")
    print(f"    Documents checked: {checksums.get('documents_checked', 0)}")
    print(f"    Mismatches:        {checksums.get('mismatches', 0)}")

    if summary.get("failed", 0) > 0:
        print(f"\n  FAILED DOCUMENTS:")
        for err in report.get("failed_documents", [])[:10]:
            print(f"    - [{err.get('source_id')}] {err.get('source_name')}")
            print(f"      Error: {err.get('error')}")

    overall = "PASS" if summary.get("failed", 0) == 0 else "FAIL"
    print(f"\n  OVERALL: {overall}")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Veeva Vault Document Migration Toolkit",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry run (validate and transform, no upload)
  python main.py migrate --manifest ../sample_data/migration_manifest.csv --dry-run

  # Full migration
  python main.py migrate --manifest ../sample_data/migration_manifest.csv

  # Run a VQL query
  python main.py query --vql "SELECT id, name__v FROM documents LIMIT 10"

  # View sample VQL queries
  python main.py samples

  # Verify migration results
  python main.py verify --report ../migration_logs/reconciliation_report_*.json
        """
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # migrate command
    migrate_parser = subparsers.add_parser("migrate", help="Run document migration")
    migrate_parser.add_argument("--manifest", required=True, help="Path to CSV manifest")
    migrate_parser.add_argument("--mapping", help="Path to field mapping JSON")
    migrate_parser.add_argument("--config", default="../config/vault_config.json", help="Vault config file")
    migrate_parser.add_argument("--source-path", help="Base path for source files")
    migrate_parser.add_argument("--log-dir", help="Directory for migration logs")
    migrate_parser.add_argument("--dry-run", action="store_true", help="Validate without uploading")
    migrate_parser.set_defaults(func=cmd_migrate)

    # query command
    query_parser = subparsers.add_parser("query", help="Execute a VQL query")
    query_parser.add_argument("--vql", help="VQL query string")
    query_parser.add_argument("--sample", help="Name of a sample query to run")
    query_parser.add_argument("--config", default="../config/vault_config.json", help="Vault config file")
    query_parser.set_defaults(func=cmd_query)

    # samples command
    samples_parser = subparsers.add_parser("samples", help="Show sample VQL queries")
    samples_parser.set_defaults(func=cmd_samples)

    # verify command
    verify_parser = subparsers.add_parser("verify", help="Verify migration from report")
    verify_parser.add_argument("--report", required=True, help="Path to reconciliation JSON")
    verify_parser.set_defaults(func=cmd_verify)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()
