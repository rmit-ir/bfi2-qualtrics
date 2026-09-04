#!/usr/bin/env python3
"""Flag careless responding in a Qualtrics BFI-2 response export.

Reads a Qualtrics CSV/TSV export of any BFI-2 form (Full / Short /
Extra-short), runs a battery of post-hoc careless-responding checks per
respondent, and writes the data back out with one boolean column per check
plus a joined ``Careless_Flags`` summary column.

The battery implements the post-hoc methods reviewed by Ward & Meade (2023),
through their "moderate" screening tier, plus the BFI-2-specific DRIP
inconsistency scale (Ruchensky, Edens, & Donnellan, 2025). A-priori /
design-time methods (instructed-response items, bogus items, infrequency
items, self-report seriousness checks, page timers) do not apply — the
surveys in ../output/ contain none by design. Note the surveys render as a
single page (one matrix question), so the total ``Duration (in seconds)`` is
effectively the page-level timing Ward & Meade recommend.

Checks (each yields a metric column and/or a ``flag_*`` column):

  Invariability
  * ``flag_longstring``  longest run of identical raw answers >= cutoff
                         (``longstring_max``).
  * ``flag_variance``    within-person SD of raw answers below cutoff
                         (``response_sd``) — catches patterned invariance
                         (e.g. 2,3,2,3, ...) that longstring misses
                         (Dunn et al. 2018 via Ward & Meade 2023).

  Outlier analysis
  * ``flag_mahalanobis`` Mahalanobis D^2 vs. the reference sample beyond the
                         chi-square cutoff (``mahal_d2``).

  Consistency
  * ``flag_psychsyn``    psychometric synonyms (Meade & Craig 2012): item
                         pairs empirically correlated >= --syn-pair-r in the
                         reference sample; within-person correlation across
                         pairs below cutoff (``psychsyn_r``). Computed on
                         reverse-keyed scores, so antonym pairs fold in as
                         synonyms.
  * ``flag_evenodd``     even-odd consistency / personal reliability: each
                         facet split into odd/even halves, half-scores
                         correlated within person, Spearman-Brown corrected
                         (``evenodd_r``). Skipped on the XS form (facets are
                         single items).
  * ``flag_persontotal`` person-total correlation (Curran 2016): each
                         person's keyed responses vs. the leave-one-out
                         sample mean per item (``person_total_r``).
  * ``flag_drip``        (Full form only) DRIP — Detection of Response
                         Inconsistency Procedure: sum of |item1 - item2| over
                         15 highly correlated item pairs, reverse-keyed first
                         (``drip_score``; Ruchensky et al. 2025, cut range
                         14-17). NOTE: DRIP misses straight-liners (all-"3"
                         scores 0); the invariability checks cover that case.

  Speed / completeness
  * ``flag_speed``       total ``Duration (in seconds)`` < 2 s/item
                         (Bowling et al. 2016 via Ward & Meade 2023).
  * ``flag_incomplete``  any unanswered item.

Sequential removal (Ward & Meade 2023, "extensive" tier): rows flagged as
invariant (longstring/variance) are excluded from the REFERENCE statistics —
the Mahalanobis centroid/covariance, the synonym-pair correlations, and the
person-total means — then every row is scored against those references.

Out of scope, deliberately: person-fit IRT / polytomous Guttman errors,
resampling, and latent-class/mixture modeling — they require modeling choices
with no published cutoffs. The per-check metric columns this script emits are
exactly the indicator inputs such models need, so they can be run downstream.

Usage:
    python3 verify_responses.py <export.csv|export.tsv> [-o flagged.csv]
        [--longstring-run N] [--sd-cutoff X] [--mahal-alpha A]
        [--syn-pair-r R] [--syn-cutoff R] [--evenodd-cutoff R]
        [--persontotal-cutoff R] [--drip-cutoff N] [--sec-per-item S]

The export may be CSV or TSV (inferred from the extension) in either UTF-8 or
UTF-16 (sniffed from the byte-order mark), with item cells as choice labels or
numeric values (inferred from the data) — matching whatever Qualtrics emitted.

Requires pandas, numpy, scipy (see requirements.txt); the rest of the repo is
stdlib-only.

References:
  Ward, M. K., & Meade, A. W. (2023). Dealing with careless responding in
    survey data: Prevention, identification, and recommended best practices.
    Annual Review of Psychology, 74, 577-596.
    https://doi.org/10.1146/annurev-psych-040422-045007
  Ruchensky, J. R., Edens, J. F., & Donnellan, M. B. (2025). Development of
    an inconsistent responding scale for the Big Five Inventory-2. Journal of
    Personality Assessment, 107(3), 384-391.
    https://doi.org/10.1080/00223891.2024.2411557
"""
import argparse
import json
import math
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import chi2

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
MASTER_MAPPING = REPO_ROOT / ".claude" / "skills" / "bfi2-qsf-splitter" / "master_mapping.json"
DRIP_PAIRS_TSV = HERE / "drip_item_pairs.tsv"

# Answer text -> numeric point value. Matches the 5-point BFI-2 Likert scale
# baked into the surveys (see output/*.qsf Answers).
ANSWER_SCALE = {
    "disagree strongly": 1,
    "disagree a little": 2,
    "neutral; no opinion": 3,
    "agree a little": 4,
    "agree strongly": 5,
}

