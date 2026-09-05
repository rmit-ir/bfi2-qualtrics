#!/usr/bin/env python3
"""PROTOTYPE, Phase 1 of 2 -- see plans (this session) for the full design.

Hand-authors PART of the Prolific Part-2 Survey Flow wiring directly inside
a generated .qsf, as an alternative to the fully-manual UI build documented
in ../../../docs/qualtrics-part2-wiring.md -- useful when the Qualtrics REST
API (which could otherwise build this via PUT /survey-definitions/{id}/flow)
is blocked by organizational policy. Import via the plain Qualtrics UI file
upload needs no API access at all.

Reads ../../../output/BFI-2_Full_RPS.qsf (untouched) and writes
../../../output/BFI-2_Full_RPS_Wired.qsf, adding:
  1. A new Descriptive Text/Graphic question (QuestionType "DB", Selector
     "TB") carrying gen_drip_js.py's generated snippet in QuestionJS -- the
     "JS on its own page" block wiring-doc S2 step 3 describes.
  2. Its own Survey Flow Block, positioned after the existing content
     block.
  3. An EmbeddedData flow node with the fail-closed defaults S2 step 1
     documents (attention_checks_passed=false, page_submit_seconds=0,
     __js_drip_score=-1, __js_bfi_answered=-1, prolific_pid/study_id/
     session_id piped from Prolific's PROLIFIC_PID/STUDY_ID/SESSION_ID).

Deliberately does NOT generate the Branch (attention-check condition), the
EmbeddedData copy step, or the WebService element -- those need a real
donor .qsf export (a small survey hand-built once in the Qualtrics UI
containing a Branch/WebService/redirect) before their exact field shapes
can be trusted; Qualtrics' REST API JSON schema is NOT confirmed to match
the .qsf export format for these element types. See the design plan for
why. This file is therefore a PARTIAL wiring -- import it to validate this
half (does it import cleanly? does the JS question render in Preview?),
not as a complete Part-2 survey.

QuestionType/Selector ("DB"/"TB") and the QuestionJS field name are
high-confidence from general QSF knowledge; the EmbeddedData flow element's
exact shape below is best-effort and should be spot-checked after import
against what Qualtrics actually stores (Survey Flow -> Embedded Data
element -> re-export), same as any other unverified piece here.

Usage:
    python3 wire_qualtrics_flow.py
"""
import hashlib
import json
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
REPO_ROOT = SKILL_DIR.parent.parent.parent
INPUT_FILE = REPO_ROOT / "output" / "BFI-2_Full_RPS.qsf"
OUTPUT_FILE = REPO_ROOT / "output" / "BFI-2_Full_RPS_Wired.qsf"

sys.path.insert(0, str(SKILL_DIR))
from split_bfi2 import find_element  # noqa: E402
import gen_drip_js  # noqa: E402

QID_JS = "QID102"  # QID100/QID101 already used by add_rps.py's RPS items

# Same deterministic-ID-from-seed technique as split_bfi2.scoring_category_id
# (not reused directly -- that one is SC_-prefixed and a fixed 15 chars;
# this generalizes the prefix). Deterministic so the ID stays stable across
# regenerations rather than changing on every run.
_ID_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"


def _deterministic_id(prefix, seed, length=15):
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    body = [_ID_ALPHABET[b % len(_ID_ALPHABET)] for b in digest[:length]]
    return prefix + "".join(body)


JS_BLOCK_ID = _deterministic_id("BL_", "wire_qualtrics_flow.js_block")

# Flow envelope shape (FlowID/Type/Flow[]/Properties.Count) and that Branch/
# EmbeddedData/WebService are real flow element types are confirmed against
# Qualtrics' live REST API reference (api.qualtrics.com, Survey Flows ->
# Update Flow / Get Flow). The .qsf's existing content-block flow node uses
# FlowID "FL_2" (root is "FL_1") -- continue the sequence.
EMBEDDED_DATA_FLOW_ID = "FL_3"
JS_BLOCK_FLOW_ID = "FL_4"


