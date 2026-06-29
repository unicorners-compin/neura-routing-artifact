#!/usr/bin/env python3
"""Generate IEEE-style pgfplots figures from redesigned issue-4 artifacts."""

from __future__ import annotations

import csv
import json
import math
import os
import re
from collections import defaultdict
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/mpl-neura-figures")

import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

REPO = Path(__file__).resolve().parents[1]
ART = REPO / "results" / "issue-4" / "artifacts"
FIG = REPO / "results" / "issue-4" / "figures"
PAPER_FIG = REPO / "paper" / "figures"
PUB_FIG = PAPER_FIG / "generated"

FIG.mkdir(parents=True, exist_ok=True)
PAPER_FIG.mkdir(parents=True, exist_ok=True)
PUB_FIG.mkdir(parents=True, exist_ok=True)

DISPLAY = {
    "snn_sra": "NEURA",
    "ospf_te": "OSPF-TE",
    "triggered_te": "Triggered-TE",
    "bandit": "Bandit",
    "te_ecmp": "TE+ECMP",
    "snn_baseline": "NEURA",
    "ospf_te_t5": "OSPF-TE",
    "full": "Full NEURA",
    "no_memory": "No memory",
    "no_inhibition": "No switch suppression",
    "no_slow": "No slow state",
    "baseline": "Minimal core",
    "memory_only": "Memory only",
}

COLOR = {
    "snn_sra": "SNNBlue",
    "ospf_te": "TEOrange",
    "triggered_te": "BaselineGray",
    "bandit": "BanditRed",
    "te_ecmp": "ECMPGreen",
    "snn_baseline": "SNNBlue",
    "ospf_te_t5": "TEOrange",
    "full": "FullTeal",
    "no_memory": "MemoryPurple",
    "no_inhibition": "BaselineGray",
    "no_slow": "TEOrange",
    "baseline": "BaselineGray",
    "memory_only": "MemoryPurple",
}

MARK = {
    "snn_sra": "*",
    "ospf_te": "square*",
    "triggered_te": "pentagon*",
    "bandit": "diamond*",
    "te_ecmp": "triangle*",
    "snn_baseline": "*",
    "ospf_te_t5": "square*",
    "full": "*",
    "no_memory": "diamond*",
    "no_inhibition": "square*",
    "no_slow": "triangle*",
    "baseline": "square*",
    "memory_only": "diamond*",
}

LINE = {
    "snn_sra": "solid",
    "ospf_te": "densely dashed",
    "triggered_te": "dash dot",
    "bandit": "dotted",
    "te_ecmp": "dash dot dot",
    "snn_baseline": "solid",
    "ospf_te_t5": "densely dashed",
    "full": "solid",
    "no_memory": "densely dashed",
    "no_inhibition": "dotted",
    "no_slow": "dash dot",
    "baseline": "dotted",
    "memory_only": "densely dashed",
}

MPL_COLOR = {
    "snn_sra": "#1f77b4",
    "ospf_te": "#ff7f0e",
    "triggered_te": "#7f7f7f",
    "bandit": "#d62728",
    "te_ecmp": "#2ca02c",
    "snn_baseline": "#1f77b4",
    "ospf_te_t5": "#ff7f0e",
    "full": "#178f92",
    "no_memory": "#9467bd",
    "no_inhibition": "#7f7f7f",
    "no_slow": "#ff7f0e",
    "baseline": "#7f7f7f",
    "memory_only": "#9467bd",
}

MPL_LINE = {
    "snn_sra": "-",
    "ospf_te": "--",
    "triggered_te": "-.",
    "bandit": ":",
    "te_ecmp": (0, (3, 2, 1, 2, 1, 2)),
    "snn_baseline": "-",
    "ospf_te_t5": "--",
    "full": "-",
    "no_memory": "--",
    "no_inhibition": ":",
    "no_slow": "-.",
    "baseline": ":",
    "memory_only": "--",
}

MPL_MARK = {
    "snn_sra": "o",
    "ospf_te": "s",
    "triggered_te": "D",
    "bandit": "P",
    "te_ecmp": "^",
}

DPI = 600


plt.rcParams.update(
    {
        "font.family": "serif",
        "font.serif": ["Times New Roman", "Times", "Nimbus Roman", "DejaVu Serif"],
        "font.size": 7.6,
        "axes.labelsize": 7.8,
        "axes.titlesize": 8.0,
        "xtick.labelsize": 7.0,
        "ytick.labelsize": 7.0,
        "legend.fontsize": 7.0,
        "figure.dpi": DPI,
        "savefig.dpi": DPI,
        "axes.linewidth": 0.7,
        "grid.linewidth": 0.45,
        "lines.linewidth": 1.15,
        "lines.markersize": 3.2,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
    }
)


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def sample_std(xs: list[float]) -> float:
    if len(xs) <= 1:
        return 0.0
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def ci95(xs: list[float]) -> float:
    if len(xs) <= 1:
        return 0.0
    return 1.96 * sample_std(xs) / math.sqrt(len(xs))


def fmt(value: float, precision: int = 3) -> str:
    return f"{value:.{precision}f}"


def nice_ymax(value: float, step: float) -> float:
    if value <= 0:
        return step
    return math.ceil(value / step) * step


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def style_axis(ax) -> None:
    ax.grid(True, axis="y", color="#d8d8d8", linewidth=0.45)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_color("#222222")
        spine.set_linewidth(0.7)
    ax.tick_params(direction="in", top=True, right=True, length=3.0, width=0.6, pad=2)


def thousands(x: float, _pos: int) -> str:
    return f"{int(x):,}"


def save_pubfig(fig, stem: str) -> None:
    pdf = PUB_FIG / f"{stem}.pdf"
    png = PUB_FIG / f"{stem}.png"
    fig.savefig(pdf, bbox_inches="tight", pad_inches=0.015)
    fig.savefig(png, dpi=DPI, bbox_inches="tight", pad_inches=0.015)
    plt.close(fig)


def write_include_wrapper(stem: str, env: str, width: str, caption: str, label: str) -> None:
    tex = rf"""
\begin{{{env}}}[t]
\centering
\includegraphics[width={width}]{{figures/generated/{stem}.pdf}}
\caption{{{caption}}}
\label{{{label}}}
\end{{{env}}}
"""
    (PAPER_FIG / f"{stem}.tex").write_text(tex.strip() + "\n")


def add_shared_legend(fig, axes, methods: list[str], ncol: int | None = None, y: float = 0.99) -> None:
    handles = []
    labels = []
    for method in methods:
        handle, = axes[0].plot(
            [],
            [],
            color=MPL_COLOR[method],
            linestyle=MPL_LINE[method],
            marker=MPL_MARK.get(method, None),
            linewidth=1.15,
            markersize=3.2,
        )
        handles.append(handle)
        labels.append(DISPLAY[method])
    fig.legend(
        handles,
        labels,
        loc="upper center",
        bbox_to_anchor=(0.5, y),
        ncol=ncol or len(methods),
        frameon=False,
        columnspacing=1.6,
        handlelength=2.6,
        handletextpad=0.45,
    )