# Item-column prefix per form (DataExportTag). Item number == ChoiceID ==
# published item number; the suffix after the prefix is that number.
FORM_PREFIXES = {
    "BFI-2-XS": "extra-short",
    "BFI-2-S": "short",
    "BFI-2": "full",
}

# Per-form survey file + question ID, for item metadata (facet/reverse keys).
# Item numbers are form-local: Short item 3 is a different statement than
# Full item 3, so each form's keys must come from its own qsf.
FORM_QSF = {
    "full": ("BFI-2_Full.qsf", "QID2"),
    "short": ("BFI-2_Short.qsf", "QID3"),
    "extra-short": ("BFI-2_ExtraShort.qsf", "QID4"),
}

# The real published item count per form -- checked against the detected
# item columns so a truncated or filtered export (missing columns, or an
# accidental partial export) is rejected with a clear message rather than
# silently scored against the wrong item count (a wrong flag_speed
# threshold) or crashing deep inside a check that assumes every item
# exists (DRIP's item lookups).
EXPECTED_N_ITEMS = {"full": 60, "short": 30, "extra-short": 15}

DURATION_COL = "Duration (in seconds)"

# Every column name this script ever adds to its output. If an INPUT export
# already has one of these (most plausibly: re-running the script against
# its own previously flagged output), the formula-neutralization step below
# would sanitize this script's own computed metrics as if they were
# original respondent data -- e.g. quoting a genuine negative correlation
# like "-0.53" into the string "'-0.53". Reject that input shape outright
# instead.
RESERVED_OUTPUT_COLUMNS = {
    "longstring_max", "response_sd", "mahal_d2", "psychsyn_r",
    "person_total_r", "evenodd_r", "drip_score",
    "flag_incomplete", "flag_speed", "flag_longstring", "flag_variance",
    "flag_mahalanobis", "flag_psychsyn", "flag_persontotal", "flag_evenodd",
    "flag_drip", "Careless_Flags",
}

# Cutoffs. Sources: sec/item and syn-pair-r are from the literature (Bowling
# et al. 2016; Meade & Craig 2012); drip-cutoff is the sensitive end of
# Ruchensky et al.'s recommended 14-17 range. The remaining score cutoffs are
# conventional heuristics — Ward & Meade (2023) give no numeric cutoffs for
# them and note generalizable cut scores are elusive — so they are advisory
# defaults meant to be calibrated per sample via the CLI flags.
DEFAULT_LONGSTRING_RUN = 10
DEFAULT_SD_CUTOFF = 0.5
DEFAULT_MAHAL_ALPHA = 0.001
DEFAULT_SYN_PAIR_R = 0.60
DEFAULT_SYN_CUTOFF = 0.22
DEFAULT_EVENODD_CUTOFF = 0.30
DEFAULT_PERSONTOTAL_CUTOFF = 0.0
DEFAULT_DRIP_CUTOFF = 14
DEFAULT_SEC_PER_ITEM = 2.0

# Minimum reference-sample sizes below which sample-dependent indices are
# skipped rather than computed on unstable statistics.
MIN_N_PSYCHSYN = 30
MIN_N_PERSONTOTAL = 10
MIN_SYNONYM_PAIRS = 5


def normalize_text(text):
    """Match split_bfi2.py:normalize_text — collapse nbsp/whitespace, curly
    quotes, and case, so answer-text and item-text comparisons are robust."""
    text = text.replace("&nbsp;", " ").replace(" ", " ")
    text = text.replace("‘", "'").replace("’", "'")
    text = re.sub(r"\s+", " ", text).strip()
    return text.lower()


def note(msg):
    print(f"  note: {msg}", file=sys.stderr)


def load_master_mapping():
    with open(MASTER_MAPPING, encoding="utf-8") as f:
        raw = json.load(f)
    return {normalize_text(k): v for k, v in raw.items()}


def load_form_meta(form_name, mapping):
    """item number -> {domain, facet, reverse, text} for the detected form.

    Looks the form's own qsf up so item numbers map to the right statements
    (Short/XS item numbers are form-local, not Full-form numbers). `text` is
    the item's actual Display text in this qsf (a fresh dict per item, not
    a mutated master_mapping entry -- entries are shared across forms).
    """
    fname, qid = FORM_QSF[form_name]
    qsf_path = REPO_ROOT / "output" / fname
    qsf = json.loads(qsf_path.read_text(encoding="utf-8"))
    sq = next((e for e in qsf["SurveyElements"]
               if e["Element"] == "SQ" and e["PrimaryAttribute"] == qid), None)
    if sq is None:
        raise SystemExit(f"{qsf_path}: no {qid} question found (expected the "
                          f"{form_name} form's item matrix)")
    meta = {}
    for cid, c in sq["Payload"]["Choices"].items():
        key = normalize_text(c["Display"])
        if key not in mapping:
            raise SystemExit(
                f"{qsf_path}: item {cid} ({c['Display']!r}) has no entry in "
                "master_mapping.json -- edited item wording, or the map is "
                "out of date?")
        meta[int(cid)] = {**mapping[key], "text": c["Display"]}
    return meta


