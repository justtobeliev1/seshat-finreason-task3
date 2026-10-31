# Submission log

## Development phase

The development server scored three submissions per team per day and reported scores per rule.
Counts are rule-judged correct answers per rule (DQC_0015: 162, DQC_0117: 54, DQC_0126: 464 cases).
"Official" is the score of the language-model judge (`gpt-5-mini`, minimal reasoning effort).

| | Configuration | 0015 | 0117 | 0126 | Rule-judged (%) | Official (%) |
|---|---|---:|---:|---:|---:|---:|
| S1 | first full version | 147 | 47 | 464 | 96.76 | 96.62 |
| S2 | S1 + name normalization + same-cube guard + two experimental policies | 148 | 45 | 464 | 96.62 | 96.32 |
| S3 | S1 + name normalization | 148 | 47 | 464 | 96.91 | 96.76 |
| S4 | S3 + same-cube guard | 148 | 47 | 464 | 96.91 | 96.62 |
| S5 | S3 + thousands separators + second DQC_0117 assertion + *first* | 151 | 49 | 464 | 97.65 | 97.94 |
| S6 | S5 with *last* + observed-member fallback | 148 | 49 | 464 | 97.21 | 97.50 |
| S7 | S5 + ignore exemptions + lone-10093 priority | 146 | 50 | 464 | 97.06 | 97.93 † |
| S8 | diagnostic probe on S5 + lone-10093 priority (21 answers replaced by 0) | 143 | 50 | 464 | 96.62 | 97.91 † |
| S9 | S5 + lone-10093 priority + fallbacks + no imputation | 151 | 50 | 464 | 97.79 | 97.94 |
| S10 | **final**: S9 with the most frequent value | 151 | 50 | 464 | 97.79 | 97.94 |

† Cases without a valid judge label are removed from the official denominator: 5 for S7, 9 for S8.

Notes:

- The ranked files are sorted by rule. We used this only to see which rule's cases a change touched
  and, after the competition, to check rule identification. No prediction uses labels, external data,
  a case identifier or its position.
- S8 replaced the answers of the 19 ambiguous nonnegativity cases and two roll-up cases with zero to
  count the correct answers in each subset (8 of 19 and 0 of 2). Its counts enter no prediction.
- After S8, three changes (standard-member retry, observed-member fallback, no dimensional imputation)
  came from reviewing the rule source and the unlabelled test inputs for answers no rule can produce.
  Each changed one test prediction; none changed the development score.

## Test phase

The test phase accepted repeated uploads, returned no scores, and ranked the last upload.
We uploaded five prediction files. The ranked one is the final configuration, with SHA-256
`16abbf035b2871d45be3ded178b97f9b220f1f4de79ab18c087a02f16cad4c8c`.
Final result: 97.21% (DQC_0015 91.98%, DQC_0117 88.89%, DQC_0126 100.00%), 4th of 23 teams.
