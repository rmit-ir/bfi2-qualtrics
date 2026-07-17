# Response quality verification

Post-hoc careless-responding detection for Qualtrics **response** exports of the
BFI-2 surveys in `../output/`. `verify_responses.py` reads an export, runs
per-respondent checks, and writes the data back with one boolean `flag_*` column
per check plus a joined `Careless_Flags` summary column.

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
reviewing. The script exits 0 whether or not anything is flagged.

## Checks

| Flag | What it catches | Applies to |
|---|---|---|
| `flag_incomplete` | any unanswered item | all forms |
| `flag_speed` | total `Duration` < 2 s/item | all forms |
| `flag_longstring` | ≥ 10 identical consecutive answers (straight-lining); also emits `longstring_max` | all forms |
| `flag_mahalanobis` | multivariate outlier vs. sample centroid, χ²(df = n_items, 1 − α); emits `mahal_d2`; skipped when N ≤ n_items | all forms |
| `flag_drip` | DRIP score ≥ 14 — sum of \|item1 − item2\| over 15 highly correlated pairs, reverse-keyed first; emits `drip_score` | **Full only** (needs all 60 items) |

### Thresholds

Overridable on the CLI:

```
--longstring-run N     longstring run cutoff       (default 10)
--drip-cutoff N        DRIP score cutoff           (default 14)
--sec-per-item S       speed threshold, s/item     (default 2.0)
--mahal-alpha A        Mahalanobis chi-square alpha (default 0.001)
```

## DRIP pair table

`drip_item_pairs.tsv` is the single source of truth for the 15 DRIP pairs
(columns: `Pair, Domain, Facet, Item1, Item2, Statement1, Statement2,
Reverse1, Reverse2, r_S1, r_S2, r_S3`). It is generated from Ruchensky et al.'s
Table 1 **by BFI-2 item number** (item # = ChoiceID in `../output/BFI-2_Full.qsf`);
statement text and reverse flags come from the splitter's
`master_mapping.json`, so they match the survey verbatim. At load time the
script cross-validates every pair against `master_mapping.json` (facet, domain,
and reverse-flag agreement) and aborts on any mismatch. Don't hand-edit
statement text here — regenerate from the item numbers instead.

## Tests

```
python3 -m unittest discover ../tests        # or from repo root
```

`tests/test_verify_responses.py` checks the pair-table integrity, runs the
detector against synthetic full-form exports (CSV/label, CSV/value, and
UTF-16 TSV/value), and — when present — against the checked-in fake-response
TSV in `sample_data/`. The detector tests skip cleanly when pandas/numpy/scipy
aren't installed.

`sample_data/` holds a small **fabricated** full-form TSV export (all careless
responses) used only as a test fixture and a worked example; it is not real
respondent data. The raw Qualtrics `*.zip` downloads are gitignored.

## Citation

DRIP pairs and the 14–17 cut score come from:

> Ruchensky, J. R., et al. Development of an Inconsistent Responding Scale for
> the BFI-2.

The BFI-2 itself is the work of Christopher J. Soto and Oliver P. John — see the
repo root `README.md`.
