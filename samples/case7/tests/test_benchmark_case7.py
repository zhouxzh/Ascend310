import unittest

from scripts.benchmark_case7 import percentile


class BenchmarkHelpersTests(unittest.TestCase):
    def test_percentile_uses_linear_interpolation(self):
        self.assertEqual(percentile([], 50), None)
        self.assertEqual(percentile([1.0, 2.0, 3.0], 50), 2.0)
        self.assertEqual(percentile([1.0, 2.0, 3.0, 4.0], 95), 3.85)


if __name__ == "__main__":
    unittest.main()
