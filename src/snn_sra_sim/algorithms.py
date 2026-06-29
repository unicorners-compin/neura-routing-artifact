from __future__ import annotations

from dataclasses import dataclass
import heapq
import random
from typing import Dict, List

from .types import CandidateState, NodeState, RouteDelta, RouteState


def _periodic_phase(node_id: int, interval: int, phase_seed: int) -> int:
    if interval <= 0:
        return 0
    return ((phase_seed * 1103515245) + (node_id * 2654435761)) % interval


def _periodic_emit_due(tick: int, interval: int, node_id: int, phase_seed: int) -> bool:
    if tick == 0 or interval <= 0:
        return False
    phase = _periodic_phase(node_id=node_id, interval=interval, phase_seed=phase_seed)
    if tick < phase:
        return False
    return (tick - phase) % interval == 0


@dataclass
class SnnSraParams:
    leak: float = 0.15
    threshold: float = 1.4
    refractory_ticks: int = 4
    min_emit_ticks: int = 3
    delta_threshold: float = 0.05
    ttl: int = 12
    alpha: float = 1.0
    beta: float = 0.5
    gamma: float = 0.8
    damage_boost: float = 1.5
    w_damage: float = 2.0
    w_edge: float = 1.0
    w_self_pressure: float = 1.0
    w_nh_pressure: float = 0.5
    w_memory: float = 0.5
    inhibit_penalty: float = 0.18
    inhibit_release: float = 0.08
    score_scale: float = 10.0
    memory_decay: float = 0.78
    memory_pressure_gain: float = 0.28
    memory_damage_gain: float = 0.6
    memory_switch_gain: float = 0.35
    fast_leak: float = 0.24
    slow_decay: float = 0.88
    slow_leak: float = 0.08
    fast_route_gain: float = 1.0
    fast_pressure_gain: float = 0.5
    fast_damage_gain: float = 1.2
    slow_pressure_gain: float = 0.1
    slow_damage_gain: float = 0.22
    slow_weight: float = 0.35
    switch_guard: float = 0.45
    rebound_guard: float = 0.35
    selected_memory_boost: float = 0.25
    deselected_memory_boost: float = 0.55


