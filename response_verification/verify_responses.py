#!/usr/bin/env python3
"""Flag careless responding in a Qualtrics BFI-2 response export.

Reads a Qualtrics CSV export of any BFI-2 form (Full / Short / Extra-short),
runs a set of post-hoc careless-responding checks per respondent, and writes
the data back out with one boolean column per check plus a joined
``Careless_Flags`` summary column.

The checks are post-hoc only — the surveys contain no instructed-response or
bogus items and no page timers, so those methods do not apply. Implemented:

  * ``flag_speed``       total ``Duration (in seconds)`` below 2s/item.
  * ``flag_longstring``  longest run of identical raw answers >= cutoff.
  * ``flag_mahalanobis`` multivariate outlier vs. the sample centroid.
  * ``flag_drip``        (Full form only) Detection of Response Inconsistency
                         Procedure — sum of |item1 - item2| over 15 highly
                         correlated item pairs, reverse-keyed first
                         (Ruchensky et al.). Flagged at >= 14.

Usage:
    python3 verify_responses.py <export.csv|export.tsv> [-o flagged.csv]
        [--longstring-run N] [--drip-cutoff N] [--sec-per-item S]
        [--mahal-alpha A]

The export may be CSV or TSV (inferred from the extension) in either UTF-8 or
UTF-16 (sniffed from the byte-order mark), with item cells as choice labels or
numeric values (inferred from the data) — matching whatever Qualtrics emitted.

Requires pandas, numpy, scipy (see requirements.txt); the rest of the repo is
stdlib-only.
"""
import argparse
import json
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

DURATION_COL = "Duration (in seconds)"

DEFAULT_LONGSTRING_RUN = 10
DEFAULT_DRIP_CUTOFF = 14
DEFAULT_SEC_PER_ITEM = 2.0
DEFAULT_MAHAL_ALPHA = 0.001


def normalize_text(text):
    """Match split_bfi2.py:normalize_text — collapse nbsp/whitespace, curly
    quotes, and case, so answer-text and item-text comparisons are robust."""
    text = text.replace("&nbsp;", " ").replace(" ", " ")
    text = text.replace("‘", "'").replace("’", "'")
    text = re.sub(r"\s+", " ", text).strip()
    return text.lower()


def load_master_mapping():
    with open(MASTER_MAPPING, encoding="utf-8") as f:
        raw = json.load(f)
    return {normalize_text(k): v for k, v in raw.items()}


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
        })
    return pairs


def cross_validate_pairs(pairs, mapping, choice_meta):
    """Abort if drip_item_pairs.tsv disagrees with master_mapping.json.

    choice_meta maps item number -> mapping entry for the loaded (full) form.
    Validates that each pair's items exist, share the pair's facet/domain, and
    that the TSV reverse flags match the master key.
    """
    problems = []
    for p in pairs:
        for side in ("1", "2"):
            item = p[f"item{side}"]
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


def check_speed(df, n_items, sec_per_item):
    """flag_speed: total Duration below sec_per_item * n_items."""
    if DURATION_COL not in df.columns:
        print(f"  note: no {DURATION_COL!r} column — speed check skipped.",
              file=sys.stderr)
        return pd.Series(False, index=df.index)
    duration = pd.to_numeric(df[DURATION_COL], errors="coerce")
    threshold = sec_per_item * n_items
    return duration < threshold


