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

    def test_no_item_appears_in_more_than_one_pair(self):
        seen = set()
        for row in self.rows:
            for side in ("1", "2"):
                item = int(row[f"Item{side}"])
                self.assertNotIn(item, seen,
                                 f"item {item} appears in more than one DRIP pair")
                seen.add(item)

    def test_matches_paper_verified_pairs(self):
        # Ruchensky, Edens, & Donnellan (2025) Table 1, by BFI-2 item number
        # -- independently transcribed and verified against the paper (see
        # plans/careless-responding-detection.md's "Verified facts" section,
        # which records this same list as a checked fact, not just a
        # comment). This is the closest thing to an independent oracle this
        # repo has for the pair *set* itself (item numbers), as opposed to
        # the facet/domain/reverse consistency checked above, which is only
        # self-consistency against master_mapping.json.
        expected_pairs = {
            frozenset((54, 39)), frozenset((35, 20)), frozenset((31, 16)),
            frozenset((46, 1)), frozenset((56, 41)), frozenset((33, 3)),
            frozenset((49, 34)), frozenset((51, 21)), frozenset((44, 29)),
            frozenset((43, 13)), frozenset((53, 38)), frozenset((52, 7)),
            frozenset((58, 28)), frozenset((59, 14)), frozenset((60, 15)),
        }
        actual_pairs = {frozenset((int(row["Item1"]), int(row["Item2"])))
                        for row in self.rows}
        self.assertEqual(actual_pairs, expected_pairs)


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
        """Synthetic sample: one trait-consistent clean respondent, a
        straight-liner, an engineered inconsistent responder, a speeder, and
        a fleet of trait-consistent reference rows.

        Trait-consistent = each facet gets a trait level and every item in
        that facet is answered at that level on the KEYED scale (reverse
        items answered 6 - level raw). That satisfies every consistency
        check at once: DRIP pairs agree (same facet), facet even-odd halves
        agree, synonym pairs agree, and the respondent tracks the sample
        consensus — while raw answers still vary item to item (levels 2/3/4
        across facets), keeping longstring and within-person SD unflagged.
        """
        meta = full_choice_meta()
        facets = sorted({m["facet"] for m in meta.values()})
        facet_idx = {f: i for i, f in enumerate(facets)}

        def trait_respondent(level_of):
            """Raw 1..5 answers from keyed facet levels."""
            scores = {}
            for i in range(1, N_ITEMS + 1):
                lv = min(5, max(1, level_of(facet_idx[meta[i]["facet"]])))
                scores[i] = (6 - lv) if meta[i]["reverse"] else lv
            return scores

        clean = trait_respondent(lambda f: 2 + (f % 3))

        # Straight-liner: all raw "3" -> longstring 60, SD 0. DRIP stays 0
        # (keyed 3 either way), proving flags are independent.
        straight = {i: 3 for i in range(1, N_ITEMS + 1)}

        # Inconsistent responder engineered to guarantee DRIP >= 14: each
        # pair maximally disagrees post-reverse (|1-5| = 4 per pair -> 60).
        random_r = {i: 3 for i in range(1, N_ITEMS + 1)}
        for p in self.pairs:
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
        # Reference fleet: trait-consistent rows jittered around the clean
        # respondent's levels, so N > n_items (Mahalanobis runs), synonym
        # pairs exist, and the consensus sits near the clean row.
        for k in range(70):
            respondents.append({
                "scores": trait_respondent(
                    lambda f, k=k: 2 + (f % 3) + (((k + f) % 3) - 1)),
                "Duration": 300,
            })
        return respondents, clean, straight, random_r

    def test_flags(self):
        respondents, clean, straight, random_r = self._make_respondents()
        df = self._run(respondents)

        clean_row = df.iloc[0]
        straight_row = df.iloc[1]
        random_row = df.iloc[2]
        fast_row = df.iloc[3]

        # Clean: no flags at all, and high consistency metrics.
        self.assertEqual(clean_row["Careless_Flags"], "")
        self.assertGreater(float(clean_row["psychsyn_r"]), 0.9)
        self.assertGreater(float(clean_row["evenodd_r"]), 0.9)
        self.assertGreater(float(clean_row["person_total_r"]), 0.9)

        # Straight-liner: BOTH invariability checks fire; DRIP does NOT
        # (all-3 -> DRIP 0, the blind spot Ruchensky et al. acknowledge),
        # proving flags are independent. Its consistency correlations are
        # undefined (constant vector) and must not be flagged.
        self.assertIn("flag_longstring", straight_row["Careless_Flags"])
        self.assertIn("flag_variance", straight_row["Careless_Flags"])
        self.assertNotIn("flag_drip", straight_row["Careless_Flags"])
        self.assertNotIn("flag_psychsyn", straight_row["Careless_Flags"])
        self.assertEqual(int(float(straight_row["drip_score"])), 0)
        self.assertEqual(float(straight_row["response_sd"]), 0.0)

        # Inconsistent responder: the consistency checks fire (engineered to
        # DRIP 60 >= 14, which also destroys synonym and even-odd agreement).
        self.assertIn("flag_drip", random_row["Careless_Flags"])
        self.assertIn("flag_psychsyn", random_row["Careless_Flags"])
        self.assertIn("flag_evenodd", random_row["Careless_Flags"])
        self.assertGreaterEqual(int(float(random_row["drip_score"])),
                                self.mod.DEFAULT_DRIP_CUTOFF)

        # Too-fast: speed fires, and nothing else (same answers as clean).
        self.assertEqual(fast_row["Careless_Flags"], "flag_speed")

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
class TestSafetyFeatures(unittest.TestCase):
    """Direct coverage for the newer defensive checks -- threshold range
    validation, malformed-header rejection, formula-injection
    neutralization, output delimiter selection, and exact item-count
    enforcement -- none of which the flag-correctness tests above exercise."""

    @classmethod
    def setUpClass(cls):
        cls.mod = load_verify_module()

    def _one_respondent(self):
        return [{"scores": {i: 3 for i in range(1, N_ITEMS + 1)}, "Duration": 300}]

    def _write(self, d, respondents=None, **kwargs):
        src = Path(d) / "export.csv"
        write_synthetic_export(src, respondents or self._one_respondent(), **kwargs)
        return src

    def test_invalid_threshold_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            src = self._write(d)
            with self.assertRaises(SystemExit):
                self.mod.main([str(src), "--mahal-alpha", "0"])
            with self.assertRaises(SystemExit):
                self.mod.main([str(src), "--sd-cutoff", "-1"])
            with self.assertRaises(SystemExit):
                self.mod.main([str(src), "--syn-pair-r", "2"])

    def test_reprocessing_own_output_rejected(self):
        # Feeding this script's own previously-flagged output back in as
        # input must be rejected outright, not silently sanitize its own
        # computed metric columns (a negative correlation) as if they were
        # respondent data.
        with tempfile.TemporaryDirectory() as d:
            src = self._write(d)
            rows = list(csv.reader(open(src, newline="", encoding="utf-8")))
            rows[0].append("psychsyn_r")  # a reserved output column name
            for i, row in enumerate(rows[3:], start=3):
                row.append("-0.5")
            with open(src, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerows(rows)
            with self.assertRaises(SystemExit):
                self.mod.main([str(src)])

    def test_duplicate_item_number_columns_rejected(self):
        # "BFI-2_1" and "BFI-2_01" both parse to item 1 -- set(numbers)
        # alone would miss this (still covers 1..60), silently dropping
        # one column's data downstream. Must be rejected explicitly.
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "export.csv"
            write_synthetic_export(src, self._one_respondent())
            rows = list(csv.reader(open(src, newline="", encoding="utf-8")))
            item1_idx = rows[0].index("BFI-2_1")
            for row in rows:
                row.insert(item1_idx + 1, row[item1_idx])  # duplicate column
            rows[0][item1_idx + 1] = "BFI-2_01"
            with open(src, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerows(rows)
            with self.assertRaises(SystemExit):
                self.mod.main([str(src)])

    def test_malformed_header_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "bad.csv"
            # Only 2 header rows, not Qualtrics' real 3 (column names /
            # question text / ImportId JSON).
            with open(src, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["StartDate"] + ITEM_COLS)
                w.writerow(["Start Date"] + [f"Item {i}" for i in range(1, N_ITEMS + 1)])
                w.writerow(["2026-01-01"] + ["Agree strongly"] * N_ITEMS)
            with self.assertRaises(SystemExit):
                self.mod.main([str(src)])

    def test_truncated_item_set_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "export.csv"
            write_synthetic_export(src, self._one_respondent())
            # Drop the last 5 item columns -> 55 of 60, no longer the full
            # published form.
            import pandas as pd
            df = pd.read_csv(src, dtype=str)
            df = df.drop(columns=[f"BFI-2_{i}" for i in range(56, 61)])
            df.to_csv(src, index=False)
            with self.assertRaises(SystemExit):
                self.mod.main([str(src)])

    def test_output_delimiter_matches_requested_extension(self):
        with tempfile.TemporaryDirectory() as d:
            src = self._write(d)
            out = Path(d) / "flagged.tsv"
            rc = self.mod.main([str(src), "-o", str(out)])
            self.assertEqual(rc, 0)
            first_line = out.read_text(encoding="utf-8").splitlines()[0]
            self.assertIn("\t", first_line)
            self.assertNotIn(",", first_line.split("\t")[0])

    def test_formula_injection_neutralized_in_output(self):
        with tempfile.TemporaryDirectory() as d:
            src = self._write(d)
            # Poison an original-export column (not one this script
            # computes) with a spreadsheet formula -- editing the raw CSV
            # rows directly, not via a naive pandas re-read/write, which
            # would misinterpret the file's 3-row Qualtrics header as data.
            rows = list(csv.reader(open(src, newline="", encoding="utf-8")))
            start_date_col = rows[0].index("StartDate")
            rows[3][start_date_col] = "=cmd|'/c calc'!A1"  # row 3 = first respondent
            with open(src, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerows(rows)
            out = Path(d) / "flagged.csv"
            rc = self.mod.main([str(src), "-o", str(out)])
            self.assertEqual(rc, 0)
            import pandas as pd
            out_df = pd.read_csv(out, skiprows=[1, 2], dtype=str)
            self.assertTrue(out_df.iloc[0]["StartDate"].startswith("'="))

    def test_formula_injection_neutralized_in_header(self):
        # The header row is itself a CSV cell -- a poisoned column name
        # (DataExportTag) must be neutralized too, not just data cells.
        with tempfile.TemporaryDirectory() as d:
            src = self._write(d)
            rows = list(csv.reader(open(src, newline="", encoding="utf-8")))
            rows[0][rows[0].index("StartDate")] = "=cmd|'/c calc'!A1"
            with open(src, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerows(rows)
            out = Path(d) / "flagged.csv"
            rc = self.mod.main([str(src), "-o", str(out)])
            self.assertEqual(rc, 0)
            header = out.read_text(encoding="utf-8").splitlines()[0]
            self.assertIn("'=cmd", header)


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
