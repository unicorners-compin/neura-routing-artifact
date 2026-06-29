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


def parse_window(spec: str) -> tuple[int, int, float]:
    window, multiplier = spec.split(":")
    start, end = window.split("-")
    return int(start), int(end), float(multiplier)


def calm_between(
    timeline: list[dict[str, object]],
    start_tick: int,
    next_start_tick: int,
    delivery_threshold: float,
) -> bool:
    calm_run = 0
    for row in timeline:
        tick = int(row["tick"])
        if tick <= start_tick or tick >= next_start_tick:
            continue
        control_quiet = float(row["emitted_this_tick"]) == 0.0
        pressure_quiet = float(row["hotspot_pressure"]) <= 0.2
        service_ok = float(row["delivery_ratio"]) >= delivery_threshold
        if control_quiet and pressure_quiet and service_ok:
            calm_run += 1
            if calm_run >= 2:
                return True
        else:
            calm_run = 0
    return False


def run_case(task: dict[str, object]) -> dict[str, object]:
    burst_windows = list(task["burst_windows"])
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
        hotspot_start=int(burst_windows[0][0]),
        hotspot_end=int(burst_windows[0][1]),
        num_flows=int(task["num_flows"]),
        base_demand=float(task["base_demand"]),
        hotspot_share=float(task["hotspot_share"]),
        burst_multiplier=float(burst_windows[0][2]),
        link_capacity=float(task["link_capacity"]),
        link_delay=float(task["link_delay"]),
        queue_capacity=float(task["queue_capacity"]),
        pressure_gain=float(task["pressure_gain"]),
        burst_windows=burst_windows,
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
                "burst_multiplier": float(row["burst_multiplier"]),
                "hotspot_pressure": float(row["hotspot_pressure"]),
                "delivery_ratio": float(row["delivery_ratio"]),
                "control_messages": emitted,
                "control_bytes": emitted * CONTROL_RECORD_BYTES,
                "route_changes": float(row["route_changes_this_tick"]),
            }
        )

    service_loss_ticks = sum(1 for row in timeline if float(row["delivery_ratio"]) < float(task["delivery_threshold"]))
    service_loss_area = sum(
        max(0.0, float(task["delivery_threshold"]) - float(row["delivery_ratio"]))
        for row in timeline
    )
    post_burst_active_ticks = sum(
        1
        for row in timeline
        if not int(row["burst_active"]) and float(row["emitted_this_tick"]) > 0.0
    )
    recovery_failures = 0
    for idx, (_, end, _) in enumerate(burst_windows[:-1]):
        next_start = burst_windows[idx + 1][0]
        if not calm_between(timeline, int(end), int(next_start), float(task["delivery_threshold"])):
            recovery_failures += 1

    detail_row = {
        "topology": task["topology"],
        "seed": task["seed"],
        "method": task["method"],
        "service_loss_ticks": service_loss_ticks,
        "service_loss_area": service_loss_area,
        "post_burst_active_ticks": post_burst_active_ticks,
        "recovery_failures": recovery_failures,
        "total_control_bytes": result["totals"]["emitted_updates"] * CONTROL_RECORD_BYTES,
        "total_route_changes": result["totals"]["route_changes"],
        "route_changes_per_node": result["totals"]["route_changes"] / float(task["nodes"]),
        "peak_route_changes_per_tick": max(float(row["route_changes_this_tick"]) for row in timeline),
        "peak_event_rate": result["peak_event_rate"],
        "mean_burst_delivery_ratio": result["under_burst_summary"]["mean_delivery_ratio"],
    }
    return {"timeline_rows": timeline_rows, "detail_row": detail_row}


