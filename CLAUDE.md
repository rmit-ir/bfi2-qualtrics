# CLAUDE.md

Guidance for Claude Code when working in this repository. User-facing overview
is in `README.md`.

## Repository contents

The deliverables are the three ready-to-import Qualtrics surveys in `output/` —
the BFI-2 full form and its short and extra-short variants, each a standalone
survey with domain- and facet-level scoring baked in. There is no build system.
Alongside them are two skills for working with `.qsf` files (below) and a test.

The source survey and the official reference documents (published-form PDFs,
SPSS syntax) are **not** in this repo — it ships only the finished, scored
outputs plus tooling. Regenerating `output/` therefore requires supplying the
unified source `.qsf` as input to the splitter (see below); it can't run from
repo contents alone. Verify scoring questions against the official BFI-2 forms,
not by fetching — the Colby site returns HTTP 403 to non-browser clients.

The BFI-2 and its item content are the work of Christopher J. Soto and Oliver
P. John; the original Qualtrics `.qsf` this repo's outputs derive from came from
their [Colby Personality Lab](https://www.colby.edu/academics/departments-and-programs/psychology/research-opportunities/personality-lab/the-bfi-2/).
Preserve their attribution in `README.md`. Cite: Soto, C. J., & John, O. P.
(2017). The next Big Five Inventory (BFI-2): Developing and assessing a
hierarchical model with 15 facets to enhance bandwidth, fidelity, and
predictive power. *Journal of Personality and Social Psychology, 113*,
117–143.

## Tests

Stdlib `unittest`, no pytest:

```
python3 -m unittest discover tests
```

`test_qsf_parses.py` asserts every `.qsf` in the repo is valid, importable JSON
with the expected top-level shape. Run it after editing any `.qsf`.

## Skills

- **`.claude/skills/qsf-tools/`** — general `.qsf` toolkit. `SCHEMA.md`
  documents the QSF schema (element types, question payloads, the
  multi-category `GradingData` scoring format, import invariants); `qsf_lint.py`
  validates structural consistency. Read the schema before hand-editing qsf
  JSON; lint after any edit or generation:
  ```
  python3 .claude/skills/qsf-tools/qsf_lint.py <file.qsf>
  ```
- **`.claude/skills/bfi2-qsf-splitter/`** — splits a unified BFI-2 source `.qsf`
  into the three standalone, self-scoring `output/` files. Its
  `master_mapping.json` (item text → domain/facet/reverse) is verified
  item-by-item against the official keys. Matching is by item text, so it needs
  a source qsf whose item wording matches the map.

## File format

`.qsf` files are single-line JSON. The invariant Qualtrics enforces is **valid
JSON**, not single-line — it imports pretty-printed files too. What breaks an
import is malformed JSON, most easily a non-JSON-aware tool un-escaping the
quotes inside embedded HTML `QuestionText` (`<div style=\"...\">` → `<div
style="...">`). Reformat only with JSON-aware tools (`jq`, `json.dump`); the
parse test catches escaping damage.

`Display` strings contain non-breaking spaces (U+00A0) and curly apostrophes
(e.g. `Is suspicious of others’ intentions.`). Don't ASCII-"clean" them — the
splitter's text normalizer accounts for them, and rewrites would silently alter
the survey text.

Top-level keys: `SurveyEntry` (metadata) and `SurveyElements` (array of typed
elements, each with an `Element` discriminator: `BL`, `FL`, `SO`, `SCO`, `SQ`,
`RS`, `PROJ`, `STAT`, `QC`). Inspect with `jq`:
```
jq '.SurveyElements[] | select(.Element=="SQ") | .PrimaryAttribute' output/BFI-2_Full.qsf
```

## Survey structure

Each `output/` file is a single `Matrix`/`Likert` question (QID2 full, QID3
short, QID4 extra-short) with items as `Choices` and a 5-point `Disagree
strongly` → `Agree strongly` scale as `Answers`. Blocks use
`Randomization.Type = "All"` and `ForceResponse = "RequestResponse"` (soft
prompt). Scoring lives in the `SCO` element plus per-cell `GradingData` on the
question — see `.claude/skills/qsf-tools/SCHEMA.md`.

The short forms are strict subsets of the full measure — the same item wording
appears verbatim across all three. When editing item text, update every variant
that contains that item. The `DataExportTag` per question (`BFI-2`, `BFI-2-S`,
`BFI-2-XS`) is the contract with downstream analysis; don't change it casually.

Item order follows published item numbering (item 1 = choice `1`); scoring keys
assume that order. Don't reorder or renumber choices without updating the
scoring in the same file.
