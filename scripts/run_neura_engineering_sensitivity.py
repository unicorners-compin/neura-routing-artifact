#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from compare_queue_closed_loop import run_closed_loop
from run_memory_rebound_matrix import run_variant as run_memory_variant
from run_snn_mechanism_studies import build_params


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def ci95(values: list[float]) -> float:
    if len(values) <= 1:
        return 0.0
    import math

    m = mean(values)
    var = sum((x - m) ** 2 for x in values) / (len(values) - 1)
    return 1.96 * math.sqrt(var) / math.sqrt(len(values))


SWEEP = {
    "w_edge": [0.8, 1.0, 1.2],
    "w_self_pressure": [0.7, 1.0, 1.3],
    "w_nh_pressure": [0.2, 0.5, 0.8],
    "switch_guard": [0.25, 0.45, 0.65],
    "rebound_guard": [0.15, 0.35, 0.55],
    "ttl": [4, 8, 12],
}


def hotspot_eval(seed: int, param_name: str, value: float | int) -> dict[str, float]:
    params = build_params("full")
    setattr(params, param_name, value)
    result = run_closed_loop(
        method="snn_sra",
        topology="er",
        node_count=100,
        edge_prob=0.06,
        ba_attach=2,
        ticks=80,
        seed=seed,
        hotspot_node=1,
        hotspot_start=25,
        hotspot_end=55,
        num_flows=400,
        base_demand=0.2,
        hotspot_share=0.6,
        burst_multiplier=3.0,
        link_capacity=10.0,
        link_delay=1.0,
        queue_capacity=30.0,
        pressure_gain=1.8,
        snn_params=params,
    )
    return {
        "hotspot_burst_delivery_ratio": float(result["under_burst_summary"]["mean_delivery_ratio"]),
        "hotspot_burst_loss_ratio": float(result["under_burst_summary"]["mean_loss_ratio"]),
        "hotspot_total_control_mb": float(result["totals"]["emitted_updates"]) * 32.0 / 1_000_000.0,
        "hotspot_peak_event_rate": float(result["peak_event_rate"]),
    }


def chaos_eval(seed: int, param_name: str, value: float | int) -> dict[str, float]:
    params = build_params("full")
    setattr(params, param_name, value)
    result = run_closed_loop(
        method="snn_sra",
        topology="er",
        node_count=100,
        edge_prob=0.06,
        ba_attach=2,
        ticks=140,
        seed=seed,
        hotspot_node=1,
        hotspot_start=30,
        hotspot_end=45,
        num_flows=400,
        base_demand=0.2,
        hotspot_share=0.6,
        burst_multiplier=3.0,
        link_capacity=10.0,
        link_delay=1.0,
        queue_capacity=30.0,
        pressure_gain=1.8,
        burst_windows=[(30, 45, 3.0), (60, 75, 3.0), (90, 105, 3.0)],
        snn_params=params,
    )
    return {
        "chaos_total_control_mb": float(result["totals"]["emitted_updates"]) * 32.0 / 1_000_000.0,
        "chaos_route_changes_per_node": float(result["totals"]["route_changes"]) / 100.0,
        "chaos_peak_event_rate": float(result["peak_event_rate"]),
        "chaos_burst_delivery_ratio": float(result["under_burst_summary"]["mean_delivery_ratio"]),
    }


def rebound_eval(seed: int, param_name: str, value: float | int) -> dict[str, float]:
    params = build_params("full")
    setattr(params, param_name, value)
    result = run_memory_variant(
        mode="full",
        topology="er",
        node_count=100,
        edge_prob=0.06,
        ba_attach=2,
        ticks=80,
        seed=seed,
        stage1_start=15,
        stage1_end=30,
        stage2_start=40,
        stage2_end=55,
        hotspot_pressure=1.0,
        params_override=params,
    )
    return {
        "rebound_ratio_after_release": float(result["rebound_ratio_after_release"]),
        "post_stage2_route_changes": float(result["post_stage2_route_changes"]),
    }


