# Edge Service — IoT Telemetry Ingestion

Servicio edge para la ingesta de telemetría ambiental (CO2 y PM2.5) desde dispositivos IoT. Valida el estado del dispositivo localmente (cache) y sincroniza telemetría, comandos y presencia con clair-core via HTTP autenticado.

## Stack Tecnológico

| Tecnología | Propósito |
|---|---|
| **Python 3.13** | Lenguaje principal |
| **uv** | Gestor de paquetes y entornos virtuales |
| **Flask** | Framework web para los endpoints REST |
| **Peewee** | ORM ligero para mapear entidades a libSQL |
| **Turso (libSQL)** | Base de datos libSQL gestionada y remota |

## Arquitectura

El proyecto sigue **Domain-Driven Design (DDD)** con dos bounded contexts:

```
edge-service/
├── app.py                         # Punto de entrada Flask
├── iam/                           # Bounded Context: Identity & Access Management
│   ├── domain/
│   │   ├── entities.py            # Entidad Device (aggregate root, atributos sincronizados)
│   │   └── services.py            # AuthService: valida credenciales + status sincronizado
│   ├── application/
│   │   └── services.py            # AuthApplicationService: orquesta autenticación local
│   ├── infrastructure/
│   │   ├── models.py              # DeviceModel (Peewee) → tabla 'devices'
│   │   └── repositories.py        # DeviceRepository: find/update_last_seen
│   └── interfaces/
│       └── services.py            # Blueprint iam_api + authenticate_request()
├── device/                        # Bounded Context: Device Telemetry
│   ├── domain/
│   │   ├── entities.py            # Entidad DeviceTelemetry (CO2, PM2.5)
│   │   └── services.py            # Validación de rangos (CO2: 0-5000, PM2.5: 0-500)
│   ├── application/
│   │   └── services.py            # DeviceTelemetryAppService: orquesta validación y guardado
│   ├── infrastructure/
│   │   ├── models.py              # DeviceTelemetryModel → tabla 'device_telemetry'
│   │   └── repositories.py        # DeviceTelemetryRepository: persistencia
│   └── interfaces/
│       └── api.py                 # Blueprint device_api + POST /api/v1/device/telemetry
├── provisioning/                  # Bounded Context: Device Provisioning
│   ├── application/               # Pollers HTTP + ACL contra clair-core
│   ├── domain/                    # Commands, queries y validación de cache
│   ├── infrastructure/            # Upsert del cache local de devices
│   └── interfaces/                # Recursos HTTP del bounded context
└── shared/                        # Infraestructura compartida
    └── infrastructure/
        ├── database.py            # TursoDatabase(EDGE_TURSO_URL || EDGE_DATABASE_PATH) + init_db()
        └── turso_database.py      # peewee.Database subclass backed by libsql
```

## Requisitos Previos

- **Python 3.13+**
- **uv** (gestor de paquetes)

### Instalar uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Cómo Ejecutar

```bash
# Entrar al directorio del proyecto
cd edge-service

# Sincronizar dependencias (crea .venv automáticamente)
uv sync

# Ejecutar el servicio
uv run python app.py
```

El servidor arranca en `http://127.0.0.1:5000` con debug desactivado.

## Docker (producción)

Construye y ejecuta la imagen multi-stage con uv:

```bash
# Build (BuildKit recomendado para caché de capas uv)
DOCKER_BUILDKIT=1 docker build -t edge-service:dev .

# Run en el puerto 5000 (mismo default que la ejecución directa)
docker run --rm -p 5000:5000 --env-file .env edge-service:dev
```

La imagen resultante:

- Usa Python 3.13-slim-bookworm con uv para resolver dependencias en un `builder` stage.
- Crea un usuario no-root (`edge`) en el stage de runtime.
- Escucha en el puerto `5000` (sobrescribible con `-e PORT=...`).
- Incluye `HEALTHCHECK` contra `GET /health` (intervalo 30 s, 3 reintentos).

Inspecciona logs:

```bash
docker logs -f <container-id>
```

## API Endpoints

### `POST /api/v1/device/telemetry`

Crea un nuevo registro de telemetría ambiental para un dispositivo autenticado.

**Headers requeridos:**

```
Content-Type: application/json
X-Hardware-Id: <hardware-id-del-dispositivo>
X-API-Key: <api-key-del-dispositivo>
```

**Body (JSON)** — contrato v1, ver `clair-core/docs/contracts/edge-v1/device-edge/telemetry.request.json`:

