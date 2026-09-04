#!/usr/bin/env python3
"""Ensure every .qsf file in the repo is valid, structurally-sound JSON.

`.qsf` files are valid JSON that Qualtrics is strict about -- single-line is
this repo's own writing convention, not a Qualtrics requirement (it accepts
pretty-printed files too; see CLAUDE.md). This guards the basic contract:
each file parses as JSON, has the top-level shape
Qualtrics expects (`SurveyEntry` object + `SurveyElements` array), and passes
qsf_lint.py's structural checks (IDs, cross-references, scoring). It does NOT
check scoring correctness against the BFI-2's published key — that's the
splitter skill's own verification (see tests/test_split_bfi2.py).

Stdlib only (no pytest needed):
    python3 -m unittest discover tests
    python3 tests/test_qsf_parses.py
"""
import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
QSF_FILES = sorted(REPO_ROOT.rglob("*.qsf"))

sys.path.insert(0, str(REPO_ROOT / ".claude" / "skills" / "qsf-tools"))
import qsf_lint  # noqa: E402


class TestQsfFilesParse(unittest.TestCase):
    def test_at_least_one_qsf_exists(self):
        self.assertTrue(QSF_FILES, f"no .qsf files found under {REPO_ROOT}")

    def test_each_qsf_parses(self):
        for path in QSF_FILES:
            rel = path.relative_to(REPO_ROOT)
            with self.subTest(qsf=str(rel)):
                text = path.read_text(encoding="utf-8")
                try:
                    data = json.loads(text)
                except json.JSONDecodeError as e:
                    self.fail(f"{rel} is not valid JSON: {e}")

                self.assertIsInstance(data, dict, f"{rel}: top level is not a JSON object")
                self.assertIsInstance(
                    data.get("SurveyEntry"), dict,
                    f"{rel}: missing or non-object SurveyEntry")
                self.assertIsInstance(
                    data.get("SurveyElements"), list,
                    f"{rel}: missing or non-array SurveyElements")
                self.assertTrue(
                    data["SurveyElements"], f"{rel}: SurveyElements is empty")
                for i, el in enumerate(data["SurveyElements"]):
                    self.assertIn(
                        "Element", el, f"{rel}: SurveyElements[{i}] has no Element key")

    def test_each_qsf_passes_lint(self):
        for path in QSF_FILES:
            rel = path.relative_to(REPO_ROOT)
            with self.subTest(qsf=str(rel)):
                errors, warnings = qsf_lint.lint(path)
                self.assertEqual(errors, [], f"{rel}: qsf_lint errors: {errors}")
                self.assertEqual(warnings, [], f"{rel}: qsf_lint warnings: {warnings}")


if __name__ == "__main__":
    unittest.main()
