# Qualtrics QSF schema reference

Compiled from real Qualtrics exports, including verified multi-category
scoring. Qualtrics publishes no official spec for scoring internals; fields
marked *(observed)* are reverse-engineered and may vary by account or Qualtrics
version.

A `.qsf` is one JSON object on a single line:

```json
{ "SurveyEntry": { ... }, "SurveyElements": [ ... ] }
```

## SurveyEntry

Survey-level metadata. Keys observed: `SurveyID`, `SurveyName`,
`SurveyDescription`, `SurveyOwnerID`, `SurveyBrandID`, `DivisionID`,
`SurveyLanguage`, `SurveyActiveResponseSet`, `SurveyStatus`,
`SurveyStartDate`, `SurveyExpirationDate`, `SurveyCreationDate`,
`CreatorID`, `LastModified`, `LastAccessed`, `LastActivated`, `Deleted`.

On import Qualtrics assigns a fresh `SurveyID`; the embedded IDs only need
to be internally consistent, not globally unique.

## SurveyElements

Array of typed elements. Common envelope:

```json
{
  "SurveyID": "SV_...",          // must match across all elements
  "Element": "<discriminator>",
  "PrimaryAttribute": "...",     // meaning depends on Element type
  "SecondaryAttribute": "...",   // often null
  "TertiaryAttribute": null,
  "Payload": { ... }             // object, array, or null by type
}
```

| Element | PrimaryAttribute | Payload | Purpose |
|---|---|---|---|
| `BL` | `"Survey Blocks"` | array of blocks¹ | Block definitions |
| `FL` | `"Survey Flow"` | object | Order blocks are shown |
| `SO` | `"Survey Options"` | object | Back button, protection, etc. |
| `SCO` | `"Scoring"` | object | Scoring categories |
| `SQ` | QID, e.g. `"QID2"` | object | One question each (repeatable) |
| `RS` | `"RS_..."` | null | Response set stub |
| `QC` | `"Survey Question Count"` | null | Count in `SecondaryAttribute` (string, includes Trash questions) |
| `PROJ` | `"CORE"` | object | Project/theme metadata |
| `STAT` | `"Survey Statistics"` | object | Stats stub |
| `PL` | `"Preview Link"` | object | Preview link metadata; present in some exports, safe to drop |

Element order within `SurveyElements` is not fixed — Qualtrics emits it
differently across exports (e.g. `SCO` may precede or follow `SO`/`SQ`). Match
elements by `Element`, never by position.

¹ In some exports `BL.Payload` is an object keyed `"0"`,`"1"`,… instead of
an array — handle both when parsing.

## BL — blocks

Each block:

```json
{
  "Type": "Default" | "Standard" | "Trash",
  "Description": "human-readable block name",
  "ID": "BL_...",
  "BlockElements": [ {"Type": "Question", "QuestionID": "QID2"} ]
}
```

Exactly one block should be `Default` (first shown); `Trash` holds deleted
questions — its contents are NOT shown to respondents but its `SQ` elements
still exist in the file and count toward `QC`.

## FL — flow

```json
{
  "Flow": [ {"ID": "BL_...", "Type": "Block" | "Standard", "FlowID": "FL_2"} ],
  "Properties": {"Count": 4},
  "FlowID": "FL_1",
  "Type": "Root"
}
```

Every `Flow[].ID` must be a real block ID. Trash blocks do not appear in the
flow. `FlowID`s must be unique; `Properties.Count` should be ≥ the number of
flow nodes + root *(observed: not strictly validated on import)*.

## SQ — questions

`PrimaryAttribute` = `Payload.QuestionID` (keep in sync).
`SecondaryAttribute` = truncated question text (display-only, safe to ignore).

Payload keys for a Matrix/Likert question (the BFI-2 shape):

```json
{
  "QuestionType": "Matrix", "Selector": "Likert", "SubSelector": "SingleAnswer",
  "QuestionID": "QID2",
  "QuestionText": "…", "QuestionDescription": "truncated…",
  "DataExportTag": "BFI-2",          // column prefix in exported data — breaking to change
  "Choices":  {"1": {"Display": "Is outgoing, sociable."}, ...},   // matrix ROWS / items
  "ChoiceOrder": [1, 2, ...],        // ints or strings depending on export
  "Answers":  {"1": {"Display": "Disagree strongly"}, ...},        // matrix COLUMNS / scale
  "AnswerOrder": [1, ...],
  "ChoiceDataExportTags": false,
  "NextChoiceId": 61, "NextAnswerId": 6,   // must exceed max existing IDs
  "Configuration": {"QuestionDescriptionOption": "UseText", "MobileFirst": true, ...},
  "Validation": {"Settings": {"ForceResponse": "RequestResponse", "ForceResponseType": "RequestResponse", "Type": "None"}},
  "Randomization": {"Type": "All", "TotalRandSubset": "", "Advanced": null},
  "DataVisibility": {"Private": false, "Hidden": false},
  "DefaultChoices": false,
  "GradingData": [ ... ],            // see Scoring below; may be [] or absent
  "Language": []
}
```

Simple multiple-choice (`QuestionType: "MC"`, `Selector: "SAVR"`) uses the
same `Choices`/`ChoiceOrder` but no `Answers`.