class SnnSraAlgorithm:
    def __init__(self, params: SnnSraParams) -> None:
        self.params = params
        self._versions: Dict[tuple[int, int, int], int] = {}

    def initialize_node(self, node: NodeState, node_ids: List[int]) -> None:
        for dst in node_ids:
            if dst == node.node_id:
                continue
            route = RouteState(dst=dst)
            for nbr in node.neighbors:
                cand = CandidateState(next_hop=nbr)
                if nbr == dst:
                    cand.advertised_cost = 0.0
                    cand.route_cost = 1.0
                    cand.belief_score = self.params.score_scale - cand.route_cost
                route.candidates[nbr] = cand
            self._refresh_selection(node, route, tick=0)
            node.route_table[dst] = route

    def seed_initial_updates(self, node: NodeState) -> List[RouteDelta]:
        out: List[RouteDelta] = []
        for dst, route in node.route_table.items():
            if route.selected_next_hop is None:
                continue
            if dst in node.neighbors:
                out.append(
                    RouteDelta(
                        src=node.node_id,
                        dst=dst,
                        advertised_cost=1.0,
                        next_hop=route.selected_next_hop,
                        version=0,
                        ttl=self.params.ttl,
                        pressure=node.pressure,
                    )
                )
        return out

    def on_tick(self, node: NodeState, tick: int) -> List[RouteDelta]:
        emitted: List[RouteDelta] = []
        for dst, route in node.route_table.items():
            selected = route.selected_next_hop
            if selected is None:
                continue
            cand = route.candidates[selected]
            cand.local_memory = (
                self.params.memory_decay * cand.local_memory
                + self.params.memory_pressure_gain * node.pressure
                + self.params.memory_damage_gain * node.damage_signal
            )
            cand.membrane_fast = max(
                0.0,
                cand.membrane_fast
                - self.params.fast_leak
                + self.params.fast_pressure_gain * node.pressure
                + self.params.fast_damage_gain * node.damage_signal,
            )
            cand.membrane_slow = max(
                0.0,
                self.params.slow_decay * cand.membrane_slow
                + self.params.slow_pressure_gain * node.pressure
                + self.params.slow_damage_gain * node.damage_signal
                - self.params.slow_leak,
            )
            cand.membrane = max(0.0, cand.membrane_fast + self.params.slow_weight * cand.membrane_slow)
            if cand.membrane >= self.params.threshold:
                emitted.extend(self._maybe_emit(node, route, cand, tick))
        return emitted

    def on_update(
        self,
        node: NodeState,
        tick: int,
        incoming_neighbor: int,
        update: RouteDelta,
        neighbor_pressure: float,
        edge_cost: float,
    ) -> List[RouteDelta]:
        if update.dst == node.node_id:
            return []
        route = node.route_table[update.dst]
        cand = route.candidates.setdefault(incoming_neighbor, CandidateState(next_hop=incoming_neighbor))
        node.metrics.received_updates += 1
        if update.version < cand.version:
            cand.suppressed_small_delta += 1
            node.metrics.suppressed_small_delta += 1
            return []

        advertised_cost = update.advertised_cost + edge_cost
        new_score = self.params.score_scale - (
            self.params.w_edge * advertised_cost
            + self.params.w_self_pressure * node.pressure
            + self.params.w_nh_pressure * neighbor_pressure
            + self.params.w_damage * node.damage_signal
            + self.params.w_memory * cand.local_memory
        )
        score_delta = abs(new_score - cand.belief_score) if cand.belief_score != float("-inf") else abs(new_score)
        if score_delta < self.params.delta_threshold:
            cand.suppressed_small_delta += 1
            node.metrics.suppressed_small_delta += 1
            return []

        cand.advertised_cost = update.advertised_cost
        cand.edge_cost = edge_cost
        cand.neighbor_pressure = neighbor_pressure
        cand.route_cost = advertised_cost
        cand.belief_score = new_score
        cand.version = update.version
        cand.local_memory = (
            self.params.memory_decay * cand.local_memory
            + self.params.memory_pressure_gain * node.pressure
            + self.params.memory_damage_gain * node.damage_signal
        )
        cand.membrane_fast = max(
            0.0,
            cand.membrane_fast
            - self.params.fast_leak
            + self.params.fast_route_gain * score_delta
            + self.params.fast_pressure_gain * node.pressure
            + self.params.fast_damage_gain * node.damage_signal,
        )
        cand.membrane_slow = max(
            0.0,
            self.params.slow_decay * cand.membrane_slow
            + self.params.slow_pressure_gain * node.pressure
            + self.params.slow_damage_gain * node.damage_signal
            + 0.15 * edge_cost
            - self.params.slow_leak,
        )
        cand.membrane = max(0.0, cand.membrane_fast + self.params.slow_weight * cand.membrane_slow)
        old_selected = route.selected_next_hop
        self._refresh_selection(node, route, tick)
        emitted: List[RouteDelta] = []
        if route.selected_next_hop is not None:
            active = route.candidates[route.selected_next_hop]
            if active.membrane >= self.params.threshold:
                emitted.extend(self._maybe_emit(node, route, active, tick))
        if route.selected_next_hop != old_selected:
            node.metrics.route_changes += 1
            if tick > 0:
                node.metrics.post_event_route_changes += 1
        return emitted

    def _maybe_emit(self, node: NodeState, route: RouteState, cand: CandidateState, tick: int) -> List[RouteDelta]:
        if tick < cand.refractory_until:
            cand.suppressed_refractory += 1
            node.metrics.suppressed_refractory += 1
            return []
        if tick - cand.last_emit_tick < self.params.min_emit_ticks:
            cand.suppressed_emit_interval += 1
            node.metrics.suppressed_emit_interval += 1
            return []
        version_key = (node.node_id, route.dst, cand.next_hop)
        version = self._versions.get(version_key, 0) + 1
        self._versions[version_key] = version
        cand.last_emit_tick = tick
        cand.refractory_until = tick + self.params.refractory_ticks
        cand.membrane_fast = max(0.0, cand.membrane_fast - self.params.threshold)
        cand.membrane = max(0.0, cand.membrane_fast + self.params.slow_weight * cand.membrane_slow)
        cand.fire_count += 1
        node.metrics.fire_count += 1
        node.metrics.emitted_updates += len(node.neighbors)
        return [
            RouteDelta(
                src=node.node_id,
                dst=route.dst,
                advertised_cost=route.selected_cost,
                next_hop=cand.next_hop,
                version=version,
                ttl=self.params.ttl,
                pressure=node.pressure,
            )
        ]

    def _refresh_selection(self, node: NodeState, route: RouteState, tick: int) -> None:
        selected_before = route.selected_next_hop
        stressed_selected = route.candidates.get(selected_before) if selected_before is not None else None
        best = None
        best_score = float("-inf")
        selected_effective_score = float("-inf")
        selected_stress = 0.0
        if stressed_selected is not None and selected_before is not None:
            selected_stress = (
                node.pressure
                + node.damage_signal
                + stressed_selected.local_memory
                + self.params.slow_weight * stressed_selected.membrane_slow
            )
        for cand in route.candidates.values():
            effective_score = cand.belief_score
            if stressed_selected is not None and selected_before is not None:
                if cand.next_hop == selected_before:
                    effective_score -= self.params.inhibit_penalty * selected_stress
                else:
                    effective_score += self.params.inhibit_release * selected_stress
            if cand.next_hop == selected_before:
                selected_effective_score = effective_score
            if best is None or effective_score > best_score:
                best = cand
                best_score = effective_score
        if best is None or best.belief_score == float("-inf"):
            route.selected_next_hop = None
            route.selected_next_hops = []
            route.selected_cost = float("inf")
            route.selected_score = float("-inf")
            return
        if stressed_selected is not None and selected_before is not None and best.next_hop != selected_before:
            switch_margin = self.params.switch_guard + self.params.rebound_guard * selected_stress
            if best_score < selected_effective_score + switch_margin:
                best = stressed_selected
                best_score = selected_effective_score
            else:
                stressed_selected.local_memory += self.params.deselected_memory_boost * max(selected_stress, 1.0)
                stressed_selected.membrane_fast = max(0.0, stressed_selected.membrane_fast - 0.5 * self.params.threshold)
                best.local_memory = max(0.0, best.local_memory - self.params.selected_memory_boost)
        route.selected_next_hop = best.next_hop
        route.selected_next_hops = [best.next_hop]
        route.selected_cost = best.route_cost
        route.selected_score = best_score
        route.last_change_tick = tick


