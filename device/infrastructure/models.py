"""Peewee ORM model for the device_telemetry table.

Maps the DeviceTelemetry aggregate root to the SQLite 'device_telemetry' table
with only the fields present in the optimized embedded device payload.
"""

from peewee import (
    AutoField,
    CharField,
    DateTimeField,
    FloatField,
    IntegerField,
    Model,
)

from shared.infrastructure.database import db


class DeviceTelemetryModel(Model):
    """Peewee model for the 'device_telemetry' table.

    ``recorded_at`` is the measurement instant (contract name ``measured_at``);
    ``received_at`` is edge receipt metadata. ``reading_id`` is unique per
    device so a device-hop retry is recognised. Legacy rows may carry NULLs
    in the newer columns; SQLite treats NULLs as distinct in unique indexes.
    """

    id = AutoField()

    device_id = CharField(index=True)
    reading_id = CharField(null=True)

    device_time = CharField()          # e.g., "14:30:25" (display only)
    uptime_seconds = IntegerField()

    co2 = FloatField()
    temperature = FloatField()
    humidity = FloatField()

    pm1_0 = FloatField()
    pm2_5 = FloatField()
    pm10 = FloatField()

    wifi_status = CharField()
    network_name = CharField(default="")
    signal_strength = IntegerField(default=0)

    country = CharField(default="")

    health_status = IntegerField(default=100)

    status = CharField()

    recorded_at = DateTimeField()
    received_at = DateTimeField(null=True)
    time_source = CharField(default="device")

    class Meta:
        database = db
        table_name = 'device_telemetry'
        indexes = (
            (('device_id', 'recorded_at'), False),
            (('device_id', 'reading_id'), True),
        )


class DeviceCommandModel(Model):
    """Peewee model representing edge-local device commands."""

    command_id = CharField(primary_key=True)
    device_id = CharField(index=True)
    hardware_id = CharField(index=True)
    command_type = CharField()
    status = CharField(index=True)
    payload = CharField(null=True)
    received_at = DateTimeField()
    delivered_at = DateTimeField(null=True)
    failure_reason = CharField(null=True)

    class Meta:
        database = db
        table_name = 'device_commands'
        indexes = (
            (('hardware_id', 'status'), False),
        )
