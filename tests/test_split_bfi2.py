#!/usr/bin/env python3
"""Regression coverage for split_bfi2.py -- the generator behind the three
committed output/*.qsf deliverables. Previously untested: only the
JSON-validity test (test_qsf_parses.py) and the splitter's own
generation-time assert_invariants covered this at all, and neither
persists as a regression check against the real scoring arithmetic.

Builds fixtures from the real, checked-in master_mapping.json (60 items,
verified against the official BFI-2 key) rather than the proprietary
unified source .qsf, which is deliberately not in this repo (see
CLAUDE.md) -- QID2's shape (Choices/Answers/ChoiceOrder) is fully known
and reproducible from that map alone.

Caveat: master_mapping.json is also this test suite's oracle for what the
"correct" domain/facet/reverse assignment is -- these tests verify the
splitter's ARITHMETIC and STRUCTURE against that map (internal
consistency, correct recoding, correct counts), not the map's own content
against the official published BFI-2 key independently. That one-time
human verification is documented, not re-run here, per CLAUDE.md (no
official reference PDF/SPSS-syntax ships in this repo to check against).


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

import split_bfi2 as sb  # noqa: E402


def load_raw_mapping():
    with open(SKILL_DIR / "master_mapping.json", encoding="utf-8") as f:
        return json.load(f)


def canonical_answers():
    return {
        "1": {"Display": "Disagree strongly"}, "2": {"Display": "Disagree a little"},
        "3": {"Display": "Neutral; no opinion"}, "4": {"Display": "Agree a little"},
        "5": {"Display": "Agree strongly"},
    }


def full_question_payload(item_texts):
    """A QID2-shaped payload: one row per item text, canonical 5-pt scale."""
    choices = {str(i): {"Display": text} for i, text in enumerate(item_texts, start=1)}
    return {
        "Choices": choices,
        "ChoiceOrder": list(range(1, len(item_texts) + 1)),
        "Answers": canonical_answers(),
        "AnswerOrder": [1, 2, 3, 4, 5],
    }


class TestMasterMappingIntegrity(unittest.TestCase):
    """The map itself: exactly 60 unique items, 30 reverse, 12/domain, 4/facet."""

    @classmethod
    def setUpClass(cls):
        cls.raw = load_raw_mapping()
        cls.normalized = {sb.normalize_text(k): v for k, v in cls.raw.items()}

    def test_sixty_unique_entries(self):
        self.assertEqual(len(self.raw), 60)
        self.assertEqual(len(self.normalized), 60,
                         "a normalized-text collision silently dropped an entry")

    def test_thirty_reverse_keyed(self):
        n_reverse = sum(1 for m in self.normalized.values() if m["reverse"])
        self.assertEqual(n_reverse, 30)

    def test_twelve_items_per_domain(self):
        from collections import Counter
        counts = Counter(m["domain"] for m in self.normalized.values())
        self.assertEqual(set(counts), set(sb.DOMAINS))
        for domain, n in counts.items():
            self.assertEqual(n, 12, f"domain {domain} has {n} items, expected 12")

    def test_four_items_per_facet(self):
        from collections import Counter
        counts = Counter(m["facet"] for m in self.normalized.values())
        self.assertEqual(set(counts), set(sb.FACET_DOMAIN))
        for facet, n in counts.items():
            self.assertEqual(n, 4, f"facet {facet} has {n} items, expected 4")

    def test_every_facet_domain_consistent(self):
        for text, m in self.normalized.items():
            self.assertEqual(sb.FACET_DOMAIN[m["facet"]], m["domain"],
                             f"{text!r}: facet {m['facet']} belongs to a "
                             f"different domain than {m['domain']}")


class TestValidateAnswerScale(unittest.TestCase):
    def test_canonical_scale_passes(self):
        payload = full_question_payload(["x"])
        sb.validate_answer_scale(payload, "QID2")  # must not raise

    def test_wrong_label_rejected(self):
        payload = full_question_payload(["x"])
        payload["Answers"]["3"]["Display"] = "Something else entirely"
        with self.assertRaises(ValueError):
            sb.validate_answer_scale(payload, "QID2")

    def test_missing_answer_id_rejected(self):
        payload = full_question_payload(["x"])
        del payload["Answers"]["5"]
        with self.assertRaises(ValueError):
            sb.validate_answer_scale(payload, "QID2")

    def test_reordered_ids_with_swapped_labels_rejected(self):
        # AnswerID 1 given the "Agree strongly" label -- a real, dangerous
        # misconfiguration (silently inverted scoring) this must catch.
        payload = full_question_payload(["x"])
        payload["Answers"]["1"]["Display"] = "Agree strongly"
        payload["Answers"]["5"]["Display"] = "Disagree strongly"
        with self.assertRaises(ValueError):
            sb.validate_answer_scale(payload, "QID2")


class TestBuildGradingDataFull(unittest.TestCase):
    """Exercises the real 60-item BFI-2 Full scoring against master_mapping.json."""

    @classmethod
    def setUpClass(cls):
        cls.mapping = sb.load_master_mapping()
        cls.item_texts = list(load_raw_mapping().keys())
        cls.payload = full_question_payload(cls.item_texts)
        cls.grading, cls.unmatched = sb.build_grading_data(
            cls.payload, cls.mapping, include_facets=True, qid="QID2")

    def test_all_sixty_items_matched(self):
        self.assertEqual(self.unmatched, [])
        n_items = len({g["ChoiceID"] for g in self.grading})
        self.assertEqual(n_items, 60)

    def test_cell_count(self):
        self.assertEqual(len(self.grading), 60 * 5)

    def test_reverse_item_scores_5_to_1(self):
        # "Tends to be disorganized." (choice 3 in master_mapping.json's own
        # order) is reverse-keyed per the official key.
        idx = self.item_texts.index("Tends to be disorganized.") + 1
        cells = {g["AnswerID"]: g for g in self.grading if g["ChoiceID"] == idx}
        domain_id = sb.DOMAIN_CATEGORY_ID["Conscientiousness"]
        self.assertEqual(cells[1]["Grades"][domain_id], "5")
        self.assertEqual(cells[5]["Grades"][domain_id], "1")

    def test_normal_item_scores_1_to_5(self):
        idx = self.item_texts.index("Is outgoing, sociable.") + 1
        cells = {g["AnswerID"]: g for g in self.grading if g["ChoiceID"] == idx}
        domain_id = sb.DOMAIN_CATEGORY_ID["Extraversion"]
        self.assertEqual(cells[1]["Grades"][domain_id], "1")
        self.assertEqual(cells[5]["Grades"][domain_id], "5")

    def test_item_carries_both_domain_and_facet_category(self):
        idx = self.item_texts.index("Is outgoing, sociable.") + 1
        cell = next(g for g in self.grading if g["ChoiceID"] == idx and g["AnswerID"] == 1)
        self.assertEqual(len(cell["Grades"]), 2)
        self.assertIn(sb.DOMAIN_CATEGORY_ID["Extraversion"], cell["Grades"])
        self.assertIn(sb.facet_category_id("Sociability"), cell["Grades"])

    def test_assert_invariants_passes_on_full_correct_set(self):
        categories = sb.build_scoring_categories(include_facets=True, short_form=False)
        form = {"output_file": "BFI-2_Full.qsf", "question_id": "QID2", "facets": True}
        sb.assert_invariants(self.grading, categories, form)  # must not raise


class TestBalancedOmissionCaught(unittest.TestCase):
    """Regression for the exact gap identified in review: a derived-only
    invariant check (n_items // 5) can't catch a *balanced* set of
    omissions. assert_invariants now checks against a hard expected total
    (EXPECTED_ITEMS), so this must be rejected."""

    def test_one_missing_item_per_domain_is_rejected(self):
        mapping = sb.load_master_mapping()
        item_texts = list(load_raw_mapping().keys())
        # Drop exactly one item per domain -> 55 items, still evenly
        # divisible (55 // 5 == 11 per domain) under the old derived check.
        dropped = set()
        seen_domains = set()
        for text in item_texts:
            domain = mapping[sb.normalize_text(text)]["domain"]
            if domain not in seen_domains:
                seen_domains.add(domain)
                dropped.add(text)
        kept = [t for t in item_texts if t not in dropped]
        self.assertEqual(len(kept), 55)

        payload = full_question_payload(kept)
        grading, unmatched = sb.build_grading_data(
            payload, mapping, include_facets=True, qid="QID2")
        self.assertEqual(unmatched, [])  # all 55 remaining items matched fine

        categories = sb.build_scoring_categories(include_facets=True, short_form=False)
        form = {"output_file": "BFI-2_Full.qsf", "question_id": "QID2", "facets": True}
        with self.assertRaises(AssertionError):
            sb.assert_invariants(grading, categories, form)


class TestUnmatchedItemsDontCrash(unittest.TestCase):
    """Regression: assert_invariants' hard 60/30/15 count check must not
    pre-empt the unmatched-item report with a bare AssertionError -- an
    item with unrecognized text should surface as `unmatched`, not crash."""

    def test_one_unrecognized_item_text_returns_unmatched_not_raises(self):
        mapping = sb.load_master_mapping()
        item_texts = list(load_raw_mapping().keys())
        item_texts[0] = "This text is not in master_mapping.json."
        payload = full_question_payload(item_texts)
        grading, unmatched = sb.build_grading_data(
            payload, mapping, include_facets=True, qid="QID2")  # must not raise
        self.assertEqual(unmatched, ["This text is not in master_mapping.json."])
        n_items = len({g["ChoiceID"] for g in grading})
        self.assertEqual(n_items, 59)


class TestBuildFormQsfDropsDanglingReferences(unittest.TestCase):
    """Regression for the block-copy bug: a source block containing an
    extra question alongside the target must not leak into the output's
    BlockElements (which would dangle, since only the target SQ is kept)."""

    def _minimal_source(self):
        item_texts = list(load_raw_mapping().keys())
        qid2_payload = full_question_payload(item_texts)
        qid2_payload["QuestionID"] = "QID2"
        decoy_payload = {"QuestionID": "QID99", "Choices": {}, "ChoiceOrder": [],
                         "Answers": {}, "AnswerOrder": []}
        block = {
            "Type": "Default", "Description": "combined block", "ID": "BL_1",
            "BlockElements": [
                {"Type": "Question", "QuestionID": "QID2"},
                {"Type": "Question", "QuestionID": "QID99"},  # the decoy
            ],
        }
        return {
            "SurveyEntry": {"SurveyID": "SV_test"},
            "SurveyElements": [
                {"SurveyID": "SV_test", "Element": "BL", "PrimaryAttribute": "Survey Blocks",
                 "Payload": [block]},
                {"SurveyID": "SV_test", "Element": "FL", "PrimaryAttribute": "Survey Flow",
                 "Payload": {"Flow": [{"ID": "BL_1", "Type": "Block", "FlowID": "FL_2"}],
                             "Properties": {"Count": 2}, "FlowID": "FL_1", "Type": "Root"}},
                {"SurveyID": "SV_test", "Element": "SQ", "PrimaryAttribute": "QID2",
                 "Payload": qid2_payload},
                {"SurveyID": "SV_test", "Element": "SQ", "PrimaryAttribute": "QID99",
                 "Payload": decoy_payload},
                {"SurveyID": "SV_test", "Element": "RS", "PrimaryAttribute": "RS_test",
                 "Payload": None},
                {"SurveyID": "SV_test", "Element": "QC", "PrimaryAttribute": "Survey Question Count",
                 "Payload": None},
            ],
        }

    def test_decoy_question_not_referenced_in_output_block(self):
        mapping = sb.load_master_mapping()
        source = self._minimal_source()
        form = {"question_id": "QID2", "survey_name": "test",
                "output_file": "BFI-2_Full.qsf", "facets": True, "short": False}
        qsf, matched_count, category_count, unmatched = sb.build_form_qsf(
            copy.deepcopy(source), form, mapping)
        self.assertEqual(unmatched, [])
        bl = next(e for e in qsf["SurveyElements"] if e["Element"] == "BL")
        qids_in_block = {be["QuestionID"] for be in bl["Payload"][0]["BlockElements"]}
        self.assertEqual(qids_in_block, {"QID2"})
        sq_qids = {e["PrimaryAttribute"] for e in qsf["SurveyElements"] if e["Element"] == "SQ"}
        self.assertEqual(sq_qids, {"QID2"})


SHORT_ITEM_TEXTS = ['Tends to be quiet.', 'Is compassionate, has a soft heart.', 'Tends to be disorganized.', 'Worries a lot.', 'Is fascinated by art, music, or literature.', 'Is dominant, acts as a leader.', 'Is sometimes rude to others.', 'Has difficulty getting started on tasks.', 'Tends to feel depressed, blue.', 'Has little interest in abstract ideas.', 'Is full of energy.', 'Assumes the best about people.', 'Is reliable, can always be counted on.', 'Is emotionally stable, not easily upset.', 'Is original, comes up with new ideas.', 'Is outgoing, sociable.', 'Can be cold and uncaring.', 'Keeps things neat and tidy.', 'Is relaxed, handles stress well.', 'Has few artistic interests.', 'Prefers to have others take charge.', 'Is respectful, treats others with respect.', 'Is persistent, works until the task is finished.', 'Feels secure, comfortable with self.', 'Is complex, a deep thinker.', 'Is less active than other people.', 'Tends to find fault with others.', 'Can be somewhat careless.', 'Is temperamental, gets emotional easily.', 'Has little creativity.']
XS_ITEM_TEXTS = SHORT_ITEM_TEXTS[:15]


class TestAllOrNothingAcrossForms(unittest.TestCase):
    """Regression: an unmatched item in ONE form (e.g. Full) must block
    writing ALL THREE forms, not just the broken one -- the three files
    are meant to always be regenerated together (same source, same
    mapping), so a partial write would leave a mixed-generation set."""

    def _multi_form_source(self, corrupt_full=False):
        item_texts = list(load_raw_mapping().keys())
        if corrupt_full:
            item_texts[0] = "This text is not in master_mapping.json."

        def question_payload(qid, texts):
            p = full_question_payload(texts)
            p["QuestionID"] = qid
            return p

        def block(qid):
            return {"Type": "Default", "Description": qid, "ID": f"BL_{qid}",
                    "BlockElements": [{"Type": "Question", "QuestionID": qid}]}

        return {
            "SurveyEntry": {"SurveyID": "SV_test"},
            "SurveyElements": [
                {"SurveyID": "SV_test", "Element": "BL", "PrimaryAttribute": "Survey Blocks",
                 "Payload": [block("QID2"), block("QID3"), block("QID4")]},
                {"SurveyID": "SV_test", "Element": "SQ", "PrimaryAttribute": "QID2",
                 "Payload": question_payload("QID2", item_texts)},
                {"SurveyID": "SV_test", "Element": "SQ", "PrimaryAttribute": "QID3",
                 "Payload": question_payload("QID3", SHORT_ITEM_TEXTS)},
                {"SurveyID": "SV_test", "Element": "SQ", "PrimaryAttribute": "QID4",
                 "Payload": question_payload("QID4", XS_ITEM_TEXTS)},
                {"SurveyID": "SV_test", "Element": "RS", "PrimaryAttribute": "RS_test", "Payload": None},
                {"SurveyID": "SV_test", "Element": "QC", "PrimaryAttribute": "Survey Question Count",
                 "Payload": None},
            ],
        }

    def _run_main_against_tempdir(self, source):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            tmp_out_dir = Path(d) / "output"
            tmp_out_dir.mkdir()
            src_path = Path(d) / "source.qsf"
            src_path.write_text(json.dumps(source), encoding="utf-8")

            orig_output_dir = sb.OUTPUT_DIR
            orig_paths = {f["question_id"]: f["output_path"] for f in sb.FORMS}
            sb.OUTPUT_DIR = tmp_out_dir
            for f in sb.FORMS:
                f["output_path"] = tmp_out_dir / f["output_file"]
            orig_argv = sys.argv
            sys.argv = ["split_bfi2.py", str(src_path)]
            try:
                try:
                    sb.main()
                    exit_code = 0
                except SystemExit as e:
                    exit_code = e.code
            finally:
                sb.OUTPUT_DIR = orig_output_dir
                for f in sb.FORMS:
                    f["output_path"] = orig_paths[f["question_id"]]
                sys.argv = orig_argv
            return exit_code, list(tmp_out_dir.iterdir())

    def test_all_three_written_when_all_clean(self):
        exit_code, files = self._run_main_against_tempdir(self._multi_form_source())
        self.assertEqual(exit_code, 0)
        self.assertEqual({f.name for f in files},
                         {"BFI-2_Full.qsf", "BFI-2_Short.qsf", "BFI-2_ExtraShort.qsf"})

    def test_none_written_when_full_has_unmatched_item(self):
        exit_code, files = self._run_main_against_tempdir(
            self._multi_form_source(corrupt_full=True))
        self.assertEqual(exit_code, 1)
        self.assertEqual(files, [],
                         "Short/XS were clean but must not be written when Full wasn't")


class TestCommittedOutputFiles(unittest.TestCase):
    """Spot-checks the REAL, committed output/*.qsf deliverables directly --
    not a synthetic fixture. Full's own regeneration can't be re-verified
    end-to-end here (the proprietary unified source isn't in this repo,
    see CLAUDE.md), so this is the closest thing to a regression test
    against what actually ships for Short and XS too, which
    TestBuildGradingDataFull above doesn't cover at all."""

    @classmethod
    def setUpClass(cls):
        cls.qsfs = {}
        for qid in ("QID2", "QID3", "QID4"):
            fname = {"QID2": "BFI-2_Full.qsf", "QID3": "BFI-2_Short.qsf",
                     "QID4": "BFI-2_ExtraShort.qsf"}[qid]
            data = json.loads((REPO_ROOT / "output" / fname).read_text(encoding="utf-8"))
            sq = next(e for e in data["SurveyElements"]
                     if e["Element"] == "SQ" and e["PrimaryAttribute"] == qid)
            cls.qsfs[qid] = sq["Payload"]

    def _grades_for(self, qid, item_text, category_id):
        payload = self.qsfs[qid]
        choice_id = next(int(cid) for cid, c in payload["Choices"].items()
                         if c["Display"] == item_text)
        return {g["AnswerID"]: g["Grades"][category_id]
                for g in payload["GradingData"] if g["ChoiceID"] == choice_id}

    def test_full_reverse_item_scores_5_to_1(self):
        grades = self._grades_for("QID2", "Tends to be disorganized.",
                                  sb.DOMAIN_CATEGORY_ID["Conscientiousness"])
        self.assertEqual(grades, {1: "5", 2: "4", 3: "3", 4: "2", 5: "1"})

    def test_short_reverse_item_scores_5_to_1(self):
        grades = self._grades_for("QID3", "Tends to be disorganized.",
                                  sb.DOMAIN_CATEGORY_ID["Conscientiousness"])
        self.assertEqual(grades, {1: "5", 2: "4", 3: "3", 4: "2", 5: "1"})

    def test_xs_reverse_item_scores_5_to_1(self):
        grades = self._grades_for("QID4", "Tends to be disorganized.",
                                  sb.DOMAIN_CATEGORY_ID["Conscientiousness"])
        self.assertEqual(grades, {1: "5", 2: "4", 3: "3", 4: "2", 5: "1"})

    def test_item_counts(self):
        for qid, expected in sb.EXPECTED_ITEMS.items():
            n = len(self.qsfs[qid]["Choices"])
            self.assertEqual(n, expected, f"{qid} has {n} items, expected {expected}")

    def test_xs_has_no_facet_categories(self):
        # Published BFI-2-XS key: domain scores only (single-item facets).
        cats = {g_cat for g in self.qsfs["QID4"]["GradingData"] for g_cat in g["Grades"]}
        self.assertEqual(cats, set(sb.DOMAIN_CATEGORY_ID.values()))


if __name__ == "__main__":
    unittest.main()
