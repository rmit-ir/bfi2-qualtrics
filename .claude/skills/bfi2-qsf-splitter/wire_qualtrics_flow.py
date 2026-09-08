#!/usr/bin/env python3
"""Generates the Prolific Part-2 Survey Flow's real-time quality-gate Web
Service element and writes ../../../output/BFI-2_Full_RPS_Wired.qsf.

History: the original design computed drip_score/bfi_answered client-side
in a hidden question's custom JavaScript, then a synchronous Web Service
element sent the two pre-computed values. Extensive live testing against a
real Qualtrics account (both Preview and a published survey, both Classic
and New Survey Taking Experience) established that Question JavaScript
never executes at all in that account -- even the most trivial possible
script never wrote its embedded-data value, every time. That design is
abandoned; this script no longer generates any question or custom JS.

Instead: the Web Service element sends all 60 raw BFI-2 item answers
(`${q://QID2/SelectedAnswerRecode/<ChoiceID>}` per item -- a Matrix row's
recoded numeric answer, confirmed live via real completed-response exports
to resolve correctly when piped directly into a Web Service Body Parameter,
outside of any JavaScript), the RPS attention-check's native scoring-
category score (`${gr://SC_.../Score}`, also confirmed live), and
Qualtrics's built-in `Q_RecaptchaScore`. drip_score/bfi_answered are
computed server-side (ase2-ai-mode) from the raw values -- see
../../../docs/qualtrics-part2-wiring.md for the full request/response
contract and the ase2-ai-mode handoff spec.

Reads ../../../output/BFI-2_Full_RPS.qsf (untouched) and writes
../../../output/BFI-2_Full_RPS_Wired.qsf, adding two Survey-Flow nodes:
  1. An EmbeddedData node (fail-closed defaults) before the content block.
  2. A WebService node (the quality-gate POST) after the content block.

Still NOT generated (needs a real donor .qsf export before its exact field
shape can be trusted, same reasoning as before): the Branch/redirect/
EndSurvey nodes, and a custom auth-header entry (only an empty `Headers: []`
is confirmed). The committed output is not deployable as-is -- see its
SurveyName suffix and the wiring doc for exactly what's still manual.

Usage:
    python3 wire_qualtrics_flow.py
    python3 wire_qualtrics_flow.py --transport-validation
"""
import copy
import json
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
REPO_ROOT = SKILL_DIR.parent.parent.parent
INPUT_FILE = REPO_ROOT / "output" / "BFI-2_Full_RPS.qsf"
OUTPUT_FILE = REPO_ROOT / "output" / "BFI-2_Full_RPS_Wired.qsf"

sys.path.insert(0, str(SKILL_DIR))
from split_bfi2 import find_element  # noqa: E402
import add_rps  # noqa: E402
import drip_spec  # noqa: E402

EXPECTED_BFI_ITEMS = 60
CONTRACT_VERSION = "personality-raw-bfi-v1"

# Flow envelope shape (FlowID/Type/Flow[]/Properties.Count), and the
# EmbeddedData/WebService element shapes below, are confirmed against a
# real donor .qsf exported from this project's own test survey this
# session (not guessed from the REST API's JSON schema, which earlier
# investigation established is NOT confirmed to match the .qsf export
# format for these element types). The input's existing content-block flow
# node uses FlowID "FL_2" (root is "FL_1") -- continue the sequence.
EMBEDDED_DATA_FLOW_ID = "FL_3"
WEB_SERVICE_FLOW_ID = "FL_4"

# Safe placeholders -- never the real endpoint/secret. See the module
# docstring: the custom auth header this needs is deliberately NOT
# generated (unconfirmed .qsf shape), so there's no placeholder header to
# match this URL; add the real header manually in the Qualtrics UI.
PLACEHOLDER_URL = "https://example.invalid/api/qualtrics/verdict"
PLACEHOLDER_REVIEW_FALLBACK_URL = "https://example.invalid/review-fallback"

NOT_DEPLOYABLE_SUFFIX = " [quality Web Service generated; auth and routing manual]"


