# Refresh-aware trophy rarity forecasts

[![make.fun competition](https://make.fun/badge/gta6-trophy-rarity-forecast-01.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01)
[![Rank](https://make.fun/badge/gta6-trophy-rarity-forecast-01/hacker-beep/gta6-trophy-rarity-forecast-01-entry-v2/rank.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01)
[![Reward](https://make.fun/badge/gta6-trophy-rarity-forecast-01/hacker-beep/gta6-trophy-rarity-forecast-01-entry-v2/reward.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01)

An independently authored Python entry for `gta6-trophy-rarity-forecast-01`.
It forecasts five earn-rate quantiles for every trophy using only the supplied
case. The runtime uses the Python standard library, has no network access or
model calls, and needs no installation.

## Run

```sh
MAKEFUN_INPUT_DIR=/path/to/input MAKEFUN_OUTPUT_DIR=/path/to/output \
  PYTHONHASHSEED=0 python3 -P -s main.py
```

The defaults are `/input` and `/output`. Each `*.in` file produces
`<case id>.out` with exactly the competition's `trophies` array. The program
reads all files in filename order, preserves trophy identifiers, sorts output
rows by identifier, and writes compact deterministic JSON. Missing historical
rates are skipped per trophy; a completely unobserved trophy uses a rare-rate
fallback. Output percentages are finite, sorted within each quantile vector,
and rounded to seven decimal places.

## Model

The page refreshes in steps. Repeated identical snapshots are collapsed before
estimation. A trophy's earned count is its rate times the tracked population;
changes in that count estimate progression separately from denominator growth.
A weighted regression of log population on log launch age predicts future
tracked players. A positive acquisition hazard, estimated from changes in
earned counts divided by uncompleted-player exposure, projects earned counts
with eight fixed integration steps. The forecast rate divides that count by
the projected population. Negative count increments caused by rounding or
tracker corrections do not become negative progression hazards.

Uncertainty combines population regression residuals, each trophy's count
volatility, causal one-step forecast errors, horizon, and the size of the
projected rate movement. A scalar calibration of that adaptive width is 0.65.
The quantile distribution mixes a refreshed-page component with an atom at the
last rate, representing the chance that the tracker remains unchanged.
Effectively universal trophies remain universal.

Before any refresh has been observed, player growth and refresh timing cannot
be learned from the case. This branch uses a launch-refresh prior informed by
the supplied rules' public description of the first refresh: roughly sixfold
population dilution, a four-hour lower waiting-time bound, and uncertainty in
both refresh timing and trophy progression. Description and trophy tier give
weak progression-time priors. There are no stored target rates, case-specific
answer tables, or special branches on practice IDs. The cold-start branch is
explicitly prior-driven; its success on unchanged snapshots cannot establish
its accuracy across a refresh.

Runtime work is a linear pass over reads and trophies, followed by a fixed
amount of work per trophy. There are no per-trophy optimization searches.
`evaluate.py` is a development tool and is not imported by the entrypoint.

## Reproduce validation

```sh
python3 evaluate.py --kit /path/to/unpacked/kit --calibrate
```

Use Python 3.13 for the closest local comparison to the grader. Reports and
small test artifacts go under ignored `artifacts/`. The evaluator runs each
practice case in its own subprocess twice with the grader's `-P -s` flags,
checks byte identity and every output constraint, and enforces an eight-second
timeout. It also tests null/missing observations, extreme rates, zero horizon,
and 721 reads covering five days with a subsequent 6,510-minute forecast.

An optional `--answers path.json` accepts a released JSON mapping from case ID
to its list of actual rates, in input trophy order. This is not the sealed
reference file's native wrapper; extract that mapping when actual rates become
available. Without actual rates the evaluator reports practice accuracy as
unavailable rather than estimating it from the initial page.

## Results and limits

Tested locally on Python **3.13.18**, also cross-checked on Python 3.12.3.
Mean pinball losses, including the contest's factor of two:

| Evaluation | This model | Persistence | Curve replica |
| --- | ---: | ---: | ---: |
| 15 real history-prefix splits | 0.000000 | 0.018939 | 0.018939 |
| 45 synthetic cohort splits | 0.304184 | 0.864981 | 0.483617 |
| Later 48-hour synthetic cutoffs, 15 splits | 0.181268 | 0.499218 | 0.247236 |

The real prefixes all contain the **same unchanged launch snapshot** and are
overlapping, not independent evidence of progression accuracy. Synthetic
cohorts are independently simulated using lognormal completion delays and
three player-growth shapes, but they are **not real GTA target observations**.
The curve is an independently implemented replica of the published formula;
the house curve source is not distributed. The model loses to that curve on
16 of the 45 synthetic splits, despite winning in aggregate. Synthetic 90%
coverage is 90.55%. See [BACKTEST.md](BACKTEST.md) for calibration, individual
losses, and limitations.

| Actual practice execution | Rows | Bytes | Run 1 | Run 2 | Same bytes |
| --- | ---: | ---: | ---: | ---: | --- |
| p00 | 55 | 3,036 | 0.0644 s | 0.0642 s | yes |
| p01 | 55 | 3,299 | 0.0641 s | 0.0641 s | yes |
| Five-day / 721-read stress case | 55 | 3,848 | 0.1143 s | 0.1146 s | yes |

These are local wall times including process startup, not official grader
measurements. Both practice outputs satisfy the exact schema and size cap.
**The kit intentionally omits actual practice target rates. No p00/p01 target
loss or claim of beating the house on those targets is reported.** The rules
state that both house baselines score approximately 1.51 there; that is not a
measured score for this entry. The house model forecast and future final
rates are also unavailable.

The kit's local `check` passes with the genuine recorded session bundled by
the kit, including authorship, revision, transcript, and run-record checks.
Checks deferred to submission still apply. This repository was created
**2026-11-19 12:47:06 UTC**, after the competition opened at **11:30:00
UTC**, so it satisfies the repository-creation requirement. The transcript
bundle is kept locally under ignored `artifacts/`, not published in the
source repository.

Licensed under MIT; see [LICENSE](LICENSE).
