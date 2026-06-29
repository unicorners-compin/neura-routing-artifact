#!/usr/bin/env python3
"""Audit NEURA manuscript result claims against issue-4 artifacts.

The script intentionally checks the rounded values that appear in the
current paper draft.  It is a lightweight guard against figure/table drift:
if a source CSV changes, the audit table points to the affected claim.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ART = REPO / "results" / "issue-4" / "artifacts"
OUT_CSV = ART / "neura_result_audit.csv"
OUT_MD = ART / "neura_result_audit.md"


@dataclass
class Check:
    claim: str
    location: str
    source: str
    metric: str
    actual: float
    expected: float
    tolerance: float
    unit: str = ""

    @property
    def status(self) -> str:
        return "PASS" if abs(self.actual - self.expected) <= self.tolerance else "FAIL"


def rows(relpath: str) -> list[dict[str, str]]:
    with (ART / relpath).open(newline="") as fh:
        return list(csv.DictReader(fh))


def row_by(relpath: str, **keys: object) -> dict[str, str]:
    for row in rows(relpath):
        if all(row[key] == str(value) for key, value in keys.items()):
            return row
    joined = ", ".join(f"{key}={value}" for key, value in keys.items())
    raise KeyError(f"{relpath}: {joined}")


def ns3_row(relpath: str, method: str) -> dict[str, str]:
    with (ART / relpath).open(newline="") as fh:
        for row in csv.DictReader(fh):
            if row["method"] == method:
                return row
    raise KeyError(f"{relpath}: method={method}")


def mean(values: list[float]) -> float:
    return sum(values) / len(values)


def add(checks: list[Check], *items: Check) -> None:
    checks.extend(items)


def shock_checks(checks: list[Check]) -> None:
    data = rows("shock_response_matrix_er_n100_s5_summary.csv")
    calm_ticks = range(30, 40)
    active = [r for r in data if r["burst_active"] == "1"]

    calm_expect = {
        "snn_sra": ("NEURA calm control", 1.1, 0.15),
        "triggered_te": ("Triggered-TE calm control", 311.0, 2.0),
        "ospf_te": ("OSPF-TE calm control", 378.0, 2.0),
        "bandit": ("Bandit calm control", 626.0, 3.0),
    }
    for method, (claim, expected, tol) in calm_expect.items():
        vals = [
            float(r["control_bytes_mean"]) / 1000.0
            for r in data
            if r["method"] == method and int(r["tick"]) in calm_ticks
        ]
        checks.append(
            Check(
                claim,
                "neura.tex shock-response text",
                "shock_response_matrix_er_n100_s5_summary.csv",
                f"{method} mean control, 300-390 ms",
                mean(vals),
                expected,
                tol,
                "KB / 10 ms",
            )
        )

    point_expect = [
        ("NEURA delivery at shock onset", "snn_sra", 40, "delivery_ratio_mean", 100.0, 68.5, 0.15, "%"),
        ("NEURA delivery at 450 ms", "snn_sra", 45, "delivery_ratio_mean", 100.0, 96.7, 0.15, "%"),
        ("NEURA control at 450 ms", "snn_sra", 45, "control_bytes_mean", 1 / 1000.0, 79.0, 1.0, "KB"),
        ("Triggered-TE delivery at shock onset", "triggered_te", 40, "delivery_ratio_mean", 100.0, 89.7, 0.15, "%"),
        ("Triggered-TE delivery at 450 ms", "triggered_te", 45, "delivery_ratio_mean", 100.0, 95.5, 0.15, "%"),
        ("Triggered-TE control at 450 ms", "triggered_te", 45, "control_bytes_mean", 1 / 1000.0, 319.0, 2.0, "KB"),
    ]
    for claim, method, tick, field, scale, expected, tol, unit in point_expect:
        row = next(r for r in data if r["method"] == method and int(r["tick"]) == tick)
        checks.append(
            Check(
                claim,
                "neura.tex shock-response text",
                "shock_response_matrix_er_n100_s5_summary.csv",
                f"{method} {field}, tick {tick}",
                float(row[field]) * scale,
                expected,
                tol,
                unit,
            )
        )

    churn_expect = {
        "snn_sra": ("NEURA burst route changes", 22.0, 0.5),
        "triggered_te": ("Triggered-TE burst route changes", 12.0, 0.5),
        "ospf_te": ("OSPF-TE burst route changes", 108.0, 1.0),
        "bandit": ("Bandit burst route changes", 3779.0, 5.0),
    }
    for method, (claim, expected, tol) in churn_expect.items():
        vals = [float(r["route_changes_mean"]) for r in active if r["method"] == method]
        checks.append(
            Check(
                claim,
                "neura.tex shock-response text",
                "shock_response_matrix_er_n100_s5_summary.csv",
                f"{method} mean route changes during burst",
                mean(vals),
                expected,
                tol,
                "changes / 10 ms",
            )
        )


def locality_checks(checks: list[Check]) -> None:
    dist = rows("activation_locality_matrix_er_n100_s5_distance_profile.csv")
    for method, prefix, expected in [
        ("snn_baseline", "NEURA ER", {0: 11.7, 1: 88.3}),
        ("triggered_te", "Triggered-TE ER", {0: 1.5, 1: 15.9, 2: 49.1, 3: 33.5}),
        ("ospf_te_t5", "OSPF-TE ER", {0: 1.1, 1: 12.9, 2: 48.0, 3: 38.0}),
    ]:
        totals = {}
        seeds = sorted({r["seed"] for r in dist if r["method"] == method})
        for distance in [0, 1, 2, 3]:
            vals = []
            for seed in seeds:
                share = sum(
                    float(r["emit_share"])
                    for r in dist
                    if r["method"] == method
                    and r["seed"] == seed
                    and (int(r["distance"]) == distance if distance < 3 else int(r["distance"]) >= 3)
                )
                vals.append(100.0 * share)
            totals[distance] = mean(vals)
        for distance, expected_share in expected.items():
            label = f"{distance}-hop" if distance < 3 else "3+-hop"
            checks.append(
                Check(
                    f"{prefix} {label} update share",
                    "neura.tex blast-radius text",
                    "activation_locality_matrix_er_n100_s5_distance_profile.csv",
                    method,
                    totals[distance],
                    expected_share,
                    0.15,
                    "%",
                )
            )

    er_summary = rows("activation_locality_matrix_er_n100_s5_summary.csv")
    for method, label, expected_total in [
        ("snn_baseline", "NEURA ER hotspot emitted updates", 44.6),
        ("triggered_te", "Triggered-TE ER hotspot emitted updates", 392.0),
        ("ospf_te_t5", "OSPF-TE ER hotspot emitted updates", 420.0),
    ]:
        row = next(r for r in er_summary if r["method"] == method)
        checks.append(
            Check(
                label,
                "neura.tex blast-radius text",
                "activation_locality_matrix_er_n100_s5_summary.csv",
                f"{method} total_emit_events_under_hotspot",
                float(row["total_emit_events_under_hotspot"]),
                expected_total,
                0.05,
                "updates",
            )
        )

    rgg = row_by("activation_locality_matrix_rgg_n100_s5_summary.csv", method="snn_full")
    ospf = row_by("activation_locality_matrix_rgg_n100_s5_summary.csv", method="ospf_te_t5")
    add(
        checks,
        Check("NEURA RGG within-two-hop share", "neura.tex blast-radius text", "activation_locality_matrix_rgg_n100_s5_summary.csv", "snn_full near_emit_share_h2", 100 * float(rgg["near_emit_share_h2"]), 100.0, 0.05, "%"),
        Check("OSPF-TE RGG within-two-hop share", "neura.tex blast-radius text", "activation_locality_matrix_rgg_n100_s5_summary.csv", "ospf_te_t5 near_emit_share_h2", 100 * float(ospf["near_emit_share_h2"]), 27.6, 0.15, "%"),
        Check("NEURA RGG weighted emit distance", "results README", "activation_locality_matrix_rgg_n100_s5_summary.csv", "snn_full weighted_mean_emit_distance", float(rgg["weighted_mean_emit_distance"]), 0.91, 0.01, "hops"),
        Check("OSPF-TE RGG weighted emit distance", "results README", "activation_locality_matrix_rgg_n100_s5_summary.csv", "ospf_te_t5 weighted_mean_emit_distance", float(ospf["weighted_mean_emit_distance"]), 3.97, 0.01, "hops"),
    )


def stress_checks(checks: list[Check]) -> None:
    expectations = [
        ("snn_sra", 3.0, 93.6, 9.51, 100.0),
        ("triggered_te", 3.0, 97.4, 54.2, 118.0),
        ("ospf_te", 3.0, 97.3, 59.6, 172.0),
        ("te_ecmp", 3.0, 98.2, 115.2, 242.0),
        ("bandit", 3.0, 97.9, 99.6, 6380.0),
        ("snn_sra", 5.0, 85.8, 10.6, None),
        ("triggered_te", 5.0, 87.2, 56.7, None),
        ("ospf_te", 5.0, 86.9, 59.6, None),
    ]
    for method, mult, delivery, control, churn in expectations:
        row = row_by("stress_sweep_matrix_er_n100_s5_summary.csv", method=method, burst_multiplier=mult)
        loc = "neura.tex stress-tradeoff text"
        add(
            checks,
            Check(f"{method} {mult:g}x delivery", loc, "stress_sweep_matrix_er_n100_s5_summary.csv", "delivery_mean", 100 * float(row["delivery_mean"]), delivery, 0.08, "%"),
            Check(f"{method} {mult:g}x control", loc, "stress_sweep_matrix_er_n100_s5_summary.csv", "control_bytes_mean", float(row["control_bytes_mean"]) / 1_000_000, control, 0.08, "MB"),
        )
        if churn is not None:
            checks.append(Check(f"{method} {mult:g}x route changes per node", loc, "stress_sweep_matrix_er_n100_s5_summary.csv", "route_changes_per_node_mean", float(row["route_changes_per_node_mean"]), churn, 8.0, "changes / node"))

    for method, delivery, control in [("snn_sra", 51.9, 14.86), ("triggered_te", 50.5, 75.66), ("ospf_te", 55.5, 72.97)]:
        row = row_by("stress_sweep_matrix_rgg_n100_s5_summary.csv", method=method, burst_multiplier=3.0)
        add(
            checks,
            Check(f"{method} RGG 3x delivery", "neura.tex spatial robustness text", "stress_sweep_matrix_rgg_n100_s5_summary.csv", "delivery_mean", 100 * float(row["delivery_mean"]), delivery, 0.08, "%"),
            Check(f"{method} RGG 3x control", "neura.tex spatial robustness text", "stress_sweep_matrix_rgg_n100_s5_summary.csv", "control_bytes_mean", float(row["control_bytes_mean"]) / 1_000_000, control, 0.02, "MB"),
        )


def chaos_checks(checks: list[Check]) -> None:
    expected = {
        "snn_sra": (9.14, 100.0, 7.4, 96.0),
        "ospf_te": (52.09, 210.0, 7.2, 96.6),
        "triggered_te": (48.43, 122.0, 6.8, 96.4),
        "te_ecmp": (99.85, 282.0, 6.4, 98.1),
        "bandit": (87.01, 5645.0, 4.0, 98.1),
    }
    for method, (control, churn, loss, delivery) in expected.items():
        row = row_by("continuous_chaos_matrix_er_n100_s5_summary.csv", method=method)
        loc = "neura.tex repeated-disturbance text"
        add(
            checks,
            Check(f"{method} chaos total control", loc, "continuous_chaos_matrix_er_n100_s5_summary.csv", "total_control_bytes_mean", float(row["total_control_bytes_mean"]) / 1_000_000, control, 0.02, "MB"),
            Check(f"{method} chaos route changes", loc, "continuous_chaos_matrix_er_n100_s5_summary.csv", "route_changes_per_node_mean", float(row["route_changes_per_node_mean"]), churn, 2.0, "changes / node"),
            Check(f"{method} chaos service-loss ticks", loc, "continuous_chaos_matrix_er_n100_s5_summary.csv", "service_loss_ticks_mean", float(row["service_loss_ticks_mean"]), loss, 0.05, "ticks"),
            Check(f"{method} chaos burst delivery", loc, "continuous_chaos_matrix_er_n100_s5_summary.csv", "burst_delivery_mean", 100 * float(row["burst_delivery_mean"]), delivery, 0.15, "%"),
        )

    for method, control, churn in [("snn_sra", 17.63, 209.0), ("ospf_te", 63.46, 773.0)]:
        row = row_by("continuous_chaos_matrix_rgg_n100_s5_summary.csv", method=method)
        add(
            checks,
            Check(f"{method} RGG chaos control", "neura.tex robustness text", "continuous_chaos_matrix_rgg_n100_s5_summary.csv", "total_control_bytes_mean", float(row["total_control_bytes_mean"]) / 1_000_000, control, 0.02, "MB"),
            Check(f"{method} RGG chaos churn", "neura.tex robustness text", "continuous_chaos_matrix_rgg_n100_s5_summary.csv", "route_changes_per_node_mean", float(row["route_changes_per_node_mean"]), churn, 1.0, "changes / node"),
        )


def supplemental_checks(checks: list[Check]) -> None:
    startup_expected = {"snn_sra": 40.0, "ospf_te": 88.0, "triggered_te": 40.0, "bandit": 62.0}
    safety = rows("safety_indicator_matrix_er_n100_s5_summary.csv")
    for method, expected in startup_expected.items():
        row = next(r for r in safety if r["scenario"] == "hotspot_3x" and r["method"] == method)
        checks.append(Check(f"{method} startup convergence", "sm.tex startup text", "safety_indicator_matrix_er_n100_s5_summary.csv", "startup_full_reachability_ms_mean", float(row["startup_full_reachability_ms_mean"]), expected, 0.05, "ms"))

    startup_mb = {"snn_sra": 1.76, "ospf_te": 2.73, "triggered_te": 3.36, "bandit": 3.22}
    shock_detail = rows("shock_response_matrix_er_n100_s5_detail.csv")
    for method, expected in startup_mb.items():
        vals = [float(r["startup_control_bytes_before_full_reachability"]) / 1_000_000 for r in shock_detail if r["method"] == method]
        checks.append(Check(f"{method} startup control", "sm.tex startup text", "shock_response_matrix_er_n100_s5_detail.csv", "startup_control_bytes_before_full_reachability", mean(vals), expected, 0.02, "MB"))

    stretch_expected = {"snn_sra": 1.048, "ospf_te": 1.007, "triggered_te": 1.031, "bandit": 1.002}
    for method, expected in stretch_expected.items():
        row = row_by("stress_sweep_matrix_er_n100_s5_summary.csv", method=method, burst_multiplier=3.0)
        checks.append(Check(f"{method} path stretch at 3x", "sm.tex startup/stretch text", "stress_sweep_matrix_er_n100_s5_summary.csv", "path_stretch_mean", float(row["path_stretch_mean"]), expected, 0.001, "stretch"))

    gray_expected = {
        "snn_sra": (92.5, 20.3, 6.39),
        "triggered_te": (96.4, 329.3, 41.68),
        "ospf_te": (97.3, 378.3, 44.53),
        "bandit": (97.1, 630.7, 74.43),
    }
    for method, (delivery, epoch_kb, total_mb) in gray_expected.items():
        row = row_by("gray_failure_matrix_er_n100_s5_summary.csv", method=method)
        add(
            checks,
            Check(f"{method} gray delivery", "sm.tex gray-failure table", "gray_failure_matrix_er_n100_s5_summary.csv", "impairment_delivery_mean", 100 * float(row["impairment_delivery_mean"]), delivery, 0.08, "%"),
            Check(f"{method} gray epoch control", "sm.tex gray-failure table", "gray_failure_matrix_er_n100_s5_summary.csv", "impairment_control_bytes_mean", float(row["impairment_control_bytes_mean"]) / 1000, epoch_kb, 0.08, "KB / epoch"),
            Check(f"{method} gray total control", "sm.tex gray-failure table", "gray_failure_matrix_er_n100_s5_summary.csv", "total_control_bytes_mean", float(row["total_control_bytes_mean"]) / 1_000_000, total_mb, 0.02, "MB"),
        )

    safety_expected = [
        ("hotspot_3x", "snn_sra", 99.87, 0.13, 0.00, 100.0, 100.0),
        ("continuous_chaos", "snn_sra", 99.87, 0.13, 0.00, 100.0, 100.0),
        ("gray_failure", "snn_sra", 99.94, 0.06, 0.00, 100.0, 100.0),
    ]
    for scenario, method, reach, loop, blackhole, final_reach, complete in safety_expected:
        row = next(r for r in safety if r["scenario"] == scenario and r["method"] == method)
        add(
            checks,
            Check(f"{method} {scenario} worst reachability", "sm.tex safety table", "safety_indicator_matrix_er_n100_s5_summary.csv", "post_startup_min_reachability_worst", 100 * float(row["post_startup_min_reachability_worst"]), reach, 0.01, "%"),
            Check(f"{method} {scenario} worst loop ratio", "sm.tex safety table", "safety_indicator_matrix_er_n100_s5_summary.csv", "post_startup_max_loop_ratio_worst", 100 * float(row["post_startup_max_loop_ratio_worst"]), loop, 0.01, "%"),
            Check(f"{method} {scenario} worst blackhole ratio", "sm.tex safety table", "safety_indicator_matrix_er_n100_s5_summary.csv", "post_startup_max_blackhole_ratio_worst", 100 * float(row["post_startup_max_blackhole_ratio_worst"]), blackhole, 0.01, "%"),
            Check(f"{method} {scenario} final reachability", "sm.tex safety table", "safety_indicator_matrix_er_n100_s5_summary.csv", "final_pair_reachability_mean", 100 * float(row["final_pair_reachability_mean"]), final_reach, 0.01, "%"),
            Check(f"{method} {scenario} final complete routes", "sm.tex safety table", "safety_indicator_matrix_er_n100_s5_summary.csv", "final_node_complete_route_ratio_mean", 100 * float(row["final_node_complete_route_ratio_mean"]), complete, 0.01, "%"),
        )


def ablation_checks(checks: list[Check]) -> None:
    rebound_expected = {
        "baseline": (1.57, 21.0, 208.4),
        "memory_only": (1.40, 0.0, 180.8),
        "full": (0.28, 0.0, 165.5),
    }
    for variant, (rebound, post, updates) in rebound_expected.items():
        row = row_by("memory_rebound_matrix_er_n100_s5_summary.csv", section="variant_mean", variant=variant)
        add(
            checks,
            Check(f"{variant} rebound ratio", "tab6_neura_ablation.tex", "memory_rebound_matrix_er_n100_s5_summary.csv", "rebound_ratio_after_release", 100 * float(row["rebound_ratio_after_release"]), rebound, 0.01, "%"),
            Check(f"{variant} post-stage-2 changes", "tab6_neura_ablation.tex", "memory_rebound_matrix_er_n100_s5_summary.csv", "post_stage2_route_changes", float(row["post_stage2_route_changes"]), post, 0.05, "changes"),
            Check(f"{variant} emitted updates", "tab6_neura_ablation.tex", "memory_rebound_matrix_er_n100_s5_summary.csv", "emitted_updates", float(row["emitted_updates"]) / 1000, updates, 0.05, "k"),
        )

    chaos_expected = {
        "full": (96.0, 100.1, 29.7, 7.4),
        "no_memory": (96.0, 100.0, 29.7, 7.4),
        "no_inhibition": (96.7, 125.9, 41.6, 8.0),
        "no_slow": (96.0, 100.1, 29.7, 7.4),
    }
    for variant, (delivery, churn, peak, loss) in chaos_expected.items():
        row = row_by("neura_ablation_matrix_er_n100_s5_summary.csv", scenario="chaos", variant=variant)
        add(
            checks,
            Check(f"{variant} ablation burst delivery", "tab6_neura_ablation.tex", "neura_ablation_matrix_er_n100_s5_summary.csv", "delivery_ratio_mean", 100 * float(row["delivery_ratio_mean"]), delivery, 0.05, "%"),
            Check(f"{variant} ablation route changes", "tab6_neura_ablation.tex", "neura_ablation_matrix_er_n100_s5_summary.csv", "route_changes_per_node_mean", float(row["route_changes_per_node_mean"]), churn, 0.1, "changes / node"),
            Check(f"{variant} ablation peak event rate", "tab6_neura_ablation.tex", "neura_ablation_matrix_er_n100_s5_summary.csv", "peak_event_rate_mean", float(row["peak_event_rate_mean"]) / 1000, peak, 0.1, "10^3 / tick"),
            Check(f"{variant} ablation loss ticks", "tab6_neura_ablation.tex", "neura_ablation_matrix_er_n100_s5_summary.csv", "service_loss_ticks_mean", float(row["service_loss_ticks_mean"]), loss, 0.05, "ticks"),
        )


def ns3_checks(checks: list[Check]) -> None:
    replay = {
        "snn_sra": ("NEURA", 0.35, 99.70, 0.35, 99.25),
        "triggered_te": ("Triggered-TE", 1.63, 99.96, 1.63, 99.89),
        "ospf_te": ("OSPF-TE", 1.65, 99.81, 1.65, 99.53),
    }
    for method, (label, h_ctrl, h_del, r_ctrl, r_del) in replay.items():
        hotspot = ns3_row("ns3_replay_hotspot_er_n24_seed51_u2_tick100/ns3_replay_rate10_q20_summary.csv", method)
        repeated = ns3_row("ns3_replay_repeated_er_n24_seed51_u2_tick100/ns3_replay_rate10_q20_summary.csv", method)
        add(
            checks,
            Check(f"{label} ns-3 hotspot control", "tab7_ns3_validation.tex", "ns3 replay hotspot summary", "ns3_control_tx_bytes", float(hotspot["ns3_control_tx_bytes"]) / 1_000_000, h_ctrl, 0.01, "MB"),
            Check(f"{label} ns-3 hotspot delivery", "tab7_ns3_validation.tex", "ns3 replay hotspot summary", "ns3_data_delivery_ratio", 100 * float(hotspot["ns3_data_delivery_ratio"]), h_del, 0.01, "%"),
            Check(f"{label} ns-3 repeated control", "tab7_ns3_validation.tex", "ns3 replay repeated summary", "ns3_control_tx_bytes", float(repeated["ns3_control_tx_bytes"]) / 1_000_000, r_ctrl, 0.01, "MB"),
            Check(f"{label} ns-3 repeated delivery", "tab7_ns3_validation.tex", "ns3 replay repeated summary", "ns3_data_delivery_ratio", 100 * float(repeated["ns3_data_delivery_ratio"]), r_del, 0.01, "%"),
        )

    queue_expected = {"lift": (1, 0, 100.0), "triggered_te": (4, 2, 100.0), "ospf_te": (12, 0, 100.0)}
    for method, (changes, tails, delivery) in queue_expected.items():
        row = ns3_row("ns3_queue_signal_sanity_rate10_q20_sample100_pri4_bg8/summary.csv", method)
        add(
            checks,
            Check(f"{method} queue route changes", "tab7_ns3_validation.tex", "ns3 queue-signal summary", "route_changes", float(row["route_changes"]), changes, 0.01, "changes"),
            Check(f"{method} queue tail switches", "tab7_ns3_validation.tex", "ns3 queue-signal summary", "tail_switches", float(row["tail_switches"]), tails, 0.01, "switches"),
            Check(f"{method} queue delivery", "tab7_ns3_validation.tex", "ns3 queue-signal summary", "primary_delivery_ratio", 100 * float(row["primary_delivery_ratio"]), delivery, 0.01, "%"),
        )

    tcp_expected = {"lift": (6.89, 14, 7, 399), "triggered_te": (7.36, 42, 22, 454), "ospf_te": (6.77, 21, 9, 319)}
    for method, (goodput, changes, tails, drops) in tcp_expected.items():
        row = ns3_row("ns3_tcp_goodput_sanity_rate10_q20_sample100_bg8/summary.csv", method)
        add(
            checks,
            Check(f"{method} TCP burst goodput", "tab7_ns3_validation.tex", "ns3 TCP summary", "primary_burst_goodput_mbps", float(row["primary_burst_goodput_mbps"]), goodput, 0.01, "Mbps"),
            Check(f"{method} TCP route changes", "tab7_ns3_validation.tex", "ns3 TCP summary", "route_changes", float(row["route_changes"]), changes, 0.01, "changes"),
            Check(f"{method} TCP tail switches", "tab7_ns3_validation.tex", "ns3 TCP summary", "tail_switches", float(row["tail_switches"]), tails, 0.01, "switches"),
            Check(f"{method} TCP cwnd drops", "tab7_ns3_validation.tex", "ns3 TCP summary", "tcp_cwnd_drop_events", float(row["tcp_cwnd_drop_events"]), drops, 0.01, "events"),
        )

    online_expected = {
        ("hotspot", "neura"): (97.9, 0.003, 10.6, 0.6),
        ("hotspot", "triggered_te"): (98.3, 0.007, 21.6, 11.6),
        ("hotspot", "ospf_te"): (99.9, 0.885, 29.2, 12.0),
        ("repeated", "neura"): (95.7, 0.004, 12.0, 1.4),
        ("repeated", "triggered_te"): (96.6, 0.014, 43.2, 23.2),
        ("repeated", "ospf_te"): (99.8, 0.895, 60.8, 25.2),
    }
    online_rows = rows("ns3_online_er_validation_n24_s5_f10_rate10_q20/summary.csv")
    for (scenario, method), (delivery, control, changes, tails) in online_expected.items():
        row = next(r for r in online_rows if r["scenario"] == scenario and r["method"] == method)
        add(
            checks,
            Check(f"{method} online ER {scenario} delivery", "tab8_ns3_online_er_validation.tex", "ns3 online ER summary", "delivery_mean", 100 * float(row["delivery_mean"]), delivery, 0.05, "%"),
            Check(f"{method} online ER {scenario} control", "tab8_ns3_online_er_validation.tex", "ns3 online ER summary", "ns3_control_tx_mb_mean", float(row["ns3_control_tx_mb_mean"]), control, 0.0006, "MB"),
            Check(f"{method} online ER {scenario} route changes", "tab8_ns3_online_er_validation.tex", "ns3 online ER summary", "route_changes_mean", float(row["route_changes_mean"]), changes, 0.05, "changes"),
            Check(f"{method} online ER {scenario} tail switches", "tab8_ns3_online_er_validation.tex", "ns3 online ER summary", "tail_switches_mean", float(row["tail_switches_mean"]), tails, 0.05, "switches"),
        )


def sensitivity_summary(checks: list[Check]) -> None:
    param = rows("snn_param_sensitivity_er_n100_s5_summary.csv")
    eng = rows("snn_engineering_sensitivity_er_n100_s5_summary.csv")
    checks.append(Check("parameter sweep row count", "sm.tex sensitivity table", "snn_param_sensitivity_er_n100_s5_summary.csv", "rows", float(len(param)), 12.0, 0.01, "rows"))
    checks.append(Check("engineering sweep row count", "sm.tex sensitivity table", "snn_engineering_sensitivity_er_n100_s5_summary.csv", "rows", float(len(eng)), 18.0, 0.01, "rows"))

    threshold = [r for r in param if r["parameter"] == "threshold"]
    threshold_delivery = [100 * float(r["burst_delivery_ratio_mean"]) for r in threshold]
    checks.append(
        Check(
            "threshold sweep delivery range",
            "sm.tex sensitivity summary",
            "snn_param_sensitivity_er_n100_s5_summary.csv",
            "max-min burst_delivery_ratio_mean",
            max(threshold_delivery) - min(threshold_delivery),
            1.22,
            0.05,
            "percentage points",
        )
    )

    guard = [r for r in eng if r["parameter"] == "switch_guard"]
    guard_control = [float(r["chaos_total_control_mb_mean"]) for r in guard]
    checks.append(
        Check(
            "switch-guard control range",
            "sm.tex sensitivity summary",
            "snn_engineering_sensitivity_er_n100_s5_summary.csv",
            "max-min chaos_total_control_mb_mean",
            max(guard_control) - min(guard_control),
            0.17,
            0.02,
            "MB",
        )
    )


def write_outputs(checks: list[Check]) -> None:
    fields = ["status", "claim", "location", "source", "metric", "actual", "expected", "tolerance", "unit"]
    with OUT_CSV.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for c in checks:
            writer.writerow(
                {
                    "status": c.status,
                    "claim": c.claim,
                    "location": c.location,
                    "source": c.source,
                    "metric": c.metric,
                    "actual": f"{c.actual:.6g}",
                    "expected": f"{c.expected:.6g}",
                    "tolerance": f"{c.tolerance:.6g}",
                    "unit": c.unit,
                }
            )

    failures = [c for c in checks if c.status != "PASS"]
    lines = [
        "# NEURA result audit",
        "",
        f"- Checks: {len(checks)}",
        f"- Passed: {len(checks) - len(failures)}",
        f"- Failed: {len(failures)}",
        "",
    ]
    if failures:
        lines += ["## Failures", ""]
        for c in failures:
            lines.append(
                f"- {c.claim}: actual `{c.actual:.6g}` vs expected `{c.expected:.6g}` "
                f"(tol `{c.tolerance:.6g}` {c.unit}) from `{c.source}`"
            )
        lines.append("")
    lines += [
        "## Scope",
        "",
        "The audit covers the rounded manuscript values for the main ER-100 result matrix, "
        "RGG robustness checks, gray failure, safety indicators, mechanism attribution, "
        "sensitivity summaries, and ns-3 validation tables including online ER validation.",
        "",
        f"Machine-readable details are in `{OUT_CSV.relative_to(REPO)}`.",
        "",
    ]
    OUT_MD.write_text("\n".join(lines))


def main() -> None:
    checks: list[Check] = []
    shock_checks(checks)
    locality_checks(checks)
    stress_checks(checks)
    chaos_checks(checks)
    supplemental_checks(checks)
    ablation_checks(checks)
    ns3_checks(checks)
    sensitivity_summary(checks)
    write_outputs(checks)
    failures = [c for c in checks if c.status != "PASS"]
    print(json.dumps({"checks": len(checks), "passed": len(checks) - len(failures), "failed": len(failures)}, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
