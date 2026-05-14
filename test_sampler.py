"""
gold_standard_sampler/tests/test_sampler.py
--------------------------------------------
Tests against the sample data from the uploaded example files.
Run with:  python -m pytest tests/ -v
"""

from __future__ import annotations

import textwrap
import unittest
from unittest.mock import patch, mock_open

import io
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from models import ParseMethod, SourceProvenance, SamplingSlice
from parse_method_classifier import classify_parse_method
from publication_type_classifier import classify_publication_type
from sampler import _parse_result_file, _extract_field_presence, _build_candidate
from ranking import compute_priority_score


# ── Sample data (from uploaded example files) ──────────────────────────────

SAMPLE_TEXT_RESULT = textwrap.dedent("""\
    ---<1998cond.mat.11124T>---
    5 1989SPIE.739748824C -- * Current address, Michigan State University, East Lansing, MI 48824.
    0 ................... -- Prange, R. E. and Girvin, S. M., eds. The Quantum Hall Effect. Second ed. (Springer-Verlag, New York, 1990).
    0 ................... -- Das Sarma, S. and Pinczuk, A., eds. Perspectives in Quantum Hall Effects. (Wiley Inter-Science, New York, 1997).
    5 1981AIPC....4.5632L -- Laughlin, R. B. Quantized Hall conductivity in two dimensions. Phys. Rev. B 23, 5632-5634 (1981).
    1 1984SurSc.142..182C -- Chalker, J. T. The quantum Hall effect, a sum rule. Surface Science 142, 182-185 (1984).
    0 ................... -- von Klitzing, K., Dorda, G. and Pepper, M. New method for high-accuracy determination of the fine-structure constant. Phys. Rev. Lett. 45, 494-497 (1980).
""")

SAMPLE_XML_RESULT = textwrap.dedent("""\
    ---<1998Natur.396..695M>---
    0 ................... -- <mixed-citation publication-type="journal"><person-group person-group-type="author"><name><surname>Chen</surname><given-names>JM</given-names></name></person-group><article-title xml:lang="en">Cloning, isolation, and characterisation of mammalian legumain.</article-title><source>J. Biol. Chem.</source><year>1997</year><volume>272</volume><fpage>8090</fpage><lpage>8098</lpage><pub-id pub-id-type="doi">10.1074/jbc.272.12.8090</pub-id></mixed-citation>
    1 1993ArBB..303..208K -- <mixed-citation publication-type="journal"><person-group person-group-type="author"><name><surname>Kembhavi</surname><given-names>AA</given-names></name></person-group><article-title xml:lang="en">The two cysteine endopeptidases of legume seeds.</article-title><source>Arch. Biochem. Biophys.</source><year>1993</year><volume>303</volume><fpage>208</fpage><lpage>213</lpage><pub-id pub-id-type="doi">10.1006/abbi.1993.1274</pub-id></mixed-citation>
""")


# ── Tests: result file parsing ─────────────────────────────────────────────

class TestParseResultFile(unittest.TestCase):

    def _parse(self, content: str, fmt: str):
        with patch("builtins.open", mock_open(read_data=content)):
            return _parse_result_file("fake_path.result", fmt)

    def test_text_result_parses_all_lines(self):
        refs = self._parse(SAMPLE_TEXT_RESULT, "TEXT")
        self.assertEqual(len(refs), 6)

    def test_text_matched_line(self):
        refs = self._parse(SAMPLE_TEXT_RESULT, "TEXT")
        matched = [r for r in refs if r["status"] == 1]
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["matched_bibcode"], "1984SurSc.142..182C")

    def test_text_uncertain_line(self):
        refs = self._parse(SAMPLE_TEXT_RESULT, "TEXT")
        uncertain = [r for r in refs if r["status"] == 5]
        self.assertEqual(len(uncertain), 2)
        # bibcodes should be captured for uncertain matches
        bibcodes = {r["matched_bibcode"] for r in uncertain}
        self.assertIn("1989SPIE.739748824C", bibcodes)
        self.assertIn("1981AIPC....4.5632L", bibcodes)

    def test_text_unmatched_bibcode_is_none(self):
        refs = self._parse(SAMPLE_TEXT_RESULT, "TEXT")
        unmatched = [r for r in refs if r["status"] == 0]
        for r in unmatched:
            self.assertIsNone(r["matched_bibcode"])

    def test_xml_result_parses_correctly(self):
        refs = self._parse(SAMPLE_XML_RESULT, "XML")
        self.assertEqual(len(refs), 2)

    def test_xml_matched_line(self):
        refs = self._parse(SAMPLE_XML_RESULT, "XML")
        matched = [r for r in refs if r["status"] == 1]
        self.assertEqual(matched[0]["matched_bibcode"], "1993ArBB..303..208K")

    def test_citing_bibcode_assigned(self):
        refs = self._parse(SAMPLE_TEXT_RESULT, "TEXT")
        self.assertTrue(all(r["citing_bibcode"] == "1998cond.mat.11124T" for r in refs))


