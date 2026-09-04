#!/usr/bin/env python3
"""Split a unified BFI-2 .qsf into three standalone, self-scoring .qsf files.

Reads a .qsf containing the BFI-2 Full (QID2), BFI-2-S Short (QID3), and
BFI-2-XS Extra-short (QID4) forms as separate blocks in one survey flow, and
writes three independent surveys — each containing only one form plus a
Qualtrics Scoring (SCO) configuration that computes the five BFI-2 domain
scores and (for the Full and Short forms) the 15 facet scores, with
reverse-coding, from that form's matrix responses.

Item -> domain/facet/reverse-key lookup is by statement TEXT
(master_mapping.json in this directory), not by ChoiceID position, so it
survives item reordering.

Scoring encoding (confirmed end to end: this script's output imports into
Qualtrics, re-exports unchanged, and scores real responses correctly):
GradingData has
one entry per matrix CELL — i.e. per (ChoiceID, AnswerID) pair, so an N-item
question yields N*5 entries. Each entry's "Grades" maps scoring-category ID ->
the points for that specific cell (a scalar, not a nested map): one key for
the item's domain and, when facets are enabled, one for its facet. See
../qsf-tools/SCHEMA.md.

Usage:
    python3 split_bfi2.py <input.qsf>

Output: BFI-2_Full.qsf, BFI-2_Short.qsf, BFI-2_ExtraShort.qsf, written to
../../../output/ (relative to this script, i.e. the repo's output/
directory) regardless of the current working directory.
"""
import copy
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
REPO_ROOT = SKILL_DIR.parent.parent.parent
OUTPUT_DIR = REPO_ROOT / "output"

# Canonical answer labels in AnswerID 1..5 order (Disagree strongly ..
# Agree strongly). AnswerID is trusted as an ordinal position (1=lowest,
# 5=highest) everywhere in this script -- checked against the source
# question's actual Answers before generation, not assumed.
CANONICAL_ANSWER_LABELS = {
    1: "disagree strongly", 2: "disagree a little", 3: "neutral; no opinion",
    4: "agree a little", 5: "agree strongly",
}

# Hard expected structure per form -- NOT derived from however many items
# happened to match master_mapping.json, so a balanced set of omissions
# (e.g. exactly one item missing from each domain) can't silently pass.
EXPECTED_ITEMS = {"QID2": 60, "QID3": 30, "QID4": 15}
EXPECTED_PER_DOMAIN = {"QID2": 12, "QID3": 6, "QID4": 3}
EXPECTED_PER_FACET = {"QID2": 4, "QID3": 2}  # QID4 has no facet scoring

# Qualtrics scoring-category IDs are "SC_" + 15 case-sensitive alphanumeric
# chars (no underscores in the body), e.g. "SC_eDlwlvh41Ka2IbY". Importing a
# survey whose IDs don't match this format fails. We generate them
# deterministically from a stable seed string so a category keeps the same ID
# across runs while staying unique and format-valid.
_SC_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"


def scoring_category_id(seed):
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    body = []
    for byte in digest:
        if len(body) == 15:
            break
        body.append(_SC_ALPHABET[byte % len(_SC_ALPHABET)])
    return "SC_" + "".join(body)

DOMAINS = [
    "Extraversion",
    "Agreeableness",
    "Conscientiousness",
    "Negative Emotionality",
    "Open-Mindedness",
]
DOMAIN_LETTER = {
    "Extraversion": "E",
    "Agreeableness": "A",
    "Conscientiousness": "C",
    "Negative Emotionality": "N",
    "Open-Mindedness": "O",
}
DOMAIN_CATEGORY_ID = {name: scoring_category_id(f"BFI2-domain-{name}") for name in DOMAINS}

