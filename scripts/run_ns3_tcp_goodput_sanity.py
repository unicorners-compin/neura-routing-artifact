#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import subprocess

REPO_ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, cwd=REPO_ROOT, check=True, text=True, capture_output=True)
    return proc.stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser(description="Run small TCP goodput ns-3 sanity check.")
    ap.add_argument("--issue", type=int, default=4)
    ap.add_argument("--methods", default="lift,triggered_te,ospf_te")
    ap.add_argument("--link-rate-mbps", type=float, default=10.0)
    ap.add_argument("--queue-packets", type=int, default=20)
    ap.add_argument("--sample-ms", type=float, default=100.0)
    ap.add_argument("--sim-seconds", type=float, default=12.0)
    ap.add_argument("--background-rate-mbps", type=float, default=8.0)
    args = ap.parse_args()

    methods = [m.strip() for m in args.methods.split(",") if m.strip()]
    tag = (
        f"ns3_tcp_goodput_sanity_rate{args.link_rate_mbps:g}_q{args.queue_packets}"
        f"_sample{args.sample_ms:g}_bg{args.background_rate_mbps:g}"
    ).replace(".", "p")
    out_dir = REPO_ROOT / f"results/issue-{args.issue}/artifacts/{tag}"
    out_dir.mkdir(parents=True, exist_ok=True)

    binary_path = Path(run(["bash", "scripts/build_ns3_tcp_goodput.sh"]).splitlines()[-1])
    rows: list[dict[str, object]] = []
    for method in methods:
        summary_json = out_dir / f"{method}_summary.json"
        timeline_csv = out_dir / f"{method}_timeline.csv"
        cwnd_csv = out_dir / f"{method}_cwnd.csv"
        cmd = [
            str(binary_path),
            f"--method={method}",
            f"--summaryJson={summary_json}",
            f"--timelineCsv={timeline_csv}",
            f"--cwndCsv={cwnd_csv}",
            f"--linkRateMbps={args.link_rate_mbps}",
            f"--queuePackets={args.queue_packets}",
            f"--sampleMs={args.sample_ms}",
            f"--simSeconds={args.sim_seconds}",
            f"--backgroundRateMbps={args.background_rate_mbps}",
        ]
        subprocess.run(cmd, cwd=REPO_ROOT, check=True)
        payload = json.loads(summary_json.read_text(encoding="utf-8"))
        rows.append(
            {
                "method": method,
                "link_rate_mbps": args.link_rate_mbps,
                "queue_packets": args.queue_packets,
                "sample_ms": args.sample_ms,
                "sim_seconds": args.sim_seconds,
                "background_rate_mbps": args.background_rate_mbps,
                "primary_overall_goodput_mbps": payload["primary_overall_goodput_mbps"],
                "primary_burst_goodput_mbps": payload["primary_burst_goodput_mbps"],
                "primary_mean_delay_ms": payload["primary_mean_delay_ms"],
                "route_changes": payload["route_changes"],
                "tail_switches": payload["tail_switches"],
                "tcp_cwnd_drop_events": payload["tcp_cwnd_drop_events"],
                "tcp_cwnd_min_bytes": payload["tcp_cwnd_min_bytes"],
                "tcp_cwnd_max_bytes": payload["tcp_cwnd_max_bytes"],
                "final_selected_path": payload["final_selected_path"],
            }
        )

    summary_csv = out_dir / "summary.csv"
    with summary_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(summary_csv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
