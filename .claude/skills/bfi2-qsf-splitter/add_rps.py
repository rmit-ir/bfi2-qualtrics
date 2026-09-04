#!/usr/bin/env python3
"""Add the Risk Propensity Scale + one attention-check item to BFI-2_Full.qsf.

Builds the Prolific Part-2 survey (BFI-2 Full + RPS) for the quality-gated
multi-part study: reads ../../../output/BFI-2_Full.qsf, adds two new scored
questions, and writes ../../../output/BFI-2_Full_RPS.qsf. The pure 60-item
BFI-2_Full.qsf is left untouched (other tooling, e.g. verify_responses.py and
tests/test_qsf_parses.py, assumes the three output/ files are BFI-2-only).

Reuses split_bfi2.py's scoring_category_id() and the cell-based GradingData
shape it already proved against a real Qualtrics import/re-export/score
round-trip (see ../qsf-tools/SCHEMA.md).

RPS: Meertens, R. M., & Lion, R. (2008). Measuring an individual's tendency
to take risks: The Risk Propensity Scale. Journal of Applied Social
Psychology, 38(6), 1506-1520. 7 items, 9-point scale, items 1/2/3/5
reverse-keyed. Item 7 uses distinct endpoint anchors ("Risk avoider" .. "Risk
taker") rather than Disagree..Agree, so it's its own question -- a single
Qualtrics Matrix's Answers (columns) are shared across all its rows, so it
can't carry different anchor labels than items 1-6.

Scores are reported as sums, same convention as the BFI-2 domain/facet
scores (see ../../../README.md): Risk Propensity sums the 7 items
(reverse items scored 10-x on the 1-9 scale); mean = sum / 7.

Usage:
    python3 add_rps.py
"""
import copy
import json
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
REPO_ROOT = SKILL_DIR.parent.parent.parent
INPUT_FILE = REPO_ROOT / "output" / "BFI-2_Full.qsf"
OUTPUT_FILE = REPO_ROOT / "output" / "BFI-2_Full_RPS.qsf"

sys.path.insert(0, str(SKILL_DIR))
from split_bfi2 import scoring_category_id, find_element  # noqa: E402

RPS_CATEGORY_ID = scoring_category_id("BFI2-RPS-total")
ATTENTION_CATEGORY_ID = scoring_category_id("BFI2-RPS-attention-check")

ANSWER_LABELS = [
    "Totally Disagree", "Strongly Disagree", "Disagree", "Slightly Disagree",
    "Neutral", "Slightly Agree", "Agree", "Strongly Agree", "Totally Agree",
]
ANSWER_IDS = list(range(1, 10))  # 1..9

# (statement, reverse) for the 6 items that share the Disagree..Agree scale.
# Item 7 (distinct anchors) is handled separately below.
RPS_ITEMS = [
    ("Safety first.", True),
    ("I do not take risks with my health.", True),
    ("I prefer to avoid risks.", True),
    ("I take risks regularly.", False),
    ("I really dislike not knowing what is going to happen.", True),
    ("I usually view risks as a challenge.", False),
]
ATTENTION_CHECK_TEXT = (
    "To show you're reading carefully, please select 'Totally Agree' for this item."
)
ATTENTION_CORRECT_ANSWER_ID = 9  # "Totally Agree"

RPS_ITEM7_TEXT = "I see myself as a risk taker."
ITEM7_ANSWER_LABELS = [f"{i} - Risk avoider" if i == 1 else
                        f"{i} - Risk taker" if i == 9 else str(i)
                        for i in ANSWER_IDS]

QID_MAIN = "QID100"   # 6 RPS items + attention check
QID_ITEM7 = "QID101"  # RPS item 7 (distinct anchors)


def normal_grades_9pt():
    return {i: str(i) for i in ANSWER_IDS}


def reverse_grades_9pt():
    return {i: str(10 - i) for i in ANSWER_IDS}


def attention_grades():
    return {i: ("1" if i == ATTENTION_CORRECT_ANSWER_ID else "0") for i in ANSWER_IDS}


