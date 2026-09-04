#!/usr/bin/env python3
"""Regression coverage for add_rps.py and gen_drip_js.py.

Checks the actual arithmetic (reverse-keying, attention-check scoring, the
DRIP JS's recode logic) rather than just "the script runs" -- add_rps.py's
own assert_invariants() already checks structure at generation time, but
that doesn't survive as a regression test once the generated .qsf is just
data on disk.

Stdlib only (no pytest needed):
    python3 -m unittest discover tests
"""
import csv
import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = REPO_ROOT / ".claude" / "skills" / "bfi2-qsf-splitter"
sys.path.insert(0, str(SKILL_DIR))

import add_rps  # noqa: E402
import gen_drip_js  # noqa: E402


class TestAddRpsScoring(unittest.TestCase):
    def setUp(self):
        self.main_payload = add_rps.build_main_question()
        self.item7_payload = add_rps.build_item7_question()

    def _cell(self, payload, choice_id, answer_id):
        for g in payload["GradingData"]:
            if g["ChoiceID"] == choice_id and g["AnswerID"] == answer_id:
                return g
        self.fail(f"no cell for choice {choice_id}, answer {answer_id}")

    def test_every_rps_item_reverse_status_matches_the_published_key(self):
        # Meertens & Lion (2008): items 1/2/3/5 reverse-keyed, 4/6 not --
        # hardcoded here independently of add_rps.RPS_ITEMS (not read from
        # it), so this is a real regression test against the published key,
        # not a tautology that would pass even if a reverse flag were wrong
        # in RPS_ITEMS itself.
        published_reverse = {1: True, 2: True, 3: True, 4: False, 5: True, 6: False}
        self.assertEqual(len(add_rps.RPS_ITEMS), len(published_reverse))
        for choice_id, expected_reverse in published_reverse.items():
            for answer_id in range(1, 10):
                cell = self._cell(self.main_payload, choice_id, answer_id)
                expected = str(10 - answer_id) if expected_reverse else str(answer_id)
                self.assertEqual(
                    cell["Grades"][add_rps.RPS_CATEGORY_ID], expected,
                    f"item {choice_id}, reverse={expected_reverse}, answer {answer_id}")

    def test_attention_check_only_correct_answer_scores_1(self):
        # Choice 7 is the attention-check row (7th and last row of QID100 --
        # 6 RPS items + 1 AC row = choice 7).
        for answer_id in range(1, 10):
            cell = self._cell(self.main_payload, 7, answer_id)
            expected = "1" if answer_id == add_rps.ATTENTION_CORRECT_ANSWER_ID else "0"
            self.assertEqual(cell["Grades"][add_rps.ATTENTION_CATEGORY_ID], expected)

    def test_item7_not_reverse_keyed(self):
        for answer_id in range(1, 10):
            cell = self._cell(self.item7_payload, 1, answer_id)
            self.assertEqual(
                cell["Grades"][add_rps.RPS_CATEGORY_ID], str(answer_id))

    def test_seven_rps_items_total(self):
        rps_choices_main = {g["ChoiceID"] for g in self.main_payload["GradingData"]
                            if add_rps.RPS_CATEGORY_ID in g["Grades"]}
        rps_choices_item7 = {g["ChoiceID"] for g in self.item7_payload["GradingData"]
                             if add_rps.RPS_CATEGORY_ID in g["Grades"]}
        self.assertEqual(len(rps_choices_main) + len(rps_choices_item7), 7)


class TestGenDripJs(unittest.TestCase):
    def setUp(self):
        self.pairs = gen_drip_js.load_pairs()
        self.js = gen_drip_js.render(self.pairs)

    def test_fifteen_pairs_loaded(self):
        self.assertEqual(len(self.pairs), 15)

    def test_reverse_pair_gets_6_minus_x_recode(self):
        reverse_pair = next(p for p in self.pairs if p["reverse1"] or p["reverse2"])
        idx = self.pairs.index(reverse_pair) + 1
        if reverse_pair["reverse1"]:
            self.assertIn(f"(6 - v{idx}a)", self.js)
        if reverse_pair["reverse2"]:
            self.assertIn(f"(6 - v{idx}b)", self.js)

    def test_non_reverse_pair_has_no_recode(self):
        plain_pair = next(p for p in self.pairs if not p["reverse1"] and not p["reverse2"])
        idx = self.pairs.index(plain_pair) + 1
        self.assertNotIn(f"(6 - v{idx}a)", self.js)
        self.assertNotIn(f"(6 - v{idx}b)", self.js)

    def test_every_pair_item_piped_from_qid2(self):
        for p in self.pairs:
            self.assertIn(f"QID2/SelectedChoicesRecode/{p['item1']}", self.js)
            self.assertIn(f"QID2/SelectedChoicesRecode/{p['item2']}", self.js)

    def test_nan_sentinel_present(self):
        # Regression for the Starlette-error-serializer issue: a failed
        # parseInt (skipped item) must sentinel to -1, never reach the Web
        # Service call as a literal NaN.
        self.assertIn("isNaN(drip)", self.js)
        self.assertIn("drip = -1", self.js)

    def test_drip_pairs_tsv_row_count_matches(self):
        pairs_file = REPO_ROOT / "response_verification" / "drip_item_pairs.tsv"
        with open(pairs_file, encoding="utf-8") as f:
            rows = list(csv.DictReader(f, delimiter="\t"))
        self.assertEqual(len(rows), 15)


class TestGeneratedQsf(unittest.TestCase):
    """output/BFI-2_Full_RPS.qsf is a committed deliverable (README/CLAUDE.md
    both list it), not a build artifact -- its absence is a real failure,
    not something to skip past."""

    @classmethod
    def setUpClass(cls):
        import json
        qsf_path = REPO_ROOT / "output" / "BFI-2_Full_RPS.qsf"
        if not qsf_path.exists():
            raise AssertionError(
                f"{qsf_path} is missing -- it's a committed deliverable "
                "(see README.md/CLAUDE.md), regenerate with "
                "`python3 .claude/skills/bfi2-qsf-splitter/add_rps.py`")
        cls.data = json.loads(qsf_path.read_text(encoding="utf-8"))

    def _sq(self, qid):
        return next(e for e in self.data["SurveyElements"]
                    if e["Element"] == "SQ" and e["PrimaryAttribute"] == qid)["Payload"]

    def test_rps_and_attention_categories_present(self):
        sco = next(e for e in self.data["SurveyElements"] if e["Element"] == "SCO")
        names = {c["Name"] for c in sco["Payload"]["ScoringCategories"]}
        self.assertIn("Risk Propensity", names)
        self.assertIn("Attention Check", names)

    def test_committed_qid100_matches_a_fresh_regeneration(self):
        # Not just category names: the persisted GradingData cells
        # themselves, compared against what add_rps.py produces right now
        # -- catches a stale/hand-edited committed file that's drifted
        # from what the generator would actually produce today.
        persisted = self._sq("QID100")
        fresh = add_rps.build_main_question()
        self.assertEqual(persisted["GradingData"], fresh["GradingData"])
        self.assertEqual(persisted["Choices"], fresh["Choices"])

    def test_committed_qid101_matches_a_fresh_regeneration(self):
        persisted = self._sq("QID101")
        fresh = add_rps.build_item7_question()
        self.assertEqual(persisted["GradingData"], fresh["GradingData"])
        self.assertEqual(persisted["Choices"], fresh["Choices"])


if __name__ == "__main__":
    unittest.main()