Gotchas:
- `Display` strings may contain non-breaking spaces (U+00A0), `&nbsp;`, and
  curly apostrophes. Never ASCII-"clean" them; normalize only for matching.
- `ForceResponse: "RequestResponse"` = soft prompt; `"ON"` = hard require;
  `"OFF"` = optional.
- When adding a choice, use the current `NextChoiceId` as its key and
  increment it; reusing a deleted ID corrupts response mapping.

## SCO + GradingData — scoring

Decoded from real Qualtrics exports with scoring built in the UI, and confirmed
end to end: this repo's generated `output/BFI-2_Full.qsf` imported into
Qualtrics, its re-export came back byte-for-byte identical on every scoring
field (all 20 category IDs, all 300 GradingData cells, the summary pointers),
and Qualtrics' computed scores on real responses matched an independent
recomputation exactly.

`SCO.Payload`:

```json
{
  "ScoringCategories": [
    {"ID": "SC_eDlwlvh41Ka2IbY", "Name": "Extraversion", "Description": ""}
  ],
  "ScoringCategoryGroups": [],
  "DefaultScoringCategory": "SC_...",   // a category ID, or null
  "ScoringSummaryCategory": "SC_...",   // a category ID, or null
  "ScoringSummaryAfterQuestions": 0,
  "ScoringSummaryAfterSurvey": 0,
  "AutoScoringCategory": null,
  "IgnoreNullValues": true
}
```

- Category `ID`s have a strict format: `SC_` followed by exactly **15
  case-sensitive alphanumeric characters**, no underscores in the body (e.g.
  `SC_eDlwlvh41Ka2IbY`). Qualtrics generates these; a hand-authored file
  whose category IDs don't match the format **fails to import entirely**
  (Qualtrics reports only "Something went wrong and the project wasn't
  created"). Every ID referenced in a question's `GradingData` must appear
  here. Each category has `ID`, `Name`, `Description` (`""` or null).
- `IgnoreNullValues` and the two `Scoring*Category` pointers appear once
  scoring is configured; an empty/never-scored survey may omit them and have
  `ScoringCategories: []`.

Per-question scores live in `SQ.Payload.GradingData`, as **one entry per
matrix cell** — i.e. per `(ChoiceID, AnswerID)` pair. A Matrix/Likert question
with C items (Choices) and A scale points (Answers) has **C × A** entries
(e.g. 60 items × 5 = 300). Each entry's `Grades` gives the point value that
one cell contributes to each scoring category:

```json
"GradingData": [
  {
    "AnswerID": 1,
    "ChoiceID": 1,
    "Grades": { "SC_eDlwlvh41Ka2IbY": 1, "SC_3Wv6Jhw79jwnMhw": "3" },
    "index": 0
  },
  {
    "AnswerID": 2,
    "ChoiceID": 1,
    "Grades": { "SC_eDlwlvh41Ka2IbY": 2, "SC_3Wv6Jhw79jwnMhw": "1" },
    "index": 1
  }
]
```

- `AnswerID` and `ChoiceID` are **integers** (keys into `Answers` / `Choices`).
- `Grades` maps category ID → the **scalar** points for THIS cell (not a
  nested per-answer map). Points may be int or string; Qualtrics emits both.
  A cell lists only the categories that score this item — an item scored by
  one category has a one-key `Grades`; an item in two categories has two keys.
- Reverse-keying is expressed in the point values across a choice's 5 cells:
  normal = answers 1..5 → 1..5, reverse = answers 1..5 → 5..1.
- `index` is a running counter over the flattened grid (0-based), in
  choice-major order: `index = (ChoiceID_position)*A + (AnswerID - 1)` when
  choices/answers are numbered 1..N contiguously.
- Every category referenced in `Grades` must be an `ID` in
  `SCO.ScoringCategories`.

> A pre-2026-07 version of this repo's splitter emitted a flat
> `{"ChoiceID", "Category": "<id>", "Grades": {answer: pts}}` shape (one entry
> per choice, nested per-answer map). That was a guess and does **not** match
> real exports — regenerate any such files. `qsf_lint.py` warns on it.

## Import invariants (what breaks an import or the data)

1. **Valid** JSON — Qualtrics is strict about the overall schema, but
   accepts pretty-printed files too; single-line is this repo's own
   writing convention (`json.dump(..., separators=(",", ":"))`), not a
   Qualtrics requirement. What actually breaks an import is malformed
   JSON, most easily a non-JSON-aware tool un-escaping the quotes inside
   embedded HTML `QuestionText`.
2. `SurveyID` identical on every element.
3. All `FL` flow IDs resolve to `BL` block IDs; all `BlockElements`
   QuestionIDs resolve to `SQ` elements.
4. `SQ.PrimaryAttribute == Payload.QuestionID`.
5. `ChoiceOrder` ⊆ keys of `Choices` (same for answers).
6. `DataExportTag` is the contract with downstream analysis — never change
   casually.
7. Scoring category IDs consistent between `SCO` and every `GradingData`,
   AND formatted as `SC_` + 15 alphanumeric chars — malformed IDs fail the
   whole import.
