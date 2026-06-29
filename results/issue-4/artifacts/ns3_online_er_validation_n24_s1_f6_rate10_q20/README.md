# ns-3 Online ER Validation

Medium-scale packet-level online cross-validation for the NEURA paper.
This is not a full distributed protocol-stack implementation; it is an online ns-3 validation where source host routes are updated from sampled DropTail queue pressure.

- nodes: 24
- seeds: 51
- target flows per run: 6
- methods: neura, triggered_te, ospf_te
- scenarios: hotspot, repeated
- link rate: 10 Mbps
- queue: 20 packets

Main outputs:

- `summary.csv`: aggregate means and confidence intervals
- `detail.csv`: per-method, per-scenario, per-seed run metrics
- `*_summary.json`: raw per-run ns-3 metrics
- `*_timeline.csv`: sampled controller timeline
