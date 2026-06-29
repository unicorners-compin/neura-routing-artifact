#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, cwd=REPO_ROOT, check=True, text=True, capture_output=True)
    return proc.stdout.strip()


def format_tag(value: float | int) -> str:
    if isinstance(value, int):
        return str(value)
    return f"{value:g}".replace(".", "p")


def main() -> int:
    ap = argparse.ArgumentParser(description="Run minimal ns-3 replay validation for selected routing methods.")
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
    ap.add_argument("--tick-ms", type=int, default=100)
    ap.add_argument("--mbps-per-unit", type=float, default=1.0)
    ap.add_argument("--link-rate-mbps", type=float, default=10.0)
    ap.add_argument("--link-delay-ms", type=float, default=2.0)
    ap.add_argument("--queue-packets", type=int, default=100)
    args = ap.parse_args()

    methods = [part.strip() for part in args.methods.split(",") if part.strip()]

    export_cmd = [
        sys.executable,
        "scripts/export_ns3_link_replay.py",
        "--issue", str(args.issue),
        "--scenario", args.scenario,
        "--methods", ",".join(methods),
        "--topology", args.topology,
        "--nodes", str(args.nodes),
        "--edge-prob", str(args.edge_prob),
        "--ba-attach", str(args.ba_attach),
        "--geo-radius", str(args.geo_radius),
        "--ticks", str(args.ticks),
        "--seed", str(args.seed),
        "--tick-ms", str(args.tick_ms),
        "--mbps-per-unit", str(args.mbps_per_unit),
    ]
    manifest_path = Path(run(export_cmd).splitlines()[-1])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    binary_path = Path(run(["bash", "scripts/build_ns3_replay.sh"]).splitlines()[-1])
    out_dir = manifest_path.parent
    ns3_tag = f"rate{format_tag(args.link_rate_mbps)}_q{args.queue_packets}"
    summary_rows: list[dict[str, object]] = []

    for method in methods:
        method_info = manifest["methods"][method]
        events_csv = REPO_ROOT / method_info["events_csv"]
        summary_json = out_dir / f"{method}_ns3_{ns3_tag}_summary.json"
        cmd = [
            str(binary_path),
            f"--topologyCsv={REPO_ROOT / manifest['topology_csv']}",
            f"--eventsCsv={events_csv}",
            f"--summaryJson={summary_json}",
            f"--linkRateMbps={args.link_rate_mbps}",
            f"--linkDelayMs={args.link_delay_ms}",
            f"--tickMs={args.tick_ms}",
            f"--queuePackets={args.queue_packets}",
        ]
        subprocess.run(cmd, cwd=REPO_ROOT, check=True)
        ns3_summary = json.loads(summary_json.read_text(encoding="utf-8"))
        sim_summary = json.loads((REPO_ROOT / method_info["sim_summary_json"]).read_text(encoding="utf-8"))
        summary_rows.append(
            {
                "method": method,
                "scenario": args.scenario,
                "topology": args.topology,
                "nodes": args.nodes,
                "seed": args.seed,
                "mbps_per_unit": args.mbps_per_unit,
                "link_rate_mbps": args.link_rate_mbps,
                "queue_packets": args.queue_packets,
                "sim_control_bytes": method_info["totals"]["control_bytes"],
                "sim_data_bytes": method_info["totals"]["data_bytes"],
                "sim_delivery_ratio": sim_summary["final_metrics"].get("delivery_ratio"),
                "ns3_control_delivery_ratio": ns3_summary["control"]["delivery_ratio"],
                "ns3_data_delivery_ratio": ns3_summary["data"]["delivery_ratio"],
                "ns3_control_tx_bytes": ns3_summary["control"]["tx_bytes"],
                "ns3_control_rx_bytes": ns3_summary["control"]["rx_bytes"],
                "ns3_data_tx_bytes": ns3_summary["data"]["tx_bytes"],
                "ns3_data_rx_bytes": ns3_summary["data"]["rx_bytes"],
                "ns3_control_mean_delay_ms": ns3_summary["control"]["mean_delay_ms"],
                "ns3_data_mean_delay_ms": ns3_summary["data"]["mean_delay_ms"],
            }
        )

    summary_csv = out_dir / f"ns3_replay_{ns3_tag}_summary.csv"
    with summary_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary_rows[0].keys()))
        writer.writeheader()
        writer.writerows(summary_rows)

    print(summary_csv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