def plot_fig1_shock_response(out_rows: list[dict[str, object]], timeline_methods: list[str], delivery_methods: list[str], start_ms: int, end_ms: int) -> None:
    fig, axes = plt.subplots(3, 1, figsize=(7.15, 3.05), sharex=True)
    x = [float(r["time_ms"]) for r in out_rows]
    panels = [
        ("control_kb", "Control\n(KB / 10 ms)", 0.0, None, timeline_methods),
        ("route_changes", "Route changes\n/ 10 ms", 0.0, None, timeline_methods),
        ("delivery_pct", "Delivery\n(%)", 60.0, 101.0, delivery_methods),
    ]
    for ax, (suffix, ylabel, ymin, ymax, methods) in zip(axes, panels):
        for method in methods:
            ax.plot(
                x,
                [float(r[f"{method}_{suffix}"]) for r in out_rows],
                color=MPL_COLOR[method],
                linestyle=MPL_LINE[method],
                linewidth=1.15,
            )
        ax.axvspan(start_ms, end_ms, color="#e9e9e9", zorder=0)
        ax.axvline(start_ms, color="#9a9a9a", linestyle="--", linewidth=0.75)
        ax.axvline(end_ms, color="#9a9a9a", linestyle="--", linewidth=0.75)
        ax.set_ylabel(ylabel)
        ax.set_ylim(bottom=ymin, top=ymax)
        style_axis(ax)
    axes[-1].set_xlabel("Time (ms)")
    axes[0].yaxis.set_major_formatter(FuncFormatter(thousands))
    axes[1].yaxis.set_major_formatter(FuncFormatter(thousands))
    add_shared_legend(fig, axes, delivery_methods, y=1.0)
    fig.subplots_adjust(left=0.075, right=0.995, bottom=0.12, top=0.88, hspace=0.18)
    save_pubfig(fig, "fig1_shock_response")
    write_include_wrapper(
        "fig1_shock_response",
        "figure*",
        r"\textwidth",
        "Shock response under localized stress. The shaded interval marks the burst window. NEURA stays quiet before the shock, reacts only inside the disturbance interval, and keeps route churn far below the engineering and learning baselines.",
        "fig:shock-response",
    )


def plot_fig2_blast_radius(out_rows: list[dict[str, object]], methods: list[str], buckets: list[str]) -> None:
    fig, ax = plt.subplots(figsize=(3.45, 2.05))
    x = list(range(len(buckets)))
    width = min(0.26, 0.72 / max(len(methods), 1))
    for idx, method in enumerate(methods):
        offset = (idx - (len(methods) - 1) / 2.0) * width
        ax.bar(
            [v + offset for v in x],
            [float(row[method]) for row in out_rows],
            width=width,
            color=MPL_COLOR[method],
            label=DISPLAY[method],
            edgecolor="none",
        )
    ax.set_ylabel("Share of hotspot-window updates (%)")
    ax.set_xticks(x, buckets)
    ax.set_ylim(0, 105)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, 1.16), ncol=len(methods), frameon=False, handlelength=1.0)
    style_axis(ax)
    fig.subplots_adjust(left=0.145, right=0.995, bottom=0.16, top=0.84)
    save_pubfig(fig, "fig2_blast_radius")
    write_include_wrapper(
        "fig2_blast_radius",
        "figure",
        r"\columnwidth",
        "Blast radius of control activity during a localized hotspot event. The bars report the mean update share generated at each hop-distance bucket from the stressed region. NEURA is compared with the nearest event-triggered engineering baseline and the periodic OSPF-TE baseline.",
        "fig:blast-radius",
    )


def plot_fig3_stress_tradeoff(out_rows: list[dict[str, object]], methods: list[str]) -> None:
    fig, axes = plt.subplots(2, 1, figsize=(3.45, 3.15), sharex=True)
    for method in methods:
        pts = sorted([r for r in out_rows if r["method"] == method], key=lambda r: float(r["burst_multiplier"]))
        x = [float(r["burst_multiplier"]) for r in pts]
        axes[0].plot(
            x,
            [float(r["delivery_pct"]) for r in pts],
            color=MPL_COLOR[method],
            linestyle=MPL_LINE[method],
            marker=MPL_MARK[method],
            linewidth=1.1,
            markersize=3.0,
            label=DISPLAY[method],
        )
        axes[1].plot(
            x,
            [float(r["control_mb"]) for r in pts],
            color=MPL_COLOR[method],
            linestyle=MPL_LINE[method],
            marker=MPL_MARK[method],
            linewidth=1.1,
            markersize=3.0,
        )
    axes[0].set_ylabel("Burst-window\ndelivery (%)")
    axes[0].set_ylim(84, 102)
    axes[1].set_ylabel("Control traffic\n(MB)")
    axes[1].set_xlabel("Hotspot demand multiplier")
    axes[1].set_ylim(bottom=0)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(
        handles,
        labels,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.995),
        ncol=2,
        frameon=False,
        columnspacing=1.0,
        handlelength=2.2,
    )
    for ax in axes:
        ax.set_xlim(1.8, 5.2)
        style_axis(ax)
    fig.subplots_adjust(left=0.17, right=0.995, bottom=0.13, top=0.74, hspace=0.24)
    save_pubfig(fig, "fig3_stress_tradeoff")
    write_include_wrapper(
        "fig3_stress_tradeoff",
        "figure",
        r"\columnwidth",
        "Mitigation cost under increasing stress. The upper panel reports retained service during the hotspot window. The lower panel reports the control traffic required over the same disturbance interval. NEURA accepts a modest delivery penalty while maintaining the lowest control-cost operating point across the stress sweep.",
        "fig:stress-tradeoff",
    )


def plot_fig4_startup_and_stretch(out_rows: list[dict[str, object]], methods: list[str], bar_key: dict[str, str]) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(7.15, 2.25))
    x = list(range(len(methods)))
    colors = [MPL_COLOR[m] for m in methods]
    labels = [bar_key[m] for m in methods]
    startup = [next(float(r["startup_ms_mean"]) for r in out_rows if r["method"] == m) for m in methods]
    startup_err = [next(float(r["startup_ms_ci95"]) for r in out_rows if r["method"] == m) for m in methods]
    stretch = [next(float(r["path_stretch_mean"]) for r in out_rows if r["method"] == m) for m in methods]
    stretch_err = [next(float(r["path_stretch_ci95"]) for r in out_rows if r["method"] == m) for m in methods]
    axes[0].bar(x, startup, yerr=startup_err, color=colors, edgecolor="none", capsize=2.0)
    axes[1].bar(x, stretch, yerr=stretch_err, color=colors, edgecolor="none", capsize=2.0)
    axes[0].set_title("(a) Startup convergence")
    axes[0].set_ylabel("Time to full reachability (ms)")
    axes[0].set_ylim(bottom=0)
    axes[1].set_title(r"(b) Path stretch at $3\times$ stress")
    axes[1].set_ylabel("Burst-window path stretch")
    axes[1].set_ylim(1.0, 1.08)
    for ax in axes:
        ax.set_xticks(x, labels)
        style_axis(ax)
    fig.subplots_adjust(left=0.075, right=0.995, bottom=0.18, top=0.86, wspace=0.28)
    save_pubfig(fig, "fig4_startup_and_stretch")
    write_include_wrapper(
        "fig4_startup_and_stretch",
        "figure*",
        r"\textwidth",
        r"Startup cost and data-plane path penalty. Panel (a) reports the time needed to establish full reachability from startup. Panel (b) reports mean burst-window path stretch at the representative $3\times$ hotspot workload. NEURA matches the fastest startup convergence and maintains bounded path inflation rather than achieving low control traffic by allowing arbitrarily poor routes.",
        "fig:startup-and-stretch",
    )


