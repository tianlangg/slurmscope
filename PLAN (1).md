# SlurmScope: Implementation Plan

> Full-stack experiment management dashboard for local and Slurm workloads.
>
> **Status:** planning. This document defines the implementation target; checkboxes only become complete after the corresponding code and tests pass.
>
> **Objective:** Build a working, demoable project that supports the resume entry, not a UI mockup that only simulates job management.

## 1. Definition of done

SlurmScope is a single-user research experiment manager. A user defines a command from an approved template, chooses parameters and a sweep, launches runs on either a local executor or a real Slurm cluster, and tracks results from a React dashboard.

A complete v1 must demonstrate all of the following:

1. Define an experiment with a name, approved task template, parameter sweep, and execution backend.
2. Expand the Cartesian product of sweep values into individual runs, with a preview and run-count limit.
3. Launch runs asynchronously without tying their lifetimes to an HTTP request.
4. Show live job states, timestamps, backend IDs, and errors in the UI.
5. Cancel eligible queued, pending, or running jobs.
6. Retrieve capped stdout/stderr logs safely.
7. Save run metadata and metrics to PostgreSQL, surviving API and worker restarts.
8. Reconcile jobs after a restart without treating "not in squeue" as automatic success.
9. Compare numeric results across parameter combinations in both a table and a chart.
10. Run with Docker Compose and pass automated unit, integration, and CI checks.
11. Exercise the local backend end to end and **verify the Slurm backend against an authorized cluster** before describing live Slurm support as tested.

**Not in v1:** multi-tenant accounts, billing, Kubernetes, GPUs-as-a-service, user-uploaded arbitrary shell scripts, arbitrary remote filesystem browsing, a workflow DAG, auto-tuning/Bayesian optimization, job retries, or deployment as a public SaaS.

## 2. Fixed technology stack

| Layer | Choice | Why |
| --- | --- | --- |
| Frontend | React + TypeScript + Vite | Fast iteration and a conventional typed web client |
| UI | Tailwind CSS + lightweight component library | Responsive dashboard with consistent components |
| Data fetching | TanStack Query | Server state, cache invalidation, periodic polling |
| Charts | Recharts | Parameter-versus-metric comparison charts |
| API | Python 3.12+ + FastAPI + Pydantic v2 | Typed REST endpoints and validation |
| Database | PostgreSQL 16+ + SQLAlchemy 2 + Alembic | Durable run state and schema migrations |
| Job orchestrator | Separate Python worker process | Polling and submission outside web requests |
| Local executor | `subprocess` with explicit argv | Functional development backend without Slurm |
| Slurm executor | Slurm CLI adapter: `sbatch`, `squeue`, `sacct`, `scancel` | Real scheduler integration behind one interface |
| Tests | pytest, pytest-asyncio, HTTPX, Vitest, React Testing Library | API, worker, adapter, and frontend coverage |
| Packaging | Docker Compose | Reproducible app and database startup |
| CI | GitHub Actions | Automated formatting, lint, tests, and build |

Use `uv` to manage the Python environment and lockfile and `npm` for the frontend. Avoid introducing Redis, Celery, WebSockets, or a separate message broker for v1. Polling every 3 to 5 seconds is sufficient for the UI; the worker should poll scheduler state at a configurable, conservative interval (e.g. 10 to 20 seconds) and respect cluster policies.

### Important deployment decision

The local executor must work from a laptop using Docker Compose. The Slurm CLI adapter only works where the backend worker is authorized to invoke a configured Slurm client, usually an appropriate cluster access environment. **Do not assume a Docker container on a laptop has access to `sbatch`, and do not assume Docker is allowed on an HPC login node.**

Build and validate the Slurm CLI adapter separately using mocked commands first, then run a manual smoke test in an authorized Slurm environment. A future `SSHSlurmAdapter` can be added if the intended everyday setup is a laptop connected to a cluster via SSH; remote SSH, MFA, credentials, and file syncing are not silently part of v1.

## 3. Product flows

### A. Create and launch experiment

1. Open the experiments page and click **New experiment**.
2. Enter a name and select a *server-approved* task template, for example `synthetic_benchmark`.
3. Select `local` or `slurm` and enter validated resource settings when applicable.
4. Configure sweep values, for example `learning_rate = [0.01, 0.1, 0.5]` and `seed = [1, 2]`.
5. Preview the **6** generated runs and their arguments; reject oversized sweeps (default max: **50 runs per launch**).
6. Launch; the API persists queued runs, and the worker performs submission.
7. Navigate to experiment details and watch status updates.

