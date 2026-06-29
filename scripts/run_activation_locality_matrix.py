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

from run_snn_mechanism_studies import build_params
from src.snn_sra_sim import SimulationEngine, generate_connected_topology
from src.snn_sra_sim.algorithms import (
    OspfTeParams,
    OspfTeStyleAlgorithm,
    SnnSraAlgorithm,
    TriggeredTeParams,
    TriggeredTeStyleAlgorithm,
)
from src.snn_sra_sim.topology import links_to_neighbor_map


METHODS = ("snn_baseline", "snn_full", "triggered_te", "ospf_te_t5", "ospf_te_t1")


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den_x = sum((x - mean_x) ** 2 for x in xs) ** 0.5
    den_y = sum((y - mean_y) ** 2 for y in ys) ** 0.5
    if den_x == 0.0 or den_y == 0.0:
        return None
    return num / (den_x * den_y)


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


def choose_hotspot(node_count: int, neighbor_map: dict[int, list[int]]) -> int:
    best_node = 1
    best_degree = -1
    for node_id in range(1, node_count + 1):
        degree = len(neighbor_map.get(node_id, []))
        if degree > best_degree:
            best_node = node_id
            best_degree = degree
    return best_node


def build_algorithm(method: str):
    if method == "snn_baseline":
        return SnnSraAlgorithm(build_params("baseline"))
    if method == "snn_full":
        return SnnSraAlgorithm(build_params("full"))
    if method == "triggered_te":
        return TriggeredTeStyleAlgorithm(TriggeredTeParams())
    if method == "ospf_te_t5":
        return OspfTeStyleAlgorithm(OspfTeParams(periodic_interval_ticks=5))
    if method == "ospf_te_t1":
        return OspfTeStyleAlgorithm(OspfTeParams(periodic_interval_ticks=1))
    raise ValueError(method)


def activation_metrics(
    summary: dict,
    distances: dict[int, int],
    hotspot_start: int,
    hotspot_end: int,
    hotspot_node: int,
) -> dict[str, float | int | None]:
    per_node_emit_counts: dict[int, int] = {}
    for node_id_str, ticks in summary["emit_tick_log"].items():
        node_id = int(node_id_str)
        count = sum(1 for tick in ticks if hotspot_start <= int(tick) <= hotspot_end)
        per_node_emit_counts[node_id] = count

    active_nodes = [node_id for node_id, count in per_node_emit_counts.items() if count > 0]
    total_emit_events = sum(per_node_emit_counts.values())
    weighted_distances: list[float] = []
    repeated_dists: list[float] = []
    near_emit = 0
    far_emit = 0
    hotspot_emit = per_node_emit_counts.get(hotspot_node, 0)
    zero_far_nodes = 0
    far_nodes = 0
    zero_outer_nodes = 0
    outer_nodes = 0
    node_distance_values: list[float] = []
    node_emit_values: list[float] = []
    for node_id, dist in distances.items():
        count = per_node_emit_counts.get(node_id, 0)
        node_distance_values.append(float(dist))
        node_emit_values.append(float(count))
        if count > 0:
            weighted_distances.extend([float(dist)] * count)
        if dist <= 2:
            near_emit += count
        if dist >= 5:
            far_emit += count
            far_nodes += 1
            if count == 0:
                zero_far_nodes += 1
        if dist >= 3:
            outer_nodes += 1
            if count == 0:
                zero_outer_nodes += 1

    weighted_mean_distance = mean(weighted_distances)
    sorted_weighted = sorted(weighted_distances)
    p90_radius = None
    if sorted_weighted:
        idx = min(len(sorted_weighted) - 1, max(0, int(0.9 * len(sorted_weighted)) - 1))
        p90_radius = sorted_weighted[idx]

    active_distances = [distances[node_id] for node_id in active_nodes]
    return {
        "active_node_ratio": len(active_nodes) / max(len(per_node_emit_counts), 1),
        "total_emit_events_under_hotspot": total_emit_events,
        "weighted_mean_emit_distance": weighted_mean_distance,
        "max_active_distance": max(active_distances) if active_distances else None,
        "p90_emit_radius": p90_radius,
        "near_emit_share_h2": near_emit / max(total_emit_events, 1),
        "far_emit_share_h5": far_emit / max(total_emit_events, 1),
        "hotspot_emit_share": hotspot_emit / max(total_emit_events, 1),
        "far_quiet_ratio_h5": zero_far_nodes / max(far_nodes, 1),
        "outer_quiet_ratio_h3": zero_outer_nodes / max(outer_nodes, 1),
        "distance_emit_correlation": pearson(node_distance_values, node_emit_values),
    }


def distance_profile(
    summary: dict,
    distances: dict[int, int],
    hotspot_start: int,
    hotspot_end: int,
) -> list[dict[str, object]]:
    per_node_emit_counts: dict[int, int] = {}
    for node_id_str, ticks in summary["emit_tick_log"].items():
        node_id = int(node_id_str)
        count = sum(1 for tick in ticks if hotspot_start <= int(tick) <= hotspot_end)
        per_node_emit_counts[node_id] = count

    max_distance = max(distances.values()) if distances else 0
    rows: list[dict[str, object]] = []
    cumulative = 0
    total_emit_events = sum(per_node_emit_counts.values())
    for dist in range(0, max_distance + 1):
        emit_count = sum(count for node_id, count in per_node_emit_counts.items() if distances.get(node_id) == dist)
        cumulative += emit_count
        rows.append(
            {
                "distance": dist,
                "emit_count": emit_count,
                "emit_share": emit_count / max(total_emit_events, 1),
                "cdf_emit_share": cumulative / max(total_emit_events, 1),
            }
        )
    return rows


