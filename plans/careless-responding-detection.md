# Careless-responding detection for Qualtrics BFI-2 exports

> **Status: superseded / historical.** This is the original build plan for
> `verify_responses.py` and predates the later "moderate tier" additions
> (within-person SD, psychometric synonyms, even-odd consistency,
> person-total correlation) — the "Checks" section below lists only 4
> checks; the shipped tool has 8+. It also documents a since-fixed TSV
> corruption as a still-open problem. For the current, accurate check
> list and thresholds, see `response_verification/README.md`. Kept here
> as a historical record of the DRIP-pair verification work (the "Verified
> facts" section below is still the record of that verification), not as
> a live spec.

## Context

The repo ships three self-scoring BFI-2 Qualtrics surveys (`output/*.qsf`). The goal
is a pandas-based verification script that reads a Qualtrics response-data CSV export
and flags careless responding per participant, appending a `Careless_Flags` column.
Scope decisions already made:

- **Post-hoc checks only** — no instructed-response/bogus items or page timers (the
  surveys contain none). The always-available total-`Duration` speed check IS included.
- **DRIP pairs come from the original paper**, not the corrupted TSV. The paper
  (Ruchensky et al., *Development of an Inconsistent Responding Scale for the BFI-2*,
  in `response_verification/`) was read; its Table 1 defines the 15 pairs by **BFI-2
  item number**, and all 15 validate against `master_mapping.json` (same facet, items
  present). The existing TSV's statement-text columns are corrupted (wrong/foreign
  statements on pairs 1, 2, 5, 7, 8, 11, 15; pairs 9/14 shown as duplicates) and must
  be regenerated from the paper.

### Verified facts driving the design

- **Correct DRIP pairs** (paper Table 1, `(item#, item#)`, item# = ChoiceID in the
  Full qsf): 1:(54,39) 2:(35,20) 3:(31,16) 4:(46,1) 5:(56,41) 6:(33,3) 7:(49,34)
  8:(51,21) 9:(44,29) 10:(43,13) 11:(53,38) 12:(52,7) 13:(58,28) 14:(59,14)
  15:(60,15). At least one pair per domain.
- Paper note: *"Items were reverse-keyed consistent with the scoring key"* → recode
  reverse items (6-x) **before** taking |differences|. Reverse flags come from
  `master_mapping.json`.
- Paper cut scores: genuine mean DRIP ≈ 9.07 (SD 3.95); scores **14–17** recommended
  for exclusion; AUC .913–.990. The requested ≥14 threshold matches the paper.
- Qualtrics CSV export format (verified against a real export earlier): 3 header
  rows (labels / question text / `{"ImportId":"QID2_1"}`), item columns named
  `<DataExportTag>_<ChoiceID>` (`BFI-2_1`…`BFI-2_60`), cell values are answer **text**
  ("Disagree strongly"…"Agree strongly", with possible nbsp/whitespace), plus
  `Duration (in seconds)` and scoring columns SC0…. Item number = ChoiceID =
  published item number.
- DRIP is defined for the **full form only** (needs all 60 items); on Short/XS
  exports the check is skipped with a notice. Longstring/Mahalanobis/speed work on
  any form.

## Deliverables

### 1. `response_verification/drip_item_pairs.tsv` — regenerate from the paper

Replace `Designated Item Pairs for the BFI-2 DRIP Scale - Table 1.tsv` with a
corrected table (new canonical name `drip_item_pairs.tsv`, delete the old file):
columns `Pair, Domain, Facet, Item1, Item2, Statement1, Statement2, Reverse1,
Reverse2, r_S1, r_S2, r_S3`. Item numbers and correlations from paper Table 1;
statements and reverse flags pulled from `master_mapping.json` so text matches the
survey verbatim. The script reads this file (single source of truth for the pairs).

### 2. `response_verification/verify_responses.py` — the detector

Pandas script, CLI: `python3 verify_responses.py <export.csv> [-o flagged.csv]`
plus threshold overrides (`--longstring-run`, `--drip-cutoff`, `--sec-per-item`,
`--mahal-alpha`).

Structure:

