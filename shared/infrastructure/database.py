"""Turso (libSQL) database configuration and initialization.

Provides the shared ``TursoDatabase`` instance and ``init_db()`` function
that creates all tables across bounded contexts.

In production the database is the remote Turso instance identified by
``EDGE_TURSO_URL`` + ``EDGE_TURSO_TOKEN``. When ``EDGE_TURSO_URL`` is empty the
same client falls back to the local libSQL file at ``EDGE_DATABASE_PATH`` so
unit tests and offline development keep working without an internet
connection.
"""

from shared.infrastructure.environment import (
    get_edge_database_path,
    get_edge_turso_token,
    get_edge_turso_url,
)
from shared.infrastructure.turso_database import TursoDatabase

_turso_url = get_edge_turso_url()
_turso_token = get_edge_turso_token()

if _turso_url:
    db = TursoDatabase(database=_turso_url, auth_token=_turso_token)
else:
    # Local-file fallback. The libsql client is SQLite-compatible, so the
    # same schema/DDL code paths work against either target. We pass an empty
    # auth_token explicitly so the libsql.connect call stays a keyword call.
    db = TursoDatabase(database=get_edge_database_path(), auth_token="")


def _migrate_device_cache_schema():
    """Add roster columns without dropping data from existing edge databases.

    Uses Peewee's portable ``db.get_tables``/``db.get_columns`` introspection
    rather than raw ``PRAGMA``/``sqlite_master`` SQL so the migration works
    against both a local libSQL file and a remote Turso database.
    """
    if "devices" not in db.get_tables():
        return  # Nothing to migrate; create_tables() will build the table.
    existing = {c.name for c in db.get_columns("devices")}
    if "deleted" not in existing:
        db.execute_sql(
            "ALTER TABLE devices ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0"
        )
    if "updated_at" not in existing:
        db.execute_sql("ALTER TABLE devices ADD COLUMN updated_at DATETIME")


def _migrate_telemetry_schema():
    """Recreate ``device_telemetry`` if its schema is stale.

    The optimized payload no longer sends ``deviceHealth``/``deviceInfo``/
    detailed connectivity fields. If legacy columns are detected, or the
    required new columns are missing, we drop the table so Peewee can
    recreate the clean schema on startup. ``db.drop_tables(safe=True)`` is
    portable across the local libSQL file and remote Turso; the raw
    ``DROP TABLE`` SQL is not used anywhere.
    """
    if "device_telemetry" not in db.get_tables():
        return  # Nothing to migrate; create_tables() will build the table.
    column_names = {c.name for c in db.get_columns("device_telemetry")}

    has_legacy_columns = bool(
        {"wifi_ssid", "free_heap", "chip_model", "air_quality_valid", "pm_valid"}
        & column_names
    )
    missing_required = not {"signal_strength", "health_status"} <= column_names

    if has_legacy_columns or missing_required:
        from device.infrastructure.models import DeviceTelemetryModel
        db.drop_tables([DeviceTelemetryModel], safe=True)


def init_db():
    """Initialize the database by creating all tables if they don't exist.

    Uses deferred imports to avoid circular dependencies between
    bounded context modules.
    """
    db.connect(reuse_if_open=True)
    try:
        # Deferred imports to avoid circular dependencies
        from iam.infrastructure.models import DeviceModel
        from device.infrastructure.models import DeviceCommandModel, DeviceTelemetryModel
        from device.infrastructure.outbox.outbox_record_model import OutboxRecordModel
        from device.infrastructure.outbox.outbox_payload_snapshot_model import OutboxPayloadSnapshotModel
        from alerting.infrastructure.models import AlertIncidentEventModel
        from shared.infrastructure.models import SyncWatermarkModel

        _migrate_device_cache_schema()
        _migrate_telemetry_schema()
        db.create_tables(
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
    finally:
        db.close()