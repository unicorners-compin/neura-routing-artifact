#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import defaultdict
import heapq
import json
from pathlib import Path
import random
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.snn_sra_sim import SimulationEngine, TrafficFlow, evaluate_forwarding_with_queues, generate_connected_topology
from src.snn_sra_sim.algorithms import (
    BanditParams,
    BanditRoutingAlgorithm,
    OspfTeParams,
    OspfTeStyleAlgorithm,
    SnnSraAlgorithm,
    SnnSraParams,
    TeEcmpParams,
    TeEcmpStyleAlgorithm,
    TriggeredTeParams,
    TriggeredTeStyleAlgorithm,
)
from src.snn_sra_sim.types import RouteDelta


def shortest_path_nodes(node_count: int, links, src: int, dst: int) -> list[int]:
    adj: dict[int, list[int]] = defaultdict(list)
    for link in links:
        if link.up:
            adj[link.src].append(link.dst)
    dist = {src: 0.0}
    prev: dict[int, int] = {}
    heap: list[tuple[float, int]] = [(0.0, src)]
    while heap:
        d, u = heapq.heappop(heap)
        if u == dst:
            break
        if d > dist.get(u, float("inf")):
            continue
        for v in adj.get(u, []):
            nd = d + 1.0
            if nd < dist.get(v, float("inf")):
                dist[v] = nd
                prev[v] = u
                heapq.heappush(heap, (nd, v))
    if dst not in dist:
        return []
    path = [dst]
    cur = dst
    while cur != src:
        cur = prev[cur]
        path.append(cur)
    path.reverse()
    return path


def shortest_hop_map_for_flows(node_count: int, links, flows: list[TrafficFlow]) -> dict[tuple[int, int], int]:
    hop_map: dict[tuple[int, int], int] = {}
    for flow in flows:
        key = (flow.src, flow.dst)
        if key in hop_map:
            continue
        path = shortest_path_nodes(node_count, links, flow.src, flow.dst)
        if path:
            hop_map[key] = max(1, len(path) - 1)
    return hop_map


def build_flow_templates(node_count: int, links, hotspot_node: int, seed: int, num_flows: int, base_demand: float, hotspot_share: float) -> list[TrafficFlow]:
    rng = random.Random(seed)
    flows: list[TrafficFlow] = []
    hotspot_flows = int(num_flows * hotspot_share)
    transit_candidates: list[tuple[int, int]] = []
    fallback_candidates: list[tuple[int, int]] = []
    for src in range(1, node_count + 1):
        for dst in range(1, node_count + 1):
            if src == dst or src == hotspot_node or dst == hotspot_node:
                continue
            path = shortest_path_nodes(node_count, links, src, dst)
            if not path:
                continue
            if hotspot_node in path[1:-1]:
                transit_candidates.append((src, dst))
            else:
                fallback_candidates.append((src, dst))
    rng.shuffle(transit_candidates)
    rng.shuffle(fallback_candidates)
    selected_hot = transit_candidates[:hotspot_flows]
    if len(selected_hot) < hotspot_flows:
        selected_hot.extend(fallback_candidates[: hotspot_flows - len(selected_hot)])
    selected_cold = fallback_candidates[: max(0, num_flows - len(selected_hot))]
    while len(selected_cold) < num_flows - len(selected_hot):
        src = rng.randint(1, node_count)
        dst = rng.randint(1, node_count)
        if src != dst and src != hotspot_node and dst != hotspot_node:
            selected_cold.append((src, dst))
    for src, dst in selected_hot + selected_cold:
        flows.append(TrafficFlow(src=src, dst=dst, demand=base_demand))
    return flows


def instantiate_algorithm(method: str, snn_params: SnnSraParams | None = None, phase_seed: int = 0):
    if method == "snn_sra":
        return SnnSraAlgorithm(snn_params or SnnSraParams())
    if method == "ospf_te":
        return OspfTeStyleAlgorithm(OspfTeParams(periodic_phase_seed=phase_seed))
    if method == "triggered_te":
        return TriggeredTeStyleAlgorithm(TriggeredTeParams(refresh_phase_seed=phase_seed))
    if method == "te_ecmp":
        return TeEcmpStyleAlgorithm(TeEcmpParams(periodic_phase_seed=phase_seed))
    if method == "bandit":
        return BanditRoutingAlgorithm(BanditParams(periodic_phase_seed=phase_seed, seed=phase_seed or 12345))
    raise ValueError(method)