def plot_fig5_continuous_chaos(out_rows: list[dict[str, object]], summary_records: list[dict[str, object]], timeline_methods: list[str], summary_methods: list[str], windows: list[tuple[int, int]]) -> None:
    fig = plt.figure(figsize=(7.15, 3.35))
    gs = fig.add_gridspec(3, 2, height_ratios=[1.0, 1.0, 0.82], hspace=0.52, wspace=0.24)
    ax_control = fig.add_subplot(gs[0, :])
    ax_delivery = fig.add_subplot(gs[1, :], sharex=ax_control)
    ax_cost = fig.add_subplot(gs[2, 0])
    ax_churn = fig.add_subplot(gs[2, 1])
    x = [float(r["time_ms"]) for r in out_rows]
    for method in timeline_methods:
        ax_control.plot(x, [float(r[f"{method}_control_kb"]) for r in out_rows], color=MPL_COLOR[method], linestyle=MPL_LINE[method], linewidth=1.1)
        ax_delivery.plot(x, [float(r[f"{method}_delivery_pct"]) for r in out_rows], color=MPL_COLOR[method], linestyle=MPL_LINE[method], linewidth=1.1)
    for ax, ymin, ymax in [(ax_control, 0, None), (ax_delivery, 80, 101)]:
        for start_ms, end_ms in windows:
            ax.axvspan(start_ms, end_ms, color="#e9e9e9", zorder=0)
            ax.axvline(start_ms, color="#9a9a9a", linestyle="--", linewidth=0.75)
            ax.axvline(end_ms, color="#9a9a9a", linestyle="--", linewidth=0.75)
        ax.set_ylim(bottom=ymin, top=ymax)
        style_axis(ax)
    ax_control.set_ylabel("Control\n(KB / 10 ms)")
    ax_delivery.set_ylabel("Delivery\n(%)")
    ax_delivery.set_xlabel("Time (ms)")
    ax_control.yaxis.set_major_formatter(FuncFormatter(thousands))
    ax_control.tick_params(labelbottom=False)
    bar_labels = {"snn_sra": "NEURA", "ospf_te": "OSPF", "triggered_te": "Triggered", "te_ecmp": "ECMP", "bandit": "Bandit"}
    xbar = list(range(len(summary_methods)))
    record_by_method = {str(r["method"]): r for r in summary_records}
    ax_cost.bar(
        xbar,
        [float(record_by_method[m]["total_control_mb"]) for m in summary_methods],
        yerr=[float(record_by_method[m]["total_control_mb_ci95"]) for m in summary_methods],
        color=[MPL_COLOR[m] for m in summary_methods],
        edgecolor="none",
        capsize=2.0,
    )
    ax_churn.bar(
        xbar,
        [float(record_by_method[m]["route_changes_per_node_mean"]) for m in summary_methods],
        yerr=[float(record_by_method[m]["route_changes_per_node_ci95"]) for m in summary_methods],
        color=[MPL_COLOR[m] for m in summary_methods],
        edgecolor="none",
        capsize=2.0,
    )
    ax_cost.set_ylabel("Total control\ntraffic (MB)")
    ax_churn.set_ylabel("Route changes\nper node")
    ax_churn.yaxis.set_major_formatter(FuncFormatter(thousands))
    for ax in [ax_cost, ax_churn]:
        ax.set_xticks(xbar, [bar_labels[m] for m in summary_methods])
        style_axis(ax)
    add_shared_legend(fig, [ax_control], timeline_methods, y=0.995)
    fig.subplots_adjust(left=0.075, right=0.995, bottom=0.105, top=0.88)
    save_pubfig(fig, "fig5_continuous_chaos")
    write_include_wrapper(
        "fig5_continuous_chaos",
        "figure*",
        r"\textwidth",
        "Continuous-chaos behavior under three repeated hotspot shocks. The shaded intervals mark the disturbance windows. The upper panels show control traffic and delivery over time. The lower panels summarize cumulative control cost and route churn. NEURA keeps both totals far below the stronger baselines while preserving useful service.",
        "fig:continuous-chaos",
    )


def plot_fig6_neura_ablation(figure_rows: list[dict[str, object]], memory_variants: list[str], chaos_variants: list[str]) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(7.15, 3.25))
    memory_data = {str(r["variant"]): r for r in figure_rows if r["panel"] == "memory"}
    chaos_data = {str(r["variant"]): r for r in figure_rows if r["panel"] == "chaos"}
    panels = [
        (axes[0, 0], memory_data, memory_variants, "rebound_ratio_pct", "Rebound ratio\nafter release (%)"),
        (axes[0, 1], memory_data, memory_variants, "post_stage2_route_changes", "Post-stage-2\nroute changes"),
        (axes[1, 0], chaos_data, chaos_variants, "chaos_route_changes_per_node", "Chaos route\nchanges / node"),
        (axes[1, 1], chaos_data, chaos_variants, "chaos_peak_event_rate_k", r"Chaos peak event rate" + "\n" + r"($10^3$ msgs / tick)"),
    ]
    for ax, data, variants, key, ylabel in panels:
        x = list(range(len(variants)))
        ax.bar(x, [float(data[v][key]) for v in variants], color=[MPL_COLOR[v] for v in variants], edgecolor="none")
        ax.set_xticks(x, [DISPLAY[v] for v in variants], rotation=12, ha="right")
        ax.set_ylabel(ylabel)
        ax.set_ylim(bottom=0)
        style_axis(ax)
    fig.subplots_adjust(left=0.08, right=0.995, bottom=0.18, top=0.94, hspace=0.42, wspace=0.28)
    save_pubfig(fig, "fig6_neura_ablation")
    write_include_wrapper(
        "fig6_neura_ablation",
        "figure*",
        r"\textwidth",
        "Mechanism attribution for NEURA. The top row shows that memory and the full mechanism suppress rebound and remove post-recovery churn. The bottom row shows that removing switch suppression raises route rewrites and peak event intensity even when delivery remains high. The slow state remains comparatively neutral in the tested hotspot region.",
        "fig:neura-ablation",
    )


def select_artifact(pattern: str) -> Path:
    matches = list(ART.glob(pattern))
    if not matches:
        raise FileNotFoundError(pattern)

    def score(path: Path) -> tuple[int, float]:
        m = re.search(r"_s(\d+)", path.name)
        seeds = int(m.group(1)) if m else 0
        return (seeds, path.stat().st_mtime)

    return sorted(matches, key=score)[-1]