def main() -> int:
    ap = argparse.ArgumentParser(description="Run continuous-chaos disturbance experiments for the localized control-plane paper.")
    ap.add_argument("--issue", type=int, default=4)
    ap.add_argument("--topology", choices=("er", "ba", "rgg"), default="er")
    ap.add_argument("--nodes", type=int, default=100)
    ap.add_argument("--edge-prob", type=float, default=0.06)
    ap.add_argument("--ba-attach", type=int, default=2)
    ap.add_argument("--geo-radius", type=float, default=0.17)
    ap.add_argument("--ticks", type=int, default=140)
    ap.add_argument("--seeds", type=int, nargs="+", default=[121, 122, 123, 124, 125])
    ap.add_argument("--hotspot-node", type=int, default=1)
    ap.add_argument("--burst-windows", type=str, nargs="+", default=["30-45:3.0", "60-75:3.0", "90-105:3.0"])
    ap.add_argument("--num-flows", type=int, default=400)
    ap.add_argument("--base-demand", type=float, default=0.2)
    ap.add_argument("--hotspot-share", type=float, default=0.6)
    ap.add_argument("--link-capacity", type=float, default=10.0)
    ap.add_argument("--link-delay", type=float, default=1.0)
    ap.add_argument("--queue-capacity", type=float, default=30.0)
    ap.add_argument("--pressure-gain", type=float, default=1.8)
    ap.add_argument("--delivery-threshold", type=float, default=0.90)
    ap.add_argument("--jobs", type=int, default=min(16, max(1, os.cpu_count() or 1)))
    args = ap.parse_args()

    burst_windows = [parse_window(spec) for spec in args.burst_windows]
    out_dir = REPO_ROOT / "results" / f"issue-{args.issue}" / "artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)

    tasks: list[dict[str, object]] = []
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
                    "num_flows": args.num_flows,
                    "base_demand": args.base_demand,
                    "hotspot_share": args.hotspot_share,
                    "link_capacity": args.link_capacity,
                    "link_delay": args.link_delay,
                    "queue_capacity": args.queue_capacity,
                    "pressure_gain": args.pressure_gain,
                    "delivery_threshold": args.delivery_threshold,
                    "burst_windows": burst_windows,
                }
            )

    timeline_rows: list[dict[str, object]] = []
    per_seed_rows: list[dict[str, object]] = []
    if args.jobs <= 1:
        for idx, task in enumerate(tasks, start=1):
            result = run_case(task)
            timeline_rows.extend(result["timeline_rows"])
            per_seed_rows.append(result["detail_row"])
            print(f"[chaos] {idx}/{len(tasks)} method={task['method']} seed={task['seed']}")
    else:
        with ProcessPoolExecutor(max_workers=args.jobs) as ex:
            future_map = {ex.submit(run_case, task): task for task in tasks}
            for idx, future in enumerate(as_completed(future_map), start=1):
                task = future_map[future]
                result = future.result()
                timeline_rows.extend(result["timeline_rows"])
                per_seed_rows.append(result["detail_row"])
                print(f"[chaos] {idx}/{len(tasks)} method={task['method']} seed={task['seed']}")

    timeline_rows.sort(key=lambda row: (str(row["method"]), int(row["seed"]), int(row["tick"])))
    per_seed_rows.sort(key=lambda row: (str(row["method"]), int(row["seed"])))

    ticks = sorted({int(row["tick"]) for row in timeline_rows})
    mean_rows: list[dict[str, object]] = []
    for method in METHODS:
        method_rows = [row for row in timeline_rows if row["method"] == method]
        for tick in ticks:
            rows = [row for row in method_rows if int(row["tick"]) == tick]
            mean_rows.append(
                {
                    "method": method,
                    "tick": tick,
                    "time_ms": tick * TICK_MS,
                    "burst_active": rows[0]["burst_active"],
                    "burst_multiplier": rows[0]["burst_multiplier"],
                    "delivery_mean": mean([float(row["delivery_ratio"]) for row in rows]),
                    "delivery_ci95": ci95([float(row["delivery_ratio"]) for row in rows]),
                    "control_bytes_mean": mean([float(row["control_bytes"]) for row in rows]),
                    "control_bytes_ci95": ci95([float(row["control_bytes"]) for row in rows]),
                    "route_changes_mean": mean([float(row["route_changes"]) for row in rows]),
                    "route_changes_ci95": ci95([float(row["route_changes"]) for row in rows]),
                    "pressure_mean": mean([float(row["hotspot_pressure"]) for row in rows]),
                }
            )

    summary_rows: list[dict[str, object]] = []
    for method in METHODS:
        rows = [row for row in per_seed_rows if row["method"] == method]
        summary_rows.append(
            {
                "method": method,
                "service_loss_ticks_mean": mean([float(row["service_loss_ticks"]) for row in rows]),
                "service_loss_ticks_ci95": ci95([float(row["service_loss_ticks"]) for row in rows]),
                "service_loss_area_mean": mean([float(row["service_loss_area"]) for row in rows]),
                "service_loss_area_ci95": ci95([float(row["service_loss_area"]) for row in rows]),
                "post_burst_active_ticks_mean": mean([float(row["post_burst_active_ticks"]) for row in rows]),
                "post_burst_active_ticks_ci95": ci95([float(row["post_burst_active_ticks"]) for row in rows]),
                "recovery_failures_mean": mean([float(row["recovery_failures"]) for row in rows]),
                "recovery_failures_ci95": ci95([float(row["recovery_failures"]) for row in rows]),
                "total_control_bytes_mean": mean([float(row["total_control_bytes"]) for row in rows]),
                "total_control_bytes_ci95": ci95([float(row["total_control_bytes"]) for row in rows]),
                "route_changes_per_node_mean": mean([float(row["route_changes_per_node"]) for row in rows]),
                "route_changes_per_node_ci95": ci95([float(row["route_changes_per_node"]) for row in rows]),
                "peak_route_changes_per_tick_mean": mean([float(row["peak_route_changes_per_tick"]) for row in rows]),
                "peak_route_changes_per_tick_ci95": ci95([float(row["peak_route_changes_per_tick"]) for row in rows]),
                "burst_delivery_mean": mean([float(row["mean_burst_delivery_ratio"]) for row in rows]),
                "burst_delivery_ci95": ci95([float(row["mean_burst_delivery_ratio"]) for row in rows]),
            }
        )

    stem = (
        f"continuous_chaos_matrix_er_n{args.nodes}_s{len(args.seeds)}"
        if args.topology == "er"
        else f"continuous_chaos_matrix_{args.topology}_n{args.nodes}_s{len(args.seeds)}"
    )
    timeline_csv = out_dir / f"{stem}_timeline.csv"
    detail_csv = out_dir / f"{stem}_detail.csv"
    summary_csv = out_dir / f"{stem}_summary.csv"
    summary_json = out_dir / f"{stem}_summary.json"

    with timeline_csv.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(timeline_rows[0].keys()))
        writer.writeheader()
        writer.writerows(timeline_rows)

    with detail_csv.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(per_seed_rows[0].keys()))
        writer.writeheader()
        writer.writerows(per_seed_rows)

    with summary_csv.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(summary_rows[0].keys()))
        writer.writeheader()
        writer.writerows(summary_rows)

    summary_json.write_text(
        json.dumps(
            {
                "config": vars(args),
                "burst_windows": [
                    {"start": start, "end": end, "multiplier": multiplier}
                    for start, end, multiplier in burst_windows
                ],
                "rows": summary_rows,
            },
            indent=2,
        )
    )
    print(timeline_csv)
    print(detail_csv)
    print(summary_csv)
    print(summary_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
