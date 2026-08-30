# Planes de migración core↔edge — puntero

Este repositorio no duplica los planes de migración. La fuente única de
verdad vive en `/home/giks/projects/IOT/clair-core/docs/plans/` (o en
`../clair-core/docs/plans/` desde esta raíz).

Los documentos describen cambios coordinados entre ambos repositorios:

| Plan | Contenido |
|---|---|
| `00-overview.md` | Diagnóstico y arquitectura objetivo. |
| `01-transporte.md` | Inventario de componentes sustituidos y conservados. |
| `02-sincronizacion-devices.md` | Roster incremental, watermark y reconciliación. |
| `03-contratos-http-core-edge.md` | Contratos HTTP entre servicios. |
| `04-plan-de-corte.md` | Fases, verificaciones y rollback. |
| `05-actualizacion-documentacion.md` | Actualización de documentación técnica. |

Para trabajar únicamente en edge, el plan 04 cubre el orden seguro de
preparación, migraciones SQLite, configuración de secretos, arranque de
pollers, transporte HTTP del outbox y rollback acotado. Los planes 02 y 03
definen los payloads, cursores, endpoints y semántica de reintento que usan
los adaptadores del edge.

La documentación de este repositorio refleja el estado operativo actual:
los cambios core→edge se recuperan mediante polling con watermark persistido,
las notificaciones son una optimización de latencia y los flujos edge→core
usan HTTP autenticado. No se mantienen copias locales de los planes para
prevenir divergencias entre repositorios.
