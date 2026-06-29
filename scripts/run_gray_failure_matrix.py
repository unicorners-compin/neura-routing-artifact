#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from compare_queue_closed_loop import run_closed_loop


METHODS = ("snn_sra", "ospf_te", "triggered_te", "bandit")
CONTROL_RECORD_BYTES = 32.0


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def ci95(values: list[float]) -> float:
    if len(values) <= 1:
        return 0.0
    import math

    m = mean(values)
    var = sum((x - m) ** 2 for x in values) / (len(values) - 1)
    return 1.96 * math.sqrt(var) / math.sqrt(len(values))


def first_active_tick(timeline: list[dict[str, object]], degrade_start: int) -> int | None:
    for row in timeline:
        tick = int(row["tick"])
        if tick < degrade_start:
            continue
        if float(row["emitted_this_tick"]) > 0.0:
            return tick - degrade_start
    return None


def recovery_tick(
    timeline: list[dict[str, object]],
    degrade_start: int,
    degrade_end: int,
    delivery_target: float,
) -> int | None:
    rows = [row for row in timeline if degrade_start <= int(row["tick"]) <= degrade_end]
    for idx, row in enumerate(rows):
        if float(row["delivery_ratio"]) < delivery_target:
            continue
        if idx + 1 < len(rows):
            nxt = rows[idx + 1]
            if float(nxt["delivery_ratio"]) >= delivery_target:
                return int(row["tick"]) - degrade_start
        else:
            return int(row["tick"]) - degrade_start
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Run gray-failure validation for the NEURA control-law paper.")
    ap.add_argument("--issue", type=int, default=4)
    ap.add_argument("--topology", choices=("er", "ba"), default="er")
    ap.add_argument("--nodes", type=int, default=100)
    ap.add_argument("--edge-prob", type=float, default=0.06)
    ap.add_argument("--ba-attach", type=int, default=2)
    ap.add_argument("--ticks", type=int, default=120)
    ap.add_argument("--seeds", type=int, nargs="+", default=[121, 122, 123, 124, 125])
    ap.add_argument("--hotspot-node", type=int, default=1)
    ap.add_argument("--degrade-start", type=int, default=40)
    ap.add_argument("--degrade-end", type=int, default=70)
    ap.add_argument("--degrade-factor", type=float, default=0.30)
    ap.add_argument("--num-flows", type=int, default=400)
    ap.add_argument("--base-demand", type=float, default=0.22)
    ap.add_argument("--hotspot-share", type=float, default=0.70)
    ap.add_argument("--link-capacity", type=float, default=10.0)
    ap.add_argument("--link-delay", type=float, default=1.0)
    ap.add_argument("--queue-capacity", type=float, default=30.0)
    ap.add_argument("--pressure-gain", type=float, default=1.8)
    ap.add_argument("--delivery-target", type=float, default=0.90)
    args = ap.parse_args()

    out_dir = REPO_ROOT / "results" / f"issue-{args.issue}" / "artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)

    timeline_rows: list[dict[str, object]] = []
    detail_rows: list[dict[str, object]] = []

    for seed in args.seeds:
        for method in METHODS:
            result = run_closed_loop(
                method=method,
                topology=args.topology,
                node_count=args.nodes,
                edge_prob=args.edge_prob,
                ba_attach=args.ba_attach,
                ticks=args.ticks,
                seed=seed,
                hotspot_node=args.hotspot_node,
                hotspot_start=args.degrade_start,
                hotspot_end=args.degrade_end,
                num_flows=args.num_flows,
                base_demand=args.base_demand,
                hotspot_share=args.hotspot_share,
                burst_multiplier=1.0,
                link_capacity=args.link_capacity,
                link_delay=args.link_delay,
                queue_capacity=args.queue_capacity,
                pressure_gain=args.pressure_gain,
                burst_windows=[],
                degrade_node=args.hotspot_node,
                degrade_start=args.degrade_start,
                degrade_end=args.degrade_end,
                degrade_factor=args.degrade_factor,
            )
            timeline = result["timeline"]
            impairment_rows = [row for row in timeline if int(row["gray_failure_active"]) == 1]
            for row in timeline:
                timeline_rows.append(
                    {
                        "topology": args.topology,
                        "seed": seed,
                        "method": method,
                        "tick": int(row["tick"]),
                        "time_ms": int(row["tick"]) * 10,
                        "gray_failure_active": int(row["gray_failure_active"]),
                        "delivery_ratio": float(row["delivery_ratio"]),
                        "control_bytes": float(row["emitted_this_tick"]) * CONTROL_RECORD_BYTES,
                        "degraded_node_pressure": float(row["degraded_node_pressure"]) if row["degraded_node_pressure"] is not None else 0.0,
                    }
                )
            detail_rows.append(
                {
                    "topology": args.topology,
                    "seed": seed,
                    "method": method,
                    "first_active_tick_after_impairment": first_active_tick(timeline, args.degrade_start),
                    "impairment_relief_tick": recovery_tick(
                        timeline,
                        args.degrade_start,
                        args.degrade_end,
                        args.delivery_target,
                    ),
                    "impairment_mean_delivery_ratio": mean([float(row["delivery_ratio"]) for row in impairment_rows]),
                    "impairment_mean_control_bytes": mean([float(row["emitted_this_tick"]) * CONTROL_RECORD_BYTES for row in impairment_rows]),
                    "impairment_peak_pressure": max(float(row["degraded_node_pressure"]) for row in impairment_rows) if impairment_rows else 0.0,
                    "total_control_bytes": float(result["totals"]["emitted_updates"]) * CONTROL_RECORD_BYTES,
                }
            )

    summary_rows: list[dict[str, object]] = []
    for method in METHODS:
        rows = [row for row in detail_rows if row["method"] == method]
        summary_rows.append(
            {
                "method": method,
                "first_active_tick_mean": mean([float(row["first_active_tick_after_impairment"]) for row in rows if row["first_active_tick_after_impairment"] is not None]),
                "relief_tick_mean": mean([float(row["impairment_relief_tick"]) for row in rows if row["impairment_relief_tick"] is not None]),
                "impairment_delivery_mean": mean([float(row["impairment_mean_delivery_ratio"]) for row in rows]),
                "impairment_delivery_ci95": ci95([float(row["impairment_mean_delivery_ratio"]) for row in rows]),
                "impairment_control_bytes_mean": mean([float(row["impairment_mean_control_bytes"]) for row in rows]),
                "impairment_control_bytes_ci95": ci95([float(row["impairment_mean_control_bytes"]) for row in rows]),
                "impairment_peak_pressure_mean": mean([float(row["impairment_peak_pressure"]) for row in rows]),
                "total_control_bytes_mean": mean([float(row["total_control_bytes"]) for row in rows]),
            }
        )

    stem = (
        f"gray_failure_matrix_er_n{args.nodes}_s{len(args.seeds)}"
        if args.topology == "er"
        else f"gray_failure_matrix_{args.topology}_n{args.nodes}_s{len(args.seeds)}"
    )
    timeline_csv = out_dir / f"{stem}_timeline.csv"
    detail_csv = out_dir / f"{stem}_detail.csv"
    summary_csv = out_dir / f"{stem}_summary.csv"
    summary_json = out_dir / f"{stem}_summary.json"

    with timeline_csv.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(timeline_rows[0].keys()))
        writer.writeheader()
        writer.writerows(timeline_rows)

    with detail_csv.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(detail_rows[0].keys()))
        writer.writeheader()
        writer.writerows(detail_rows)

    with summary_csv.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(summary_rows[0].keys()))
        writer.writeheader()
        writer.writerows(summary_rows)

    summary_json.write_text(
        json.dumps(
            {
                "config": vars(args),
                "summary": summary_rows,
            },
            indent=2,
        )
    )
    print(timeline_csv)
    print(detail_csv)
    print(summary_csv)
    print(summary_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