### B. Monitor, cancel, inspect

- Display counts for queued, pending, running, succeeded, failed, cancelled, and unknown runs.
- Clicking a run opens job ID, parameters, timestamps, exit code, recent stdout/stderr, and latest metrics.
- Clicking **Cancel** requests cancellation; show `CANCELLING` while the executor responds, not `CANCELLED` immediately.
- On reload, data is read from PostgreSQL rather than the browser's memory.

### C. Compare results

- The test workload writes a `metrics.json` file with numeric results.
- The worker ingests it on successful completion (or when accessible after a terminal state).
- Show a table of parameters, run status, duration, and metric values.
- Provide a chart with an X-axis parameter selector and a Y-axis metric selector; multiple seeds can be plotted separately or summarized with an explicit aggregation.
- Missing or failed-run metrics must not be fabricated or rendered as zero.

## 4. Architecture

```text
React / TypeScript SPA
         |
         | HTTP JSON + periodic polling
         v
FastAPI server ------------------- PostgreSQL
         |                              ^
         | write experiment + runs      |
         |                              | persist transitions / metrics
         +------------------------------+
                                        |
                              Worker process (1 instance v1)
                                        |
                              Executor interface
                               /              \
                        LocalExecutor      SlurmExecutor
                        subprocess         sbatch / squeue
                        log files          sacct / scancel
                               \              /
                               Run artifact directory
                        stdout.log / stderr.log / metrics.json
```

The API is **not** the runner. In particular, do not run long jobs using FastAPI `BackgroundTasks`, lifespan tasks, or in-request `asyncio.create_task`. Those are tied to the API process and cannot deliver the intended job lifecycle semantics.

The single worker claims queued work transactionally, submits work, polls active jobs, and updates the database. Keep it at **one worker instance in v1**. If adding multiple workers later, implement explicit ownership/lease and recovery semantics first.

### Executor contract

Create a typed `Executor` interface or protocol with these operations:

```python
class Executor(Protocol):
    async def submit(self, run: RunSpec) -> SubmissionResult: ...
    async def status(self, external_id: str) -> JobSnapshot: ...
    async def cancel(self, external_id: str) -> CancelResult: ...
    async def logs(self, run: RunSpec, stream: Literal["stdout", "stderr"], tail: int) -> str: ...
```

Keep parameter expansion, state normalization, database persistence, and metrics parsing **outside** the executor implementations. Implement `LocalExecutor` first. The real `SlurmExecutor` must use the same worker and frontend flows.

### Workload and artifact convention

All runs receive a server-created UUID and a safe, isolated artifact directory:

```text
artifacts/<experiment-uuid>/<run-uuid>/
  stdout.log
  stderr.log
  metrics.json
  exit.json             # local wrapper completion marker when applicable
  submit.sh             # generated Slurm batch script when applicable
```

Require task templates to write `metrics.json` using a documented contract, for example:

```json
{
  "accuracy": 0.84,
  "loss": 0.32,
  "wall_time_seconds": 4.9
}
```

Never execute arbitrary commands provided verbatim from the browser. Templates define a known Python module or executable and an allowlist of typed arguments. Run-specific values are validated and passed as argv, not interpolated into a shell command. If Slurm uses a generated shell script, **only trusted template code** belongs in its executable body; validated parameters are passed as safely encoded arguments or placed in a data file.

## 5. Data model

Start with four tables and Alembic migrations. Use UUID primary keys, UTC timestamps, database constraints, foreign keys, and indexes.

### `experiments`

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID PK | Generated server-side |
| `name` | varchar(120) | Required |
| `description` | text nullable | Optional |
| `template_key` | varchar(80) | Approved task template |
| `backend` | enum (`local`, `slurm`) | Executor selection |
| `sweep_spec` | JSONB | Validated parameter grid |
| `resource_spec` | JSONB | Backend-specific validated settings |
| `created_at`, `updated_at` | timestamptz | UTC |

### `runs`

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID PK | Stable run identifier |
| `experiment_id` | FK -> experiments.id | Indexed |
| `parameters` | JSONB | One sweep combination |
| `backend` | enum | Snapshot from launch time |
| `status` | enum | Normalized lifecycle |
| `external_job_id` | varchar nullable | Local process identity or Slurm job ID |
| `submission_token` | UUID unique | Correlation / dedupe aid |
| `artifact_path` | text | Generated internally, never from client |
| `exit_code` | int nullable | Known terminal result only |
| `error_message` | text nullable | Sanitized failure description |
| `created_at`, `submitted_at`, `started_at`, `finished_at`, `updated_at` | timestamptz | Nullable as appropriate |

