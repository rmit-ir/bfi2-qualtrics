---
name: qsf-tools
description: Schema reference and lint/inspection tooling for Qualtrics .qsf survey files. Use whenever reading, editing, generating, or debugging a .qsf — before hand-editing the JSON, after any programmatic edit, or when an import into Qualtrics fails.
dependencies: python>=3.8, jq
user-invocable: true
disable-model-invocation: false
---

# Qualtrics QSF tools

Working knowledge and tooling for Qualtrics Survey Format (`.qsf`) files:
a schema reference compiled from real exports, a structural linter, and
`jq` recipes for fast inspection.

## Files in this skill

- `SCHEMA.md` — the QSF schema: `SurveyEntry`, every `SurveyElements` type
  (`BL`/`FL`/`SO`/`SCO`/`SQ`/`RS`/`QC`/`PROJ`/`STAT`), question payload
  shapes, the **verified multi-category `GradingData` scoring format**, and
  the list of import invariants. Read it before editing qsf JSON by hand.
- `qsf_lint.py` — structural validator enforcing those invariants.

## Workflow

1. **Before editing:** read `SCHEMA.md` (at minimum the section for the
   element type you're touching and "Import invariants" at the end).
2. **Inspect** with `jq` (recipes below) rather than opening the single-line
   JSON raw.
3. **After any edit or generation**, lint:

   ```
   python3 .claude/skills/qsf-tools/qsf_lint.py <file.qsf> [more.qsf ...]
   ```

   Exit 0 with `OK` = structurally sound. `ERROR` lines (exit 1) are
   import-breaking or data-corrupting; fix before importing. `WARN` lines
   are suspicious but importable (e.g. legacy scoring shape, question in no
   block) — mention them to the user rather than silently ignoring.
4. The linter checks structure only. It cannot verify content (item
   wording, scoring correctness against a measure's published key) or
   guarantee Qualtrics will accept the file — for real assurance, import
   into Qualtrics and check Survey Tools → Scoring.

## jq recipes

```bash
# Element inventory
jq -r '.SurveyElements[].Element' file.qsf | sort | uniq -c

# All questions: QID, export tag, type, item count
jq '.SurveyElements[] | select(.Element=="SQ") | {id:.PrimaryAttribute, tag:.Payload.DataExportTag, type:.Payload.QuestionType, n:(.Payload.Choices|length)}' file.qsf

# Blocks with their questions   (append `// (.Payload|to_entries|map(.value))` handling if Payload is a dict)
jq '.SurveyElements[] | select(.Element=="BL") | .Payload[] | {Type, Description, ID, q:[.BlockElements[]?.QuestionID]}' file.qsf

# Survey flow
jq '.SurveyElements[] | select(.Element=="FL") | .Payload.Flow' file.qsf

# One question's items
jq '.SurveyElements[] | select(.Element=="SQ" and .PrimaryAttribute=="QID2") | .Payload.Choices' file.qsf

# Scoring: categories + a question's GradingData
jq '.SurveyElements[] | select(.Element=="SCO") | .Payload.ScoringCategories' file.qsf
jq '.SurveyElements[] | select(.Element=="SQ") | .Payload.GradingData' file.qsf

# Pretty-print whole file
jq '.' file.qsf | less
```

## Hard-won rules (also in SCHEMA.md)

- Qualtrics requires **valid JSON**, not single-line — it imports
  pretty-printed files too. This repo writes single-line/compact
  (`json.dump(..., separators=(",", ":"))`) as its own convention, not
  because Qualtrics demands it; what actually breaks an import is
  malformed JSON (e.g. a non-JSON-aware tool un-escaping quotes inside
  embedded HTML). Reformat only with JSON-aware tools.
- `Display` text contains non-breaking spaces (U+00A0), `&nbsp;`, and curly
  quotes. Normalize copies for matching; never rewrite the originals.
- `DataExportTag` is the contract with downstream analysis — don't change it.
- One `GradingData` entry per matrix **cell** — i.e. per `(ChoiceID,
  AnswerID)` pair, so an N-item question has N×(number of answer options)
  entries. Each entry's `Grades` maps scoring-category ID -> that one
  cell's point value (a scalar, not a nested map) — see SCHEMA.md.
- New choices take the current `NextChoiceId` as their key; increment it,
  never reuse a deleted ID.
- Scoring internals (`SCO`/`GradingData`) are reverse-engineered, not
  documented by Qualtrics — after changing them, verify with a real import.

## Related

- `bfi2-qsf-splitter` (sibling skill) — splits this repo's BFI-2 survey and
  injects domain + facet scoring in the verified multi-category shape; lint
  its outputs with `qsf_lint.py` after runs.