def find_bfi_question(qsf, qid="QID2"):
    """[(item_number, choice_id_str), ...] sorted by item number, read from
    the real QSF -- never assumed as range(1, 61). Raises SystemExit on any
    structural mismatch, so a future edit to QID2's Choices (e.g. a
    non-numeric or gappy ID scheme) fails loudly here rather than silently
    generating a wrong/incomplete Web Service body."""
    sq = find_element(qsf["SurveyElements"],
                       lambda e: e["Element"] == "SQ" and e["PrimaryAttribute"] == qid)
    if sq is None:
        raise SystemExit(f"input has no {qid} question")
    choices = sq["Payload"].get("Choices", {})
    if len(choices) != EXPECTED_BFI_ITEMS:
        raise SystemExit(f"{qid} has {len(choices)} Choices, expected {EXPECTED_BFI_ITEMS}")
    try:
        item_numbers = {int(cid): cid for cid in choices}
    except ValueError:
        raise SystemExit(f"{qid} has a non-numeric Choice ID among {sorted(choices)}")
    if set(item_numbers) != set(range(1, EXPECTED_BFI_ITEMS + 1)):
        raise SystemExit(
            f"{qid}'s Choice IDs aren't exactly 1..{EXPECTED_BFI_ITEMS}: "
            f"got {sorted(item_numbers)}")
    choice_order = sq["Payload"].get("ChoiceOrder", [])
    if sorted(int(c) for c in choice_order) != sorted(item_numbers):
        raise SystemExit(f"{qid}'s ChoiceOrder doesn't match its Choices keys")
    return sorted(item_numbers.items())


def validate_attention_scoring(qsf):
    """Raises SystemExit unless QID100's attention-check row (add_rps.py)
    scores exactly as add_rps.py itself builds it -- checked structurally
    here (not just importing ATTENTION_CATEGORY_ID and trusting it),
    since this script's Web Service body pipes that category's score
    directly as the gate's attention-check signal."""
    sco = find_element(qsf["SurveyElements"], lambda e: e["Element"] == "SCO")
    if sco is None:
        raise SystemExit("input has no SCO (scoring) element")
    category_ids = {c["ID"] for c in sco["Payload"]["ScoringCategories"]}
    if add_rps.ATTENTION_CATEGORY_ID not in category_ids:
        raise SystemExit(f"input's SCO has no {add_rps.ATTENTION_CATEGORY_ID} category")

    sq = find_element(qsf["SurveyElements"],
                       lambda e: (e["Element"] == "SQ"
                                  and e["PrimaryAttribute"] == add_rps.QID_MAIN))
    if sq is None:
        raise SystemExit(f"input has no {add_rps.QID_MAIN} question")
    cells = [g for g in sq["Payload"]["GradingData"]
             if add_rps.ATTENTION_CATEGORY_ID in g["Grades"]]
    if len(cells) != 9:
        raise SystemExit(f"expected 9 attention-check GradingData cells, found {len(cells)}")
    for cell in cells:
        expected = "1" if cell["AnswerID"] == add_rps.ATTENTION_CORRECT_ANSWER_ID else "0"
        actual = cell["Grades"][add_rps.ATTENTION_CATEGORY_ID]
        if actual != expected:
            raise SystemExit(
                f"attention-check cell AnswerID={cell['AnswerID']} scores "
                f"{actual!r}, expected {expected!r}")


def build_body_params(item_choice_pairs, spec_hash):
    """Ordered [(key, value), ...] backing both EditBodyParams and Body --
    a single source of truth so they can't drift apart. Every value is a
    plain piped-text string (no parameterType): an unanswered Matrix row
    resolves to "", cleanly distinguishing "unanswered" from any real
    answer, which the "Number"/"JSON" parameterTypes could coerce away."""
    params = [
        ("contract_version", CONTRACT_VERSION),
        ("part", "personality"),
        ("drip_spec_sha256", spec_hash),
        ("prolific_pid", "${e://Field/prolific_pid}"),
        ("prolific_study_id", "${e://Field/prolific_study_id}"),
        ("prolific_session_id", "${e://Field/prolific_session_id}"),
        ("attention_check_score", f"${{gr://{add_rps.ATTENTION_CATEGORY_ID}/Score}}"),
        ("recaptcha_score", "${e://Field/Q_RecaptchaScore}"),
    ]
    for item_number, choice_id in item_choice_pairs:
        params.append((f"bfi_{item_number:02d}",
                       f"${{q://QID2/SelectedAnswerRecode/{choice_id}}}"))
    return params


