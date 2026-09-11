"""Data-preserving migrations and libSQL durability against a file-backed database."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest
from peewee import IntegrityError

from shared.infrastructure import database as database_module
from shared.infrastructure.turso_database import TursoDatabase


def _bind(test_db):
    """Point every model at ``test_db`` for the duration of a test."""
    import importlib
    names = (
        "iam.infrastructure.models.DeviceModel",
        "device.infrastructure.models.DeviceCommandModel",
        "device.infrastructure.models.DeviceTelemetryModel",
        "device.infrastructure.outbox.outbox_record_model.OutboxRecordModel",
        "device.infrastructure.outbox.outbox_payload_snapshot_model.OutboxPayloadSnapshotModel",
        "alerting.infrastructure.models.AlertIncidentEventModel",
        "shared.infrastructure.models.SyncWatermarkModel",
    )
    originals = {}
    for dotted in names:
        module, attr = dotted.rsplit(".", 1)
        model = getattr(importlib.import_module(module), attr)
        originals[model] = model._meta.database
        model._meta.database = test_db
    return originals


@pytest.fixture
def file_db(tmp_path):
    path = tmp_path / "edge.db"
    test_db = TursoDatabase(database=str(path))
    originals = _bind(test_db)
    try:
        yield test_db, path
    finally:
        for model, original in originals.items():
            model._meta.database = original
        try:
            test_db.close()
        except Exception:
            pass


class TestMigrations:
    def test_old_telemetry_table_gains_columns_and_keeps_rows(self, file_db):
        test_db, path = file_db
        test_db.connect(reuse_if_open=True)
        test_db.execute_sql(
            "CREATE TABLE device_telemetry (id INTEGER PRIMARY KEY, device_id VARCHAR(255), device_time VARCHAR(255),"
            " uptime_seconds INTEGER, co2 REAL, temperature REAL, humidity REAL, pm1_0 INTEGER, pm2_5 INTEGER,"
            " pm10 INTEGER, wifi_status VARCHAR(255), status VARCHAR(255), recorded_at DATETIME)")
        test_db.execute_sql(
            "INSERT INTO device_telemetry (device_id, device_time, uptime_seconds, co2, temperature, humidity,"
            " pm1_0, pm2_5, pm10, wifi_status, status, recorded_at) VALUES ('CLAIR-0001','10:00:00',5,400,20,50,"
            " 1,2,3,'connected','Optimal','2026-09-01 10:00:00')")
        test_db.execute_sql("CREATE TABLE devices (device_id VARCHAR(255) PRIMARY KEY, hardware_id VARCHAR(255),"
                            " api_key VARCHAR(255), status VARCHAR(255), created_at DATETIME, last_seen_at DATETIME)")
        test_db.close()

        test_db.connect(reuse_if_open=True)
        applied = database_module.apply_schema(test_db, str(path))
        assert "device_telemetry.reading_id" in applied and "devices.deleted" in applied
        columns = {c.name for c in test_db.get_columns("device_telemetry")}
        assert {"reading_id", "received_at", "time_source", "health_status"} <= columns
        row = test_db.execute_sql("SELECT device_id, reading_id, time_source FROM device_telemetry").fetchone()
        assert row == ("CLAIR-0001", None, "legacy")
        backups = list(path.parent.glob("edge.db.bak-*"))
        assert len(backups) == 1, "a backup is taken before the first schema change"
        # Second start: nothing to migrate, no new backup.
        assert database_module.apply_schema(test_db, str(path)) == []
        assert len(list(path.parent.glob("edge.db.bak-*"))) == 1

    def test_incompatible_legacy_table_is_quarantined_not_dropped(self, file_db):
        test_db, path = file_db
        test_db.connect(reuse_if_open=True)
        test_db.execute_sql("CREATE TABLE device_telemetry (id INTEGER PRIMARY KEY, device_id VARCHAR(255),"
                            " wifi_ssid VARCHAR(255), free_heap INTEGER, recorded_at DATETIME)")
        test_db.execute_sql("INSERT INTO device_telemetry (device_id, wifi_ssid, free_heap, recorded_at)"
                            " VALUES ('CLAIR-0001','net',1,'2026-01-01 00:00:00')")
        applied = database_module.apply_schema(test_db, str(path))
        assert any(a.startswith("device_telemetry -> device_telemetry_legacy_") for a in applied)
        tables = set(test_db.get_tables())
        legacy = [t for t in tables if t.startswith("device_telemetry_legacy_")]
        assert len(legacy) == 1 and "device_telemetry" in tables
        assert test_db.execute_sql(f'SELECT count(*) FROM "{legacy[0]}"').fetchone()[0] == 1
        assert test_db.execute_sql("SELECT count(*) FROM device_telemetry").fetchone()[0] == 0

    def test_identity_index_rejects_duplicate_reading_for_a_device(self, file_db):
        test_db, path = file_db
        test_db.connect(reuse_if_open=True)
        database_module.apply_schema(test_db, str(path))
        from device.infrastructure.models import DeviceTelemetryModel
        values = dict(device_id="CLAIR-0001", reading_id="r1", device_time="t", uptime_seconds=1, co2=1, temperature=1,
                      humidity=1, pm1_0=1, pm2_5=1, pm10=1, wifi_status="c", status="s",
                      recorded_at=datetime.now(timezone.utc))
        DeviceTelemetryModel.create(**values)
        with pytest.raises(IntegrityError):
            DeviceTelemetryModel.create(**values)
        # Legacy rows have NULL identities and never collide with each other.
        DeviceTelemetryModel.create(**{**values, "reading_id": None})
        DeviceTelemetryModel.create(**{**values, "reading_id": None})


class TestLibsqlDurability:
    def test_commit_survives_close_and_reopen(self, file_db):
        test_db, path = file_db
        test_db.connect(reuse_if_open=True)
        database_module.apply_schema(test_db, str(path))
        from shared.infrastructure.models import SyncWatermarkModel
        with test_db.atomic():
            SyncWatermarkModel.create(resource="devices", value="w1")
        test_db.close()
        reopened = TursoDatabase(database=str(path))
        reopened.connect()
        try:
            assert reopened.execute_sql("SELECT value FROM sync_watermark").fetchone() == ("w1",)
        finally:
            reopened.close()

    def test_writes_outside_atomic_are_committed_before_close(self, file_db):
        """Regression: the adapter used to leave an implicit transaction open and lose these rows."""
        test_db, path = file_db
        test_db.connect(reuse_if_open=True)
        database_module.apply_schema(test_db, str(path))
        from shared.infrastructure.models import SyncWatermarkModel
        SyncWatermarkModel.create(resource="devices", value="plain")
        SyncWatermarkModel.update(value="updated").where(SyncWatermarkModel.resource == "devices").execute()
        test_db.close()
        reopened = TursoDatabase(database=str(path))
        reopened.connect()
        try:
            assert reopened.execute_sql("SELECT value FROM sync_watermark").fetchone() == ("updated",)
        finally:
            reopened.close()

    def test_rollback_inside_atomic_discards_partial_work(self, file_db):
        test_db, path = file_db
        test_db.connect(reuse_if_open=True)
        database_module.apply_schema(test_db, str(path))
        from shared.infrastructure.models import SyncWatermarkModel
        with pytest.raises(RuntimeError):
            with test_db.atomic():
                SyncWatermarkModel.create(resource="devices", value="w1")
                raise RuntimeError("boom")
        assert SyncWatermarkModel.select().count() == 0
        # The connection is still usable afterwards.
        with test_db.atomic():
            SyncWatermarkModel.create(resource="devices", value="w2")
        assert SyncWatermarkModel.get().value == "w2"

    def test_writes_from_two_threads_are_all_persisted(self, file_db):
        test_db, path = file_db
        test_db.connect(reuse_if_open=True)
        database_module.apply_schema(test_db, str(path))
        test_db.close()
        errors = []

        def worker(prefix):
            worker_db = TursoDatabase(database=str(path))
            try:
                worker_db.connect()
                for i in range(20):
                    with worker_db.atomic():
                        worker_db.execute_sql(
                            "INSERT INTO sync_watermark (resource, value) VALUES (?, ?)", (f"{prefix}-{i}", "v"))
            except Exception as exc:  # pragma: no cover - surfaced through assertion
                errors.append(exc)
            finally:
                worker_db.close()

        threads = [threading.Thread(target=worker, args=(f"t{n}",)) for n in range(2)]
        for t in threads: t.start()
        for t in threads: t.join(10)
        assert errors == []
        test_db.connect()
        assert test_db.execute_sql("SELECT count(*) FROM sync_watermark").fetchone()[0] == 40


class TestCoreUrlPolicy:
    def test_loopback_http_is_allowed_and_default_matches_core_port(self, monkeypatch):
        from shared.infrastructure.environment import get_core_base_url
        monkeypatch.delenv("CLAIR_CORE_BASE_URL", raising=False)
        assert get_core_base_url() == "http://localhost:49220"

    def test_remote_http_needs_the_explicit_local_network_flag(self, monkeypatch):
        from shared.infrastructure.environment import get_core_base_url
        monkeypatch.setenv("CLAIR_CORE_BASE_URL", "http://clair-core:49220/")
        monkeypatch.delenv("CLAIR_CORE_ALLOW_INSECURE_HTTP", raising=False)
        with pytest.raises(ValueError):
            get_core_base_url()
        monkeypatch.setenv("CLAIR_CORE_ALLOW_INSECURE_HTTP", "true")
        assert get_core_base_url() == "http://clair-core:49220"
        monkeypatch.setenv("CLAIR_CORE_BASE_URL", "https://core.example.com")
        monkeypatch.delenv("CLAIR_CORE_ALLOW_INSECURE_HTTP", raising=False)
        assert get_core_base_url() == "https://core.example.com"
