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
    """Peewee model representing the 'device_telemetry' table in SQLite.

    Stores optimized telemetry readings with only the fields sent by
    the embedded device: environmental sensors, connectivity status,
    location, health status, and overall device state.
    """

    id = AutoField()

    device_id = CharField(index=True)

    device_time = CharField()          # e.g., "14:30:25"
    uptime_seconds = IntegerField()    # Parsed from HH:MM:SS

    co2 = FloatField()
    temperature = FloatField()
    humidity = FloatField()

    pm1_0 = IntegerField()
    pm2_5 = IntegerField()
    pm10 = IntegerField()

    wifi_status = CharField()
    network_name = CharField(default="")     # WiFi network/SSID (e.g., "Wokwi-GUEST")
    signal_strength = IntegerField(default=0)  # WiFi signal strength in dBm (e.g., -65)

    country = CharField(default="")          # Device location country (e.g., "PERU")

    health_status = IntegerField(default=100)  # Device health status percentage (0-100)

    status = CharField()

    recorded_at = DateTimeField()

    class Meta:
        database = db
        table_name = 'device_telemetry'
        indexes = (
            (('device_id', 'recorded_at'), False),
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
