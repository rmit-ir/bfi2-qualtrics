#!/usr/bin/env python3
"""Regression coverage for drip_spec.py -- pair-table structural validation,
cross-validation against a survey's real item metadata, and the canonical
hash both this repo and (a vendored copy in) ase2-ai-mode must reproduce
identically. See docs/qualtrics-part2-wiring.md for the sync contract.

Moved from tests/test_add_rps.py::TestGenDripJs after gen_drip_js.py (the
client-side JS generator) was deleted -- Question JavaScript never
executes in the target Qualtrics account, confirmed by repeated live
testing; the pair-integrity assertions that class owned still apply here.

Stdlib only (no pytest needed):
    python3 -m unittest discover tests
"""
import csv
import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = REPO_ROOT / ".claude" / "skills" / "bfi2-qsf-splitter"
sys.path.insert(0, str(SKILL_DIR))

import drip_spec  # noqa: E402

FULL_QSF = REPO_ROOT / "output" / "BFI-2_Full.qsf"


class TestLoadAndValidatePairs(unittest.TestCase):
    def setUp(self):
        self.pairs = drip_spec.load_pairs()

    def test_fifteen_pairs_loaded(self):
        self.assertEqual(len(self.pairs), 15)

    def test_drip_pairs_tsv_row_count_matches(self):
        with open(drip_spec.PAIRS_FILE, encoding="utf-8") as f:
            rows = list(csv.DictReader(f, delimiter="\t"))
        self.assertEqual(len(rows), 15)

    def test_no_item_reused_across_pairs(self):
        items = [p["item1"] for p in self.pairs] + [p["item2"] for p in self.pairs]
        self.assertEqual(len(items), len(set(items)), "an item appears in >1 pair")

    def test_wrong_pair_count_rejected(self):
        with self.assertRaises(SystemExit):
            drip_spec.validate_pair_structure(self.pairs[:-1])

    def test_reused_item_rejected(self):
        tampered = list(self.pairs)
        tampered[1] = dict(tampered[1], item1=tampered[0]["item1"])
        with self.assertRaises(SystemExit):
            drip_spec.validate_pair_structure(tampered)

    def test_empty_domain_rejected(self):
        tampered = list(self.pairs)
        tampered[0] = dict(tampered[0], domain="")
        with self.assertRaises(SystemExit):
            drip_spec.validate_pair_structure(tampered)


class TestStrictBoolParsing(unittest.TestCase):
    def test_typo_bool_rejected_not_silently_false(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "pairs.tsv"
            # A single-row file also fails the 15-pair check, but the typo
            # in Reverse1 must be what raises -- parsing happens per-row
            # before the pair-count check, so this still exercises the
            # bool parser specifically (SystemExit is raised at the first
            # bad row, before validate_pair_structure ever runs).
            p.write_text("Item1\tItem2\tReverse1\tReverse2\tDomain\tFacet\n"
                         "1\t2\ttru\tfalse\tD\tF\n", encoding="utf-8")
            with self.assertRaises(SystemExit):
                drip_spec.load_pairs(path=p)

    def test_missing_required_column_rejected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "pairs.tsv"
            p.write_text("Item1\tItem2\tReverse1\tReverse2\tDomain\n1\t2\tfalse\tfalse\tD\n",
                         encoding="utf-8")
            with self.assertRaises(SystemExit):
                drip_spec.load_pairs(path=p)


class TestCrossValidation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not FULL_QSF.exists():
            raise AssertionError(
                f"{FULL_QSF} missing -- run split_bfi2.py first "
                "(it's a committed deliverable, not optional).")
        cls.qsf = json.loads(FULL_QSF.read_text(encoding="utf-8"))
        cls.pairs = drip_spec.load_pairs()
        cls.item_meta = drip_spec.item_meta_from_qsf(cls.qsf)

    def test_committed_tsv_agrees_with_committed_full_form(self):
        drip_spec.cross_validate_pairs(self.pairs, self.item_meta)  # no raise

    def test_item_meta_has_sixty_items(self):
        self.assertEqual(len(self.item_meta), 60)

    def test_reverse_mismatch_rejected(self):
        tampered_meta = dict(self.item_meta)
        item = self.pairs[0]["item1"]
        tampered_meta[item] = dict(tampered_meta[item], reverse=not tampered_meta[item]["reverse"])
        with self.assertRaises(SystemExit):
            drip_spec.cross_validate_pairs(self.pairs, tampered_meta)

    def test_domain_mismatch_rejected(self):
        tampered_meta = dict(self.item_meta)
        item = self.pairs[0]["item1"]
        tampered_meta[item] = dict(tampered_meta[item], domain="NotARealDomain")
        with self.assertRaises(SystemExit):
            drip_spec.cross_validate_pairs(self.pairs, tampered_meta)

    def test_missing_item_rejected(self):
        tampered_meta = dict(self.item_meta)
        del tampered_meta[self.pairs[0]["item1"]]
        with self.assertRaises(SystemExit):
            drip_spec.cross_validate_pairs(self.pairs, tampered_meta)


class TestCanonicalHash(unittest.TestCase):
    def setUp(self):
        self.pairs = drip_spec.load_pairs()

    def test_fixed_expected_digest_for_current_tsv(self):
        # Regression pin: a change here means either drip_item_pairs.tsv
        # changed (expected -- update this constant) or
        # canonical_spec_bytes()'s serialization changed (NOT expected --
        # ase2-ai-mode's vendored copy must reproduce the same bytes for
        # the hash to mean anything as a sync check).
        self.assertEqual(
            drip_spec.canonical_spec_sha256(self.pairs),
            "5eb196865fb708c5c01485e74efc88d6d6442f98e4fec06b06b519ca6d203223")

    def test_deterministic_across_calls(self):
        self.assertEqual(drip_spec.canonical_spec_sha256(self.pairs),
                         drip_spec.canonical_spec_sha256(drip_spec.load_pairs()))

    def test_canonical_field_order_in_serialized_bytes(self):
        raw = drip_spec.canonical_spec_bytes(self.pairs)
        rows = json.loads(raw, object_pairs_hook=lambda pairs: pairs)
        first_row_keys = tuple(k for k, v in rows[0])
        self.assertEqual(first_row_keys, drip_spec.CANONICAL_FIELD_ORDER)

    def test_real_booleans_and_integers_not_strings(self):
        rows = json.loads(drip_spec.canonical_spec_bytes(self.pairs))
        row = rows[0]
        self.assertIsInstance(row["item1"], int)
        self.assertIsInstance(row["reverse1"], bool)

    def test_hash_changes_if_a_pair_changes(self):
        tampered = list(self.pairs)
        tampered[0] = dict(tampered[0], item1=tampered[0]["item1"] + 1000)
        self.assertNotEqual(drip_spec.canonical_spec_sha256(self.pairs),
                            drip_spec.canonical_spec_sha256(tampered))


if __name__ == "__main__":
    unittest.main()
