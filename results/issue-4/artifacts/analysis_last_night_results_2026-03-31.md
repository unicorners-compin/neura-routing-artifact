# Last-Night Result Analysis

This note summarizes the newest `issue-4` simulation outputs that were written on `2026-03-30 08:22:31` to `2026-03-30 15:26:12` in the container filesystem timezone (`UTC`), corresponding to `2026-03-30 16:22:31` to `2026-03-30 23:26:12` in `Asia/Shanghai`.

## Files Located

The newest batch is entirely under `results/issue-4/` and covers three groups:

1. Main simulator matrix updates on `ER-100`:
   - `neura_ablation_matrix_er_n100_s5_*`
   - `memory_rebound_matrix_er_n100_s5_*`
   - `shock_response_matrix_er_n100_s5_*`
   - `stress_sweep_matrix_er_n100_s5_*`
   - `continuous_chaos_matrix_er_n100_s5_*`
   - `safety_indicator_matrix_er_n100_s5_*`
   - `gray_failure_matrix_er_n100_s5_*`
   - `safety_transient_matrix_er_n100_s5_*`
   - `snn_engineering_sensitivity_er_n100_s5_*`
2. Spatial-topology follow-up on `RGG-100`:
   - `activation_locality_matrix_rgg_n100_s5_*`
   - `stress_sweep_matrix_rgg_n100_s5_*`
   - `continuous_chaos_matrix_rgg_n100_s5_*`
3. Packet-level `ns-3` validation:
   - `ns3_replay_hotspot_er_n24_seed51_u2_tick100/*`
   - `ns3_replay_repeated_er_n24_seed51_u2_tick100/*`
   - `ns3_queue_signal_sanity_rate10_q20_sample100_pri4_bg8/*`
   - `ns3_tcp_goodput_sanity_rate10_q20_sample100_bg8/*`

## Main Readout

### 1. The core paper claim still holds on the new ER batch

At `ER-100`, `3x` hotspot load:

- `snn_sra` delivery is `0.9361`, versus `0.9730` for `ospf_te` and `0.9737` for `triggered_te`.
- `snn_sra` total control is only `9.51 MB`, versus `59.64 MB` for `ospf_te` and `54.23 MB` for `triggered_te`.
- `snn_sra` route changes per node are `99.9`, versus `172.4` for `ospf_te` and `117.7` for `triggered_te`.

Interpretation: the low-overhead operating point survives; the tradeoff is still "less delivery than TE-style baselines, but far less control traffic."

### 2. Continuous-chaos and gray-failure results strengthen the low-overhead story

Under `ER-100` continuous chaos:

- `snn_sra` burst delivery is `0.9602`, close to `ospf_te` `0.9656` and `triggered_te` `0.9639`.
- `snn_sra` total control is `9.14 MB`, versus `52.09 MB` and `48.43 MB`.
- `snn_sra` route changes per node are `100.1`, versus `209.5` and `122.1`.
- `snn_sra` post-burst active ticks drop to `72`, versus `91` for both TE baselines.

Under `ER-100` gray failure:

- `snn_sra` impairment delivery is `0.9248`, lower than `ospf_te` `0.9734` and `triggered_te` `0.9644`.
- But `snn_sra` impairment-window control is only `20.3 KB`, versus `378 KB` and `329 KB`.
- Total control remains `6.39 MB`, versus `44.53 MB` and `41.68 MB`.

Interpretation: the new results still favor `snn_sra` when the question is bounded control burden under sustained stress, not peak delivery.

### 3. Memory looks like a real mechanism, slow state looks secondary

From `memory_rebound_matrix_er_n100_s5_summary.csv`:

- Baseline rebound ratio after release: `0.0157`
- Memory-only rebound ratio after release: `0.0140`
- Full system rebound ratio after release: `0.00283`
- Post-stage-2 route changes fall from `21.0` to `0.0` with memory-enabled variants.
- Emitted updates fall from `208421.8` in baseline to `165540.2` in full.