@dataclass
class FloodingParams:
    ttl: int = 12
    periodic_interval_ticks: int = 5
    periodic_phase_seed: int = 0


class FloodingLinkStateAlgorithm:
    def __init__(self, params: FloodingParams) -> None:
        self.params = params
        self._versions: Dict[tuple[int, int, int], int] = {}

    def initialize_node(self, node: NodeState, node_ids: List[int]) -> None:
        for dst in node_ids:
            if dst == node.node_id:
                continue
            route = RouteState(dst=dst)
            for nbr in node.neighbors:
                cand = CandidateState(next_hop=nbr)
                if nbr == dst:
                    cand.advertised_cost = 0.0
                    cand.edge_cost = 1.0
                    cand.route_cost = 1.0
                    cand.belief_score = -cand.route_cost
                route.candidates[nbr] = cand
            self._refresh_selection(node, route, tick=0)
            node.route_table[dst] = route

    def seed_initial_updates(self, node: NodeState) -> List[RouteDelta]:
        return self._emit_full_snapshot(node)

    def on_tick(self, node: NodeState, tick: int) -> List[RouteDelta]:
        if not _periodic_emit_due(
            tick=tick,
            interval=self.params.periodic_interval_ticks,
            node_id=node.node_id,
            phase_seed=self.params.periodic_phase_seed,
        ):
            return []
        return self._emit_full_snapshot(node)

    def on_update(
        self,
        node: NodeState,
        tick: int,
        incoming_neighbor: int,
        update: RouteDelta,
        neighbor_pressure: float,
        edge_cost: float,
    ) -> List[RouteDelta]:
        del neighbor_pressure
        if update.dst == node.node_id:
            return []
        route = node.route_table[update.dst]
        cand = route.candidates.setdefault(incoming_neighbor, CandidateState(next_hop=incoming_neighbor))
        node.metrics.received_updates += 1
        if update.version < cand.version:
            node.metrics.suppressed_small_delta += 1
            return []
        old_selected = route.selected_next_hop
        cand.version = update.version
        cand.advertised_cost = update.advertised_cost
        cand.route_cost = update.advertised_cost + edge_cost
        cand.belief_score = -cand.route_cost
        self._refresh_selection(node, route, tick)
        if route.selected_next_hop != old_selected:
            node.metrics.route_changes += 1
        return []

    def _emit_full_snapshot(self, node: NodeState) -> List[RouteDelta]:
        out: List[RouteDelta] = []
        for dst, route in node.route_table.items():
            if route.selected_next_hop is None or route.selected_cost == float("inf"):
                continue
            version_key = (node.node_id, dst, route.selected_next_hop)
            version = self._versions.get(version_key, 0) + 1
            self._versions[version_key] = version
            out.append(
                RouteDelta(
                    src=node.node_id,
                    dst=dst,
                    advertised_cost=route.selected_cost,
                    next_hop=route.selected_next_hop,
                    version=version,
                    ttl=self.params.ttl,
                    pressure=node.pressure,
                )
            )
        node.metrics.emitted_updates += len(out) * max(1, len(node.neighbors))
        return out

    def _refresh_selection(self, node: NodeState, route: RouteState, tick: int) -> None:
        del node
        best = None
        for cand in route.candidates.values():
            if best is None or cand.route_cost < best.route_cost:
                best = cand
        if best is None or best.route_cost == float("inf"):
            route.selected_next_hop = None
            route.selected_next_hops = []
            route.selected_cost = float("inf")
            route.selected_score = float("-inf")
            return
        route.selected_next_hop = best.next_hop
        route.selected_next_hops = [best.next_hop]
        route.selected_cost = best.route_cost
        route.selected_score = best.belief_score
        route.last_change_tick = tick


