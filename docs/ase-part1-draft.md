# ASE Part 1 draft, `ase-part1-2026-11-24-v1.1`

This is a **private-baseline transformer**, not a distributable QSF, live setup,
eligibility gate or ethics approval. It is separate from all BFI-2/Part 2
content, generation and verification.

It applies the selected age bands, education groups/examples, and reordered
experience items from the 24 November 2026 ASE Part 1 design decision. The age
bands follow the [ABS Age Standard](https://www.abs.gov.au/statistics/standards/age-standard/latest-release)
and the education groups are informed by the [Australian Standard Classification of Education](https://www.abs.gov.au/statistics/classifications/australian-standard-classification-education-asced/2001/level-education-structure-and-definitions/structure/broad-and-narrow-levels).
Existing experience wording/choice data and QIDs/export tags are not
regenerated. The baseline retains its own consent-first page break, remaining
demographics, and survey flow. Retained labels retain their choice IDs; new
labels start at the baseline’s `NextChoiceId` when present. Version 1.1
supports the native export omitting this optional field. In a fresh inactive
copy only, allocation then starts above every original choice ID, including
those retired by this edit. The fallback does not recover deleted IDs that no
longer appear in the export. Review any retained prior versions and supply
`--age-next-choice-id` and/or `--education-next-choice-id` if a higher floor
is needed. These flags apply only if the relevant native counter is absent.
Do not use the output to overwrite a survey or combine its recodes with past
responses.

## Prepare privately

1. Obtain and review a **native QSF definition** from the exact inactive Part
   1 source. In Qualtrics, use Tools → Import/Export → Export survey. A
   response export or Survey Definitions API JSON is not a QSF. Confirm the
   intended existing consent and integration source before using it. This
   tool does not download, invent, verify or replace those private values.
2. Put the QSF outside any Git checkout, record its SHA-256, then run:
   ```sh
   python3 prepare_ase_part1_draft.py --baseline /private/part1.qsf \
       --baseline-sha256 REVIEWED_EXPORT_SHA256 --check
   python3 prepare_ase_part1_draft.py --baseline /private/part1.qsf \
       --baseline-sha256 REVIEWED_EXPORT_SHA256 --output /private/part1-draft.qsf
   python3 .claude/skills/qsf-tools/qsf_lint.py /private/part1-draft.qsf
   ```
   A digest binds the bytes to the reviewed export; it does not prove the
   source was authentic. Input/output paths inside a Git checkout are refused.
   Output is a new file at mode 0600; an existing path is never overwritten.
   **Never stage, upload to GitHub, or otherwise share the private QSF.**
3. **Do not import the generated file as-is.** It retains private operational
   metadata and wiring from the copied baseline. Before any separately
   authorized import, use a private reviewed copy with operational callbacks,
   redirects and identifiers removed or replaced by isolated test values.
   Verify consent link and text, export tags, URL capture, and test-only
   completion handling on that private candidate. The generated QSF deliberately omits
   `RecaptchaV3` and `IncludeSecurityFields` from Survey Options after
   incompatible fresh imports in this repository. Verify any separately
   authorized Bot Detection/security setup manually after import in the
   target account, not by adding those fields to the QSF.
4. If authorized to import, use a separate **unpublished** project and verify
   the actual account-rendered order, texts, options, exclusivity,
   consent/debrief handling, and untouched identity/redirect behavior. An
   import can assign a new survey ID; do not update downstream live mappings
   or publish as part of setup. Confirm the project’s ethics/change-control
   path before recruitment or use of the revised demographics.

The repository tests use only a fabricated native JSON fixture. They verify
the transformation and guardrails, not a real account import, participant
journey, or verification of a private baseline. No provider API or network is
used by the generator. A different structure or existing references to the
edited choices fail closed and need a reviewed adapter.