def run_case(task: dict[str, object]) -> dict[str, object]:
    seed = int(task["seed"])
    param_name = str(task["parameter"])
    value = task["value"]
    row = {
        "parameter": param_name,
        "value": value,
        "seed": seed,
    }
    row.update(hotspot_eval(seed, param_name, value))
    row.update(chaos_eval(seed, param_name, value))
    row.update(rebound_eval(seed, param_name, value))
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description="Run engineering-parameter sensitivity for the formal NEURA paper draft.")
    ap.add_argument("--issue", type=int, default=4)
    ap.add_argument("--seeds", nargs="+", type=int, default=[131, 132, 133, 134, 135])
    ap.add_argument("--jobs", type=int, default=min(16, max(1, os.cpu_count() or 1)))
    args = ap.parse_args()

    out_dir = REPO_ROOT / "results" / f"issue-{args.issue}" / "artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)

    tasks: list[dict[str, object]] = []
    for parameter, values in SWEEP.items():
        for value in values:
            for seed in args.seeds:
                tasks.append({"parameter": parameter, "value": value, "seed": seed})

    detail_rows: list[dict[str, object]] = []
    if args.jobs <= 1:
        for idx, task in enumerate(tasks, start=1):
            detail_rows.append(run_case(task))
            print(f"[eng-sensitivity] {idx}/{len(tasks)} parameter={task['parameter']} value={task['value']} seed={task['seed']}")
    else:
        with ProcessPoolExecutor(max_workers=args.jobs) as ex:
            future_map = {ex.submit(run_case, task): task for task in tasks}
            for idx, future in enumerate(as_completed(future_map), start=1):
                task = future_map[future]
                detail_rows.append(future.result())
                print(f"[eng-sensitivity] {idx}/{len(tasks)} parameter={task['parameter']} value={task['value']} seed={task['seed']}")

    detail_rows.sort(key=lambda row: (str(row["parameter"]), float(row["value"]), int(row["seed"])))

    summary_rows: list[dict[str, object]] = []
    for parameter, values in SWEEP.items():
        for value in values:
            rows = [row for row in detail_rows if row["parameter"] == parameter and float(row["value"]) == float(value)]
            summary_rows.append(
                {
                    "parameter": parameter,
                    "value": value,
                    "hotspot_burst_delivery_ratio_mean": mean([float(r["hotspot_burst_delivery_ratio"]) for r in rows]),
                    "hotspot_burst_delivery_ratio_ci95": ci95([float(r["hotspot_burst_delivery_ratio"]) for r in rows]),
                    "hotspot_total_control_mb_mean": mean([float(r["hotspot_total_control_mb"]) for r in rows]),
                    "hotspot_total_control_mb_ci95": ci95([float(r["hotspot_total_control_mb"]) for r in rows]),
                    "chaos_route_changes_per_node_mean": mean([float(r["chaos_route_changes_per_node"]) for r in rows]),
                    "chaos_route_changes_per_node_ci95": ci95([float(r["chaos_route_changes_per_node"]) for r in rows]),
                    "chaos_total_control_mb_mean": mean([float(r["chaos_total_control_mb"]) for r in rows]),
                    "chaos_total_control_mb_ci95": ci95([float(r["chaos_total_control_mb"]) for r in rows]),
                    "chaos_peak_event_rate_mean": mean([float(r["chaos_peak_event_rate"]) for r in rows]),
                    "chaos_peak_event_rate_ci95": ci95([float(r["chaos_peak_event_rate"]) for r in rows]),
                    "rebound_ratio_after_release_mean": mean([float(r["rebound_ratio_after_release"]) for r in rows]),
                    "rebound_ratio_after_release_ci95": ci95([float(r["rebound_ratio_after_release"]) for r in rows]),
                    "post_stage2_route_changes_mean": mean([float(r["post_stage2_route_changes"]) for r in rows]),
                    "post_stage2_route_changes_ci95": ci95([float(r["post_stage2_route_changes"]) for r in rows]),
                }
            )

    stem = f"snn_engineering_sensitivity_er_n100_s{len(args.seeds)}"
    detail_csv = out_dir / f"{stem}_detail.csv"
    summary_csv = out_dir / f"{stem}_summary.csv"
    summary_json = out_dir / f"{stem}_summary.json"

    with detail_csv.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(detail_rows[0].keys()))
        writer.writeheader()
        writer.writerows(detail_rows)

    with summary_csv.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(summary_rows[0].keys()))
        writer.writeheader()
        writer.writerows(summary_rows)

    summary_json.write_text(json.dumps({"config": vars(args), "sweep": SWEEP, "summary": summary_rows}, indent=2))
    print(detail_csv)
    print(summary_csv)
    print(summary_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
