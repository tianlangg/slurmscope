FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src
WORKDIR /app
COPY backend/pyproject.toml ./pyproject.toml
COPY backend/src ./src
RUN pip install --no-cache-dir .
COPY backend/alembic.ini ./alembic.ini
COPY backend/migrations ./migrations
CMD ["uvicorn", "slurmscope.main:app", "--host", "0.0.0.0", "--port", "8000"]