def base_question_payload(question_id, data_export_tag, question_text,
                           n_choices, answer_labels):
    return {
        "QuestionType": "Matrix", "Selector": "Likert", "SubSelector": "SingleAnswer",
        "QuestionID": question_id,
        "QuestionText": question_text,
        "QuestionDescription": question_text,
        "DataExportTag": data_export_tag,
        "Choices": {}, "ChoiceOrder": [],
        "Answers": {str(i): {"Display": lbl} for i, lbl in zip(ANSWER_IDS, answer_labels)},
        "AnswerOrder": ANSWER_IDS,
        "ChoiceDataExportTags": False,
        "NextChoiceId": n_choices + 1, "NextAnswerId": 10,
        "Configuration": {
            "QuestionDescriptionOption": "UseText", "TextPosition": "inline",
            "ChoiceColumnWidth": 25, "RepeatHeaders": "middle",
            "WhiteSpace": "OFF", "MobileFirst": True,
        },
        "Validation": {"Settings": {"ForceResponse": "RequestResponse",
                                     "ForceResponseType": "RequestResponse", "Type": "None"}},
        "Randomization": {"Type": "All", "TotalRandSubset": "", "Advanced": None},
        "DataVisibility": {"Private": False, "Hidden": False},
        "DefaultChoices": False,
        "GradingData": [],
        "Language": [],
    }


def build_main_question():
    """QID100: RPS items 1-6 + the attention-check row, 7 rows total."""
    rows = list(RPS_ITEMS) + [(ATTENTION_CHECK_TEXT, None)]  # reverse=None marks the AC row
    payload = base_question_payload(
        QID_MAIN, "RPS",
        "<div style=\"text-align: center;\"><strong>Risk Propensity</strong></div>"
        "<div>&nbsp;</div><div>Please indicate how much you agree or disagree "
        "with each statement below.</div>",
        len(rows), ANSWER_LABELS)

    grading = []
    index = 0
    for choice_id, (text, reverse) in enumerate(rows, start=1):
        payload["Choices"][str(choice_id)] = {"Display": text}
        payload["ChoiceOrder"].append(choice_id)
        if reverse is None:
            grades_map = attention_grades()
            category = ATTENTION_CATEGORY_ID
        else:
            grades_map = reverse_grades_9pt() if reverse else normal_grades_9pt()
            category = RPS_CATEGORY_ID
        for answer_id in ANSWER_IDS:
            grading.append({
                "AnswerID": answer_id, "ChoiceID": choice_id,
                "Grades": {category: grades_map[answer_id]},
                "index": index,
            })
            index += 1
    payload["GradingData"] = grading
    return payload


def build_item7_question():
    """QID101: RPS item 7, distinct 'Risk avoider'..'Risk taker' anchors."""
    payload = base_question_payload(
        QID_ITEM7, "RPS_Q7",
        "<div>Where do you see yourself on the scale below?</div>",
        1, ITEM7_ANSWER_LABELS)
    payload["Choices"]["1"] = {"Display": RPS_ITEM7_TEXT}
    payload["ChoiceOrder"] = [1]
    grades_map = normal_grades_9pt()  # not reverse-keyed
    payload["GradingData"] = [
        {"AnswerID": aid, "ChoiceID": 1, "Grades": {RPS_CATEGORY_ID: grades_map[aid]}, "index": i}
        for i, aid in enumerate(ANSWER_IDS)
    ]
    return payload


