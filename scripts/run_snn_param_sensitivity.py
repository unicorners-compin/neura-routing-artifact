#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from compare_queue_closed_loop import run_closed_loop
from run_memory_rebound_matrix import run_variant as run_memory_variant
from run_snn_mechanism_studies import build_params
from src.snn_sra_sim.algorithms import SnnSraParams


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def ci95(values: list[float]) -> float:
    if len(values) <= 1:
        return 0.0
    m = mean(values)
    var = sum((v - m) ** 2 for v in values) / (len(values) - 1)
    return 1.96 * (var ** 0.5) / (len(values) ** 0.5)


def hotspot_eval(params: SnnSraParams, seed: int) -> dict[str, float]:
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
        "burst_delivery_ratio": float(result["under_burst_summary"]["mean_delivery_ratio"]),
        "burst_loss_ratio": float(result["under_burst_summary"]["mean_loss_ratio"]),
        "burst_mean_delay": float(result["under_burst_summary"]["mean_delay"]),
        "total_emitted_updates": float(result["totals"]["emitted_updates"]),
        "peak_event_rate": float(result["peak_event_rate"]),
    }


def memory_eval(params: SnnSraParams, seed: int) -> dict[str, float]:
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
        "return_to_stage1_ratio": float(result["return_to_stage1_ratio"]),
        "after_stage2_fire_ratio": float(result["after_stage2_fire_ratio"]),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Run formal SNN-SRA parameter export and sensitivity study.")
    ap.add_argument("--issue", type=int, default=4)
    ap.add_argument("--seeds", nargs="+", type=int, default=[131, 132, 133, 134, 135])
    args = ap.parse_args()

    out_dir = REPO_ROOT / "results" / f"issue-{args.issue}" / "artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)

    default_params = build_params("full")
    baseline_params = build_params("baseline")
    memory_params = build_params("memory_only")

    param_rows = []
    default_map = asdict(default_params)
    baseline_map = asdict(baseline_params)
    memory_map = asdict(memory_params)
    for key, value in default_map.items():
        param_rows.append(
            {
                "parameter": key,
                "full_default": value,
                "baseline_value": baseline_map[key],
                "memory_only_value": memory_map[key],
            }
        )

    param_json = out_dir / "snn_parameter_table_v1.json"
    param_csv = out_dir / "snn_parameter_table_v1.csv"
    param_json.write_text(json.dumps({"full": default_map, "baseline": baseline_map, "memory_only": memory_map}, indent=2))
    with param_csv.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(param_rows[0].keys()))
        writer.writeheader()
        writer.writerows(param_rows)

    sweep = {
        "threshold": [1.1, 1.4, 1.7],
        "refractory_ticks": [2, 4, 6],
        "memory_decay": [0.55, 0.78, 0.9],
        "slow_weight": [0.15, 0.35, 0.55],
    }

    detail_rows = []
    for param_name, values in sweep.items():
        for value in values:
            for seed in args.seeds:
                params = build_params("full")
                setattr(params, param_name, value)
                hotspot = hotspot_eval(params, seed)
                healing = memory_eval(params, seed)
                detail_rows.append(
                    {
                        "parameter": param_name,
                        "value": value,
                        "seed": seed,
                        **hotspot,
                        **healing,
                    }
                )

    summary_rows = []
    for param_name, values in sweep.items():
        for value in values:
            rows = [row for row in detail_rows if row["parameter"] == param_name and row["value"] == value]
            summary_rows.append(
                {
                    "parameter": param_name,
                    "value": value,
                    "burst_delivery_ratio_mean": mean([float(r["burst_delivery_ratio"]) for r in rows]),
                    "burst_delivery_ratio_ci95": ci95([float(r["burst_delivery_ratio"]) for r in rows]),
                    "total_emitted_updates_mean": mean([float(r["total_emitted_updates"]) for r in rows]),
                    "total_emitted_updates_ci95": ci95([float(r["total_emitted_updates"]) for r in rows]),
                    "rebound_ratio_after_release_mean": mean([float(r["rebound_ratio_after_release"]) for r in rows]),
                    "rebound_ratio_after_release_ci95": ci95([float(r["rebound_ratio_after_release"]) for r in rows]),
                    "post_stage2_route_changes_mean": mean([float(r["post_stage2_route_changes"]) for r in rows]),
                    "post_stage2_route_changes_ci95": ci95([float(r["post_stage2_route_changes"]) for r in rows]),
                }
            )

    detail_csv = out_dir / "snn_param_sensitivity_er_n100_s5_detail.csv"
    summary_csv = out_dir / "snn_param_sensitivity_er_n100_s5_summary.csv"
    summary_json = out_dir / "snn_param_sensitivity_er_n100_s5_summary.json"

    with detail_csv.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(detail_rows[0].keys()))
        writer.writeheader()
        writer.writerows(detail_rows)

    with summary_csv.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(summary_rows[0].keys()))
        writer.writeheader()
        writer.writerows(summary_rows)

    summary_json.write_text(
        json.dumps(
            {
                "config": {"seeds": args.seeds, "sweep": sweep},
                "default_full_params": default_map,
                "summary": summary_rows,
            },
            indent=2,
        )
    )

    print(param_csv)
    print(param_json)
    print(detail_csv)
    print(summary_csv)
    print(summary_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
