# Slurm setup and smoke test

The CLI adapter is implemented and mock-tested. A real cluster run is an
environment-specific acceptance step and has not been claimed by this repository
without recorded evidence. Do not run the worker on a login node unless the
center permits it. Confirm account, partition, resource, polling, storage, and
container policies first.

1. Configure a worker host that can invoke `sbatch`, `squeue`, `sacct`, and
   `scancel` as the authorized user. The artifact directory must be visible at
   the same path to the worker and compute nodes.
2. Set `SLURM_ENABLED=true`, `SLURM_ACCOUNT`, and `SLURM_PARTITIONS` to site
   approved values. Restrict CPU, memory, and time caps for the site.
3. Install the backend package and run migrations using the site's supported
   deployment method. Do not assume Docker is available on an HPC system.
4. Confirm `sbatch --version`, inspect `squeue -h -u "$USER"`, and verify
   accounting availability with a known job before launching a test.
5. Create a one-run synthetic experiment with `steps=1` and
   `delay_seconds=0`. Launch it and inspect the recorded job ID, stdout, metrics,
   and terminal state in `/api/runs/{id}`.
6. Record the center, date, account/partition category, job ID, and observed
   result in a private operations log. Never commit credentials or private
   cluster details.

Mock CLI behavior is covered by `backend/tests/test_slurm_executor.py`. It proves
parsing and normalization logic, not live cluster compatibility or site policy
approval. Submission timeout windows are surfaced for manual reconciliation;
the worker will not blindly submit a second Slurm job when the job ID was not
persisted.
