#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import asdict
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from compare_queue_closed_loop import (  # type: ignore
    build_flow_templates,
    instantiate_algorithm,
    queue_to_node_pressure,
    scale_flows,
    shortest_hop_map_for_flows,
)
from src.snn_sra_sim import SimulationEngine, evaluate_forwarding_with_queues, generate_connected_topology
from src.snn_sra_sim.types import Link, RouteDelta


def clone_links(links: list[Link]) -> list[Link]:
    return [Link(src=link.src, dst=link.dst, base_cost=link.base_cost, dynamic_cost=link.dynamic_cost, up=link.up) for link in links]


def unique_undirected_links(links: list[Link]) -> list[tuple[int, int]]:
    seen: set[tuple[int, int]] = set()
    out: list[tuple[int, int]] = []
    for link in links:
        pair = (min(link.src, link.dst), max(link.src, link.dst))
        if pair in seen:
            continue
        seen.add(pair)
        out.append(pair)
    out.sort()
    return out


def bytes_from_load_units(load_units: float, mbps_per_unit: float, tick_seconds: float) -> int:
    bits = load_units * mbps_per_unit * tick_seconds * 1_000_000.0
    if bits <= 0.0:
        return 0
    return max(1, int(round(bits / 8.0)))


def schedule_for_scenario(args: argparse.Namespace) -> list[tuple[int, int, float]]:
    if args.scenario == "hotspot":
        return [(args.hotspot_start, args.hotspot_end, args.burst_multiplier)]
    if args.scenario == "repeated":
        return [
            (args.hotspot_start, args.hotspot_end, args.burst_multiplier),
            (args.repeat_start, args.repeat_end, args.burst_multiplier),
        ]
    raise ValueError(args.scenario)