def build_response_map():
    """Not required for the gate's own calculation (that's server-side),
    but maps the server's computed signals back into the response export
    for audit/debugging -- distinguishing a Web Service failure from a
    real quality failure, and letting a completed-response export be
    compared against verify_responses.py's offline scoring.

    Orientation: {"key": <JSON path to extract from the response>,
    "value": <destination embedded-data field name>} -- confirmed live
    this session (the opposite orientation silently produces blank
    fields: a real completed-response export showed every mapped field
    empty when "key" held the destination name and "value" held the
    response path, with the export column named after "value" but its
    content blank because "key" wasn't a real path in the response)."""
    return [
        {"key": "verdict", "value": "verdict"},
        {"key": "next_url", "value": "next_url"},
        {"key": "completion_code", "value": "completion_code"},
        {"key": "quality_signals.drip_score", "value": "drip_score"},
        {"key": "quality_signals.bfi_answered", "value": "bfi_answered"},
        {"key": "quality_signals.attention_checks_passed", "value": "attention_checks_passed"},
        # Deliberately distinct destination name from Qualtrics's own
        # Q_RecaptchaScore, so a completed-response export can't confuse
        # "what Qualtrics measured" with "what the server echoed back".
        {"key": "quality_signals.recaptcha_score", "value": "gate_recaptcha_score"},
    ]


def build_transport_validation_response_map(body_params):
    """--transport-validation only: maps every outbound field back from
    httpbin's JSON echo (destination names prefixed to avoid colliding
    with the real fail-closed defaults), so a single completed-response
    export shows whether all 68 substitutions resolved correctly -- no
    manual UI response-mapping needed field-by-field. See
    build_response_map() for the key/value orientation."""
    return [{"key": f"json.{k}", "value": f"check_{k}"} for k, v in body_params]


def build_web_service(body_params, response_map, url=PLACEHOLDER_URL):
    return {
        "Type": "WebService",
        "FlowID": WEB_SERVICE_FLOW_ID,
        "URL": url,
        "Method": "POST",
        "RequestParams": [],
        "EditBodyParams": [{"key": k, "value": v} for k, v in body_params],
        "Body": dict(body_params),
        "ContentType": "application/json",
        # No custom auth-secret header: its exported .qsf shape is
        # unconfirmed (see module docstring) -- add manually post-import.
        "Headers": [],
        "ResponseMap": response_map,
        "FireAndForget": False,
        "SchemaVersion": 0,
        "StringifyValues": True,
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
            # Self-reference activates Qualtrics's built-in bot-detection
            # score for use in Survey-Flow piped text later in the same
            # response -- confirmed available a few seconds after page
            # load (Survey Options > Security > Bot Detection must be on).
            # Its own fail-open default if reCAPTCHA never loads is "1" --
            # NOT proof of a verified human, just Qualtrics's own
            # can't-tell value; the server contract should treat a blank/
            # malformed score as review, but shouldn't treat "1" as
            # provably-human either.
            field("Q_RecaptchaScore", "${e://Field/Q_RecaptchaScore}"),
            # Fail-closed: if the Web Service never runs, times out, or
            # returns non-200, these are what's left in the response --
            # a tracked review, a safe fallback redirect, and blank
            # signal values (never a stale/wrong number).
            field("verdict", "review"),
            field("next_url", PLACEHOLDER_REVIEW_FALLBACK_URL),
            field("completion_code", ""),
            field("drip_score", ""),
            field("bfi_answered", ""),
            field("attention_checks_passed", "false"),
            field("gate_recaptcha_score", ""),
        ],
    }