- **Load**: read CSV with 3 header rows (row 0 = column names, skip rows 1–2).
  Auto-detect the form by item-column prefix (`BFI-2_` / `BFI-2-S_` / `BFI-2-XS_`),
  sort item columns numerically by suffix.
- **Recode**: map answer text → 1–5 via a normalizer (collapse whitespace/nbsp,
  case-insensitive), same normalization idea as `split_bfi2.py:normalize_text`.
  Unanswered cells → NaN. A row with any missing item also gets `flag_incomplete`
  (checks that need the full vector are skipped for it rather than crashing).
- **Checks** (each adds a boolean `flag_*` column):
  - `flag_speed`: `Duration (in seconds)` < 2 × n_items.
  - `flag_longstring`: max consecutive-identical run on **raw** (pre-reverse)
    responses ≥ 10 (default). Also emit a `longstring_max` column.
  - `flag_mahalanobis`: Mahalanobis distance of the raw item vector from the sample
    centroid, D² > χ²(df = n_items, 1 − α), default α = .001. Covariance via
    numpy (`np.cov` + `np.linalg.pinv` for stability); χ² critical value via
    `scipy.stats.chi2.ppf`. Skipped with a warning when N ≤ n_items (covariance
    unstable). Emit `mahal_d2`.
  - `flag_drip` (full form only): reverse-key per `master_mapping.json`, sum
    |item1 − item2| over the 15 pairs, flag if ≥ 14 (paper cutoff). Emit
    `drip_score`.
- **Output**: original data + metric columns + flag columns + `Careless_Flags`
  (comma-joined names of triggered flags, empty when clean). Print a per-flag count
  summary and write the CSV. Exit code 0 either way (flagging isn't an error).

Dependencies: pandas, numpy, scipy — new for this repo (currently stdlib-only), so:

### 3. `response_verification/requirements.txt`

`pandas`, `numpy`, `scipy` (unpinned, one per line). Root stays dependency-free;
docs note the requirements file applies only to the verification script.

### 4. Test — `tests/test_verify_responses.py`

Stdlib unittest (repo convention, discoverable by `python3 -m unittest discover
tests`); `skipUnless(pandas available)`. Build a synthetic full-form export
in-memory/tempfile with the real 3-row header shape and answer-text cells:
a clean respondent (no flags), a straight-liner (60-run → flag_longstring, and
DRIP = 0 → NOT drip-flagged, proving flags are independent), a random responder
(seeded values chosen to guarantee DRIP ≥ 14), and a too-fast respondent
(flag_speed). Assert `Careless_Flags` contents per row. Also assert the 15 pairs
in `drip_item_pairs.tsv` all reference valid item numbers, match facets, and agree
with `master_mapping.json` reverse flags.

### 5. Docs

- `README.md`: new "Verifying response quality" section — what the script flags,
  how to run it, the DRIP citation (Ruchensky et al.) alongside the existing
  Soto & John credit; the sums-vs-means note stays untouched.
- `CLAUDE.md`: add `response_verification/` to repository contents; note the DRIP
  pair table is generated from the paper's Table 1 by item number (statement text
  in older TSVs was unreliable); note pandas/numpy/scipy are needed only for this
  script.

## Files touched

| File | Action |
|---|---|
| `response_verification/drip_item_pairs.tsv` | new (replaces corrupted Table 1 TSV, which is deleted) |
| `response_verification/verify_responses.py` | new |
| `response_verification/requirements.txt` | new |
| `tests/test_verify_responses.py` | new |
| `README.md`, `CLAUDE.md` | edit |

Unchanged: `output/*.qsf`, splitter skill, qsf-tools skill, existing test.

## Verification

1. `python3 -m unittest discover tests` — all pass (new test included; qsf test
   unaffected).
2. Pair-table integrity: the script cross-validates `drip_item_pairs.tsv` against
   `master_mapping.json` at load time (facet + reverse-flag consistency) and aborts
   on mismatch.
3. End-to-end: run `verify_responses.py` on the synthetic fixture CSV from the test
   and on a real Qualtrics export — confirm clean rows are unflagged and a
   hand-computed DRIP score for one row matches `drip_score`.