### `run_metrics`

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID PK | |
| `run_id` | FK -> runs.id | Indexed |
| `name` | varchar(80) | e.g. `accuracy` |
| `value` | double precision | Finite values only |
| `created_at` | timestamptz | |

Enforce `UNIQUE(run_id, name)` for final metrics; treat metrics ingestion as idempotent (upsert or replace as specified).

### `run_events`

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID PK | |
| `run_id` | FK -> runs.id | Indexed |
| `previous_status` | enum nullable | |
| `new_status` | enum | |
| `message` | text nullable | Limited length |
| `created_at` | timestamptz | |

Append an event on meaningful state transitions to make behavior debuggable.

### Status state machine

```text
QUEUED -> SUBMITTING -> PENDING -> RUNNING -> SUCCEEDED
   |          |             |         |-----> FAILED
   |          |             |         |-----> CANCELLING -> CANCELLED
   |          |             |         +-----> UNKNOWN
   |          |             +-----> CANCELLING -> CANCELLED
   |          +-----> FAILED / UNKNOWN
   +-----> CANCELLED

PENDING may transition directly to SUCCEEDED/FAILED/CANCELLED.
UNKNOWN means status cannot currently be established; a later poll may resolve it.
LOST is a terminal local-run recovery state only after a documented recovery attempt.
```

Keep state transitions centralized. Repeated polls must be idempotent. Avoid moving a terminal run back to running because of stale scheduler responses.

## 6. REST API contract

