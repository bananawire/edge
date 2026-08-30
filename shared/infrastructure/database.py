"""SQLite database configuration and initialization.

Provides the shared SqliteDatabase instance and init_db() function
that creates all tables across bounded contexts.
"""

from peewee import SqliteDatabase

from shared.infrastructure.environment import get_edge_database_path

db = SqliteDatabase(get_edge_database_path())


def _migrate_remove_device_secret():
    """Remove the legacy device_secret column from the devices table.

    Renamed to api_key; this migration cleans up the old column.
    If the database engine does not support DROP COLUMN, the entire
    table is dropped and recreated (data will be re-synced from HTTP).
    """
    from peewee import OperationalError

    cursor = db.execute_sql(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='devices'"
    )
    if not cursor.fetchone():
        return

    col_cursor = db.execute_sql("PRAGMA table_info(devices)")
    columns = {row[1] for row in col_cursor.fetchall()}

    if "device_secret" not in columns:
        return  # Already clean

    try:
        db.execute_sql("ALTER TABLE devices DROP COLUMN device_secret")
    except OperationalError:
        # SQLite < 3.35.0 does not support DROP COLUMN.
        # Drop the whole table; data will be re-synced from HTTP.
        db.execute_sql("DROP TABLE IF EXISTS devices")


def _migrate_telemetry_schema():
    """Recreate device_telemetry table if it still uses the legacy full schema.

    The optimized payload no longer sends deviceHealth, deviceInfo, or detailed
    connectivity fields. If the old columns are detected, the legacy table is
    dropped so Peewee can create the clean new schema on startup.
    Also recreates if new required columns (signal_strength, health_status) are missing.
    """
    cursor = db.execute_sql(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='device_telemetry'"
    )
    if not cursor.fetchone():
        return

    # Detect legacy column that does not exist in the optimized schema
    col_cursor = db.execute_sql("PRAGMA table_info(device_telemetry)")
    columns = {row[1] for row in col_cursor.fetchall()}
    
    # Drop if legacy columns exist
    if "wifi_ssid" in columns or "free_heap" in columns or "chip_model" in columns:
        db.execute_sql("DROP TABLE IF EXISTS device_telemetry")
        return
    
    # Drop if removed columns still exist
    if "air_quality_valid" in columns or "pm_valid" in columns:
        db.execute_sql("DROP TABLE IF EXISTS device_telemetry")
        return
    
    # Drop if new required columns are missing
    if "signal_strength" not in columns or "health_status" not in columns:
        db.execute_sql("DROP TABLE IF EXISTS device_telemetry")


def _migrate_device_cache_schema():
    """Add roster columns without dropping data from existing edge databases."""
    cursor = db.execute_sql(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='devices'"
    )
    if not cursor.fetchone():
        return
    columns = {row[1] for row in db.execute_sql("PRAGMA table_info(devices)").fetchall()}
    if "deleted" not in columns:
        db.execute_sql("ALTER TABLE devices ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0")
    if "updated_at" not in columns:
        db.execute_sql("ALTER TABLE devices ADD COLUMN updated_at DATETIME")


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

        _migrate_remove_device_secret()
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