def assert_invariants(main_payload, item7_payload):
    """Raises AssertionError on failure -- explicit raises, not bare
    `assert`, so this can't be silently stripped by running under `python
    -O` (this is the safety net for a research-scoring generator)."""
    def cells_for(category_id, payload):
        return [g for g in payload["GradingData"] if category_id in g["Grades"]]

    main_rps_cells = cells_for(RPS_CATEGORY_ID, main_payload)
    item7_rps_cells = cells_for(RPS_CATEGORY_ID, item7_payload)
    rps_items_scored = len({g["ChoiceID"] for g in main_rps_cells}) + \
        len({g["ChoiceID"] for g in item7_rps_cells})
    if rps_items_scored != 7:
        raise AssertionError(f"expected 7 RPS items scored, got {rps_items_scored}")
    total_rps_cells = len(main_rps_cells) + len(item7_rps_cells)
    if total_rps_cells != 7 * 9:
        raise AssertionError(f"expected 63 RPS GradingData cells, got {total_rps_cells}")

    ac_cells = cells_for(ATTENTION_CATEGORY_ID, main_payload)
    if len(ac_cells) != 9:
        raise AssertionError(f"expected 9 attention-check cells, got {len(ac_cells)}")
    correct = [g for g in ac_cells if g["Grades"][ATTENTION_CATEGORY_ID] == "1"]
    if not (len(correct) == 1 and correct[0]["AnswerID"] == ATTENTION_CORRECT_ANSWER_ID):
        raise AssertionError(
            f"expected exactly one attention-check cell scoring 1, at "
            f"AnswerID {ATTENTION_CORRECT_ANSWER_ID}; got {correct}")

    for payload in (main_payload, item7_payload):
        for g in payload["GradingData"]:
            if len(g["Grades"]) != 1:
                raise AssertionError(
                    f"{payload['QuestionID']} cell has {len(g['Grades'])} "
                    "Grades keys, expected 1")


def main():
    if not INPUT_FILE.exists():
        print(f"Missing {INPUT_FILE} -- run split_bfi2.py first.", file=sys.stderr)
        sys.exit(1)

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        qsf = json.load(f)

    existing_qids = {e.get("PrimaryAttribute") for e in qsf["SurveyElements"]
                     if e.get("Element") == "SQ"}
    if QID_MAIN in existing_qids or QID_ITEM7 in existing_qids:
        # INPUT_FILE is always BFI-2_Full.qsf (never this script's own
        # output), so this shouldn't be reachable in normal use -- but
        # cheap insurance against silently producing a duplicate-question
        # qsf if that ever changes.
        raise SystemExit(
            f"{INPUT_FILE} already contains {QID_MAIN}/{QID_ITEM7} -- "
            "expected the plain BFI-2 Full form as input, not an "
            "already-RPS-augmented one.")

    main_payload = build_main_question()
    item7_payload = build_item7_question()
    assert_invariants(main_payload, item7_payload)

    survey_id = qsf["SurveyEntry"]["SurveyID"]
    elements = qsf["SurveyElements"]

    def sq_element(payload):
        return {"SurveyID": survey_id, "Element": "SQ", "PrimaryAttribute": payload["QuestionID"],
                "SecondaryAttribute": payload["QuestionText"][:100], "TertiaryAttribute": None,
                "Payload": payload}

    elements.append(sq_element(main_payload))
    elements.append(sq_element(item7_payload))

    bl = find_element(elements, lambda e: e["Element"] == "BL")
    block = bl["Payload"][0]
    block["BlockElements"].append({"Type": "Question", "QuestionID": QID_MAIN})
    block["BlockElements"].append({"Type": "Question", "QuestionID": QID_ITEM7})

    sco = find_element(elements, lambda e: e["Element"] == "SCO")
    sco["Payload"]["ScoringCategories"].append(
        {"ID": RPS_CATEGORY_ID, "Name": "Risk Propensity", "Description": ""})
    sco["Payload"]["ScoringCategories"].append(
        {"ID": ATTENTION_CATEGORY_ID, "Name": "Attention Check", "Description": ""})

    qc = find_element(elements, lambda e: e["Element"] == "QC")
    qc["SecondaryAttribute"] = "3"

    qsf["SurveyEntry"]["SurveyName"] = "BFI-2 Full + Risk Propensity Scale (scored)"

    # Write to a sibling temp file, then rename into place -- an
    # interruption mid-write can't leave a truncated OUTPUT_FILE.
    tmp = OUTPUT_FILE.with_suffix(OUTPUT_FILE.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(qsf, f, separators=(",", ":"))
    tmp.replace(OUTPUT_FILE)

    print(f"Wrote {OUTPUT_FILE.relative_to(REPO_ROOT)}: "
          f"60 BFI-2 items + 7 RPS items + 1 attention-check item scored, "
          f"20 BFI-2 categories + Risk Propensity + Attention Check")


if __name__ == "__main__":
    main()