def check_longstring(numeric_df):
    """Longest run of identical *raw* (pre-reverse) answers per respondent.

    Returns (max_run_series, ...). NaN breaks a run. A respondent with no
    answered items has a run of 0.
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


def check_mahalanobis(numeric_df, alpha):
    """Mahalanobis D^2 of each complete row vs. the sample centroid.

    Returns (d2_series, flag_series). Rows with any missing item get NaN D^2
    and are not flagged. Skipped (all NaN / no flags) when the number of
    complete rows is <= n_items, where the covariance is unstable.
    """
    n_items = numeric_df.shape[1]
    d2 = pd.Series(np.nan, index=numeric_df.index)
    flag = pd.Series(False, index=numeric_df.index)

    complete_mask = numeric_df.notna().all(axis=1)
    complete = numeric_df[complete_mask]
    if complete.shape[0] <= n_items:
        print(f"  note: only {complete.shape[0]} complete responses for "
              f"{n_items} items — Mahalanobis check skipped (covariance "
              f"unstable; need > n_items).", file=sys.stderr)
        return d2, flag

    X = complete.to_numpy(dtype=float)
    centroid = X.mean(axis=0)
    cov = np.cov(X, rowvar=False)
    inv = np.linalg.pinv(cov)  # pseudo-inverse for numerical stability
    diff = X - centroid
    dist2 = np.einsum("ij,jk,ik->i", diff, inv, diff)
    d2.loc[complete.index] = dist2

    crit = chi2.ppf(1 - alpha, df=n_items)
    flag.loc[complete.index] = dist2 > crit
    return d2, flag


def check_drip(numeric_df, item_numbers, pairs, cutoff):
    """DRIP: sum of |item1 - item2| over the 15 pairs, reverse-keyed first.

    Reverse items are recoded 6 - x before differencing, consistent with the
    scoring key (Ruchensky et al.). Returns (score_series, flag_series). Rows
    missing any pair item get NaN (unscoreable) and are not flagged.
    """
    present = set(item_numbers)
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
    parser.add_argument("--drip-cutoff", type=int, default=DEFAULT_DRIP_CUTOFF,
                        help=f"DRIP score cutoff (default {DEFAULT_DRIP_CUTOFF}).")
    parser.add_argument("--sec-per-item", type=float, default=DEFAULT_SEC_PER_ITEM,
                        help=f"Speed threshold, seconds/item (default {DEFAULT_SEC_PER_ITEM}).")
    parser.add_argument("--mahal-alpha", type=float, default=DEFAULT_MAHAL_ALPHA,
                        help=f"Mahalanobis chi-square alpha (default {DEFAULT_MAHAL_ALPHA}).")
    args = parser.parse_args(argv)

    csv_path = Path(args.csv)
    if not csv_path.exists():
        raise SystemExit(f"Input file not found: {csv_path}")

    mapping = load_master_mapping()
    # Map item number -> mapping entry using the Full-form item text so DRIP
    # pair validation always has all 60 items available.
    full_qsf = json.loads((REPO_ROOT / "output" / "BFI-2_Full.qsf").read_text(encoding="utf-8"))
    sq = next(e for e in full_qsf["SurveyElements"]
              if e["Element"] == "SQ" and e["PrimaryAttribute"] == "QID2")
    choice_meta = {}
    for cid, choice in sq["Payload"]["Choices"].items():
        choice_meta[int(cid)] = mapping[normalize_text(choice["Display"])]

    pairs = load_drip_pairs()
    cross_validate_pairs(pairs, mapping, choice_meta)

    df, header_rows, sep, encoding = read_export(csv_path)
    form_name, prefix_tag, item_cols = detect_form(df.columns)
    mode = infer_export_mode(df, item_cols)
    numeric_df, item_numbers = recode_items(df, item_cols, mode)
    n_items = len(item_numbers)
    fmt = "TSV" if sep == "\t" else "CSV"
    mode_label = "numeric values" if mode == "value" else "choice labels"
    print(f"Detected form: {form_name} ({prefix_tag}), {n_items} items, "
          f"{len(df)} responses. Input: {fmt} ({encoding}), {mode_label}.")

    flag_incomplete = numeric_df.isna().any(axis=1)

    flag_speed = check_speed(df, n_items, args.sec_per_item)
    longstring_max = check_longstring(numeric_df)
    flag_longstring = longstring_max >= args.longstring_run
    mahal_d2, flag_mahalanobis = check_mahalanobis(numeric_df, args.mahal_alpha)

    result = df.copy()
    result["longstring_max"] = longstring_max
    result["mahal_d2"] = mahal_d2
    result["flag_incomplete"] = flag_incomplete
    result["flag_speed"] = flag_speed
    result["flag_longstring"] = flag_longstring
    result["flag_mahalanobis"] = flag_mahalanobis

    flag_columns = ["flag_incomplete", "flag_speed", "flag_longstring",
                    "flag_mahalanobis"]

    if form_name == "full":
        drip_score, flag_drip = check_drip(numeric_df, item_numbers, pairs,
                                           args.drip_cutoff)
        result["drip_score"] = drip_score
        result["flag_drip"] = flag_drip
        flag_columns.append("flag_drip")
    else:
        print("  note: DRIP requires all 60 items — skipped on this form.",
              file=sys.stderr)

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
    out_df.to_csv(out_path, index=False)
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
