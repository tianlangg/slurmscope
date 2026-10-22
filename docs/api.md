# API quick reference

All routes use `/api`. Health and execution catalogs are public because this is
a localhost-only single-user app; add authentication before any remote exposure.

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/health`, `/ready` | Liveness and database readiness |
| GET | `/templates`, `/backends` | Approved workload and executor catalog |
| GET/POST | `/experiments` | Search/list and create |
| GET/PATCH/DELETE | `/experiments/{id}` | Read, edit definition, safe delete |
| POST | `/experiments/{id}/preview` | Expand a bounded Cartesian grid |
| POST | `/experiments/{id}/launch` | Queue runs using `Idempotency-Key` |
| GET | `/experiments/{id}/runs` | Experiment run history, filterable by status |
| GET | `/experiments/{id}/comparison` | Runs and available numeric metrics |
| GET | `/runs` | Global paginated run search |
| GET | `/runs/{id}` | Run detail |
| POST | `/runs/{id}/cancel` | Idempotent cancellation request |
| GET | `/runs/{id}/logs` | Bounded stdout/stderr tail |
| GET | `/runs/{id}/metrics` | Ingested metric values |
| GET | `/runs/{id}/events` | Durable normalized state history |

OpenAPI documentation is served at `/docs` while the API is running. Run list
endpoints cap `limit`; run logs cap both lines and bytes. Slurm resources are
validated against configured upper bounds and optional partition allowlists.
Deleting an experiment is allowed only when it has no active runs; it removes
its database records and its generated artifact directory.