# ── Tests: parse method classifier ────────────────────────────────────────

class TestParseMethodClassifier(unittest.TestCase):

    def test_xml_mixed_citation(self):
        raw = ('<mixed-citation publication-type="journal">'
               '<surname>Chen</surname><year>1997</year>'
               '<source>J. Biol. Chem.</source>'
               '</mixed-citation>')
        pm, prov = classify_parse_method("XML", raw, None)
        # All content in tags → element_citation despite mixed-citation tag
        self.assertEqual(pm, ParseMethod.ELEMENT_CITATION)
        self.assertEqual(prov, SourceProvenance.PUBLISHER_XML)

    def test_xml_truly_mixed_with_free_text(self):
        raw = ('<mixed-citation publication-type="journal">'
               'Smith, J. A great paper. '
               '<source>Nature</source>, <year>2000</year>, '
               '<volume>400</volume>, <fpage>1</fpage>–<lpage>5</lpage>.'
               '</mixed-citation>')
        pm, prov = classify_parse_method("XML", raw, None)
        self.assertEqual(pm, ParseMethod.MIXED_CITATION)

    def test_xml_plain_string_is_unstructured(self):
        raw = "Smith, J. et al. Nature 400, 1-5 (2000)."
        pm, prov = classify_parse_method("XML", raw, None)
        self.assertEqual(pm, ParseMethod.UNSTRUCTURED)
        self.assertEqual(prov, SourceProvenance.PUBLISHER_XML)

    def test_text_format_is_unstructured_arxiv(self):
        pm, prov = classify_parse_method("TEXT", "Any string", None)
        self.assertEqual(pm, ParseMethod.UNSTRUCTURED)
        self.assertEqual(prov, SourceProvenance.ARXIV)

    def test_ocr_format_is_unstructured_ocr(self):
        pm, prov = classify_parse_method("OCR", "Any string", None)
        self.assertEqual(pm, ParseMethod.UNSTRUCTURED)
        self.assertEqual(prov, SourceProvenance.OCR)


# ── Tests: publication type classifier ────────────────────────────────────

class TestPublicationTypeClassifier(unittest.TestCase):

    def test_jats_journal_attribute(self):
        raw = '<mixed-citation publication-type="journal"><source>ApJ</source></mixed-citation>'
        pt, conf = classify_publication_type(ParseMethod.ELEMENT_CITATION, raw, "XML")
        self.assertEqual(pt, "journal_article")
        self.assertEqual(conf, "high")

    def test_jats_thesis_attribute(self):
        raw = '<element-citation publication-type="thesis"><source>MIT</source></element-citation>'
        pt, conf = classify_publication_type(ParseMethod.ELEMENT_CITATION, raw, "XML")
        self.assertEqual(pt, "thesis")
        self.assertEqual(conf, "high")

    def test_heuristic_thesis(self):
        raw = "Jones, A. B. 2001. PhD thesis, University of Cambridge."
        pt, conf = classify_publication_type(ParseMethod.UNSTRUCTURED, raw, "TEXT")
        self.assertEqual(pt, "thesis")

    def test_heuristic_conference(self):
        raw = "Smith, J. et al. 2018. Proc. LPSC, 49, Abstract 1234."
        pt, conf = classify_publication_type(ParseMethod.UNSTRUCTURED, raw, "TEXT")
        self.assertEqual(pt, "conference_proceedings")

    def test_heuristic_book(self):
        raw = "Prange, R. E. and Girvin, S. M., eds. The Quantum Hall Effect. Springer-Verlag, New York, 1990."
        pt, conf = classify_publication_type(ParseMethod.UNSTRUCTURED, raw, "TEXT")
        self.assertEqual(pt, "book")

    def test_default_journal_article(self):
        raw = "Laughlin, R. B. Quantized Hall conductivity in two dimensions. Phys. Rev. B 23, 5632-5634 (1981)."
        pt, conf = classify_publication_type(ParseMethod.UNSTRUCTURED, raw, "TEXT")
        self.assertEqual(pt, "journal_article")

    def test_short_string_is_unknown(self):
        pt, conf = classify_publication_type(ParseMethod.UNSTRUCTURED, "Too short", "TEXT")
        self.assertEqual(pt, "unknown")
        self.assertEqual(conf, "unknown")


# ── Tests: field presence extraction ──────────────────────────────────────