All routes use `/api` and return typed JSON. This v1 has **15+ meaningful REST endpoints**, not dummy endpoints to inflate a number.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/health` | App liveness |
| GET | `/api/ready` | DB readiness |
| GET | `/api/templates` | Approved tasks and typed parameter metadata |
| GET | `/api/backends` | Configured execution backends and availability |
| GET | `/api/experiments` | Paginated experiments, sort/filter |
| POST | `/api/experiments` | Create experiment and validate settings |
| GET | `/api/experiments/{experiment_id}` | Detail |
| PATCH | `/api/experiments/{experiment_id}` | Edit definition, not historical runs |
| DELETE | `/api/experiments/{experiment_id}` | Delete only if no active runs; define artifact policy |
| POST | `/api/experiments/{experiment_id}/preview` | Preview expanded sweep and count |
| POST | `/api/experiments/{experiment_id}/launch` | Atomically create queued runs |
| GET | `/api/experiments/{experiment_id}/runs` | Run list with filters |
| GET | `/api/experiments/{experiment_id}/comparison` | Numeric metrics and parameters |
| GET | `/api/runs` | Global run list, filters and pagination |
| GET | `/api/runs/{run_id}` | Run detail and latest status |
| POST | `/api/runs/{run_id}/cancel` | Idempotent cancellation request |
| GET | `/api/runs/{run_id}/logs?stream=stdout&tail=200` | Bounded log tail |
| GET | `/api/runs/{run_id}/metrics` | All ingested final metrics |
| GET | `/api/runs/{run_id}/events` | Status history |

### API rules

- Return `201` for created experiments, `202` for accepted launches/cancellations, `404` for missing entities, `409` for conflicting lifecycle operations, and `422` for invalid requests.
- Support `limit`/`offset` and validated `status` filters; avoid returning an unlimited job history.
- `POST /launch` should accept an idempotency key so a browser retry does not create duplicate sets of runs. Enforce the key in PostgreSQL (a small `launch_requests` table or equivalent unique record is acceptable).
- Validate sweep types and maximum size before writing any runs.
- Never return server filesystem paths or credentials in public API payloads.
- Keep separate `ExperimentCreate`, `ExperimentUpdate`, `ExperimentRead`, `RunRead`, `RunDetail`, and other Pydantic schemas.

## 7. Execution details

### Local executor (required first)

- Create a small bundled task `synthetic_benchmark` that accepts numeric arguments, sleeps briefly, prints progress, and writes repeatable `metrics.json` using a deterministic seed.
- Launch the task asynchronously with `subprocess` argument arrays and a specific working directory. Capture stdout/stderr in capped or rotated artifact files.
- Enforce a maximum number of concurrent local jobs, e.g. **2** by default.
- Record PID plus OS process creation time, or another identity signal, to avoid accidentally terminating a reused PID during cancellation/reconciliation.
- Prefer a wrapper that atomically writes `exit.json` on completion. When the worker restarts, inspect the marker and determine whether the process still exists. If state cannot be recovered after a bounded check, mark `LOST`, retaining logs, metadata, and event history.
- Do **not** assert that local processes always survive restarts. The durable guarantee is persisted records and explicit reconciliation, not magical process resurrection.

### Slurm executor (real integration)

- Accept only validated resource settings: partition/account allowlist (when configured), `cpus_per_task`, `memory_mb`, and `time_limit_minutes` with sensible upper bounds.
- Generate one batch script per run with a trusted task template and run-specific artifact paths accessible **from the Slurm execution environment**.
- Submit with `sbatch --parsable` and parse the first semicolon-delimited field as the job ID (output may include a cluster suffix).
- Poll active jobs using `squeue` with machine-parseable output. For jobs absent from the queue, consult `sacct` for job-level state and exit code; use `-X` or otherwise filter out step records.
- Normalize Slurm state variants (`PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED`, `TIMEOUT`, `OUT_OF_MEMORY`, `NODE_FAIL`, etc.) into application statuses and preserve raw Slurm details where helpful.
- Cancel with `scancel <job_id>`; a successful command alone does **not** mean cancellation has completed. Re-poll to confirm.
- If accounting is delayed or temporarily unavailable, use `UNKNOWN` and retry. Never interpret absence from `squeue` as success.
- Restrict the adapter to runs owned by the configured account/user. It must never expose global cluster controls or arbitrary job IDs.
- Handle the submission crash window explicitly: persist `SUBMITTING` and a correlation token *before* calling `sbatch`. If the worker crashes after Slurm accepts the job but before saving the job ID, **do not blindly resubmit**; mark for operator reconciliation or correlate using a supported job name/comment where available.
- Use conservative polling and follow site-specific scheduler, login-node, storage, and account policies. Do not hammer `squeue` or `sacct` separately for hundreds of runs when requests can be batched.

**Slurm references:**
- https://slurm.schedmd.com/sbatch.html
- https://slurm.schedmd.com/squeue.html
- https://slurm.schedmd.com/sacct.html
- https://slurm.schedmd.com/scancel.html
- https://slurm.schedmd.com/job_state_codes.html

### Worker durability rules

1. On startup, query `QUEUED`, `SUBMITTING`, `PENDING`, `RUNNING`, `CANCELLING`, and `UNKNOWN` records.
2. Reconcile previously submitted jobs before accepting additional work.
3. Claim queued jobs in a transaction, so a future retry or accidental parallel worker cannot trivially double-submit.
4. Record every state transition and update timestamp atomically.
5. Use bounded retries with backoff for scheduler outages, not infinite rapid polling.
6. Terminal jobs ingest metrics exactly once (idempotently) and remain queryable after restart.
7. A restarted API must never modify or reset active job states merely by starting up.

## 8. Frontend requirements

Build a usable interface with deliberate empty, loading, error, and partial-data states. Prioritize working functionality over animated decoration.

### Screens

1. **Dashboard (`/`)**: counts by job state, recent runs, recent failures, quick link to create an experiment.
2. **Experiments (`/experiments`)**: searchable/sortable experiment list, backend labels, last activity.
3. **Experiment editor (`/experiments/new`, `/experiments/:id/edit`)**: template, resource settings, dynamic sweep form, preview run combinations, validation, launch action.
4. **Experiment details (`/experiments/:id`)**: status counts, runs table, cancel actions, comparison charts.
5. **Run details (`/runs/:id`)**: exact parameters, scheduler job ID, timestamps, status history, logs (stdout/stderr tabs), numeric metrics.

### Interaction and quality rules

- Use TanStack Query to refetch active-run statuses every 3 to 5 seconds; slow or stop polling when data is terminal.
- Use request error messages and distinguish backend unavailable from no runs.
- Confirm destructive actions (cancel/delete) and disable invalid actions.
- Accessible labels, keyboard-focusable controls, adequate contrast, responsive tables.
- Display UTC timestamps converted to the user's local timezone.
- Do not fake statuses, hardcode metrics, or use placeholder values in production mode.
- Keep visual scope restrained: a sidebar/nav, KPI tiles, tables, forms, and one comparison chart are sufficient.

## 9. Repository layout

```text
slurmscope/
├── PLAN.md
├── README.md
├── .env.example
├── .gitignore
├── docker-compose.yml
├── .github/workflows/ci.yml
├── backend/
│   ├── pyproject.toml
│   ├── uv.lock
│   ├── alembic.ini
│   ├── migrations/
│   ├── src/slurmscope/
│   │   ├── api/                 # routers, dependency injection, schemas
│   │   ├── core/                # configuration, logging, security
│   │   ├── db/                  # ORM models, sessions, migrations
│   │   ├── services/            # experiments, sweeps, run lifecycle
│   │   ├── executors/           # protocol, local, Slurm, status normalization
│   │   ├── worker/              # claims, polling, reconciliation, ingestion
│   │   ├── templates/           # approved job templates
│   │   └── main.py              # app factory
│   └── tests/
│       ├── unit/
│       ├── integration/
│       └── fixtures/
├── frontend/
│   ├── package.json
│   ├── package-lock.json
│   ├── src/
│   │   ├── api/                # typed API client / hooks
│   │   ├── components/
│   │   ├── pages/
│   │   ├── features/
│   │   └── test/
│   └── index.html
├── scripts/
│   └── demo.sh
└── docs/
    ├── architecture.md
    ├── api.md
    ├── slurm-setup.md
    └── decisions.md
