# Integration plans — pointer

This repository does not duplicate integration plans. The authoritative documents live in the
sibling checkouts; links below are relative to this file and assume the three repositories sit in
one parent directory (`Clair/clair-core`, `Clair/edge`, `Clair/embedded`).

| Document | Location | Content |
|---|---|---|
| Implementation plan | [`../../../IMPLEMENTATION_PLAN.md`](../../../IMPLEMENTATION_PLAN.md) | Phased laptop deployment and contract alignment across core, edge and embedded. |
| Edge contract v1 | [`../../../clair-core/docs/contracts/edge-v1/README.md`](../../../clair-core/docs/contracts/edge-v1/README.md) | Frozen HTTP contracts core ↔ edge and device ↔ edge, with JSON fixtures. |
| Core behaviour backlog | [`../../../clair-core/docs/audit/backlog.md`](../../../clair-core/docs/audit/backlog.md) | Core-side domain items the edge depends on (B7.x). |

Operational summary of this repository: core → edge changes are recovered by polling with a
persisted watermark, the core notification is a latency optimisation only, and every edge → core
flow is authenticated HTTP. Kafka is not used.
