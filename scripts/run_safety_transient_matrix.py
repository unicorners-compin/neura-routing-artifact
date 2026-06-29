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


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def ci95(values: list[float]) -> float:
    if len(values) <= 1:
        return 0.0
    import math

    m = mean(values)
    var = sum((x - m) ** 2 for x in values) / (len(values) - 1)
    return 1.96 * math.sqrt(var) / math.sqrt(len(values))


def scenario_config(name: str) -> dict[str, object]:
    if name == "hotspot_3x":
        return {
            "ticks": 160,
            "hotspot_start": 40,
            "hotspot_end": 70,
            "burst_multiplier": 3.0,
            "burst_windows": None,
            "base_demand": 0.2,
            "hotspot_share": 0.6,
            "degrade_node": None,
            "degrade_start": None,
            "degrade_end": None,
            "degrade_factor": 1.0,
            "active_windows": [(40, 70)],
        }
    if name == "continuous_chaos":
        return {
            "ticks": 140,
            "hotspot_start": 30,
            "hotspot_end": 45,
            "burst_multiplier": 3.0,
            "burst_windows": [(30, 45, 3.0), (60, 75, 3.0), (90, 105, 3.0)],
            "base_demand": 0.2,
            "hotspot_share": 0.6,
            "degrade_node": None,
            "degrade_start": None,
            "degrade_end": None,
            "degrade_factor": 1.0,
            "active_windows": [(30, 45), (60, 75), (90, 105)],
        }
    if name == "gray_failure":
        return {
            "ticks": 120,
            "hotspot_start": 40,
            "hotspot_end": 70,
            "burst_multiplier": 1.0,
            "burst_windows": [],
            "base_demand": 0.22,
            "hotspot_share": 0.7,
            "degrade_node": 1,
            "degrade_start": 40,
            "degrade_end": 70,
            "degrade_factor": 0.30,
            "active_windows": [(40, 70)],
        }
    raise ValueError(name)


def post_startup_rows(result: dict[str, object]) -> list[dict[str, object]]:
    timeline = list(result["timeline"])
    startup_tick = result["startup_summary"]["full_reachability_tick"]
    if startup_tick is None:
        return timeline
    rows = [row for row in timeline if int(row["tick"]) >= int(startup_tick)]
    return rows or timeline


def _episode_metrics(
    rows: list[dict[str, object]],
    *,
    predicate,
    last_active_tick: int,
) -> dict[str, float | int | None]:
    active_ticks = 0
    episode_count = 0
    longest_episode_ticks = 0
    post_active_ticks = 0
    post_episode_count = 0
    longest_post_episode_ticks = 0
    first_tick = None
    last_tick = None

    current = 0
    current_post = 0
    prev_active = False
    prev_post_active = False

    for row in rows:
        tick = int(row["tick"])
        active = bool(predicate(row))
        post_active = active and tick > last_active_tick

        if active:
            active_ticks += 1
            current += 1
            if not prev_active:
                episode_count += 1
            longest_episode_ticks = max(longest_episode_ticks, current)
            first_tick = tick if first_tick is None else min(first_tick, tick)
            last_tick = tick if last_tick is None else max(last_tick, tick)
        else:
            current = 0

        if post_active:
            post_active_ticks += 1
            current_post += 1
            if not prev_post_active:
                post_episode_count += 1
            longest_post_episode_ticks = max(longest_post_episode_ticks, current_post)
        else:
            current_post = 0

        prev_active = active
        prev_post_active = post_active

    return {
        "active_ticks": active_ticks,
        "episode_count": episode_count,
        "longest_episode_ticks": longest_episode_ticks,
        "post_active_ticks": post_active_ticks,
        "post_episode_count": post_episode_count,
        "longest_post_episode_ticks": longest_post_episode_ticks,
        "first_active_tick": first_tick,
        "last_active_tick": last_tick,
    }


