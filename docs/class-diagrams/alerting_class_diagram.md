# Alerting Bounded Context Class Diagrams

This document contains the class diagrams of the **Alerting Bounded Context** in the Edge application, including the unified view and strictly separated views for each layer.

---

## 1. Unified Diagram

```mermaid
---
title: DDD Alerting Bounded Context Class Diagram - Unified
---

classDiagram

namespace interfaces {
    class AlertingApi {
        +get_pending_alert_incidents_for_embedded()
        +acknowledge_alert_incident_event(event_id)
    }
}

namespace application {
    class AlertIncidentEventApplicationService {
        -repository
        +ingest_alert_incident_changed_event(payload)
        +get_pending_for_embedded(hardware_id, limit)
        +acknowledge_for_embedded(event_id, hardware_id)
        -_normalize_payload(payload)
        -_parse_timestamp(value)
        -_to_dict(model)
    }

    class AlertIncidentPoller {
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

    class IngestAlertIncidentEventResult {
        +stored: bool
        +event_id: int
    }
}

namespace domain {
    class AlertIncidentEventRepositoryInterface {
        <<interface>>
        +create_from_integration_payload(payload, received_at)
        +find_pending_for_hardware_id(hardware_id, limit)
        +mark_delivered(event, delivered_at)
        +acknowledge(event_id, hardware_id, acknowledged_at)
    }
}

namespace infrastructure {
    class AlertIncidentEventRepository {
        +create_from_integration_payload(payload, received_at)
        +find_pending_for_hardware_id(hardware_id, limit)
        +mark_delivered(event, delivered_at)
        +acknowledge(event_id, hardware_id, acknowledged_at)
    }

    class AlertIncidentEventModel {
        +id
        +hardware_id
        +alert_id
        +device_id
        +space_id
        +metric
        +status
        +message
        +threshold_value
        +actual_value
        +occurred_at
        +resolved_at
        +received_at
        +delivered_at
        +acknowledged_at
    }

    class CoreHttpClient {
        -base_url
        -token
        -timeout
        -opener
        +get(path, params)
        +post(path, body, accept_conflict)
    }
}

%% Relationships
AlertingApi --> AlertIncidentEventApplicationService : uses

AlertIncidentPoller --> AlertIncidentEventApplicationService : uses
AlertIncidentPoller --> CoreHttpClient : uses

AlertIncidentEventApplicationService --> IngestAlertIncidentEventResult : returns
AlertIncidentEventApplicationService --> AlertIncidentEventRepositoryInterface : uses

AlertIncidentEventRepository ..|> AlertIncidentEventRepositoryInterface : implements
AlertIncidentEventRepository --> AlertIncidentEventModel : maps
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
    class AlertingApi {
        +get_pending_alert_incidents_for_embedded()
        +acknowledge_alert_incident_event(event_id)
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
    class AlertIncidentEventApplicationService {
        -repository
        +ingest_alert_incident_changed_event(payload)
        +get_pending_for_embedded(hardware_id, limit)
        +acknowledge_for_embedded(event_id, hardware_id)
        -_normalize_payload(payload)
        -_parse_timestamp(value)
        -_to_dict(model)
    }

    class AlertIncidentPoller {
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

    class IngestAlertIncidentEventResult {
        +stored: bool
        +event_id: int
    }
}

%% Relationships strictly inside Application Layer
AlertIncidentPoller --> AlertIncidentEventApplicationService : uses
AlertIncidentEventApplicationService --> IngestAlertIncidentEventResult : returns
```

---

## 3. Domain Layer

```mermaid
---
title: Domain Layer Class Diagram
---
classDiagram
namespace domain {
    class AlertIncidentEventRepositoryInterface {
        <<interface>>
        +create_from_integration_payload(payload, received_at)
        +find_pending_for_hardware_id(hardware_id, limit)
        +mark_delivered(event, delivered_at)
        +acknowledge(event_id, hardware_id, acknowledged_at)
    }
}
```

---

## 4. Infrastructure Layer

```mermaid
---
title: Infrastructure Layer Class Diagram
---
classDiagram
namespace infrastructure {
    class AlertIncidentEventRepository {
        +create_from_integration_payload(payload, received_at)
        +find_pending_for_hardware_id(hardware_id, limit)
        +mark_delivered(event, delivered_at)
        +acknowledge(event_id, hardware_id, acknowledged_at)
    }

    class AlertIncidentEventModel {
        +id
        +hardware_id
        +alert_id
        +device_id
        +space_id
        +metric
        +status
        +message
        +threshold_value
        +actual_value
        +occurred_at
        +resolved_at
        +received_at
        +delivered_at
        +acknowledged_at
    }

    class CoreHttpClient {
        -base_url
        -token
        -timeout
        -opener
        +get(path, params)
        +post(path, body, accept_conflict)
    }
}

%% Relationships strictly inside Infrastructure Layer
AlertIncidentEventRepository --> AlertIncidentEventModel : maps
```
