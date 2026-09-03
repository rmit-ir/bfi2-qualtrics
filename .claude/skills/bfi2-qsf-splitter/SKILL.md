---
name: bfi2-qsf-splitter
description: Splits a unified BFI-2 Qualtrics survey (.qsf) into three separate files (Full, Short, Extra-Short), injecting domain- and facet-level automatic scoring (with reverse-coding) by matching item statement text against a master key. Use when asked to split, export, or add scoring to the BFI-2/BFI-2-S/BFI-2-XS qsf in this repo.
dependencies: python>=3.8
user-invocable: true
disable-model-invocation: false
---

# BFI-2 QSF Splitter and Scoring Auto-Injector

Splits a unified BFI-2 `.qsf` (Full 60-item, Short 30-item, and Extra-Short
15-item forms, as separate blocks in one survey) into three standalone `.qsf`
files, each with a working Qualtrics Scoring (`SCO`) configuration and correct
reverse-coding:

- **Full** and **Short** — the five BFI-2 domains *and* all 15 facets.
- **Extra-Short** — the five domains only. The BFI-2-XS key defines no facet
  scales (one item per facet), so facet scoring is intentionally omitted.

Item → domain/facet/reverse-key matching is by **statement text**, not by
`ChoiceID` position or `QuestionID`, so it survives item reordering in
Qualtrics. The lookup normalizes whitespace / `&nbsp;` / curly-quote variants
but otherwise matches verbatim against `master_mapping.json`.

The one hardcoded thing is the three form question IDs (`QID2` full, `QID3`
short, `QID4` extra-short) in the `FORMS` table at the top of `split_bfi2.py`.
Qualtrics may renumber QIDs on re-export; update `FORMS` to match before
running. Block and flow lookups derive from those QIDs.

## Run

```
python3 .claude/skills/bfi2-qsf-splitter/split_bfi2.py <input.qsf>
```

`<input.qsf>` is a unified BFI-2 survey containing the Full (QID2), Short
(QID3), and Extra-Short (QID4) forms as separate blocks — supply the path; it
is not bundled in this repo. Writes `BFI-2_Full.qsf`, `BFI-2_Short.qsf`,
`BFI-2_ExtraShort.qsf` to the current directory. Expected output:

```
Wrote BFI-2_Full.qsf (QID2): 60 items scored, 5 domain + 15 facet categories
Wrote BFI-2_Short.qsf (QID3): 30 items scored, 5 domain + 15 facet categories
Wrote BFI-2_ExtraShort.qsf (QID4): 15 items scored, 5 domain + no facet categories
```

The script asserts internal invariants (per-domain/facet item counts, one/two
`Grades` keys per item, every referenced category defined in `SCO`) and exits
non-zero if any item fails to match the master key — a non-zero exit means some
item got NO scoring and the run failed. Investigate edited wording, stray
non-breaking spaces, or curly quotes before trusting the output.

## What it produces

For each form the script deep-copies the source and keeps only that form's
question block, rebuilding `BL`/`FL` accordingly and dropping the other forms.
It injects two things:

- **`GradingData`** on the question, in the shape Qualtrics itself exports: one
  entry per matrix cell — per `(ChoiceID, AnswerID)` pair — so an N-item form
  has N×5 entries. Each entry is `{"AnswerID", "ChoiceID", "Grades", "index"}`,
  where `Grades` maps each scoring-category ID to that cell's scalar points.
  Normal items score 1..5, reverse-keyed items 5..1. The domain category is
  always keyed; the facet category is added for Full/Short (same points).
- **`SCO`** with `ScoringCategories` for the 5 domains and, for Full/Short, the
  15 facets (named e.g. `E: Sociability`). On the Short form, facet names carry
  the reliability caveat `(2-item; use with n≳400)` per the BFI-2-S key.

Category IDs follow Qualtrics' required format — `SC_` + exactly 15
case-sensitive alphanumeric characters (no underscores), e.g.
`SC_eDlwlvh41Ka2IbY`. Malformed IDs make the whole import fail.
`scoring_category_id()` generates conformant IDs deterministically, so a
category keeps a stable ID across runs. See
`.claude/skills/qsf-tools/SCHEMA.md` for the full scoring schema.

## Verify

1. Confirm exit 0 with the expected counts, then lint:
   ```
   python3 .claude/skills/qsf-tools/qsf_lint.py BFI-2_Full.qsf BFI-2_Short.qsf BFI-2_ExtraShort.qsf
   ```
2. Spot-check a normal item (choice 1) and a reverse-keyed item (choice 3):
   ```
   jq '.SurveyElements[] | select(.Element=="SQ") | .Payload.GradingData[] | select(.ChoiceID==1 or .ChoiceID==3)' BFI-2_Full.qsf
   ```
   Each cell's `Grades` keys (domain, plus facet for Full/Short) should agree
   with the item's `master_mapping.json` entry; the five cells run 1→5 for a
   normal item and 5→1 for a reverse-keyed one.
3. For a new or edited form, import into Qualtrics, submit a test response, and
   hand-compute one domain and one facet score against the score report — the
   only end-to-end check of the score computation.

## Files

- `split_bfi2.py` — the splitter/injector.
- `master_mapping.json` — item text → `{domain, facet, reverse}` for all 60
  BFI-2 items (12 per domain, 4 per facet, 30 reverse-keyed), verified against
  the official keys. The short forms are text subsets of the full pool, so this
  one map covers all three forms. **Shared dependency:** the careless-responding
  detector (`response_verification/verify_responses.py`) also reads this map and
  cross-validates `drip_item_pairs.tsv` against it at load time — editing a
  facet/domain/reverse entry here can make that check abort. Keep them in sync.
- `add_rps.py` — adds the 7-item Risk Propensity Scale (Meertens & Lion, 2008)
  plus one attention-check item to `../../../output/BFI-2_Full.qsf`, writing
  `../../../output/BFI-2_Full_RPS.qsf`. Run after `split_bfi2.py` (needs its
  output as input); doesn't touch the pure `BFI-2_Full.qsf`. Run
  `python3 add_rps.py` with no arguments.
- `gen_drip_js.py` — generates the client-side DRIP-score JavaScript snippet
  (for `BFI-2_Full_RPS.qsf`'s real-time quality gate) from
  `../../../response_verification/drip_item_pairs.tsv`. See
  `../../../docs/qualtrics-part2-wiring.md`.