def build_js_question(js_snippet):
    return {
        "QuestionType": "DB",
        "Selector": "TB",
        "QuestionID": QID_JS,
        "QuestionText": "",
        "QuestionDescription": "Quality-gate signal computation (drip_score, bfi_answered)",
        "DataExportTag": "QID102",
        "Configuration": {"QuestionDescriptionOption": "UseText"},
        "QuestionJS": js_snippet,
        "Validation": {"Settings": {"ForceResponse": "OFF", "Type": "None"}},
        "DataVisibility": {"Private": False, "Hidden": False},
        "Language": [],
    }


def build_defaults_embedded_data():
    def field(name, value):
        return {
            "Description": name, "Type": "Custom", "Field": name,
            "VariableType": "String", "DataVisibility": [],
            "AnalyzeText": False, "Value": value,
        }
    return {
        "Type": "EmbeddedData",
        "FlowID": EMBEDDED_DATA_FLOW_ID,
        "EmbeddedData": [
            field("prolific_pid", "${e://Field/PROLIFIC_PID}"),
            field("prolific_study_id", "${e://Field/STUDY_ID}"),
            field("prolific_session_id", "${e://Field/SESSION_ID}"),
            field("attention_checks_passed", "false"),
            field("page_submit_seconds", "0"),
            field("__js_drip_score", "-1"),
            field("__js_bfi_answered", "-1"),
        ],
    }


def assert_invariants(qsf):
    fl = find_element(qsf["SurveyElements"], lambda e: e["Element"] == "FL")
    if not fl["Payload"].get("FlowID"):
        raise AssertionError("FL root has no FlowID")
    flow = fl["Payload"]["Flow"]
    flow_node_ids = [n.get("FlowID") for n in flow]
    if any(not fid for fid in flow_node_ids):
        raise AssertionError(f"flow node missing or empty FlowID: {flow}")
    flow_ids = flow_node_ids + [fl["Payload"]["FlowID"]]
    if len(flow_ids) != len(set(flow_ids)):
        raise AssertionError(f"duplicate FlowID in {flow_ids}")
    expected_count = 1 + len(flow)  # Root + every Flow[] entry
    if fl["Payload"]["Properties"]["Count"] != expected_count:
        raise AssertionError(
            f"Properties.Count {fl['Payload']['Properties']['Count']} != "
            f"expected {expected_count}")
    block_list = find_element(qsf["SurveyElements"], lambda e: e["Element"] == "BL")["Payload"]
    block_id_list = [b["ID"] for b in block_list]
    if len(block_id_list) != len(set(block_id_list)):
        raise AssertionError(f"duplicate block ID in {block_id_list}")
    block_ids = set(block_id_list)
    for node in flow:
        if node.get("Type") == "Block" and node.get("ID") not in block_ids:
            raise AssertionError(f"flow node references missing block {node.get('ID')}")
    sq_elements = [e for e in qsf["SurveyElements"] if e["Element"] == "SQ"]
    qid_list = [e["PrimaryAttribute"] for e in sq_elements]
    if len(qid_list) != len(set(qid_list)):
        raise AssertionError(f"duplicate question ID in {qid_list}")
    for e in sq_elements:
        if e["PrimaryAttribute"] != e["Payload"]["QuestionID"]:
            raise AssertionError(
                f"SQ PrimaryAttribute {e['PrimaryAttribute']!r} != "
                f"Payload.QuestionID {e['Payload']['QuestionID']!r}")
    export_tags = [e["Payload"]["DataExportTag"] for e in sq_elements]
    if len(export_tags) != len(set(export_tags)):
        raise AssertionError(f"duplicate DataExportTag in {export_tags}")
    qid_set = set(qid_list)
    for block in block_list:
        for el in block["BlockElements"]:
            if el.get("Type") == "Question" and el.get("QuestionID") not in qid_set:
                raise AssertionError(
                    f"block {block['ID']} references missing question {el.get('QuestionID')}")
    qc = find_element(qsf["SurveyElements"], lambda e: e["Element"] == "QC")
    if int(qc["SecondaryAttribute"]) != len(qid_list):
        raise AssertionError(
            f"QC {qc['SecondaryAttribute']} != actual question count {len(qid_list)}")