# 15 facets in canonical order (three per domain, domains in DOMAINS order).
FACET_ORDER = [
    ("Extraversion", "Sociability"),
    ("Extraversion", "Assertiveness"),
    ("Extraversion", "Energy Level"),
    ("Agreeableness", "Compassion"),
    ("Agreeableness", "Respectfulness"),
    ("Agreeableness", "Trust"),
    ("Conscientiousness", "Organization"),
    ("Conscientiousness", "Productiveness"),
    ("Conscientiousness", "Responsibility"),
    ("Negative Emotionality", "Anxiety"),
    ("Negative Emotionality", "Depression"),
    ("Negative Emotionality", "Emotional Volatility"),
    ("Open-Mindedness", "Intellectual Curiosity"),
    ("Open-Mindedness", "Aesthetic Sensitivity"),
    ("Open-Mindedness", "Creative Imagination"),
]
FACET_DOMAIN = {facet: domain for domain, facet in FACET_ORDER}

# Points assigned to each of the five answers (AnswerID 1..5) for a normally-
# or reverse-keyed item. Real Qualtrics exports store points as strings.
NORMAL_GRADES = {1: "1", 2: "2", 3: "3", 4: "4", 5: "5"}
REVERSE_GRADES = {1: "5", 2: "4", 3: "3", 4: "2", 5: "1"}
ANSWER_IDS = [1, 2, 3, 4, 5]

FORMS = [
    {"question_id": "QID2", "survey_name": "BFI-2 Full measure (scored)",
     "output_file": "BFI-2_Full.qsf", "facets": True, "short": False},
    {"question_id": "QID3", "survey_name": "BFI-2-S Short form (scored)",
     "output_file": "BFI-2_Short.qsf", "facets": True, "short": True},
    {"question_id": "QID4", "survey_name": "BFI-2-XS Extra-short form (scored)",
     "output_file": "BFI-2_ExtraShort.qsf", "facets": False, "short": False},
]
for _f in FORMS:
    _f["output_path"] = OUTPUT_DIR / _f["output_file"]

SHARED_ELEMENTS = {"PROJ", "SO", "STAT"}


def normalize_text(text):
    text = text.replace("&nbsp;", " ").replace(" ", " ")
    text = text.replace("‘", "'").replace("’", "'")
    text = re.sub(r"\s+", " ", text).strip()
    return text.lower()


def facet_category_id(facet):
    return scoring_category_id(f"BFI2-facet-{FACET_DOMAIN[facet]}-{facet}")


def facet_label(facet, short_form):
    label = f"{DOMAIN_LETTER[FACET_DOMAIN[facet]]}: {facet}"
    if short_form:
        # Official BFI-2-S key: 2-item facets recommended only for n ~ 400+.
        label += " (2-item; use with n≳400)"
    return label


def load_master_mapping():
    with open(SKILL_DIR / "master_mapping.json", "r", encoding="utf-8") as f:
        raw = json.load(f)
    return {normalize_text(k): v for k, v in raw.items()}


def validate_answer_scale(question_payload, qid):
    """Confirm AnswerID 1..5 really means Disagree-strongly..Agree-strongly.

    NORMAL_GRADES/REVERSE_GRADES trust AnswerID as an ordinal position
    everywhere below — if the source question's Answers were reordered,
    relabeled, or given a different scale, that trust would silently
    produce wrong (possibly inverted) scores. Checked once per form
    against the actual source payload rather than assumed.
    """
    answers = question_payload.get("Answers") or {}
    got_ids = {int(k) for k in answers}
    if got_ids != set(ANSWER_IDS):
        raise ValueError(
            f"{qid}: Answers has IDs {sorted(got_ids)}, expected exactly "
            f"{ANSWER_IDS} (1=Disagree strongly .. 5=Agree strongly)")
    for aid, expected_label in CANONICAL_ANSWER_LABELS.items():
        actual = normalize_text(answers[str(aid)].get("Display", ""))
        if actual != expected_label:
            raise ValueError(
                f"{qid}: Answer {aid} is {actual!r}, expected {expected_label!r} "
                "-- source scale doesn't match the 5-point BFI-2 Likert scale "
                "this script assumes")


