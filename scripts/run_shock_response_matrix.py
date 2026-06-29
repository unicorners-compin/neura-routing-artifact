#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import os
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from collections import defaultdict
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
    m = mean(values)
    var = sum((x - m) ** 2 for x in values) / (len(values) - 1)
    import math

    return 1.96 * math.sqrt(var) / math.sqrt(len(values))


def first_active_tick(timeline: list[dict[str, object]], hotspot_start: int) -> int | None:
    for row in timeline:
        tick = int(row["tick"])
        if tick < hotspot_start:
            continue
        if float(row["emitted_this_tick"]) > 0.0:
            return tick - hotspot_start
    return None


def hotspot_relief_tick(
    timeline: list[dict[str, object]],
    hotspot_start: int,
    delivery_target: float,
) -> int | None:
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
    return None


def quieting_tick(
    timeline: list[dict[str, object]],
    hotspot_start: int,
    hotspot_end: int,
    delivery_target: float,
) -> int | None:
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
    return None


def run_case(task: dict[str, object]) -> dict[str, object]:
    result = run_closed_loop(
        method=str(task["method"]),
        topology=str(task["topology"]),
        node_count=int(task["nodes"]),
        edge_prob=float(task["edge_prob"]),
        ba_attach=int(task["ba_attach"]),
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
    timeline = result["timeline"]
    timeline_rows: list[dict[str, object]] = []
    for row in timeline:
        emitted = float(row["emitted_this_tick"])
        timeline_rows.append(
            {
                "topology": task["topology"],
                "seed": task["seed"],
                "method": task["method"],
                "tick": int(row["tick"]),
                "time_ms": int(row["tick"]) * TICK_MS,
                "burst_active": int(row["burst_active"]),
                "hotspot_pressure": float(row["hotspot_pressure"]),
                "delivery_ratio": float(row["delivery_ratio"]),
                "loss_ratio": float(row["loss_ratio"]),
                "mean_delay": float(row["mean_delay"]) if row["mean_delay"] is not None else None,
                "control_messages": emitted,
                "control_bytes": emitted * CONTROL_RECORD_BYTES,
                "route_changes": float(row["route_changes_this_tick"]),
            }
        )
    detail_row = {
        "topology": task["topology"],
        "seed": task["seed"],
        "method": task["method"],
        "first_active_tick_after_shock": first_active_tick(timeline, int(task["hotspot_start"])),
        "hotspot_relief_tick_after_shock": hotspot_relief_tick(
            timeline,
            int(task["hotspot_start"]),
            float(task["delivery_target"]),
        ),
        "quieting_tick_after_shock": quieting_tick(
            timeline,
            int(task["hotspot_start"]),
            int(task["hotspot_end"]),
            float(task["delivery_target"]),
        ),
        "burst_mean_delivery_ratio": result["under_burst_summary"]["mean_delivery_ratio"],
        "burst_mean_control_messages": result["under_burst_summary"]["mean_emitted_this_tick"],
        "burst_mean_control_bytes": result["under_burst_summary"]["mean_emitted_this_tick"] * CONTROL_RECORD_BYTES,
        "burst_mean_route_changes": result["under_burst_summary"]["mean_route_changes_this_tick"],
        "total_emitted_updates": result["totals"]["emitted_updates"],
        "total_route_changes": result["totals"]["route_changes"],
        "peak_event_rate": result["peak_event_rate"],
        "peak_route_changes_per_tick": max(float(row["route_changes_this_tick"]) for row in timeline),
        "startup_full_reachability_tick": result["startup_summary"]["full_reachability_tick"],
        "startup_control_bytes_before_first_burst": result["startup_summary"]["control_bytes_before_first_burst"],
        "startup_control_bytes_before_full_reachability": result["startup_summary"]["control_bytes_before_full_reachability"],
    }
    return {"timeline_rows": timeline_rows, "detail_row": detail_row}


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the shock-response matrix for the localized control-plane paper.")
    ap.add_argument("--issue", type=int, default=4)
    ap.add_argument("--topology", choices=("er", "ba"), default="er")
    ap.add_argument("--nodes", type=int, default=100)
    ap.add_argument("--edge-prob", type=float, default=0.06)
    ap.add_argument("--ba-attach", type=int, default=2)
    ap.add_argument("--ticks", type=int, default=140)
    ap.add_argument("--seeds", type=int, nargs="+", default=[121, 122, 123, 124, 125])
    ap.add_argument("--hotspot-node", type=int, default=1)
    ap.add_argument("--hotspot-start", type=int, default=40)
    ap.add_argument("--hotspot-end", type=int, default=70)
    ap.add_argument("--num-flows", type=int, default=400)
    ap.add_argument("--base-demand", type=float, default=0.2)
    ap.add_argument("--hotspot-share", type=float, default=0.6)
    ap.add_argument("--burst-multiplier", type=float, default=3.0)
    ap.add_argument("--link-capacity", type=float, default=10.0)
    ap.add_argument("--link-delay", type=float, default=1.0)
    ap.add_argument("--queue-capacity", type=float, default=30.0)
    ap.add_argument("--pressure-gain", type=float, default=1.8)
    ap.add_argument("--delivery-target", type=float, default=0.90)
    ap.add_argument("--jobs", type=int, default=min(16, max(1, os.cpu_count() or 1)))
    args = ap.parse_args()

    out_dir = REPO_ROOT / "results" / f"issue-{args.issue}" / "artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)

    timeline_rows: list[dict[str, object]] = []
    summary_rows: list[dict[str, object]] = []

    tasks: list[dict[str, object]] = []
    for seed in args.seeds:
        for method in METHODS:
            tasks.append(
                {
                    "topology": args.topology,
                    "seed": seed,
                    "method": method,
                    "nodes": args.nodes,
                    "edge_prob": args.edge_prob,
                    "ba_attach": args.ba_attach,
                    "ticks": args.ticks,
                    "hotspot_node": args.hotspot_node,
                    "hotspot_start": args.hotspot_start,
                    "hotspot_end": args.hotspot_end,
                    "num_flows": args.num_flows,
                    "base_demand": args.base_demand,
                    "hotspot_share": args.hotspot_share,
                    "burst_multiplier": args.burst_multiplier,
                    "link_capacity": args.link_capacity,
                    "link_delay": args.link_delay,
                    "queue_capacity": args.queue_capacity,
                    "pressure_gain": args.pressure_gain,
                    "delivery_target": args.delivery_target,
                }
            )

    if args.jobs <= 1:
        for idx, task in enumerate(tasks, start=1):
            result = run_case(task)
            timeline_rows.extend(result["timeline_rows"])
            summary_rows.append(result["detail_row"])
            print(f"[shock] {idx}/{len(tasks)} method={task['method']} seed={task['seed']}")
    else:
        with ProcessPoolExecutor(max_workers=args.jobs) as ex:
            future_map = {ex.submit(run_case, task): task for task in tasks}
            for idx, future in enumerate(as_completed(future_map), start=1):
                task = future_map[future]
                result = future.result()
                timeline_rows.extend(result["timeline_rows"])
                summary_rows.append(result["detail_row"])
                print(f"[shock] {idx}/{len(tasks)} method={task['method']} seed={task['seed']}")

    agg_rows: list[dict[str, object]] = []
    ticks = sorted({int(r["tick"]) for r in timeline_rows})
    for method in METHODS:
        per_method = [r for r in timeline_rows if r["method"] == method]
        for tick in ticks:
            rows = [r for r in per_method if int(r["tick"]) == tick]
            agg_rows.append(
                {
                    "method": method,
                    "tick": tick,
                    "time_ms": tick * 10,
                    "burst_active": rows[0]["burst_active"],
                    "hotspot_pressure_mean": mean([float(r["hotspot_pressure"]) for r in rows]),
                    "delivery_ratio_mean": mean([float(r["delivery_ratio"]) for r in rows]),
                    "delivery_ratio_ci95": ci95([float(r["delivery_ratio"]) for r in rows]),
                    "control_messages_mean": mean([float(r["control_messages"]) for r in rows]),
                    "control_messages_ci95": ci95([float(r["control_messages"]) for r in rows]),
                    "control_bytes_mean": mean([float(r["control_bytes"]) for r in rows]),
                    "control_bytes_ci95": ci95([float(r["control_bytes"]) for r in rows]),
                    "route_changes_mean": mean([float(r["route_changes"]) for r in rows]),
                    "route_changes_ci95": ci95([float(r["route_changes"]) for r in rows]),
                }
            )

    config = vars(args)
    stem = (
        f"shock_response_matrix_er_n{args.nodes}_s{len(args.seeds)}"
        if args.topology == "er"
        else f"shock_response_matrix_{args.topology}_n{args.nodes}_s{len(args.seeds)}"
    )
    detail_csv = out_dir / f"{stem}_timeline.csv"
    summary_csv = out_dir / f"{stem}_detail.csv"
    mean_csv = out_dir / f"{stem}_summary.csv"
    summary_json = out_dir / f"{stem}_summary.json"

    with detail_csv.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(timeline_rows[0].keys()))
        writer.writeheader()
        writer.writerows(timeline_rows)

    with summary_csv.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(summary_rows[0].keys()))
        writer.writeheader()
        writer.writerows(summary_rows)

    with mean_csv.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(agg_rows[0].keys()))
        writer.writeheader()
        writer.writerows(agg_rows)

    summary = {"config": config, "per_seed": summary_rows}
    summary_json.write_text(json.dumps(summary, indent=2))
    print(detail_csv)
    print(summary_csv)
    print(mean_csv)
    print(summary_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
