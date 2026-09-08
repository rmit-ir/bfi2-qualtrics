#!/usr/bin/env python3
"""Regression coverage for wire_qualtrics_flow.py's generated Web Service
gate. Structural-only: checks internal consistency of the generated .qsf
(no duplicate IDs, correct counts, exact field names matching
ase2-ai-mode's contract) -- it cannot verify Qualtrics will actually
accept this file on import, which is why the module docstring and
docs/qualtrics-part2-wiring.md both call the generated Branch/redirect/
auth-header pieces still-manual, and why real live-completion validation
(--transport-validation) is a separate, required step.

Stdlib only (no pytest needed):
    python3 -m unittest discover tests
"""
import copy
import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = REPO_ROOT / ".claude" / "skills" / "bfi2-qsf-splitter"
sys.path.insert(0, str(SKILL_DIR))

import wire_qualtrics_flow as wf  # noqa: E402
import add_rps  # noqa: E402


class TestApplyWiring(unittest.TestCase):
    """output/BFI-2_Full_RPS.qsf is a committed deliverable (README/CLAUDE.md
    list it) -- its absence is a real failure, not a reason to skip these
    tests silently, same convention test_add_rps.py already established."""

    @classmethod
    def setUpClass(cls):
        if not wf.INPUT_FILE.exists():
            raise AssertionError(
                f"{wf.INPUT_FILE} missing -- run add_rps.py first "
                "(it's a committed deliverable, not optional).")
        with open(wf.INPUT_FILE, "r", encoding="utf-8") as f:
            cls.input_qsf = json.load(f)

    def _wired(self):
        return wf.apply_wiring(copy.deepcopy(self.input_qsf))

    def _web_service(self, qsf):
        fl = wf.find_element(qsf["SurveyElements"], lambda e: e["Element"] == "FL")
        return next(n for n in fl["Payload"]["Flow"] if n["Type"] == "WebService")

    def test_input_left_untouched(self):
        before = copy.deepcopy(self.input_qsf)
        wf.apply_wiring(copy.deepcopy(self.input_qsf))
        self.assertEqual(self.input_qsf, before)

    def test_no_duplicate_flow_ids(self):
        qsf = self._wired()
        fl = wf.find_element(qsf["SurveyElements"], lambda e: e["Element"] == "FL")
        ids = [fl["Payload"]["FlowID"]] + [n["FlowID"] for n in fl["Payload"]["Flow"]]
        self.assertEqual(len(ids), len(set(ids)), ids)

    def test_properties_count_matches_flow_length(self):
        qsf = self._wired()
        fl = wf.find_element(qsf["SurveyElements"], lambda e: e["Element"] == "FL")
        self.assertEqual(fl["Payload"]["Properties"]["Count"],
                          1 + len(fl["Payload"]["Flow"]))

    def test_new_flow_ids_dont_collide_with_existing(self):
        existing_fl = wf.find_element(
            self.input_qsf["SurveyElements"], lambda e: e["Element"] == "FL")
        existing_ids = {n.get("FlowID") for n in existing_fl["Payload"]["Flow"]}
        self.assertNotIn(wf.EMBEDDED_DATA_FLOW_ID, existing_ids)
        self.assertNotIn(wf.WEB_SERVICE_FLOW_ID, existing_ids)

    def test_rejects_double_apply(self):
        qsf = self._wired()
        with self.assertRaises(SystemExit):
            wf.apply_wiring(qsf)

    def test_no_question_or_block_added(self):
        before_sq = {e["PrimaryAttribute"] for e in self.input_qsf["SurveyElements"]
                    if e["Element"] == "SQ"}
        before_bl = wf.find_element(
            self.input_qsf["SurveyElements"], lambda e: e["Element"] == "BL")["Payload"]
        qsf = self._wired()
        after_sq = {e["PrimaryAttribute"] for e in qsf["SurveyElements"] if e["Element"] == "SQ"}
        after_bl = wf.find_element(qsf["SurveyElements"], lambda e: e["Element"] == "BL")["Payload"]
        self.assertEqual(after_sq, before_sq)
        self.assertEqual(len(after_bl), len(before_bl))

    def test_qc_unchanged(self):
        before_qc = wf.find_element(
            self.input_qsf["SurveyElements"], lambda e: e["Element"] == "QC")["SecondaryAttribute"]
        qsf = self._wired()
        after_qc = wf.find_element(qsf["SurveyElements"], lambda e: e["Element"] == "QC")["SecondaryAttribute"]
        self.assertEqual(after_qc, before_qc)

    def test_inserts_relative_to_content_block_not_blind_append(self):
        # Distinguishes the content-block-relative insert from a naive
        # flow.append(): inject a synthetic trailing flow node into a
        # deep copy of the input before wiring, and confirm the Web
        # Service node lands BEFORE it, not after.
        qsf = copy.deepcopy(self.input_qsf)
        fl = wf.find_element(qsf["SurveyElements"], lambda e: e["Element"] == "FL")
        trailing = {"Type": "EmbeddedData", "FlowID": "FL_99", "EmbeddedData": []}
        fl["Payload"]["Flow"].append(trailing)
        fl["Payload"]["Properties"]["Count"] += 1
        wired = wf.apply_wiring(qsf)
        wired_flow = wf.find_element(
            wired["SurveyElements"], lambda e: e["Element"] == "FL")["Payload"]["Flow"]
        ws_index = next(i for i, n in enumerate(wired_flow) if n["Type"] == "WebService")
        trailing_index = next(i for i, n in enumerate(wired_flow) if n.get("FlowID") == "FL_99")
        self.assertLess(ws_index, trailing_index)

    def test_flow_order_is_defaults_then_content_then_web_service(self):
        qsf = self._wired()
        fl = wf.find_element(qsf["SurveyElements"], lambda e: e["Element"] == "FL")
        flow = fl["Payload"]["Flow"]
        self.assertEqual(len(flow), 3)
        self.assertEqual(flow[0]["Type"], "EmbeddedData")
        self.assertEqual(flow[0]["FlowID"], wf.EMBEDDED_DATA_FLOW_ID)
        content_block_id = wf.find_element(
            self.input_qsf["SurveyElements"], lambda e: e["Element"] == "BL"
        )["Payload"][0]["ID"]
        self.assertEqual(flow[1], {"ID": content_block_id, "Type": "Block", "FlowID": "FL_2"})
        self.assertEqual(flow[2]["Type"], "WebService")
        self.assertEqual(flow[2]["FlowID"], wf.WEB_SERVICE_FLOW_ID)

    def test_defaults_embedded_data_field_names(self):
        # Exact field names ase2-ai-mode's endpoint / Web Service
        # ResponseMap destinations expect. A drift here would silently
        # produce a survey whose fail-closed fallback is wrong.
        expected = {
            "prolific_pid": "${e://Field/PROLIFIC_PID}",
            "prolific_study_id": "${e://Field/STUDY_ID}",
            "prolific_session_id": "${e://Field/SESSION_ID}",
            "Q_RecaptchaScore": "${e://Field/Q_RecaptchaScore}",
            "verdict": "review",
            "next_url": wf.PLACEHOLDER_REVIEW_FALLBACK_URL,
            "completion_code": "",
            "drip_score": "",
            "bfi_answered": "",
            "attention_checks_passed": "false",
            "gate_recaptcha_score": "",
        }
        ed = wf.build_defaults_embedded_data()
        actual = {f["Field"]: f["Value"] for f in ed["EmbeddedData"]}
        self.assertEqual(actual, expected)

    def test_body_params_count_and_bfi_keys(self):
        item_choice_pairs = wf.find_bfi_question(self.input_qsf)
        params = wf.build_body_params(item_choice_pairs, "deadbeef")
        keys = [k for k, v in params]
        self.assertEqual(len(keys), 68)
        expected_bfi_keys = [f"bfi_{i:02d}" for i in range(1, 61)]
        self.assertEqual(keys[-60:], expected_bfi_keys)
        self.assertEqual(keys[:8], [
            "contract_version", "part", "drip_spec_sha256",
            "prolific_pid", "prolific_study_id", "prolific_session_id",
            "attention_check_score", "recaptcha_score",
        ])

    def test_bfi_values_use_real_choice_ids_not_positional_assumption(self):
        # A choice_id that differs from its item number (e.g. "x1" instead
        # of "1") must still produce the correct bfi_NN -> that choice_id
        # mapping, not a range(1, 61) guess.
        item_choice_pairs = [(1, "x1"), (2, "2")]
        params = wf.build_body_params(item_choice_pairs, "deadbeef")
        as_dict = dict(params)
        self.assertEqual(as_dict["bfi_01"], "${q://QID2/SelectedAnswerRecode/x1}")
        self.assertEqual(as_dict["bfi_02"], "${q://QID2/SelectedAnswerRecode/2}")

    def test_bfi_values_use_singular_selected_answer_recode(self):
        # Not SelectedChoicesRecode -- confirmed live this session that
        # only the singular "SelectedAnswerRecode" resolves correctly for
        # an individual Matrix row.
        item_choice_pairs = wf.find_bfi_question(self.input_qsf)
        params = wf.build_body_params(item_choice_pairs, "deadbeef")
        for key, value in params:
            if key.startswith("bfi_"):
                self.assertIn("SelectedAnswerRecode", value)
                self.assertNotIn("SelectedChoicesRecode", value)

    def test_attention_check_uses_native_scoring_category(self):
        item_choice_pairs = wf.find_bfi_question(self.input_qsf)
        params = wf.build_body_params(item_choice_pairs, "deadbeef")
        as_dict = dict(params)
        self.assertEqual(as_dict["attention_check_score"],
                         f"${{gr://{add_rps.ATTENTION_CATEGORY_ID}/Score}}")
        # Not double-prefixed with SC_ -- ATTENTION_CATEGORY_ID already
        # includes it (scoring_category_id() returns "SC_" + 15 chars).
        self.assertNotIn(f"SC_{add_rps.ATTENTION_CATEGORY_ID}", as_dict["attention_check_score"])

    def test_web_service_no_parameter_type_anywhere(self):
        # Every field is a plain String -- "" must mean "unanswered", not
        # get coerced away by a Number/JSON parameterType.
        qsf = self._wired()
        ws = self._web_service(qsf)
        for p in ws["EditBodyParams"]:
            self.assertNotIn("parameterType", p, p)

    def test_web_service_edit_body_params_and_body_are_identical_data(self):
        qsf = self._wired()
        ws = self._web_service(qsf)
        from_edit = {p["key"]: p["value"] for p in ws["EditBodyParams"]}
        self.assertEqual(from_edit, ws["Body"])

    def test_web_service_no_nested_extra_signals(self):
        qsf = self._wired()
        ws = self._web_service(qsf)
        self.assertNotIn("extra_signals", ws["Body"])
        for v in ws["Body"].values():
            self.assertIsInstance(v, str)

    def test_web_service_static_fields(self):
        qsf = self._wired()
        ws = self._web_service(qsf)
        self.assertEqual(ws["Method"], "POST")
        self.assertEqual(ws["ContentType"], "application/json")
        self.assertEqual(ws["Headers"], [])
        self.assertIs(ws["FireAndForget"], False)
        self.assertIs(ws["StringifyValues"], True)
        self.assertEqual(ws["RequestParams"], [])

    def test_response_map_exact(self):
        qsf = self._wired()
        ws = self._web_service(qsf)
        self.assertEqual(ws["ResponseMap"], wf.build_response_map())

    def test_response_map_orientation_key_is_source_path_value_is_destination(self):
        # Confirmed live: the opposite orientation silently produces
        # blank fields -- the export column is named after "value", but
        # its content stays empty unless "key" is a real JSON path.
        rm = wf.build_response_map()
        by_value = {p["value"]: p["key"] for p in rm}
        self.assertEqual(by_value["drip_score"], "quality_signals.drip_score")
        self.assertEqual(by_value["verdict"], "verdict")

    def test_survey_options_left_completely_untouched(self):
        # Enabling Bot Detection (RecaptchaV3/IncludeSecurityFields) via a
        # plain JSON patch on SO was tried and reverted -- it broke a
        # fresh Qualtrics import even though those exact values were
        # present in donor exports of surveys enabled through the UI.
        # This script must not touch SO at all until a real donor export
        # of a *freshly-imported-and-working* file confirms what's safe.
        before_so = wf.find_element(
            self.input_qsf["SurveyElements"], lambda e: e["Element"] == "SO")
        qsf = self._wired()
        after_so = wf.find_element(qsf["SurveyElements"], lambda e: e["Element"] == "SO")
        self.assertEqual(after_so, before_so)

    def test_drip_spec_hash_present_and_matches_current_tsv(self):
        import drip_spec
        qsf = self._wired()
        ws = self._web_service(qsf)
        self.assertEqual(ws["Body"]["drip_spec_sha256"],
                         drip_spec.canonical_spec_sha256(drip_spec.load_pairs()))

    def test_no_qid102_or_js_fields_anywhere_in_output(self):
        qsf = self._wired()
        blob = json.dumps(qsf)
        for stale in ("QID102", "__js_", "QuestionJS", "addOnReady", "addOnload",
                      "setJSEmbeddedData"):
            self.assertNotIn(stale, blob)

    def test_no_real_secrets_or_production_urls(self):
        qsf = self._wired()
        ws = self._web_service(qsf)
        self.assertEqual(ws["URL"], wf.PLACEHOLDER_URL)
        self.assertTrue(ws["URL"].startswith("https://example.invalid/"))

    def test_rejects_bad_choice_id_scheme_via_find_bfi_question(self):
        qsf = copy.deepcopy(self.input_qsf)
        sq = wf.find_element(qsf["SurveyElements"],
                             lambda e: e["Element"] == "SQ" and e["PrimaryAttribute"] == "QID2")
        del sq["Payload"]["Choices"]["60"]
        with self.assertRaises(SystemExit):
            wf.find_bfi_question(qsf)

    def test_rejects_tampered_attention_scoring_via_validate_attention_scoring(self):
        qsf = copy.deepcopy(self.input_qsf)
        sq = wf.find_element(qsf["SurveyElements"],
                             lambda e: e["Element"] == "SQ" and e["PrimaryAttribute"] == add_rps.QID_MAIN)
        # The correct-answer cell is the only one scoring "1" -- flip it to
        # "0" so the tamper actually changes behavior (every other cell
        # already scores "0", so tampering one of those would be a no-op).
        correct_cell = next(
            g for g in sq["Payload"]["GradingData"]
            if add_rps.ATTENTION_CATEGORY_ID in g["Grades"]
            and g["AnswerID"] == add_rps.ATTENTION_CORRECT_ANSWER_ID)
        correct_cell["Grades"][add_rps.ATTENTION_CATEGORY_ID] = "0"
        with self.assertRaises(SystemExit):
            wf.validate_attention_scoring(qsf)

    def test_rejects_late_invariant_failure_without_partial_mutation(self):
        # A tamper that only assert_invariants (post-mutation) would catch
        # -- not any preflight check -- must still leave the caller's
        # source object untouched, per apply_wiring()'s candidate-copy
        # mutation model.
        qsf = copy.deepcopy(self.input_qsf)
        bl = wf.find_element(qsf["SurveyElements"], lambda e: e["Element"] == "BL")
        # Duplicate an existing block ID -- not checked until
        # assert_invariants runs on the mutated candidate.
        bl["Payload"].append(copy.deepcopy(bl["Payload"][0]))
        before = copy.deepcopy(qsf)
        with self.assertRaises(AssertionError):
            wf.apply_wiring(qsf)
        self.assertEqual(qsf, before)

    def test_committed_output_matches_fresh_regeneration(self):
        if not wf.OUTPUT_FILE.exists():
            self.fail(f"{wf.OUTPUT_FILE} missing -- run wire_qualtrics_flow.py")
        with open(wf.OUTPUT_FILE, "r", encoding="utf-8") as f:
            committed = json.load(f)
        fresh = self._wired()
        self.assertEqual(fresh, committed)


if __name__ == "__main__":
    unittest.main()
