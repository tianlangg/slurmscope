#!/usr/bin/env sh
set -eu
API=${API:-http://localhost:8000/api}
created=$(curl -fsS -X POST "$API/experiments" -H 'Content-Type: application/json' -d '{"name":"Recruiter demo sweep","template_key":"synthetic_benchmark","backend":"local","sweep_spec":{"learning_rate":[0.01,0.1,0.5],"seed":[1,2],"steps":[10],"delay_seconds":[0.1]}}')
id=$(printf '%s' "$created" | python -c 'import json,sys; print(json.load(sys.stdin)["id"])')
printf 'Created experiment %s\n' "$id"
curl -fsS -X POST "$API/experiments/$id/launch" -H 'Idempotency-Key: slurmscope-demo-001' | python -m json.tool
printf 'Open http://localhost:5173/experiments/%s\n' "$id"
