"""Synthetic fixtures for the private Part 1 draft transformer."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import prepare_ase_part1_draft as p


def baseline():
    sid = "SV_fixtureOnly"
    def element(kind, key, value):
        return {"SurveyID": sid, "Element": kind, "PrimaryAttribute": key, "Payload": value}

    items = ([{"Type": "Page Break"} if i is None else
              {"Type": "Question", "QuestionID": f"QID{i}"} for i in p.BASE_ORDER])
    blocks = {"0": {"ID": "BL_fixture", "Type": "Default", "BlockElements": items},
              "1": {"ID": "BL_trash", "Type": "Trash", "BlockElements": []}}
    flow = {"Type": "Root", "Flow": [{"Type": "EmbeddedData", "EmbeddedData": [{"Field": "test_only"}]},
                                      {"Type": "Block", "ID": "BL_fixture"}]}
    elements = [element("BL", "Survey Blocks", blocks), element("FL", "Survey Flow", flow),
                element("SO", "Survey Options", {"TestSetting": True, "RecaptchaV3": True,
                                                 "IncludeSecurityFields": True}),
                element("QC", "Survey Question Count", None)]
    for i, tag in enumerate(p.TAGS, 1):
        qid = f"QID{i}"
        if i == 7:
            qtype, selector = "DB", "TB"
        elif i in (4, 5, 6):
            qtype, selector = "TE", "SL"
        else:
            qtype, selector = "MC", "MAVR" if i in (10, 13) else "SAVR"
        question = {"QuestionID": qid, "DataExportTag": tag, "QuestionText": f"Synthetic {qid}",
                    "QuestionDescription": "Synthetic", "QuestionType": qtype, "Selector": selector,
                    "SubSelector": "TX", "Validation": {"Settings": {"ForceResponse": "OFF"}}}
        if qtype == "MC":
            values = (["Test tool", "Other", "None", "Prefer not to say"] if i in (10, 13) else
                      ["18–24", "25–34", "35–44", "45+", "Prefer not to say"] if i == 1 else
                      ["Below secondary", "Secondary", "Tertiary", "Postgraduate", "Other", "Prefer not to say"]
                      if i == 3 else ["Test choice", "Prefer not to say"])
            question.update(Choices={str(j): ({"Display": label, "ExclusiveAnswer": True}
                            if i in (10, 13) and label in ("None", "Prefer not to say")
                            else {"Display": label}) for j, label in enumerate(values, 1)},
                            ChoiceOrder=list(range(1, len(values) + 1)), NextChoiceId=14)
        elements.append(element("SQ", qid, question))
    return {"SurveyEntry": {"SurveyID": sid, "SurveyStatus": "Inactive"}, "SurveyElements": elements}


def payload(qsf, key):
    return next(e["Payload"] for e in qsf["SurveyElements"] if e["PrimaryAttribute"] == key)


class PreparePart1Test(unittest.TestCase):
    def test_order_content_and_untouched_native_structure(self):
        original = baseline()
        frozen = deepcopy(original)
        actual = p.prepare_draft(original)
        self.assertEqual(original, frozen)
        self.assertEqual(actual["SurveyEntry"], frozen["SurveyEntry"])
        self.assertEqual(payload(actual, "Survey Flow"), payload(frozen, "Survey Flow"))
        self.assertEqual(payload(actual, "Survey Options"), {"TestSetting": True})
        items = payload(actual, "Survey Blocks")["0"]["BlockElements"]
        self.assertEqual([item.get("QuestionID", item["Type"]) for item in items],
                         ["QID7", "Page Break", "QID1", "QID2", "QID3", "QID8",
                          "QID4", "QID5", "QID6", "QID10", "QID13", "QID9", "QID12", "QID11"])
        self.assertEqual(payload(actual, "Survey Blocks")["1"], payload(frozen, "Survey Blocks")["1"])
        self.assertEqual(len(actual["SurveyElements"]), len(frozen["SurveyElements"]))
        for before, after in zip(frozen["SurveyElements"], actual["SurveyElements"]):
            if before["PrimaryAttribute"] not in {"Survey Blocks", "Survey Options", "QID1", "QID3"}:
                self.assertEqual(before, after)
        self.assertEqual(payload(actual, "QID1")["ChoiceOrder"], [1, 2, 3, 14, 15, 16, 5])
        self.assertNotIn("4", payload(actual, "QID1")["Choices"])
        self.assertNotIn("3", payload(actual, "QID3")["Choices"])
        self.assertEqual(payload(actual, "QID3")["ChoiceOrder"], [14, 15, 16, 17, 18, 19, 6])
        for qid, expected in [("QID1", p.AGE), ("QID3", p.EDUCATION)]:
            result = payload(actual, qid)
            self.assertEqual(tuple(result["Choices"][str(key)]["Display"] for key in result["ChoiceOrder"]),
                             expected)
            self.assertEqual(result["DataExportTag"], payload(frozen, qid)["DataExportTag"])
        self.assertEqual(payload(actual, "QID3")["QuestionText"], p.EDUCATION_TEXT)

    def test_old_age_recode_supported_and_existing_nonidentity_refused(self):
        original = baseline()
        payload(original, "QID1")["RecodeValues"] = {"1": "1", "4": "4", "5": 5}
        self.assertEqual(payload(p.prepare_draft(original), "QID1")["RecodeValues"], {"1": "1", "5": 5})
        payload(original, "QID1")["RecodeValues"]["4"] = 99
        with self.assertRaises(p.BadBaseline):
            p.prepare_draft(original)

    def test_native_omits_only_age_and_education_counters(self):
        original = baseline()
        for key in ("QID1", "QID3"):
            payload(original, key).pop("NextChoiceId")
        result = p.prepare_draft(original)
        self.assertEqual(payload(result, "QID1")["ChoiceOrder"], [1, 2, 3, 6, 7, 8, 5])
        self.assertEqual(payload(result, "QID1")["NextChoiceId"], 9)
        self.assertEqual(payload(result, "QID3")["ChoiceOrder"], [7, 8, 9, 10, 11, 12, 6])
        self.assertEqual(payload(result, "QID3")["NextChoiceId"], 13)
        self.assertEqual(payload(result, "QID2"), payload(original, "QID2"))
        self.assertNotIn("NextChoiceId", payload(original, "QID1"))
        self.assertNotIn("4", payload(result, "QID1")["Choices"])
        self.assertTrue({"1", "2", "3", "4", "5"}.isdisjoint(payload(result, "QID3")["Choices"]))

    def test_reviewed_floor_only_when_missing_and_strict_native_counter(self):
        original = baseline()
        with self.assertRaisesRegex(p.BadBaseline, "cannot override"):
            p.prepare_draft(original, age_next_choice_id=20)
        for invalid in (None, True, "20", 5, 99999991):
            with self.subTest(invalid=invalid):
                payload(original, "QID1")["NextChoiceId"] = invalid
                with self.assertRaisesRegex(p.BadBaseline, "invalid.*NextChoiceId"):
                    p.prepare_draft(original)
        payload(original, "QID1").pop("NextChoiceId")
        with self.assertRaisesRegex(p.BadBaseline, "invalid.*NextChoiceId"):
            p.prepare_draft(original, age_next_choice_id=5)
        draft = p.prepare_draft(original, age_next_choice_id=20)
        self.assertEqual(payload(draft, "QID1")["ChoiceOrder"], [1, 2, 3, 20, 21, 22, 5])
        self.assertEqual(payload(draft, "QID1")["NextChoiceId"], 23)
        self.assertEqual(payload(draft, "QID3")["ChoiceOrder"], [14, 15, 16, 17, 18, 19, 6])

    def test_fail_closed_protects_legacy_or_different_versions(self):
        def replace_order(qsf):
            payload(qsf, "Survey Blocks")["0"]["BlockElements"][9:11] = reversed(
                payload(qsf, "Survey Blocks")["0"]["BlockElements"][9:11])
        changes = [
            lambda qsf: qsf["SurveyEntry"].update(SurveyStatus="Active"),
            lambda qsf: payload(qsf, "QID13")["Choices"]["3"].pop("ExclusiveAnswer"),
            lambda qsf: payload(qsf, "QID8")["Validation"]["Settings"].update(ForceResponse="ON"),
            lambda qsf: payload(qsf, "QID3").update(DataExportTag="mismatched"),
            lambda qsf: payload(qsf, "QID1").update(NextChoiceId=4),
            lambda qsf: payload(qsf, "QID1")["Choices"]["4"].update(Display="Drift"),
            lambda qsf: payload(qsf, "QID3")["Choices"]["3"].update(Display="Drift"),
            lambda qsf: payload(qsf, "Survey Flow")["Flow"].append({"Type": "Branch", "Logic": "q://QID1/4"}),
            lambda qsf: payload(qsf, "QID2").update(DisplayLogic={"QuestionID": "QID3"}),
            replace_order,
        ]
        for change in changes:
            with self.subTest(change=change):
                private = baseline()
                change(private)
                with self.assertRaises(p.BadBaseline):
                    p.prepare_draft(private)

    def test_cli_refusal_checks_and_private_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, output = root / "baseline.qsf", root / "private.qsf"
            if not p._outside_git(source):
                self.skipTest("temporary filesystem has a protected Git ancestor")
            source.write_text(json.dumps(baseline()), encoding="utf-8")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            script = Path(p.__file__).resolve()
            cli = [sys.executable, str(script), "--baseline", str(source), "--baseline-sha256"]
            def run(*args):
                return subprocess.run(cli + list(args), capture_output=True, text=True, check=False)
            check = run(digest, "--check")
            self.assertEqual(check.returncode, 0, check.stderr)
            self.assertIn("no file written", check.stdout)
            self.assertFalse(output.exists())
            wrong = run("a" * 64, "--output", str(output))
            self.assertEqual(wrong.returncode, 2)
            self.assertNotIn(str(source), wrong.stderr)
            self.assertFalse(output.exists())
            made = run(digest, "--output", str(output))
            self.assertEqual(made.returncode, 0, made.stderr)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertEqual(json.loads(output.read_text()), p.prepare_draft(baseline()))
            self.assertEqual(run(digest, "--output", str(output)).returncode, 2)
            nonprivate = root / "checkout"
            nonprivate.mkdir()
            (nonprivate / ".git").touch()
            self.assertEqual(run(digest, "--output", str(nonprivate / "survey.qsf")).returncode, 2)
            self.assertFalse((nonprivate / "survey.qsf").exists())

    def test_private_path_gate_rejects_nested_git_marker(self):
        # Simulate both views so sentinel ancestors on the test host cannot
        # make the negative assertion pass without testing the nested marker.
        root = Path("/fixture-root")
        target = root / "checkout" / "nested" / "draft.qsf"
        marker = root / "checkout" / ".git"
        with patch.object(Path, "exists", autospec=True, return_value=False):
            self.assertTrue(p._outside_git(target))
        with patch.object(Path, "exists", autospec=True, side_effect=lambda path: path == marker):
            self.assertFalse(p._outside_git(target))


if __name__ == "__main__":
    unittest.main()