def scale_flows(
    flows: list[TrafficFlow],
    hotspot_pairs: set[tuple[int, int]],
    burst_multiplier: float,
    burst_active: bool,
) -> list[TrafficFlow]:
    if not burst_active or burst_multiplier <= 1.0:
        return list(flows)
    scaled: list[TrafficFlow] = []
    for flow in flows:
        demand = flow.demand * (burst_multiplier if (flow.src, flow.dst) in hotspot_pairs else 1.0)
        scaled.append(TrafficFlow(src=flow.src, dst=flow.dst, demand=demand))
    return scaled


def queue_to_node_pressure(engine: SimulationEngine, queue_state: dict[tuple[int, int], float], queue_capacity: float, pressure_gain: float) -> dict[int, float]:
    pressures: dict[int, float] = {}
    for node_id in engine.nodes:
        local_max = 0.0
        for nbr in engine.nodes[node_id].neighbors:
            local_max = max(local_max, queue_state.get((node_id, nbr), 0.0))
            local_max = max(local_max, queue_state.get((nbr, node_id), 0.0))
        norm = 0.0 if queue_capacity <= 0 else min(local_max / queue_capacity, 1.0)
        pressures[node_id] = pressure_gain * norm
    return pressures


def run_closed_loop(
    method: str,
    topology: str,
    node_count: int,
    edge_prob: float,
    ba_attach: int,
    ticks: int,
    seed: int,
    hotspot_node: int,
    hotspot_start: int,
    hotspot_end: int,
    num_flows: int,
    base_demand: float,
    hotspot_share: float,
    burst_multiplier: float,
    link_capacity: float,
    link_delay: float,
    queue_capacity: float,
    pressure_gain: float,
    geo_radius: float = 0.17,
    snn_params: SnnSraParams | None = None,
    fail_edge: tuple[int, int] | None = None,
    fail_tick: int | None = None,
    damage_hold_ticks: int = 6,
    burst_windows: list[tuple[int, int, float]] | None = None,
    degrade_node: int | None = None,
    degrade_start: int | None = None,
    degrade_end: int | None = None,
    degrade_factor: float = 1.0,
) -> dict:
    links = generate_connected_topology(
        kind=topology,
        node_count=node_count,
        seed=seed,
        edge_prob=edge_prob,
        attach_edges=ba_attach,
        geo_radius=geo_radius,
    )
    engine = SimulationEngine(
        node_count=node_count,
        links=links,
        algorithm=instantiate_algorithm(method, snn_params=snn_params, phase_seed=seed),
    )
    flow_templates = build_flow_templates(node_count, links, hotspot_node, seed, num_flows, base_demand, hotspot_share)
    hotspot_pairs = {
        (flow.src, flow.dst)
        for flow in flow_templates[: int(num_flows * hotspot_share)]
    }
    shortest_hops = shortest_hop_map_for_flows(node_count, links, flow_templates)
    event_queue: dict[int, list[tuple[int, RouteDelta]]] = defaultdict(list)
    queue_state: dict[tuple[int, int], float] = {}
    timeline: list[dict[str, float | int | None]] = []
    peak_event_rate = 0
    prev_route_changes = {node_id: 0 for node_id in engine.nodes}

    for node in engine.nodes.values():
        for delta in engine.algorithm.seed_initial_updates(node):
            engine._fanout(node.node_id, delta, event_queue, deliver_tick=1)

    schedule = burst_windows if burst_windows is not None else [(hotspot_start, hotspot_end, burst_multiplier)]
    burst_rows: list[dict[str, float | int | None]] = []
    last_burst_end = max(end for _, end, _ in schedule) if schedule else -1
    first_burst_start = min(start for start, _, _ in schedule) if schedule else ticks

    for tick in range(ticks):
        if fail_edge is not None and fail_tick is not None and tick == fail_tick:
            for link in links:
                if (link.src, link.dst) == fail_edge or (link.src, link.dst) == (fail_edge[1], fail_edge[0]):
                    link.up = False
            shortest_hops = shortest_hop_map_for_flows(node_count, links, flow_templates)
        pressures = queue_to_node_pressure(engine, queue_state, queue_capacity, pressure_gain)
        for node_id, node in engine.nodes.items():
            node.pressure = pressures[node_id]
            node.damage_signal = 0.0
        if fail_edge is not None and fail_tick is not None and fail_tick <= tick < fail_tick + damage_hold_ticks:
            if fail_edge[0] in engine.nodes:
                engine.nodes[fail_edge[0]].damage_signal = 1.0
            if fail_edge[1] in engine.nodes:
                engine.nodes[fail_edge[1]].damage_signal = 1.0

        if hasattr(engine.algorithm, "set_link_costs"):
            engine.algorithm.set_link_costs(
                {
                    (link.src, link.dst): link.effective_cost + pressures[link.src] + engine.nodes[link.src].damage_signal
                    for link in engine.links
                    if link.up
                }
            )

        pending = event_queue.pop(tick, [])
        peak_event_rate = max(peak_event_rate, len(pending))
        emitted_this_tick = 0
        for recv_node_id, update in pending:
            if update.ttl <= 0:
                continue
            incoming = update.src
            reverse = engine.link_map.get((incoming, recv_node_id))
            if reverse is None or not reverse.up:
                continue
            recv_node = engine.nodes[recv_node_id]
            new_updates = engine.algorithm.on_update(
                recv_node,
                tick,
                incoming_neighbor=incoming,
                update=update,
                neighbor_pressure=engine.nodes[incoming].pressure,
                edge_cost=reverse.effective_cost,
            )
            for delta in new_updates:
                emitted_this_tick += len(recv_node.neighbors)
                engine._fanout(recv_node_id, delta, event_queue, deliver_tick=tick + 1)

        for node in engine.nodes.values():
            tick_updates = engine.algorithm.on_tick(node, tick)
            for delta in tick_updates:
                emitted_this_tick += len(node.neighbors)
                engine._fanout(node.node_id, delta, event_queue, deliver_tick=tick + 1)

        route_changes_this_tick = 0
        for node_id, node in engine.nodes.items():
            delta = node.metrics.route_changes - prev_route_changes[node_id]
            if delta > 0:
                route_changes_this_tick += delta
            prev_route_changes[node_id] = node.metrics.route_changes

        active_multiplier = 1.0
        for start, end, multiplier in schedule:
            if start <= tick <= end:
                active_multiplier = max(active_multiplier, multiplier)
        burst_active = active_multiplier > 1.0
        flows = scale_flows(flow_templates, hotspot_pairs, active_multiplier, burst_active)
        forwarding_table = engine._forwarding_table()
        gray_failure_active = (
            degrade_node is not None
            and degrade_start is not None
            and degrade_end is not None
            and degrade_start <= tick <= degrade_end
            and degrade_factor < 1.0
        )
        per_link_capacity = None
        if gray_failure_active:
            per_link_capacity = {}
            for link in links:
                if not link.up:
                    continue
                cap = link_capacity
                if link.src == degrade_node:
                    cap *= degrade_factor
                per_link_capacity[(link.src, link.dst)] = cap

        queue_eval = evaluate_forwarding_with_queues(
            next_hop_table={(int(k.split("->")[0]), int(k.split("->")[1])): v for k, v in forwarding_table.items()},
            links=links,
            flows=flows,
            link_capacity=link_capacity,
            link_delay=link_delay,
            queue_state=queue_state,
            queue_capacity=queue_capacity,
            shortest_hops=shortest_hops,
            per_link_capacity=per_link_capacity,
        )
        queue_state = dict(queue_eval.per_link_queue)
        reach = engine._measure_reachability()
        timeline.append(
            {
                "tick": tick,
                "burst_active": int(burst_active),
                "burst_multiplier": active_multiplier,
                "mean_pressure": sum(pressures.values()) / len(pressures) if pressures else 0.0,
                "hotspot_pressure": pressures.get(hotspot_node, 0.0),
                "delivery_ratio": 0.0 if queue_eval.offered_load == 0 else queue_eval.delivered_load / queue_eval.offered_load,
                "loss_ratio": 0.0 if queue_eval.offered_load == 0 else queue_eval.dropped_load / queue_eval.offered_load,
                "mean_delay": queue_eval.mean_delay,
                "mean_path_stretch": queue_eval.mean_path_stretch,
                "max_link_utilization": queue_eval.max_link_utilization,
                "pair_reachability_ratio": reach["pair_reachability_ratio"],
                "loop_ratio": reach["loop_ratio"],
                "blackhole_ratio": reach["blackhole_ratio"],
                "node_complete_route_ratio": reach["node_complete_route_ratio"],
                "emitted_this_tick": emitted_this_tick,
                "route_changes_this_tick": route_changes_this_tick,
                "failed_edge_active": int(fail_edge is not None and fail_tick is not None and tick >= fail_tick),
                "gray_failure_active": int(gray_failure_active),
                "degraded_node_pressure": pressures.get(degrade_node, 0.0) if degrade_node is not None else None,
            }
        )
        if burst_active:
            burst_rows.append(timeline[-1])

    node_metrics = {
        node_id: {
            "emitted_updates": node.metrics.emitted_updates,
            "received_updates": node.metrics.received_updates,
            "fire_count": node.metrics.fire_count,
            "route_changes": node.metrics.route_changes,
            "post_event_route_changes": node.metrics.post_event_route_changes,
        }
        for node_id, node in engine.nodes.items()
    }
    post_rows = [row for row in timeline if int(row["tick"]) > last_burst_end]
    startup_reachability_tick = next(
        (
            int(row["tick"])
            for row in timeline
            if float(row["pair_reachability_ratio"]) >= 0.999
        ),
        None,
    )
    pre_burst_rows = [row for row in timeline if int(row["tick"]) < first_burst_start]
    return {
        "peak_event_rate": peak_event_rate,
        "final_metrics": timeline[-1],
        "burst_schedule": [
            {"start": start, "end": end, "multiplier": multiplier}
            for start, end, multiplier in schedule
        ],
        "under_burst_summary": {
            "mean_delivery_ratio": sum(float(r["delivery_ratio"]) for r in burst_rows) / len(burst_rows) if burst_rows else None,
            "min_delivery_ratio": min(float(r["delivery_ratio"]) for r in burst_rows) if burst_rows else None,
            "mean_loss_ratio": sum(float(r["loss_ratio"]) for r in burst_rows) / len(burst_rows) if burst_rows else None,
            "mean_delay": sum(float(r["mean_delay"]) for r in burst_rows if r["mean_delay"] is not None) / len(burst_rows) if burst_rows else None,
            "mean_path_stretch": sum(float(r["mean_path_stretch"]) for r in burst_rows if r["mean_path_stretch"] is not None) / len(burst_rows) if burst_rows else None,
            "max_link_utilization": max(float(r["max_link_utilization"]) for r in burst_rows) if burst_rows else None,
            "mean_emitted_this_tick": sum(float(r["emitted_this_tick"]) for r in burst_rows) / len(burst_rows) if burst_rows else None,
            "mean_route_changes_this_tick": sum(float(r["route_changes_this_tick"]) for r in burst_rows) / len(burst_rows) if burst_rows else None,
        },
        "post_burst_summary": {
            "mean_delivery_ratio": sum(float(r["delivery_ratio"]) for r in post_rows) / len(post_rows) if post_rows else None,
            "mean_loss_ratio": sum(float(r["loss_ratio"]) for r in post_rows) / len(post_rows) if post_rows else None,
            "mean_delay": sum(float(r["mean_delay"]) for r in post_rows if r["mean_delay"] is not None) / len(post_rows) if post_rows else None,
            "mean_path_stretch": sum(float(r["mean_path_stretch"]) for r in post_rows if r["mean_path_stretch"] is not None) / len(post_rows) if post_rows else None,
            "mean_pressure": sum(float(r["mean_pressure"]) for r in post_rows) / len(post_rows) if post_rows else None,
            "mean_route_changes_this_tick": sum(float(r["route_changes_this_tick"]) for r in post_rows) / len(post_rows) if post_rows else None,
        },
        "startup_summary": {
            "full_reachability_tick": startup_reachability_tick,
            "control_bytes_before_first_burst": sum(float(r["emitted_this_tick"]) * 32.0 for r in pre_burst_rows),
            "control_bytes_before_full_reachability": sum(
                float(r["emitted_this_tick"]) * 32.0
                for r in timeline
                if startup_reachability_tick is not None and int(r["tick"]) <= startup_reachability_tick
            ),
        },
        "totals": {
            "emitted_updates": sum(m["emitted_updates"] for m in node_metrics.values()),
            "received_updates": sum(m["received_updates"] for m in node_metrics.values()),
            "fire_count": sum(m["fire_count"] for m in node_metrics.values()),
            "route_changes": sum(m["route_changes"] for m in node_metrics.values()),
            "post_event_route_changes": sum(m["post_event_route_changes"] for m in node_metrics.values()),
        },
        "timeline": timeline,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Closed-loop queue-driven comparison for LIFT and routing baselines.")
    ap.add_argument("--issue", type=int, default=4)
    ap.add_argument("--topology", choices=("er", "ba", "rgg"), default="er")
    ap.add_argument("--nodes", type=int, default=100)
    ap.add_argument("--edge-prob", type=float, default=0.06)
    ap.add_argument("--ba-attach", type=int, default=2)
    ap.add_argument("--geo-radius", type=float, default=0.17)
    ap.add_argument("--ticks", type=int, default=80)
    ap.add_argument("--seed", type=int, default=51)
    ap.add_argument("--hotspot-node", type=int, default=1)
    ap.add_argument("--hotspot-start", type=int, default=25)
    ap.add_argument("--hotspot-end", type=int, default=55)
    ap.add_argument("--num-flows", type=int, default=400)
    ap.add_argument("--base-demand", type=float, default=0.2)
    ap.add_argument("--hotspot-share", type=float, default=0.6)
    ap.add_argument("--burst-multiplier", type=float, default=3.0)
    ap.add_argument("--link-capacity", type=float, default=10.0)
    ap.add_argument("--link-delay", type=float, default=1.0)
    ap.add_argument("--queue-capacity", type=float, default=30.0)
    ap.add_argument("--pressure-gain", type=float, default=1.8)
    ap.add_argument("--fail-edge", type=str, default="")
    ap.add_argument("--fail-tick", type=int, default=-1)
    ap.add_argument("--damage-hold-ticks", type=int, default=6)
    ap.add_argument("--degrade-node", type=int, default=-1)
    ap.add_argument("--degrade-start", type=int, default=-1)
    ap.add_argument("--degrade-end", type=int, default=-1)
    ap.add_argument("--degrade-factor", type=float, default=1.0)
    args = ap.parse_args()

    fail_edge = None
    if args.fail_edge:
        a, b = args.fail_edge.split("-")
        fail_edge = (int(a), int(b))

    out_dir = REPO_ROOT / "results" / f"issue-{args.issue}" / "artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "config": vars(args),
        "methods": {
            method: run_closed_loop(
                method=method,
                topology=args.topology,
                node_count=args.nodes,
                edge_prob=args.edge_prob,
                ba_attach=args.ba_attach,
                geo_radius=args.geo_radius,
                ticks=args.ticks,
                seed=args.seed,
                hotspot_node=args.hotspot_node,
                hotspot_start=args.hotspot_start,
                hotspot_end=args.hotspot_end,
                num_flows=args.num_flows,
                base_demand=args.base_demand,
                hotspot_share=args.hotspot_share,
                burst_multiplier=args.burst_multiplier,
                link_capacity=args.link_capacity,
                link_delay=args.link_delay,
                queue_capacity=args.queue_capacity,
                pressure_gain=args.pressure_gain,
                fail_edge=fail_edge,
                fail_tick=None if args.fail_tick < 0 else args.fail_tick,
                damage_hold_ticks=args.damage_hold_ticks,
                degrade_node=None if args.degrade_node < 0 else args.degrade_node,
                degrade_start=None if args.degrade_start < 0 else args.degrade_start,
                degrade_end=None if args.degrade_end < 0 else args.degrade_end,
                degrade_factor=args.degrade_factor,
            )
            for method in ("snn_sra", "ospf_te", "triggered_te", "te_ecmp", "bandit")
        },
    }
    suffix = ""
    if fail_edge is not None and args.fail_tick >= 0:
        suffix = f"_fail_{fail_edge[0]}_{fail_edge[1]}_tick{args.fail_tick}"
    stem = (
        f"queue_closed_loop_compare_er_n{args.nodes}_seed{args.seed}"
        if args.topology == "er"
        else f"queue_closed_loop_compare_{args.topology}_n{args.nodes}_seed{args.seed}"
    )
    out_path = out_dir / f"{stem}{suffix}.json"
    out_path.write_text(json.dumps(payload, indent=2))
    print(out_path)
    print(json.dumps({k: {"under_burst_summary": v["under_burst_summary"], "final_metrics": v["final_metrics"], "totals": v["totals"]} for k, v in payload["methods"].items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
