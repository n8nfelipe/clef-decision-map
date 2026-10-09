import unittest

from app import apply_policy, normalize_clef_result, normalize_traffic_clef_result


class ClefResponseTest(unittest.TestCase):
    def test_normalizes_typed_answers(self):
        decision = normalize_clef_result(
            {
                "model": "clef",
                "answers": {
                    "urgent": {"type": "noul", "noul": 0.9},
                    "department": {
                        "type": "choice",
                        "choice": "billing",
                        "probabilities": {"billing": 0.8, "technical": 0.2},
                        "confidence": 0.8,
                    },
                    "urgency": {
                        "type": "choice",
                        "choice": "alta",
                        "probabilities": {"alta": 0.75, "baixa": 0.25},
                        "confidence": 0.75,
                    },
                    "needs_human": {"type": "noul", "noul": 0.7},
                    "is_spam": {"type": "noul", "noul": 0.01},
                    "severity": {
                        "type": "score",
                        "score": 2.4,
                        "legend": {"0": "Sem impacto"},
                        "probabilities": {"2": 0.8},
                        "confidence": 0.8,
                    },
                },
                "usage": {"input_tokens": 10, "output_tokens": 1},
            }
        )

        self.assertEqual(decision["department"], "billing")
        self.assertEqual(decision["urgency"], "alta")
        self.assertTrue(decision["urgent"])
        self.assertTrue(decision["needs_human"])
        self.assertFalse(decision["is_spam"])
        self.assertEqual(decision["confidence"], 0.7)

    def test_urgent_ticket_is_escalated_by_policy(self):
        policy = apply_policy(
            {
                "department": "technical",
                "urgency": "média",
                "urgent": True,
                "needs_human": False,
                "is_spam": False,
                "confidence": 0.9,
                "reason": "test",
            }
        )

        self.assertEqual(policy["action"], "escalate")
        self.assertTrue(policy["human_review"])

    def test_normalizes_traffic_answers(self):
        decision = normalize_traffic_clef_result(
            {
                "answers": {
                    "criticality": {"type": "score", "score": 2.7},
                    "most_critical_point": {
                        "type": "choice",
                        "choice": "p1",
                        "probabilities": {"p1": 0.9, "none": 0.1},
                        "confidence": 0.9,
                    },
                    "recommended_action": {
                        "type": "choice",
                        "choice": "redirecionar",
                        "probabilities": {"redirecionar": 0.8},
                        "confidence": 0.8,
                    },
                    "prioritize_intervention": {"type": "noul", "noul": 0.85},
                }
            },
            {"p1"},
        )

        self.assertEqual(decision["most_critical_point"], "p1")
        self.assertEqual(decision["recommended_action"], "redirecionar")
        self.assertTrue(decision["prioritize_intervention"])
        self.assertEqual(decision["confidence"], 0.8)


if __name__ == "__main__":
    unittest.main()