def apply_wiring(qsf, js_snippet):
    """Mutates qsf IN PLACE: adds the JS question + its block + the
    EmbeddedData defaults, positioned per the module docstring. No file
    I/O, so main() and tests exercise the exact same logic instead of two
    copies that can drift. All collision checks run before any mutation --
    a rejected input is left completely untouched, never partially
    modified.
    """
    elements = qsf["SurveyElements"]

    sq_elements = [e for e in elements if e.get("Element") == "SQ"]
    existing_qids = {e.get("PrimaryAttribute") for e in sq_elements}
    if QID_JS in existing_qids:
        raise SystemExit(
            f"input already contains {QID_JS} -- expected the plain "
            "BFI-2 Full + RPS form as input, not an already-wired one.")
    existing_export_tags = {e["Payload"].get("DataExportTag") for e in sq_elements}
    new_export_tag = build_js_question("")["DataExportTag"]
    if new_export_tag in existing_export_tags:
        raise SystemExit(f"input already contains DataExportTag {new_export_tag!r}")

    bl = find_element(elements, lambda e: e["Element"] == "BL")
    if JS_BLOCK_ID in {b["ID"] for b in bl["Payload"]}:
        raise SystemExit(f"input already contains block {JS_BLOCK_ID}")

    fl = find_element(elements, lambda e: e["Element"] == "FL")
    flow = fl["Payload"]["Flow"]
    existing_flow_ids = {n.get("FlowID") for n in flow} | {fl["Payload"].get("FlowID")}
    for new_flow_id in (EMBEDDED_DATA_FLOW_ID, JS_BLOCK_FLOW_ID):
        if new_flow_id in existing_flow_ids:
            raise SystemExit(f"input already contains FlowID {new_flow_id}")
    # Derived from the FLOW's own pre-existing Block-type node, not
    # bl["Payload"][0] -- keeps the block list and the flow order coupled
    # through the flow itself rather than an assumed list-order match.
    content_flow_index = next(
        i for i, node in enumerate(flow) if node.get("Type") == "Block")
    content_block_id = flow[content_flow_index]["ID"]
    if content_block_id != bl["Payload"][0]["ID"]:
        raise SystemExit(
            "the flow's content Block node doesn't match BL.Payload[0] -- "
            "input has an unexpected shape this script wasn't written for")

    survey_id = qsf["SurveyEntry"]["SurveyID"]
    js_payload = build_js_question(js_snippet)
    elements.append({
        "SurveyID": survey_id, "Element": "SQ", "PrimaryAttribute": QID_JS,
        "SecondaryAttribute": js_payload["QuestionDescription"],
        "TertiaryAttribute": None, "Payload": js_payload,
    })

    bl["Payload"].append({
        "Type": "Standard",
        "Description": "Quality-gate signal computation (own page)",
        "ID": JS_BLOCK_ID,
        "BlockElements": [{"Type": "Question", "QuestionID": QID_JS}],
    })

    flow.insert(0, build_defaults_embedded_data())
    # +2, not +1: the EmbeddedData insert above shifted content_flow_index
    # forward by one.
    flow.insert(content_flow_index + 2,
                {"ID": JS_BLOCK_ID, "Type": "Block", "FlowID": JS_BLOCK_FLOW_ID})
    fl["Payload"]["Properties"]["Count"] = 1 + len(flow)

    qc = find_element(elements, lambda e: e["Element"] == "QC")
    qc["SecondaryAttribute"] = str(int(qc["SecondaryAttribute"]) + 1)

    qsf["SurveyEntry"]["SurveyName"] = (
        qsf["SurveyEntry"]["SurveyName"] + " [PROTOTYPE: partial flow wiring, phase 1]")

    assert_invariants(qsf)
    return qsf


def main():
    if not INPUT_FILE.exists():
        print(f"Missing {INPUT_FILE} -- run add_rps.py first.", file=sys.stderr)
        sys.exit(1)

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        qsf = json.load(f)

    js_snippet = gen_drip_js.render(gen_drip_js.load_pairs())
    apply_wiring(qsf, js_snippet)

    # Write to a sibling temp file, then rename into place -- an
    # interruption mid-write can't leave a truncated OUTPUT_FILE.
    tmp = OUTPUT_FILE.with_suffix(OUTPUT_FILE.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(qsf, f, separators=(",", ":"))
    tmp.replace(OUTPUT_FILE)
    print(f"Wrote {OUTPUT_FILE.name} (PROTOTYPE, phase 1 only -- see module "
          f"docstring): added {QID_JS} + its own block + EmbeddedData "
          f"defaults. Branch/WebService NOT generated yet.")


if __name__ == "__main__":
    main()
