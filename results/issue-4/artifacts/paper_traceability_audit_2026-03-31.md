# Paper Traceability Audit

Date: `2026-03-31`
Scope: current `paper/main.tex` draft against the formal evidence under `results/issue-4/`
Branch: `exp/4-paper-evidence-final`

## Verdict

The current manuscript's main quantitative claims are traceable to existing `results/issue-4` artifacts.

No blocking mismatch was found between:

- headline abstract numbers
- the five main evaluation figures
- the safety table
- the `ns-3` validation table
- the gray-failure subsection
- the mechanism-attribution subsection

Two maintenance gaps remain:

1. the repository README evidence map was narrower than the manuscript's actual evidence usage
2. the safety table uses worst-case-over-seeds values from the detail CSV, which should be recorded explicitly for provenance

## Traceability Checklist

### Abstract Headline Claims

- Claim: `3x` hotspot gives `93.6%` delivery and `9.51 MB` control for `NEURA`, versus `97.3%` and `59.64 MB` for `OSPF-TE`
  - source: `figures/fig3_stress_tradeoff.csv`
  - upstream: `artifacts/stress_sweep_matrix_er_n100_s5_summary.csv`
  - status: matched

- Claim: repeated disturbance gives `9.14 MB` total control and about `100` route changes per node for `NEURA`, versus `52.09 MB` and about `210` for `OSPF-TE`
  - source: `figures/fig5_continuous_chaos_summary.csv`
  - upstream: `artifacts/continuous_chaos_matrix_er_n100_s5_summary.csv`
  - status: matched

### Figure 1: Shock Response

- Manuscript hook: `\input{figures/fig1_shock_response}`
- Generated figure source: `paper/figures/fig1_shock_response.tex`
- CSV source: `figures/fig1_shock_response_timeline.csv`
- Upstream artifact: `artifacts/shock_response_matrix_er_n100_s5_summary.csv`
- Checked claims:
  - calm-period control near `1.1 KB`, `311 KB`, `378 KB`, `626 KB`
  - hotspot onset delivery and control movement
  - burst-window route-change ordering
- status: matched

### Figure 2: Blast Radius

- Manuscript hook: `\input{figures/fig2_blast_radius}`
- Generated figure source: `paper/figures/fig2_blast_radius.tex`
- CSV source: `figures/fig2_blast_radius.csv`
- Upstream artifact: `artifacts/activation_locality_matrix_er_n100_s5_distance_profile.csv`
- Checked claims:
  - `NEURA` keeps `100%` of hotspot-window updates within one hop
  - `11.7%` hotspot and `88.3%` one-hop split
  - `OSPF-TE` keeps about `14.0%` within one hop and shifts the rest to `2` hops and beyond
- status: matched

### Figure 3: Stress Tradeoff

- Manuscript hook: `\input{figures/fig3_stress_tradeoff}`
- Generated figure source: `paper/figures/fig3_stress_tradeoff.tex`
- CSV source: `figures/fig3_stress_tradeoff.csv`
- Upstream artifact: `artifacts/stress_sweep_matrix_er_n100_s5_summary.csv`
- Checked claims:
  - `3x` hotspot values for `NEURA`, `Triggered-TE`, `OSPF-TE`, `TE+ECMP`, `Bandit`
  - `5x` hotspot values for `NEURA`, `Triggered-TE`, `OSPF-TE`
  - route-change ordering at `3x`
- status: matched

### Figure 4: Startup And Stretch

- Manuscript hook: `\input{figures/fig4_startup_and_stretch}`
- Generated figure source: `paper/figures/fig4_startup_and_stretch.tex`
- CSV source: `figures/fig4_startup_and_stretch.csv`
- Upstream artifact: `artifacts/stress_sweep_matrix_er_n100_s5_summary.csv`
- Checked claims:
  - startup convergence `40 / 40 / 88 / 62 ms`
  - startup control budget `1.76 / 3.36 / 2.73 / 3.22 MB`
  - path stretch `1.048 / 1.031 / 1.007 / 1.002`
- status: matched

### Figure 5: Continuous Chaos

- Manuscript hook: `\input{figures/fig5_continuous_chaos}`
- Generated figure source: `paper/figures/fig5_continuous_chaos.tex`
- CSV sources:
  - `figures/fig5_continuous_chaos.csv`
  - `figures/fig5_continuous_chaos_summary.csv`
- Upstream artifacts:
  - `artifacts/continuous_chaos_matrix_er_n100_s5_timeline.csv`
  - `artifacts/continuous_chaos_matrix_er_n100_s5_summary.csv`
- Checked claims:
  - total control `9.14 / 48.43 / 52.09 / 99.85 / 87.01 MB`
  - route changes `100 / 122 / 210 / 282 / 5645`
  - service-loss ticks `7.4 / 6.8 / 7.2 / 6.4 / 4.0`
  - mean burst delivery ordering
- status: matched

### Gray-Failure Subsection

- Manuscript location: gray-failure subsection in `paper/main.tex`
- Upstream artifacts:
  - `artifacts/gray_failure_matrix_er_n100_s5_summary.csv`
  - `artifacts/gray_failure_matrix_er_n100_s5_detail.csv`
