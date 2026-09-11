"""Device-hop telemetry ingestion against a real in-memory libSQL database."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from device.application.services import DeviceTelemetryAppService
from device.domain.commands import CreateFullTelemetryRecordCommand
from device.domain.errors import TelemetryConflictError
from device.domain.services import DeviceTelemetryService
from device.infrastructure.models import DeviceTelemetryModel
from device.infrastructure.outbox.outbox_record_model import OutboxRecordModel
from device.infrastructure.outbox.outbox_payload_snapshot_model import OutboxPayloadSnapshotModel
from iam.infrastructure.models import DeviceModel

READING_ID = "40e67f87-2c0a-47ef-a3ed-7999e106cc9c"


def _device(hardware_id="CLAIR-0001"):
    DeviceModel.create(device_id="dev-1", hardware_id=hardware_id, api_key="k", status="ONLINE",
                       created_at=datetime.now(timezone.utc))


def _command(**overrides):
    base = dict(
        hardware_id="CLAIR-0001", device_time="14:30:25", uptime="01:00:00",
        air_quality={"co2": 812.5, "temperature": 22.5, "humidity": 45.0},
        particulate_matter={"pm1_0": 3.2, "pm2_5": 8.05, "pm10": 12.4},
        connectivity={"status": "connected", "network": "room-wifi", "signalStrength": -50},
        location={"country": "PERU"}, health_status=100, status="Optimal",
        reading_id=READING_ID, measured_at="2026-09-11T14:30:25.123Z",
    )
    base.update(overrides)
    return CreateFullTelemetryRecordCommand(**base)


class TestIdempotentIngestion:
    def test_first_submission_stores_reading_and_contract_snapshot(self, db):
        _device()
        result = DeviceTelemetryAppService().ingest(_command())
        assert result.duplicate is False
        assert result.record.reading_id == READING_ID
        assert result.record.time_source == "device"
        assert result.record.recorded_at == datetime(2026, 9, 11, 14, 30, 25, 123000, tzinfo=timezone.utc)
        assert result.record.received_at is not None
        row = DeviceTelemetryModel.get()
        assert row.pm2_5 == 8.05  # decimals preserved through persistence
        snapshot = json.loads(OutboxPayloadSnapshotModel.get().payload)
        assert snapshot["reading_id"] == READING_ID
        assert snapshot["occurred_at"] == "2026-09-11T14:30:25.123000+00:00"
        assert snapshot["pm2_5"] == 8.05
        assert "device_time" not in snapshot

    def test_exact_retry_returns_existing_without_second_row_or_outbox(self, db):
        _device()
        service = DeviceTelemetryAppService()
        first = service.ingest(_command())
        second = service.ingest(_command())
        assert second.duplicate is True
        assert second.record.id == first.record.id
        assert DeviceTelemetryModel.select().count() == 1
        assert OutboxRecordModel.select().count() == 1

    def test_changed_payload_under_same_identity_conflicts(self, db):
        _device()
        service = DeviceTelemetryAppService()
        service.ingest(_command())
        with pytest.raises(TelemetryConflictError):
            service.ingest(_command(air_quality={"co2": 900.0, "temperature": 22.5, "humidity": 45.0}))
        assert DeviceTelemetryModel.select().count() == 1

    def test_same_reading_id_on_another_device_is_a_different_reading(self, db):
        _device("CLAIR-0001")
        DeviceModel.create(device_id="dev-2", hardware_id="CLAIR-0002", api_key="k2", status="ONLINE",
                           created_at=datetime.now(timezone.utc))
        service = DeviceTelemetryAppService()
        service.ingest(_command())
        service.ingest(_command(hardware_id="CLAIR-0002"))
        assert DeviceTelemetryModel.select().count() == 2

    def test_unknown_device_is_rejected_before_anything_is_written(self, db):
        with pytest.raises(ValueError, match="Device not found"):
            DeviceTelemetryAppService().ingest(_command())
        assert DeviceTelemetryModel.select().count() == 0


class TestValidation:
    def test_missing_sensor_field_is_an_error_not_a_zero(self):
        service = DeviceTelemetryService()
        with pytest.raises(ValueError, match="Missing particulate matter"):
            service.create_record_from_command(_command(particulate_matter={"pm1_0": 1, "pm2_5": 2}))
        with pytest.raises(ValueError, match="Missing air quality"):
            service.create_record_from_command(_command(air_quality={"co2": None, "temperature": 1, "humidity": 1}))

    def test_non_finite_and_out_of_range_values_are_rejected(self):
        service = DeviceTelemetryService()
        with pytest.raises(ValueError, match="finite"):
            service.create_record_from_command(_command(air_quality={"co2": float("nan"), "temperature": 1, "humidity": 1}))
        with pytest.raises(ValueError, match="between"):
            service.create_record_from_command(_command(particulate_matter={"pm1_0": 1, "pm2_5": 5000, "pm10": 1}))
        with pytest.raises(ValueError, match="health_status"):
            service.create_record_from_command(_command(health_status=150))

    def test_measured_at_without_offset_or_malformed_is_rejected(self):
        service = DeviceTelemetryService()
        with pytest.raises(ValueError, match="UTC offset"):
            service.create_record_from_command(_command(measured_at="2026-09-11T14:30:25"))
        with pytest.raises(ValueError, match="Invalid measured_at"):
            service.create_record_from_command(_command(measured_at="14:30:25"))

    def test_bad_reading_id_is_rejected(self):
        with pytest.raises(ValueError, match="reading_id must be a UUID"):
            DeviceTelemetryService().create_record_from_command(_command(reading_id="not-a-uuid"))


class TestLegacyClients:
    def test_legacy_client_gets_an_edge_minted_id_and_receipt_time_flagged(self):
        clock = lambda: datetime(2026, 9, 11, 15, 0, tzinfo=timezone.utc)
        record = DeviceTelemetryService(clock=clock).create_record_from_command(
            _command(reading_id=None, measured_at=None))
        assert len(record.reading_id) == 36
        assert record.time_source == "edge_receipt"
        assert record.recorded_at == clock()

    def test_strict_mode_rejects_a_missing_measurement_instant(self):
        with pytest.raises(ValueError, match="measured_at is required"):
            DeviceTelemetryService(require_measured_at=True).create_record_from_command(
                _command(reading_id=None, measured_at=None))
