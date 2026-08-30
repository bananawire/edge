# IAM Bounded Context Class Diagrams

This document contains the class diagrams of the **IAM Bounded Context** in the Edge application, including the unified view and strictly separated views for each layer.

---

## 1. Unified Diagram

```mermaid
---
title: DDD IAM Bounded Context Class Diagram - Unified
---

classDiagram

namespace interfaces {
    class IamApi {
        +authenticate_request(update_last_seen)
    }
}

namespace application {
    class AuthApplicationService {
        -device_repository
        -auth_service
        +authenticate(hardware_id, api_key)
    }

    class DevicePresenceApplicationService {
        -device_repository
        -core_presence_publisher
        +mark_seen(hardware_id)
        +mark_stale_devices_offline(offline_before)
        -_publish_presence(device, occurred_at)
    }

    class DevicePresenceMonitor {
        -device_presence_service
        +start()
        +stop()
        -_run()
    }

    class CorePresenceHttpPublisher {
        -base_url
        -token
        -timeout
        -_opener
        +publish_device_presence_changed(payload)
    }
}

namespace domain {
    class Device {
        +device_id
        +hardware_id
        +api_key
        +status
        +created_at
        +last_seen_at
    }

    class AuthService {
        +authenticate(device)
    }

    class DevicePresenceChangedEvent {
        +device_id
        +hardware_id
        +status
        +occurred_at
    }

    class DeviceRepositoryInterface {
        <<interface>>
        +find_by_hardware_id_and_api_key(hardware_id, api_key)
        +update_last_seen(hardware_id)
        +mark_offline_stale_devices(offline_before)
        +find_by_hardware_id(hardware_id)
        +find_by_device_id(device_id)
    }
}

namespace infrastructure {
    class DeviceRepository {
        +find_by_hardware_id_and_api_key(hardware_id, api_key)
        +update_last_seen(hardware_id)
        +mark_offline_stale_devices(offline_before)
        +find_by_hardware_id(hardware_id)
        +find_by_device_id(device_id)
    }

    class DeviceModel {
        +device_id
        +hardware_id
        +api_key
        +status
        +created_at
        +last_seen_at
    }

}

%% Relationships
IamApi --> AuthApplicationService : uses
IamApi --> DevicePresenceApplicationService : uses

DevicePresenceMonitor --> DevicePresenceApplicationService : uses

AuthApplicationService --> AuthService : uses
AuthApplicationService --> DeviceRepositoryInterface : uses

DevicePresenceApplicationService --> DeviceRepositoryInterface : uses
DevicePresenceApplicationService --> CorePresenceHttpPublisher : uses
DevicePresenceApplicationService --> DevicePresenceChangedEvent : instantiates

AuthService --> Device : validates

DeviceRepository ..|> DeviceRepositoryInterface : implements
DeviceRepository --> DeviceModel : maps
DeviceRepository --> Device : returns

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
    class IamApi {
        +authenticate_request(update_last_seen)
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
    class AuthApplicationService {
        -device_repository
        -auth_service
        +authenticate(hardware_id, api_key)
    }

    class DevicePresenceApplicationService {
        -device_repository
        -core_presence_publisher
        +mark_seen(hardware_id)
        +mark_stale_devices_offline(offline_before)
        -_publish_presence(device, occurred_at)
    }

    class DevicePresenceMonitor {
        -device_presence_service
        +start()
        +stop()
        -_run()
    }

    class CorePresenceHttpPublisher {
        -base_url
        -token
        -timeout
        -_opener
        +publish_device_presence_changed(payload)
    }
}

%% Relationships strictly inside Application Layer
DevicePresenceMonitor --> DevicePresenceApplicationService : uses
DevicePresenceApplicationService --> CorePresenceHttpPublisher : uses
```

---

## 3. Domain Layer

```mermaid
---
title: Domain Layer Class Diagram
---
classDiagram
namespace domain {
    class Device {
        +device_id
        +hardware_id
        +api_key
        +status
        +created_at
        +last_seen_at
    }

    class AuthService {
        +authenticate(device)
    }

    class DevicePresenceChangedEvent {
        +device_id
        +hardware_id
        +status
        +occurred_at
    }

    class DeviceRepositoryInterface {
        <<interface>>
        +find_by_hardware_id_and_api_key(hardware_id, api_key)
        +update_last_seen(hardware_id)
        +mark_offline_stale_devices(offline_before)
        +find_by_hardware_id(hardware_id)
        +find_by_device_id(device_id)
    }
}

%% Relationships strictly inside Domain Layer
AuthService --> Device : validates
```

---

## 4. Infrastructure Layer

```mermaid
---
title: Infrastructure Layer Class Diagram
---
classDiagram
namespace infrastructure {
    class DeviceRepository {
        +find_by_hardware_id_and_api_key(hardware_id, api_key)
        +update_last_seen(hardware_id)
        +mark_offline_stale_devices(offline_before)
        +find_by_hardware_id(hardware_id)
        +find_by_device_id(device_id)
    }

    class DeviceModel {
        +device_id
        +hardware_id
        +api_key
        +status
        +created_at
        +last_seen_at
    }

}

%% Relationships strictly inside Infrastructure Layer
DeviceRepository --> DeviceModel : maps
```