```json
{
  "deviceId": "CLAIR-0001",
  "reading_id": "40e67f87-2c0a-47ef-a3ed-7999e106cc9c",
  "measured_at": "2026-09-11T14:30:25.123Z",
  "timestamp": "14:30:25",
  "uptime": "01:00:00",
  "airQuality": { "co2": 812.5, "temperature": 22.5, "humidity": 45.0 },
  "particulateMatter": { "pm1_0": 3.2, "pm2_5": 8.05, "pm10": 12.4 },
  "connectivity": { "status": "connected", "network": "room-wifi", "signalStrength": -50 },
  "location": { "country": "PERU" },
  "healthStatus": 100,
  "status": "Optimal"
}
```

| Campo | Tipo | Requerido | Descripción |
|---|---|---|---|
| `reading_id` | UUID | Sí (v1) | Identidad estable de la muestra, generada por el firmware. Un reintento exacto devuelve el mismo registro; un cambio bajo la misma identidad responde `409`. |
| `measured_at` | ISO-8601 con offset | Sí (v1) | Instante de medición. Sin offset se rechaza. |
| `airQuality.*`, `particulateMatter.*` | number | Sí | Todos los campos son obligatorios y finitos; nunca se rellenan con cero. PM conserva decimales. |
| `timestamp`, `uptime` | string | Sí | Hora local y uptime del dispositivo, solo informativos. |

Clientes *legacy* (sin `reading_id` ni `measured_at`): el edge acuña un UUID una vez y usa su hora de
recepción marcada como `time_source = "edge_receipt"`. Con `EDGE_REQUIRE_MEASURED_AT=true` esos
envíos se rechazan con `400`.

**Respuestas:**

| Código | Condición | Body |
|---|---|---|
| `201` | Registro almacenado (o reintento exacto, `duplicate: true`) | `{"id": 1842, "reading_id": "...", "device_id": "CLAIR-0001", "measured_at": "...", "received_at": "...", "time_source": "device", "duplicate": false}` |
| `400` | Campos faltantes o valores inválidos | `{"error": "..."}` |
| `401` | Credenciales inválidas o dispositivo no autorizado | `{"error": "..."}` |
| `409` | Mismo `reading_id` con datos distintos | `{"error": "..."}` |

### Probar con curl

```bash
curl -X POST http://127.0.0.1:5000/api/v1/device/telemetry \
  -H 'Content-Type: application/json' \
  -H 'X-Hardware-Id: CLAIR-0001' \
  -H 'X-API-Key: <api-key>' \
  -d '{
    "co2": 420.5,
    "pm25": 35.2,
    "created_at": "2026-05-16T22:30:00-05:00"
  }'
```

## Sincronizacion de Devices

El edge no crea devices de prueba. Obtiene los devices maestros desde `clair-core` mediante el roster HTTP incremental y los cachea en SQLite para validar telemetría localmente.

La sincronización usa polling periódico con watermark persistido; las notificaciones HTTP del core solo aceleran el siguiente ciclo.

Variables relevantes:

| Variable | Default | Descripción |
|---|---|---|
| `EDGE_TURSO_URL` | (vacío) | URL `libsql://...` de la base Turso remota. Si está definida, el edge escribe contra Turso y `EDGE_DATABASE_PATH` se ignora. |
| `EDGE_TURSO_TOKEN` | (vacío) | JWT emitido por `turso db tokens create` para autenticar el cliente libsql contra Turso. Requerido en producción. |
| `EDGE_DATABASE_PATH` | `clair_edge.db` | Fallback local: ruta del archivo libSQL usada solo cuando `EDGE_TURSO_URL` está vacío (tests unitarios, desarrollo offline). |
| `CLAIR_CORE_BASE_URL` | `http://localhost:49220` | URL base de clair-core. HTTP solo para loopback; HTTPS en cualquier otro host. |
| `CLAIR_CORE_ALLOW_INSECURE_HTTP` | `false` | Permite HTTP hacia nombres de red local/contenedor (p. ej. `http://clair-core:49220`) en una red de confianza. |
| `EDGE_REQUIRE_MEASURED_AT` | `false` | Rechaza telemetría sin `measured_at` (activar cuando todo el firmware envíe el contrato v1). |
| `EDGE_OUTBOX_DEAD_LETTER_RETENTION_HOURS` | `168` | Retención de entregas en cuarentena antes de purgarlas. |
| `EDGE_TO_CORE_TOKEN` | (requerido) | Token para llamadas edge → core |
| `EDGE_TOKEN` | (requerido) | Token para notificaciones core → edge |
| `DEVICE_ROSTER_POLL_INTERVAL_SECONDS` | `30` | Intervalo del roster |
| `EDGE_COMMAND_POLL_INTERVAL_SECONDS` | `5` | Intervalo de comandos |
| `EDGE_ALERT_POLL_INTERVAL_SECONDS` | `5` | Intervalo de alertas |
| `EDGE_PRESENCE_POLL_INTERVAL_SECONDS` | `5` | Intervalo de presencia |
| `EDGE_OUTBOX_POLL_INTERVAL_SECONDS` | `5` | Intervalo del outbox |
| `EDGE_PUBLIC_BASE_URL` | `http://127.0.0.1:5000` | Base URL para el OpenAPI `servers` (docs) |

