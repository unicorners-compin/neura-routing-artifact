#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from compare_queue_closed_loop import run_closed_loop


METHODS = ("snn_sra", "ospf_te", "triggered_te", "te_ecmp", "bandit")
CONTROL_RECORD_BYTES = 32.0
TICK_MS = 10


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def ci95(values: list[float]) -> float:
    if len(values) <= 1:
        return 0.0
    import math

    m = mean(values)
    var = sum((x - m) ** 2 for x in values) / (len(values) - 1)
    return 1.96 * math.sqrt(var) / math.sqrt(len(values))


def hotspot_relief_tick(
    timeline: list[dict[str, object]],
    hotspot_start: int,
    delivery_target: float,
) -> int:
    post_rows = [row for row in timeline if int(row["tick"]) >= hotspot_start]
    for idx, row in enumerate(post_rows):
        if float(row["delivery_ratio"]) < delivery_target:
            continue
        if idx + 1 < len(post_rows):
            nxt = post_rows[idx + 1]
            if float(nxt["delivery_ratio"]) >= delivery_target:
                return int(row["tick"]) - hotspot_start
        else:
            return int(row["tick"]) - hotspot_start
    return int(post_rows[-1]["tick"]) - hotspot_start if post_rows else 0


def quieting_tick(
    timeline: list[dict[str, object]],
    hotspot_start: int,
    hotspot_end: int,
    delivery_target: float,
) -> int:
    pre_rows = [row for row in timeline if hotspot_start - 10 <= int(row["tick"]) < hotspot_start]
    if pre_rows:
        baseline_control = sum(float(row["emitted_this_tick"]) for row in pre_rows) / len(pre_rows)
    else:
        baseline_control = 0.0
    control_threshold = baseline_control * 1.10 + 1e-9
    post_rows = [row for row in timeline if int(row["tick"]) > hotspot_end]
    for idx, row in enumerate(post_rows):
        if idx + 1 < len(post_rows):
            nxt = post_rows[idx + 1]
            if (
                float(row["emitted_this_tick"]) <= control_threshold
                and float(nxt["emitted_this_tick"]) <= control_threshold
                and float(row["delivery_ratio"]) >= delivery_target
                and float(nxt["delivery_ratio"]) >= delivery_target
            ):
                return int(row["tick"]) - hotspot_end
    return int(post_rows[-1]["tick"]) - hotspot_end if post_rows else 0