def build_grading_data(question_payload, mapping, include_facets, qid):
    """One GradingData entry per matrix cell (ChoiceID x AnswerID).

    Matches the shape Qualtrics itself exports: each cell
    lists the point value it contributes to the item's domain category and,
    when enabled, its facet category. `index` is a running counter over the
    flattened choice-major grid.
    """
    validate_answer_scale(question_payload, qid)
    grading = []
    unmatched = []
    index = 0
    for choice_id in question_payload["ChoiceOrder"]:
        choice = question_payload["Choices"][str(choice_id)]
        key = normalize_text(choice.get("Display", ""))
        meta = mapping.get(key)
        if meta is None:
            unmatched.append(choice.get("Display", ""))
            index += len(ANSWER_IDS)
            continue
        # A facet always belongs to its item's domain — guards mapping integrity
        # and the "facet items ⊆ domain items" invariant by construction.
        if FACET_DOMAIN[meta["facet"]] != meta["domain"]:
            raise ValueError(
                f"mapping inconsistency: facet {meta['facet']!r} not in "
                f"domain {meta['domain']!r}")
        grades_map = REVERSE_GRADES if meta["reverse"] else NORMAL_GRADES
        category_ids = [DOMAIN_CATEGORY_ID[meta["domain"]]]
        if include_facets:
            category_ids.append(facet_category_id(meta["facet"]))
        for answer_id in ANSWER_IDS:
            points = grades_map[answer_id]
            grading.append({
                "AnswerID": answer_id,
                "ChoiceID": int(choice_id),
                "Grades": {cid: points for cid in category_ids},
                "index": index,
            })
            index += 1
    return grading, unmatched


def build_scoring_categories(include_facets, short_form):
    categories = [{"ID": DOMAIN_CATEGORY_ID[name], "Name": name, "Description": ""}
                  for name in DOMAINS]
    if include_facets:
        for _domain, facet in FACET_ORDER:
            categories.append({"ID": facet_category_id(facet),
                               "Name": facet_label(facet, short_form),
                               "Description": ""})
    return categories


def assert_invariants(grading_data, categories, form):
    """Validate the cell-based GradingData grid against HARD expected counts.

    GradingData has one entry per (ChoiceID, AnswerID) cell, so an item
    contributes 5 entries. Counts below are in items (distinct ChoiceIDs).
    Expected totals come from EXPECTED_ITEMS/EXPECTED_PER_DOMAIN/
    EXPECTED_PER_FACET (the real published 60/30/15 structure) rather than
    being derived from n_items itself -- a derived check can't catch a
    *balanced* set of omissions (e.g. exactly one missing item per domain),
    since dividing the smaller total by 5 still comes out even.
    """
    out = form["output_file"]
    qid = form["question_id"]
    category_ids = {c["ID"] for c in categories}
    if len(category_ids) != len(categories):
        raise AssertionError(f"{out}: duplicate scoring-category IDs")

    expected_keys = 2 if form["facets"] else 1
    referenced = set()
    items_per_category = Counter()
    seen = set()  # (ChoiceID, category) counted once per item
    cells_per_choice = Counter()
    for g in grading_data:
        keys = set(g["Grades"])
        if len(keys) != expected_keys:
            raise AssertionError(
                f"{out}: cell (choice {g['ChoiceID']}, answer {g['AnswerID']}) has "
                f"{len(keys)} Grades keys, expected {expected_keys}")
        referenced |= keys
        cells_per_choice[g["ChoiceID"]] += 1
        for cat in keys:
            if (g["ChoiceID"], cat) not in seen:
                seen.add((g["ChoiceID"], cat))
                items_per_category[cat] += 1

    if referenced - category_ids:
        raise AssertionError(f"{out}: GradingData references undefined categories {referenced - category_ids}")
    if category_ids - referenced:
        raise AssertionError(f"{out}: SCO defines unused categories {category_ids - referenced}")

    n_items = len(cells_per_choice)
    for choice_id, n in cells_per_choice.items():
        if n != len(ANSWER_IDS):
            raise AssertionError(
                f"{out}: choice {choice_id} has {n} cells, expected {len(ANSWER_IDS)}")

    expected_total = EXPECTED_ITEMS[qid]
    if n_items != expected_total:
        raise AssertionError(f"{out}: {n_items} items scored, expected exactly {expected_total}")

    per_domain = EXPECTED_PER_DOMAIN[qid]
    for name in DOMAINS:
        if items_per_category[DOMAIN_CATEGORY_ID[name]] != per_domain:
            raise AssertionError(
                f"{out}: domain {name} scores {items_per_category[DOMAIN_CATEGORY_ID[name]]} items, "
                f"expected {per_domain}")
    if form["facets"]:
        per_facet = EXPECTED_PER_FACET[qid]
        for _domain, facet in FACET_ORDER:
            if items_per_category[facet_category_id(facet)] != per_facet:
                raise AssertionError(
                    f"{out}: facet {facet} scores {items_per_category[facet_category_id(facet)]} items, "
                    f"expected {per_facet}")


