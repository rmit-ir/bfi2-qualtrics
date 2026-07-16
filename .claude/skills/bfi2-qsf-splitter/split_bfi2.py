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

Output: BFI-2_Full.qsf, BFI-2_Short.qsf, BFI-2_ExtraShort.qsf in the CWD.
"""
import copy
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent

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


def build_grading_data(question_payload, mapping, include_facets):
    """One GradingData entry per matrix cell (ChoiceID x AnswerID).

    Matches the shape Qualtrics itself exports: each cell
    lists the point value it contributes to the item's domain category and,
    when enabled, its facet category. `index` is a running counter over the
    flattened choice-major grid.
    """
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
        assert FACET_DOMAIN[meta["facet"]] == meta["domain"], (
            f"mapping inconsistency: facet {meta['facet']!r} not in domain {meta['domain']!r}")
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
    """Validate the cell-based GradingData grid.

    GradingData has one entry per (ChoiceID, AnswerID) cell, so an item
    contributes 5 entries. Counts below are in items (distinct ChoiceIDs).
    """
    out = form["output_file"]
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

    per_domain = n_items // 5
    for name in DOMAINS:
        if items_per_category[DOMAIN_CATEGORY_ID[name]] != per_domain:
            raise AssertionError(
                f"{out}: domain {name} scores {items_per_category[DOMAIN_CATEGORY_ID[name]]} items, "
                f"expected {per_domain}")
    if form["facets"]:
        per_facet = n_items // 15
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
    grading_data, unmatched = build_grading_data(sq["Payload"], mapping, form["facets"])
    sq["Payload"]["GradingData"] = grading_data

    categories = build_scoring_categories(form["facets"], form["short"])
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


def main():
    if len(sys.argv) != 2:
        print("Usage: python3 split_bfi2.py <input.qsf>", file=sys.stderr)
        sys.exit(1)

    input_file = sys.argv[1]
    with open(input_file, "r", encoding="utf-8") as f:
        source = json.load(f)

    mapping = load_master_mapping()

    any_unmatched = False
    for form in FORMS:
        qsf, matched_count, category_count, unmatched = build_form_qsf(source, form, mapping)
        with open(form["output_file"], "w", encoding="utf-8") as f:
            json.dump(qsf, f, separators=(",", ":"))
        facet_note = f"{category_count - 5} facet" if form["facets"] else "no facet"
        print(f"Wrote {form['output_file']} ({form['question_id']}): {matched_count} items scored, "
              f"5 domain + {facet_note} categories")
        if unmatched:
            any_unmatched = True
            print(f"  UNMATCHED ({len(unmatched)}) -- these items got NO GradingData entry:")
            for text in unmatched:
                print(f"    - {text!r}")

    if any_unmatched:
        print("\nWarning: one or more items did not match master_mapping.json. "
              "Domain scores for those items will be missing. Check for edited "
              "item wording or unexpected characters before trusting the output.")
        sys.exit(1)


if __name__ == "__main__":
    main()
