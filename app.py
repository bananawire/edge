"""Edge Service — Flask application entry point.

Registers bounded-context blueprints, initializes the SQLite database,
starts Kafka topic bootstrapping, and launches background consumers.
"""

from flask import Flask, request
import logging
import os

from dotenv import load_dotenv

load_dotenv()

from device.application.kafka_command_consumer import KafkaCommandConsumer
from device.application.outbox_processor import TelemetryOutboxProcessor
from device.infrastructure.kafka.device_kafka_topics import DeviceKafkaTopics
from device.interfaces.api import device_api
from alerting.infrastructure.kafka.alerting_kafka_topics import AlertingKafkaTopics
from alerting.interfaces.api import alerting_api
from alerting.application.kafka_alert_incident_consumer import KafkaAlertIncidentConsumer
from iam.application.device_presence_monitor import DevicePresenceMonitor
from iam.infrastructure.kafka.iam_kafka_topics import IamKafkaTopics
from iam.interfaces.services import iam_api
from provisioning.application.kafka_provisioning_consumer import KafkaProvisioningConsumer
from provisioning.infrastructure.kafka.provisioning_kafka_topics import ProvisioningKafkaTopics
from shared.infrastructure.database import init_db
from shared.infrastructure.environment import (
    get_edge_cors_allowed_headers,
    get_edge_cors_allowed_origins,
)
from shared.infrastructure.kafka_client import KafkaInfrastructureClient
from shared.interfaces.docs_api import docs_api

app = Flask(__name__)
app.register_blueprint(iam_api)
app.register_blueprint(device_api)
app.register_blueprint(alerting_api)
app.register_blueprint(docs_api)


@app.get("/health")
def health():
    return {"status": "UP"}, 200


logger = logging.getLogger(__name__)

_initialized = False
_outbox_processor = TelemetryOutboxProcessor()
_command_consumer = KafkaCommandConsumer()
_device_presence_monitor = DevicePresenceMonitor()
_provisioning_consumer = KafkaProvisioningConsumer()
_alert_incident_consumer = KafkaAlertIncidentConsumer()


def _collect_all_topics() -> list:
    """Gather topic definitions from every bounded context."""
    return (
        DeviceKafkaTopics.all()
        + AlertingKafkaTopics.all()
        + IamKafkaTopics.all()
        + ProvisioningKafkaTopics.all()
    )


@app.after_request
def add_cors_headers(response):
    """Allow browser clients to call the edge API with device auth headers."""
    allowed_origins = get_edge_cors_allowed_origins()
    request_origin = request.headers.get("Origin")

    if "*" in allowed_origins:
        response.headers["Access-Control-Allow-Origin"] = "*"
    elif request_origin in allowed_origins:
        response.headers["Access-Control-Allow-Origin"] = request_origin
        response.headers.add("Vary", "Origin")

    response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = get_edge_cors_allowed_headers()
    response.headers["Access-Control-Max-Age"] = "86400"
    return response


@app.before_request
def initialize():
    """Initialize database, bootstrap Kafka topics, and start background workers."""
    global _initialized
    if not _initialized:
        init_db()

        # Bootstrap Kafka topics from each bounded context's own registry
        try:
            kafka_client = KafkaInfrastructureClient()
            kafka_client.bootstrap_topics(_collect_all_topics())
        except Exception as exc:
            logger.warning("Kafka topic bootstrap failed: %s", exc)

        _outbox_processor.start()
        _command_consumer.start()
        _provisioning_consumer.start()
        _device_presence_monitor.start()
        _alert_incident_consumer.start()
        _initialized = True


if __name__ == "__main__":
    # Ensure the edge cache is ready even before the first HTTP request.
    initialize()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