def shock_window(rows: list[dict[str, str]]) -> tuple[int, int]:
    burst_ticks = [int(r["tick"]) for r in rows if int(r["burst_active"]) == 1]
    return min(burst_ticks), max(burst_ticks)


def gen_fig1_shock_response() -> None:
    src = select_artifact("shock_response_matrix_er_n100_s*_summary.csv")
    rows = read_csv_rows(src)
    timeline_methods = ["snn_sra", "ospf_te", "triggered_te", "bandit"]
    delivery_methods = ["snn_sra", "ospf_te", "triggered_te", "te_ecmp", "bandit"]
    start_tick, end_tick = shock_window(rows)
    keep_from = max(0, start_tick - 12)
    keep_to = end_tick + 20
    rows = [r for r in rows if keep_from <= int(r["tick"]) <= keep_to and r["method"] in set(delivery_methods)]

    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["method"]].append(row)

    out_rows = []
    tick_set = sorted({int(r["tick"]) for r in rows})
    burst_bounds = {tick: max(int(r["burst_active"]) for r in rows if int(r["tick"]) == tick) for tick in tick_set}
    for tick in tick_set:
        record = {"tick": tick, "time_ms": tick * 10, "burst_active": burst_bounds[tick]}
        for method in delivery_methods:
            row = next(r for r in grouped[method] if int(r["tick"]) == tick)
            record[f"{method}_control_kb"] = float(row["control_bytes_mean"]) / 1000.0
            record[f"{method}_delivery_pct"] = 100.0 * float(row["delivery_ratio_mean"])
            record[f"{method}_route_changes"] = float(row["route_changes_mean"])
        out_rows.append(record)
    write_csv(FIG / "fig1_shock_response_timeline.csv", out_rows, list(out_rows[0].keys()))

    control_series = []
    delivery_series = []
    churn_series = []
    for method in timeline_methods:
        control_coords = " ".join(f"({r['time_ms']},{fmt(float(r[f'{method}_control_kb']),2)})" for r in out_rows)
        delivery_coords = " ".join(f"({r['time_ms']},{fmt(float(r[f'{method}_delivery_pct']),2)})" for r in out_rows)
        churn_coords = " ".join(f"({r['time_ms']},{fmt(float(r[f'{method}_route_changes']),1)})" for r in out_rows)
        style = f"{COLOR[method]}, {LINE[method]}, line width=1.15pt, mark=none"
        control_series.append(rf"""\addplot+[{style}] coordinates {{{control_coords}}};
""")
        delivery_series.append(rf"""\addplot+[{style}] coordinates {{{delivery_coords}}};""")
        churn_series.append(rf"""\addplot+[{style}] coordinates {{{churn_coords}}};""")
    te_coords = " ".join(f"({r['time_ms']},{fmt(float(r['te_ecmp_delivery_pct']),2)})" for r in out_rows)
    delivery_series.append(rf"""\addplot+[{COLOR['te_ecmp']}, {LINE['te_ecmp']}, line width=1.15pt, mark=none] coordinates {{{te_coords}}};""")

    start_ms = start_tick * 10
    end_ms = end_tick * 10
    xmin = out_rows[0]["time_ms"]
    xmax = out_rows[-1]["time_ms"]
    control_ymax = nice_ymax(max(float(r[f"{m}_control_kb"]) for r in out_rows for m in timeline_methods) * 1.08, 100.0)
    churn_ymax = nice_ymax(max(float(r[f"{m}_route_changes"]) for r in out_rows for m in timeline_methods) * 1.10, 500.0)
    tex = rf"""
\begin{{figure*}}[t]
\centering
\begin{{tikzpicture}}
\begin{{groupplot}}[
group style={{group size=1 by 3, vertical sep=0.72cm}},
ieeeplot,
width=0.94\textwidth,
height=0.17\textwidth,
xmin={xmin},
xmax={xmax},
xlabel style={{font=\footnotesize}},
ylabel style={{font=\footnotesize}},
tick label style={{font=\scriptsize}},
legend style={{draw=none, fill=none}},
]
\nextgroupplot[
ylabel={{Control (KB / 10 ms)}},
ymin=0,
ymax={fmt(control_ymax, 0)},
ytick={{0,200,400,600,800}},
xticklabels=\empty,
]
\path[fill=black!4] (axis cs:{start_ms},0) rectangle (axis cs:{end_ms},{fmt(control_ymax, 0)});
\addplot+[black!45, densely dashed, line width=0.75pt, mark=none, forget plot] coordinates {{({start_ms},0) ({start_ms},{fmt(control_ymax, 0)})}};
\addplot+[black!45, densely dashed, line width=0.75pt, mark=none, forget plot] coordinates {{({end_ms},0) ({end_ms},{fmt(control_ymax, 0)})}};
{chr(10).join(control_series)}
\nextgroupplot[
ylabel={{Route changes}},
ymin=0,
ymax={fmt(churn_ymax, 0)},
ytick={{0,2000,4000}},
xticklabels=\empty,
]
\path[fill=black!4] (axis cs:{start_ms},0) rectangle (axis cs:{end_ms},{fmt(churn_ymax, 0)});
\addplot+[black!45, densely dashed, line width=0.75pt, mark=none, forget plot] coordinates {{({start_ms},0) ({start_ms},{fmt(churn_ymax, 0)})}};
\addplot+[black!45, densely dashed, line width=0.75pt, mark=none, forget plot] coordinates {{({end_ms},0) ({end_ms},{fmt(churn_ymax, 0)})}};
{chr(10).join(churn_series)}
\nextgroupplot[
xlabel={{Time (ms)}},
ylabel={{Delivery (\%)}},
ymin=60,
ymax=101,
ytick={{60,80,100}},
]
\path[fill=black!4] (axis cs:{start_ms},60) rectangle (axis cs:{end_ms},101);
\addplot+[black!45, densely dashed, line width=0.75pt, mark=none, forget plot] coordinates {{({start_ms},60) ({start_ms},101)}};
\addplot+[black!45, densely dashed, line width=0.75pt, mark=none, forget plot] coordinates {{({end_ms},60) ({end_ms},101)}};
{chr(10).join(delivery_series)}
\end{{groupplot}}
\node[anchor=south, font=\scriptsize, inner sep=1pt] at ($(group c1r1.north)+(0,0.42cm)$) {{
\begin{{tabular}}{{@{{}}c@{{\quad}}c@{{\quad}}c@{{\quad}}c@{{\quad}}c@{{}}}}
\raisebox{{0.35ex}}{{\tikz{{\draw[SNNBlue, solid, line width=1.15pt] (0,0) -- (0.42,0);}}}}~NEURA &
\raisebox{{0.35ex}}{{\tikz{{\draw[TEOrange, densely dashed, line width=1.15pt] (0,0) -- (0.42,0);}}}}~OSPF-TE &
\raisebox{{0.35ex}}{{\tikz{{\draw[BaselineGray, dash dot, line width=1.15pt] (0,0) -- (0.42,0);}}}}~Triggered-TE &
\raisebox{{0.35ex}}{{\tikz{{\draw[ECMPGreen, dash dot dot, line width=1.15pt] (0,0) -- (0.42,0);}}}}~TE+ECMP &
\raisebox{{0.35ex}}{{\tikz{{\draw[BanditRed, dotted, line width=1.15pt] (0,0) -- (0.42,0);}}}}~Bandit
\end{{tabular}}
}};
\end{{tikzpicture}}
\caption{{Shock response under localized stress. The shaded interval marks the burst window. NEURA stays quiet before the shock, reacts only inside the disturbance interval, and keeps route churn far below the engineering and learning baselines.}}
\label{{fig:shock-response}}
\end{{figure*}}
"""
    (PAPER_FIG / "fig1_shock_response.tex").write_text(tex.strip() + "\n")
    plot_fig1_shock_response(out_rows, timeline_methods, delivery_methods, start_ms, end_ms)


