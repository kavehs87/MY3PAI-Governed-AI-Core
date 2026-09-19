"""Benchmark runner: boots the gateway, executes the k6 suite, stores results.

Writes evidence/benchmarks/k6_results.json with per-load-level p50/p95/p99,
failure rates, withdrawal propagation samples, and the k6 pass/fail verdict.
Also prints an ASCII summary table for README embedding.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def wait_for_health(base: str, timeout_s: float = 20.0) -> None:
    opener = _opener()
    deadline = time.time() + timeout_s
    last: Exception | None = None
    while time.time() < deadline:
        try:
            with opener.open(base + "/health", timeout=2) as resp:
                if resp.status == 200:
                    return
        except Exception as exc:
            last = exc
        time.sleep(0.2)
    raise RuntimeError(f"gateway at {base} not healthy: {last}")


def run_k6(script: Path, summary_path: Path, base: str) -> dict[str, object]:
    cmd = [
        "k6",
        "run",
        "--summary-export",
        str(summary_path),
        "--summary-mode",
        "full",
        "--summary-trend-stats",
        "avg,min,med,max,p(90),p(95),p(99),count",
        str(script),
    ]
    env = {"K6_URL": base}
    import os

    merged = dict(os.environ)
    merged.update(env)
    proc = subprocess.run(  # noqa: S603 -- fixed argv, no shell, k6 binary from PATH
        cmd, capture_output=True, text=True, env=merged, timeout=600, check=False
    )
    sys.stdout.write(proc.stdout[-4000:])
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr[-4000:])
        raise RuntimeError(f"k6 exited with code {proc.returncode}")
    return json.loads(summary_path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def extract_results(summary: dict[str, object]) -> dict[str, object]:
    metrics = summary.get("metrics", {})
    assert isinstance(metrics, dict)

    def trend(name: str, tag: str) -> dict[str, float]:
        # k6 materializes tagged sub-metrics for tag values referenced by
        # thresholds; the suite defines one threshold per load level.
        key = f"{name}{{{tag}}}"
        entry = metrics.get(key)
        assert isinstance(entry, dict), f"missing sub-metric {key}"
        breached = entry.get("thresholds", {})
        assert isinstance(breached, dict)
        if any(breached.values()):
            raise RuntimeError(f"threshold breached on {key}: {breached}")
        return {
            "p50": float(entry.get("med", 0.0)),
            "p95": float(entry.get("p(95)", 0.0)),
            "p99": float(entry.get("p(99)", 0.0)),
            "avg": float(entry.get("avg", 0.0)),
            "max": float(entry.get("max", 0.0)),
            "count": float(entry.get("count", 0.0)),
        }

    failed = metrics.get("http_req_failed", {})
    assert isinstance(failed, dict)
    fail_rate = float(failed.get("value", 0.0))
    failed_thresholds = failed.get("thresholds", {})
    assert isinstance(failed_thresholds, dict)
    if any(failed_thresholds.values()):
        raise RuntimeError(f"failure-rate threshold breached: {failed_thresholds}")

    return {
        "evaluate_200rps_ms": trend("http_req_duration", "load:200rps"),
        "evaluate_500rps_ms": trend("http_req_duration", "load:500rps"),
        "evaluate_1000rps_ms": trend("http_req_duration", "load:1000rps"),
        "http_req_failed_rate": fail_rate,
        "thresholds_passed": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="evidence/benchmarks/k6_results.json")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8410)
    args = parser.parse_args()

    base = f"http://{args.host}:{args.port}"
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path = out_path.parent / "k6_summary.json"

    gateway = subprocess.Popen(  # noqa: S603 -- fixed argv, no shell, local module
        [sys.executable, "-m", "src.policy.service", "--host", args.host, "--port", str(args.port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_for_health(base)
        summary = run_k6(Path("benchmarks/k6_gateway_stress.js"), summary_path, base)
        results = extract_results(summary)
        payload = {
            "gateway": base,
            "suite": "benchmarks/k6_gateway_stress.js",
            "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            **results,
        }
        out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        print("\n+----------------+----------------+---------------+")
        print("| load level     | p50 (ms)       | p95 (ms)      |")
        print("+----------------+----------------+---------------+")
        for level in ("evaluate_200rps_ms", "evaluate_500rps_ms", "evaluate_1000rps_ms"):
            row = results[level]
            assert isinstance(row, dict)
            print(f"| {level:<14} | {row['p50']:>14.2f} | {row['p95']:>13.2f} |")
        print("+----------------+----------------+---------------+")
    finally:
        gateway.terminate()
        gateway.wait(timeout=10)


if __name__ == "__main__":
    main()
