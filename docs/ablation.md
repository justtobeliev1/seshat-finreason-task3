# Component ablation (paper, Table VI)

Each row reruns the final configuration with one component disabled or altered and counts the
predictions, of 680 per split, that differ from the final ones. These are counts of changed
predictions, not of correct answers. The ablation flags below are opt-in; without them the solver
reproduces the submitted predictions byte for byte (see `README.md`).

Final configuration (`$FINAL`):

```bash
FINAL="--rule-10093 --prefer-10093 --negative-policy modal --observed-member-fallback \
  --standard-member-retry --no-imputation --number-format comma"
python participant/solve.py --input path/to/test_inputs.jsonl \
  --output test_predictions.jsonl --trace test_trace.jsonl $FINAL
```

| Table VI row | Flags (replace `$FINAL` with) | Dev | Test |
|---|---|---:|---:|
| Violation filter, Eq. (1), off | `$FINAL --no-violation-filter` | 1 | 0 |
| ... and general switch, H1, H2 off | `--negative-policy modal --observed-member-fallback --no-imputation --number-format comma --no-violation-filter` | 4 | 2 |
| Path order (ii) before (i) | `$FINAL --path-order cid` | 11 | 10 |
| Path order (iii) before (ii) | `$FINAL --path-order idc` | 6 | 8 |
| Any of the five other orders (union) | `$FINAL --path-order X` for X in `idc cid cdi dic dci` | 17 | 18 |
| General switch off | `$FINAL --no-10093-silent` | 1 | 0 |
| ... only its missing-child trigger off | `$FINAL --no-10093-missing-trigger` | 1 | 0 |
| H1 off | `$FINAL` without `--prefer-10093` | 1 | 1 |
| General switch and H1 off | `$FINAL` without `--rule-10093 --prefer-10093` | 3 | 1 |
| H2 off | `$FINAL` without `--standard-member-retry` | 0 | 1 |
| H3 off | `$FINAL` without `--observed-member-fallback` | 1 | 1 |
| Dimensional imputation on | `$FINAL` without `--no-imputation` | 0 | 1 |
| The three changes made after the diagnostic reverted | `$FINAL` without `--observed-member-fallback --standard-member-retry --no-imputation` | 1 | 3 |
| DQC_0117 guards restored on main path | `$FINAL --restore-0117-guards` | 0 | 2 |

`--path-order` permutes the first three path tiers: `i` a disagreeing roll-up in a declared cube
whose primary items contain the target, `c` a disagreeing calculation with at least two observed
operands, `d` any other disagreeing roll-up. The default is `icd`. A disagreeing calculation with
fewer than two observed operands always follows these three tiers, and when no candidate disagrees
the solver falls back to an agreeing calculation, then an agreeing roll-up
(`--no-agree-fallback` disables this fallback: 0 dev, 1 test prediction changed).

`--restore-0117-guards` is our approximation of the rule 9574 guards that the main path omits; it
is not the code of `participant/dqc0117.py`. For a monetary target it drops a main-path roll-up
whose axis has (a) a multi-dimensional fact of the target without the matching one-dimensional
member fact (checked over all facts on the axis), (b) a fact in a parenthetical cube, or (c) a fact
in a note cube, unless one note value exceeds the reported value. Non-monetary units pass.

Count changed predictions, for example:

```bash
python - <<'PY'
a = open("dev_final.jsonl").readlines(); b = open("dev_ablated.jsonl").readlines()
print(sum(x != y for x, y in zip(a, b)))
PY
```
