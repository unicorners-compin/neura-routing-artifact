#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PY = "python3"


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=REPO_ROOT, check=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the redesigned issue-4 evaluation suite.")
    ap.add_argument("--issue", type=int, default=4)
    ap.add_argument("--profile", choices=("fast", "formal"), default="formal")
    ap.add_argument("--topology", choices=("er", "ba"), default="er")
    ap.add_argument("--jobs", type=int, default=min(16, max(1, os.cpu_count() or 1)))
    args = ap.parse_args()

    if args.profile == "fast":
        seeds = ["121"]
        shock_ticks = ["--ticks", "140", "--hotspot-start", "40", "--hotspot-end", "70"]
        sweep_ticks = ["--ticks", "160", "--hotspot-start", "40", "--hotspot-end", "70", "--burst-multipliers", "2.0", "3.0"]
        chaos_ticks = ["--ticks", "140", "--burst-windows", "30-45:3.0", "60-75:3.0", "90-105:3.0"]
    else:
        seeds = ["121", "122", "123", "124", "125"]
        shock_ticks = ["--ticks", "140", "--hotspot-start", "40", "--hotspot-end", "70"]
        sweep_ticks = ["--ticks", "160", "--hotspot-start", "40", "--hotspot-end", "70", "--burst-multipliers", "2.0", "3.0", "5.0"]
        chaos_ticks = ["--ticks", "140", "--burst-windows", "30-45:3.0", "60-75:3.0", "90-105:3.0"]

    base = [PY]
    run(base + ["scripts/run_shock_response_matrix.py", "--issue", str(args.issue), "--topology", args.topology, "--seeds", *seeds, *shock_ticks])
    run(base + ["scripts/run_activation_locality_matrix.py", "--issue", str(args.issue), "--topology", args.topology, "--seeds", *seeds])
    run(base + ["scripts/run_stress_sweep_matrix.py", "--issue", str(args.issue), "--topology", args.topology, "--seeds", *seeds, "--jobs", str(args.jobs), *sweep_ticks])
    run(base + ["scripts/run_continuous_chaos_matrix.py", "--issue", str(args.issue), "--topology", args.topology, "--seeds", *seeds, "--jobs", str(args.jobs), *chaos_ticks])
    run(base + ["scripts/run_gray_failure_matrix.py", "--issue", str(args.issue), "--topology", args.topology, "--seeds", *seeds])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