def find_element(elements, predicate):
    for el in elements:
        if predicate(el):
            return el
    return None


def build_form_qsf(source, form, mapping):
    qsf = copy.deepcopy(source)
    elements = qsf["SurveyElements"]
    question_id = form["question_id"]

    qsf["SurveyEntry"]["SurveyName"] = form["survey_name"]

    sq = find_element(elements, lambda e: e["Element"] == "SQ" and e["PrimaryAttribute"] == question_id)
    if sq is None:
        raise ValueError(f"Could not find SQ element for {question_id}")
    sq = copy.deepcopy(sq)
    grading_data, unmatched = build_grading_data(sq["Payload"], mapping, form["facets"], question_id)
    sq["Payload"]["GradingData"] = grading_data

    categories = build_scoring_categories(form["facets"], form["short"])
    # Skip the hard-count invariant check when items are already known to be
    # unmatched -- it would just raise "N items scored, expected 60" (true,
    # but useless), pre-empting main()'s graceful "UNMATCHED -- NOT writing"
    # report (which names the actual unmatched item text) with a bare
    # AssertionError/traceback instead.
    if not unmatched:
        assert_invariants(grading_data, categories, form)

    bl_element = find_element(elements, lambda e: e["Element"] == "BL")
    source_block = None
    for block in bl_element["Payload"]:
        if any(be.get("Type") == "Question" and be.get("QuestionID") == question_id
               for be in block.get("BlockElements", [])):
            source_block = block
            break
    if source_block is None:
        raise ValueError(f"Could not find block containing {question_id}")
    new_block = copy.deepcopy(source_block)
    new_block["Type"] = "Default"
    # Keep only the target question's entry -- the source block may contain
    # other questions/instructions co-located with this form; carrying them
    # over verbatim would leave BlockElements pointing at QuestionIDs whose
    # SQ elements this output doesn't include (a dangling reference that
    # can fail import).
    new_block["BlockElements"] = [
        be for be in new_block.get("BlockElements", [])
        if be.get("Type") == "Question" and be.get("QuestionID") == question_id
    ]

    new_bl = {
        "SurveyID": qsf["SurveyEntry"]["SurveyID"],
        "Element": "BL",
        "PrimaryAttribute": "Survey Blocks",
        "SecondaryAttribute": None,
        "TertiaryAttribute": None,
        "Payload": [new_block],
    }

    new_fl = {
        "SurveyID": qsf["SurveyEntry"]["SurveyID"],
        "Element": "FL",
        "PrimaryAttribute": "Survey Flow",
        "SecondaryAttribute": None,
        "TertiaryAttribute": None,
        "Payload": {
            "Flow": [{"ID": new_block["ID"], "Type": "Block", "FlowID": "FL_2"}],
            "Properties": {"Count": 2},
            "FlowID": "FL_1",
            "Type": "Root",
        },
    }

    new_sco = {
        "SurveyID": qsf["SurveyEntry"]["SurveyID"],
        "Element": "SCO",
        "PrimaryAttribute": "Scoring",
        "SecondaryAttribute": None,
        "TertiaryAttribute": None,
        "Payload": {
            "ScoringCategories": categories,
            "ScoringCategoryGroups": [],
            # Both point to a real category ID in a working export; match that.
            "DefaultScoringCategory": categories[0]["ID"],
            "ScoringSummaryCategory": categories[0]["ID"],
            "ScoringSummaryAfterQuestions": 0,
            "ScoringSummaryAfterSurvey": 0,
            "AutoScoringCategory": None,
            "IgnoreNullValues": True,
        },
    }

    rs_element = find_element(elements, lambda e: e["Element"] == "RS")
    qc_element = find_element(elements, lambda e: e["Element"] == "QC")
    new_qc = copy.deepcopy(qc_element)
    new_qc["SecondaryAttribute"] = "1"

    shared = [copy.deepcopy(e) for e in elements if e["Element"] in SHARED_ELEMENTS]

    qsf["SurveyElements"] = [new_bl, new_fl, *shared, new_sco, new_qc, sq, copy.deepcopy(rs_element)]
    n_items = len({g["ChoiceID"] for g in grading_data})
    return qsf, n_items, len(categories), unmatched


