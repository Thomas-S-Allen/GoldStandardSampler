"""Tests for the local JSONL output viewer."""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from viewer import JsonlStore, PAGE_SIZE


class TestJsonlStore(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.output_dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _write_cell(self, cell_key, records):
        path = os.path.join(self.output_dir, f"{cell_key}.jsonl")
        with open(path, "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record) + "\n")

    def test_cells_uses_summary_metadata(self):
        cell_key = "astrophysics__unstructured"
        self._write_cell(cell_key, [{"priority_score": 0.5}])
        summary = {
            cell_key: {"target_min": 600, "target_max": 800, "candidates": 1}
        }
        with open(os.path.join(self.output_dir, "sampling_summary.json"),
                  "w", encoding="utf-8") as handle:
            json.dump(summary, handle)

        cells = JsonlStore(self.output_dir).cells()

        self.assertEqual(cells, [{
            "key": cell_key,
            "discipline": "astrophysics",
            "parse_method": "unstructured",
            "count": 1,
            "target_min": 600,
            "target_max": 800,
        }])

    def test_cells_counts_lines_without_summary(self):
        self._write_cell("heliophysics__mixed_citation", [{}, {}, {}])

        cells = JsonlStore(self.output_dir).cells()

        self.assertEqual(cells[0]["count"], 3)

    def test_page_returns_fixed_size_pages_in_file_order(self):
        records = [{"position": index} for index in range(PAGE_SIZE + 2)]
        self._write_cell("earth_science__element_citation", records)
        store = JsonlStore(self.output_dir)

        first = store.page("earth_science__element_citation", 1)
        second = store.page("earth_science__element_citation", 2)

        self.assertEqual(len(first["records"]), PAGE_SIZE)
        self.assertEqual(first["records"][0]["position"], 0)
        self.assertEqual(first["ranks"][0], 1)
        self.assertEqual(second["records"], [{"position": PAGE_SIZE},
                                              {"position": PAGE_SIZE + 1}])
        self.assertEqual(second["ranks"], [PAGE_SIZE + 1, PAGE_SIZE + 2])
        self.assertEqual(second["pages"], 2)

    def test_page_filters_by_match_category_and_keeps_original_rank(self):
        records = [
            {"position": 1, "match_status": 1},
            {"position": 2, "match_status": 5},
            {"position": 3, "match_status": 0},
            {"position": 4, "match_status": 5},
        ]
        self._write_cell("astrophysics__unstructured", records)
        store = JsonlStore(self.output_dir)

        page = store.page("astrophysics__unstructured", 1, "near_miss")

        self.assertEqual([row["position"] for row in page["records"]], [2, 4])
        self.assertEqual(page["ranks"], [2, 4])
        self.assertEqual(page["total"], 2)
        self.assertEqual(page["category"], "near_miss")

    def test_unknown_match_category_is_rejected(self):
        self._write_cell("astrophysics__unstructured", [])

        with self.assertRaises(ValueError):
            JsonlStore(self.output_dir).page(
                "astrophysics__unstructured", 1, "maybe"
            )

    def test_page_reports_malformed_json(self):
        path = os.path.join(self.output_dir, "test__unstructured.jsonl")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write('{"valid": true}\n')
            handle.write("not json\n")

        page = JsonlStore(self.output_dir).page("test__unstructured", 1)

        self.assertEqual(page["records"], [{"valid": True}])
        self.assertEqual(page["invalid_records"], 1)

    def test_unknown_cell_is_rejected(self):
        with self.assertRaises(KeyError):
            JsonlStore(self.output_dir).page("missing__cell", 1)


if __name__ == "__main__":
    unittest.main()
