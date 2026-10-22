# SlurmScope

SlurmScope is a single-user experiment manager for parameter sweeps. It pairs a
React and TypeScript dashboard with a FastAPI API, PostgreSQL state, and a
separate worker that runs approved tasks locally or through Slurm.

> **Verification status (2026-10-21):** The Compose stack and local worker were
> exercised, including a six-run sweep and API/worker restart with PostgreSQL
> records retained. All 63 pytest cases passed against PostgreSQL; four frontend
> tests, lint, and production build passed. Slurm CLI tests use mocked commands;
> a real Slurm job still requires an authorized cluster and is not claimed here.
> GitHub Actions is configured but has not yet run on a hosted runner. The app is
> designed for localhost use and has no authentication. `npm audit` reports zero
> frontend dependency vulnerabilities.

## Quick start

Requirements: Docker Compose v2.30+, and a browser. From a clean checkout:

```sh
cp .env.example .env
# Change POSTGRES_PASSWORD to a local password if desired.
docker compose up --build
```

Open <http://localhost:5173>. The API is at <http://localhost:8000> and OpenAPI
docs at <http://localhost:8000/docs>. Compose runs PostgreSQL, an Alembic
migration job, the API, one worker, and the frontend. Database data and workload
artifacts use named volumes. The API binds to loopback.

If either localhost port is already in use, set `API_PORT` or `FRONTEND_PORT`
in `.env` before starting Compose; the defaults are `8000` and `5173`.

To run a six-combination local experiment, create an experiment in the UI with
`learning_rate=[0.01, 0.1, 0.5]`, `seed=[1, 2]`, then preview and launch. Watch
the run table, inspect a run's logs and metrics, and compare accuracy in the
chart. You can also run `scripts/demo.sh` after startup.

## Local development

Backend commands (run from `backend/`):

```sh
uv sync --extra dev
export DATABASE_URL='postgresql+psycopg://slurmscope:slurmscope@localhost:5432/slurmscope'
uv run alembic upgrade head
uv run uvicorn slurmscope.main:app --reload
uv run python -m slurmscope.worker.runner
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
```

Frontend commands (run from `frontend/`):

```sh
npm ci
npm run dev
npm run lint
npm test
npm run build
```

Integration tests require a dedicated PostgreSQL database whose name ends in
`_test`. The test fixture refuses to drop tables if that guard is not satisfied.
For example, create `slurmscope_test` in a disposable PostgreSQL instance, then:

```sh
cd backend
DATABASE_URL='postgresql+psycopg://slurmscope:test@localhost:5432/slurmscope_test' uv run pytest -q
```

Tests cover sweep expansion and validation, PostgreSQL API persistence and
idempotency, local subprocess execution, metrics/log behavior, and Slurm CLI
parsing/state handling. CI starts a PostgreSQL service, runs migrations, pytest,
Ruff, mypy, frontend tests, lint, and production build.

The implementation was verified with 63 passing pytest cases against a
dedicated PostgreSQL test database, four passing frontend tests, Ruff, mypy,
ESLint, a clean `npm ci`, zero findings from `npm audit`, and a production
frontend build. The Compose smoke test completed a real six-run local sweep and
retained its results after restarting the API and worker.
See [architecture decisions](docs/decisions.md) and [the verification matrix in
the plan](PLAN.md#14-resume-claim-verification-matrix) for the design rationale
and remaining verification boundaries.

## Architecture

The API validates definitions and persists queued runs. The standalone worker
claims runs transactionally and talks to an executor protocol. The local adapter
starts only the bundled benchmark as an argv list. The Slurm adapter uses
`sbatch --parsable`, machine-readable `squeue`, `sacct -X` fallback, and
`scancel`. Both write/read artifacts in the shared run directory. PostgreSQL
retains experiments, run metadata, metrics, launch idempotency keys, and status
events across API and worker restarts. The browser polls active runs every three
seconds and stops after terminal states.

```text
React dashboard ──HTTP/polling──> FastAPI ──> PostgreSQL
       │                            │              ▲
 tables / charts             validation            │
                                    └── Worker ── Executor interface
                                                   ├── Local subprocess
                                                   └── Slurm CLI
```

Interview explanation: the API does not own long-running work. It writes durable
queued records; a worker claims them and translates executor-specific states
into one run state model. This makes the UI and persistence path common to both
execution backends. A process lost during restart becomes `LOST`; missing Slurm
accounting becomes `UNKNOWN`, never fabricated success.

## Slurm setup

See [docs/slurm-setup.md](docs/slurm-setup.md) before enabling Slurm. The worker
must run where the approved Slurm CLI and shared artifact paths are available.
The local Compose stack does not expose host credentials or install Slurm.

## API and design notes

- [API reference](docs/api.md)
- [Architecture decisions](docs/architecture.md)
- [Full implementation plan and acceptance matrix](PLAN.md)

The API includes over 15 useful endpoints for health, catalogs, experiment CRUD,
sweep preview and launch, runs, cancellation, logs, metrics, events, and result
comparison. This is a single-user localhost application; add authentication and
authorization before exposing it on a network.