def gen_fig2_blast_radius() -> None:
    src = select_artifact("activation_locality_matrix_er_n100_s*_distance_profile.csv")
    rows = read_csv_rows(src)
    methods = ["snn_baseline", "triggered_te", "ospf_te_t5"]
    buckets = ["0 hop", "1 hop", "2 hops", "3+ hops"]
    bucket_map = {0: "0 hop", 1: "1 hop", 2: "2 hops"}
    per_seed: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: {bucket: 0.0 for bucket in buckets})
    for row in rows:
        method = row["method"]
        if method not in methods:
            continue
        dist = int(row["distance"])
        bucket = bucket_map.get(dist, "3+ hops")
        per_seed[(method, row["seed"])][bucket] += 100.0 * float(row["emit_share"])

    out_rows = []
    for bucket in buckets:
        record = {"bucket": bucket}
        for method in methods:
            seeds = sorted({seed for m, seed in per_seed if m == method})
            record[method] = mean([per_seed[(method, seed)][bucket] for seed in seeds])
        out_rows.append(record)
    write_csv(FIG / "fig2_blast_radius.csv", out_rows, ["bucket"] + methods)

    xcoords = ",".join(buckets)
    parts = []
    for idx, method in enumerate(methods):
        coords = " ".join(f"({row['bucket']},{fmt(float(row[method]),2)})" for row in out_rows)
        shift = (idx - (len(methods) - 1) / 2.0) * 8
        parts.append(rf"""\addplot+[
ybar,
draw=none,
fill={COLOR[method]},
bar width=7pt,
bar shift={fmt(shift, 1)}pt,
] coordinates {{{coords}}};
\addlegendentry{{{DISPLAY[method]}}}""")
    tex = rf"""
\begin{{figure}}[t]
\centering
\begin{{tikzpicture}}
\begin{{axis}}[
ieeeplot,
ybar,
width=\columnwidth,
height=0.58\columnwidth,
ylabel={{Share of hotspot-window updates (\%)}},
ymin=0,
ymax=105,
symbolic x coords={{{xcoords}}},
xtick=data,
xticklabel style={{font=\footnotesize}},
legend columns=3,
legend style={{at={{(0.5,1.12)}}, anchor=south, font=\scriptsize, draw=none, fill=none}},
]
{chr(10).join(parts)}
\end{{axis}}
\end{{tikzpicture}}
\caption{{Blast radius of control activity during a localized hotspot event. The bars report the mean update share generated at each hop-distance bucket from the stressed region. NEURA is compared with the nearest event-triggered engineering baseline and the periodic OSPF-TE baseline.}}
\label{{fig:blast-radius}}
\end{{figure}}
"""
    (PAPER_FIG / "fig2_blast_radius.tex").write_text(tex.strip() + "\n")
    plot_fig2_blast_radius(out_rows, methods, buckets)


def gen_fig3_stress_tradeoff() -> None:
    src = select_artifact("stress_sweep_matrix_er_n100_s*_summary.csv")
    rows = read_csv_rows(src)
    methods = ["snn_sra", "ospf_te", "triggered_te", "te_ecmp", "bandit"]
    out_rows = []
    for row in rows:
        if row["method"] not in methods:
            continue
        out_rows.append(
            {
                "method": row["method"],
                "burst_multiplier": float(row["burst_multiplier"]),
                "delivery_pct": 100.0 * float(row["delivery_mean"]),
                "delivery_ci95": 100.0 * float(row["delivery_ci95"]),
                "control_mb": float(row["control_bytes_mean"]) / 1_000_000.0,
                "control_ci95": float(row["control_bytes_ci95"]) / 1_000_000.0,
            }
        )
    write_csv(FIG / "fig3_stress_tradeoff.csv", out_rows, list(out_rows[0].keys()))

    delivery_series = []
    control_series = []
    for method in methods:
        pts = [r for r in out_rows if r["method"] == method]
        delivery_coords = " ".join(f"({fmt(float(r['burst_multiplier']),1)},{fmt(float(r['delivery_pct']),2)})" for r in pts)
        control_coords = " ".join(f"({fmt(float(r['burst_multiplier']),1)},{fmt(float(r['control_mb']),3)})" for r in pts)
        style = f"{COLOR[method]}, {LINE[method]}, line width=1.05pt, mark={MARK[method]}, mark size=1.6pt"
        delivery_series.append(rf"""\addplot+[{style}] coordinates {{{delivery_coords}}};
\addlegendentry{{{DISPLAY[method]}}}""")
        control_series.append(rf"""\addplot+[{style}] coordinates {{{control_coords}}};""")
    tex = rf"""
\begin{{figure}}[t]
\centering
\begin{{tikzpicture}}
\begin{{groupplot}}[
group style={{group size=1 by 2, vertical sep=0.82cm}},
ieeeplot,
width=\columnwidth,
height=0.50\columnwidth,
xmin=1.8,
xmax=5.2,
legend columns=3,
legend style={{at={{(0.5,1.22)}}, anchor=south, font=\scriptsize, draw=none, fill=none}},
]
\nextgroupplot[
ylabel={{Burst-window delivery ratio (\%)}},
ymin=84,
ymax=102,
]
{chr(10).join(delivery_series)}
\nextgroupplot[
xlabel={{Hotspot demand multiplier (relative to nominal flow demand)}},
ylabel={{Control traffic (MB)}},
ymin=0,
]
{chr(10).join(control_series)}
\end{{groupplot}}
\end{{tikzpicture}}
\caption{{Mitigation cost under increasing stress. The upper panel reports retained service during the hotspot window. The lower panel reports the control traffic required over the same disturbance interval. NEURA accepts a modest delivery penalty while maintaining the lowest control-cost operating point across the stress sweep.}}
\label{{fig:stress-tradeoff}}
\end{{figure}}
"""
    (PAPER_FIG / "fig3_stress_tradeoff.tex").write_text(tex.strip() + "\n")
    plot_fig3_stress_tradeoff(out_rows, methods)