Este proyecto soporta archivo `.env` (cargado al iniciar via `python-dotenv`). Usa `.env.example` como base.

## Operación, migraciones y rollback

`init_db()` aplica migraciones locales idempotentes al arrancar: conserva el
catálogo, añade `deleted`/`updated_at` y crea `sync_watermark`. El roster usa
ese watermark para reanudar sincronización tras una caída; si core no está
disponible, el edge continúa sirviendo con su caché anterior.

Antes de operar en un entorno no local, configura ambos tokens con secretos
fuertes y una URL HTTPS de core. Los intervalos de workers se pueden ajustar
mediante las variables del archivo `.env.example`; valores inválidos o menores
que 0.1 segundos se corrigen de forma segura.

Si el transporte HTTP del outbox falla después del corte, aplica este
rollback acotado, sin borrar datos: (1) detén el proceso del edge; (2) haz una
copia de `EDGE_DATABASE_PATH`; (3) restaura el artefacto edge versionado
anterior que el operador haya identificado como compatible con el contrato
HTTP; (4) inicia el proceso y verifica `/health`; (5) revisa que
`device_outbox` conserve sus entradas pendientes. No se restaura un broker ni
se elimina `device_outbox`: el rollback conserva los pollers independientes y
permite reintentar telemetría cuando el transporte corregido vuelva a estar
disponible.

Las entradas legacy sin snapshot inmutable no se reconstruyen desde aggregates
mutables: el worker las marca `dead_letter` y registra que requieren replay
manual desde un payload confiable. Las entradas nuevas siempre guardan el
snapshot en la misma transacción que el ACK o la telemetría.

## Outbox: entregas en cuarentena y reintentos

Cada lectura y cada ACK se guardan en el outbox en la misma transacción que el dato y se entregan al
core en segundo plano. Los fallos se clasifican: red/timeout/5xx se reintentan indefinidamente con
backoff (máx. 5 min); `401/403` se marcan `BLOCKED` (revisar `EDGE_TO_CORE_TOKEN`) y se reintentan
cada 5 min; rechazos permanentes del core (`VALIDATION_ERROR`) quedan en cuarentena con el motivo.

```bash
uv run python -m tools.outbox status
uv run python -m tools.outbox list
uv run python -m tools.outbox show 1842
uv run python -m tools.outbox replay 1842
uv run python -m tools.outbox purge --older-than-hours 168
```

Antes de cualquier migración de esquema el edge copia el archivo SQLite a `<archivo>.bak-<fecha>`.
Las tablas de telemetría con esquema incompatible se renombran a `device_telemetry_legacy_<fecha>`;
nunca se borran filas.

## Inspeccionar la Base de Datos

```bash
# Producción: Turso (libSQL remoto)
turso db shell <db-name> ".tables"
turso db shell <db-name> "SELECT * FROM devices;"

# Desarrollo local (cuando EDGE_TURSO_URL está vacío)
sqlite3 clair_edge.db ".tables"
sqlite3 clair_edge.db "SELECT * FROM devices;"
sqlite3 clair_edge.db "SELECT * FROM device_telemetry;"
```

## Configuración de Turso

Turso provee una base libSQL gestionada con replicas y HTTP. Para apuntar el
edge a Turso:

1. Crear la base (una sola vez):
   ```bash
   turso db create clair-edge
   ```
2. Emitir un token JWT para el cliente:
   ```bash
   turso db tokens create clair-edge
   ```
3. Copiar la URL `libsql://...` que imprime `turso db show clair-edge` y el
   token en `.env`:
   ```ini
   EDGE_TURSO_URL=libsql://clair-edge.turso.io
   EDGE_TURSO_TOKEN=<token-generado>
   ```
4. Iniciar el edge. `init_db()` aplicará migraciones idempotentes y creará
   las tablas contra Turso; no se genera ningún archivo local.

> El esquema se mantiene igual contra Turso o contra el archivo local: el
> cliente `libsql` es wire-compatible con SQLite, así que
> `ALTER TABLE ... ADD COLUMN`, `BEGIN`/`COMMIT` y `PRAGMA table_info`
> funcionan sin cambios. La serialización de escritura vive en el servidor
> Turso, por lo que el edge ya no necesita `BEGIN IMMEDIATE`.