- Checked claims:
  - `NEURA` `92.5%`, `20.3 KB`, `6.39 MB`
  - `Triggered-TE` `96.4%`, `329.3 KB`, `41.68 MB`
  - `OSPF-TE` `97.3%`, `378.3 KB`, `44.53 MB`
  - `Bandit` `97.1%`, `630.7 KB`, `74.43 MB`
- status: matched

### Safety Table

- Manuscript location: `tab:safety` in `paper/main.tex`
- Primary source:
  - `artifacts/safety_indicator_matrix_er_n100_s5_detail.csv`
- Supporting summaries:
  - `artifacts/safety_indicator_matrix_er_n100_s5_summary.json`
  - `artifacts/safety_transient_matrix_er_n100_s5_summary.json`
- Verification method:
  - recomputed worst-case-over-seeds values from the detail CSV
- Checked claims:
  - `NEURA` hotspot `99.87 / 0.13 / 0.00 / 100.0 / 100.0`
  - `Triggered-TE` hotspot `98.97 / 1.03 / 0.00 / 99.05 / 6.0`
  - `Triggered-TE` chaos `97.64 / 2.36 / 0.00 / 99.02 / 3.0`
  - `Triggered-TE` gray failure `99.11 / 0.89 / 0.00 / 99.12 / 13.0`
- status: matched

### Safety Tail Narrative

- Manuscript location: paragraph after `tab:safety`
- Primary source:
  - `artifacts/safety_transient_matrix_er_n100_s5_summary.json`
- Detail source:
  - `artifacts/safety_transient_matrix_er_n100_s5_detail.csv`
- Checked claims:
  - `NEURA` hotspot loop-active ticks `14.6`, post-hotspot `0.2`
  - `NEURA` chaos and gray-failure post-stress tail `0.0`
  - `Triggered-TE` post-stress incomplete-route tails `25.2`, `13.6`, `9.8`
  - worst post-stress episodes `89`, `34`, `49`
- status: matched

### ns-3 Validation Table

- Manuscript hook: `\input{figures/tab7_ns3_validation}`
- Source table snippet: `paper/figures/tab7_ns3_validation.tex`
- Replay sources:
  - `artifacts/ns3_replay_hotspot_er_n24_seed51_u2_tick100/ns3_replay_rate10_q20_summary.csv`
  - `artifacts/ns3_replay_repeated_er_n24_seed51_u2_tick100/ns3_replay_rate10_q20_summary.csv`
- Queue-signal sanity source:
  - `artifacts/ns3_queue_signal_sanity_rate10_q20_sample100_pri4_bg8/summary.csv`
- TCP sanity source:
  - `artifacts/ns3_tcp_goodput_sanity_rate10_q20_sample100_bg8/summary.csv`
- Checked claims:
  - replay control `0.35 / 1.63 / 1.65 MB`
  - replay delivery near `99.7 / 99.96 / 99.81` for hotspot and `99.25 / 99.89 / 99.53` for repeated
  - queue sanity route changes `1 / 4 / 12`
  - TCP burst goodput `6.89 / 7.36 / 6.77 Mbps`
  - TCP route changes `14 / 42 / 21`
  - TCP tail switches `7 / 22 / 9`
  - TCP cwnd drops `399 / 454 / 319`
- status: matched

### Mechanism Attribution Table

- Manuscript hook: `\input{figures/tab6_neura_ablation}`
- Generated table snippet: `paper/figures/tab6_neura_ablation.tex`
- Intermediate CSV source:
  - `figures/fig6_neura_ablation.csv`
- Upstream artifacts:
  - `artifacts/memory_rebound_matrix_er_n100_s5_summary.csv`
  - `artifacts/neura_ablation_matrix_er_n100_s5_summary.csv`
- Checked claims:
  - rebound `1.57 / 1.40 / 0.28`
  - post-stage-2 route changes `21 / 0 / 0`
  - chaos route changes `100.1 / 100.0 / 125.9 / 100.1`
  - peak event rate `29.7 / 29.7 / 41.6 / 29.7`
- status: matched

### Supporting Topology Validation

- RGG stress sweep source:
  - `artifacts/stress_sweep_matrix_rgg_n100_s5_summary.csv`
- RGG chaos source:
  - `artifacts/continuous_chaos_matrix_rgg_n100_s5_summary.csv`
- RGG locality source:
  - `artifacts/activation_locality_matrix_rgg_n100_s5_distance_profile.csv`
- Checked claims:
  - `3x` hotspot `51.9% / 14.86 MB / 181` for `NEURA`
  - chaos `54.0% / 17.63 MB / 209` for `NEURA`
  - control-locality ordering persists versus `Triggered-TE` and `OSPF-TE`
- status: matched

## Notes

- The manuscript currently relies on generated `paper/figures/*.tex` files for the five main figures and the mechanism-attribution table.
- The safety table is hand-written in `paper/main.tex`, so its provenance should continue to be checked against `safety_indicator_matrix_*_detail.csv` after any rerun.
- The current audit did not re-run simulations; it verified consistency against existing formal artifacts already present under `results/issue-4/`.
