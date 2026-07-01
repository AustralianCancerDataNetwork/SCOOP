import random
import unittest

from scoop.configuration.transformation_graph import TransformationGraph


class TransformationGraphDependencyTests(unittest.TestCase):
    def test_active_when_dependencies_affect_sampling_order(self):
        graph = TransformationGraph.model_validate(
            {
                "nodes": {
                    "person.flag": {
                        "type": "categorical",
                        "distribution": {"on": 1.0},
                    },
                    "treatment_episode.some_endpoint": {
                        "type": "categorical",
                        "labels": ["endpoint"],
                        "active_when": {"person.flag": "on"},
                    },
                },
                "edges": [],
            }
        )

        self.assertLess(
            graph.order.index("person.flag"),
            graph.order.index("treatment_episode.some_endpoint"),
        )
        sampled = graph.sample_one()
        self.assertEqual(sampled["person.flag"], "on")
        self.assertEqual(sampled["treatment_episode.some_endpoint"], "endpoint")

    def test_branch_rule_dependencies_affect_sampling_order(self):
        graph = TransformationGraph.model_validate(
            {
                "nodes": {
                    "person.performance_status": {
                        "type": "categorical",
                        "distribution": {"ps0": 1.0},
                    },
                    "condition_episode.chemo_eligible": {
                        "type": "branch",
                        "rules": [
                            {
                                "if": {"person.performance_status": "ps0"},
                                "then": "eligible",
                            }
                        ],
                        "default": "ineligible",
                    },
                },
                "edges": [],
            }
        )

        self.assertLess(
            graph.order.index("person.performance_status"),
            graph.order.index("condition_episode.chemo_eligible"),
        )
        sampled = graph.sample_one()
        self.assertEqual(sampled["condition_episode.chemo_eligible"], "eligible")

    def test_implicit_dependency_cycles_are_rejected(self):
        with self.assertRaises(ValueError):
            TransformationGraph.model_validate(
                {
                    "nodes": {
                        "a": {
                            "type": "categorical",
                            "distribution": {"x": 1.0},
                            "active_when": {"b": "y"},
                        },
                        "b": {
                            "type": "categorical",
                            "distribution": {"y": 1.0},
                            "active_when": {"a": "x"},
                        },
                    },
                    "edges": [],
                }
            )

    def test_branch_node_tracks_rule_inputs(self):
        graph = TransformationGraph.model_validate(
            {
                "nodes": {
                    "a": {"type": "categorical", "distribution": {"x": 1.0}},
                    "b": {"type": "categorical", "distribution": {"y": 1.0}},
                    "route": {
                        "type": "branch",
                        "rules": [
                            {"if": {"a": "x", "b": "y"}, "then": "go"},
                        ],
                        "default": "stop",
                    },
                },
                "edges": [],
            }
        )

        self.assertEqual(graph.nodes["route"].from_, ["a", "b"])

    def test_sample_one_uses_inherited_initial_state(self):
        graph = TransformationGraph.model_validate(
            {
                "nodes": {
                    "condition_occurrence.biomarker": {
                        "type": "categorical",
                        "distribution": {"egfr": 1.0},
                    },
                    "treatment_episode.egfr_intervention": {
                        "type": "categorical",
                        "labels": ["egfr_int"],
                        "active_when": {"condition_occurrence.biomarker": "egfr"},
                    },
                },
                "edges": [],
            }
        )

        sampled = graph.sample_one(
            initial_state={"condition_occurrence.biomarker": "egfr"}
        )

        self.assertEqual(sampled["condition_occurrence.biomarker"], "egfr")
        self.assertEqual(sampled["treatment_episode.egfr_intervention"], "egfr_int")


if __name__ == "__main__":
    unittest.main()
