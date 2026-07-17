#!/usr/bin/env python3
"""Tests for response_verification/verify_responses.py.

Builds a synthetic full-form Qualtrics export (real 3-row header shape, answer
*text* cells) with hand-picked respondents and asserts each careless-responding
flag fires exactly where expected. Also cross-checks that drip_item_pairs.tsv
agrees with master_mapping.json.

Stdlib unittest (repo convention). The verify_responses checks need
pandas/numpy/scipy; those tests skip cleanly when the deps are absent.

    python3 -m unittest discover tests
"""
import csv
import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RV_DIR = REPO_ROOT / "response_verification"
DRIP_TSV = RV_DIR / "drip_item_pairs.tsv"
MASTER_MAPPING = REPO_ROOT / ".claude" / "skills" / "bfi2-qsf-splitter" / "master_mapping.json"
FULL_QSF = REPO_ROOT / "output" / "BFI-2_Full.qsf"

try:
    import pandas  # noqa: F401
    import numpy  # noqa: F401
    import scipy  # noqa: F401
    HAVE_DEPS = True
except ImportError:
    HAVE_DEPS = False


def normalize_text(text):
    text = text.replace("&nbsp;", " ").replace(" ", " ")
    text = text.replace("‘", "'").replace("’", "'")
    text = re.sub(r"\s+", " ", text).strip()
    return text.lower()


def load_mapping():
    with open(MASTER_MAPPING, encoding="utf-8") as f:
        return {normalize_text(k): v for k, v in json.load(f).items()}


def full_choice_meta():
    """item number -> mapping entry, via the Full-form qsf item text."""
    mapping = load_mapping()
    qsf = json.loads(FULL_QSF.read_text(encoding="utf-8"))
    sq = next(e for e in qsf["SurveyElements"]
              if e["Element"] == "SQ" and e["PrimaryAttribute"] == "QID2")
    return {int(cid): mapping[normalize_text(c["Display"])]
            for cid, c in sq["Payload"]["Choices"].items()}


# Answer text for each numeric score, matching verify_responses.ANSWER_SCALE.
SCORE_TEXT = {
    1: "Disagree strongly",
    2: "Disagree a little",
    3: "Neutral; no opinion",
    4: "Agree a little",
    5: "Agree strongly",
}

N_ITEMS = 60
ITEM_COLS = [f"BFI-2_{i}" for i in range(1, N_ITEMS + 1)]


