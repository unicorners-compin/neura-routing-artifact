from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

from .types import Link


@dataclass
class TrafficFlow:
    src: int
    dst: int
    demand: float


@dataclass
class TrafficResult:
    offered_load: float
    delivered_load: float
    dropped_load: float
    mean_delay: float | None
    max_link_utilization: float
    per_link_load: Dict[Tuple[int, int], float]


@dataclass
class QueueTrafficResult:
    offered_load: float
    delivered_load: float
    dropped_load: float
    mean_delay: float | None
    mean_hops: float | None
    mean_path_stretch: float | None
    max_link_utilization: float
    per_link_offered: Dict[Tuple[int, int], float]
    per_link_queue: Dict[Tuple[int, int], float]


def evaluate_forwarding_snapshot(
    next_hop_table: Dict[Tuple[int, int], int | List[int] | None],
    links: List[Link],
    flows: List[TrafficFlow],
    link_capacity: float = 10.0,
    link_delay: float = 1.0,
    per_link_capacity: Dict[Tuple[int, int], float] | None = None,
) -> TrafficResult:
    link_map = {(link.src, link.dst): link for link in links if link.up}
    per_link_load: Dict[Tuple[int, int], float] = {}
    offered = 0.0
    delivered = 0.0
    dropped = 0.0
    weighted_delay = 0.0

    for flow in flows:
        delivered_piece, dropped_piece, delay_piece = _deliver_flow(
            src=flow.src,
            dst=flow.dst,
            demand=flow.demand,
            next_hop_table=next_hop_table,
            link_map=link_map,
            per_link_load=per_link_load,
            link_capacity=link_capacity,
            link_delay=link_delay,
            per_link_capacity=per_link_capacity,
        )
        offered += flow.demand
        delivered += delivered_piece
        dropped += dropped_piece
        weighted_delay += delay_piece

    max_util = 0.0
    for edge, load in per_link_load.items():
        edge_capacity = link_capacity if per_link_capacity is None else per_link_capacity.get(edge, link_capacity)
        max_util = max(max_util, load / edge_capacity if edge_capacity > 0 else 0.0)
    mean_delay = None if delivered == 0 else weighted_delay / delivered
    return TrafficResult(
        offered_load=offered,
        delivered_load=delivered,
        dropped_load=dropped,
        mean_delay=mean_delay,
        max_link_utilization=max_util,
        per_link_load=per_link_load,
    )


def evaluate_forwarding_with_queues(
    next_hop_table: Dict[Tuple[int, int], int | List[int] | None],
    links: List[Link],
    flows: List[TrafficFlow],
    link_capacity: float,
    link_delay: float,
    queue_state: Dict[Tuple[int, int], float],
    queue_capacity: float,
    shortest_hops: Dict[Tuple[int, int], int] | None = None,
    per_link_capacity: Dict[Tuple[int, int], float] | None = None,
) -> QueueTrafficResult:
    link_map = {(link.src, link.dst): link for link in links if link.up}
    per_link_offered: Dict[Tuple[int, int], float] = {}
    flow_paths: List[tuple[float, List[Tuple[int, int]]]] = []
    offered = 0.0

    for flow in flows:
        offered += flow.demand
        nh = next_hop_table.get((flow.src, flow.dst))
        if isinstance(nh, list):
            next_hops = nh
        elif nh is None:
            next_hops = []
        else:
            next_hops = [nh]
        if not next_hops:
            flow_paths.append((flow.demand, []))
            continue
        split = flow.demand / len(next_hops)
        for next_hop in next_hops:
            path = _trace_path(
                src=flow.src,
                dst=flow.dst,
                next_hop_table=next_hop_table,
                link_map=link_map,
                first_hop_override=next_hop,
            )
            flow_paths.append((split, path))
            if not path:
                continue
            for edge in path:
                per_link_offered[edge] = per_link_offered.get(edge, 0.0) + split

    edge_delivery_ratio: Dict[Tuple[int, int], float] = {}
    next_queue_state: Dict[Tuple[int, int], float] = dict(queue_state)
    max_util = 0.0
    for edge, offered_edge in per_link_offered.items():
        prev_queue = queue_state.get(edge, 0.0)
        total_arrival = prev_queue + offered_edge
        edge_capacity = link_capacity if per_link_capacity is None else per_link_capacity.get(edge, link_capacity)
        served = min(total_arrival, edge_capacity)
        ratio = 1.0 if total_arrival <= 0 else min(served / total_arrival, 1.0)
        next_queue_state[edge] = min(max(total_arrival - served, 0.0), queue_capacity)
        edge_delivery_ratio[edge] = ratio
        max_util = max(max_util, min(total_arrival / edge_capacity, 1.0) if edge_capacity > 0 else 0.0)

    delivered = 0.0
    dropped = 0.0
    weighted_delay = 0.0
    weighted_hops = 0.0
    weighted_stretch = 0.0
    stretch_delivered = 0.0
    for demand, path in flow_paths:
        if not path:
            dropped += demand
            continue
        ratio = min(edge_delivery_ratio.get(edge, 0.0) for edge in path)
        delivered_piece = demand * ratio
        dropped_piece = demand - delivered_piece
        avg_queue_delay = 0.0
        for edge in path:
            edge_capacity = link_capacity if per_link_capacity is None else per_link_capacity.get(edge, link_capacity)
            avg_queue_delay += queue_state.get(edge, 0.0) / edge_capacity if edge_capacity > 0 else 0.0
        delay = len(path) * link_delay + avg_queue_delay
        delivered += delivered_piece
        dropped += dropped_piece
        weighted_delay += delivered_piece * delay
        weighted_hops += delivered_piece * len(path)
        if shortest_hops is not None and path:
            src = path[0][0]
            dst = path[-1][1]
            shortest = shortest_hops.get((src, dst))
            if shortest is not None and shortest > 0:
                weighted_stretch += delivered_piece * (len(path) / shortest)
                stretch_delivered += delivered_piece

    return QueueTrafficResult(
        offered_load=offered,
        delivered_load=delivered,
        dropped_load=dropped,
        mean_delay=None if delivered == 0 else weighted_delay / delivered,
        mean_hops=None if delivered == 0 else weighted_hops / delivered,
        mean_path_stretch=None if stretch_delivered == 0 else weighted_stretch / stretch_delivered,
        max_link_utilization=max_util,
        per_link_offered=per_link_offered,
        per_link_queue=next_queue_state,
    )