def load_drip_pairs():
    """Read the DRIP pair table (single source of truth for the 15 pairs)."""
    df = pd.read_csv(DRIP_PAIRS_TSV, sep="\t", dtype=str)
    pairs = []
    for _, row in df.iterrows():
        pairs.append({
            "pair": int(row["Pair"]),
            "domain": row["Domain"],
            "facet": row["Facet"],
            "item1": int(row["Item1"]),
            "item2": int(row["Item2"]),
            "reverse1": row["Reverse1"].strip().lower() == "true",
            "reverse2": row["Reverse2"].strip().lower() == "true",
            "statement1": row.get("Statement1"),
            "statement2": row.get("Statement2"),
        })
    if len(pairs) != 15:
        raise SystemExit(
            f"{DRIP_PAIRS_TSV}: expected exactly 15 DRIP pairs, found {len(pairs)}")
    return pairs


def cross_validate_pairs(pairs, choice_meta):
    """Abort if drip_item_pairs.tsv disagrees with master_mapping.json.

    choice_meta maps item number -> mapping entry for the FULL form.
    Validates that each pair's items exist, share the pair's facet/domain,
    that the TSV reverse flags match the master key, that no item appears
    in more than one pair, and that Statement1/Statement2 still match the
    survey's actual item text (those columns are documentation-only, never
    used for scoring, so nothing else would catch them drifting stale).
    """
    problems = []
    seen_items = set()
    for p in pairs:
        for side in ("1", "2"):
            item = p[f"item{side}"]
            if item in seen_items:
                problems.append(f"pair {p['pair']}: item {item} also appears in an earlier pair")
            seen_items.add(item)
            meta = choice_meta.get(item)
            if meta is None:
                problems.append(f"pair {p['pair']}: item {item} not present in form")
                continue
            if meta["facet"] != p["facet"]:
                problems.append(
                    f"pair {p['pair']}: item {item} facet {meta['facet']!r} "
                    f"!= table facet {p['facet']!r}")
            if meta["domain"] != p["domain"]:
                problems.append(
                    f"pair {p['pair']}: item {item} domain {meta['domain']!r} "
                    f"!= table domain {p['domain']!r}")
            if bool(meta["reverse"]) != p[f"reverse{side}"]:
                problems.append(
                    f"pair {p['pair']}: item {item} reverse {meta['reverse']} "
                    f"!= table reverse {p[f'reverse{side}']}")
            statement = p.get(f"statement{side}")
            if statement is not None and meta.get("text") is not None and \
                    normalize_text(statement) != normalize_text(meta["text"]):
                problems.append(
                    f"pair {p['pair']}: item {item} statement{side} "
                    f"{statement!r} != survey text {meta['text']!r}")
    if problems:
        raise SystemExit(
            "drip_item_pairs.tsv is inconsistent with master_mapping.json:\n  "
            + "\n  ".join(problems))


