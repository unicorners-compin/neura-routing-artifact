#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.snn_sra_sim import SimulationEngine, generate_connected_er_topology
from src.snn_sra_sim.algorithms import SnnSraAlgorithm, SnnSraParams


def build_params(mode: str) -> SnnSraParams:
    params = SnnSraParams()
    if mode == "baseline":
        params.w_memory = 0.0
        params.memory_decay = 0.0
        params.memory_pressure_gain = 0.0
        params.memory_damage_gain = 0.0
        params.memory_switch_gain = 0.0
        params.inhibit_penalty = 0.0
        params.inhibit_release = 0.0
        params.switch_guard = 0.0
        params.rebound_guard = 0.0
        params.selected_memory_boost = 0.0
        params.deselected_memory_boost = 0.0
        params.fast_leak = params.leak
        params.slow_decay = 0.0
        params.slow_leak = 0.0
        params.slow_pressure_gain = 0.0
        params.slow_damage_gain = 0.0
        params.slow_weight = 0.0
    elif mode == "memory_only":
        params.inhibit_penalty = 0.0
        params.inhibit_release = 0.0
        params.switch_guard = 0.0
        params.rebound_guard = 0.0
        params.selected_memory_boost = 0.0
        params.deselected_memory_boost = 0.0
        params.slow_decay = 0.0
        params.slow_leak = 0.0
        params.slow_pressure_gain = 0.0
        params.slow_damage_gain = 0.0
        params.slow_weight = 0.0
    elif mode == "memory_inhibition":
        params.slow_decay = 0.0
        params.slow_leak = 0.0
        params.slow_pressure_gain = 0.0
        params.slow_damage_gain = 0.0
        params.slow_weight = 0.0
    elif mode == "full":
        pass
    else:
        raise ValueError(mode)
    return params


def route_change_count(snapshot_a: dict[str, object], snapshot_b: dict[str, object]) -> int:
    changed = 0
    for key, value in snapshot_a.items():
        if snapshot_b.get(key) != value:
            changed += 1
    return changed