@dataclass
class OspfTeParams:
    ttl: int = 12
    periodic_interval_ticks: int = 5
    periodic_phase_seed: int = 0
    self_pressure_weight: float = 1.0
    neighbor_pressure_weight: float = 0.5


class OspfTeStyleAlgorithm:
    def __init__(self, params: OspfTeParams) -> None:
        self.params = params
        self._versions: Dict[tuple[int, int, int], int] = {}

    def initialize_node(self, node: NodeState, node_ids: List[int]) -> None:
        for dst in node_ids:
            if dst == node.node_id:
                continue
            route = RouteState(dst=dst)
            for nbr in node.neighbors:
                cand = CandidateState(next_hop=nbr)
                if nbr == dst:
                    cand.advertised_cost = 0.0
                    cand.route_cost = 1.0
                    cand.belief_score = -cand.route_cost
                route.candidates[nbr] = cand
            self._refresh_selection(route, tick=0)
            node.route_table[dst] = route

    def seed_initial_updates(self, node: NodeState) -> List[RouteDelta]:
        return self._emit_snapshot(node)

    def on_tick(self, node: NodeState, tick: int) -> List[RouteDelta]:
        if not _periodic_emit_due(
            tick=tick,
            interval=self.params.periodic_interval_ticks,
            node_id=node.node_id,
            phase_seed=self.params.periodic_phase_seed,
        ):
            return []
        return self._emit_snapshot(node)

    def on_update(
        self,
        node: NodeState,
        tick: int,
        incoming_neighbor: int,
        update: RouteDelta,
        neighbor_pressure: float,
        edge_cost: float,
    ) -> List[RouteDelta]:
        if update.dst == node.node_id:
            return []
        route = node.route_table[update.dst]
        cand = route.candidates.setdefault(incoming_neighbor, CandidateState(next_hop=incoming_neighbor))
        node.metrics.received_updates += 1
        if update.version < cand.version:
            node.metrics.suppressed_small_delta += 1
            return []
        old_selected = route.selected_next_hop
        cand.version = update.version
        cand.advertised_cost = update.advertised_cost
        cand.route_cost = (
            update.advertised_cost
            + edge_cost
            + self.params.self_pressure_weight * node.pressure
            + self.params.neighbor_pressure_weight * neighbor_pressure
        )
        cand.belief_score = -cand.route_cost
        self._refresh_selection(route, tick=tick)
        if route.selected_next_hop != old_selected:
            node.metrics.route_changes += 1
        return []

    def _emit_snapshot(self, node: NodeState) -> List[RouteDelta]:
        out: List[RouteDelta] = []
        for dst, route in node.route_table.items():
            if route.selected_next_hop is None or route.selected_cost == float("inf"):
                continue
            version_key = (node.node_id, dst, route.selected_next_hop)
            version = self._versions.get(version_key, 0) + 1
            self._versions[version_key] = version
            out.append(
                RouteDelta(
                    src=node.node_id,
                    dst=dst,
                    advertised_cost=route.selected_cost,
                    next_hop=route.selected_next_hop,
                    version=version,
                    ttl=self.params.ttl,
                    pressure=node.pressure,
                )
            )
        node.metrics.emitted_updates += len(out) * max(1, len(node.neighbors))
        return out

    @staticmethod
    def _refresh_selection(route: RouteState, tick: int) -> None:
        best = None
        for cand in route.candidates.values():
            if best is None or cand.route_cost < best.route_cost:
                best = cand
        if best is None or best.route_cost == float("inf"):
            route.selected_next_hop = None
            route.selected_next_hops = []
            route.selected_cost = float("inf")
            route.selected_score = float("-inf")
            return
        route.selected_next_hop = best.next_hop
        route.selected_next_hops = [best.next_hop]
        route.selected_cost = best.route_cost
        route.selected_score = best.belief_score
        route.last_change_tick = tick


@dataclass
class TriggeredTeParams:
    ttl: int = 12
    delta_threshold: float = 0.05
    min_emit_ticks: int = 3
    refresh_interval_ticks: int = 6
    refresh_phase_seed: int = 0
    self_pressure_weight: float = 1.0
    neighbor_pressure_weight: float = 0.5
    damage_weight: float = 2.0
    switch_hysteresis: float = 0.45


