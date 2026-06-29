#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import statistics
import subprocess

REPO_ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, cwd=REPO_ROOT, check=True, text=True, capture_output=True)
    return proc.stdout.strip()


def fmt_tag(value: float | int) -> str:
    return f"{value:g}".replace(".", "p")


def mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def ci95(values: list[float]) -> float:
    if len(values) <= 1:
        return 0.0
    return 1.96 * statistics.stdev(values) / (len(values) ** 0.5)


def main() -> int:
    ap = argparse.ArgumentParser(description="Run medium-scale online ns-3 ER validation.")
    ap.add_argument("--issue", type=int, default=4)
    ap.add_argument("--methods", default="neura,triggered_te,ospf_te")
    ap.add_argument("--scenarios", default="hotspot,repeated")
    ap.add_argument("--seeds", default="51,52,53,54,55")
    ap.add_argument("--nodes", type=int, default=24)
    ap.add_argument("--target-flows", type=int, default=10)
    ap.add_argument("--edge-prob", type=float, default=0.18)
    ap.add_argument("--link-rate-mbps", type=float, default=10.0)
    ap.add_argument("--queue-packets", type=int, default=20)
    ap.add_argument("--sample-ms", type=float, default=100.0)
    ap.add_argument("--sim-seconds", type=float, default=12.0)
    ap.add_argument("--primary-rate-mbps", type=float, default=1.2)
    ap.add_argument("--background-rate-mbps", type=float, default=12.0)
    args = ap.parse_args()

    methods = [x.strip() for x in args.methods.split(",") if x.strip()]
    scenarios = [x.strip() for x in args.scenarios.split(",") if x.strip()]
    seeds = [int(x.strip()) for x in args.seeds.split(",") if x.strip()]
    tag = (
        f"ns3_online_er_validation_n{args.nodes}_s{len(seeds)}"
        f"_f{args.target_flows}_rate{fmt_tag(args.link_rate_mbps)}_q{args.queue_packets}"
    )
    out_dir = REPO_ROOT / f"results/issue-{args.issue}/artifacts/{tag}"
    out_dir.mkdir(parents=True, exist_ok=True)
    binary = Path(run(["bash", "scripts/build_ns3_online_er.sh"]).splitlines()[-1])

    detail_rows: list[dict[str, object]] = []
    for scenario in scenarios:
        for seed in seeds:
            for method in methods:
                summary_json = out_dir / f"{scenario}_{method}_seed{seed}_summary.json"
                timeline_csv = out_dir / f"{scenario}_{method}_seed{seed}_timeline.csv"
                cmd = [
                    str(binary),
                    f"--method={method}",
                    f"--scenario={scenario}",
                    f"--summaryJson={summary_json}",
                    f"--timelineCsv={timeline_csv}",
                    f"--nodes={args.nodes}",
                    f"--seed={seed}",
                    f"--targetFlows={args.target_flows}",
                    f"--edgeProb={args.edge_prob}",
                    f"--linkRateMbps={args.link_rate_mbps}",
                    f"--queuePackets={args.queue_packets}",
                    f"--sampleMs={args.sample_ms}",
                    f"--simSeconds={args.sim_seconds}",
                    f"--primaryRateMbps={args.primary_rate_mbps}",
                    f"--backgroundRateMbps={args.background_rate_mbps}",
                ]
                subprocess.run(cmd, cwd=REPO_ROOT, check=True)
                payload = json.loads(summary_json.read_text(encoding="utf-8"))
                detail_rows.append(payload)

    detail_csv = out_dir / "detail.csv"
    fieldnames = list(detail_rows[0].keys())
    with detail_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(detail_rows)

    summary_rows: list[dict[str, object]] = []
    for scenario in scenarios:
        for method in methods:
            rows = [r for r in detail_rows if r["scenario"] == scenario and r["method"] == method]
            summary_rows.append(
                {
                    "scenario": scenario,
                    "method": method,
                    "runs": len(rows),
                    "delivery_mean": mean([float(r["primary_delivery_ratio"]) for r in rows]),
                    "delivery_ci95": ci95([float(r["primary_delivery_ratio"]) for r in rows]),
                    "goodput_mbps_mean": mean([float(r["primary_goodput_mbps"]) for r in rows]),
                    "delay_ms_mean": mean([float(r["primary_mean_delay_ms"]) for r in rows]),
                    "route_changes_mean": mean([float(r["route_changes"]) for r in rows]),
                    "tail_switches_mean": mean([float(r["tail_switches"]) for r in rows]),
                    "logical_control_mb_mean": mean([float(r["logical_control_bytes"]) / 1_000_000.0 for r in rows]),
                    "ns3_control_tx_mb_mean": mean([float(r["ns3_control_tx_bytes"]) / 1_000_000.0 for r in rows]),
                    "flows_mean": mean([float(r["flows"]) for r in rows]),
                    "edges_mean": mean([float(r["edges"]) for r in rows]),
                }
            )

    summary_csv = out_dir / "summary.csv"
    with summary_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary_rows[0].keys()))
        writer.writeheader()
        writer.writerows(summary_rows)

    readme = out_dir / "README.md"
    readme.write_text(
        "\n".join(
            [
                "# ns-3 Online ER Validation",
                "",
                "Medium-scale packet-level online cross-validation for the NEURA paper.",
                "This is not a full distributed protocol-stack implementation; it is an online ns-3 validation where source host routes are updated from sampled DropTail queue pressure.",
                "",
                f"- nodes: {args.nodes}",
                f"- seeds: {', '.join(map(str, seeds))}",
                f"- target flows per run: {args.target_flows}",
                f"- methods: {', '.join(methods)}",
                f"- scenarios: {', '.join(scenarios)}",
                f"- link rate: {args.link_rate_mbps:g} Mbps",
                f"- queue: {args.queue_packets} packets",
                "",
                "Main outputs:",
                "",
                "- `summary.csv`: aggregate means and confidence intervals",
                "- `detail.csv`: per-method, per-scenario, per-seed run metrics",
                "- `*_summary.json`: raw per-run ns-3 metrics",
                "- `*_timeline.csv`: sampled controller timeline",
                "",
            ]
        )
    )
    print(summary_csv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