def _deliver_flow(
    src: int,
    dst: int,
    demand: float,
    next_hop_table: Dict[Tuple[int, int], int | List[int] | None],
    link_map: Dict[Tuple[int, int], Link],
    per_link_load: Dict[Tuple[int, int], float],
    link_capacity: float,
    link_delay: float,
    per_link_capacity: Dict[Tuple[int, int], float] | None,
) -> tuple[float, float, float]:
    nh = next_hop_table.get((src, dst))
    if isinstance(nh, list):
        if not nh:
            return 0.0, demand, 0.0
        delivered = 0.0
        dropped = 0.0
        weighted_delay = 0.0
        split_demand = demand / len(nh)
        for next_hop in nh:
            d, dr, w = _deliver_single_path(
                src=src,
                dst=dst,
                demand=split_demand,
                next_hop_override=next_hop,
                next_hop_table=next_hop_table,
                link_map=link_map,
                per_link_load=per_link_load,
                link_capacity=link_capacity,
                link_delay=link_delay,
                per_link_capacity=per_link_capacity,
            )
            delivered += d
            dropped += dr
            weighted_delay += w
        return delivered, dropped, weighted_delay
    return _deliver_single_path(
        src=src,
        dst=dst,
        demand=demand,
        next_hop_override=None,
        next_hop_table=next_hop_table,
        link_map=link_map,
        per_link_load=per_link_load,
        link_capacity=link_capacity,
        link_delay=link_delay,
        per_link_capacity=per_link_capacity,
    )


def _deliver_single_path(
    src: int,
    dst: int,
    demand: float,
    next_hop_override: int | None,
    next_hop_table: Dict[Tuple[int, int], int | List[int] | None],
    link_map: Dict[Tuple[int, int], Link],
    per_link_load: Dict[Tuple[int, int], float],
    link_capacity: float,
    link_delay: float,
    per_link_capacity: Dict[Tuple[int, int], float] | None,
) -> tuple[float, float, float]:
    cur = src
    visited = set()
    path: List[Tuple[int, int]] = []
    ok = True
    first_hop_used = False
    while cur != dst:
        if cur in visited:
            ok = False
            break
        visited.add(cur)
        if not first_hop_used and next_hop_override is not None:
            nh = next_hop_override
            first_hop_used = True
        else:
            entry = next_hop_table.get((cur, dst))
            if isinstance(entry, list):
                nh = entry[0] if entry else None
            else:
                nh = entry
        if nh is None or (cur, nh) not in link_map:
            ok = False
            break
        path.append((cur, nh))
        cur = nh
        if len(path) > len(next_hop_table):
            ok = False
            break
    if not ok:
        return 0.0, demand, 0.0
    bottleneck = demand
    for edge in path:
        current = per_link_load.get(edge, 0.0)
        edge_capacity = link_capacity if per_link_capacity is None else per_link_capacity.get(edge, link_capacity)
        available = max(edge_capacity - current, 0.0)
        bottleneck = min(bottleneck, available)
    if bottleneck <= 0.0:
        return 0.0, demand, 0.0
    for edge in path:
        per_link_load[edge] = per_link_load.get(edge, 0.0) + bottleneck
    return bottleneck, demand - bottleneck, bottleneck * (len(path) * link_delay)


def _trace_path(
    src: int,
    dst: int,
    next_hop_table: Dict[Tuple[int, int], int | List[int] | None],
    link_map: Dict[Tuple[int, int], Link],
    first_hop_override: int | None = None,
) -> List[Tuple[int, int]]:
    cur = src
    visited = set()
    path: List[Tuple[int, int]] = []
    first_hop_used = False
    while cur != dst:
        if cur in visited:
            return []
        visited.add(cur)
        if not first_hop_used and first_hop_override is not None:
            nh = first_hop_override
            first_hop_used = True
        else:
            entry = next_hop_table.get((cur, dst))
            if isinstance(entry, list):
                nh = entry[0] if entry else None
            else:
                nh = entry
        if nh is None or (cur, nh) not in link_map:
            return []
        path.append((cur, nh))
        cur = nh
        if len(path) > len(next_hop_table):
            return []
    return path
