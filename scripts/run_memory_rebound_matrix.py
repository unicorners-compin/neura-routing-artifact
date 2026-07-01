#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from collections import deque
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from run_snn_mechanism_studies import build_params, route_change_count
from src.snn_sra_sim import SimulationEngine, generate_connected_topology
from src.snn_sra_sim.algorithms import SnnSraAlgorithm, SnnSraParams
from src.snn_sra_sim.topology import links_to_neighbor_map


VARIANTS = ("baseline", "memory_only", "full")


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def choose_hotspot_pair(node_count: int, neighbor_map: dict[int, list[int]]) -> tuple[int, int]:
    ranked = sorted(range(1, node_count + 1), key=lambda n: len(neighbor_map.get(n, [])), reverse=True)
    primary = ranked[0]
    dist = bfs_distances(node_count, neighbor_map, primary)
    secondary = max(ranked[1:], key=lambda n: (dist.get(n, -1), len(neighbor_map.get(n, []))))
    return primary, secondary


def bfs_distances(node_count: int, neighbor_map: dict[int, list[int]], source: int) -> dict[int, int]:
    dist = {node_id: -1 for node_id in range(1, node_count + 1)}
    dist[source] = 0
    q: deque[int] = deque([source])
    while q:
        cur = q.popleft()
        for nbr in neighbor_map.get(cur, []):
            if dist[nbr] != -1:
                continue
            dist[nbr] = dist[cur] + 1
            q.append(nbr)
    return dist


def build_pressure_schedule(
    stage1_node: int,
    stage2_node: int,
    stage1_start: int,
    stage1_end: int,
    stage2_start: int,
    stage2_end: int,
    pressure: float,
) -> dict[int, dict[int, float]]:
    schedule: dict[int, dict[int, float]] = {}
    for tick in range(stage1_start, stage1_end + 1):
        schedule[tick] = {stage1_node: pressure}
    for tick in range(stage2_start, stage2_end + 1):
        schedule.setdefault(tick, {})
        schedule[tick][stage2_node] = pressure
    return schedule


