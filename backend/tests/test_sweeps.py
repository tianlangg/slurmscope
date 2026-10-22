import pytest
from fastapi import HTTPException

from slurmscope.db.models import Backend
from slurmscope.services import expand_sweep, validate_resource


def test_three_by_two_cartesian_grid_is_sorted_and_deterministic():
    grid = {"seed": [1, 2], "learning_rate": [0.01, 0.1, 0.5]}
    first = expand_sweep(grid)
    assert len(first) == 6
    assert first == expand_sweep(grid)
    assert first[0] == {"learning_rate": 0.01, "seed": 1}


@pytest.mark.parametrize("grid", [{}, {"seed": []}])
def test_empty_grid_or_value_list_is_rejected(grid):
    with pytest.raises(HTTPException) as error:
        expand_sweep(grid)
    assert error.value.status_code == 422


@pytest.mark.parametrize(
    "grid",
    [
        {"steps": list(range(1, 52))},
        {"seed": list(range(10)), "steps": list(range(1, 7))},
    ],
)
def test_oversized_sweeps_fail_before_run_creation(grid):
    with pytest.raises(HTTPException, match="limit"):
        expand_sweep(grid)


@pytest.mark.parametrize(
    "grid",
    [
        {"script": [1]},
        {"learning_rate;rm": [0.1]},
        {"arbitrary_path": [2]},
    ],
)
def test_unapproved_template_parameters_are_rejected(grid):
    with pytest.raises(HTTPException, match="Unsupported"):
        expand_sweep(grid)


@pytest.mark.parametrize(
    "resource",
    [
        {"cpus_per_task": 0},
        {"cpus_per_task": 33},
        {"memory_mb": -1},
        {"time_limit_minutes": 10081},
        {"shell": "echo unsafe"},
        {"partition": "batch\n#SBATCH --output=/tmp/pwned"},
        {"account": "arbitrary-account"},
    ],
)
def test_invalid_slurm_resources_are_rejected(resource):
    with pytest.raises(HTTPException):
        validate_resource(Backend.slurm, resource)


def test_local_backend_rejects_slurm_resources():
    with pytest.raises(HTTPException, match="only apply"):
        validate_resource(Backend.local, {"cpus_per_task": 2})


@pytest.mark.parametrize(
    "grid",
    [
        {"steps": [1.5]},
        {"steps": [0]},
        {"seed": [-1]},
        {"delay_seconds": [float("nan")]},
    ],
)
def test_typed_parameter_values_are_bounded_and_finite(grid):
    with pytest.raises(HTTPException):
        expand_sweep(grid)