class TriggeredTeStyleAlgorithm:
    def __init__(self, params: TriggeredTeParams) -> None:
        self.params = params
        self._versions: Dict[tuple[int, int, int], int] = {}
        self._pending: set[tuple[int, int]] = set()

    def initialize_node(self, node: NodeState, node_ids: List[int]) -> None:
        for dst in node_ids:
            if dst == node.node_id:
                continue
            route = RouteState(dst=dst)
            for nbr in node.neighbors:
                cand = CandidateState(next_hop=nbr)
                if nbr == dst:
                    cand.advertised_cost = 0.0
                    cand.route_cost = 1.0
                    cand.belief_score = -cand.route_cost
                route.candidates[nbr] = cand
            self._refresh_selection(route, tick=0)
            node.route_table[dst] = route

    def seed_initial_updates(self, node: NodeState) -> List[RouteDelta]:
        return self._emit_snapshot(node, tick=0)

    def on_tick(self, node: NodeState, tick: int) -> List[RouteDelta]:
        emitted: List[RouteDelta] = []
        for route in node.route_table.values():
            old_selected = route.selected_next_hop
            old_cost = route.selected_cost
            local_change = False
            for cand in route.candidates.values():
                if cand.advertised_cost == float("inf") and cand.route_cost == float("inf"):
                    continue
                new_cost = (
                    cand.advertised_cost
                    + cand.edge_cost
                    + self.params.self_pressure_weight * node.pressure
                    + self.params.neighbor_pressure_weight * cand.neighbor_pressure
                    + self.params.damage_weight * node.damage_signal
                )
                if abs(new_cost - cand.route_cost) >= self.params.delta_threshold:
                    cand.route_cost = new_cost
                    cand.belief_score = -new_cost
                    local_change = True
            if local_change:
                self._schedule_route_update(node, route, old_selected, old_cost, tick)
        if _periodic_emit_due(
            tick=tick,
            interval=self.params.refresh_interval_ticks,
            node_id=node.node_id,
            phase_seed=self.params.refresh_phase_seed,
        ):
            return emitted + self._emit_snapshot(node, tick)
        pending_keys = [
            key for key in self._pending
            if key[0] == node.node_id
        ]
        for key in pending_keys:
            route = node.route_table.get(key[1])
            if route is None or route.selected_next_hop is None or route.selected_cost == float("inf"):
                self._pending.discard(key)
                continue
            selected = route.candidates[route.selected_next_hop]
            if tick - selected.last_emit_tick < self.params.min_emit_ticks:
                selected.suppressed_emit_interval += 1
                node.metrics.suppressed_emit_interval += 1
                continue
            emitted.extend(self._emit_route(node, route, tick))
            self._pending.discard(key)
        return emitted

    def on_update(
        self,
        node: NodeState,
        tick: int,
        incoming_neighbor: int,
        update: RouteDelta,
        neighbor_pressure: float,
        edge_cost: float,
    ) -> List[RouteDelta]:
        if update.dst == node.node_id:
            return []
        route = node.route_table[update.dst]
        cand = route.candidates.setdefault(incoming_neighbor, CandidateState(next_hop=incoming_neighbor))
        node.metrics.received_updates += 1
        if update.version < cand.version:
            node.metrics.suppressed_small_delta += 1
            return []

        old_selected = route.selected_next_hop
        old_cost = route.selected_cost
        cand.version = update.version
        cand.advertised_cost = update.advertised_cost
        cand.edge_cost = edge_cost
        cand.neighbor_pressure = neighbor_pressure
        cand.route_cost = (
            update.advertised_cost
            + edge_cost
            + self.params.self_pressure_weight * node.pressure
            + self.params.neighbor_pressure_weight * neighbor_pressure
            + self.params.damage_weight * node.damage_signal
        )
        cand.belief_score = -cand.route_cost
        return self._schedule_route_update(node, route, old_selected, old_cost, tick)

    def _emit_snapshot(self, node: NodeState, tick: int) -> List[RouteDelta]:
        out: List[RouteDelta] = []
        for route in node.route_table.values():
            if route.selected_next_hop is None or route.selected_cost == float("inf"):
                continue
            out.extend(self._emit_route(node, route, tick))
        return out

    def _emit_route(self, node: NodeState, route: RouteState, tick: int) -> List[RouteDelta]:
        if route.selected_next_hop is None or route.selected_cost == float("inf"):
            return []
        selected = route.candidates[route.selected_next_hop]
        version_key = (node.node_id, route.dst, route.selected_next_hop)
        version = self._versions.get(version_key, 0) + 1
        self._versions[version_key] = version
        selected.last_emit_tick = tick
        node.metrics.emitted_updates += len(node.neighbors)
        return [
            RouteDelta(
                src=node.node_id,
                dst=route.dst,
                advertised_cost=route.selected_cost,
                next_hop=route.selected_next_hop,
                version=version,
                ttl=self.params.ttl,
                pressure=node.pressure,
            )
        ]

    def _significant_route_change(
        self,
        route: RouteState,
        old_selected: int | None,
        old_cost: float,
    ) -> bool:
        if route.selected_next_hop is None or route.selected_cost == float("inf"):
            return old_selected is not None
        if old_selected is None or old_cost == float("inf"):
            return True
        if route.selected_next_hop != old_selected:
            return True
        return abs(route.selected_cost - old_cost) >= self.params.delta_threshold

    def _schedule_route_update(
        self,
        node: NodeState,
        route: RouteState,
        old_selected: int | None,
        old_cost: float,
        tick: int,
    ) -> List[RouteDelta]:
        self._refresh_selection(route, tick=tick)
        emitted: List[RouteDelta] = []
        if route.selected_next_hop != old_selected:
            node.metrics.route_changes += 1
        if self._significant_route_change(route, old_selected, old_cost):
            key = (node.node_id, route.dst)
            self._pending.add(key)
            selected = route.candidates.get(route.selected_next_hop) if route.selected_next_hop is not None else None
            if selected is not None and tick - selected.last_emit_tick >= self.params.min_emit_ticks:
                emitted.extend(self._emit_route(node, route, tick))
                self._pending.discard(key)
        return emitted

    def _refresh_selection(self, route: RouteState, tick: int) -> None:
        finite = [cand for cand in route.candidates.values() if cand.route_cost < float("inf")]
        if not finite:
            route.selected_next_hop = None
            route.selected_next_hops = []
            route.selected_cost = float("inf")
            route.selected_score = float("-inf")
            return
        best = min(finite, key=lambda cand: cand.route_cost)
        selected_before = route.selected_next_hop
        if selected_before is not None and selected_before in route.candidates:
            selected = route.candidates[selected_before]
            if selected.route_cost < float("inf") and best.next_hop != selected_before:
                improvement = selected.route_cost - best.route_cost
                if improvement < self.params.switch_hysteresis:
                    best = selected
        route.selected_next_hop = best.next_hop
        route.selected_next_hops = [best.next_hop]
        route.selected_cost = best.route_cost
        route.selected_score = best.belief_score
        route.last_change_tick = tick


