# Device Bounded Context Class Diagrams

This document contains the class diagrams of the **Device Bounded Context** in the Edge application, including the unified view and strictly separated views for each layer.

---

## 1. Unified Diagram

```mermaid
---
title: DDD Device Bounded Context Class Diagram - Unified
---

classDiagram

namespace interfaces {
    class DeviceApi {
        +create_telemetry_record()
        +get_pending_device_commands_for_embedded()
        +acknowledge_embedded_device_command(command_id)
        +get_device_connection_status(hardware_id)
    }
}

namespace application {
    class DeviceTelemetryAppService {
        -telemetry_repository
        -telemetry_service
        -device_repository
        -outbox_repository
        +create_full_telemetry_record(command, raw_payload)
    }

    class DeviceCommandApplicationService {
        -command_repository
        -device_repository
        -external_core_service
        +ingest_command_messages(messages)
        +get_pending_commands_for_embedded(hardware_id)
        +acknowledge_embedded_command(command)
    }

    class GetDeviceConnectionStatusQueryHandler {
        -telemetry_repository
        +handle(query)
    }

    class DeviceCommandPoller {
        -client
        -service
        -_running
        -_thread
        -_trigger
        +start()
        +stop()
        +trigger()
        +poll_once()
        -_run()
    }

    class TelemetryOutboxProcessor {
        -outbox_repository
        -telemetry_repository
        -external_core_service
        -circuit_breaker
        +start()
        +stop()
        -_process_batch()
        -_send_entry(entry)
    }

    class CreateFullTelemetryRecordCommand {
        +hardware_id
        +device_time
        +uptime
        +air_quality
        +particulate_matter
        +connectivity
        +location
        +health_status
        +status
        +created_at
    }

    class AcknowledgeEmbeddedDeviceCommandCommand {
        +hardware_id
        +command_id
        +status
        +failure_reason
    }

    class GetDeviceConnectionStatusQuery {
        +hardware_id
    }
}

namespace domain {
    class DeviceTelemetry {
        +id
        +device_id
        +device_time
        +uptime_seconds
        +air_quality : AirQuality
        +particulate_matter : ParticulateMatter
        +connectivity : Connectivity
        +location : Location
        +health_status
        +status
        +recorded_at
    }

    class DeviceCommand {
        +command_id
        +device_id
        +hardware_id
        +command_type
        +status
        +payload
        +received_at
        +delivered_at
        +acknowledged_at
        +failure_reason
        +mark_delivered_to_embedded(delivered_at)
        +mark_executed(acknowledged_at)
        +mark_failed(acknowledged_at, failure_reason)
    }

    class OutboxEntry {
        +id
        +aggregate_type
        +aggregate_id
        +event_type
        +status
        +retry_count
        +next_retry_at
        +created_at
        +sent_at
        +error_message
    }

    class DeviceTelemetryService {
        +create_record_from_command(command)
        -_parse_uptime(uptime)
        -_parse_timestamp(created_at)
    }

    class AirQuality {
        +co2
        +temperature
        +humidity
    }

    class ParticulateMatter {
        +pm1_0
        +pm2_5
        +pm10
    }

    class Connectivity {
        +status
        +network
        +signal_strength
    }

    class Location {
        +country
    }

    class DeviceConnectionStatus {
        +hardware_id
        +status
        +last_seen_at
        +seconds_since_last_seen
    }

    class DeviceTelemetryRepositoryInterface {
        <<interface>>
        +save(telemetry)
        +find_by_id(record_id)
        +find_last_telemetry_by_hardware_id(hardware_id)
    }

    class DeviceCommandRepositoryInterface {
        <<interface>>
        +save(command)
        +find_by_command_id(command_id)
        +find_pending_for_hardware_id(hardware_id)
        +mark_commands_delivered(commands)
    }

    class OutboxRepositoryInterface {
        <<interface>>
        +save(entry)
        +find_pending(limit)
        +mark_sent(entry_id)
        +mark_retry(entry_id, next_retry_at, error)
        +delete_sent_older_than(before)
    }
}

namespace infrastructure {
    class DeviceTelemetryRepository {
        +save(telemetry)
        +find_by_id(record_id)
        +find_last_telemetry_by_hardware_id(hardware_id)
        -_model_to_entity(model)
    }

    class DeviceCommandRepository {
        +save(command)
        +find_by_command_id(command_id)
        +find_pending_for_hardware_id(hardware_id)
        +mark_commands_delivered(commands)
        -_model_to_entity(model)
    }

    class OutboxRepository {
        +save(entry)
        +find_pending(limit)
        +mark_sent(entry_id)
        +mark_retry(entry_id, next_retry_at, error)
        +delete_sent_older_than(before)
        -_model_to_entity(model)
    }

    class DeviceTelemetryModel {
        +id
        +device_id
        +device_time
        +uptime_seconds
        +co2
        +temperature
        +humidity
        +pm1_0
        +pm2_5
        +pm10
        +wifi_status
        +network_name
        +signal_strength
        +country
        +health_status
        +status
        +recorded_at
    }

    class DeviceCommandModel {
        +command_id
        +device_id
        +hardware_id
        +command_type
        +status
        +payload
        +received_at
        +delivered_at
        +acknowledged_at
        +failure_reason
    }

    class OutboxRecordModel {
        +id
        +aggregate_type
        +aggregate_id
        +event_type
        +status
        +retry_count
        +next_retry_at
        +created_at
        +sent_at
        +error_message
    }

    class ExternalCoreService {
        -_facade
        +publish_telemetry_recorded(payload)
        +publish_command_acknowledged(payload)
    }

    class CoreContextFacade {
        <<abstract>>
        +publish_telemetry_recorded(payload)
        +publish_command_acknowledged(payload)
    }

    class HttpCoreContextFacadeImpl {
        -base_url
        -token
        -timeout
        -_opener
        +publish_telemetry_recorded(payload)
        +publish_command_acknowledged(payload)
    }
}

%% Relationships
DeviceApi --> DeviceTelemetryAppService : uses
DeviceApi --> DeviceCommandApplicationService : uses
DeviceApi --> GetDeviceConnectionStatusQueryHandler : uses
DeviceApi --> CreateFullTelemetryRecordCommand : receives
DeviceApi --> AcknowledgeEmbeddedDeviceCommandCommand : receives

DeviceTelemetryAppService --> CreateFullTelemetryRecordCommand : receives
DeviceTelemetryAppService --> DeviceTelemetryService : uses
DeviceTelemetryAppService --> DeviceTelemetry : creates
DeviceTelemetryAppService --> OutboxEntry : creates
DeviceTelemetryAppService --> DeviceTelemetryRepositoryInterface : uses
DeviceTelemetryAppService --> OutboxRepositoryInterface : uses

DeviceCommandApplicationService --> AcknowledgeEmbeddedDeviceCommandCommand : receives
DeviceCommandApplicationService --> DeviceCommand : updates
DeviceCommandApplicationService --> DeviceCommandRepositoryInterface : uses
DeviceCommandApplicationService --> ExternalCoreService : uses

GetDeviceConnectionStatusQueryHandler --> GetDeviceConnectionStatusQuery : receives
GetDeviceConnectionStatusQueryHandler --> DeviceTelemetryRepositoryInterface : uses
GetDeviceConnectionStatusQueryHandler --> DeviceConnectionStatus : returns

DeviceCommandPoller --> DeviceCommandApplicationService : uses
DeviceCommandPoller --> CoreHttpClient : uses
TelemetryOutboxProcessor --> OutboxRepositoryInterface : uses
TelemetryOutboxProcessor --> DeviceTelemetryRepositoryInterface : uses
TelemetryOutboxProcessor --> ExternalCoreService : uses
ExternalCoreService --> CoreContextFacade : uses
HttpCoreContextFacadeImpl ..|> CoreContextFacade : implements

DeviceTelemetryService --> CreateFullTelemetryRecordCommand : receives
DeviceTelemetryService --> DeviceTelemetry : creates
DeviceTelemetryService --> AirQuality : instantiates
DeviceTelemetryService --> ParticulateMatter : instantiates
DeviceTelemetryService --> Connectivity : instantiates
DeviceTelemetryService --> Location : instantiates

DeviceTelemetry *-- AirQuality : contains
DeviceTelemetry *-- ParticulateMatter : contains
DeviceTelemetry *-- Connectivity : contains
DeviceTelemetry *-- Location : contains

%% Interface Implementations
DeviceTelemetryRepository ..|> DeviceTelemetryRepositoryInterface : implements
DeviceCommandRepository ..|> DeviceCommandRepositoryInterface : implements
OutboxRepository ..|> OutboxRepositoryInterface : implements

%% Model mappings
DeviceTelemetryRepository --> DeviceTelemetryModel : maps
DeviceCommandRepository --> DeviceCommandModel : maps
OutboxRepository --> OutboxRecordModel : maps
```