def run_method(
    method: str,
    topology: str,
    node_count: int,
    edge_prob: float,
    ba_attach: int,
    geo_radius: float,
    ticks: int,
    seed: int,
    hotspot_start: int,
    hotspot_end: int,
    hotspot_pressure: float,
) -> dict[str, object]:
    links = generate_connected_topology(
        kind=topology,
        node_count=node_count,
        seed=seed,
        edge_prob=edge_prob,
        attach_edges=ba_attach,
        geo_radius=geo_radius,
    )
    neighbor_map = links_to_neighbor_map(links)
    hotspot_node = choose_hotspot(node_count, neighbor_map)
    distances = bfs_distances(node_count, neighbor_map, hotspot_node)
    engine = SimulationEngine(
        node_count=node_count,
        links=links,
        algorithm=build_algorithm(method),
    )
    summary = engine.run(
        total_ticks=ticks,
        hotspot=(hotspot_start, hotspot_end, hotspot_node, hotspot_pressure),
        snapshot_ticks=[],
    ).summary
    metrics = activation_metrics(summary, distances, hotspot_start, hotspot_end, hotspot_node)
    profile_rows = distance_profile(summary, distances, hotspot_start, hotspot_end)
    return {
        "method": method,
        "topology": topology,
        "seed": seed,
        "hotspot_node": hotspot_node,
        "peak_event_rate": summary["peak_event_rate"],
        "initial_convergence_tick": summary["initial_convergence_tick"],
        **metrics,
        "distance_profile": profile_rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Measure hotspot activation locality at 100-node scale.")
    ap.add_argument("--issue", type=int, default=4)
    ap.add_argument("--topology", choices=("er", "ba", "rgg"), default="er")
    ap.add_argument("--nodes", type=int, default=100)
    ap.add_argument("--edge-prob", type=float, default=0.06)
    ap.add_argument("--ba-attach", type=int, default=2)
    ap.add_argument("--geo-radius", type=float, default=0.17)
    ap.add_argument("--ticks", type=int, default=60)
    ap.add_argument("--seeds", type=int, nargs="+", default=[91, 92, 93, 94, 95])
    ap.add_argument("--hotspot-start", type=int, default=25)
    ap.add_argument("--hotspot-end", type=int, default=45)
    ap.add_argument("--hotspot-pressure", type=float, default=1.0)
    args = ap.parse_args()

    out_dir = REPO_ROOT / "results" / f"issue-{args.issue}" / "artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)

    detail_rows: list[dict[str, object]] = []
    distance_rows: list[dict[str, object]] = []
    for seed in args.seeds:
        for method in METHODS:
            result = run_method(
                method=method,
                topology=args.topology,
                node_count=args.nodes,
                edge_prob=args.edge_prob,
                ba_attach=args.ba_attach,
                geo_radius=args.geo_radius,
                ticks=args.ticks,
                seed=seed,
                hotspot_start=args.hotspot_start,
                hotspot_end=args.hotspot_end,
                hotspot_pressure=args.hotspot_pressure,
            )
            profile_rows = result.pop("distance_profile")
            detail_rows.append(result)
            for row in profile_rows:
                distance_rows.append(
                    {
                        "method": method,
                        "topology": args.topology,
                        "seed": seed,
                        **row,
                    }
                )

    summary = {"config": vars(args), "method_means": {}}
    numeric_fields = [
        "peak_event_rate",
        "initial_convergence_tick",
        "active_node_ratio",
        "total_emit_events_under_hotspot",
        "weighted_mean_emit_distance",
        "near_emit_share_h2",
        "far_emit_share_h5",
        "hotspot_emit_share",
        "far_quiet_ratio_h5",
        "outer_quiet_ratio_h3",
    ]
    optional_fields = [
        "max_active_distance",
        "p90_emit_radius",
        "distance_emit_correlation",
    ]
    for method in METHODS:
        rows = [row for row in detail_rows if row["method"] == method]
        summary["method_means"][method] = {}
        for field in numeric_fields:
            vals = [float(row[field]) for row in rows if row[field] is not None]
            summary["method_means"][method][field] = mean(vals) if vals else None
        for field in optional_fields:
            vals = [float(row[field]) for row in rows if row[field] is not None]
            summary["method_means"][method][field] = mean(vals) if vals else None

    stem = (
        f"activation_locality_matrix_er_n{args.nodes}_s{len(args.seeds)}"
        if args.topology == "er"
        else f"activation_locality_matrix_{args.topology}_n{args.nodes}_s{len(args.seeds)}"
    )
    detail_csv = out_dir / f"{stem}_detail.csv"
    distance_csv = out_dir / f"{stem}_distance_profile.csv"
    summary_csv = out_dir / f"{stem}_summary.csv"
    summary_json = out_dir / f"{stem}_summary.json"

    with detail_csv.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(detail_rows[0].keys()))
        writer.writeheader()
        writer.writerows(detail_rows)

    with distance_csv.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(distance_rows[0].keys()))
        writer.writeheader()
        writer.writerows(distance_rows)

    with summary_csv.open("w", newline="") as f:
        fieldnames = ["method"] + list(next(iter(summary["method_means"].values())).keys())
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for method in METHODS:
            writer.writerow({"method": method, **summary["method_means"][method]})

    summary_json.write_text(json.dumps(summary, indent=2))
    print(detail_csv)
    print(distance_csv)
    print(summary_csv)
    print(summary_json)
    print(json.dumps(summary["method_means"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
