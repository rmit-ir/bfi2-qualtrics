#!/usr/bin/env python3
"""Ensure every .qsf file in the repo is valid, importable JSON.

`.qsf` files are single-line JSON that Qualtrics is strict about. This guards
the most basic contract: each file parses as JSON and has the top-level shape
Qualtrics expects (`SurveyEntry` object + `SurveyElements` array). It does NOT
check scoring correctness — that's the splitter skill's own verification.

Stdlib only (no pytest needed):
    python3 -m unittest discover tests
    python3 tests/test_qsf_parses.py
"""
import json
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
QSF_FILES = sorted(REPO_ROOT.rglob("*.qsf"))


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


if __name__ == "__main__":
    unittest.main()