def sniff_encoding(path):
    """Detect the file encoding from a leading byte-order mark.

    Qualtrics exports CSV as UTF-8 and TSV as UTF-16 LE (both with a BOM);
    fall back to UTF-8 when no BOM is present.
    """
    with open(path, "rb") as f:
        head = f.read(4)
    if head[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return "utf-16"
    if head[:3] == b"\xef\xbb\xbf":
        return "utf-8-sig"
    return "utf-8"


def delimiter_for(path):
    """Field delimiter from the file extension: .tsv/.tab -> tab, else comma."""
    return "\t" if path.suffix.lower() in (".tsv", ".tab") else ","


def read_export(path):
    """Read a Qualtrics CSV or TSV export with its 3-row header.

    Delimiter is inferred from the extension (.tsv/.tab -> tab, else comma) and
    the encoding from the byte-order mark (Qualtrics writes UTF-8 CSV / UTF-16
    TSV). Row 0 = machine column names (used as the DataFrame header), rows 1-2
    are the human-readable question text and the ``{"ImportId": ...}`` JSON;
    both are skipped. Returns (dataframe, header_rows, sep, encoding) so the
    extra header rows can be written back out in the same format.
    """
    sep = delimiter_for(path)
    encoding = sniff_encoding(path)
    header_rows = pd.read_csv(path, header=None, nrows=3, dtype=str,
                              sep=sep, encoding=encoding)
    if header_rows.shape[0] < 3:
        raise SystemExit(
            f"{path}: only {header_rows.shape[0]} row(s) found, expected a "
            "Qualtrics export's 3-row header (column names / question text / "
            "ImportId JSON). Is 'Insert a row of variable names' plus the "
            "default advanced export options both enabled?")
    import_id_row = " ".join(str(v) for v in header_rows.iloc[2].dropna())
    if "ImportId" not in import_id_row:
        raise SystemExit(
            f"{path}: row 3 doesn't look like Qualtrics' {{\"ImportId\": ...}} "
            "metadata row -- this export's header shape doesn't match what "
            "this script assumes (3 rows: column names / question text / "
            "ImportId JSON). Re-export with Qualtrics' default header "
            "options rather than editing this file by hand.")
    df = pd.read_csv(path, header=0, skiprows=[1, 2], dtype=str,
                     sep=sep, encoding=encoding)
    return df, header_rows, sep, encoding


def detect_form(columns):
    """Identify the form and its ordered item columns from column names.

    Item columns look like ``<DataExportTag>_<n>``. Longest prefix wins
    (``BFI-2-XS_`` before ``BFI-2_``) so the more specific tag matches first.
    Returns (form_name, prefix, [item_columns sorted by numeric suffix]).
    """
    for prefix_tag, form_name in FORM_PREFIXES.items():
        prefix = prefix_tag + "_"
        item_cols = []
        for col in columns:
            if col.startswith(prefix):
                suffix = col[len(prefix):]
                if suffix.isdigit():
                    item_cols.append((int(suffix), col))
        if item_cols:
            item_cols.sort()
            numbers = [n for n, _ in item_cols]
            expected = set(range(1, EXPECTED_N_ITEMS[form_name] + 1))
            # set(numbers) == expected alone would accept a duplicate
            # numeric ID (e.g. both "BFI-2_1" and "BFI-2_01" parse to item
            # 1) as long as the SET still covers 1..N -- also check the
            # count, or a later dict keyed by item number
            # (recode_items -- {num: df[col]...}) would silently drop one
            # of the two columns' data.
            if len(numbers) != len(set(numbers)):
                from collections import Counter
                dupes = sorted(n for n, c in Counter(numbers).items() if c > 1)
                raise SystemExit(
                    f"{prefix_tag}_ columns include duplicate item number(s) "
                    f"{dupes} (e.g. both '{prefix_tag}_{dupes[0]}' and "
                    f"'{prefix_tag}_0{dupes[0]}' parsing to the same item) "
                    "-- fix the export's column names before scoring.")
            if set(numbers) != expected:
                missing = sorted(expected - set(numbers))
                extra = sorted(set(numbers) - expected)
                detail = []
                if missing:
                    detail.append(f"missing item(s) {missing}")
                if extra:
                    detail.append(f"unexpected item number(s) {extra}")
                raise SystemExit(
                    f"{prefix_tag}_ columns present but don't cover the full "
                    f"published {form_name} form ({EXPECTED_N_ITEMS[form_name]} "
                    f"items, 1..{EXPECTED_N_ITEMS[form_name]}): "
                    f"{'; '.join(detail)}. Truncated or filtered export?")
            return form_name, prefix_tag, [c for _, c in item_cols]
    raise SystemExit(
        "No BFI-2 item columns found (expected columns like 'BFI-2_1'). "
        "Is this a BFI-2 Qualtrics export?")


def infer_export_mode(df, item_cols):
    """Decide whether item cells hold recoded numeric values or answer labels.

    Qualtrics can export either "Use choice text" (labels like ``Agree
    strongly``) or "Use numeric values" (``1``..``5``). We look at the
    non-blank item cells: if every one is an integer 1..5 we treat the export
    as numeric; if they match the known answer labels we treat it as labels;
    otherwise we prefer whichever mode recognizes more cells so a mostly-clean
    export still scores. Returns "value" or "label".
    """
    values = df[item_cols].to_numpy().ravel()
    nonblank = [str(v) for v in values if not pd.isna(v) and str(v).strip() != ""]
    if not nonblank:
        return "value"
    n_numeric = sum(1 for v in nonblank
                    if v.strip().isdigit() and 1 <= int(v.strip()) <= 5)
    n_label = sum(1 for v in nonblank if normalize_text(v) in ANSWER_SCALE)
    return "value" if n_numeric >= n_label else "label"


def recode_items(df, item_cols, mode):
    """Map item cells -> 1..5 numeric scores into new columns.

    ``mode`` is "value" (cells already 1..5) or "label" (answer text via
    ANSWER_SCALE). Returns (numeric_df, item_numbers) with one column per item
    (indexed by item number); blank/unrecognized/out-of-range cells become NaN.
    """
    item_numbers = [int(c.split("_")[-1]) for c in item_cols]

    def to_score(v):
        if pd.isna(v):
            return np.nan
        s = str(v).strip()
        if s == "":
            return np.nan
        if mode == "value":
            if s.isdigit() and 1 <= int(s) <= 5:
                return int(s)
            return np.nan
        return ANSWER_SCALE.get(normalize_text(s), np.nan)

    numeric = {num: df[col].map(to_score)
               for num, col in zip(item_numbers, item_cols)}
    numeric_df = pd.DataFrame(numeric, index=df.index)
    return numeric_df, item_numbers


def keyed_scores(numeric_df, form_meta):
    """Reverse-key the numeric responses per the scoring key (6 - x)."""
    keyed = {}
    for num in numeric_df.columns:
        col = numeric_df[num]
        keyed[num] = (6 - col) if form_meta[num]["reverse"] else col
    return pd.DataFrame(keyed, index=numeric_df.index)


def safe_corr(x, y):
    """Pearson r of two 1-D arrays, NaN when undefined (constant/short)."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    ok = ~(np.isnan(x) | np.isnan(y))
    x, y = x[ok], y[ok]
    if len(x) < 3 or np.std(x) == 0 or np.std(y) == 0:
        return np.nan
    return float(np.corrcoef(x, y)[0, 1])


# ---------------------------------------------------------------------------
# Checks. Sample-dependent checks take reference_df — the rows their sample
# statistics are computed from (invariant rows already removed) — and score
# every row of the full frame against those statistics.
# ---------------------------------------------------------------------------

def check_speed(df, n_items, sec_per_item):
    """flag_speed: total Duration below sec_per_item * n_items.

    The BFI-2 surveys are one page (a single matrix question), so total
    duration is effectively the page-level timing Ward & Meade recommend.
    """
    if DURATION_COL not in df.columns:
        note(f"no {DURATION_COL!r} column — speed check skipped.")
        return pd.Series(False, index=df.index)
    duration = pd.to_numeric(df[DURATION_COL], errors="coerce")
    threshold = sec_per_item * n_items
    return duration < threshold


def check_longstring(numeric_df):
    """Longest run of identical *raw* (pre-reverse) answers per respondent.

    NaN breaks a run. A respondent with no answered items has a run of 0.
    """
    def max_run(row):
        best = 0
        cur = 0
        prev = object()
        for v in row:
            if pd.isna(v):
                cur = 0
                prev = object()
                continue
            if v == prev:
                cur += 1
            else:
                cur = 1
                prev = v
            best = max(best, cur)
        return best
    return numeric_df.apply(max_run, axis=1)


def check_variance(numeric_df):
    """Within-person SD of raw answers (inter-item SD; Marjanovic et al.).

    Low SD marks invariant responding, including alternating patterns that
    longstring misses. Rows with < 2 answered items get NaN (not flagged).
    """
    return numeric_df.std(axis=1, ddof=1)


def check_mahalanobis(numeric_df, reference_df, alpha):
    """Mahalanobis D^2 of each complete row vs. the reference centroid.

    Centroid and covariance come from reference_df (invariant responders
    removed — sequential removal); every complete row is scored against them.
    Returns (d2_series, flag_series). Skipped (all NaN / no flags) when the
    reference has <= n_items complete rows (covariance unstable).
    """
    n_items = numeric_df.shape[1]
    d2 = pd.Series(np.nan, index=numeric_df.index)
    flag = pd.Series(False, index=numeric_df.index)

    ref = reference_df.dropna()
    if ref.shape[0] <= n_items:
        note(f"only {ref.shape[0]} reference responses for {n_items} items — "
             f"Mahalanobis check skipped (covariance unstable; need > n_items).")
        return d2, flag

    centroid = ref.to_numpy(dtype=float).mean(axis=0)
    cov = np.cov(ref.to_numpy(dtype=float), rowvar=False)
    inv = np.linalg.pinv(cov)  # pseudo-inverse for numerical stability

    complete = numeric_df.dropna()
    diff = complete.to_numpy(dtype=float) - centroid
    dist2 = np.einsum("ij,jk,ik->i", diff, inv, diff)
    d2.loc[complete.index] = dist2

    crit = chi2.ppf(1 - alpha, df=n_items)
    flag.loc[complete.index] = dist2 > crit
    return d2, flag


def build_synonym_pairs(reference_keyed, pair_r):
    """Empirical psychometric-synonym pairs (Meade & Craig 2012).

    Pairs are item pairs whose correlation in the reference sample is
    >= pair_r, computed on reverse-KEYED scores so that semantic antonyms
    (which correlate negatively on raw scores) surface as synonym pairs too.
    Returns a list of (item_a, item_b).
    """
    corr = reference_keyed.corr()
    items = list(corr.columns)
    pairs = []
    for i, a in enumerate(items):
        for b in items[i + 1:]:
            r = corr.at[a, b]
            if pd.notna(r) and r >= pair_r:
                pairs.append((a, b))
    return pairs


def check_psychsyn(keyed_df, reference_keyed, pair_r, cutoff):
    """Psychometric synonyms: within-person correlation across item pairs.

    Returns (score_series, flag_series, n_pairs). Skipped (NaN scores, no
    flags) when the reference sample is too small to pair items stably or
    too few pairs clear the pairing threshold. A person's score is NaN when
    fewer than MIN_SYNONYM_PAIRS pairs are answered or a pair vector is
    constant (e.g. straight-liners — the invariability checks own that case).
    """
    score = pd.Series(np.nan, index=keyed_df.index)
    flag = pd.Series(False, index=keyed_df.index)

    n_ref = reference_keyed.dropna().shape[0]
    if n_ref < MIN_N_PSYCHSYN:
        note(f"only {n_ref} reference responses — psychometric-synonyms check "
             f"skipped (need >= {MIN_N_PSYCHSYN} for stable item pairing).")
        return score, flag, 0

    pairs = build_synonym_pairs(reference_keyed, pair_r)
    if len(pairs) < MIN_SYNONYM_PAIRS:
        note(f"only {len(pairs)} item pairs correlate >= {pair_r} in this "
             f"sample — psychometric-synonyms check skipped (need >= "
             f"{MIN_SYNONYM_PAIRS}; consider lowering --syn-pair-r).")
        return score, flag, len(pairs)

    a_items = [a for a, _ in pairs]
    b_items = [b for _, b in pairs]
    for idx, row in keyed_df.iterrows():
        va = row[a_items].to_numpy(dtype=float)
        vb = row[b_items].to_numpy(dtype=float)
        ok = ~(np.isnan(va) | np.isnan(vb))
        if ok.sum() < MIN_SYNONYM_PAIRS:
            continue
        score.at[idx] = safe_corr(va[ok], vb[ok])

    flag = score < cutoff
    flag = flag.fillna(False).astype(bool)
    return score, flag, len(pairs)


def check_evenodd(keyed_df, form_meta, cutoff):
    """Even-odd consistency (personal reliability; Johnson 2005).

    Each facet's items (in item-number order) are split into odd- and
    even-position halves; half-scores are means of keyed items. The two
    half-score vectors are correlated within person and Spearman-Brown
    corrected: r_sb = 2r / (1 + r). Facets must have >= 2 items — on the XS
    form (single-item facets) the caller skips this check. A person's score
    is NaN when fewer than half the facet pairs are computable or a half
    vector is constant.
    """
    facet_items = {}
    for num in keyed_df.columns:
        facet_items.setdefault(form_meta[num]["facet"], []).append(num)
    splits = []
    for facet, items in sorted(facet_items.items()):
        items = sorted(items)
        if len(items) < 2:
            continue
        splits.append((items[0::2], items[1::2]))
    if not splits:
        return pd.Series(np.nan, index=keyed_df.index), \
            pd.Series(False, index=keyed_df.index)

    # ceil, not floor: "at least half" of an odd count (e.g. 15 facets)
    # must round up (8), not down (7 -- which is LESS than half of 15).
    min_pairs = max(2, math.ceil(len(splits) / 2))
    score = pd.Series(np.nan, index=keyed_df.index)
    for idx, row in keyed_df.iterrows():
        odd_means, even_means = [], []
        for odd_items, even_items in splits:
            om = row[odd_items].mean()
            em = row[even_items].mean()
            if pd.isna(om) or pd.isna(em):
                continue
            odd_means.append(om)
            even_means.append(em)
        if len(odd_means) < min_pairs:
            continue
        r = safe_corr(odd_means, even_means)
        if np.isnan(r):
            continue
        if r == -1.0:
            score.at[idx] = -1.0  # Spearman-Brown undefined; clamp
        else:
            score.at[idx] = 2 * r / (1 + r)

    flag = (score < cutoff).fillna(False).astype(bool)
    return score, flag


def check_person_total(keyed_df, reference_keyed, cutoff):
    """Person-total correlation (Curran 2016).

    Each person's keyed item vector is correlated with the per-item mean of
    all OTHER reference respondents (leave-one-out when the person is in the
    reference). Low/negative r marks responding unrelated to the sample
    consensus. Skipped when the reference is too small.
    """
    score = pd.Series(np.nan, index=keyed_df.index)
    flag = pd.Series(False, index=keyed_df.index)

    ref = reference_keyed
    n_ref = ref.dropna().shape[0]
    if n_ref < MIN_N_PERSONTOTAL:
        note(f"only {n_ref} reference responses — person-total check skipped "
             f"(need >= {MIN_N_PERSONTOTAL}).")
        return score, flag

    col_sum = ref.sum(axis=0, skipna=True)
    col_cnt = ref.count(axis=0)
    in_ref = keyed_df.index.isin(ref.index)

    for pos, (idx, row) in enumerate(keyed_df.iterrows()):
        if in_ref[pos]:
            cnt = col_cnt - row.notna().astype(int)
            tot = col_sum - row.fillna(0)
        else:
            cnt, tot = col_cnt, col_sum
        with np.errstate(invalid="ignore", divide="ignore"):
            others_mean = tot / cnt.replace(0, np.nan)
        score.at[idx] = safe_corr(row.to_numpy(dtype=float),
                                  others_mean.to_numpy(dtype=float))

    flag = (score < cutoff).fillna(False).astype(bool)
    return score, flag


def check_drip(numeric_df, pairs, cutoff):
    """DRIP: sum of |item1 - item2| over the 15 pairs, reverse-keyed first.

    Reverse items are recoded 6 - x before differencing, consistent with the
    scoring key (Ruchensky et al. 2025, Table 1 note). Returns (score_series,
    flag_series). Rows missing any pair item get NaN (unscoreable) and are
    not flagged.
    """
    score = pd.Series(np.nan, index=numeric_df.index)
    flag = pd.Series(False, index=numeric_df.index)

    def recode(series, reverse):
        return (6 - series) if reverse else series

    per_pair_abs = []
    for p in pairs:
        v1 = recode(numeric_df[p["item1"]], p["reverse1"])
        v2 = recode(numeric_df[p["item2"]], p["reverse2"])
        per_pair_abs.append((v1 - v2).abs())
    diffs = pd.concat(per_pair_abs, axis=1)
    # A row is scoreable only if every pair item was answered.
    scoreable = diffs.notna().all(axis=1)
    score.loc[scoreable] = diffs[scoreable].sum(axis=1)
    flag.loc[scoreable] = score.loc[scoreable] >= cutoff
    return score, flag


def build_careless_flags(flag_columns, df):
    """Join the names of triggered flags per row into one comma-separated
    string (empty when a row triggered none)."""
    def join(row):
        return ",".join(name for name in flag_columns if bool(row[name]))
    return df.apply(join, axis=1)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Flag careless responding in a Qualtrics BFI-2 export.")
    parser.add_argument("csv", metavar="export",
                        help="Qualtrics CSV or TSV export (3 header rows).")
    parser.add_argument("-o", "--output", help="Output CSV path "
                        "(default: <input>_flagged.csv).")
    parser.add_argument("--longstring-run", type=int,
                        default=DEFAULT_LONGSTRING_RUN,
                        help=f"Longstring run cutoff (default {DEFAULT_LONGSTRING_RUN}).")
    parser.add_argument("--sd-cutoff", type=float, default=DEFAULT_SD_CUTOFF,
                        help="Within-person SD below this flags invariance "
                             f"(default {DEFAULT_SD_CUTOFF}).")
    parser.add_argument("--mahal-alpha", type=float, default=DEFAULT_MAHAL_ALPHA,
                        help=f"Mahalanobis chi-square alpha (default {DEFAULT_MAHAL_ALPHA}).")
    parser.add_argument("--syn-pair-r", type=float, default=DEFAULT_SYN_PAIR_R,
                        help="Sample correlation to pair items as psychometric "
                             f"synonyms (default {DEFAULT_SYN_PAIR_R}; Meade & Craig 2012).")
    parser.add_argument("--syn-cutoff", type=float, default=DEFAULT_SYN_CUTOFF,
                        help="Within-person synonym correlation below this "
                             f"flags (default {DEFAULT_SYN_CUTOFF}; heuristic).")
    parser.add_argument("--evenodd-cutoff", type=float,
                        default=DEFAULT_EVENODD_CUTOFF,
                        help="Even-odd (Spearman-Brown) consistency below this "
                             f"flags (default {DEFAULT_EVENODD_CUTOFF}; heuristic).")
    parser.add_argument("--persontotal-cutoff", type=float,
                        default=DEFAULT_PERSONTOTAL_CUTOFF,
                        help="Person-total correlation below this flags "
                             f"(default {DEFAULT_PERSONTOTAL_CUTOFF}; heuristic).")
    parser.add_argument("--drip-cutoff", type=int, default=DEFAULT_DRIP_CUTOFF,
                        help=f"DRIP score cutoff (default {DEFAULT_DRIP_CUTOFF}; "
                             "Ruchensky et al. recommend the 14-17 range).")
    parser.add_argument("--sec-per-item", type=float, default=DEFAULT_SEC_PER_ITEM,
                        help=f"Speed threshold, seconds/item (default {DEFAULT_SEC_PER_ITEM}).")
    args = parser.parse_args(argv)

    # Range-check thresholds so a typo (e.g. --mahal-alpha 5) doesn't
    # silently disable a check or flag every respondent while still
    # exiting 0. Correlation cutoffs live in [-1, 1] by definition;
    # everything else here is a count or a probability.
    if not (0 < args.mahal_alpha < 1):
        raise SystemExit(
            f"--mahal-alpha {args.mahal_alpha} must be strictly between 0 "
            "and 1 (0 or 1 makes the chi-square cutoff degenerate -- never "
            "or always flags)")
    range_checks = [
        ("--longstring-run", args.longstring_run, 1, None),
        ("--sd-cutoff", args.sd_cutoff, 0, None),
        ("--syn-pair-r", args.syn_pair_r, -1, 1),
        ("--syn-cutoff", args.syn_cutoff, -1, 1),
        ("--evenodd-cutoff", args.evenodd_cutoff, -1, 1),
        ("--persontotal-cutoff", args.persontotal_cutoff, -1, 1),
        ("--drip-cutoff", args.drip_cutoff, 0, None),
        ("--sec-per-item", args.sec_per_item, 0, None),
    ]
    for name, value, lo, hi in range_checks:
        # NaN/inf compare False against every bound below (a chained-
        # comparison quirk mahal_alpha's own explicit check above doesn't
        # share) -- reject them explicitly rather than silently pass through
        # a value that would disable the check (inf) or behave unpredictably
        # (NaN) while still exiting 0.
        if not math.isfinite(value) or value < lo or (hi is not None and value > hi):
            bound = f"[{lo}, {hi}]" if hi is not None else f">= {lo}"
            raise SystemExit(f"{name} {value} is out of range, expected a finite value {bound}")

    csv_path = Path(args.csv)
    if not csv_path.exists():
        raise SystemExit(f"Input file not found: {csv_path}")

    mapping = load_master_mapping()
    full_meta = load_form_meta("full", mapping)
    pairs = load_drip_pairs()
    cross_validate_pairs(pairs, full_meta)

    df, header_rows, sep, encoding = read_export(csv_path)
    collision = RESERVED_OUTPUT_COLUMNS & set(df.columns)
    if collision:
        raise SystemExit(
            f"{csv_path}: already has column(s) {sorted(collision)} that "
            "this script itself would add -- looks like an already-flagged "
            "output, not a fresh Qualtrics export. Re-run against the "
            "original export instead.")
    form_name, prefix_tag, item_cols = detect_form(df.columns)
    mode = infer_export_mode(df, item_cols)
    numeric_df, item_numbers = recode_items(df, item_cols, mode)
    n_items = len(item_numbers)
    fmt = "TSV" if sep == "\t" else "CSV"
    mode_label = "numeric values" if mode == "value" else "choice labels"
    print(f"Detected form: {form_name} ({prefix_tag}), {n_items} items, "
          f"{len(df)} responses. Input: {fmt} ({encoding}), {mode_label}.")

    form_meta = full_meta if form_name == "full" else \
        load_form_meta(form_name, mapping)
    keyed_df = keyed_scores(numeric_df, form_meta)

    flag_incomplete = numeric_df.isna().any(axis=1)
    flag_speed = check_speed(df, n_items, args.sec_per_item)

    # Within-person checks first: invariability ...
    longstring_max = check_longstring(numeric_df)
    flag_longstring = longstring_max >= args.longstring_run
    response_sd = check_variance(numeric_df)
    flag_variance = (response_sd < args.sd_cutoff).fillna(False).astype(bool)

    # ... whose flags define the reference sample for the sample-dependent
    # checks (sequential removal, Ward & Meade 2023): invariant rows are
    # excluded from reference statistics but still scored against them.
    invariant = flag_longstring | flag_variance
    reference = ~invariant & ~flag_incomplete
    n_removed = int((invariant & ~flag_incomplete).sum())
    if n_removed:
        note(f"sequential removal: {n_removed} invariant row(s) excluded from "
             f"reference statistics for Mahalanobis / synonyms / person-total.")

    mahal_d2, flag_mahalanobis = check_mahalanobis(
        numeric_df, numeric_df[reference], args.mahal_alpha)
    psychsyn_r, flag_psychsyn, n_syn_pairs = check_psychsyn(
        keyed_df, keyed_df[reference], args.syn_pair_r, args.syn_cutoff)
    if n_syn_pairs:
        note(f"psychometric synonyms: {n_syn_pairs} item pairs at "
             f"r >= {args.syn_pair_r}.")
    person_total_r, flag_persontotal = check_person_total(
        keyed_df, keyed_df[reference], args.persontotal_cutoff)

    result = df.copy()
    result["longstring_max"] = longstring_max
    result["response_sd"] = response_sd
    result["mahal_d2"] = mahal_d2
    result["psychsyn_r"] = psychsyn_r
    result["person_total_r"] = person_total_r
    result["flag_incomplete"] = flag_incomplete
    result["flag_speed"] = flag_speed
    result["flag_longstring"] = flag_longstring
    result["flag_variance"] = flag_variance
    result["flag_mahalanobis"] = flag_mahalanobis
    result["flag_psychsyn"] = flag_psychsyn
    result["flag_persontotal"] = flag_persontotal

    flag_columns = ["flag_incomplete", "flag_speed", "flag_longstring",
                    "flag_variance", "flag_mahalanobis", "flag_psychsyn",
                    "flag_persontotal"]

    if form_name == "extra-short":
        note("even-odd consistency requires multi-item facets — skipped on "
             "the XS form (single-item facets).")
    else:
        evenodd_r, flag_evenodd = check_evenodd(keyed_df, form_meta,
                                                args.evenodd_cutoff)
        result["evenodd_r"] = evenodd_r
        result["flag_evenodd"] = flag_evenodd
        flag_columns.append("flag_evenodd")

    if form_name == "full":
        drip_score, flag_drip = check_drip(numeric_df, pairs, args.drip_cutoff)
        result["drip_score"] = drip_score
        result["flag_drip"] = flag_drip
        flag_columns.append("flag_drip")
    else:
        note("DRIP requires all 60 items — skipped on this form.")

    result["Careless_Flags"] = build_careless_flags(flag_columns, result)

    print("\nPer-flag counts:")
    for name in flag_columns:
        print(f"  {name}: {int(result[name].sum())}")
    n_flagged = (result["Careless_Flags"] != "").sum()
    print(f"  any flag: {int(n_flagged)} / {len(result)}")

    out_path = Path(args.output) if args.output else \
        csv_path.with_name(csv_path.stem + "_flagged.csv")

    # Reattach the two extra Qualtrics header rows for the original columns so
    # the export retains its 3-row shape; new columns get blank header cells.
    extra = header_rows.iloc[1:3].copy()
    extra.columns = df.columns
    for col in result.columns:
        if col not in extra.columns:
            extra[col] = ""
    extra = extra[result.columns]
    out_df = pd.concat([extra, result], ignore_index=True)

    # Neutralize spreadsheet-formula injection (CSV injection): a cell
    # starting with =, +, -, @, tab, or CR is executed as a formula by
    # Excel/Sheets if the exported CSV is later opened there. Only the
    # ORIGINAL export's own columns are touched -- this script's own
    # computed metric columns (which can legitimately start with "-" for
    # a negative correlation) are never altered, or a downstream re-parse
    # of drip_score/psychsyn_r etc. would silently see strings, not floats.
    FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

    def neutralize(v):
        s = str(v)
        return "'" + s if s.startswith(FORMULA_PREFIXES) else v
    for col in df.columns:
        out_df[col] = out_df[col].map(neutralize)
    # The header row is itself a CSV cell: a source export with a column
    # name (DataExportTag) starting with one of these characters would
    # otherwise still produce a formula-capable header. Column names are
    # normally researcher-, not respondent-, controlled -- lower risk than
    # the data cells above -- but cheap to close given the mechanism's
    # already here.
    header_map = {c: neutralize(c) for c in df.columns}
    sanitized_names = list(header_map.values())
    if len(sanitized_names) != len(set(sanitized_names)):
        # e.g. source columns "=foo" and "'=foo" both sanitize to "'=foo" --
        # exceedingly unlikely for a real Qualtrics export, but silently
        # emitting two identically-named output columns is worse than
        # refusing to guess which is which.
        from collections import Counter
        dupes = sorted(n for n, c in Counter(sanitized_names).items() if c > 1)
        raise SystemExit(
            f"{csv_path}: column name(s) {dupes} collide after formula-"
            "injection sanitization -- rename the conflicting source "
            "column(s) before running this script.")
    out_df = out_df.rename(columns=header_map)

    out_sep = delimiter_for(out_path)
    # Sibling temp file + rename: an interruption mid-write can't leave a
    # truncated flagged export sitting at out_path.
    out_tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    out_df.to_csv(out_tmp, index=False, sep=out_sep, encoding="utf-8")
    out_tmp.replace(out_path)
    fmt_note = "TSV" if out_sep == "\t" else "CSV"
    print(f"\nWrote {out_path} ({fmt_note}, UTF-8 -- always UTF-8 regardless "
          f"of the input's encoding; delimiter matches the output extension)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
