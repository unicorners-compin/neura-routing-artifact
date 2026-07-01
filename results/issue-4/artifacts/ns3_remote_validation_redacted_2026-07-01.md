# Remote ns-3 Validation

Date: 2026-07-01

This note records an external remote validation of the public NEURA reproduction
package on a Linux host with a source-built ns-3.43 installation.

Environment:

- Python: 3.10
- g++: 11.x
- cmake: 3.22
- ns-3: 3.43, discovered through `NS3_ROOT` and pkg-config

Repository snapshot:

- public GitHub repository: `https://github.com/unicorners-compin/neura-routing-artifact`
- verified public commit: `2e52a9d`

Checks run:

```bash
NS3_ROOT=<ns-3.43-root> bash scripts/check_ns3_env.sh
NS3_ROOT=<ns-3.43-root> python3 scripts/run_ns3_online_er_validation.py --issue 906 --seeds 51 --methods neura,triggered_te,ospf_te --scenarios hotspot,repeated --nodes 24 --target-flows 10 --sim-seconds 12 --link-rate-mbps 10 --queue-packets 20
NS3_ROOT=<ns-3.43-root> python3 scripts/run_ns3_queue_signal_sanity.py --issue 906
NS3_ROOT=<ns-3.43-root> python3 scripts/run_ns3_tcp_goodput_sanity.py --issue 906
NS3_ROOT=<ns-3.43-root> python3 scripts/run_ns3_replay_minimal.py --issue 906 --scenario hotspot --methods snn_sra,triggered_te,ospf_te --nodes 24 --ticks 60 --seed 51 --link-rate-mbps 10 --queue-packets 100
```

Result:

- ns-3.43 was detected successfully through `NS3_ROOT`.
- The online ER validation completed for both `hotspot` and `repeated`.
- The queue-signal sanity check completed and reports `neura`.
- The TCP-goodput sanity check completed and reports `neura`.
- The replay validation completed for `snn_sra`, `triggered_te`, and `ospf_te`.

Representative online-validation rows:

```text
hotspot,neura,1,1.0,0.0,11.76424,8.550543,10.0,0.0,0.00176,0.0033,10.0,72.0
hotspot,triggered_te,1,1.0,0.0,11.76424,7.255662,20.0,10.0,0.00352,0.0066,10.0,72.0
hotspot,ospf_te,1,1.0,0.0,11.76424,7.257567,20.0,10.0,0.515008,0.96564,10.0,72.0
repeated,neura,1,1.0,0.0,11.76424,8.550543,10.0,0.0,0.00176,0.0033,10.0,72.0
repeated,triggered_te,1,1.0,0.0,11.76424,8.048123,40.0,20.0,0.00704,0.0132,10.0,72.0
repeated,ospf_te,1,1.0,0.0,11.76424,8.050036,40.0,20.0,0.518528,0.97224,10.0,72.0
```

Conclusion:

The public reproduction package can compile and run the ns-3 validation
programs against a source-built ns-3.43 installation.
