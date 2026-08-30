"""Shared persistence models."""
from peewee import CharField, Model

from shared.infrastructure.database import db


class SyncWatermarkModel(Model):
    """Last server watermark successfully applied for a resource."""

    resource = CharField(primary_key=True)
    value = CharField(null=False)

    class Meta:
        database = db
        table_name = "sync_watermark"