def run_case(task: dict[str, object]) -> dict[str, object]:
    cfg = scenario_config(str(task["scenario"]))
    result = run_closed_loop(
        method=str(task["method"]),
        topology=str(task["topology"]),
        node_count=int(task["nodes"]),
        edge_prob=float(task["edge_prob"]),
        ba_attach=int(task["ba_attach"]),
        ticks=int(cfg["ticks"]),
        seed=int(task["seed"]),
        hotspot_node=int(task["hotspot_node"]),
        hotspot_start=int(cfg["hotspot_start"]),
        hotspot_end=int(cfg["hotspot_end"]),
        num_flows=int(task["num_flows"]),
        base_demand=float(cfg["base_demand"]),
        hotspot_share=float(cfg["hotspot_share"]),
        burst_multiplier=float(cfg["burst_multiplier"]),
        link_capacity=float(task["link_capacity"]),
        link_delay=float(task["link_delay"]),
        queue_capacity=float(task["queue_capacity"]),
        pressure_gain=float(task["pressure_gain"]),
        burst_windows=cfg["burst_windows"],
        degrade_node=cfg["degrade_node"],
        degrade_start=cfg["degrade_start"],
        degrade_end=cfg["degrade_end"],
        degrade_factor=float(cfg["degrade_factor"]),
    )
    rows = post_startup_rows(result)
    last_active_tick = max(end for start, end in cfg["active_windows"])

    loop_stats = _episode_metrics(
        rows,
        predicate=lambda row: float(row["loop_ratio"]) > 0.0,
        last_active_tick=last_active_tick,
    )
    blackhole_stats = _episode_metrics(
        rows,
        predicate=lambda row: float(row["blackhole_ratio"]) > 0.0,
        last_active_tick=last_active_tick,
    )
    reachability_stats = _episode_metrics(
        rows,
        predicate=lambda row: float(row["pair_reachability_ratio"]) < 0.999,
        last_active_tick=last_active_tick,
    )
    incomplete_route_stats = _episode_metrics(
        rows,
        predicate=lambda row: float(row["node_complete_route_ratio"]) < 0.999,
        last_active_tick=last_active_tick,
    )

    out = {
        "scenario": task["scenario"],
        "topology": task["topology"],
        "seed": task["seed"],
        "method": task["method"],
        "startup_full_reachability_tick": result["startup_summary"]["full_reachability_tick"],
    }
    for prefix, stats in (
        ("loop", loop_stats),
        ("blackhole", blackhole_stats),
        ("reachability_gap", reachability_stats),
        ("incomplete_route", incomplete_route_stats),
    ):
        for key, value in stats.items():
            out[f"{prefix}_{key}"] = value
    return out


