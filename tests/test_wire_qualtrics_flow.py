#!/usr/bin/env python3
"""Regression coverage for wire_qualtrics_flow.py (Phase 1 prototype).

Structural-only, same philosophy as test_add_rps.py's TestGenDripJs class:
this checks internal consistency of the generated .qsf (no duplicate IDs,
correct counts, exact field names matching ase2-ai-mode's contract) -- it
cannot verify Qualtrics will actually accept this file on import, which is
why the module docstring and docs/qualtrics-part2-wiring.md both call this
a PROTOTYPE pending a real import test.

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

    def _wired(self, js_snippet="/* test js */"):
        qsf = copy.deepcopy(self.input_qsf)
        wf.apply_wiring(qsf, js_snippet)
        return qsf

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

    def test_new_ids_dont_collide_with_existing(self):
        existing_qids = {e["PrimaryAttribute"] for e in self.input_qsf["SurveyElements"]
                         if e["Element"] == "SQ"}
        self.assertNotIn(wf.QID_JS, existing_qids)
        existing_block_ids = {b["ID"] for b in wf.find_element(
            self.input_qsf["SurveyElements"], lambda e: e["Element"] == "BL")["Payload"]}
        self.assertNotIn(wf.JS_BLOCK_ID, existing_block_ids)

    def test_rejects_double_apply(self):
        # apply_wiring() called twice on the same qsf must not silently
        # duplicate the question/block -- it's the idempotency guard
        # add_rps.py/gen_drip_js.py's own generators already establish.
        qsf = self._wired()
        with self.assertRaises(SystemExit):
            wf.apply_wiring(qsf, "/* second call */")

    def test_rejects_duplicate_flow_id_via_assert_invariants(self):
        qsf = self._wired()
        fl = wf.find_element(qsf["SurveyElements"], lambda e: e["Element"] == "FL")
        fl["Payload"]["Flow"][0]["FlowID"] = fl["Payload"]["Flow"][1]["FlowID"]
        with self.assertRaises(AssertionError):
            wf.assert_invariants(qsf)

    def test_rejects_missing_flow_id_via_assert_invariants(self):
        qsf = self._wired()
        fl = wf.find_element(qsf["SurveyElements"], lambda e: e["Element"] == "FL")
        del fl["Payload"]["Flow"][0]["FlowID"]
        with self.assertRaises(AssertionError):
            wf.assert_invariants(qsf)

    def test_rejects_duplicate_block_id_via_assert_invariants(self):
        qsf = self._wired()
        bl = wf.find_element(qsf["SurveyElements"], lambda e: e["Element"] == "BL")
        bl["Payload"][1]["ID"] = bl["Payload"][0]["ID"]
        with self.assertRaises(AssertionError):
            wf.assert_invariants(qsf)

    def test_rejects_block_referencing_missing_question_via_assert_invariants(self):
        qsf = self._wired()
        bl = wf.find_element(qsf["SurveyElements"], lambda e: e["Element"] == "BL")
        bl["Payload"][1]["BlockElements"][0]["QuestionID"] = "QID_DOES_NOT_EXIST"
        with self.assertRaises(AssertionError):
            wf.assert_invariants(qsf)

    def test_inserts_relative_to_content_block_not_blind_append(self):
        # Distinguishes the content-block-relative insert from a naive
        # flow.append(): inject a synthetic trailing flow node into a
        # deep copy of the input before wiring, and confirm the JS block
        # lands BEFORE it, not after.
        qsf = copy.deepcopy(self.input_qsf)
        fl = wf.find_element(qsf["SurveyElements"], lambda e: e["Element"] == "FL")
        trailing = {"Type": "EmbeddedData", "FlowID": "FL_99", "EmbeddedData": []}
        fl["Payload"]["Flow"].append(trailing)
        fl["Payload"]["Properties"]["Count"] += 1
        wf.apply_wiring(qsf, "/* test js */")
        flow = fl["Payload"]["Flow"]
        js_block_index = next(i for i, n in enumerate(flow) if n.get("ID") == wf.JS_BLOCK_ID)
        trailing_index = flow.index(trailing)
        self.assertLess(js_block_index, trailing_index)

    def test_embedded_data_field_names_match_ase2_ai_mode_contract(self):
        # Exact field names ase2-ai-mode's qualtrics_router.py /
        # QualtricsVerdictRequest expects, plus the __js_-prefixed fields
        # setJSEmbeddedData requires (see docs/qualtrics-part2-wiring.md S2
        # step 1). A drift here would silently produce a survey whose Web
        # Service call 422s or never gates correctly.
        expected = {
            "prolific_pid": "${e://Field/PROLIFIC_PID}",
            "prolific_study_id": "${e://Field/STUDY_ID}",
            "prolific_session_id": "${e://Field/SESSION_ID}",
            "attention_checks_passed": "false",
            "page_submit_seconds": "0",
            "__js_drip_score": "-1",
            "__js_bfi_answered": "-1",
        }
        ed = wf.build_defaults_embedded_data()
        actual = {f["Field"]: f["Value"] for f in ed["EmbeddedData"]}
        self.assertEqual(actual, expected)

    def test_defaults_resolve_to_correctly_typed_json_literals_unquoted(self):
        # docs/qualtrics-part2-wiring.md S5: attention_checks_passed,
        # page_submit_seconds, drip_score, bfi_answered are all UNQUOTED in
        # the Web Service JSON body template -- Qualtrics' piped-text
        # substitution inserts the embedded-data value's literal text
        # directly into the JSON body string. So these string-typed
        # embedded-data defaults are correct only if their literal text,
        # substituted unquoted, parses as the right JSON type/value --
        # not merely "the right string".
        ed = wf.build_defaults_embedded_data()
        values = {f["Field"]: f["Value"] for f in ed["EmbeddedData"]}
        self.assertIs(json.loads(values["attention_checks_passed"]), False)
        self.assertEqual(json.loads(values["page_submit_seconds"]), 0)
        self.assertEqual(json.loads(values["__js_drip_score"]), -1)
        self.assertEqual(json.loads(values["__js_bfi_answered"]), -1)

    def test_no_literal_urls_generated_in_phase_1(self):
        # Phase 1 generates no WebService element and no literal URLs at
        # all (only ${e://Field/...} piped-text placeholders) -- a bare
        # http(s):// literal in what THIS script generates would mean a
        # real or placeholder URL leaked in earlier than intended. Scoped
        # to the real rendered JS snippet + the new EmbeddedData node
        # only, not the whole (inherited) qsf, so this doesn't silently
        # start passing/failing based on unrelated content in the input
        # survey. Necessarily needs updating once Phase 2 adds a
        # WebService element with its own (placeholder) URL.
        js_snippet = gen_drip_js_render()
        self.assertNotIn("http://", js_snippet)
        self.assertNotIn("https://", js_snippet)
        ed_blob = json.dumps(wf.build_defaults_embedded_data())
        self.assertNotIn("http://", ed_blob)
        self.assertNotIn("https://", ed_blob)

    def test_js_question_uses_descriptive_text_type(self):
        payload = wf.build_js_question("/* js */")
        self.assertEqual(payload["QuestionType"], "DB")
        self.assertEqual(payload["Selector"], "TB")
        self.assertIn("QuestionJS", payload)

    def test_flow_order_is_defaults_then_content_then_js_block(self):
        qsf = self._wired()
        fl = wf.find_element(qsf["SurveyElements"], lambda e: e["Element"] == "FL")
        flow = fl["Payload"]["Flow"]
        self.assertEqual(len(flow), 3)
        self.assertEqual(flow[0]["Type"], "EmbeddedData")
        content_block_id = wf.find_element(
            self.input_qsf["SurveyElements"], lambda e: e["Element"] == "BL"
        )["Payload"][0]["ID"]
        self.assertEqual(flow[1], {"ID": content_block_id, "Type": "Block", "FlowID": "FL_2"})
        self.assertEqual(flow[2]["ID"], wf.JS_BLOCK_ID)
        self.assertEqual(flow[2]["Type"], "Block")

    def test_every_block_question_exists_and_qc_matches(self):
        qsf = self._wired()
        sq_ids = {e["PrimaryAttribute"] for e in qsf["SurveyElements"] if e["Element"] == "SQ"}
        bl = wf.find_element(qsf["SurveyElements"], lambda e: e["Element"] == "BL")
        for block in bl["Payload"]:
            for el in block["BlockElements"]:
                self.assertIn(el["QuestionID"], sq_ids)
        qc = wf.find_element(qsf["SurveyElements"], lambda e: e["Element"] == "QC")
        self.assertEqual(int(qc["SecondaryAttribute"]), len(sq_ids))

    def test_committed_output_matches_fresh_regeneration(self):
        # Same pattern as test_add_rps.py's test_committed_qid100_matches_a_
        # fresh_regeneration: the real gen_drip_js.py-rendered snippet
        # (not a placeholder), applied fresh, must be structurally
        # identical (parsed-JSON equality, not literal byte comparison --
        # key order isn't semantically meaningful) to what's actually
        # committed to output/BFI-2_Full_RPS_Wired.qsf.
        if not wf.OUTPUT_FILE.exists():
            self.fail(f"{wf.OUTPUT_FILE} missing -- run wire_qualtrics_flow.py")
        with open(wf.OUTPUT_FILE, "r", encoding="utf-8") as f:
            committed = json.load(f)
        js_snippet = gen_drip_js_render()
        fresh = self._wired(js_snippet)
        self.assertEqual(fresh, committed)


def gen_drip_js_render():
    import gen_drip_js  # noqa: E402 -- imported lazily, same sys.path setup
    return gen_drip_js.render(gen_drip_js.load_pairs())


if __name__ == "__main__":
    unittest.main()