---

## 2. Layer-by-Layer Diagrams

### 2.1. Interfaces Layer

```mermaid
---
title: Interfaces Layer Class Diagram
---
classDiagram
namespace interfaces {
    class DeviceApi {
        +create_telemetry_record()
        +get_pending_device_commands_for_embedded()
        +acknowledge_embedded_device_command(command_id)
        +get_device_connection_status(hardware_id)
    }
}
```

---

### 2.2. Application Layer

```mermaid
---
title: Application Layer Class Diagram
---
classDiagram
namespace application {
    class DeviceTelemetryAppService {
        -telemetry_repository
        -telemetry_service
        -device_repository
        -outbox_repository
        +create_full_telemetry_record(command, raw_payload)
    }

    class DeviceCommandApplicationService {
        -command_repository
        -device_repository
        -external_core_service
        +ingest_command_messages(messages)
        +get_pending_commands_for_embedded(hardware_id)
        +acknowledge_embedded_command(command)
    }

    class GetDeviceConnectionStatusQueryHandler {
        -telemetry_repository
        +handle(query)
    }

    class DeviceCommandPoller {
        -client
        -service
        -_running
        -_thread
        -_trigger
        +start()
        +stop()
        +trigger()
        +poll_once()
        -_run()
    }

    class TelemetryOutboxProcessor {
        -outbox_repository
        -telemetry_repository
        -external_core_service
        -circuit_breaker
        +start()
        +stop()
        -_process_batch()
        -_send_entry(entry)
    }

    class CreateFullTelemetryRecordCommand {
        +hardware_id
        +device_time
        +uptime
        +air_quality
        +particulate_matter
        +connectivity
        +location
        +health_status
        +status
        +created_at
    }

    class AcknowledgeEmbeddedDeviceCommandCommand {
        +hardware_id
        +command_id
        +status
        +failure_reason
    }

    class GetDeviceConnectionStatusQuery {
        +hardware_id
    }
}

%% Relationships strictly inside Application Layer
DeviceTelemetryAppService --> CreateFullTelemetryRecordCommand : receives
DeviceCommandApplicationService --> AcknowledgeEmbeddedDeviceCommandCommand : receives
GetDeviceConnectionStatusQueryHandler --> GetDeviceConnectionStatusQuery : receives
```