def gen_fig4_startup_and_stretch() -> None:
    stress_src = select_artifact("stress_sweep_matrix_er_n100_s*_summary.csv")
    stress_rows = read_csv_rows(stress_src)
    shock_src = select_artifact("shock_response_matrix_er_n100_s*_detail.csv")
    shock_rows = read_csv_rows(shock_src)
    methods = ["snn_sra", "ospf_te", "triggered_te", "bandit"]

    shock_stats: dict[str, dict[str, float]] = {}
    for method in methods:
        rows = [row for row in shock_rows if row["method"] == method]
        startup_ms = [10.0 * float(row["startup_full_reachability_tick"]) for row in rows]
        startup_mb = [float(row["startup_control_bytes_before_full_reachability"]) / 1_000_000.0 for row in rows]
        shock_stats[method] = {
            "startup_ms_mean": mean(startup_ms),
            "startup_ms_ci95": ci95(startup_ms),
            "startup_mb_mean": mean(startup_mb),
            "startup_mb_ci95": ci95(startup_mb),
        }

    out_rows = []
    for row in stress_rows:
        if row["method"] not in methods or abs(float(row["burst_multiplier"]) - 3.0) > 1e-9:
            continue
        stats = shock_stats[row["method"]]
        out_rows.append(
            {
                "method": row["method"],
                "startup_ms_mean": stats["startup_ms_mean"],
                "startup_ms_ci95": stats["startup_ms_ci95"],
                "startup_mb_mean": stats["startup_mb_mean"],
                "startup_mb_ci95": stats["startup_mb_ci95"],
                "path_stretch_mean": float(row["path_stretch_mean"]),
                "path_stretch_ci95": float(row["path_stretch_ci95"]),
            }
        )
    write_csv(FIG / "fig4_startup_and_stretch.csv", out_rows, list(out_rows[0].keys()))
    bar_key = {"snn_sra": "NEURA", "ospf_te": "OSPF", "triggered_te": "Triggered", "bandit": "Bandit"}
    xcoords = ",".join(bar_key[m] for m in methods)
    xticklabels = ",".join(bar_key[m] for m in methods)
    startup_parts = "\n".join(
        rf"""\addplot+[
ybar,
bar shift=0pt,
draw=none,
fill={COLOR[row['method']]},
error bars/y dir=both,
error bars/y explicit,
] coordinates {{({bar_key[row['method']]},{fmt(float(row['startup_ms_mean']),1)}) +- (0,{fmt(float(row['startup_ms_ci95']),1)})}};"""
        for row in out_rows
    )
    stretch_parts = "\n".join(
        rf"""\addplot+[
ybar,
bar shift=0pt,
draw=none,
fill={COLOR[row['method']]},
error bars/y dir=both,
error bars/y explicit,
] coordinates {{({bar_key[row['method']]},{fmt(float(row['path_stretch_mean']),3)}) +- (0,{fmt(float(row['path_stretch_ci95']),3)})}};"""
        for row in out_rows
    )
    tex = rf"""
\begin{{figure*}}[t]
\centering
\begin{{tikzpicture}}
\begin{{groupplot}}[
group style={{group size=2 by 1, horizontal sep=1.5cm}},
ieeeplot,
width=0.39\textwidth,
height=0.22\textwidth,
symbolic x coords={{{xcoords}}},
xtick={{{xcoords}}},
xticklabels={{{xticklabels}}},
xticklabel style={{font=\footnotesize}},
enlarge x limits=0.18,
]
\nextgroupplot[
ybar,
title={{(a) Startup convergence}},
ylabel={{Time to full reachability (ms)}},
ymin=0,
]
{startup_parts}
\nextgroupplot[
ybar,
title={{(b) Path stretch at $3\times$ stress}},
ylabel={{Burst-window path stretch}},
ymin=1.0,
ymax=1.08,
]
{stretch_parts}
\end{{groupplot}}
\end{{tikzpicture}}
\caption{{Startup cost and data-plane path penalty. Panel (a) reports the time needed to establish full reachability from startup. Panel (b) reports mean burst-window path stretch at the representative $3\times$ hotspot workload. NEURA matches the fastest startup convergence and maintains bounded path inflation rather than achieving low control traffic by allowing arbitrarily poor routes.}}
\label{{fig:startup-and-stretch}}
\end{{figure*}}
"""
    (PAPER_FIG / "fig4_startup_and_stretch.tex").write_text(tex.strip() + "\n")
    plot_fig4_startup_and_stretch(out_rows, methods, bar_key)


