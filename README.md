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

## API Endpoints

### `POST /api/v1/device/telemetry`

Crea un nuevo registro de telemetría ambiental para un dispositivo autenticado.

**Headers requeridos:**

```
Content-Type: application/json
X-Hardware-Id: <hardware-id-del-dispositivo>
X-API-Key: <api-key-del-dispositivo>
```

**Body (JSON):**

```json
{
  "co2": 420.5,
  "pm25": 35.2,
  "created_at": "2026-05-16T22:30:00-05:00"
}
```

| Campo | Tipo | Requerido | Descripción |
|---|---|---|---|
| `co2` | number | Sí | Concentración de CO2 en ppm (rango: 0–5000) |
| `pm25` | number | Sí | Material particulado PM2.5 en µg/m³ (rango: 0–500) |
| `created_at` | string | No | Timestamp ISO 8601; si se omite usa UTC actual |

**Respuestas:**

| Código | Condición | Body |
|---|---|---|
| `201` | Registro creado | `{"id": 1, "hardware_id": "...", "co2": 420.5, "pm25": 35.2, "created_at": "..."}` |
| `400` | Campos faltantes o valores inválidos | `{"error": "..."}` |
| `401` | Credenciales inválidas o dispositivo no autorizado | `{"error": "..."}` |

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
| `CLAIR_CORE_BASE_URL` | `https://core.example.internal` | URL base de clair-core (HTTPS fuera de localhost) |
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