---

## 3. Domain Layer

```mermaid
---
title: Domain Layer Class Diagram
---
classDiagram
namespace domain {
    class DeviceTelemetry {
        +id
        +device_id
        +device_time
        +uptime_seconds
        +air_quality : AirQuality
        +particulate_matter : ParticulateMatter
        +connectivity : Connectivity
        +location : Location
        +health_status
        +status
        +recorded_at
    }

    class DeviceCommand {
        +command_id
        +device_id
        +hardware_id
        +command_type
        +status
        +payload
        +received_at
        +delivered_at
        +acknowledged_at
        +failure_reason
        +mark_delivered_to_embedded(delivered_at)
        +mark_executed(acknowledged_at)
        +mark_failed(acknowledged_at, failure_reason)
    }

    class OutboxEntry {
        +id
        +aggregate_type
        +aggregate_id
        +event_type
        +status
        +retry_count
        +next_retry_at
        +created_at
        +sent_at
        +error_message
    }

    class DeviceTelemetryService {
        +create_record_from_command(command)
        -_parse_uptime(uptime)
        -_parse_timestamp(created_at)
    }

    class AirQuality {
        +co2
        +temperature
        +humidity
    }

    class ParticulateMatter {
        +pm1_0
        +pm2_5
        +pm10
    }

    class Connectivity {
        +status
        +network
        +signal_strength
    }

    class Location {
        +country
    }

    class DeviceConnectionStatus {
        +hardware_id
        +status
        +last_seen_at
        +seconds_since_last_seen
    }

    class DeviceTelemetryRepositoryInterface {
        <<interface>>
        +save(telemetry)
        +find_by_id(record_id)
        +find_last_telemetry_by_hardware_id(hardware_id)
    }

    class DeviceCommandRepositoryInterface {
        <<interface>>
        +save(command)
        +find_by_command_id(command_id)
        +find_pending_for_hardware_id(hardware_id)
        +mark_commands_delivered(commands)
    }

    class OutboxRepositoryInterface {
        <<interface>>
        +save(entry)
        +find_pending(limit)
        +mark_sent(entry_id)
        +mark_retry(entry_id, next_retry_at, error)
        +delete_sent_older_than(before)
    }
}

%% Relationships strictly inside Domain Layer
DeviceTelemetry *-- AirQuality : contains
DeviceTelemetry *-- ParticulateMatter : contains
DeviceTelemetry *-- Connectivity : contains
DeviceTelemetry *-- Location : contains

DeviceTelemetryService --> DeviceTelemetry : creates
DeviceTelemetryService --> AirQuality : instantiates
DeviceTelemetryService --> ParticulateMatter : instantiates
DeviceTelemetryService --> Connectivity : instantiates
DeviceTelemetryService --> Location : instantiates
```