@dataclass
class TeEcmpParams:
    ttl: int = 12
    periodic_interval_ticks: int = 5
    periodic_phase_seed: int = 0
    self_pressure_weight: float = 1.0
    neighbor_pressure_weight: float = 0.5
    ecmp_slack: float = 1e-6


class TeEcmpStyleAlgorithm:
    def __init__(self, params: TeEcmpParams) -> None:
        self.params = params
        self._versions: Dict[tuple[int, int, int], int] = {}

    def initialize_node(self, node: NodeState, node_ids: List[int]) -> None:
        for dst in node_ids:
            if dst == node.node_id:
                continue
            route = RouteState(dst=dst)
            for nbr in node.neighbors:
                cand = CandidateState(next_hop=nbr)
                if nbr == dst:
                    cand.advertised_cost = 0.0
                    cand.route_cost = 1.0
                    cand.belief_score = -cand.route_cost
                route.candidates[nbr] = cand
            self._refresh_selection(route, tick=0)
            node.route_table[dst] = route

    def seed_initial_updates(self, node: NodeState) -> List[RouteDelta]:
        return self._emit_snapshot(node)

    def on_tick(self, node: NodeState, tick: int) -> List[RouteDelta]:
        if not _periodic_emit_due(
            tick=tick,
            interval=self.params.periodic_interval_ticks,
            node_id=node.node_id,
            phase_seed=self.params.periodic_phase_seed,
        ):
            return []
        return self._emit_snapshot(node)

    def on_update(
        self,
        node: NodeState,
        tick: int,
        incoming_neighbor: int,
        update: RouteDelta,
        neighbor_pressure: float,
        edge_cost: float,
    ) -> List[RouteDelta]:
        if update.dst == node.node_id:
            return []
        route = node.route_table[update.dst]
        cand = route.candidates.setdefault(incoming_neighbor, CandidateState(next_hop=incoming_neighbor))
        node.metrics.received_updates += 1
        if update.version < cand.version:
            node.metrics.suppressed_small_delta += 1
            return []
        old_selected = list(route.selected_next_hops)
        cand.version = update.version
        cand.advertised_cost = update.advertised_cost
        cand.route_cost = (
            update.advertised_cost
            + edge_cost
            + self.params.self_pressure_weight * node.pressure
            + self.params.neighbor_pressure_weight * neighbor_pressure
        )
        cand.belief_score = -cand.route_cost
        self._refresh_selection(route, tick=tick)
        if route.selected_next_hops != old_selected:
            node.metrics.route_changes += 1
        return []

    def _emit_snapshot(self, node: NodeState) -> List[RouteDelta]:
        out: List[RouteDelta] = []
        for dst, route in node.route_table.items():
            if not route.selected_next_hops or route.selected_cost == float("inf"):
                continue
            for next_hop in route.selected_next_hops:
                version_key = (node.node_id, dst, next_hop)
                version = self._versions.get(version_key, 0) + 1
                self._versions[version_key] = version
                out.append(
                    RouteDelta(
                        src=node.node_id,
                        dst=dst,
                        advertised_cost=route.selected_cost,
                        next_hop=next_hop,
                        version=version,
                        ttl=self.params.ttl,
                        pressure=node.pressure,
                    )
                )
        node.metrics.emitted_updates += len(out) * max(1, len(node.neighbors))
        return out

    def _refresh_selection(self, route: RouteState, tick: int) -> None:
        finite = [cand for cand in route.candidates.values() if cand.route_cost < float("inf")]
        if not finite:
            route.selected_next_hop = None
            route.selected_next_hops = []
            route.selected_cost = float("inf")
            route.selected_score = float("-inf")
            return
        best_cost = min(cand.route_cost for cand in finite)
        winners = sorted(
            cand.next_hop
            for cand in finite
            if cand.route_cost <= best_cost + self.params.ecmp_slack
        )
        route.selected_next_hops = winners
        route.selected_next_hop = winners[0] if winners else None
        route.selected_cost = best_cost
        route.selected_score = -best_cost
        route.last_change_tick = tick