def _write_temp_json(path, obj):
    """Write JSON to a sibling .tmp file (not yet the real target)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, separators=(",", ":"))
    return tmp


def main():
    if len(sys.argv) != 2:
        print("Usage: python3 split_bfi2.py <input.qsf>", file=sys.stderr)
        sys.exit(1)

    input_file = sys.argv[1]
    with open(input_file, "r", encoding="utf-8") as f:
        source = json.load(f)

    mapping = load_master_mapping()

    # Build every form fully in memory first -- nothing is written to disk
    # until every form has succeeded. A partial/failed regeneration (one
    # form has unmatched items) must never overwrite the other, currently-
    # good, checked-in output files.
    built = []
    any_unmatched = False
    for form in FORMS:
        qsf, matched_count, category_count, unmatched = build_form_qsf(source, form, mapping)
        built.append((form, qsf, matched_count, category_count, unmatched))
        if unmatched:
            any_unmatched = True

    # All three forms are built from the same source/mapping, so an
    # unmatched item ANYWHERE signals a problem with the shared input (an
    # edited item statement, a stale master_mapping.json) that plausibly
    # affects the other forms too, even if their own match check happened
    # to pass. Report every form's status either way, but write NONE of
    # them if ANY form had a problem -- an "all 3 regenerated together, or
    # none of them" guarantee is easier to reason about than "2 of 3
    # updated, 1 left stale," which is what per-form gating alone allows.
    for form, qsf, matched_count, category_count, unmatched in built:
        facet_note = f"{category_count - 5} facet" if form["facets"] else "no facet"
        # "Built", not "Wrote" -- nothing has touched disk yet at this point
        # (see the commit phase below); printing "Wrote" here would be a
        # false success message if a later write/rename in this same run
        # fails.
        status = "UNMATCHED items" if unmatched else "Built"
        print(f"{status} {form['output_file']} ({form['question_id']}): {matched_count} items scored, "
              f"5 domain + {facet_note} categories")
        if unmatched:
            print(f"  UNMATCHED ({len(unmatched)}) -- these items got NO GradingData entry:")
            for text in unmatched:
                print(f"    - {text!r}")

    if any_unmatched:
        print("\nWarning: one or more items did not match master_mapping.json. "
              "NONE of the three forms were written (even the ones with no "
              "unmatched items of their own) -- they're built from the same "
              "source and mapping, so a partial regeneration would leave a "
              "mixed set. Check for edited item wording or unexpected "
              "characters before rerunning.")
        sys.exit(1)

    # Write every form's JSON to its own .tmp file first, and only start
    # renaming once every write above has succeeded -- an I/O failure
    # DURING a write (disk full on form 2 of 3) can't leave form 1 updated
    # and forms 2-3 stale. NOT a true cross-file transaction, though: the
    # three renames below are still sequential syscalls, each individually
    # atomic but not as a group -- a process kill between rename 1 and
    # rename 2 (a vanishingly small window; renames are near-instant,
    # no I/O in between) would leave a mixed set. Accepted residual risk
    # for this repo rather than the added complexity of a directory-swap
    # scheme, given how narrow that window is.
    OUTPUT_DIR.mkdir(exist_ok=True)
    to_commit = [(_write_temp_json(form["output_path"], qsf), form["output_path"])
                 for form, qsf, _, _, _ in built]
    for tmp, output_path in to_commit:
        tmp.replace(output_path)
    for _, output_path in to_commit:
        print(f"Wrote {output_path.name}")


if __name__ == "__main__":
    main()