def run_case(task: dict[str, object]) -> dict[str, object]:
    result = run_closed_loop(
        method=str(task["method"]),
        topology=str(task["topology"]),
        node_count=int(task["nodes"]),
        edge_prob=float(task["edge_prob"]),
        ba_attach=int(task["ba_attach"]),
        geo_radius=float(task["geo_radius"]),
        ticks=int(task["ticks"]),
        seed=int(task["seed"]),
        hotspot_node=int(task["hotspot_node"]),
        hotspot_start=int(task["hotspot_start"]),
        hotspot_end=int(task["hotspot_end"]),
        num_flows=int(task["num_flows"]),
        base_demand=float(task["base_demand"]),
        hotspot_share=float(task["hotspot_share"]),
        burst_multiplier=float(task["burst_multiplier"]),
        link_capacity=float(task["link_capacity"]),
        link_delay=float(task["link_delay"]),
        queue_capacity=float(task["queue_capacity"]),
        pressure_gain=float(task["pressure_gain"]),
    )
    return {
        "topology": task["topology"],
        "seed": task["seed"],
        "method": task["method"],
        "burst_multiplier": task["burst_multiplier"],
        "per_flow_load_to_capacity_ratio": (float(task["base_demand"]) * float(task["burst_multiplier"])) / float(task["link_capacity"]),
        "burst_mean_delivery_ratio": result["under_burst_summary"]["mean_delivery_ratio"],
        "burst_mean_loss_ratio": result["under_burst_summary"]["mean_loss_ratio"],
        "burst_mean_delay": result["under_burst_summary"]["mean_delay"],
        "burst_mean_path_stretch": result["under_burst_summary"]["mean_path_stretch"],
        "burst_mean_control_messages": result["under_burst_summary"]["mean_emitted_this_tick"],
        "burst_mean_control_bytes": result["under_burst_summary"]["mean_emitted_this_tick"] * CONTROL_RECORD_BYTES,
        "burst_mean_route_changes": result["under_burst_summary"]["mean_route_changes_this_tick"],
        "hotspot_peak_pressure": max(float(row["hotspot_pressure"]) for row in result["timeline"]),
        "hotspot_relief_tick_after_shock": hotspot_relief_tick(
            result["timeline"],
            int(task["hotspot_start"]),
            float(task["delivery_target"]),
        ),
        "quieting_tick_after_shock": quieting_tick(
            result["timeline"],
            int(task["hotspot_start"]),
            int(task["hotspot_end"]),
            float(task["delivery_target"]),
        ),
        "total_emitted_updates": result["totals"]["emitted_updates"],
        "total_control_bytes": result["totals"]["emitted_updates"] * CONTROL_RECORD_BYTES,
        "total_route_changes": result["totals"]["route_changes"],
        "total_route_changes_per_node": result["totals"]["route_changes"] / float(task["nodes"]),
        "peak_event_rate": result["peak_event_rate"],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Run a disturbance-intensity sweep for hotspot mitigation cost.")
    ap.add_argument("--issue", type=int, default=4)
    ap.add_argument("--topology", choices=("er", "ba", "rgg"), default="er")
    ap.add_argument("--nodes", type=int, default=100)
    ap.add_argument("--edge-prob", type=float, default=0.06)
    ap.add_argument("--ba-attach", type=int, default=2)
    ap.add_argument("--geo-radius", type=float, default=0.17)
    ap.add_argument("--ticks", type=int, default=160)
    ap.add_argument("--seeds", type=int, nargs="+", default=[121, 122, 123, 124, 125])
    ap.add_argument("--hotspot-node", type=int, default=1)
    ap.add_argument("--hotspot-start", type=int, default=40)
    ap.add_argument("--hotspot-end", type=int, default=70)
    ap.add_argument("--num-flows", type=int, default=400)
    ap.add_argument("--base-demand", type=float, default=0.2)
    ap.add_argument("--hotspot-share", type=float, default=0.6)
    ap.add_argument("--burst-multipliers", type=float, nargs="+", default=[2.0, 3.0, 4.0, 5.0])
    ap.add_argument("--link-capacity", type=float, default=10.0)
    ap.add_argument("--link-delay", type=float, default=1.0)
    ap.add_argument("--queue-capacity", type=float, default=30.0)
    ap.add_argument("--pressure-gain", type=float, default=1.8)
    ap.add_argument("--delivery-target", type=float, default=0.90)
    ap.add_argument("--jobs", type=int, default=min(16, max(1, os.cpu_count() or 1)))
    args = ap.parse_args()

    out_dir = REPO_ROOT / "results" / f"issue-{args.issue}" / "artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)

    tasks: list[dict[str, object]] = []
    for burst_multiplier in args.burst_multipliers:
        for seed in args.seeds:
            for method in METHODS:
                tasks.append(
                    {
                        "topology": args.topology,
                        "nodes": args.nodes,
                        "edge_prob": args.edge_prob,
                        "ba_attach": args.ba_attach,
                        "geo_radius": args.geo_radius,
                        "ticks": args.ticks,
                        "seed": seed,
                        "method": method,
                        "hotspot_node": args.hotspot_node,
                        "hotspot_start": args.hotspot_start,
                        "hotspot_end": args.hotspot_end,
                        "num_flows": args.num_flows,
                        "base_demand": args.base_demand,
                        "hotspot_share": args.hotspot_share,
                        "burst_multiplier": burst_multiplier,
                        "link_capacity": args.link_capacity,
                        "link_delay": args.link_delay,
                        "queue_capacity": args.queue_capacity,
                        "pressure_gain": args.pressure_gain,
                        "delivery_target": args.delivery_target,
                    }
                )

    detail_rows: list[dict[str, object]] = []
    if args.jobs <= 1:
        for idx, task in enumerate(tasks, start=1):
            detail_rows.append(run_case(task))
            print(
                f"[stress] {idx}/{len(tasks)} "
                f"method={task['method']} seed={task['seed']} burst={task['burst_multiplier']}"
            )
    else:
        with ProcessPoolExecutor(max_workers=args.jobs) as ex:
            future_map = {ex.submit(run_case, task): task for task in tasks}
            for idx, future in enumerate(as_completed(future_map), start=1):
                task = future_map[future]
                detail_rows.append(future.result())
                print(
                    f"[stress] {idx}/{len(tasks)} "
                    f"method={task['method']} seed={task['seed']} burst={task['burst_multiplier']}"
                )

    detail_rows.sort(key=lambda row: (float(row["burst_multiplier"]), int(row["seed"]), str(row["method"])))

    summary_rows: list[dict[str, object]] = []
    for burst_multiplier in args.burst_multipliers:
        for method in METHODS:
            rows = [row for row in detail_rows if row["method"] == method and float(row["burst_multiplier"]) == burst_multiplier]
            summary_rows.append(
                {
                    "method": method,
                    "burst_multiplier": burst_multiplier,
                    "per_flow_load_to_capacity_ratio": mean([float(r["per_flow_load_to_capacity_ratio"]) for r in rows]),
                    "delivery_mean": mean([float(r["burst_mean_delivery_ratio"]) for r in rows]),
                    "delivery_ci95": ci95([float(r["burst_mean_delivery_ratio"]) for r in rows]),
                    "path_stretch_mean": mean([float(r["burst_mean_path_stretch"]) for r in rows]),
                    "path_stretch_ci95": ci95([float(r["burst_mean_path_stretch"]) for r in rows]),
                    "control_bytes_mean": mean([float(r["total_control_bytes"]) for r in rows]),
                    "control_bytes_ci95": ci95([float(r["total_control_bytes"]) for r in rows]),
                    "route_changes_per_node_mean": mean([float(r["total_route_changes_per_node"]) for r in rows]),
                    "route_changes_per_node_ci95": ci95([float(r["total_route_changes_per_node"]) for r in rows]),
                    "burst_route_changes_mean": mean([float(r["burst_mean_route_changes"]) for r in rows]),
                    "burst_route_changes_ci95": ci95([float(r["burst_mean_route_changes"]) for r in rows]),
                    "hotspot_relief_ms_mean": mean([float(r["hotspot_relief_tick_after_shock"]) * TICK_MS for r in rows]),
                    "hotspot_relief_ms_ci95": ci95([float(r["hotspot_relief_tick_after_shock"]) * TICK_MS for r in rows]),
                    "quieting_ms_mean": mean([float(r["quieting_tick_after_shock"]) * TICK_MS for r in rows]),
                    "quieting_ms_ci95": ci95([float(r["quieting_tick_after_shock"]) * TICK_MS for r in rows]),
                    "peak_pressure_mean": mean([float(r["hotspot_peak_pressure"]) for r in rows]),
                }
            )

    stem = (
        f"stress_sweep_matrix_er_n{args.nodes}_s{len(args.seeds)}"
        if args.topology == "er"
        else f"stress_sweep_matrix_{args.topology}_n{args.nodes}_s{len(args.seeds)}"
    )
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

    summary_json.write_text(json.dumps({"config": vars(args), "rows": summary_rows}, indent=2))
    print(detail_csv)
    print(summary_csv)
    print(summary_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