def run_variant(
    *,
    mode: str,
    topology: str,
    node_count: int,
    edge_prob: float,
    ba_attach: int,
    ticks: int,
    seed: int,
    stage1_start: int,
    stage1_end: int,
    stage2_start: int,
    stage2_end: int,
    hotspot_pressure: float,
    params_override: SnnSraParams | None = None,
) -> dict[str, object]:
    links = generate_connected_topology(
        kind=topology,
        node_count=node_count,
        seed=seed,
        edge_prob=edge_prob,
        attach_edges=ba_attach,
    )
    neighbor_map = links_to_neighbor_map(links)
    stage1_node, stage2_node = choose_hotspot_pair(node_count, neighbor_map)
    engine = SimulationEngine(
        node_count=node_count,
        links=links,
        algorithm=SnnSraAlgorithm(params_override or build_params(mode)),
    )
    pressure_schedule = build_pressure_schedule(
        stage1_node=stage1_node,
        stage2_node=stage2_node,
        stage1_start=stage1_start,
        stage1_end=stage1_end,
        stage2_start=stage2_start,
        stage2_end=stage2_end,
        pressure=hotspot_pressure,
    )
    summary = engine.run(
        total_ticks=ticks,
        pressure_schedule=pressure_schedule,
        snapshot_ticks=list(range(ticks)),
    ).summary
    snapshots = summary["forwarding_snapshots"]

    stage1_pre = snapshots[str(max(0, stage1_start - 1))]
    stage1_peak = snapshots[str(stage1_start + max(0, (stage1_end - stage1_start) // 2))]
    stage2_peak = snapshots[str(stage2_start + max(0, (stage2_end - stage2_start) // 2))]
    final_snapshot = snapshots[str(ticks - 1)]

    stage1_escape = route_change_count(stage1_pre, stage1_peak)
    rebound_after_release = route_change_count(stage1_peak, stage2_peak)
    post_stage2_churn = 0
    for tick in range(stage2_end + 1, ticks):
        post_stage2_churn += route_change_count(snapshots[str(tick - 1)], snapshots[str(tick)])

    return_count = route_change_count(final_snapshot, stage1_peak)
    fire_log = summary["fire_tick_log"]
    stage2_fire = 0
    after_stage2_fire = 0
    timeline: list[dict[str, object]] = []
    for ticks_list in fire_log.values():
        for tick in ticks_list:
            if stage2_start <= int(tick) <= stage2_end:
                stage2_fire += 1
            elif int(tick) > stage2_end:
                after_stage2_fire += 1
    for tick in range(1, ticks):
        prev = snapshots[str(tick - 1)]
        curr = snapshots[str(tick)]
        timeline.append(
            {
                "tick": tick,
                "route_changes": route_change_count(prev, curr),
                "phase": (
                    "stage1"
                    if stage1_start <= tick <= stage1_end
                    else "stage2"
                    if stage2_start <= tick <= stage2_end
                    else "recovery"
                    if tick > stage2_end
                    else "pre"
                ),
            }
        )
    node_metrics = summary["node_metrics"]
    return {
        "mode": mode,
        "topology": topology,
        "seed": seed,
        "stage1_node": stage1_node,
        "stage2_node": stage2_node,
        "initial_convergence_tick": summary["initial_convergence_tick"],
        "peak_event_rate": summary["peak_event_rate"],
        "stage1_escape_ratio": stage1_escape / max(len(stage1_pre), 1),
        "rebound_ratio_after_release": rebound_after_release / max(len(stage1_peak), 1),
        "post_stage2_route_changes": post_stage2_churn,
        "return_to_stage1_ratio": return_count / max(len(stage1_peak), 1),
        "after_stage2_fire_ratio": after_stage2_fire / max(stage2_fire, 1),
        "emitted_updates": sum(m["emitted_updates"] for m in node_metrics.values()),
        "route_changes": sum(m["route_changes"] for m in node_metrics.values()),
        "post_event_route_changes": sum(m["post_event_route_changes"] for m in node_metrics.values()),
        "timeline": timeline,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Measure memory-driven rebound suppression with two-stage disturbances.")
    ap.add_argument("--issue", type=int, default=4)
    ap.add_argument("--topology", choices=("er", "ba"), default="er")
    ap.add_argument("--nodes", type=int, default=100)
    ap.add_argument("--edge-prob", type=float, default=0.06)
    ap.add_argument("--ba-attach", type=int, default=2)
    ap.add_argument("--ticks", type=int, default=80)
    ap.add_argument("--seeds", type=int, nargs="+", default=[101, 102, 103, 104, 105])
    ap.add_argument("--stage1-start", type=int, default=15)
    ap.add_argument("--stage1-end", type=int, default=30)
    ap.add_argument("--stage2-start", type=int, default=40)
    ap.add_argument("--stage2-end", type=int, default=55)
    ap.add_argument("--hotspot-pressure", type=float, default=1.0)
    args = ap.parse_args()

    out_dir = REPO_ROOT / "results" / f"issue-{args.issue}" / "artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)

    detail_rows: list[dict[str, object]] = []
    timeline_rows: list[dict[str, object]] = []
    for seed in args.seeds:
        for mode in VARIANTS:
            result = run_variant(
                mode=mode,
                topology=args.topology,
                node_count=args.nodes,
                edge_prob=args.edge_prob,
                ba_attach=args.ba_attach,
                ticks=args.ticks,
                seed=seed,
                stage1_start=args.stage1_start,
                stage1_end=args.stage1_end,
                stage2_start=args.stage2_start,
                stage2_end=args.stage2_end,
                hotspot_pressure=args.hotspot_pressure,
            )
            timeline = result.pop("timeline")
            detail_rows.append(result)
            for row in timeline:
                timeline_rows.append(
                    {
                        "topology": args.topology,
                        "mode": mode,
                        "seed": seed,
                        **row,
                    }
                )

    summary = {"config": vars(args), "variant_means": {}, "study_means": {}}
    numeric_fields = [
        "initial_convergence_tick",
        "peak_event_rate",
        "stage1_escape_ratio",
        "rebound_ratio_after_release",
        "post_stage2_route_changes",
        "return_to_stage1_ratio",
        "after_stage2_fire_ratio",
        "emitted_updates",
        "route_changes",
        "post_event_route_changes",
    ]
    for mode in VARIANTS:
        rows = [row for row in detail_rows if row["mode"] == mode]
        summary["variant_means"][mode] = {field: mean([float(row[field]) for row in rows]) for field in numeric_fields}

    baseline = summary["variant_means"]["baseline"]
    memory = summary["variant_means"]["memory_only"]
    full = summary["variant_means"]["full"]
    summary["study_means"] = {
        "memory_rebound_ratio_delta": memory["rebound_ratio_after_release"] - baseline["rebound_ratio_after_release"],
        "memory_post_stage2_route_change_delta": memory["post_stage2_route_changes"] - baseline["post_stage2_route_changes"],
        "memory_return_to_stage1_ratio_delta": memory["return_to_stage1_ratio"] - baseline["return_to_stage1_ratio"],
        "memory_after_stage2_fire_ratio_delta": memory["after_stage2_fire_ratio"] - baseline["after_stage2_fire_ratio"],
        "memory_emitted_update_delta": memory["emitted_updates"] - baseline["emitted_updates"],
        "full_rebound_ratio_delta": full["rebound_ratio_after_release"] - baseline["rebound_ratio_after_release"],
        "full_post_stage2_route_change_delta": full["post_stage2_route_changes"] - baseline["post_stage2_route_changes"],
        "full_return_to_stage1_ratio_delta": full["return_to_stage1_ratio"] - baseline["return_to_stage1_ratio"],
        "full_after_stage2_fire_ratio_delta": full["after_stage2_fire_ratio"] - baseline["after_stage2_fire_ratio"],
        "full_emitted_update_delta": full["emitted_updates"] - baseline["emitted_updates"],
    }

    stem = (
        f"memory_rebound_matrix_er_n{args.nodes}_s{len(args.seeds)}"
        if args.topology == "er"
        else f"memory_rebound_matrix_{args.topology}_n{args.nodes}_s{len(args.seeds)}"
    )
    detail_csv = out_dir / f"{stem}_detail.csv"
    timeline_csv = out_dir / f"{stem}_timeline.csv"
    summary_csv = out_dir / f"{stem}_summary.csv"
    summary_json = out_dir / f"{stem}_summary.json"

    with detail_csv.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(detail_rows[0].keys()))
        writer.writeheader()
        writer.writerows(detail_rows)

    with timeline_csv.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(timeline_rows[0].keys()))
        writer.writeheader()
        writer.writerows(timeline_rows)

    with summary_csv.open("w", newline="") as f:
        fieldnames = ["section", "variant"] + numeric_fields + list(summary["study_means"].keys())
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for mode in VARIANTS:
            writer.writerow({"section": "variant_mean", "variant": mode, **summary["variant_means"][mode]})
        writer.writerow({"section": "study_mean", "variant": "delta", **summary["study_means"]})

    summary_json.write_text(json.dumps(summary, indent=2))
    print(detail_csv)
    print(timeline_csv)
    print(summary_csv)
    print(summary_json)
    print(json.dumps(summary["study_means"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