def summarize_rows(rows: list[dict[str, object]], prefix: str) -> dict[str, float]:
    return {
        f"{prefix}_active_ticks_mean": mean([float(row[f"{prefix}_active_ticks"]) for row in rows]),
        f"{prefix}_active_ticks_ci95": ci95([float(row[f"{prefix}_active_ticks"]) for row in rows]),
        f"{prefix}_active_ticks_worst": max(float(row[f"{prefix}_active_ticks"]) for row in rows),
        f"{prefix}_episode_count_mean": mean([float(row[f"{prefix}_episode_count"]) for row in rows]),
        f"{prefix}_longest_episode_ticks_mean": mean([float(row[f"{prefix}_longest_episode_ticks"]) for row in rows]),
        f"{prefix}_longest_episode_ticks_worst": max(float(row[f"{prefix}_longest_episode_ticks"]) for row in rows),
        f"{prefix}_post_active_ticks_mean": mean([float(row[f"{prefix}_post_active_ticks"]) for row in rows]),
        f"{prefix}_post_active_ticks_worst": max(float(row[f"{prefix}_post_active_ticks"]) for row in rows),
        f"{prefix}_post_episode_count_mean": mean([float(row[f"{prefix}_post_episode_count"]) for row in rows]),
        f"{prefix}_longest_post_episode_ticks_mean": mean(
            [float(row[f"{prefix}_longest_post_episode_ticks"]) for row in rows]
        ),
        f"{prefix}_longest_post_episode_ticks_worst": max(
            float(row[f"{prefix}_longest_post_episode_ticks"]) for row in rows
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Run post-startup safety transient analysis for the NEURA paper.")
    ap.add_argument("--issue", type=int, default=4)
    ap.add_argument("--topology", choices=("er", "ba"), default="er")
    ap.add_argument("--nodes", type=int, default=100)
    ap.add_argument("--edge-prob", type=float, default=0.06)
    ap.add_argument("--ba-attach", type=int, default=2)
    ap.add_argument("--seeds", type=int, nargs="+", default=[121, 122, 123, 124, 125])
    ap.add_argument("--hotspot-node", type=int, default=1)
    ap.add_argument("--num-flows", type=int, default=400)
    ap.add_argument("--link-capacity", type=float, default=10.0)
    ap.add_argument("--link-delay", type=float, default=1.0)
    ap.add_argument("--queue-capacity", type=float, default=30.0)
    ap.add_argument("--pressure-gain", type=float, default=1.8)
    ap.add_argument(
        "--scenarios",
        nargs="+",
        default=["hotspot_3x", "continuous_chaos", "gray_failure"],
        choices=["hotspot_3x", "continuous_chaos", "gray_failure"],
    )
    ap.add_argument("--jobs", type=int, default=min(16, max(1, os.cpu_count() or 1)))
    args = ap.parse_args()

    out_dir = REPO_ROOT / "results" / f"issue-{args.issue}" / "artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)

    tasks: list[dict[str, object]] = []
    for scenario in args.scenarios:
        for seed in args.seeds:
            for method in METHODS:
                tasks.append(
                    {
                        "scenario": scenario,
                        "topology": args.topology,
                        "nodes": args.nodes,
                        "edge_prob": args.edge_prob,
                        "ba_attach": args.ba_attach,
                        "seed": seed,
                        "method": method,
                        "hotspot_node": args.hotspot_node,
                        "num_flows": args.num_flows,
                        "link_capacity": args.link_capacity,
                        "link_delay": args.link_delay,
                        "queue_capacity": args.queue_capacity,
                        "pressure_gain": args.pressure_gain,
                    }
                )

    detail_rows: list[dict[str, object]] = []
    if args.jobs <= 1:
        for idx, task in enumerate(tasks, start=1):
            detail_rows.append(run_case(task))
            print(
                f"[safety-transient] {idx}/{len(tasks)} scenario={task['scenario']} "
                f"method={task['method']} seed={task['seed']}"
            )
    else:
        with ProcessPoolExecutor(max_workers=args.jobs) as ex:
            future_map = {ex.submit(run_case, task): task for task in tasks}
            for idx, future in enumerate(as_completed(future_map), start=1):
                task = future_map[future]
                detail_rows.append(future.result())
                print(
                    f"[safety-transient] {idx}/{len(tasks)} scenario={task['scenario']} "
                    f"method={task['method']} seed={task['seed']}"
                )

    detail_rows.sort(key=lambda row: (str(row["scenario"]), str(row["method"]), int(row["seed"])))

    summary_rows: list[dict[str, object]] = []
    for scenario in args.scenarios:
        for method in METHODS:
            rows = [row for row in detail_rows if row["scenario"] == scenario and row["method"] == method]
            summary = {
                "scenario": scenario,
                "method": method,
                "startup_full_reachability_tick_mean": mean(
                    [float(row["startup_full_reachability_tick"]) for row in rows]
                ),
            }
            for prefix in ("loop", "blackhole", "reachability_gap", "incomplete_route"):
                summary.update(summarize_rows(rows, prefix))
            summary_rows.append(summary)

    stem = (
        f"safety_transient_matrix_er_n{args.nodes}_s{len(args.seeds)}"
        if args.topology == "er"
        else f"safety_transient_matrix_{args.topology}_n{args.nodes}_s{len(args.seeds)}"
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
