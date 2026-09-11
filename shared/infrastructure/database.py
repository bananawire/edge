"""Turso (libSQL) database configuration and initialization.

Provides the shared ``TursoDatabase`` instance and ``init_db()`` function
that creates all tables across bounded contexts.

In production the database is the remote Turso instance identified by
``EDGE_TURSO_URL`` + ``EDGE_TURSO_TOKEN``. When ``EDGE_TURSO_URL`` is empty the
same client falls back to the local libSQL file at ``EDGE_DATABASE_PATH`` so
unit tests and offline development keep working without an internet
connection.
"""

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from shared.infrastructure.environment import (
    get_edge_database_path,
    get_edge_turso_token,
    get_edge_turso_url,
)
from shared.infrastructure.turso_database import TursoDatabase

logger = logging.getLogger(__name__)

_turso_url = get_edge_turso_url()
_turso_token = get_edge_turso_token()

if _turso_url:
    db = TursoDatabase(database=_turso_url, auth_token=_turso_token)
else:
    # Local-file fallback. The libsql client is SQLite-compatible, so the
    # same schema/DDL code paths work against either target. We pass an empty
    # auth_token explicitly so the libsql.connect call stays a keyword call.
    db = TursoDatabase(database=get_edge_database_path(), auth_token="")


ADDITIVE_COLUMNS = {
    "devices": {
        "assignment_id": "ALTER TABLE devices ADD COLUMN assignment_id VARCHAR(255)",
    },
    "device_commands": {
        "assignment_id": "ALTER TABLE device_commands ADD COLUMN assignment_id VARCHAR(255)",
    },
    "alert_incident_events": {
        "sequence": "ALTER TABLE alert_incident_events ADD COLUMN sequence INTEGER",
    },
}


def _migrate_additive_columns(database):
    """Add contract-v1 columns to whichever of these tables already exist; rows are untouched."""
    tables = set(database.get_tables())
    applied = []
    for table, columns in ADDITIVE_COLUMNS.items():
        if table not in tables:
            continue
        existing = {c.name for c in database.get_columns(table)}
        for column, ddl in columns.items():
            if column not in existing:
                database.execute_sql(ddl)
                applied.append(f"{table}.{column}")
    return applied


def _migrate_device_cache_schema(database):
    """Add roster columns without dropping data from existing edge databases."""
    if "devices" not in database.get_tables():
        return []
    existing = {c.name for c in database.get_columns("devices")}
    applied = []
    if "deleted" not in existing:
        database.execute_sql("ALTER TABLE devices ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0")
        applied.append("devices.deleted")
    if "updated_at" not in existing:
        database.execute_sql("ALTER TABLE devices ADD COLUMN updated_at DATETIME")
        applied.append("devices.updated_at")
    return applied


LEGACY_TELEMETRY_COLUMNS = {"wifi_ssid", "free_heap", "chip_model", "air_quality_valid", "pm_valid"}
TELEMETRY_ADDITIVE_COLUMNS = {
    "reading_id": "ALTER TABLE device_telemetry ADD COLUMN reading_id VARCHAR(255)",
    "received_at": "ALTER TABLE device_telemetry ADD COLUMN received_at DATETIME",
    "time_source": "ALTER TABLE device_telemetry ADD COLUMN time_source VARCHAR(255) NOT NULL DEFAULT 'legacy'",
    "network_name": "ALTER TABLE device_telemetry ADD COLUMN network_name VARCHAR(255) NOT NULL DEFAULT ''",
    "signal_strength": "ALTER TABLE device_telemetry ADD COLUMN signal_strength INTEGER NOT NULL DEFAULT 0",
    "country": "ALTER TABLE device_telemetry ADD COLUMN country VARCHAR(255) NOT NULL DEFAULT ''",
    "health_status": "ALTER TABLE device_telemetry ADD COLUMN health_status INTEGER NOT NULL DEFAULT 100",
}