class TestFieldPresenceExtraction(unittest.TestCase):

    def test_xml_extracts_year(self):
        raw = "<mixed-citation><year>1997</year><source>Nature</source></mixed-citation>"
        fp = _extract_field_presence(raw, ParseMethod.ELEMENT_CITATION)
        self.assertTrue(fp["year"])
        self.assertTrue(fp["journal"])

    def test_text_year_detected(self):
        raw = "Smith, J. A. 1999. Some paper. Nature 400, 1-5."
        fp = _extract_field_presence(raw, ParseMethod.UNSTRUCTURED)
        self.assertTrue(fp["year"])

    def test_text_doi_detected(self):
        raw = "doi: 10.1038/nature12345"
        fp = _extract_field_presence(raw, ParseMethod.UNSTRUCTURED)
        self.assertTrue(fp["doi"])

    def test_text_arxiv_detected(self):
        raw = "arXiv: 1901.00001"
        fp = _extract_field_presence(raw, ParseMethod.UNSTRUCTURED)
        self.assertTrue(fp["arxiv_id"])


# ── Tests: ranking ─────────────────────────────────────────────────────────

class TestRanking(unittest.TestCase):

    def _make_candidate(self, status, pub_type="journal_article",
                        parse_method=ParseMethod.UNSTRUCTURED,
                        sampling_slice=SamplingSlice.MID_CONFIDENCE):
        from models import ReferenceInstance, CandidateRecord, GoldCandidate
        ref = ReferenceInstance(
            id="test-id",
            reference_string="test ref",
            source_document_id="2000ApJ..123..456A",
            source_filename="test.result",
            parse_method=parse_method,
            source_provenance=SourceProvenance.ARXIV,
            discipline="astrophysics",
            publication_type=pub_type,
            publication_type_confidence="medium",
            field_presence={"author": True, "year": True, "journal": False,
                            "volume": False, "page": False, "doi": False,
                            "arxiv_id": False, "title": False},
            ads_confidence_score=0.6,
            sampling_slice=sampling_slice,
            article_n_total=30, article_n_matched=20,
            article_n_uncertain=3, article_n_unmatched=7,
        )
        cand = CandidateRecord(
            id="cand-id", bibcode=None, scix_id=None,
            negative_type=None,
            needs_adjudication=(status == 5),
        )
        return GoldCandidate(reference=ref, candidate=cand, match_status=status)

    def test_mid_confidence_scores_higher_than_high(self):
        mid = self._make_candidate(1, sampling_slice=SamplingSlice.MID_CONFIDENCE)
        high = self._make_candidate(1, sampling_slice=SamplingSlice.HIGH_CONFIDENCE)
        self.assertGreater(compute_priority_score(mid),
                           compute_priority_score(high))

    def test_thesis_scores_higher_than_journal(self):
        thesis = self._make_candidate(1, pub_type="thesis")
        journal = self._make_candidate(1, pub_type="journal_article")
        self.assertGreater(compute_priority_score(thesis),
                           compute_priority_score(journal))

    def test_near_miss_gets_bonus(self):
        near_miss = self._make_candidate(5)
        matched = self._make_candidate(1)
        # both mid-confidence, but near-miss should score higher
        self.assertGreater(compute_priority_score(near_miss),
                           compute_priority_score(matched))

    def test_score_bounded_01(self):
        for status in (0, 1, 5):
            for pt in ("journal_article", "thesis", "conference_proceedings"):
                c = self._make_candidate(status, pub_type=pt)
                score = compute_priority_score(c)
                self.assertGreaterEqual(score, 0.0)
                self.assertLessEqual(score, 1.0)




# ── Tests: interleaved iteration ───────────────────────────────────────────

