import json
import tempfile
import unittest
from pathlib import Path

from scripts import prepare_mobileclip_text_fixtures as fixtures


def _manifest(queries):
    return {"dataset_id": "COCO-CN", "queries": {"en": queries}}


class MobileClipTextFixtureTests(unittest.TestCase):
    def test_loads_exactly_twenty_nonempty_unique_english_queries(self):
        queries = [
            {"query": f"query-{index}", "keywords": [f"keyword-{index}"], "relevant_image_ids": []}
            for index in range(20)
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps(_manifest(queries)), encoding="utf-8")
            result = fixtures._load_queries(path)
        self.assertEqual(len(result), 20)
        self.assertEqual(result[0]["index"], 1)
        self.assertEqual(result[-1]["query"], "query-19")

    def test_rejects_duplicate_or_incomplete_query_sets(self):
        duplicate = [
            {"query": "same", "keywords": ["same"], "relevant_image_ids": []}
            for _ in range(20)
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps(_manifest(duplicate)), encoding="utf-8")
            with self.assertRaisesRegex(fixtures.FixtureError, "duplicated"):
                fixtures._load_queries(path)
            path.write_text(json.dumps(_manifest(duplicate[:19])), encoding="utf-8")
            with self.assertRaisesRegex(fixtures.FixtureError, "exactly 20"):
                fixtures._load_queries(path)

    def test_rejects_the_canonical_production_reference_directory(self):
        with self.assertRaisesRegex(fixtures.FixtureError, "protected"):
            fixtures._ensure_isolated_output(
                fixtures.ROOT / "reports" / "model_pipeline" / "references"
            )


if __name__ == "__main__":
    unittest.main()
