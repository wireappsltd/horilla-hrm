"""
horilla_retention

ISO 27001 / Sri Lanka HR data-retention engine for Horilla.

Configurable per-company retention periods per record category, flag-and-anonymize
workflow with a configurable grace period, full audit trail. Mirrors the HR Data
Retention and Disposal Procedure V1 (24 Mar 2026).
"""

default_app_config = "horilla_retention.apps.HorillaRetentionConfig"