def run_trace_export(
    *,
    method: str,
    topology: str,
    node_count: int,
    edge_prob: float,
    ba_attach: int,
    geo_radius: float,
    ticks: int,
    seed: int,
    hotspot_node: int,
    schedule: list[tuple[int, int, float]],
    num_flows: int,
    base_demand: float,
    hotspot_share: float,
    link_capacity: float,
    link_delay: float,
    queue_capacity: float,
    pressure_gain: float,
    mbps_per_unit: float,
    tick_ms: int,
    control_record_bytes: int,
    links_template: list[Link] | None = None,
    flow_templates=None,
) -> dict:
    tick_seconds = tick_ms / 1000.0
    links = clone_links(links_template) if links_template is not None else generate_connected_topology(
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
        algorithm=instantiate_algorithm(method, phase_seed=seed),
    )
    if flow_templates is None:
        flow_templates = build_flow_templates(node_count, links, hotspot_node, seed, num_flows, base_demand, hotspot_share)
    hotspot_pairs = {
        (flow.src, flow.dst)
        for flow in flow_templates[: int(num_flows * hotspot_share)]
    }
    shortest_hops = shortest_hop_map_for_flows(node_count, links, flow_templates)
    event_queue: dict[int, list[tuple[int, RouteDelta]]] = defaultdict(list)
    queue_state: dict[tuple[int, int], float] = {}
    prev_route_changes = {node_id: 0 for node_id in engine.nodes}
    timeline: list[dict[str, float | int | None]] = []
    data_events: dict[tuple[int, int, int], int] = defaultdict(int)
    control_packets: dict[tuple[int, int, int], int] = defaultdict(int)
    peak_event_rate = 0

    def fanout_and_record(sender_id: int, delta: RouteDelta, *, emit_tick: int, deliver_tick: int) -> None:
        for nbr in engine.nodes[sender_id].neighbors:
            control_packets[(emit_tick, sender_id, nbr)] += 1
            new_delta = RouteDelta(
                src=sender_id,
                dst=delta.dst,
                advertised_cost=delta.advertised_cost,
                next_hop=delta.next_hop,
                version=delta.version,
                ttl=delta.ttl - 1,
                pressure=delta.pressure,
            )
            event_queue[deliver_tick].append((nbr, new_delta))

    for node in engine.nodes.values():
        for delta in engine.algorithm.seed_initial_updates(node):
            fanout_and_record(node.node_id, delta, emit_tick=0, deliver_tick=1)

    for tick in range(ticks):
        pressures = queue_to_node_pressure(engine, queue_state, queue_capacity, pressure_gain)
        for node_id, node in engine.nodes.items():
            node.pressure = pressures[node_id]
            node.damage_signal = 0.0

        if hasattr(engine.algorithm, "set_link_costs"):
            engine.algorithm.set_link_costs(
                {
                    (link.src, link.dst): link.effective_cost + pressures[link.src]
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
                fanout_and_record(recv_node_id, delta, emit_tick=tick, deliver_tick=tick + 1)

        for node in engine.nodes.values():
            tick_updates = engine.algorithm.on_tick(node, tick)
            for delta in tick_updates:
                emitted_this_tick += len(node.neighbors)
                fanout_and_record(node.node_id, delta, emit_tick=tick, deliver_tick=tick + 1)

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
        queue_eval = evaluate_forwarding_with_queues(
            next_hop_table={(int(k.split("->")[0]), int(k.split("->")[1])): v for k, v in forwarding_table.items()},
            links=links,
            flows=flows,
            link_capacity=link_capacity,
            link_delay=link_delay,
            queue_state=queue_state,
            queue_capacity=queue_capacity,
            shortest_hops=shortest_hops,
        )
        queue_state = dict(queue_eval.per_link_queue)
        for (src, dst), load_units in queue_eval.per_link_offered.items():
            row_bytes = bytes_from_load_units(load_units, mbps_per_unit=mbps_per_unit, tick_seconds=tick_seconds)
            if row_bytes > 0:
                data_events[(tick, src, dst)] += row_bytes

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
            }
        )

    control_events = {
        key: packets * control_record_bytes
        for key, packets in control_packets.items()
        if packets > 0
    }
    return {
        "timeline": timeline,
        "peak_event_rate": peak_event_rate,
        "data_events": [
            {"tick": tick, "kind": "data", "src": src, "dst": dst, "bytes": row_bytes}
            for (tick, src, dst), row_bytes in sorted(data_events.items())
        ],
        "control_events": [
            {"tick": tick, "kind": "control", "src": src, "dst": dst, "bytes": row_bytes}
            for (tick, src, dst), row_bytes in sorted(control_events.items())
        ],
        "totals": {
            "data_bytes": sum(data_events.values()),
            "control_bytes": sum(control_events.values()),
            "route_changes": sum(node.metrics.route_changes for node in engine.nodes.values()),
            "emitted_updates": sum(node.metrics.emitted_updates for node in engine.nodes.values()),
        },
        "final_metrics": timeline[-1] if timeline else {},
    }


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write(",".join(fieldnames) + "\n")
        for row in rows:
            handle.write(",".join(str(row.get(name, "")) for name in fieldnames) + "\n")


def format_tag(value: float | int) -> str:
    if isinstance(value, int):
        return str(value)
    text = f"{value:g}"
    return text.replace(".", "p")