def assert_invariants(qsf, body_params, response_map):
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
    qc = find_element(qsf["SurveyElements"], lambda e: e["Element"] == "QC")
    if int(qc["SecondaryAttribute"]) != len(qid_list):
        raise AssertionError(
            f"QC {qc['SecondaryAttribute']} != actual question count {len(qid_list)} "
            "-- this script must never add/remove a question")

    ws_nodes = [n for n in flow if n.get("Type") == "WebService"]
    if len(ws_nodes) != 1:
        raise AssertionError(f"expected exactly 1 WebService flow node, found {len(ws_nodes)}")
    ws = ws_nodes[0]
    expected_keys = [k for k, v in body_params]
    edit_keys = [p["key"] for p in ws["EditBodyParams"]]
    if edit_keys != expected_keys:
        raise AssertionError(f"EditBodyParams keys {edit_keys} != expected {expected_keys}")
    for p in ws["EditBodyParams"]:
        if "parameterType" in p:
            raise AssertionError(f"EditBodyParams[{p['key']!r}] has a parameterType "
                                  "-- every field must be a plain String")
    if ws["Body"] != dict(body_params):
        raise AssertionError("Body doesn't match EditBodyParams' key/value pairs exactly")
    if ws["Headers"] != []:
        raise AssertionError(f"Headers should be [], got {ws['Headers']}")
    if ws["FireAndForget"] is not False:
        raise AssertionError("FireAndForget must be False -- the gate needs a synchronous reply")
    if ws["StringifyValues"] is not True:
        raise AssertionError("StringifyValues must be True")
    if ws["ContentType"] != "application/json":
        raise AssertionError(f"ContentType should be application/json, got {ws['ContentType']}")
    if ws["ResponseMap"] != response_map:
        raise AssertionError("ResponseMap doesn't match the expected generated mapping")
    # "value" is the destination field name (see build_response_map()'s
    # docstring for the confirmed key/value orientation) -- two entries
    # writing to the same destination is the real bug to catch, not two
    # entries reading the same source path (which is fine).
    response_map_destinations = [p["value"] for p in ws["ResponseMap"]]
    if len(response_map_destinations) != len(set(response_map_destinations)):
        raise AssertionError(f"duplicate ResponseMap destination in {response_map_destinations}")

    ed_nodes = [n for n in flow if n.get("Type") == "EmbeddedData"
                and n.get("FlowID") == EMBEDDED_DATA_FLOW_ID]
    if len(ed_nodes) != 1:
        raise AssertionError(f"expected exactly 1 defaults EmbeddedData node, found {len(ed_nodes)}")

    content_index = next((i for i, n in enumerate(flow) if n.get("Type") == "Block"), None)
    ed_index = next((i for i, n in enumerate(flow) if n is ed_nodes[0]), None)
    ws_index = next((i for i, n in enumerate(flow) if n is ws), None)
    if not (ed_index < content_index < ws_index):
        raise AssertionError(
            f"expected flow order EmbeddedData({ed_index}) < content Block({content_index}) "
            f"< WebService({ws_index})")


