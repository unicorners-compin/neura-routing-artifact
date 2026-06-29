#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=REPO_ROOT, check=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the supporting random-geometric-topology evidence suite.")
    ap.add_argument("--issue", type=int, default=4)
    ap.add_argument("--nodes", type=int, default=100)
    ap.add_argument("--geo-radius", type=float, default=0.17)
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--seeds", type=int, nargs="+", default=[121, 122, 123, 124, 125])
    args = ap.parse_args()

    seeds = [str(seed) for seed in args.seeds]
    base = [
        PY,
    ]

    run(
        base
        + [
            "scripts/run_activation_locality_matrix.py",
            "--issue",
            str(args.issue),
            "--topology",
            "rgg",
            "--nodes",
            str(args.nodes),
            "--geo-radius",
            str(args.geo_radius),
            "--seeds",
            *seeds,
        ]
    )
    run(
        base
        + [
            "scripts/run_stress_sweep_matrix.py",
            "--issue",
            str(args.issue),
            "--topology",
            "rgg",
            "--nodes",
            str(args.nodes),
            "--geo-radius",
            str(args.geo_radius),
            "--jobs",
            str(args.jobs),
            "--seeds",
            *seeds,
        ]
    )
    run(
        base
        + [
            "scripts/run_continuous_chaos_matrix.py",
            "--issue",
            str(args.issue),
            "--topology",
            "rgg",
            "--nodes",
            str(args.nodes),
            "--geo-radius",
            str(args.geo_radius),
            "--jobs",
            str(args.jobs),
            "--seeds",
            *seeds,
        ]
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
