#!/usr/bin/env python3
"""Load, validate, and canonically hash the DRIP (Ruchensky, Edens, &
Donnellan, 2025) item-pair specification -- the single source of truth
for wire_qualtrics_flow.py's generated Web Service request, and (via a
vendored copy + canonical_spec_sha256()) ase2-ai-mode's server-side DRIP
calculator. See docs/qualtrics-part2-wiring.md for the cross-repo sync
contract this enables.

Split out of gen_drip_js.py (client-side JS generation was abandoned
after live testing established Question JavaScript never executes in
the target Qualtrics account -- see docs/qualtrics-part2-wiring.md for
the full history). This module owns pair loading/validation/hashing
only; it computes nothing about any particular response.

Stdlib only -- verify_responses.py's pandas/numpy/scipy dependency stays
opt-in for the offline detector; this module is imported by qsf-generation
tooling and must stay lightweight.
"""
import csv
import hashlib
import json
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
REPO_ROOT = SKILL_DIR.parent.parent.parent
PAIRS_FILE = REPO_ROOT / "response_verification" / "drip_item_pairs.tsv"

sys.path.insert(0, str(SKILL_DIR))
from split_bfi2 import normalize_text, load_master_mapping  # noqa: E402

REQUIRED_COLUMNS = ("Item1", "Item2", "Reverse1", "Reverse2", "Domain", "Facet")
EXPECTED_PAIR_COUNT = 15
# Fixed field order for canonical_spec_bytes() -- both this repo and
# ase2-ai-mode's vendored copy must serialize in exactly this order for
# the hash to be meaningful as a sync check.
CANONICAL_FIELD_ORDER = ("item1", "item2", "reverse1", "reverse2", "domain", "facet")


def _parse_strict_bool(raw, context):
    """Only literal 'true'/'false' (case-normalized) are valid -- unlike a
    bare `== "true"` check, a typo like 'tru' fails loudly instead of
    silently becoming False."""
    text = raw.strip().lower()
    if text == "true":
        return True
    if text == "false":
        return False
    raise SystemExit(f"{context}: expected literal 'true' or 'false', got {raw!r}")


def load_pairs(path=None):
    """(item1, item2, reverse1, reverse2, domain, facet) dicts, in TSV row
    order. Structural validation only (validate_pair_structure()) -- does
    NOT cross-validate against any particular survey's item metadata; call
    cross_validate_pairs() explicitly with a specific QSF's item_meta for
    that (see item_meta_from_qsf())."""
    path = path or PAIRS_FILE
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"{path}: missing required column(s): {missing}")
        pairs = []
        for i, row in enumerate(reader, start=1):
            pairs.append({
                "item1": int(row["Item1"]), "item2": int(row["Item2"]),
                "reverse1": _parse_strict_bool(row["Reverse1"], f"{path} row {i} Reverse1"),
                "reverse2": _parse_strict_bool(row["Reverse2"], f"{path} row {i} Reverse2"),
                "domain": row["Domain"].strip(), "facet": row["Facet"].strip(),
            })
    validate_pair_structure(pairs)
    return pairs


def validate_pair_structure(pairs):
    """Raises SystemExit on failure. Explicit raises, not bare `assert`,
    so this can't be silently stripped under `python -O`."""
    if len(pairs) != EXPECTED_PAIR_COUNT:
        raise SystemExit(f"expected {EXPECTED_PAIR_COUNT} DRIP pairs, found {len(pairs)}")
    seen_items = set()
    for p in pairs:
        for item in (p["item1"], p["item2"]):
            if item in seen_items:
                raise SystemExit(f"item {item} appears in more than one DRIP pair")
            seen_items.add(item)
        if not p["domain"]:
            raise SystemExit(f"pair with items {p['item1']}/{p['item2']} has empty domain")
        if not p["facet"]:
            raise SystemExit(f"pair with items {p['item1']}/{p['item2']} has empty facet")


def item_meta_from_qsf(qsf, qid="QID2"):
    """item number -> {domain, facet, reverse}, via a specific QSF's real
    Choices -- parameterized on the actual QSF being validated against,
    not a hardcoded committed file, so a caller wiring e.g.
    output/BFI-2_Full_RPS.qsf cross-validates against THAT file's item
    text, not silently against a different one."""
    mapping = load_master_mapping()
    sq = next(e for e in qsf["SurveyElements"]
              if e["Element"] == "SQ" and e["PrimaryAttribute"] == qid)
    return {int(cid): mapping[normalize_text(c["Display"])]
            for cid, c in sq["Payload"]["Choices"].items()}


def cross_validate_pairs(pairs, item_meta):
    """Raises SystemExit if a pair's facet/domain/reverse disagrees with
    item_meta (see item_meta_from_qsf()) -- a corrupted pair here is
    worse than in the offline detector: it ends up baked into a live
    Qualtrics survey's generated Web Service request."""
    problems = []
    for i, p in enumerate(pairs, start=1):
        for side in ("1", "2"):
            item = p[f"item{side}"]
            meta = item_meta.get(item)
            if meta is None:
                problems.append(f"pair {i}: item {item} not present in the target QSF")
                continue
            if bool(meta["reverse"]) != p[f"reverse{side}"]:
                problems.append(
                    f"pair {i}: item {item} reverse={meta['reverse']} in "
                    f"master_mapping.json but reverse{side}={p[f'reverse{side}']} in the TSV")
            if meta["facet"] != p["facet"]:
                problems.append(
                    f"pair {i}: item {item} facet {meta['facet']!r} in "
                    f"master_mapping.json but table facet {p['facet']!r}")
            if meta["domain"] != p["domain"]:
                problems.append(
                    f"pair {i}: item {item} domain {meta['domain']!r} in "
                    f"master_mapping.json but table domain {p['domain']!r}")
    if problems:
        raise SystemExit(
            "drip_item_pairs.tsv disagrees with the target QSF's item metadata:\n  "
            + "\n  ".join(problems))


def canonical_spec_bytes(pairs):
    """Deterministic UTF-8 bytes for hashing: compact JSON array, TSV row
    order, each element with real JSON booleans/integers and exactly the
    CANONICAL_FIELD_ORDER keys in that order. ase2-ai-mode's vendored
    copy must reproduce these exact bytes (same field order, same
    separators, same row order) for canonical_spec_sha256() to be a
    meaningful sync check rather than a coincidence."""
    rows = [{k: p[k] for k in CANONICAL_FIELD_ORDER} for p in pairs]
    return json.dumps(rows, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def canonical_spec_sha256(pairs):
    return hashlib.sha256(canonical_spec_bytes(pairs)).hexdigest()


def main():
    pairs = load_pairs()
    print(f"{len(pairs)} pairs loaded from {PAIRS_FILE.relative_to(REPO_ROOT)}")
    print(f"canonical_spec_sha256: {canonical_spec_sha256(pairs)}")


if __name__ == "__main__":
    main()