def main() -> int:
    ap = argparse.ArgumentParser(description="Export link-level trace files for ns-3 replay validation.")
    ap.add_argument("--issue", type=int, default=4)
    ap.add_argument("--scenario", choices=("hotspot", "repeated"), default="hotspot")
    ap.add_argument("--methods", default="snn_sra,triggered_te,ospf_te")
    ap.add_argument("--topology", choices=("er", "ba", "rgg"), default="er")
    ap.add_argument("--nodes", type=int, default=24)
    ap.add_argument("--edge-prob", type=float, default=0.18)
    ap.add_argument("--ba-attach", type=int, default=2)
    ap.add_argument("--geo-radius", type=float, default=0.33)
    ap.add_argument("--ticks", type=int, default=60)
    ap.add_argument("--seed", type=int, default=51)
    ap.add_argument("--hotspot-node", type=int, default=1)
    ap.add_argument("--hotspot-start", type=int, default=15)
    ap.add_argument("--hotspot-end", type=int, default=28)
    ap.add_argument("--repeat-start", type=int, default=36)
    ap.add_argument("--repeat-end", type=int, default=49)
    ap.add_argument("--num-flows", type=int, default=80)
    ap.add_argument("--base-demand", type=float, default=0.25)
    ap.add_argument("--hotspot-share", type=float, default=0.6)
    ap.add_argument("--burst-multiplier", type=float, default=3.0)
    ap.add_argument("--link-capacity", type=float, default=10.0)
    ap.add_argument("--link-delay", type=float, default=1.0)
    ap.add_argument("--queue-capacity", type=float, default=30.0)
    ap.add_argument("--pressure-gain", type=float, default=1.8)
    ap.add_argument("--tick-ms", type=int, default=100)
    ap.add_argument("--mbps-per-unit", type=float, default=1.0)
    ap.add_argument("--control-record-bytes", type=int, default=32)
    args = ap.parse_args()

    methods = [part.strip() for part in args.methods.split(",") if part.strip()]
    schedule = schedule_for_scenario(args)
    out_dir = (
        REPO_ROOT
        / "results"
        / f"issue-{args.issue}"
        / "artifacts"
        / (
            f"ns3_replay_{args.scenario}_{args.topology}_n{args.nodes}_seed{args.seed}"
            f"_u{format_tag(args.mbps_per_unit)}_tick{args.tick_ms}"
        )
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    base_links = generate_connected_topology(
        kind=args.topology,
        node_count=args.nodes,
        seed=args.seed,
        edge_prob=args.edge_prob,
        attach_edges=args.ba_attach,
        geo_radius=args.geo_radius,
    )
    flow_templates = build_flow_templates(
        args.nodes,
        base_links,
        args.hotspot_node,
        args.seed,
        args.num_flows,
        args.base_demand,
        args.hotspot_share,
    )

    topology_csv = out_dir / "topology.csv"
    write_csv(
        topology_csv,
        [{"src": src, "dst": dst} for src, dst in unique_undirected_links(base_links)],
        ["src", "dst"],
    )

    methods_manifest: dict[str, dict[str, object]] = {}
    for method in methods:
        exported = run_trace_export(
            method=method,
            topology=args.topology,
            node_count=args.nodes,
            edge_prob=args.edge_prob,
            ba_attach=args.ba_attach,
            geo_radius=args.geo_radius,
            ticks=args.ticks,
            seed=args.seed,
            hotspot_node=args.hotspot_node,
            schedule=schedule,
            num_flows=args.num_flows,
            base_demand=args.base_demand,
            hotspot_share=args.hotspot_share,
            link_capacity=args.link_capacity,
            link_delay=args.link_delay,
            queue_capacity=args.queue_capacity,
            pressure_gain=args.pressure_gain,
            mbps_per_unit=args.mbps_per_unit,
            tick_ms=args.tick_ms,
            control_record_bytes=args.control_record_bytes,
            links_template=base_links,
            flow_templates=flow_templates,
        )
        events_csv = out_dir / f"{method}_events.csv"
        write_csv(
            events_csv,
            exported["data_events"] + exported["control_events"],
            ["tick", "kind", "src", "dst", "bytes"],
        )
        sim_summary_json = out_dir / f"{method}_sim_summary.json"
        sim_summary_json.write_text(
            json.dumps(
                {
                    "method": method,
                    "scenario": args.scenario,
                    "config": vars(args),
                    "schedule": [{"start": s, "end": e, "multiplier": m} for s, e, m in schedule],
                    "totals": exported["totals"],
                    "final_metrics": exported["final_metrics"],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        methods_manifest[method] = {
            "events_csv": str(events_csv.relative_to(REPO_ROOT)),
            "sim_summary_json": str(sim_summary_json.relative_to(REPO_ROOT)),
            "totals": exported["totals"],
        }

    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "scenario": args.scenario,
                "config": vars(args),
                "schedule": [{"start": s, "end": e, "multiplier": m} for s, e, m in schedule],
                "topology_csv": str(topology_csv.relative_to(REPO_ROOT)),
                "methods": methods_manifest,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
