"""Pytest fixtures for the edge test suite.

Provides a fresh in-memory ``TursoDatabase`` per test so each test starts
with a clean schema. The fixture swaps the production singleton in
``shared.infrastructure.database.db`` so modules that imported ``db``
directly pick up the per-test instance, and rebinds every Peewee model to
the same connection so ``Model.create``/``Model.select`` etc. route to the
test DB.
"""

from __future__ import annotations

import pytest

from shared.infrastructure.turso_database import TursoDatabase


_KNOWN_MODELS = (
    "iam.infrastructure.models.DeviceModel",
    "device.infrastructure.models.DeviceCommandModel",
    "device.infrastructure.models.DeviceTelemetryModel",
    "device.infrastructure.outbox.outbox_record_model.OutboxRecordModel",
    "device.infrastructure.outbox.outbox_payload_snapshot_model.OutboxPayloadSnapshotModel",
    "alerting.infrastructure.models.AlertIncidentEventModel",
    "shared.infrastructure.models.SyncWatermarkModel",
)


def _import_models() -> list:
    """Late import so test collection does not require the full app graph."""
    import importlib

    models = []
    for dotted in _KNOWN_MODELS:
        module_name, attr = dotted.rsplit(".", 1)
        module = importlib.import_module(module_name)
        models.append(getattr(module, attr))
    return models


def _build_test_schema(test_db) -> None:
    """Run the same migrations + ``create_tables`` as production ``init_db``.

    Unlike ``init_db()`` this helper does NOT close the connection: libsql's
    in-memory target is per-connection, so closing between schema setup and
    test body would discard every table.
    """
    test_db.connect(reuse_if_open=True)
    try:
        # Deferred imports to avoid circular dependencies
        from iam.infrastructure.models import DeviceModel
        from device.infrastructure.models import DeviceCommandModel, DeviceTelemetryModel
        from device.infrastructure.outbox.outbox_record_model import OutboxRecordModel
        from device.infrastructure.outbox.outbox_payload_snapshot_model import OutboxPayloadSnapshotModel
        from alerting.infrastructure.models import AlertIncidentEventModel
        from shared.infrastructure.models import SyncWatermarkModel

        # Inline migration logic (mirrors shared.infrastructure.database
        # but keeps the connection open).
        if "devices" in test_db.get_tables():
            existing = {c.name for c in test_db.get_columns("devices")}
            if "deleted" not in existing:
                test_db.execute_sql(
                    "ALTER TABLE devices ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0"
                )
            if "updated_at" not in existing:
                test_db.execute_sql("ALTER TABLE devices ADD COLUMN updated_at DATETIME")

        if "device_telemetry" in test_db.get_tables():
            column_names = {c.name for c in test_db.get_columns("device_telemetry")}
            has_legacy = bool(
                {"wifi_ssid", "free_heap", "chip_model", "air_quality_valid", "pm_valid"}
                & column_names
            )
            missing_required = not {"signal_strength", "health_status"} <= column_names
            if has_legacy or missing_required:
                test_db.drop_tables([DeviceTelemetryModel], safe=True)

        test_db.create_tables(
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
        # Leave the connection open for the duration of the test.
        pass


@pytest.fixture(autouse=True)
def db():
    """Yield a fresh ``TursoDatabase`` against an in-memory libSQL file.

    Applied to every test automatically (autouse=True). The fixture:

    - Creates a new ``TursoDatabase(":memory:")`` for the duration of the test.
    - Replaces ``shared.infrastructure.database.db`` so any code path that
      resolves ``db`` through the module attribute uses the test instance.
    - Rebinds every known Peewee model so direct ``Model.*`` operations route
      to the test DB.
    - Builds the schema on the open connection (the connection MUST stay
      open across the test: libsql's ``:memory:`` target is per-connection).
    - On teardown: closes the test DB, restores the original ``db`` singleton
      and every model's original ``_meta.database``.
    """
    import shared.infrastructure.database as db_module

    original_db = db_module.db
    test_db = TursoDatabase(database=":memory:")

    # Swap module-level singleton so later imports + getattr lookups resolve
    # to the test instance.
    db_module.db = test_db

    # Rebind models whose Meta.database was captured at import time.
    all_models = _import_models()
    original_bindings = {model: model._meta.database for model in all_models}
    for model in all_models:
        model._meta.database = test_db

    try:
        _build_test_schema(test_db)
        yield test_db
    finally:
        try:
            test_db.close()
        except Exception:  # pragma: no cover - defensive cleanup
            pass
        for model, original in original_bindings.items():
            model._meta.database = original
        db_module.db = original_db