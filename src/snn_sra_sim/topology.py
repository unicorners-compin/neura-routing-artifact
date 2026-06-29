from __future__ import annotations

import random
import math
from typing import Dict, Iterable, List, Tuple

from .types import Link, NodeId


def _is_connected(node_count: int, adj: Dict[NodeId, List[NodeId]]) -> bool:
    if node_count == 0:
        return True
    seen = set()
    stack = [1]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(n for n in adj[cur] if n not in seen)
    return len(seen) == node_count


def generate_connected_er_topology(node_count: int, edge_prob: float, seed: int) -> List[Link]:
    rng = random.Random(seed)
    while True:
        adj: Dict[NodeId, List[NodeId]] = {i: [] for i in range(1, node_count + 1)}
        edges: List[Tuple[NodeId, NodeId]] = []
        for src in range(1, node_count + 1):
            for dst in range(src + 1, node_count + 1):
                if rng.random() <= edge_prob:
                    adj[src].append(dst)
                    adj[dst].append(src)
                    edges.append((src, dst))
        if _is_connected(node_count, adj):
            links: List[Link] = []
            for src, dst in edges:
                links.append(Link(src=src, dst=dst))
                links.append(Link(src=dst, dst=src))
            return links


def generate_connected_ba_topology(node_count: int, attach_edges: int, seed: int) -> List[Link]:
    if node_count < 2:
        return []
    m = max(1, min(attach_edges, node_count - 1))
    rng = random.Random(seed)
    initial = max(2, m + 1)
    adj: Dict[NodeId, List[NodeId]] = {i: [] for i in range(1, node_count + 1)}
    edges: set[Tuple[NodeId, NodeId]] = set()

    for src in range(1, initial + 1):
        for dst in range(src + 1, initial + 1):
            adj[src].append(dst)
            adj[dst].append(src)
            edges.add((src, dst))

    targets: List[NodeId] = []
    for node_id in range(1, initial + 1):
        targets.extend([node_id] * len(adj[node_id]))

    for new_node in range(initial + 1, node_count + 1):
        chosen: set[NodeId] = set()
        while len(chosen) < m:
            if targets:
                chosen.add(rng.choice(targets))
            else:
                chosen.add(rng.randint(1, new_node - 1))
        for old_node in chosen:
            src, dst = sorted((new_node, old_node))
            edges.add((src, dst))
            adj[new_node].append(old_node)
            adj[old_node].append(new_node)
        targets.extend(chosen)
        targets.extend([new_node] * len(chosen))

    links: List[Link] = []
    for src, dst in sorted(edges):
        links.append(Link(src=src, dst=dst))
        links.append(Link(src=dst, dst=src))
    return links


def generate_connected_rgg_topology(node_count: int, radius: float, seed: int) -> List[Link]:
    rng = random.Random(seed)
    radius = max(0.01, min(radius, 1.5))
    while True:
        positions: Dict[NodeId, tuple[float, float]] = {
            i: (rng.random(), rng.random()) for i in range(1, node_count + 1)
        }
        adj: Dict[NodeId, List[NodeId]] = {i: [] for i in range(1, node_count + 1)}
        edges: List[Tuple[NodeId, NodeId]] = []
        for src in range(1, node_count + 1):
            x1, y1 = positions[src]
            for dst in range(src + 1, node_count + 1):
                x2, y2 = positions[dst]
                if math.hypot(x1 - x2, y1 - y2) <= radius:
                    adj[src].append(dst)
                    adj[dst].append(src)
                    edges.append((src, dst))
        if _is_connected(node_count, adj):
            links: List[Link] = []
            for src, dst in edges:
                links.append(Link(src=src, dst=dst))
                links.append(Link(src=dst, dst=src))
            return links


def generate_connected_topology(
    kind: str,
    node_count: int,
    seed: int,
    edge_prob: float = 0.06,
    attach_edges: int = 2,
    geo_radius: float = 0.17,
) -> List[Link]:
    if kind == "er":
        return generate_connected_er_topology(node_count=node_count, edge_prob=edge_prob, seed=seed)
    if kind == "ba":
        return generate_connected_ba_topology(node_count=node_count, attach_edges=attach_edges, seed=seed)
    if kind == "rgg":
        return generate_connected_rgg_topology(node_count=node_count, radius=geo_radius, seed=seed)
    raise ValueError(f"unknown topology kind: {kind}")


def links_to_neighbor_map(links: Iterable[Link]) -> Dict[NodeId, List[NodeId]]:
    nbrs: Dict[NodeId, List[NodeId]] = {}
    for link in links:
        if not link.up:
            continue
        nbrs.setdefault(link.src, []).append(link.dst)
        nbrs.setdefault(link.dst, nbrs.get(link.dst, []))
    for node_id in nbrs:
        nbrs[node_id] = sorted(set(nbrs[node_id]))
    return nbrs