```

## 10. Milestones and execution order

Implement **one milestone at a time**. Do not scaffold the entire product in one gigantic agent change. A milestone is complete only when its acceptance criteria pass and the user can explain the important design decisions.

### M0: Scaffold and local environment

**Deliverables**
- [ ] Separate `backend/` and `frontend/`; configure `uv`, TypeScript, Vite, Ruff, ESLint, formatting.
- [ ] FastAPI app factory, `/api/health` and `/api/ready`.
- [ ] PostgreSQL container, API container, worker container placeholder, frontend dev server in Compose.
- [ ] Typed settings from environment, `.env.example`, `.gitignore`, README quickstart.
- [ ] GitHub Actions basic CI workflow.

**Acceptance**: `docker compose up --build` starts the stack, health and readiness behave correctly, the frontend loads, and initial CI checks pass. A worker placeholder may start but must not pretend to execute jobs.

### M1: Persistence, sweep logic, and API CRUD

**Deliverables**
- [ ] SQLAlchemy models, Alembic migration, async DB session dependency.
- [ ] Experiment CRUD, typed sweep/resource validation, preview endpoint.
- [ ] Cartesian sweep generation with deterministic parameter ordering and max-run count.
- [ ] Atomic launch transaction that creates `QUEUED` run records.
- [ ] Run list/detail endpoints, lifecycle service, status events, launch idempotency.
- [ ] Real PostgreSQL integration tests and unit tests for invalid/empty/oversized sweeps.

**Acceptance**: create an experiment, preview six combinations from a 3x2 grid, launch six queued run records, reload the API, and retrieve the same records. No execution yet.

### M2: Functional local execution and cancellation

**Deliverables**
- [ ] Shared executor contract and `LocalExecutor`.
- [ ] Bundled `synthetic_benchmark` template with deterministic metrics and logs.
- [ ] Standalone worker process with local concurrency cap and polling loop.
- [ ] Lifecycle transitions, cancellation, stdout/stderr retrieval, metric ingestion.
- [ ] Restart reconciliation: preserve all DB records, inspect live/finished local runs, and mark unrecoverable states clearly.
- [ ] Worker unit/integration tests, including failed process and cancellation cases.

**Acceptance**: launch six local runs and observe real transitions to terminal states; see distinct `metrics.json` values; cancel a long-running test job; restart the API/worker and retain history without duplicate runs.

### M3: Complete React dashboard

**Deliverables**
- [ ] Dashboard and experiments list.
- [ ] Create/edit experiment forms with validated sweep editor and preview.
- [ ] Experiment run table with status, filtering, timestamps, and cancel buttons.
- [ ] Run detail page with stderr/stdout log tail and error handling.
- [ ] Metric comparison table and interactive chart.
- [ ] Frontend tests for sweep preview, errors, filtering, and cancellation UX.

**Acceptance**: a user can complete the local create -> launch -> monitor -> inspect -> compare -> cancel flow from the browser without using curl or editing the database.

### M4: Real Slurm backend

**Deliverables**
- [ ] `SlurmExecutor` with validated batch script generation and `sbatch --parsable`.
- [ ] `squeue` polling, `sacct` accounting fallback, `scancel` handling, standardized states.
- [ ] Optional Slurm config: account, allowed partitions, runtime/memory limits, artifact root.
- [ ] Fake CLI test scripts covering pending/running/completed/failed/cancelled, scheduler failure, malformed output, accounting lag.
- [ ] `docs/slurm-setup.md` showing prerequisite commands and a safe live smoke test.
- [ ] Run **at least one real short batch job** on an authorized Slurm environment and verify job ID, logs, metrics, and terminal state.

**Acceptance**: the same frontend flow works with a real Slurm run. If live cluster access is unavailable, label this milestone **partially verified**, not complete. Mocked tests alone do not justify claiming proven live Slurm integration.

### M5: Hardening, integration tests, Docker, and CI

**Deliverables**
- [ ] At least **20 substantive pytest tests** across API, DB, scheduler, metrics, validation, and lifecycle; count cases meaningfully.
- [ ] Integration tests run against PostgreSQL, not just SQLite mocks.
- [ ] Frontend build/tests, backend Ruff/pytest/type check, migration check in GitHub Actions.
- [ ] Compose mounts durable Postgres and run-artifact volumes; API and worker share required storage.
- [ ] Startup/readiness handling and graceful shutdown; document local-worker restart caveat.
- [ ] Security review: command injection, path traversal, file permissions, log limits, malformed input, secrets, account permissions.
- [ ] README architecture diagram, GIF/screenshots (from real operation), exact setup and demo instructions.

**Acceptance**: from a clean checkout, follow README to launch a six-run demo, compare metrics, inspect logs, cancel another run, restart containers, and recover historical state. CI passes with no local-only hacks.

## 11. Tests to implement (minimum test plan)

These represent **test scenarios**, not a claim that they already exist.

**Sweep and validation**
1. 3x2 grid expands to six deterministic combinations.
2. Empty value list is rejected.
3. Sweep above 50 runs is rejected before writes.
4. Unsupported template or unknown parameter is rejected.
5. Invalid Slurm resource values are rejected.

**API and persistence**
6. Create/read/update experiment persists in Postgres.
7. Launch creates correct run count in one transaction.
8. Duplicate launch idempotency key returns the original launch without duplicates.
9. Invalid experiment ID returns 404.
10. Cannot delete an experiment with active runs.
11. Filter/paginate run list correctly.
12. App restart preserves experiments, runs, and events.

**Worker and executors**
13. Local run transitions from queued through running to success.
14. Nonzero local exit code becomes failed, not succeeded.
15. Cancellation of queued run is terminal without execution.
16. Cancellation of active local run terminates the correct process group.
17. Worker restart does not duplicate a previously submitted run.
18. Malformed or missing metrics do not crash the worker.
19. Metrics ingestion is idempotent.
20. Slurm `sbatch --parsable` parses a job ID with and without cluster suffix.
21. Slurm pending/running states normalize correctly.
22. Job absent from `squeue` but present in `sacct` resolves correctly.
23. Job absent from both queues/accounting remains unknown instead of succeeding.
24. Slurm timeout/OOM/cancel/failure normalize correctly.
25. Scheduler CLI timeout or malformed response is surfaced, retried, and recorded.
26. Log tail rejects traversal and enforces stream/size limits.

**Frontend**
27. Experiment form shows sweep count and errors.
28. Run table renders returned statuses (including UNKNOWN) correctly.
29. Cancel action shows pending state and API failure feedback.
30. Comparison chart excludes missing/nonfinite metrics and failed runs as specified.

## 12. Security, resource and operational constraints

- **Single-user by design.** Bind to localhost for local use. Do not expose an unauthenticated job-execution API on the public internet. Add authentication/authorization before remote/public deployment.
- No arbitrary browser-supplied bash, code uploads, paths, or raw `sbatch` directives.
- Use subprocess argument arrays and `shell=False` for scheduler commands. For generated batch scripts, use trusted script bodies, strict resource validation, and safe argument/data-file handling.
- Generated artifact locations must stay under a configured root. Reject path traversal, absolute client paths, symlink escapes, and oversized logs.
- Keep `.env`, database passwords, SSH material, and Slurm credentials out of Git and API responses.
- Limit concurrent local tasks and maximum sweep size; cap log-tail lines and bytes.
- Do not allow a dashboard user to cancel arbitrary job IDs: only target recorded runs.
- Make worker errors observable in logs and `run_events`; do not silently retry forever.
- Confirm compliance with HPC center rules before running app services on any shared cluster infrastructure.

## 13. Local demo walkthrough (must work)

1. Clone repo and configure `.env` using `.env.example`.
2. Run `docker compose up --build`.
3. Open the dashboard in a browser.
4. Create a `Synthetic Benchmark` experiment, backend `local`, with `learning_rate=[0.01,0.1,0.5]` and `seed=[1,2]`.
5. Preview and launch six actual local processes.
6. Watch QUEUED -> RUNNING -> SUCCEEDED; inspect logs and six metric records.
7. Compare the metric across learning rates/seeds in the chart.
8. Launch a deliberately slower experiment, cancel one run, and confirm the terminal status.
9. Restart API and worker containers; confirm experiment history, metrics, and state events remain available.
10. Demonstrate one authorized Slurm run separately and show the captured Slurm job ID and terminal accounting state.

The README must show the actual commands for setup, migration, backend/frontend tests, lint, demo, and optional Slurm validation. No claim of readiness without executing these steps.

## 14. Resume-claim verification matrix

The resume entry should only state work that has been implemented and validated.

| Resume claim | Evidence required |
| --- | --- |
| Full-stack dashboard with React, TypeScript, FastAPI, PostgreSQL | Working browser-to-API-to-Postgres demo (M0, M1, M3) |
| `10+ REST endpoints` | 10+ implemented, nontrivial endpoints documented and covered by tests (M1 to M5) |
| Configuring parameter sweeps | Deterministic grid expansion, validated preview, real multi-run launch (M1/M2) |
| Launching compute jobs | Genuine subprocess-backed local runs (M2), with Slurm separately validated (M4) |
| Comparing results | Real ingested numerical metrics, comparison table and chart (M3) |
| Local and Slurm scheduler abstraction | Same API + `Executor` protocol, tested local and actual Slurm run (M2/M4) |
| Submission, polling, cancellation, logs | End-to-end real jobs, state transitions and logs (M2/M4) |
| Persistent state across application restarts | PostgreSQL retention and meaningful worker reconciliation (M2/M5) |
| `20+ pytest unit and integration tests` | Test count, test report, and real DB integration coverage (M5) |
| Docker Compose + GitHub Actions | Clean Compose startup and passing CI run (M0/M5) |

**Do not put “Slurm integration completed” or “20+ tests” on a resume until those facts are true.** If building incrementally, adjust bullets to reflect the actual milestone.

## 15. Working rules for a coding agent

- Read this `PLAN.md`, relevant source files, and `git diff` before changing anything.
- Work only on the current milestone unless explicitly asked to expand scope.
- Before implementation, provide a short file-by-file plan and identify new dependencies.
- Prefer small, reviewable changes with typed code, clear ownership boundaries, and focused tests.
- Do not add fake endpoints, mocked UI data in production, or hardcoded job state to satisfy resume bullets.
- Do not start a later milestone to cover up a failing earlier milestone.
- Run relevant automated checks and report the exact commands and results.
- Do not claim a cluster run occurred when only mocked CLI fixtures were used.
- Preserve existing user edits. Do not commit, push, deploy, or touch cluster resources without explicit permission.
- Update `README.md`, `docs/decisions.md`, and this checklist when architecture or implementation state changes.
- After each milestone, report: **what works, evidence/tests, known limitations, and the next milestone**.
- Favor explaining one important design decision per milestone so the owner can defend the project in an interview.

## 16. Suggested first coding prompt

> Implement **Milestone M0 only** from `PLAN.md`. Read the plan and current repository first, then propose the smallest file structure. Set up the FastAPI app factory with `/api/health` and `/api/ready`, a React/TypeScript/Vite frontend, a PostgreSQL-backed Docker Compose stack, environment configuration, Ruff/ESLint, and a minimal GitHub Actions workflow. Add smoke tests and a README quickstart. Do **not** implement experiments, job execution, Slurm, dashboards, or other milestones yet. Show the commands you ran and any remaining blockers. Do not commit or push.

---

### Scope-control principle

**The smallest credible finished product is a real local experiment manager with persistent run state and comparison UI, followed by a verified Slurm adapter.** A good-looking UI without a durable worker is not enough. A working scheduler without an understandable UI also does not meet the full-stack target.
