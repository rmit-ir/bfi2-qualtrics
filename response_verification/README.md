# Response quality verification

Post-hoc careless-responding detection for Qualtrics **response** exports of the
BFI-2 surveys in `../output/`. `verify_responses.py` reads an export, runs
per-respondent checks, and writes the data back with one boolean `flag_*` column
per check, the underlying metric columns, and a joined `Careless_Flags` summary
column.

The battery implements the post-hoc methods reviewed by **Ward & Meade (2023)**
through their "moderate" screening tier — invariability, outlier, and multiple
consistency indices — plus the BFI-2-specific **DRIP** inconsistency scale
(**Ruchensky, Edens, & Donnellan, 2025**). Both papers are in this directory.

This is the one part of the repo with third-party dependencies; the surveys and
the rest of the tooling are stdlib-only.

## Install & run

```
uv pip install -r requirements.txt       # pandas, numpy, scipy
python3 verify_responses.py <export.csv|export.tsv> [-o flagged.csv]
```

Uses [uv](https://docs.astral.sh/uv/) to manage the packages; plain
`pip install -r requirements.txt` works too.

The input is a standard Qualtrics response export with its 3-row header (column
names / question text / `{"ImportId": ...}`). Both export flavours are handled
automatically:

- **CSV or TSV** — inferred from the file extension (`.tsv`/`.tab` → tab, else
  comma). Encoding is sniffed from the byte-order mark, so Qualtrics' UTF-8 CSV
  and UTF-16 TSV both load without any flags.
- **Choice labels or numeric values** — inferred from the item cells, so an
  export made with either "Use choice text" (`Agree strongly`) or "Use numeric
  values" (`5`) recodes correctly.

The form (Full / Short / Extra-short) is auto-detected from the item-column
prefix (`BFI-2_`, `BFI-2-S_`, `BFI-2-XS_`). Output defaults to
`<input>_flagged.csv`. The startup line reports what was detected, e.g.
`Input: TSV (utf-16), numeric values.`

Flagging is advisory — every respondent is kept; flags mark rows worth
reviewing. Ward & Meade recommend treating indices as dichotomous flags, never
relying on a single indicator, and (when power matters) reporting analyses with
and without flagged respondents. The script exits 0 whether or not anything is
flagged.

## Checks

Grouped per Ward & Meade's taxonomy. "Full/Short/XS" = which forms support the
check.

| Flag | Metric | Method | Forms |
|---|---|---|---|
| `flag_longstring` | `longstring_max` | **Longstring** (Johnson 2005): longest run of identical consecutive raw answers ≥ 10. Catches straight-lining. | all |
| `flag_variance` | `response_sd` | **Within-person variability** (Marjanovic et al. 2015; Dunn et al. 2018): SD of raw answers < 0.5. Catches patterned invariance (`2,3,2,3,…`) that longstring misses. | all |
| `flag_mahalanobis` | `mahal_d2` | **Mahalanobis distance**: D² vs. the reference sample beyond χ²(df = n_items, 1 − α), α = .001. Catches multivariate outliers, incl. random responders. Skipped when reference N ≤ n_items. | all |
| `flag_psychsyn` | `psychsyn_r` | **Psychometric synonyms** (Meade & Craig 2012): item pairs correlating ≥ .60 in the reference sample (on reverse-keyed scores, so antonyms fold in); within-person correlation across pairs < 0.22. Skipped when reference N < 30 or < 5 pairs form. | all |
| `flag_evenodd` | `evenodd_r` | **Even-odd consistency / personal reliability** (Johnson 2005): each facet split into odd/even item halves, half-scores correlated within person, Spearman-Brown corrected; flagged < 0.30. | Full, Short (XS facets are single items) |
| `flag_persontotal` | `person_total_r` | **Person-total correlation** (Curran 2016): keyed responses vs. the leave-one-out per-item sample mean; flagged < 0. Skipped when reference N < 10. | all |
| `flag_drip` | `drip_score` | **DRIP** (Ruchensky et al. 2025): sum of \|item1 − item2\| over 15 highly correlated item pairs, reverse-keyed first; flagged ≥ 14 (paper's recommended cut range: 14–17; genuine-responder M ≈ 9, SD ≈ 4; AUC .91–.99 vs. random data). | Full only (needs all 60 items) |
| `flag_speed` | — | **Response time** < 2 s/item (Bowling et al. 2016). The surveys are a single page, so total `Duration (in seconds)` is effectively the page-level timing Ward & Meade recommend. | all |
| `flag_incomplete` | — | Any unanswered item. Rows with missing items are excluded from checks that need the full vector rather than crashing them. | all |

**Sequential removal** (Ward & Meade's "extensive" tier): rows flagged by either
invariability check are excluded from the *reference statistics* — the
Mahalanobis centroid/covariance, the synonym-pair correlations, and the
person-total means — and then every row (including the invariant ones) is
scored against those cleaner references. This stops straight-liners from
dragging the sample centroid toward themselves.

**Complementary blind spots** (why several checks): DRIP and the other
consistency indices score a straight-liner as perfectly *consistent* (all-"3"
→ DRIP = 0) — Ruchensky et al. call this out explicitly — while the
invariability checks catch exactly that case and say nothing about random
responding. Speed is independent of both.

## Known limitations

- **Longstring vs. item randomization.** `../output/BFI-2_Full.qsf` (and
  the other forms) set `Randomization.Type: "All"` on the matrix
  question — Qualtrics shows each respondent the items in a different
  order. `flag_longstring` computes its "consecutive identical answers"
  run over the exported columns in **published item-number order**, not
  the order that respondent actually saw — those aren't the same
  sequence per respondent. This can distort the metric in **either
  direction**: a genuinely consecutive on-screen run can get scattered
  across item-number columns (understating true straight-lining), and
  conversely, items that are adjacent in item-number order but were
  *not* shown consecutively can coincidentally share an answer by chance
  (inflating `longstring_max` for an attentive respondent — a possible
  false positive). Reconstructing true display order would need
  Qualtrics to export per-response item ordering, which isn't currently
  captured; disabling randomization would remove a real methodological
  benefit (order-effect control) for an uncertain gain in one check's
  precision. Neither change is made unilaterally here — flagging it for
  whoever calibrates this battery to decide, and treating `flag_longstring`
  as one input among several (as the rest of this battery already does)
  rather than a precise measurement on its own.
- **Mahalanobis "skipped" vs. "stable."** `flag_mahalanobis` runs as soon
  as the reference sample exceeds `n_items` (61 responses for 60 items) —
  the minimum for the covariance matrix to be invertible at all, not for
  it to be a *stable* estimate (rules of thumb for that typically want
  several times `n_items`). Treat this check as more trustworthy the
  larger the reference sample is above that bare minimum, particularly
  early in data collection.
- **CSV output escapes the *original* export's cells against spreadsheet-
  formula injection** (a leading `=`, `+`, `-`, `@`, tab, or CR is
  prefixed with `'`) but does not, and cannot, prevent the same risk if
  *you* later open the input export directly in Excel/Sheets — that risk
  exists independent of this script.

## Method coverage

Every BFI-2-relevant method identified in the two source papers, and where
this tooling stands on each. Ward & Meade (2023) tier labels refer to their
Table 1 screening recommendation (*minimal* → *moderate* → *extensive*);
"W&M" = Ward & Meade (2023), "R+25" = Ruchensky et al. (2025).
Instrument-specific inconsistency scales for other measures (MMPI VRIN/TRIN,
PAI INC, HEXACO BRIE/HIRT, TriPM TAPIR — discussed in R+25 as precedents) and
lab-only techniques (eye tracking, W&M) are omitted as not applicable to a
BFI-2 Qualtrics export.

### Post-hoc methods (computable from the export)

| Method | Short description | Source (tier) | Status |
|---|---|---|---|
| Longstring | Longest run of identical consecutive answers | W&M (minimal), after Johnson 2005 | ✅ `flag_longstring` |
| Within-person variability | SD/variance of a person's answers; low = invariant, incl. alternating patterns longstring misses | W&M (minimal), after Marjanovic et al. 2015 / Dunn et al. 2018 | ✅ `flag_variance` |
| Response time | Completion faster than 2 s/item, timed per page | W&M (minimal), after Bowling et al. 2016 | ✅ `flag_speed` — surveys are one page, so total duration *is* the page time |
| Mahalanobis distance | Multivariate distance from the sample centroid, χ² cutoff | W&M (minimal) | ✅ `flag_mahalanobis` |
| Psychometric synonyms, empirically paired | Within-person correlation across item pairs that correlate ≥ .60 in the sample | W&M (moderate), after Meade & Craig 2012 | ✅ `flag_psychsyn` |
| Semantic synonyms / antonyms, theoretically paired | Same idea with theory-chosen pairs; antonyms reverse-coded first | W&M, after Kurtz & Parrish 2001 / Johnson 2005 | ✅ covered: pairing runs on reverse-*keyed* scores, so antonym pairs surface as synonyms; DRIP (below) is the published theory-grounded pair set for the BFI-2 |
| Even-odd consistency (personal reliability) | Facet-wise odd/even half scores correlated within person, Spearman-Brown corrected | W&M (moderate), after Johnson 2005 | ✅ `flag_evenodd` (Full & Short; XS facets are single items) |
| Person-total correlation | Person's answers vs. the leave-one-out per-item sample mean | W&M, after Curran 2016 | ✅ `flag_persontotal` |
| DRIP | Sum of \|differences\| over 15 highly correlated BFI-2 item pairs, reverse-keyed first; cut range 14–17 | R+25 | ✅ `flag_drip` (Full form) |
| Sequential removal | Exclude invariant responders before computing sample-based statistics | W&M (extensive) | ✅ built-in: longstring/variance-flagged rows are dropped from the Mahalanobis, synonym-pairing, and person-total references |
| Response-option frequency | Count of each response option chosen, as another invariability signal | W&M | ❌ not implemented — redundant with SD + longstring for a 5-point single-scale survey |
| Polytomous Guttman errors | Flags agreeing more with a rarer (more extreme) item than a more popular one | W&M, after Curran 2016 | ❌ not implemented — no published cutoff |
| Person-fit IRT statistics | Likelihood of the response pattern under an IRT model | W&M (extensive), see Beck et al. 2019 | ❌ not implemented — requires model fitting with no generalizable cut scores |
| Resampling | Recompute split-based indices over many random splits and average | W&M (extensive) | ❌ not implemented — refinement of even-odd, not a distinct detector |
| Latent class / mixture modeling | Classify respondents from several indices jointly; W&M call it the most promising approach | W&M (extensive), after Meade & Craig 2012 | ❌ not implemented — modeling choices are study-specific. The metric columns this script emits (`longstring_max`, `response_sd`, `mahal_d2`, `psychsyn_r`, `evenodd_r`, `person_total_r`, `drip_score`) are exactly the indicator inputs; run downstream, e.g. with the R package [`careless`](https://cran.r-project.org/package=careless) |

### A-priori / survey-design methods (require survey changes)

These cannot be computed post-hoc; the surveys in `../output/` deliberately
contain none of them, matching the published BFI-2 forms. Listed here as
design options if you customize the surveys — if you add any, keep them out of
the scored `BFI-2*` matrix question so the scoring and this script are
unaffected.

| Method | Short description | Source (tier) | Status |
|---|---|---|---|
| Instructed-response items | "Please select *Disagree strongly* for this item"; misses flag inattention | W&M (minimal) | ➖ design-time only |
| Bogus items | Logically impossible statements (agreement flags carelessness); W&M warn of false positives from diligent respondents | W&M (moderate: optional; extensive: required) | ➖ design-time only |
| Infrequency scale (CIFR) | Items with near-universal or near-zero base rates; unlikely answers flag carelessness. R+25 used the 3-item CIFR-Short-RED interspersed between BFI-2 items 9/10, 32/33, 51/52 | R+25, after Kay & Saucier 2023 | ➖ design-time only |
| Self-report seriousness check | End-of-survey "should we use your data?" item; honesty not guaranteed when compensated | W&M; used to pre-screen R+25 samples 2–3 (after Aust et al. 2013) | ➖ design-time only |
| Page-level response timing | Capture duration per page rather than per survey, to avoid false negatives from breaks | W&M (minimal) | ➖ moot here: each survey is a single page, so Qualtrics' total `Duration (in seconds)` already is the page-level time used by `flag_speed` |

### Thresholds

Overridable on the CLI:

```
--longstring-run N       longstring run cutoff             (default 10)
--sd-cutoff X            within-person SD floor            (default 0.5)
--mahal-alpha A          Mahalanobis chi-square alpha      (default 0.001)
--syn-pair-r R           sample r to pair synonym items    (default 0.60, Meade & Craig 2012)
--syn-cutoff R           within-person synonym r floor     (default 0.22)
--evenodd-cutoff R       even-odd consistency floor        (default 0.30)
--persontotal-cutoff R   person-total correlation floor    (default 0.0)
--drip-cutoff N          DRIP score cutoff                 (default 14; paper range 14-17)
--sec-per-item S         speed threshold, seconds/item     (default 2.0, Bowling et al. 2016)
```

Sourced defaults are cited; the rest are advisory heuristics — Ward & Meade
note that generalizable cut scores for the consistency indices are elusive
(Yentes 2020), so calibrate them per sample and pre-register the decision
rule.

## DRIP pair table

`drip_item_pairs.tsv` is the single source of truth for the 15 DRIP pairs
(columns: `Pair, Domain, Facet, Item1, Item2, Statement1, Statement2,
Reverse1, Reverse2, r_S1, r_S2, r_S3`). It is generated from Table 1 of
Ruchensky et al. (2025) **by BFI-2 item number** (item # = ChoiceID in
`../output/BFI-2_Full.qsf`); statement text and reverse flags come from the
splitter's `master_mapping.json`, so they match the survey verbatim. At load
time the script cross-validates every pair against `master_mapping.json`
(facet, domain, and reverse-flag agreement) and aborts on any mismatch. Don't
hand-edit statement text here — regenerate from the item numbers instead.

## Tests

```
python3 -m unittest discover ../tests        # or from repo root
```

`tests/test_verify_responses.py` checks the pair-table integrity, runs the
detector against synthetic full-form exports (CSV/label, CSV/value, and
UTF-16 TSV/value) with engineered clean / straight-lining / inconsistent /
speeding respondents, and — when present — against the checked-in
fake-response TSV in `sample_data/`. The detector tests skip cleanly when
pandas/numpy/scipy aren't installed.

`sample_data/` holds a small **fabricated** full-form TSV export (all careless
responses) used only as a test fixture and a worked example; it is not real
respondent data. The raw Qualtrics `*.zip` downloads are gitignored.

## References

The two methods papers (both in this directory as PDFs, gitignored):

> Ward, M. K., & Meade, A. W. (2023). Dealing with careless responding in
> survey data: Prevention, identification, and recommended best practices.
> *Annual Review of Psychology, 74*, 577–596.
> https://doi.org/10.1146/annurev-psych-040422-045007

> Ruchensky, J. R., Edens, J. F., & Donnellan, M. B. (2025). Development of an
> inconsistent responding scale for the Big Five Inventory-2. *Journal of
> Personality Assessment, 107*(3), 384–391.
> https://doi.org/10.1080/00223891.2024.2411557

Method-level primary sources cited above (Johnson 2005; Meade & Craig 2012;
Marjanovic et al. 2015; Bowling et al. 2016; Curran 2016; Dunn et al. 2018;
Yentes 2020) are reviewed in Ward & Meade (2023).

The BFI-2 itself is the work of Christopher J. Soto and Oliver P. John — see
the repo root `README.md`.