def apply_wiring(qsf, url=PLACEHOLDER_URL, diagnostic_response_map=False):
    """Returns a new, fully-validated qsf dict; the input is left
    untouched. Preflight validates the source and builds every new
    structure from it (any of this can raise SystemExit); only then is a
    deep copy mutated and re-validated (assert_invariants, AssertionError)
    before being adopted -- so a failure at ANY point, including a
    post-generation invariant failure, never returns a partially-mutated
    result.

    diagnostic_response_map=True (--transport-validation only) swaps the
    production ResponseMap for one that echoes every outbound field back
    from httpbin's JSON response -- see build_transport_validation_
    response_map()."""
    item_choice_pairs = find_bfi_question(qsf)
    validate_attention_scoring(qsf)

    fl = find_element(qsf["SurveyElements"], lambda e: e["Element"] == "FL")
    flow = fl["Payload"]["Flow"]
    if any(n.get("Type") == "WebService" for n in flow):
        raise SystemExit("input already contains a WebService flow element -- "
                          "expected an unwired BFI-2_Full_RPS.qsf as input")
    existing_flow_ids = {n.get("FlowID") for n in flow} | {fl["Payload"].get("FlowID")}
    for new_id in (EMBEDDED_DATA_FLOW_ID, WEB_SERVICE_FLOW_ID):
        if new_id in existing_flow_ids:
            raise SystemExit(f"input already contains FlowID {new_id}")

    content_flow_index = next(
        (i for i, node in enumerate(flow) if node.get("Type") == "Block"), None)
    if content_flow_index is None:
        raise SystemExit("input's Survey Flow has no content Block node")
    bl = find_element(qsf["SurveyElements"], lambda e: e["Element"] == "BL")
    if flow[content_flow_index]["ID"] != bl["Payload"][0]["ID"]:
        raise SystemExit(
            "the flow's content Block node doesn't match BL.Payload[0] -- "
            "input has an unexpected shape this script wasn't written for")

    pairs = drip_spec.load_pairs()
    drip_spec.cross_validate_pairs(pairs, drip_spec.item_meta_from_qsf(qsf))
    spec_hash = drip_spec.canonical_spec_sha256(pairs)

    body_params = build_body_params(item_choice_pairs, spec_hash)
    response_map = (build_transport_validation_response_map(body_params)
                    if diagnostic_response_map else build_response_map())
    web_service = build_web_service(body_params, response_map, url=url)
    defaults_ed = build_defaults_embedded_data()

    candidate = copy.deepcopy(qsf)
    c_fl = find_element(candidate["SurveyElements"], lambda e: e["Element"] == "FL")
    c_flow = c_fl["Payload"]["Flow"]
    c_flow.insert(0, defaults_ed)
    # +2, not +1: the EmbeddedData insert above shifted content_flow_index
    # forward by one.
    c_flow.insert(content_flow_index + 2, web_service)
    c_fl["Payload"]["Properties"]["Count"] = 1 + len(c_flow)
    candidate["SurveyEntry"]["SurveyName"] = (
        candidate["SurveyEntry"]["SurveyName"] + NOT_DEPLOYABLE_SUFFIX)

    assert_invariants(candidate, body_params, response_map)
    return candidate


def main():
    if not INPUT_FILE.exists():
        print(f"Missing {INPUT_FILE} -- run add_rps.py first.", file=sys.stderr)
        sys.exit(1)

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        qsf = json.load(f)

    transport_validation = "--transport-validation" in sys.argv
    # Synthetic-only, gitignored scratch target -- never the tracked
    # OUTPUT_FILE, never a real endpoint. Confirms all 68 outbound
    # substitutions resolve correctly against a real completed response
    # before trusting the design at full (68-parameter) scale.
    url = "https://httpbin.org/post" if transport_validation else PLACEHOLDER_URL
    wired = apply_wiring(qsf, url=url, diagnostic_response_map=transport_validation)

    out = (REPO_ROOT / "qualtrics_test_exports" / "BFI-2_Full_RPS_Wired_transport_validation.qsf"
           if transport_validation else OUTPUT_FILE)
    out.parent.mkdir(exist_ok=True)
    # Write to a sibling temp file, then rename into place -- an
    # interruption mid-write can't leave a truncated output file.
    tmp = out.with_suffix(out.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(wired, f, separators=(",", ":"))
    tmp.replace(out)
    print(f"Wrote {out.relative_to(REPO_ROOT)}: EmbeddedData defaults + a "
          f"68-field Web Service (60 BFI-2 items + attention/recaptcha/"
          f"contract fields). Branch/redirect/auth-header NOT generated -- "
          f"see the module docstring."
          + (" [transport-validation build, targets httpbin.org]"
             if transport_validation else ""))


if __name__ == "__main__":
    main()
