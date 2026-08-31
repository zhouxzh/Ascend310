from __future__ import annotations

import math
import unittest

import numpy as np

from palmprint_workbench.domain.metrics import rank1_decision, verification_metrics


class VerificationMetricTests(unittest.TestCase):
    def test_eer_uses_an_observed_score_threshold(self):
        # Counterexample: the FAR/FRR crossing lies between 0.6 and 0.4. An
        # interpolated threshold would not be an executable matcher threshold.
        genuine = np.array([0.9, 0.7, 0.6, 0.3])
        impostor = np.array([0.8, 0.4, 0.2])

        metrics = verification_metrics(genuine, impostor)

        self.assertIn(metrics["threshold"], set(np.concatenate([genuine, impostor])))
        self.assertAlmostEqual(metrics["threshold"], 0.6)
        self.assertAlmostEqual(metrics["far_at_threshold"], 1 / 3)
        self.assertAlmostEqual(metrics["frr_at_threshold"], 1 / 4)
        self.assertAlmostEqual(metrics["eer_balance_gap"], 1 / 12)
        self.assertAlmostEqual(metrics["eer"], 7 / 24)

    def test_all_tied_scores_do_not_select_an_infinite_sentinel_threshold(self):
        # Counterexample: both reject-all (+inf) and accept-all (-inf) have
        # the same FAR/FRR gap as the observed score. Calibration must still
        # return a threshold accepted by the matcher.
        metrics = verification_metrics(np.array([0.5]), np.array([0.5]))

        self.assertEqual(metrics["threshold"], 0.5)
        self.assertTrue(math.isfinite(metrics["threshold"]))
        self.assertEqual(metrics["far_at_threshold"], 1.0)
        self.assertEqual(metrics["frr_at_threshold"], 0.0)

    def test_auc_uses_mann_whitney_tie_credit_with_repeated_fpr(self):
        # Counterexample: ungrouped ROC rows repeat FPR at 0.0 and tied 0.8
        # scores. Standard Mann-Whitney AUC gives each positive/negative tie
        # half credit: (4 + 3 + 3 + 0.5) / 16 = 0.65625.
        genuine = np.array([0.9, 0.8, 0.8, 0.1])
        impostor = np.array([0.8, 0.8, 0.2, 0.1])

        metrics = verification_metrics(genuine, impostor)

        self.assertAlmostEqual(metrics["auc"], 0.65625)

    def test_rank1_ties_use_lexicographic_identity_order(self):
        first = {"palm-b": 0.8, "palm-a": 0.8, "palm-c": 0.7}
        second = {"palm-c": 0.7, "palm-a": 0.8, "palm-b": 0.8}

        self.assertEqual(rank1_decision(first), ("palm-a", ("palm-a", "palm-b")))
        self.assertEqual(rank1_decision(second), ("palm-a", ("palm-a", "palm-b")))


if __name__ == "__main__":
    unittest.main()