class ShortestPathOracleAlgorithm:
    def __init__(self) -> None:
        self._neighbors: Dict[int, List[int]] = {}
        self._node_ids: List[int] = []
        self._link_costs: Dict[tuple[int, int], float] = {}

    def initialize_node(self, node: NodeState, node_ids: List[int]) -> None:
        self._neighbors[node.node_id] = list(node.neighbors)
        self._node_ids = list(node_ids)
        for dst in node_ids:
            if dst == node.node_id:
                continue
            route = RouteState(dst=dst)
            for nbr in node.neighbors:
                route.candidates[nbr] = CandidateState(next_hop=nbr)
            node.route_table[dst] = route

    def seed_initial_updates(self, node: NodeState) -> List[RouteDelta]:
        del node
        return []

    def on_tick(self, node: NodeState, tick: int) -> List[RouteDelta]:
        del tick
        self._recompute_node(node)
        return []

    def on_update(
        self,
        node: NodeState,
        tick: int,
        incoming_neighbor: int,
        update: RouteDelta,
        neighbor_pressure: float,
        edge_cost: float,
    ) -> List[RouteDelta]:
        del tick, incoming_neighbor, update, neighbor_pressure, edge_cost
        self._recompute_node(node)
        return []

    def set_link_costs(self, link_costs: Dict[tuple[int, int], float]) -> None:
        self._link_costs = dict(link_costs)

    def _recompute_node(self, node: NodeState) -> None:
        distances, first_hops = self._shortest_paths_from(node.node_id)
        for dst in self._node_ids:
            if dst == node.node_id:
                continue
            route = node.route_table[dst]
            best_next_hop = first_hops.get(dst)
            best_cost = distances.get(dst, float("inf"))
            old_selected = route.selected_next_hop
            for nbr, cand in route.candidates.items():
                if nbr == best_next_hop:
                    cand.route_cost = best_cost
                    cand.belief_score = -best_cost
                else:
                    cand.route_cost = float("inf")
                    cand.belief_score = float("-inf")
            route.selected_next_hop = best_next_hop
            route.selected_next_hops = [] if best_next_hop is None else [best_next_hop]
            route.selected_cost = best_cost
            route.selected_score = -best_cost if best_next_hop is not None else float("-inf")
            if best_next_hop != old_selected:
                node.metrics.route_changes += 1

    def _shortest_paths_from(self, src: int) -> tuple[Dict[int, float], Dict[int, int]]:
        distances: Dict[int, float] = {src: 0.0}
        first_hops: Dict[int, int] = {}
        heap: list[tuple[float, int, int]] = []
        for nbr in self._neighbors.get(src, []):
            cost = self._link_costs.get((src, nbr), 1.0)
            heapq.heappush(heap, (cost, nbr, nbr))
        while heap:
            dist, node_id, first_hop = heapq.heappop(heap)
            if node_id in distances:
                continue
            distances[node_id] = dist
            first_hops[node_id] = first_hop
            for nbr in self._neighbors.get(node_id, []):
                if nbr in distances:
                    continue
                edge_cost = self._link_costs.get((node_id, nbr), 1.0)
                heapq.heappush(heap, (dist + edge_cost, nbr, first_hop))
        return distances, first_hops


