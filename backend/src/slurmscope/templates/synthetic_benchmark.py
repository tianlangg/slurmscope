import json
import math
import random
import sys
import time
from pathlib import Path


def main() -> int:
    params = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    learning_rate = float(params.get("learning_rate", 0.1))
    seed = int(params.get("seed", 1))
    steps = int(params.get("steps", 100))
    delay = float(params.get("delay_seconds", 0.1))
    if not 1 <= steps <= 100_000 or not 0 <= delay <= 60 or not math.isfinite(learning_rate):
        print("Invalid benchmark parameters", file=sys.stderr)
        return 2
    rng = random.Random(seed)
    for step in range(steps):
        if step % max(1, steps // 10) == 0:
            print(f"progress {step}/{steps}", flush=True)
        time.sleep(delay / max(1, steps // 100))
    loss = round(abs(learning_rate - 0.1) + rng.random() / 100, 6)
    accuracy = round(max(0.0, min(1.0, 1 - loss)), 6)
    Path("metrics.json.tmp").write_text(
        json.dumps(
            {"accuracy": accuracy, "loss": loss, "wall_time_seconds": round(steps * delay, 3)}
        ),
        encoding="utf-8",
    )
    Path("metrics.json.tmp").replace("metrics.json")
    return 0


if __name__ == "__main__":
    code = main()
    Path("exit.json.tmp").write_text(json.dumps({"exit_code": code}), encoding="utf-8")
    Path("exit.json.tmp").replace("exit.json")
    raise SystemExit(code)
