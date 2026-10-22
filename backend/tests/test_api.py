from uuid import UUID


def create(client):
    return client.post(
        "/api/experiments",
        json={
            "name": "demo",
            "template_key": "synthetic_benchmark",
            "backend": "local",
            "sweep_spec": {"learning_rate": [0.01, 0.1, 0.5], "seed": [1, 2]},
        },
    )


def test_health_and_database_readiness(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/ready").json()["database"] == "ok"


def test_cors_allows_configured_localhost_frontend_port(client):
    response = client.options(
        "/api/ready",
        headers={
            "Origin": "http://localhost:5174",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5174"


def test_cors_rejects_non_localhost_origin(client):
    response = client.options(
        "/api/ready",
        headers={
            "Origin": "https://example.com",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.headers.get("access-control-allow-origin") is None


def test_template_and_backend_catalog(client):
    assert client.get("/api/templates").json()[0]["key"] == "synthetic_benchmark"
    assert {b["key"] for b in client.get("/api/backends").json()} == {"local", "slurm"}


def test_create_experiment_persists_across_requests(client):
    response = create(client)
    assert response.status_code == 201
    item = response.json()
    assert client.get(f"/api/experiments/{item['id']}").json()["name"] == "demo"
    assert client.get("/api/experiments").json()[0]["id"] == item["id"]


def test_preview_returns_six_deterministic_combinations(client):
    item = create(client).json()
    result = client.post(f"/api/experiments/{item['id']}/preview", json={}).json()
    assert result["count"] == 6 and result["runs"][0] == {"learning_rate": 0.01, "seed": 1}


def test_launch_is_atomic_and_idempotent(client):
    item = create(client).json()
    url = f"/api/experiments/{item['id']}/launch"
    headers = {"Idempotency-Key": "demo-launch-001"}
    first = client.post(url, headers=headers)
    second = client.post(url, headers=headers)
    assert first.status_code == 202 and first.json()["count"] == 6
    assert second.json()["replayed"] is True and second.json()["run_ids"] == first.json()["run_ids"]
    assert len(client.get(f"/api/experiments/{item['id']}/runs").json()) == 6


def test_update_and_delete_experiment(client):
    item = create(client).json()
    changed = client.patch(f"/api/experiments/{item['id']}", json={"name": "renamed"})
    assert changed.json()["name"] == "renamed"
    assert client.delete(f"/api/experiments/{item['id']}").status_code == 204


def test_invalid_template_parameter_and_slurm_settings_rejected(client):
    body = {"name": "bad", "template_key": "shell", "backend": "local", "sweep_spec": {"seed": [1]}}
    assert client.post("/api/experiments", json=body).status_code == 422
    body.update(template_key="synthetic_benchmark", sweep_spec={"unknown": [1]})
    assert client.post("/api/experiments", json=body).status_code == 422
    body.update(
        sweep_spec={"seed": [1]},
        backend="slurm",
        resource_spec={"cpus_per_task": 0},
    )
    assert client.post("/api/experiments", json=body).status_code == 422


def test_run_detail_logs_events_metrics_and_pagination(client):
    item = create(client).json()
    launch = client.post(
        f"/api/experiments/{item['id']}/launch", headers={"Idempotency-Key": "detail-launch-001"}
    ).json()
    run_id = launch["run_ids"][0]
    assert client.get("/api/runs", params={"limit": 2}).status_code == 200
    assert UUID(client.get(f"/api/runs/{run_id}").json()["id"])
    assert client.get(f"/api/runs/{run_id}/metrics").json() == []
    assert client.get(f"/api/runs/{run_id}/logs").json()["content"] == ""
    assert client.get(f"/api/runs/{run_id}/events").json()[0]["new_status"] == "QUEUED"


def test_cancel_queued_run_is_immediately_terminal(client):
    item = create(client).json()
    launch = client.post(
        f"/api/experiments/{item['id']}/launch", headers={"Idempotency-Key": "cancel-launch-001"}
    ).json()
    run_id = launch["run_ids"][0]
    assert client.post(f"/api/runs/{run_id}/cancel").json()["status"] == "CANCELLED"
    assert client.get(f"/api/runs/{run_id}").json()["status"] == "CANCELLED"


def test_cannot_delete_experiment_with_queued_runs(client):
    item = create(client).json()
    client.post(
        f"/api/experiments/{item['id']}/launch", headers={"Idempotency-Key": "delete-launch-001"}
    )
    assert client.delete(f"/api/experiments/{item['id']}").status_code == 409


def test_missing_entities_return_not_found(client):
    missing = "00000000-0000-0000-0000-000000000001"
    assert client.get(f"/api/experiments/{missing}").status_code == 404
    assert client.get(f"/api/runs/{missing}").status_code == 404
