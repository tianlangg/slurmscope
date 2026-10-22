from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://slurmscope:slurmscope@localhost:5432/slurmscope"
    artifact_root: str = "./artifacts"
    worker_poll_seconds: float = 10
    max_runs_per_launch: int = 50
    max_local_jobs: int = 2
    log_tail_max_lines: int = 1000
    log_tail_max_bytes: int = 200_000
    log_file_max_bytes: int = 5_000_000
    slurm_enabled: bool = False
    slurm_account: str | None = None
    slurm_partitions: str = ""
    slurm_max_cpus: int = 32
    slurm_max_memory_mb: int = 131_072
    slurm_max_time_minutes: int = 10_080


settings = Settings()
