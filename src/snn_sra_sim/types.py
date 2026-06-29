from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


NodeId = int
RouteKey = Tuple[NodeId, NodeId]


@dataclass
class Link:
    src: NodeId
    dst: NodeId
    base_cost: float = 1.0
    dynamic_cost: float = 0.0
    up: bool = True

    @property
    def effective_cost(self) -> float:
        return self.base_cost + self.dynamic_cost


@dataclass
class RouteDelta:
    src: NodeId
    dst: NodeId
    advertised_cost: float
    next_hop: NodeId
    version: int
    ttl: int
    pressure: float


@dataclass
class CandidateState:
    next_hop: NodeId
    advertised_cost: float = float("inf")
    route_cost: float = float("inf")
    belief_score: float = float("-inf")
    edge_cost: float = 0.0
    neighbor_pressure: float = 0.0
    membrane: float = 0.0
    membrane_fast: float = 0.0
    membrane_slow: float = 0.0
    local_memory: float = 0.0
    refractory_until: int = 0
    last_emit_tick: int = -10**9
    version: int = -1
    sample_count: int = 0
    fire_count: int = 0
    suppressed_small_delta: int = 0
    suppressed_refractory: int = 0
    suppressed_emit_interval: int = 0


@dataclass
class RouteState:
    dst: NodeId
    candidates: Dict[NodeId, CandidateState] = field(default_factory=dict)
    selected_next_hop: Optional[NodeId] = None
    selected_next_hops: List[NodeId] = field(default_factory=list)
    selected_cost: float = float("inf")
    selected_score: float = float("-inf")
    last_change_tick: int = 0


@dataclass
class NodeMetrics:
    emitted_updates: int = 0
    received_updates: int = 0
    suppressed_small_delta: int = 0
    suppressed_refractory: int = 0
    suppressed_emit_interval: int = 0
    route_changes: int = 0
    fire_count: int = 0
    post_event_route_changes: int = 0


@dataclass
class NodeState:
    node_id: NodeId
    neighbors: List[NodeId] = field(default_factory=list)
    pressure: float = 0.0
    damage_signal: float = 0.0
    route_table: Dict[NodeId, RouteState] = field(default_factory=dict)
    metrics: NodeMetrics = field(default_factory=NodeMetrics)
