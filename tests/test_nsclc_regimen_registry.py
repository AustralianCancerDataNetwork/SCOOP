import random
import re
import unittest
from pathlib import Path

from scoop.configuration.config_loader import (
    get_profile_config,
    load_profile,
    load_profile_manifest,
    load_routed_profile,
    load_transformation_graph,
)
from scoop.configuration.protocol_registry import load_protocol_registry
from scoop.schema.treatment_episode import (
    build_graph_outcome,
    extract_active_treatment_endpoints,
)


ROOT = Path(__file__).resolve().parents[1]
CONFIG_ROOT = ROOT / "scoop" / "configuration"
PROFILE_DIR = CONFIG_ROOT / "profiles" / "nsclc"
REGISTRY_PATH = PROFILE_DIR / "nsclc_protocol_registry.yaml"
STAGE_SPECIFIC_GRAPHS = [
    "nsclc_stg1.yaml",
    "nsclc_stg23_resectable.yaml",
    "nsclc_stg3_unresectable.yaml",
    "nsclc_stg4_driver.yaml",
    "nsclc_stg4_nodrivers.yaml",
]


class NSCLCProtocolRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_protocol_registry(REGISTRY_PATH)

    def test_protocol_registry_loads_current_yaml(self):
        self.assertEqual(self.registry.label, "nsclc")
        self.assertEqual(self.registry.version, 1)
        self.assertEqual(
            self.registry.default_conditions,
            ["Lung cancer", "Non-small cell lung cancer"],
        )
        self.assertGreater(len(self.registry.source_phases), 0)
        self.assertGreater(len(self.registry.protocols), 0)
        self.assertGreater(len(self.registry.entries), 0)

    def test_profile_manifest_uses_canonical_protocol_registry(self):
        manifest = load_profile_manifest("nsclc", base_dir=CONFIG_ROOT)
        self.assertEqual(manifest.protocol_registry, Path("nsclc_protocol_registry.yaml"))
        self.assertIsNone(manifest.legacy_regimen_registry)

        profile = get_profile_config("nsclc", base_dir=CONFIG_ROOT)
        self.assertEqual(
            profile.protocol_registry_relpath,
            Path("profiles/nsclc/nsclc_protocol_registry.yaml"),
        )

    def test_registry_entry_resolvers_are_structurally_valid(self):
        valid_types = {"component_query", "bundle_recipe", "composite", "none"}
        for entry_id, entry in self.registry.entries.items():
            self.assertIsNotNone(entry.resolver, entry_id)
            self.assertIn(entry.resolver.type, valid_types, entry_id)

            if entry.resolver.type == "composite":
                for member in entry.resolver.members:
                    self.assertIn(member, self.registry.entries, f"{entry_id} -> {member}")

    def test_registry_phase_references_are_loaded(self):
        source_phase_ids = set(self.registry.source_phases)
        for protocol_id, protocol in self.registry.protocols.items():
            self.assertTrue(
                set(protocol.source_phase_ids).issubset(source_phase_ids),
                protocol_id,
            )
            for optional in (protocol.optional_phases or {}).values():
                criteria = optional.get("criteria")
                if criteria is not None:
                    self.assertIn(criteria.as_phase, protocol.source_phase_ids)

        for union_id, union in self.registry.union_phases.items():
            self.assertTrue(set(union.source_phase_ids).issubset(source_phase_ids), union_id)

    def test_profile_loader_attaches_protocol_registry(self):
        context = load_profile("nsclc", base_dir=CONFIG_ROOT)
        self.assertIsNotNone(context.protocol_registry)
        self.assertEqual(context.protocol_registry.label, "nsclc")

    def test_routed_runtime_loads_for_nsclc(self):
        runtime = load_routed_profile("nsclc", base_dir=CONFIG_ROOT)
        self.assertEqual(runtime.entry_graph_name, "nsclc")
        self.assertIn("nsclc_stage4_driver", runtime.pathway_to_graph)
        self.assertIn("nsclc_stg4_driver", runtime.tail_contexts)
        self.assertIsNotNone(runtime.entry_context.protocol_registry)

    def test_graph_endpoints_are_covered_by_protocol_entries(self):
        pattern = re.compile(r"^\s+treatment_episode\.([A-Za-z0-9_]+):", re.MULTILINE)
        covered = set(self.registry.entries)
        for graph_name in STAGE_SPECIFIC_GRAPHS:
            text = (PROFILE_DIR / graph_name).read_text(encoding="utf-8")
            endpoints = set(pattern.findall(text))
            self.assertTrue(
                endpoints - {"resection_margin"} <= covered,
                f"{graph_name}: {sorted(endpoints - covered)}",
            )

    def test_stage_specific_graphs_load(self):
        for graph_name in STAGE_SPECIFIC_GRAPHS:
            graph = load_transformation_graph(str(PROFILE_DIR / graph_name))
            self.assertGreater(len(graph.nodes), 0, graph_name)

    def test_combined_nsclc_graph_routes_to_known_pathways(self):
        graph = load_transformation_graph(str(PROFILE_DIR / "nsclc.yaml"))
        allowed = {
            "nsclc_stage1",
            "nsclc_stage23_resectable",
            "nsclc_stage3_unresectable",
            "nsclc_stage4_driver",
            "nsclc_stage4_nodrivers",
            "nsclc_other",
        }

        random.seed(17)
        seen = {graph.sample_one()["condition_episode.pathway"] for _ in range(500)}
        self.assertTrue(seen.issubset(allowed))
        self.assertTrue(seen - {"nsclc_other"})
        self.assertNotIn("nsclc_other", seen)

    def test_stage4_driver_tail_graph_produces_declared_endpoints(self):
        graph = load_transformation_graph(str(PROFILE_DIR / "nsclc_stg4_driver.yaml"))
        biomarker_to_endpoint = {
            "egfr": "egfr_intervention",
            "alk": "alk_intervention",
            "ros1": "ros1_intervention",
            "braf": "braf_intervention",
            "ret": "ret_intervention",
            "erbb2": "erbb2_intervention",
            "ntrk": "ntrk_intervention",
            "met": "met_intervention",
        }

        for biomarker, expected_endpoint in biomarker_to_endpoint.items():
            sampled = graph.sample_one(
                initial_state={"condition_occurrence.biomarker": biomarker}
            )
            self.assertEqual(
                set(extract_active_treatment_endpoints(sampled)),
                {expected_endpoint},
                biomarker,
            )

    def test_stage23_resectable_tail_graph_produces_declared_endpoints(self):
        graph = load_transformation_graph(
            str(PROFILE_DIR / "nsclc_stg23_resectable.yaml")
        )
        cases = [
            (
                "neoadjuvant_or_perioperative_ici_w_chemo",
                {
                    "person.chemo_ici_eligible": "eligible",
                    "condition_occurrence.biomarker": "none",
                    "condition_occurrence.pdl1_binary": "present",
                    "person.performance_status": "ps0",
                },
            ),
            (
                "adjuvant_pembrolizumab",
                {
                    "person.chemo_ici_eligible": "ineligible",
                    "condition_occurrence.pdl1_binary": "absent",
                    "condition_occurrence.biomarker": "none",
                    "person.performance_status": "ps1",
                },
            ),
            (
                "adjuvant_osimertinib",
                {
                    "condition_occurrence.biomarker": "egfr",
                    "person.performance_status": "ps3",
                    "person.chemo_ici_eligible": "eligible",
                },
            ),
            (
                "adjuvant_optchemo_alectinib",
                {
                    "condition_occurrence.biomarker": "alk",
                    "person.performance_status": "ps1",
                    "person.chemo_ici_eligible": "eligible",
                },
            ),
        ]

        for expected_endpoint, initial_state in cases:
            sampled = graph.sample_one(initial_state=initial_state)
            self.assertEqual(
                set(extract_active_treatment_endpoints(sampled)),
                {expected_endpoint},
                expected_endpoint,
            )

    def test_stage3_unresectable_tail_graph_produces_declared_endpoints(self):
        graph = load_transformation_graph(
            str(PROFILE_DIR / "nsclc_stg3_unresectable.yaml")
        )
        cases = [
            (
                "radiotherapy",
                {
                    "person.performance_status_v_frail": "v_frail",
                    "person.performance_status": "ps3",
                    "condition_occurrence.pdl1_binary": "absent",
                    "condition_occurrence.biomarker": "none",
                },
            ),
            (
                "sequential_crt",
                {
                    "person.performance_status_v_frail": "not_v_frail",
                    "person.performance_status": "ps2",
                    "condition_occurrence.pdl1_binary": "absent",
                    "condition_occurrence.biomarker": "none",
                },
            ),
            (
                "concurrent_crt",
                {
                    "person.performance_status_v_frail": "not_v_frail",
                    "person.performance_status": "ps1",
                    "condition_occurrence.pdl1_binary": "absent",
                    "condition_occurrence.biomarker": "none",
                },
            ),
        ]

        for expected_endpoint, initial_state in cases:
            sampled = graph.sample_one(initial_state=initial_state)
            self.assertEqual(
                set(extract_active_treatment_endpoints(sampled)),
                {expected_endpoint},
                expected_endpoint,
            )

    def test_stage1_tail_graph_produces_declared_endpoint_patterns(self):
        graph = load_transformation_graph(str(PROFILE_DIR / "nsclc_stg1.yaml"))
        cases = [
            (
                {"sbrt"},
                {
                    "person.performance_status_v_frail": "v_frail",
                    "condition_occurrence.biomarker": "none",
                },
            ),
            (
                {"surgery_only"},
                {
                    "person.performance_status_v_frail": "not_v_frail",
                    "condition_occurrence.biomarker": "none",
                    "treatment_episode.resection_margin": "r0",
                },
            ),
            (
                {"port_adjuvant_osimertinib"},
                {
                    "person.performance_status_v_frail": "not_v_frail",
                    "condition_occurrence.biomarker": "egfr",
                    "treatment_episode.resection_margin": "r1",
                },
            ),
        ]

        for expected_endpoints, initial_state in cases:
            sampled = graph.sample_one(initial_state=initial_state)
            self.assertEqual(
                set(extract_active_treatment_endpoints(sampled)),
                expected_endpoints,
                str(expected_endpoints),
            )

    def test_registry_metadata_is_available_for_graph_outcomes(self):
        context = load_profile(
            "nsclc",
            "nsclc_stg4_driver",
            base_dir=CONFIG_ROOT,
        )
        outcome = build_graph_outcome(
            {"treatment_episode.egfr_intervention": "egfr_intervention"},
            registry=context.protocol_registry,
            source_profile="nsclc_stg4_driver",
        )

        self.assertEqual(outcome.primary_category_id, "egfr_intervention")
        self.assertEqual(outcome.resolver_type, "component_query")
        self.assertEqual(outcome.source_profile, "nsclc_stg4_driver")


if __name__ == "__main__":
    unittest.main()
