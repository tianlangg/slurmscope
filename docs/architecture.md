# Architecture

The React client polls typed FastAPI endpoints. FastAPI validates experiment
definitions and writes the experiment, queued runs, idempotency key, and initial
events to PostgreSQL. It never executes workloads in a request handler.

One worker process claims queued runs in a database transaction, commits the
claim before external submission, then polls the selected executor. The local
executor launches the bundled Python module as an argv array in a dedicated
process group. The Slurm executor writes a server-generated batch script and
calls the configured Slurm CLI. Both executors report normalized states to the
same lifecycle service. Artifacts live in a shared Docker volume while metadata,
metrics, and transition history live in PostgreSQL.

```text
React + TanStack Query --HTTP/polling--> FastAPI --SQL--> PostgreSQL
          |                                  |                 ^
          +-- tables and charts              +-- validated API |
                                                              |
                                             one worker -- Executor protocol
                                                              /       \
                                                      Local process   Slurm CLI
                                                              \       /
                                              shared run artifact volume
```

## Decisions to explain

- A separate worker gives jobs a lifecycle independent of an HTTP request.
- PostgreSQL stores the source of truth, so browser reloads and API restarts do
  not erase runs or their state history.
- Executor adapters isolate scheduler-specific operations from the worker and
  API. The UI and run state model stay the same across local and Slurm.
- A local process cannot be promised to survive host/container restarts. The
  worker checks an atomic exit marker and process identity; otherwise it records
  `LOST` instead of inventing success.
- Missing Slurm jobs remain `UNKNOWN` until accounting resolves them. Queue
  absence alone is never interpreted as successful completion.
- Template values are numeric allowlisted inputs passed as JSON data/argv. The
  browser cannot submit arbitrary shell commands or file paths.

## Deliberate v1 limits

Single user, one worker instance, no broker, no retries, and no remote SSH
transport. Enable Slurm only in an environment where the worker has authorized
CLI and shared artifact storage. The demo Compose stack does not install Slurm.