Interpretation: short-term memory is doing meaningful rebound suppression work. This supports the paper's ablation claim that memory and inhibition matter more than the slow background state in the tested region.

### 4. Safety metrics are mixed but defensible

From `safety_indicator_matrix_er_n100_s5_summary.csv` under `hotspot_3x`:

- `snn_sra` reaches full reachability in `40 ms`, faster than `ospf_te` and `te_ecmp` at `88 ms`.
- `snn_sra` keeps final node-complete-route ratio at `1.0`.
- `triggered_te` finishes with only `0.812` final node-complete-route ratio.

From `safety_transient_matrix_er_n100_s5_summary.csv` under `hotspot_3x`:

- `snn_sra` still shows `14.6` loop-active ticks on average.
- `triggered_te` is worse at `35.4` loop-active ticks and `25.2` post-active ticks.
- `ospf_te` has fewer loops, but introduces blackhole and incomplete-route activity.

Interpretation: `snn_sra` is not the cleanest transient in every sense, but it avoids the more damaging blackhole behavior and ends in a fully complete route state.

### 5. The new RGG topology is the main caution flag

From `activation_locality_matrix_rgg_n100_s5_summary.csv`:

- `snn_full` keeps activity local: weighted mean emit distance `0.91`, far-share `0.0`, max active distance `1.0`.
- `ospf_te_t5` spreads far wider: weighted mean emit distance `3.97`, far-share `0.388`, max active distance `8.2`.

From `stress_sweep_matrix_rgg_n100_s5_summary.csv` at `3x` load:

- `snn_sra` delivery falls to `0.5193`.
- `ospf_te` reaches `0.5546`; `bandit` reaches `0.5888`.
- `snn_sra` still uses much less control: `14.86 MB` versus `72.97 MB` for `ospf_te`.

From `continuous_chaos_matrix_rgg_n100_s5_summary.csv`:

- `snn_sra` burst delivery is `0.5395`, very close to `ospf_te` `0.5504`.
- `snn_sra` total control is `17.63 MB`, versus `63.46 MB` for `ospf_te` and `71.21 MB` for `triggered_te`.

Interpretation: locality still generalizes to `RGG`, but service quality degrades sharply for everyone, and `snn_sra` no longer looks clearly competitive on delivery. This is a boundary result, not a headline result.

### 6. The three `ns-3` checks support the paper's realism story

Replay hotspot:

- `snn_sra` control bytes: `350520`
- `triggered_te`: `1629660`
- `ospf_te`: `1646880`
- `snn_sra` ns-3 data delivery remains `0.996958`

Replay repeated:

- `snn_sra` control bytes: `350520`
- `triggered_te`: `1629660`
- `ospf_te`: `1646880`
- `snn_sra` ns-3 data delivery remains `0.992517`

Queue-signal sanity:

- `lift` route changes: `1`
- `triggered_te`: `4`
- `ospf_te`: `12`
- All three preserve `1.0` primary delivery

TCP goodput sanity:

- `triggered_te` has the highest burst goodput at `7.36 Mbps`
- `lift` is lower at `6.89 Mbps`, but with `14` route changes instead of `42`
- `lift` tail switches are `7`, versus `22` for `triggered_te`

Interpretation: packet-level execution does not erase the control-efficiency ordering, but it does show that `triggered_te` can win on burst goodput and packet delay when the comparison is transport-facing.

## Overall Conclusion

The newest batch is coherent and useful. It supports the current paper framing:

- `snn_sra` is strongest as a low-control, bounded-churn operating point.
- It is not the best raw-delivery method under every topology or every packet-level metric.
- The new `RGG` results and the `TCP` sanity check are the two main places where the framing must stay careful.

If this batch is used in the manuscript, the safest claim is:

"NEURA preserves a strong control-efficiency advantage under severe local stress, while keeping service usable rather than universally best; the new `RGG` and `ns-3` checks tighten realism and boundary-setting rather than turning the result into a universal performance win."
