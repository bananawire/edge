# Provisioning Bounded Context Class Diagrams

This document contains the class diagrams of the **Provisioning Bounded Context** in the Edge application, including the unified view and strictly separated views for each layer.

---

## 1. Unified Diagram

```mermaid
---
title: DDD Provisioning Bounded Context Class Diagram - Unified
---

classDiagram

namespace interfaces {
    class DeviceCacheResource {
        +device_id: str
        +hardware_id: str
        +api_key: str
        +status: str
        +from_dict(payload)
        +to_command_record()
    }
}

namespace application {
    class DeviceProvisioningApplicationService {
        -device_cache_repository
        -device_cache_service
        +handle_device_changed_event(payload)
        -_normalize_payload(payload)
    }

    class DeviceRosterPoller {
        -client
        -service
        -watermark
        -_running
        -_thread
        -_trigger
        +start()
        +stop()
        +trigger()
        +sync_once()
        -_run()
    }

    class DeviceChangedIntegrationEvent {
        +device_id: str
        +hardware_id: str
        +api_key: str
        +status: str
        +change_type: str
        +changed_at: str
    }

    class DevicesSyncRequestedIntegrationEvent {
        +edge_instance_id: str
        +requested_at: str
    }
}

namespace domain {
    class DeviceCacheService {
        +validate_device_record(device)
    }

    class DeviceCacheRepositoryInterface {
        <<interface>>
        +upsert_many(devices)
    }
}

namespace infrastructure {
    class DeviceCacheRepository {
        +upsert_many(devices)
    }

    class DeviceModel {
        +device_id
        +hardware_id
        +api_key
        +status
        +created_at
        +last_seen_at
    }

    class CoreHttpClient {
        -base_url
        -token
        -timeout
        -opener
        +get(path, params)
        +post(path, body, accept_conflict)
    }

    class SyncWatermarkModel {
        +resource
        +value
    }
}

%% Relationships
DeviceRosterPoller --> DeviceProvisioningApplicationService : uses
DeviceRosterPoller --> CoreHttpClient : uses
DeviceRosterPoller --> SyncWatermarkModel : reads/writes

DeviceProvisioningApplicationService --> DeviceCacheService : uses
DeviceProvisioningApplicationService --> DeviceCacheRepositoryInterface : uses

DeviceCacheRepository ..|> DeviceCacheRepositoryInterface : implements
DeviceCacheRepository --> DeviceModel : maps
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
    class DeviceCacheResource {
        +device_id: str
        +hardware_id: str
        +api_key: str
        +status: str
        +from_dict(payload)
        +to_command_record()
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
    class DeviceProvisioningApplicationService {
        -device_cache_repository
        -device_cache_service
        +handle_device_changed_event(payload)
        -_normalize_payload(payload)
    }

    class DeviceRosterPoller {
        -client
        -service
        -watermark
        -_running
        -_thread
        -_trigger
        +start()
        +stop()
        +trigger()
        +sync_once()
        -_run()
    }

    class DeviceChangedIntegrationEvent {
        +device_id: str
        +hardware_id: str
        +api_key: str
        +status: str
        +change_type: str
        +changed_at: str
    }

    class DevicesSyncRequestedIntegrationEvent {
        +edge_instance_id: str
        +requested_at: str
    }
}

%% Relationships strictly inside Application Layer
DeviceRosterPoller --> DeviceProvisioningApplicationService : uses
```

---

## 3. Domain Layer

```mermaid
---
title: Domain Layer Class Diagram
---
classDiagram
namespace domain {
    class DeviceCacheService {
        +validate_device_record(device)
    }

    class DeviceCacheRepositoryInterface {
        <<interface>>
        +upsert_many(devices)
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
    class DeviceCacheRepository {
        +upsert_many(devices)
    }

    class DeviceModel {
        +device_id
        +hardware_id
        +api_key
        +status
        +created_at
        +last_seen_at
    }

    class CoreHttpClient {
        -base_url
        -token
        -timeout
        -opener
        +get(path, params)
        +post(path, body, accept_conflict)
    }

    class SyncWatermarkModel {
        +resource
        +value
    }
}

%% Relationships strictly inside Infrastructure Layer
DeviceCacheRepository --> DeviceModel : maps
```