def load_verify_module():
    spec = importlib.util.spec_from_file_location(
        "verify_responses", RV_DIR / "verify_responses.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestDripPairTable(unittest.TestCase):
    """Pair-table integrity — runs without pandas/numpy/scipy."""

    def setUp(self):
        self.meta = full_choice_meta()
        self.rows = []
        with open(DRIP_TSV, encoding="utf-8") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                self.rows.append(row)

    def test_fifteen_pairs(self):
        self.assertEqual(len(self.rows), 15)

    def test_items_valid_and_consistent(self):
        for row in self.rows:
            for side in ("1", "2"):
                item = int(row[f"Item{side}"])
                self.assertIn(item, self.meta,
                              f"pair {row['Pair']}: item {item} not in form")
                meta = self.meta[item]
                self.assertEqual(meta["facet"], row["Facet"],
                                 f"pair {row['Pair']}: item {item} facet mismatch")
                self.assertEqual(meta["domain"], row["Domain"],
                                 f"pair {row['Pair']}: item {item} domain mismatch")
                tsv_rev = row[f"Reverse{side}"].strip().lower() == "true"
                self.assertEqual(bool(meta["reverse"]), tsv_rev,
                                 f"pair {row['Pair']}: item {item} reverse mismatch")


def write_synthetic_export(path, respondents, sep=",", encoding="utf-8",
                           mode="label"):
    """Write a 3-row-header Qualtrics export.

    respondents: list of dict {item_number: score_int} plus optional
    'Duration' seconds. ``sep`` picks CSV (",") vs TSV ("\\t"); ``encoding``
    lets a test emit a UTF-16 TSV like a real Qualtrics export; ``mode`` picks
    label cells ("Agree strongly") vs numeric-value cells ("5")."""
    columns = ["StartDate", "Duration (in seconds)"] + ITEM_COLS
    q_text = ["Start Date", "Duration (in seconds)"] + \
        [f"Item {i}" for i in range(1, N_ITEMS + 1)]
    import_ids = ['{"ImportId":"startDate","timeZone":"Z"}',
                  '{"ImportId":"duration"}'] + \
        [f'{{"ImportId":"QID2_{i}"}}' for i in range(1, N_ITEMS + 1)]
    with open(path, "w", newline="", encoding=encoding) as f:
        w = csv.writer(f, delimiter=sep)
        w.writerow(columns)
        w.writerow(q_text)
        w.writerow(import_ids)
        for r in respondents:
            duration = r.get("Duration", 300)
            row = ["2026-07-17 10:00:00", str(duration)]
            for i in range(1, N_ITEMS + 1):
                score = r["scores"].get(i)
                if score is None:
                    row.append("")
                elif mode == "value":
                    row.append(str(score))
                else:
                    row.append(SCORE_TEXT[score])
            w.writerow(row)


@unittest.skipUnless(HAVE_DEPS, "pandas/numpy/scipy not installed")
class TestVerifyResponses(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_verify_module()
        cls.pairs = cls.mod.load_drip_pairs()

    def _drip_of(self, scores):
        """Reference DRIP: reverse-key then sum |item1-item2| over 15 pairs."""
        total = 0
        for p in self.pairs:
            v1 = scores[p["item1"]]
            v2 = scores[p["item2"]]
            if p["reverse1"]:
                v1 = 6 - v1
            if p["reverse2"]:
                v2 = 6 - v2
            total += abs(v1 - v2)
        return total

    def _run(self, respondents, argv_extra=None, sep=",", encoding="utf-8",
             mode="label", suffix=".csv"):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / f"export{suffix}"
            out = Path(d) / "flagged.csv"
            write_synthetic_export(src, respondents, sep=sep,
                                   encoding=encoding, mode=mode)
            argv = [str(src), "-o", str(out)] + (argv_extra or [])
            rc = self.mod.main(argv)
            self.assertEqual(rc, 0)
            import pandas as pd
            # Skip the 2 extra header rows written back into the output.
            # keep_default_na=False so an empty Careless_Flags stays "" (a
            # clean respondent), not NaN.
            df = pd.read_csv(out, skiprows=[1, 2], dtype=str,
                             keep_default_na=False)
            return df

    def _make_respondents(self):
        # A clean, varied respondent — no flags. Build a pattern with no long
        # runs and a low DRIP (consistent within each pair).
        clean = {}
        base_cycle = [2, 4, 1, 5, 3]
        for i in range(1, N_ITEMS + 1):
            clean[i] = base_cycle[(i - 1) % len(base_cycle)]
        # Force each DRIP pair to be internally consistent (post-reverse equal)
        # so the clean respondent's DRIP is 0.
        for p in self.pairs:
            v2 = clean[p["item2"]]
            target = (6 - v2) if p["reverse2"] else v2
            clean[p["item1"]] = (6 - target) if p["reverse1"] else target

        # Straight-liner: all "3" -> longstring 60. DRIP: post-reverse a pair
        # may differ, but with all raw 3 -> reverse gives 3 too, so DRIP 0.
        straight = {i: 3 for i in range(1, N_ITEMS + 1)}

        # Random responder engineered to guarantee DRIP >= 14: set each pair to
        # maximal post-reverse disagreement (|1-5| = 4 per pair -> 60 total),
        # while keeping raw runs short.
        random_r = {i: 3 for i in range(1, N_ITEMS + 1)}
        for p in self.pairs:
            # raw so that post-reverse item1=1, item2=5 -> |1-5|=4.
            random_r[p["item1"]] = 5 if p["reverse1"] else 1
            random_r[p["item2"]] = 1 if p["reverse2"] else 5

        # Too-fast respondent: same clean pattern, tiny duration.
        fast = dict(clean)

        respondents = [
            {"scores": clean, "Duration": 300},
            {"scores": straight, "Duration": 300},
            {"scores": random_r, "Duration": 300},
            {"scores": fast, "Duration": 10},  # 10s < 2*60=120
        ]
        # Pad with extra clean-ish rows so N > n_items for Mahalanobis to run.
        for k in range(70):
            jitter = dict(clean)
            # small varied perturbation, keep runs short and DRIP low-ish
            idx = (k % N_ITEMS) + 1
            jitter[idx] = (jitter[idx] % 5) + 1
            respondents.append({"scores": jitter, "Duration": 300})
        return respondents, clean, straight, random_r

    def test_flags(self):
        respondents, clean, straight, random_r = self._make_respondents()
        df = self._run(respondents)

        clean_row = df.iloc[0]
        straight_row = df.iloc[1]
        random_row = df.iloc[2]
        fast_row = df.iloc[3]

        # Clean: no flags at all.
        self.assertEqual(clean_row["Careless_Flags"], "")

        # Straight-liner: longstring fires; DRIP does NOT (all-3 -> DRIP 0),
        # proving flags are independent.
        self.assertIn("flag_longstring", straight_row["Careless_Flags"])
        self.assertNotIn("flag_drip", straight_row["Careless_Flags"])
        self.assertEqual(int(float(straight_row["drip_score"])), 0)

        # Random responder: DRIP fires (engineered to 60 >= 14).
        self.assertIn("flag_drip", random_row["Careless_Flags"])
        self.assertGreaterEqual(int(float(random_row["drip_score"])),
                                self.mod.DEFAULT_DRIP_CUTOFF)

        # Too-fast: speed fires.
        self.assertIn("flag_speed", fast_row["Careless_Flags"])

        # drip_score column matches an independent reference computation.
        self.assertEqual(int(float(random_row["drip_score"])),
                         self._drip_of(random_r))
        self.assertEqual(int(float(clean_row["drip_score"])),
                         self._drip_of(clean))

    def test_longstring_value(self):
        respondents, clean, straight, random_r = self._make_respondents()
        df = self._run(respondents)
        self.assertEqual(int(float(df.iloc[1]["longstring_max"])), N_ITEMS)

    def _assert_expected_flags(self, df, clean, straight, random_r):
        self.assertEqual(df.iloc[0]["Careless_Flags"], "")
        self.assertIn("flag_longstring", df.iloc[1]["Careless_Flags"])
        self.assertIn("flag_drip", df.iloc[2]["Careless_Flags"])
        self.assertIn("flag_speed", df.iloc[3]["Careless_Flags"])
        self.assertEqual(int(float(df.iloc[2]["drip_score"])),
                         self._drip_of(random_r))

    def test_tsv_utf16_value_mode(self):
        """A UTF-16 TSV with numeric-value cells (real Qualtrics TSV export
        shape) is read identically to a UTF-8 CSV with label cells."""
        respondents, clean, straight, random_r = self._make_respondents()
        df = self._run(respondents, sep="\t", encoding="utf-16",
                       mode="value", suffix=".tsv")
        self._assert_expected_flags(df, clean, straight, random_r)

    def test_value_mode_csv(self):
        """Numeric-value cells in a CSV recode the same as label cells."""
        respondents, clean, straight, random_r = self._make_respondents()
        df_label = self._run(respondents, mode="label")
        df_value = self._run(respondents, mode="value")
        self.assertEqual(list(df_label["Careless_Flags"]),
                         list(df_value["Careless_Flags"]))
        self.assertEqual(list(df_label["drip_score"]),
                         list(df_value["drip_score"]))

    def test_mode_inference(self):
        import pandas as pd
        item_cols = ITEM_COLS[:5]
        labels = pd.DataFrame([["Agree strongly", "Neutral; no opinion",
                                "Disagree a little", "Agree a little",
                                "Disagree strongly"]], columns=item_cols)
        values = pd.DataFrame([["5", "3", "2", "4", "1"]], columns=item_cols)
        self.assertEqual(self.mod.infer_export_mode(labels, item_cols), "label")
        self.assertEqual(self.mod.infer_export_mode(values, item_cols), "value")


@unittest.skipUnless(HAVE_DEPS, "pandas/numpy/scipy not installed")
class TestRealSampleFixture(unittest.TestCase):
    """Runs the detector against the checked-in fake-response TSV export."""

    SAMPLE = RV_DIR / "sample_data" / "BFI-2-Full_July 16, 2026_19.46.tsv"

    @classmethod
    def setUpClass(cls):
        if not cls.SAMPLE.exists():
            raise unittest.SkipTest(f"sample export not present: {cls.SAMPLE}")
        cls.mod = load_verify_module()

    def test_runs_and_flags(self):
        import pandas as pd
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "flagged.csv"
            rc = self.mod.main([str(self.SAMPLE), "-o", str(out)])
            self.assertEqual(rc, 0)
            df = pd.read_csv(out, skiprows=[1, 2], dtype=str,
                             keep_default_na=False)
        # Detector produced full-form columns and scored every respondent.
        self.assertIn("drip_score", df.columns)
        self.assertIn("Careless_Flags", df.columns)
        self.assertEqual(len(df), 7)
        # These are fabricated careless responses: every row is flagged.
        self.assertTrue((df["Careless_Flags"] != "").all())


if __name__ == "__main__":
    unittest.main()
