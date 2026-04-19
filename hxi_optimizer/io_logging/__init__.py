"""Crash-safe CSV + immutable audit logging.

Package is named `io_logging` (not `logging`) to avoid shadowing stdlib `logging`.

Contains:
    csv_logger     Background-thread CSV writer with 5 s fsync
    audit_logger   Per-write fsync audit trail for every PLC write attempt
"""