---

## 4. Infrastructure Layer

```mermaid
---
title: Infrastructure Layer Class Diagram
---
classDiagram
namespace infrastructure {
    class DeviceTelemetryRepository {
        +save(telemetry)
        +find_by_id(record_id)
        +find_last_telemetry_by_hardware_id(hardware_id)
        -_model_to_entity(model)
    }

    class DeviceCommandRepository {
        +save(command)
        +find_by_command_id(command_id)
        +find_pending_for_hardware_id(hardware_id)
        +mark_commands_delivered(commands)
        -_model_to_entity(model)
    }

    class OutboxRepository {
        +save(entry)
        +find_pending(limit)
        +mark_sent(entry_id)
        +mark_retry(entry_id, next_retry_at, error)
        +delete_sent_older_than(before)
        -_model_to_entity(model)
    }

    class DeviceTelemetryModel {
        +id
        +device_id
        +device_time
        +uptime_seconds
        +co2
        +temperature
        +humidity
        +pm1_0
        +pm2_5
        +pm10
        +wifi_status
        +network_name
        +signal_strength
        +country
        +health_status
        +status
        +recorded_at
    }

    class DeviceCommandModel {
        +command_id
        +device_id
        +hardware_id
        +command_type
        +status
        +payload
        +received_at
        +delivered_at
        +acknowledged_at
        +failure_reason
    }

    class OutboxRecordModel {
        +id
        +aggregate_type
        +aggregate_id
        +event_type
        +status
        +retry_count
        +next_retry_at
        +created_at
        +sent_at
        +error_message
    }

    class ExternalCoreService {
        -_facade
        +publish_telemetry_recorded(payload)
        +publish_command_acknowledged(payload)
    }

    class CoreContextFacade {
        <<abstract>>
        +publish_telemetry_recorded(payload)
        +publish_command_acknowledged(payload)
    }

    class HttpCoreContextFacadeImpl {
        -base_url
        -token
        -timeout
        -_opener
        +publish_telemetry_recorded(payload)
        +publish_command_acknowledged(payload)
    }
}

%% Relationships strictly inside Infrastructure Layer
DeviceTelemetryRepository --> DeviceTelemetryModel : maps
DeviceCommandRepository --> DeviceCommandModel : maps
OutboxRepository --> OutboxRecordModel : maps
```