def gen_fig5_continuous_chaos() -> None:
    timeline_src = select_artifact("continuous_chaos_matrix_er_n100_s*_timeline.csv")
    rows = read_csv_rows(timeline_src)
    timeline_methods = ["snn_sra", "ospf_te", "triggered_te", "bandit"]
    summary_methods = ["snn_sra", "ospf_te", "triggered_te", "te_ecmp", "bandit"]
    summary_path = select_artifact("continuous_chaos_matrix_er_n100_s*_summary.json")
    summary = json.loads(summary_path.read_text())
    windows = [(w["start"] * 10, w["end"] * 10) for w in summary["burst_windows"]]

    grouped: dict[tuple[str, int], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        if row["method"] in timeline_methods:
            grouped[(row["method"], int(row["tick"]))].append(row)

    ticks = sorted({int(r["tick"]) for r in rows})
    out_rows = []
    for tick in ticks:
        record = {"tick": tick, "time_ms": tick * 10}
        for method in timeline_methods:
            vals = grouped[(method, tick)]
            record[f"{method}_control_kb"] = mean([float(v["control_bytes"]) / 1000.0 for v in vals])
            record[f"{method}_delivery_pct"] = mean([100.0 * float(v["delivery_ratio"]) for v in vals])
        out_rows.append(record)

    summary_csv_src = select_artifact("continuous_chaos_matrix_er_n100_s*_summary.csv")
    summary_records = [
        {
            "method": row["method"],
            "total_control_mb": float(row["total_control_bytes_mean"]) / 1_000_000.0,
            "total_control_mb_ci95": float(row["total_control_bytes_ci95"]) / 1_000_000.0,
            "route_changes_per_node_mean": float(row["route_changes_per_node_mean"]),
            "route_changes_per_node_ci95": float(row["route_changes_per_node_ci95"]),
        }
        for row in read_csv_rows(summary_csv_src)
        if row["method"] in summary_methods
    ]

    write_csv(FIG / "fig5_continuous_chaos.csv", out_rows, list(out_rows[0].keys()))
    write_csv(FIG / "fig5_continuous_chaos_summary.csv", summary_records, list(summary_records[0].keys()))

    control_series = []
    delivery_series = []
    for method in timeline_methods:
        control_coords = " ".join(f"({r['time_ms']},{fmt(float(r[f'{method}_control_kb']),2)})" for r in out_rows)
        delivery_coords = " ".join(f"({r['time_ms']},{fmt(float(r[f'{method}_delivery_pct']),2)})" for r in out_rows)
        style = f"{COLOR[method]}, {LINE[method]}, line width=1.0pt, mark=none"
        control_series.append(rf"""\addplot+[{style}] coordinates {{{control_coords}}};""")
        delivery_series.append(rf"""\addplot+[{style}] coordinates {{{delivery_coords}}};""")

    control_ymax = nice_ymax(max(float(r[f"{m}_control_kb"]) for r in out_rows for m in timeline_methods) * 1.08, 100.0)
    control_ymax = max(control_ymax, 800.0)
    window_regions_control = []
    window_regions_delivery = []
    window_lines_control = []
    window_lines_delivery = []
    for start_ms, end_ms in windows:
        window_regions_control.append(rf"\path[fill=black!7] (axis cs:{start_ms},0) rectangle (axis cs:{end_ms},{fmt(control_ymax, 0)});")
        window_regions_delivery.append(rf"\path[fill=black!7] (axis cs:{start_ms},80) rectangle (axis cs:{end_ms},101);")
        window_lines_control.append(rf"\addplot+[black!30, densely dashed, mark=none, forget plot] coordinates {{({start_ms},0) ({start_ms},{fmt(control_ymax, 0)})}};")
        window_lines_control.append(rf"\addplot+[black!30, densely dashed, mark=none, forget plot] coordinates {{({end_ms},0) ({end_ms},{fmt(control_ymax, 0)})}};")
        window_lines_delivery.append(rf"\addplot+[black!30, densely dashed, mark=none, forget plot] coordinates {{({start_ms},80) ({start_ms},101)}};")
        window_lines_delivery.append(rf"\addplot+[black!30, densely dashed, mark=none, forget plot] coordinates {{({end_ms},80) ({end_ms},101)}};")

    bar_key = {"snn_sra": "NEURA", "ospf_te": "OSPF", "triggered_te": "Triggered", "te_ecmp": "ECMP", "bandit": "Bandit"}
    xcoords = ",".join(bar_key[m] for m in summary_methods)
    xticklabels = ",".join(bar_key[m] for m in summary_methods)
    control_bars = "\n".join(
        rf"""\addplot+[
ybar,
bar shift=0pt,
draw=none,
fill={COLOR[row['method']]},
error bars/y dir=both,
error bars/y explicit,
] coordinates {{({bar_key[row['method']]},{fmt(float(row['total_control_mb']),2)}) +- (0,{fmt(float(row['total_control_mb_ci95']),2)})}};"""
        for row in summary_records
    )
    churn_bars = "\n".join(
        rf"""\addplot+[
ybar,
bar shift=0pt,
draw=none,
fill={COLOR[row['method']]},
error bars/y dir=both,
error bars/y explicit,
] coordinates {{({bar_key[row['method']]},{fmt(float(row['route_changes_per_node_mean']),2)}) +- (0,{fmt(float(row['route_changes_per_node_ci95']),2)})}};"""
        for row in summary_records
    )

    tex = rf"""
\begin{{figure*}}[t]
\centering
\begin{{tikzpicture}}
\draw[{COLOR['snn_sra']}, {LINE['snn_sra']}, line width=1.1pt] (0.05\textwidth,0.475\textwidth) -- (0.105\textwidth,0.475\textwidth);
\node[anchor=west, font=\scriptsize] at (0.11\textwidth,0.475\textwidth) {{{DISPLAY['snn_sra']}}};
\draw[{COLOR['ospf_te']}, {LINE['ospf_te']}, line width=1.1pt] (0.34\textwidth,0.475\textwidth) -- (0.395\textwidth,0.475\textwidth);
\node[anchor=west, font=\scriptsize] at (0.40\textwidth,0.475\textwidth) {{{DISPLAY['ospf_te']}}};
\draw[{COLOR['triggered_te']}, {LINE['triggered_te']}, line width=1.1pt] (0.50\textwidth,0.475\textwidth) -- (0.555\textwidth,0.475\textwidth);
\node[anchor=west, font=\scriptsize] at (0.56\textwidth,0.475\textwidth) {{{DISPLAY['triggered_te']}}};
\draw[{COLOR['bandit']}, {LINE['bandit']}, line width=1.1pt] (0.76\textwidth,0.475\textwidth) -- (0.815\textwidth,0.475\textwidth);
\node[anchor=west, font=\scriptsize] at (0.82\textwidth,0.475\textwidth) {{{DISPLAY['bandit']}}};
\begin{{axis}}[
ieeeplot,
at={{(0,0.305\textwidth)}},
anchor=south west,
width=\textwidth,
height=0.145\textwidth,
ylabel={{\shortstack{{Control\\(KB / 10 ms)}}}},
ylabel style={{align=center, font=\scriptsize}},
xlabel={{}},
xmin={out_rows[0]['time_ms']},
xmax={out_rows[-1]['time_ms']},
xtick={{0,200,400,600,800,1000,1200,1400}},
xticklabels=\empty,
ymin=0,
ymax={fmt(control_ymax, 0)},
]
{chr(10).join(window_regions_control)}
{chr(10).join(window_lines_control)}
{chr(10).join(control_series)}
\end{{axis}}
\begin{{axis}}[
ieeeplot,
at={{(0,0.155\textwidth)}},
anchor=south west,
width=\textwidth,
height=0.145\textwidth,
xlabel={{Time (ms)}},
ylabel={{\shortstack{{Delivery\\(\%)}}}},
ylabel style={{align=center, font=\scriptsize}},
xmin={out_rows[0]['time_ms']},
xmax={out_rows[-1]['time_ms']},
xtick={{0,200,400,600,800,1000,1200,1400}},
ymin=80,
ymax=101,
]
{chr(10).join(window_regions_delivery)}
{chr(10).join(window_lines_delivery)}
{chr(10).join(delivery_series)}
\end{{axis}}
\begin{{axis}}[
ieeeplot,
at={{(0,0)}},
anchor=south west,
width=0.47\textwidth,
height=0.125\textwidth,
ybar,
ylabel={{\shortstack{{Total control\\traffic (MB)}}}},
ylabel style={{align=center, font=\scriptsize}},
symbolic x coords={{{xcoords}}},
xtick={{{xcoords}}},
xticklabels={{{xticklabels}}},
xticklabel style={{font=\footnotesize}},
enlarge x limits=0.18,
ymin=0,
]
{control_bars}
\end{{axis}}
\begin{{axis}}[
ieeeplot,
at={{(0.53\textwidth,0)}},
anchor=south west,
width=0.47\textwidth,
height=0.125\textwidth,
ybar,
ylabel={{\shortstack{{Route changes\\per node}}}},
ylabel style={{align=center, font=\scriptsize}},
symbolic x coords={{{xcoords}}},
xtick={{{xcoords}}},
xticklabels={{{xticklabels}}},
xticklabel style={{font=\footnotesize}},
enlarge x limits=0.18,
ymin=0,
]
{churn_bars}
\end{{axis}}
\end{{tikzpicture}}
\caption{{Continuous-chaos behavior under three repeated hotspot shocks. The shaded intervals mark the disturbance windows. The upper panels show control traffic and delivery over time. The lower panels summarize cumulative control cost and route churn. NEURA keeps both totals far below the stronger baselines while preserving useful service.}}
\label{{fig:continuous-chaos}}
\end{{figure*}}
"""
    (PAPER_FIG / "fig5_continuous_chaos.tex").write_text(tex.strip() + "\n")
    plot_fig5_continuous_chaos(out_rows, summary_records, timeline_methods, summary_methods, windows)


def gen_fig6_neura_ablation() -> None:
    rebound_rows = read_csv_rows(select_artifact("memory_rebound_matrix_er_n100_s*_summary.csv"))
    chaos_rows = read_csv_rows(select_artifact("neura_ablation_matrix_er_n100_s*_summary.csv"))

    memory_variants = ["baseline", "memory_only", "full"]
    chaos_variants = ["full", "no_memory", "no_inhibition", "no_slow"]
    memory_means = {
        row["variant"]: row
        for row in rebound_rows
        if row["section"] == "variant_mean"
    }
    chaos_means = {
        row["variant"]: row
        for row in chaos_rows
        if row["scenario"] == "chaos"
    }
    figure_rows = []
    for variant in memory_variants:
        row = memory_means[variant]
        figure_rows.append(
            {
                "panel": "memory",
                "variant": variant,
                "label": DISPLAY[variant],
                "rebound_ratio_pct": 100.0 * float(row["rebound_ratio_after_release"]),
                "post_stage2_route_changes": float(row["post_stage2_route_changes"]),
            }
        )
    for variant in chaos_variants:
        row = chaos_means[variant]
        figure_rows.append(
            {
                "panel": "chaos",
                "variant": variant,
                "label": DISPLAY[variant],
                "chaos_route_changes_per_node": float(row["route_changes_per_node_mean"]),
                "chaos_peak_event_rate_k": float(row["peak_event_rate_mean"]) / 1000.0,
            }
        )
    fig6_fields = sorted({key for row in figure_rows for key in row.keys()})
    write_csv(FIG / "fig6_neura_ablation.csv", figure_rows, fig6_fields)

    def single_bar_block(variants: list[str], value_fn) -> str:
        parts = []
        for variant in variants:
            parts.append(
                rf"""\addplot+[
ybar,
draw=none,
fill={COLOR[variant]},
bar width=15pt,
] coordinates {{({variant},{fmt(value_fn(variant),2)})}};"""
            )
        return "\n".join(parts)

    memory_xcoords = ",".join(memory_variants)
    memory_xticklabels = ",".join(DISPLAY[variant] for variant in memory_variants)
    chaos_xcoords = ",".join(chaos_variants)
    chaos_xticklabels = ",".join(DISPLAY[variant] for variant in chaos_variants)
    rebound_ymax = nice_ymax(max(100.0 * float(memory_means[v]["rebound_ratio_after_release"]) for v in memory_variants) * 1.20, 1.0)
    stage2_churn_ymax = nice_ymax(max(float(memory_means[v]["post_stage2_route_changes"]) for v in memory_variants) * 1.20, 5.0)
    chaos_route_ymax = nice_ymax(max(float(chaos_means[v]["route_changes_per_node_mean"]) for v in chaos_variants) * 1.15, 20.0)
    chaos_peak_ymax = nice_ymax(max(float(chaos_means[v]["peak_event_rate_mean"]) / 1000.0 for v in chaos_variants) * 1.15, 5.0)
    legend_block = rf"""
\path[fill={COLOR['baseline']}] (0.02\textwidth,0.545\textwidth) rectangle +(0.02\textwidth,0.012\textwidth);
\node[anchor=west, font=\small] at (0.045\textwidth,0.551\textwidth) {{{DISPLAY['baseline']}}};
\path[fill={COLOR['memory_only']}] (0.27\textwidth,0.545\textwidth) rectangle +(0.02\textwidth,0.012\textwidth);
\node[anchor=west, font=\small] at (0.295\textwidth,0.551\textwidth) {{{DISPLAY['memory_only']}}};
\path[fill={COLOR['full']}] (0.53\textwidth,0.545\textwidth) rectangle +(0.02\textwidth,0.012\textwidth);
\node[anchor=west, font=\small] at (0.555\textwidth,0.551\textwidth) {{{DISPLAY['full']}}};
\path[fill={COLOR['no_inhibition']}] (0.75\textwidth,0.545\textwidth) rectangle +(0.02\textwidth,0.012\textwidth);
\node[anchor=west, font=\small] at (0.775\textwidth,0.551\textwidth) {{{DISPLAY['no_inhibition']}}};
"""
    tex = rf"""
\begin{{figure*}}[t]
\centering
\begin{{tikzpicture}}
\begin{{groupplot}}[
group style={{group size=2 by 2, horizontal sep=1.2cm, vertical sep=1.0cm}},
ieeeplot,
width=0.47\textwidth,
height=0.25\textwidth,
ybar,
xticklabel style={{font=\footnotesize, rotate=12, anchor=east}},
enlarge x limits=0.18,
]
\nextgroupplot[
ylabel={{Rebound ratio after release (\%)}},
ymin=0,
ymax={fmt(rebound_ymax, 0)},
symbolic x coords={{{memory_xcoords}}},
xtick={{{memory_xcoords}}},
xticklabels={{{memory_xticklabels}}},
]
{single_bar_block(memory_variants, lambda variant: 100.0 * float(memory_means[variant]["rebound_ratio_after_release"]))}
\nextgroupplot[
ylabel={{Post-stage-2 route changes}},
ymin=0,
ymax={fmt(stage2_churn_ymax, 0)},
symbolic x coords={{{memory_xcoords}}},
xtick={{{memory_xcoords}}},
xticklabels={{{memory_xticklabels}}},
]
{single_bar_block(memory_variants, lambda variant: float(memory_means[variant]["post_stage2_route_changes"]))}
\nextgroupplot[
ylabel={{Chaos route changes / node}},
ymin=0,
ymax={fmt(chaos_route_ymax, 0)},
symbolic x coords={{{chaos_xcoords}}},
xtick={{{chaos_xcoords}}},
xticklabels={{{chaos_xticklabels}}},
]
{single_bar_block(chaos_variants, lambda variant: float(chaos_means[variant]["route_changes_per_node_mean"]))}
\nextgroupplot[
ylabel={{Chaos peak event rate ($10^3$ msgs / tick)}},
ymin=0,
ymax={fmt(chaos_peak_ymax, 0)},
symbolic x coords={{{chaos_xcoords}}},
xtick={{{chaos_xcoords}}},
xticklabels={{{chaos_xticklabels}}},
]
{single_bar_block(chaos_variants, lambda variant: float(chaos_means[variant]["peak_event_rate_mean"]) / 1000.0)}
\end{{groupplot}}
{legend_block}
\end{{tikzpicture}}
\caption{{Mechanism attribution for NEURA. The top row shows that memory and the full mechanism suppress rebound and remove post-recovery churn. The bottom row shows that removing switch suppression raises route rewrites and peak event intensity even when delivery remains high. The slow state remains comparatively neutral in the tested hotspot region.}}
\label{{fig:neura-ablation}}
\end{{figure*}}
"""
    (PAPER_FIG / "fig6_neura_ablation.tex").write_text(tex.strip() + "\n")
    plot_fig6_neura_ablation(figure_rows, memory_variants, chaos_variants)


def main() -> None:
    gen_fig1_shock_response()
    gen_fig2_blast_radius()
    gen_fig3_stress_tradeoff()
    gen_fig4_startup_and_stretch()
    gen_fig5_continuous_chaos()
    gen_fig6_neura_ablation()
    print("Generated redesigned IEEE figure assets.")


if __name__ == "__main__":
    main()