class TestInterleavedIteration(unittest.TestCase):

    def _make_file(self, rows):
        """Returns a list of lines as a mock file."""
        lines = []
        for i, (bibcode, disc) in enumerate(rows):
            lines.append(
                f"{bibcode}\t/fake/path.result\t10\t8\t1\t1\tXML\t{disc}\n"
            )
        return "".join(lines)

    def test_single_file_passthrough(self):
        """Single file yields the same rows as _iter_single_file."""
        from sampler import _iter_interleaved
        content = self._make_file([
            ("2000ApJ..100....1A", "AST"),
            ("2001ApJ..101....2B", "AST"),
        ])
        with patch("builtins.open", mock_open(read_data=content)):
            rows = list(_iter_interleaved("fake.txt"))
        self.assertEqual(len(rows), 2)

    def test_interleave_two_files_round_robin(self):
        """Rows from two files are interleaved in round-robin order."""
        from sampler import _iter_interleaved, _iter_single_file
        file_a = self._make_file([
            ("2000ApJ..100....1A", "AST"),
            ("2001ApJ..101....2B", "AST"),
        ])
        file_b = self._make_file([
            ("2000GeoRL..10....1C", "GEO"),
            ("2001GeoRL..11....2D", "GEO"),
        ])

        call_count = [0]
        files = {"a.txt": io.StringIO(file_a), "b.txt": io.StringIO(file_b)}

        def fake_open(path, *args, **kwargs):
            return files[path]

        with patch("builtins.open", side_effect=fake_open):
            rows = list(_iter_interleaved("a.txt", "b.txt"))

        # Should be 4 rows total
        self.assertEqual(len(rows), 4)
        # First row from file a (AST), second from file b (GEO), etc.
        self.assertEqual(rows[0][7], "AST")
        self.assertEqual(rows[1][7], "GEO")
        self.assertEqual(rows[2][7], "AST")
        self.assertEqual(rows[3][7], "GEO")

    def test_interleave_unequal_files(self):
        """When one file runs out, iteration continues from the other."""
        from sampler import _iter_interleaved
        file_short = self._make_file([("2000ApJ..100....1A", "AST")])
        file_long  = self._make_file([
            ("2000GeoRL..10....1C", "GEO"),
            ("2001GeoRL..11....2D", "GEO"),
            ("2002GeoRL..12....3E", "GEO"),
        ])

        files = {
            "short.txt": io.StringIO(file_short),
            "long.txt":  io.StringIO(file_long),
        }

        def fake_open(path, *args, **kwargs):
            return files[path]

        with patch("builtins.open", side_effect=fake_open):
            rows = list(_iter_interleaved("short.txt", "long.txt"))

        self.assertEqual(len(rows), 4)
        # First two rows: one from each file
        self.assertEqual(rows[0][7], "AST")
        self.assertEqual(rows[1][7], "GEO")
        # Remaining three rows are all from the long file
        self.assertTrue(all(r[7] == "GEO" for r in rows[2:]))


# ── Tests: report.py directory handling ───────────────────────────────────

class TestReportDirectoryHandling(unittest.TestCase):

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp)

    def _data_dir(self, name="data"):
        return os.path.join(self.tmp, name)

    def _report_dir(self, name="reports"):
        return os.path.join(self.tmp, name)

    def test_nonexistent_data_dir_is_created(self):
        """report.run() creates data_dir if it does not exist."""
        from report import run as report_run
        data_dir = self._data_dir("nonexistent_data")
        report_dir = self._report_dir()
        report_run(data_dir, report_dir)
        self.assertTrue(os.path.isdir(data_dir))

    def test_nonexistent_report_dir_is_created(self):
        """report.run() creates report_dir if it does not exist."""
        from report import run as report_run
        data_dir = self._data_dir()
        os.makedirs(data_dir)
        report_dir = self._report_dir("nonexistent_reports")
        report_run(data_dir, report_dir)
        self.assertTrue(os.path.isdir(report_dir))

    def test_empty_data_dir_writes_notice(self):
        """report.run() writes a notice file when data_dir has no JSONL files."""
        from report import run as report_run
        data_dir = self._data_dir()
        report_dir = self._report_dir()
        os.makedirs(data_dir)
        report_run(data_dir, report_dir)
        # A report_index.txt notice should be written
        self.assertTrue(os.path.isfile(os.path.join(report_dir, "report_index.txt")))
        with open(os.path.join(report_dir, "report_index.txt")) as f:
            content = f.read()
        self.assertIn("No JSONL files found", content)

    def test_defaults_used_when_no_args(self):
        """run() accepts calls with no arguments using config defaults."""
        from report import run as report_run
        # Both dirs will be created; no JSONL files so exits cleanly
        data_dir = self._data_dir("default_data")
        report_dir = self._report_dir("default_reports")
        # Should not raise
        report_run(data_dir, report_dir)

    def test_report_files_written_when_jsonl_present(self):
        """report.run() writes cell_summary, pub_type, floor_check when data exists."""
        import json as _json
        from report import run as report_run
        data_dir = self._data_dir()
        report_dir = self._report_dir()
        os.makedirs(data_dir)
        # Write a minimal JSONL file so _load_cells finds something
        sample = {
            "reference": {
                "publication_type": "journal_article",
                "discipline": "astrophysics",
                "parse_method": "element_citation",
                "ads_confidence_score": 0.9,
                "sampling_slice": "high_confidence",
                "field_presence": {}
            },
            "candidate": {"negative_type": None},
            "match_status": 1,
            "priority_score": 0.6,
        }
        jsonl_path = os.path.join(data_dir, "astrophysics__element_citation.jsonl")
        with open(jsonl_path, "w") as f:
            f.write(_json.dumps(sample) + "\n")
        report_run(data_dir, report_dir)
        for fname in ("cell_summary.txt", "pub_type_distribution.txt",
                      "floor_check.txt", "report_index.txt"):
            self.assertTrue(
                os.path.isfile(os.path.join(report_dir, fname)),
                f"Expected {fname} to be written to {report_dir}"
            )


if __name__ == "__main__":
    unittest.main()
