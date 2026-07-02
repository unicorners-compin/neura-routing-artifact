# NEURA Reproduction Package

This repository contains the reproduction package for:

**NEURA: Local Excitable Routing Control for Stress-Adaptive Networks**

NEURA is the manuscript name of the method. The Python package name
`snn_sra_sim` and a few retained artifact stems such as `snn_*` are internal
development identifiers and do not denote a separate method.

## Repository Contents

- `src/snn_sra_sim/`: maintained Python simulator and routing/control logic.
- `scripts/`: entry points for the formal simulator suite, figure generation,
  ns-3 validation, and result auditing.
- `ns3/`: packet-level ns-3 validation programs used as external checks.
- `results/issue-4/`: curated formal outputs used by the manuscript figures and
  tables, including audit summaries and figure-source data.
- `paper/`: manuscript source files and generated figure panels.

The package intentionally includes the consolidated evidence used for the
submission rather than every exploratory run performed during development.

## Quick Verification

Create an environment with Python 3.10 or newer and install the plotting
dependency:

```bash
python3 -m pip install -r requirements.txt
```

Run the result audit:

```bash
python3 scripts/audit_neura_results.py
```

The expected audited state is 207 passed checks and 0 failed checks for the
curated issue-4 artifacts.

## Regenerate Manuscript Figures

```bash
python3 scripts/generate_ieee_figures.py
```

This rebuilds the figure PDFs under `paper/figures/generated/` from the audited
CSV and JSON inputs under `results/issue-4/artifacts/`.

## Reproduce the Formal Simulator Suite

The main ER-topology formal run can be regenerated with:

```bash
python3 scripts/run_redesigned_issue4_suite.py --issue 4 --profile formal --topology er --jobs 8
```

The spatial-topology validation can be regenerated with:

```bash
python3 scripts/run_spatial_topology_suite.py --issue 4 --jobs 8
```

These commands write outputs under `results/issue-4/`.

## ns-3 Validation

The ns-3 programs are external packet-level validation checks for the simulator
findings. They are not a full production routing protocol implementation.

After preparing an ns-3 environment, check local paths with:

```bash
scripts/check_ns3_env.sh
```

Then run the online ER validation:

```bash
python3 scripts/run_ns3_online_er_validation.py --issue 4
```

Additional sanity checks are available through the `run_ns3_*` scripts and the
matching `build_ns3_*` helpers.

## Paper Build

From `paper/`, build the manuscript with:

```bash
latexmk -pdf -interaction=nonstopmode -halt-on-error neura_v1.tex
```

The cover letter source is also provided in `paper/cover_letter.tex`.
The separate Elsevier highlights file is provided in `paper/highlights.tex`.

## Submission Snapshot

The `v1.0.0` release corresponds to the 2026-07-02 final submission snapshot.
Convenience copies of the submission artifacts are provided under
`results/issue-4/artifacts/`:

- `neura_submission_main.pdf`
- `neura_submission_supplementary.pdf`
- `neura_submission_cover_letter.pdf`
- `neura_elsevier_source_2026-07-01.zip`

## License

This reproduction package is released under the BSD 3-Clause License. See
`LICENSE` for the full terms.
