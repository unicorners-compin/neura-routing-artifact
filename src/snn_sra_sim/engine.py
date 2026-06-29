from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import math
from typing import Dict, List, Mapping, Sequence, Tuple

from .algorithms import SnnSraAlgorithm
from .topology import links_to_neighbor_map
from .types import Link, NodeState, RouteDelta


@dataclass
class SimulationResult:
    summary: Dict[str, object]


class SimulationEngine:
    def __init__(self, node_count: int, links: List[Link], algorithm: SnnSraAlgorithm) -> None:
        self.node_count = node_count
        self.links = links
        self.algorithm = algorithm
        self.neighbor_map = links_to_neighbor_map(links)
        self.link_map: Dict[Tuple[int, int], Link] = {(link.src, link.dst): link for link in links}
        self.nodes: Dict[int, NodeState] = {
            node_id: NodeState(node_id=node_id, neighbors=self.neighbor_map.get(node_id, []))
            for node_id in range(1, node_count + 1)
        }
        for node in self.nodes.values():
            self.algorithm.initialize_node(node, list(self.nodes))

    def run(
        self,
        total_ticks: int,
        hotspot: Tuple[int, int, float, float] | None = None,
        snapshot_ticks: Sequence[int] | None = None,
        pressure_schedule: Mapping[int, Mapping[int, float]] | None = None,
    ) -> SimulationResult:
        event_queue: Dict[int, List[Tuple[int, RouteDelta]]] = defaultdict(list)
        snapshot_tick_set = set(snapshot_ticks or [])
        forwarding_snapshots: Dict[str, Dict[str, int | None]] = {}
        fire_tick_log: Dict[int, List[int]] = {node_id: [] for node_id in self.nodes}
        emit_tick_log: Dict[int, List[int]] = {node_id: [] for node_id in self.nodes}
        for node in self.nodes.values():
            for delta in self.algorithm.seed_initial_updates(node):
                self._fanout(node.node_id, delta, event_queue, deliver_tick=1)

        reachability_series: List[Dict[str, float]] = []
        peak_event_rate = 0
        convergence_tick = None
        hotspot_observation: List[Dict[str, float]] = []
        prev_fire_counts = {node_id: 0 for node_id in self.nodes}
        prev_emitted_updates = {node_id: 0 for node_id in self.nodes}

        for tick in range(total_ticks):
            for node in self.nodes.values():
                node.pressure = 0.0
            if pressure_schedule is not None and tick in pressure_schedule:
                for node_id, pressure_value in pressure_schedule[tick].items():
                    if int(node_id) in self.nodes:
                        self.nodes[int(node_id)].pressure = float(pressure_value)
            elif hotspot is not None:
                start_tick, end_tick, target_node, pressure_value = hotspot
                if start_tick <= tick <= end_tick:
                    self.nodes[int(target_node)].pressure = pressure_value

            if hasattr(self.algorithm, "set_link_costs"):
                self.algorithm.set_link_costs(
                    {
                        (link.src, link.dst): link.effective_cost + self.nodes[link.src].pressure
                        for link in self.links
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
                link = self.link_map.get((recv_node_id, incoming))
                reverse = self.link_map.get((incoming, recv_node_id))
                if reverse is None or not reverse.up:
                    continue
                recv_node = self.nodes[recv_node_id]
                neighbor_pressure = self.nodes[incoming].pressure
                new_updates = self.algorithm.on_update(
                    recv_node,
                    tick,
                    incoming_neighbor=incoming,
                    update=update,
                    neighbor_pressure=neighbor_pressure,
                    edge_cost=reverse.effective_cost,
                )
                for delta in new_updates:
                    emitted_this_tick += len(recv_node.neighbors)
                    self._fanout(recv_node_id, delta, event_queue, deliver_tick=tick + 1)

            for node in self.nodes.values():
                tick_updates = self.algorithm.on_tick(node, tick)
                for delta in tick_updates:
                    emitted_this_tick += len(node.neighbors)
                    self._fanout(node.node_id, delta, event_queue, deliver_tick=tick + 1)

            metrics = self._measure_reachability()
            metrics["tick"] = tick
            metrics["emitted_this_tick"] = emitted_this_tick
            reachability_series.append(metrics)
            if tick in snapshot_tick_set:
                forwarding_snapshots[str(tick)] = self._forwarding_table()
            fire_count_deltas = {
                node_id: node.metrics.fire_count - prev_fire_counts[node_id]
                for node_id, node in self.nodes.items()
            }
            if hotspot is not None:
                _, _, target_node, _ = hotspot
                hotspot_node = self.nodes[int(target_node)]
                hotspot_observation.append(
                    {
                        "tick": tick,
                        "pressure": hotspot_node.pressure,
                        "fire_delta": fire_count_deltas[int(target_node)],
                    }
                )
            for node_id, node in self.nodes.items():
                fire_delta = fire_count_deltas[node_id]
                if fire_delta > 0:
                    fire_tick_log[node_id].extend([tick] * fire_delta)
                emitted_delta = node.metrics.emitted_updates - prev_emitted_updates[node_id]
                if emitted_delta > 0:
                    emit_tick_log[node_id].append(tick)
                prev_fire_counts[node_id] = node.metrics.fire_count
                prev_emitted_updates[node_id] = node.metrics.emitted_updates
            if convergence_tick is None and metrics["pair_reachability_ratio"] >= 0.999:
                convergence_tick = tick

        summary = {
            "node_count": self.node_count,
            "total_ticks": total_ticks,
            "initial_convergence_tick": convergence_tick,
            "final_metrics": reachability_series[-1],
            "peak_event_rate": peak_event_rate,
            "hotspot_metrics": self._build_hotspot_metrics(hotspot, hotspot_observation),
            "final_forwarding_table": self._forwarding_table(),
            "forwarding_snapshots": forwarding_snapshots,
            "fire_tick_log": {str(node_id): ticks for node_id, ticks in fire_tick_log.items()},
            "emit_tick_log": {str(node_id): ticks for node_id, ticks in emit_tick_log.items()},
            "node_metrics": {
                node_id: {
                    "emitted_updates": node.metrics.emitted_updates,
                    "received_updates": node.metrics.received_updates,
                    "suppressed_small_delta": node.metrics.suppressed_small_delta,
                    "suppressed_refractory": node.metrics.suppressed_refractory,
                    "suppressed_emit_interval": node.metrics.suppressed_emit_interval,
                    "route_changes": node.metrics.route_changes,
                    "post_event_route_changes": node.metrics.post_event_route_changes,
                    "fire_count": node.metrics.fire_count,
                }
                for node_id, node in self.nodes.items()
            },
            "reachability_series": reachability_series,
        }
        return SimulationResult(summary=summary)

    def _forwarding_table(self) -> Dict[str, int | list[int] | None]:
        table: Dict[str, int | list[int] | None] = {}
        for node_id, node in self.nodes.items():
            for dst, route in node.route_table.items():
                if route.selected_next_hops:
                    table[f"{node_id}->{dst}"] = list(route.selected_next_hops)
                else:
                    table[f"{node_id}->{dst}"] = route.selected_next_hop
        return table

    def _build_hotspot_metrics(
        self,
        hotspot: Tuple[int, int, float, float] | None,
        hotspot_observation: List[Dict[str, float]],
    ) -> Dict[str, object]:
        if hotspot is None:
            return {}
        start_tick, end_tick, target_node, pressure_value = hotspot
        first_fire_tick = None
        total_fire_under_pressure = 0
        total_fire_before_pressure = 0
        xs: List[float] = []
        ys: List[float] = []
        for row in hotspot_observation:
            if row["tick"] < start_tick:
                total_fire_before_pressure += int(row["fire_delta"])
            if start_tick <= row["tick"] <= end_tick:
                xs.append(float(row["pressure"]))
                ys.append(float(row["fire_delta"]))
                total_fire_under_pressure += int(row["fire_delta"])
                if first_fire_tick is None and row["fire_delta"] > 0:
                    first_fire_tick = int(row["tick"])
        before_len = max(int(start_tick), 1)
        under_len = max(int(end_tick - start_tick + 1), 1)
        pre_fire_rate = total_fire_before_pressure / before_len
        under_fire_rate = total_fire_under_pressure / under_len
        return {
            "hotspot_node": int(target_node),
            "hotspot_start_tick": int(start_tick),
            "hotspot_end_tick": int(end_tick),
            "hotspot_pressure_value": float(pressure_value),
            "time_to_first_fire_under_pressure": None if first_fire_tick is None else int(first_fire_tick - start_tick),
            "pre_hotspot_fire_rate": pre_fire_rate,
            "under_hotspot_fire_rate": under_fire_rate,
            "fire_rate_gain": None if pre_fire_rate == 0 else under_fire_rate / pre_fire_rate,
            "total_fire_under_pressure": total_fire_under_pressure,
            "pressure_fire_correlation": self._pearson_corr(xs, ys),
            "hotspot_observation": hotspot_observation,
        }

    @staticmethod
    def _pearson_corr(xs: List[float], ys: List[float]) -> float | None:
        if len(xs) != len(ys) or len(xs) < 2:
            return None
        mean_x = sum(xs) / len(xs)
        mean_y = sum(ys) / len(ys)
        den_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
        den_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
        if den_x == 0.0 or den_y == 0.0:
            return None
        num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
        return num / (den_x * den_y)

    def _fanout(self, sender_id: int, delta: RouteDelta, event_queue: Dict[int, List[Tuple[int, RouteDelta]]], deliver_tick: int) -> None:
        for nbr in self.nodes[sender_id].neighbors:
            if nbr == delta.next_hop:
                pass
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

    def _measure_reachability(self) -> Dict[str, float]:
        pairs = 0
        reachable = 0
        loops = 0
        blackholes = 0
        complete_nodes = 0
        for src in self.nodes:
            complete = True
            for dst in self.nodes:
                if src == dst:
                    continue
                pairs += 1
                ok, kind = self._trace(src, dst)
                if ok:
                    reachable += 1
                elif kind == "loop":
                    loops += 1
                else:
                    blackholes += 1
                if not ok:
                    complete = False
            if complete:
                complete_nodes += 1
        return {
            "pair_reachability_ratio": reachable / pairs if pairs else 0.0,
            "loop_ratio": loops / pairs if pairs else 0.0,
            "blackhole_ratio": blackholes / pairs if pairs else 0.0,
            "node_complete_route_ratio": complete_nodes / self.node_count if self.node_count else 0.0,
        }

    def _trace(self, src: int, dst: int) -> Tuple[bool, str]:
        cur = src
        seen = set()
        max_hops = self.node_count + 1
        hops = 0
        while hops < max_hops:
            if cur == dst:
                return True, "ok"
            if cur in seen:
                return False, "loop"
            seen.add(cur)
            route = self.nodes[cur].route_table.get(dst)
            if route is None or route.selected_next_hop is None:
                return False, "blackhole"
            cur = route.selected_next_hop
            hops += 1
        return False, "loop"
