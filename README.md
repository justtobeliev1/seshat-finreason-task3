# Seshat: finding the flagged computation in XBRL filings

English | [中文](README.zh.md)

Code for **Seshat**, the system of team **FinProbeBench** for
[FinReason Cup 2026](https://the-finai.github.io/IEEE-bigdata-cup/) Task 3 (Financial Audit Verification).
It accompanies the Working Notes paper
*Seshat at FinReason Cup 2026 Task 3: Finding the Flagged Computation in XBRL Filings under Hidden Rules*.

Given an excerpt of an SEC XBRL filing, a concept and a period, Task 3 asks for the value the filing
reports and the value its own relationships imply. Each case is a finding of one of three hidden
XBRL US Data Quality Committee (DQC) rules (DQC_0015, DQC_0117, DQC_0126). Seshat recovers structured
evidence from the excerpt, forms candidate computations, and selects among them with two conditions
the task supplies: the scope of the published rule (context constraints) and the fact that every
case is a finding (violation constraint). It calls no language model.

| | Result |
|---|---|
| Test (official judge) | 97.21%, 4th of 23 teams |
| Development (official judge) | 97.94% |
| Rule inferred correctly | 680/680 development, 680/680 test |
| Runtime | about 2.5 s for 680 cases on one laptop CPU core |

## Requirements

Python 3.10 or later. The solver uses only the standard library; the tests need `pytest`.

## Data

The competition data are **not** included. Obtain the development and test inputs
(`development_inputs.jsonl`, `test_inputs.jsonl`) from the
[official FinReason Cup repository](https://github.com/The-FinAI/IEEE-bigdata-cup) under its terms.

## Reproducing the final submission

```bash
python participant/solve.py \
  --input path/to/test_inputs.jsonl \
  --output test_predictions.jsonl \
  --trace test_trace.jsonl \
  --rule-10093 --prefer-10093 --negative-policy modal \
  --observed-member-fallback --standard-member-retry \
  --no-imputation --number-format comma
```

With the official inputs this reproduces the submitted files byte for byte:

| Split | SHA-256 of the predictions |
|---|---|
| Development | `77754095288d801b85c1776c4a6fcef196a1276b5850ce56cb2ceb8e5b879004` |
| Test | `16abbf035b2871d45be3ded178b97f9b220f1f4de79ab18c087a02f16cad4c8c` |

Each output line is `{"id": ..., "extracted_value": ..., "calculated_value": ...}`.
The trace file records, for every case, the candidate computations, the selected one, any candidate
it replaced, and flags for missing operands and remaining ambiguity.

Run the tests with:

```bash
python -m pytest -q participant
```

## Final configuration

| Flag | Effect |
|---|---|
| `--rule-10093` | evaluate the second DQC_0117 assertion when the first does not fire |
| `--prefer-10093` | let a lone second-assertion finding replace a first-assertion one (heuristic) |
| `--negative-policy modal` | for several violating DQC_0015 values, return the most frequent |
| `--observed-member-fallback` | roll up the observed members when no candidate resolves |
| `--standard-member-retry` | if an undeclared roll-up equals the total, keep only standard-taxonomy members |
| `--no-imputation` | do not fill missing calculation children from dimensional facts |
| `--number-format comma` | print numbers with thousands separators |

All flags are off by default. Other flags in `solve.py` are experiments that the final
configuration does not use.

## Layout

```
participant/
  solve.py                 candidate generation, constraints, path precedence, switching, output
  xbrl.py                  parser that keeps the closed elements of truncated sections
  dqc_rules.py             DQC_0015 registries and exemptions, DQC_0117 roll-up axes
  dqc0117.py               the two DQC_0117 assertions (9574 and 10093)
  dqc_nonnegative.json     per-taxonomy-year nonnegativity registries (see NOTICE)
  dimension_projection.py  experimental, disabled in the final configuration
  reconstruction.py        experimental, disabled in the final configuration
  test_*.py                unit tests
docs/
  submission_log.md        all development submissions and test uploads
```

## Citation

```bibtex
@inproceedings{finprobebench2026seshat,
  title     = {Seshat at {FinReason Cup 2026} Task 3: Finding the Flagged Computation in {XBRL} Filings under Hidden Rules},
  author    = {TODO},
  booktitle = {2026 IEEE International Conference on Big Data (BigData), FinReason Cup Working Notes},
  year      = {2026}
}
```

## License

The code is released under the MIT License (see `LICENSE`). `participant/dqc_nonnegative.json` is
derived from the DQC rule resources of XBRL US and is distributed under their terms (see `NOTICE`).