def compute_variant_metrics(summary: dict, hotspot_start: int, hotspot_end: int) -> dict:
    snapshots = summary["forwarding_snapshots"]
    pre_tick = max(0, hotspot_start - 1)
    pre_snapshot = snapshots[str(pre_tick)]
    peak_tick = hotspot_start + max(0, (hotspot_end - hotspot_start) // 2)
    peak_snapshot = snapshots[str(peak_tick)]

    hotspot_escape_count = route_change_count(pre_snapshot, peak_snapshot)
    hotspot_escape_ratio = hotspot_escape_count / max(len(pre_snapshot), 1)

    post_route_changes = 0
    settle_tick = None
    zero_change_streak = 0
    for tick in range(hotspot_end + 1, summary["total_ticks"]):
        prev = snapshots[str(tick - 1)]
        curr = snapshots[str(tick)]
        changed = route_change_count(prev, curr)
        post_route_changes += changed
        if changed == 0:
            zero_change_streak += 1
            if zero_change_streak >= 3 and settle_tick is None:
                settle_tick = tick - 2
        else:
            zero_change_streak = 0

    obs = summary["hotspot_metrics"]["hotspot_observation"]
    under_fire = sum(int(row["fire_delta"]) for row in obs if hotspot_start <= int(row["tick"]) <= hotspot_end)
    post_fire = sum(int(row["fire_delta"]) for row in obs if int(row["tick"]) > hotspot_end)
    post_fire_ratio = post_fire / max(under_fire, 1)

    node_metrics = summary["node_metrics"]
    totals = {
        "emitted_updates": sum(m["emitted_updates"] for m in node_metrics.values()),
        "received_updates": sum(m["received_updates"] for m in node_metrics.values()),
        "fire_count": sum(m["fire_count"] for m in node_metrics.values()),
        "route_changes": sum(m["route_changes"] for m in node_metrics.values()),
        "post_event_route_changes": sum(m["post_event_route_changes"] for m in node_metrics.values()),
    }
    return {
        "initial_convergence_tick": summary["initial_convergence_tick"],
        "peak_event_rate": summary["peak_event_rate"],
        "hotspot_escape_count": hotspot_escape_count,
        "hotspot_escape_ratio": hotspot_escape_ratio,
        "post_hotspot_route_changes": post_route_changes,
        "post_hotspot_fire_ratio": post_fire_ratio,
        "stabilization_tick": settle_tick,
        "totals": totals,
        "hotspot_metrics": summary["hotspot_metrics"],
        "final_metrics": summary["final_metrics"],
    }


def run_variant(
    *,
    mode: str,
    node_count: int,
    edge_prob: float,
    ticks: int,
    seed: int,
    hotspot_node: int,
    hotspot_start: int,
    hotspot_end: int,
    hotspot_pressure: float,
) -> dict:
    links = generate_connected_er_topology(node_count, edge_prob, seed)
    engine = SimulationEngine(node_count=node_count, links=links, algorithm=SnnSraAlgorithm(build_params(mode)))
    summary = engine.run(
        total_ticks=ticks,
        hotspot=(hotspot_start, hotspot_end, hotspot_node, hotspot_pressure),
        snapshot_ticks=list(range(ticks)),
    ).summary
    return compute_variant_metrics(summary, hotspot_start, hotspot_end)


def main() -> int:
    ap = argparse.ArgumentParser(description="Run formal mechanism studies for memory, inhibition, and dual-timescale SNN-SRA.")
    ap.add_argument("--issue", type=int, default=4)
    ap.add_argument("--nodes", type=int, default=50)
    ap.add_argument("--edge-prob", type=float, default=0.06)
    ap.add_argument("--ticks", type=int, default=40)
    ap.add_argument("--seed", type=int, default=71)
    ap.add_argument("--hotspot-node", type=int, default=1)
    ap.add_argument("--hotspot-start", type=int, default=10)
    ap.add_argument("--hotspot-end", type=int, default=25)
    ap.add_argument("--hotspot-pressure", type=float, default=1.0)
    args = ap.parse_args()

    out_dir = REPO_ROOT / "results" / f"issue-{args.issue}" / "artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)

    variants = {
        mode: run_variant(
            mode=mode,
            node_count=args.nodes,
            edge_prob=args.edge_prob,
            ticks=args.ticks,
            seed=args.seed,
            hotspot_node=args.hotspot_node,
            hotspot_start=args.hotspot_start,
            hotspot_end=args.hotspot_end,
            hotspot_pressure=args.hotspot_pressure,
        )
        for mode in ("baseline", "memory_only", "memory_inhibition", "full")
    }

    payload = {
        "config": vars(args),
        "variants": variants,
        "studies": {
            "memory": {
                "rebound_fire_ratio_delta": variants["memory_only"]["post_hotspot_fire_ratio"] - variants["baseline"]["post_hotspot_fire_ratio"],
                "post_hotspot_route_change_delta": variants["memory_only"]["post_hotspot_route_changes"] - variants["baseline"]["post_hotspot_route_changes"],
                "emitted_update_delta": variants["memory_only"]["totals"]["emitted_updates"] - variants["baseline"]["totals"]["emitted_updates"],
            },
            "inhibition": {
                "escape_ratio_delta": variants["memory_inhibition"]["hotspot_escape_ratio"] - variants["memory_only"]["hotspot_escape_ratio"],
                "fire_rate_gain_delta": variants["memory_inhibition"]["hotspot_metrics"]["fire_rate_gain"] - variants["memory_only"]["hotspot_metrics"]["fire_rate_gain"],
                "post_hotspot_route_change_delta": variants["memory_inhibition"]["post_hotspot_route_changes"] - variants["memory_only"]["post_hotspot_route_changes"],
            },
            "dual_timescale": {
                "stabilization_tick_delta": (variants["full"]["stabilization_tick"] or args.ticks) - (variants["memory_inhibition"]["stabilization_tick"] or args.ticks),
                "post_hotspot_fire_ratio_delta": variants["full"]["post_hotspot_fire_ratio"] - variants["memory_inhibition"]["post_hotspot_fire_ratio"],
                "fire_rate_gain_delta": variants["full"]["hotspot_metrics"]["fire_rate_gain"] - variants["memory_inhibition"]["hotspot_metrics"]["fire_rate_gain"],
            },
        },
    }

    out_path = out_dir / f"snn_mechanism_studies_er_n{args.nodes}_seed{args.seed}.json"
    out_path.write_text(json.dumps(payload, indent=2))
    print(out_path)
    print(json.dumps(payload["studies"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