@dataclass
class BanditParams:
    ttl: int = 12
    periodic_interval_ticks: int = 3
    periodic_phase_seed: int = 0
    learning_rate: float = 0.35
    exploration_c: float = 0.6
    self_pressure_weight: float = 1.0
    neighbor_pressure_weight: float = 0.5
    damage_weight: float = 1.5
    score_scale: float = 10.0
    seed: int = 12345


class BanditRoutingAlgorithm:
    def __init__(self, params: BanditParams) -> None:
        self.params = params
        self._versions: Dict[tuple[int, int, int], int] = {}
        self._tick = 0
        self._rng = random.Random(params.seed)

    def initialize_node(self, node: NodeState, node_ids: List[int]) -> None:
        for dst in node_ids:
            if dst == node.node_id:
                continue
            route = RouteState(dst=dst)
            for nbr in node.neighbors:
                cand = CandidateState(next_hop=nbr)
                if nbr == dst:
                    cand.advertised_cost = 0.0
                    cand.route_cost = 1.0
                    cand.belief_score = self.params.score_scale - 1.0
                    cand.sample_count = 1
                route.candidates[nbr] = cand
            self._refresh_selection(route, tick=0)
            node.route_table[dst] = route

    def seed_initial_updates(self, node: NodeState) -> List[RouteDelta]:
        return self._emit_snapshot(node)

    def on_tick(self, node: NodeState, tick: int) -> List[RouteDelta]:
        self._tick = tick
        route_changed = False
        for route in node.route_table.values():
            old_selected = route.selected_next_hop
            self._refresh_selection(route, tick=tick)
            if route.selected_next_hop != old_selected:
                route_changed = True
                node.metrics.route_changes += 1
        if not _periodic_emit_due(
            tick=tick,
            interval=self.params.periodic_interval_ticks,
            node_id=node.node_id,
            phase_seed=self.params.periodic_phase_seed,
        ):
            return []
        return self._emit_snapshot(node)

    def on_update(
        self,
        node: NodeState,
        tick: int,
        incoming_neighbor: int,
        update: RouteDelta,
        neighbor_pressure: float,
        edge_cost: float,
    ) -> List[RouteDelta]:
        self._tick = tick
        if update.dst == node.node_id:
            return []
        route = node.route_table[update.dst]
        cand = route.candidates.setdefault(incoming_neighbor, CandidateState(next_hop=incoming_neighbor))
        node.metrics.received_updates += 1
        if update.version < cand.version:
            node.metrics.suppressed_small_delta += 1
            return []
        cand.version = update.version
        cand.advertised_cost = update.advertised_cost
        cand.route_cost = update.advertised_cost + edge_cost
        reward = -(
            cand.route_cost
            + self.params.self_pressure_weight * node.pressure
            + self.params.neighbor_pressure_weight * neighbor_pressure
            + self.params.damage_weight * node.damage_signal
        )
        if cand.belief_score == float("-inf"):
            cand.belief_score = reward
        else:
            cand.belief_score = (1.0 - self.params.learning_rate) * cand.belief_score + self.params.learning_rate * reward
        cand.sample_count += 1
        old_selected = route.selected_next_hop
        self._refresh_selection(route, tick=tick)
        if route.selected_next_hop != old_selected:
            node.metrics.route_changes += 1
        return []

    def _emit_snapshot(self, node: NodeState) -> List[RouteDelta]:
        out: List[RouteDelta] = []
        for dst, route in node.route_table.items():
            if route.selected_next_hop is None or route.selected_cost == float("inf"):
                continue
            version_key = (node.node_id, dst, route.selected_next_hop)
            version = self._versions.get(version_key, 0) + 1
            self._versions[version_key] = version
            out.append(
                RouteDelta(
                    src=node.node_id,
                    dst=dst,
                    advertised_cost=route.selected_cost,
                    next_hop=route.selected_next_hop,
                    version=version,
                    ttl=self.params.ttl,
                    pressure=node.pressure,
                )
            )
        node.metrics.emitted_updates += len(out) * max(1, len(node.neighbors))
        return out

    def _refresh_selection(self, route: RouteState, tick: int) -> None:
        best = None
        best_value = float("-inf")
        for cand in route.candidates.values():
            if cand.route_cost == float("inf") and cand.belief_score == float("-inf"):
                continue
            bonus = self.params.exploration_c / ((cand.sample_count + 1) ** 0.5)
            value = cand.belief_score + bonus
            if best is None or value > best_value or (abs(value - best_value) <= 1e-9 and self._rng.random() < 0.5):
                best = cand
                best_value = value
        if best is None:
            route.selected_next_hop = None
            route.selected_next_hops = []
            route.selected_cost = float("inf")
            route.selected_score = float("-inf")
            return
        route.selected_next_hop = best.next_hop
        route.selected_next_hops = [best.next_hop]
        route.selected_cost = best.route_cost
        route.selected_score = best.belief_score
        route.last_change_tick = tick