def _migrate_telemetry_schema(database):
    """Bring ``device_telemetry`` to the current schema without losing rows.

    Missing columns are added in place. A table from the pre-optimised payload
    (legacy columns present) cannot be mapped onto the current aggregate, so it
    is renamed to ``device_telemetry_legacy_<utc stamp>`` and left for manual
    inspection; a fresh table is created next to it. Nothing is dropped.
    Legacy rows keep ``reading_id`` NULL and ``time_source = 'legacy'`` so they
    are never mistaken for contract-v1 readings.
    """
    if "device_telemetry" not in database.get_tables():
        return []
    column_names = {c.name for c in database.get_columns("device_telemetry")}
    applied = []
    if LEGACY_TELEMETRY_COLUMNS & column_names:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        quarantine = f"device_telemetry_legacy_{stamp}"
        database.execute_sql(f'ALTER TABLE device_telemetry RENAME TO "{quarantine}"')
        logger.warning("Quarantined incompatible telemetry table as %s; no rows were deleted", quarantine)
        return [f"device_telemetry -> {quarantine}"]
    for column, ddl in TELEMETRY_ADDITIVE_COLUMNS.items():
        if column not in column_names:
            database.execute_sql(ddl)
            applied.append(f"device_telemetry.{column}")
    return applied


def _ensure_telemetry_identity_index(database):
    """Unique (device_id, reading_id); NULL reading ids of legacy rows never collide."""
    database.execute_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS device_telemetry_device_id_reading_id "
        "ON device_telemetry (device_id, reading_id)"
    )


def _backup_local_file(database_path: str) -> str | None:
    """Copy the SQLite file next to itself before a schema change; returns the copy's path."""
    if not database_path or database_path == ":memory:" or database_path.startswith(("libsql://", "https://")):
        return None
    source = Path(database_path)
    if not source.is_file():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    target = source.with_name(f"{source.name}.bak-{stamp}")
    shutil.copy2(source, target)
    return str(target)


def _needs_migration(database) -> bool:
    tables = set(database.get_tables())
    if "devices" in tables:
        existing = {c.name for c in database.get_columns("devices")}
        if not {"deleted", "updated_at"} <= existing:
            return True
    if "device_telemetry" in tables:
        columns = {c.name for c in database.get_columns("device_telemetry")}
        if LEGACY_TELEMETRY_COLUMNS & columns or not set(TELEMETRY_ADDITIVE_COLUMNS) <= columns:
            return True
    for table, columns in ADDITIVE_COLUMNS.items():
        if table in tables and not set(columns) <= {c.name for c in database.get_columns(table)}:
            return True
    return False


def apply_schema(database, database_path: str | None = None):
    """Migrate an open database in place, then create whatever is still missing.

    Shared by ``init_db`` and the test fixture so both run the same steps.
    Returns the list of applied migration labels.
    """
    from iam.infrastructure.models import DeviceModel
    from device.infrastructure.models import DeviceCommandModel, DeviceTelemetryModel
    from device.infrastructure.outbox.outbox_record_model import OutboxRecordModel
    from device.infrastructure.outbox.outbox_payload_snapshot_model import OutboxPayloadSnapshotModel
    from alerting.infrastructure.models import AlertIncidentEventModel
    from shared.infrastructure.models import SyncWatermarkModel

    applied = []
    if database_path and _needs_migration(database):
        backup = _backup_local_file(database_path)
        if backup:
            logger.info("Backed up edge database to %s before migration", backup)
    with database.atomic():
        applied += _migrate_device_cache_schema(database)
        applied += _migrate_telemetry_schema(database)
        applied += _migrate_additive_columns(database)
        database.create_tables(
            [
                DeviceModel,
                DeviceTelemetryModel,
                DeviceCommandModel,
                OutboxRecordModel,
                OutboxPayloadSnapshotModel,
                AlertIncidentEventModel,
                SyncWatermarkModel,
            ],
            safe=True,
        )
        _ensure_telemetry_identity_index(database)
        database.execute_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS alert_incident_events_alert_id_sequence "
            "ON alert_incident_events (alert_id, sequence)"
        )
    if applied:
        logger.info("Edge schema migrations applied: %s", ", ".join(applied))
    return applied


def init_db():
    """Initialize the database: migrate in place, then create missing tables."""
    db.connect(reuse_if_open=True)
    try:
        apply_schema(db, None if _turso_url else get_edge_database_path())
    finally:
        db.close()
