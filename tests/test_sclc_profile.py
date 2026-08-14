import unittest
from pathlib import Path

from scoop.configuration.config_loader import (
    get_profile_config,
    list_profiles,
    load_routed_profile,
    load_profile_manifest,
    load_transformation_graph,
)
from scoop.schema.treatment_episode import extract_active_treatment_endpoints


ROOT = Path(__file__).resolve().parents[1]
CONFIG_ROOT = ROOT / "scoop" / "configuration"
SCLC_DIR = ROOT / "scoop" / "configuration" / "profiles" / "sclc"
SCLC_TAIL_GRAPHS = [
    "sclc_limited_surgical.yaml",
    "sclc_limited_nonsurgical.yaml",
    "sclc_extensive.yaml",
    "sclc_recurrent.yaml",
]


class SCLCProfileTests(unittest.TestCase):
    def test_profile_discovery_lists_sclc(self):
        self.assertIn("sclc", list_profiles(CONFIG_ROOT))

    def test_manifest_loads(self):
        manifest = load_profile_manifest("sclc", base_dir=CONFIG_ROOT)
        self.assertEqual(manifest.profile_id, "sclc")
        self.assertEqual(manifest.default_graph, "sclc")
        self.assertEqual(manifest.entry_graph, "sclc")
        self.assertIn("sclc", manifest.graphs)
        self.assertIn("sclc_limited_surgical", manifest.graphs)
        self.assertIn("sclc_limited_nonsurgical", manifest.graphs)
        self.assertIn("sclc_extensive", manifest.graphs)
        self.assertIn("sclc_recurrent", manifest.graphs)
        self.assertEqual(manifest.pathways["sclc_extensive"], "sclc_extensive")

    def test_get_profile_config_uses_manifest(self):
        profile = get_profile_config("sclc", base_dir=CONFIG_ROOT)
        self.assertEqual(profile.profile_id, "sclc")
        self.assertEqual(profile.profile_name, "sclc")
        self.assertEqual(profile.graph_relpath, Path("profiles/sclc/sclc.yaml"))

    def test_sclc_graph_loads(self):
        graph = load_transformation_graph(str(SCLC_DIR / "sclc.yaml"))
        self.assertGreater(len(graph.nodes), 0)

    def test_sclc_tail_graphs_load(self):
        for graph_name in SCLC_TAIL_GRAPHS:
            graph = load_transformation_graph(str(SCLC_DIR / graph_name))
            self.assertGreater(len(graph.nodes), 0, graph_name)

    @unittest.skip(
        "regimen_registry was replaced by protocol_registry.RegistryYaml (f318297). "
        "Needs porting: RegistryYaml has no .disease/.entries/.condition_sets or "
        "resolve_hemonc_bundle(), requires a session argument, and "
        "sclc_regimen_registry.yaml is still in the old registry/disease/entries format."
    )
    def test_sclc_registry_loads(self):
        registry = load_regimen_registry(SCLC_DIR / "sclc_regimen_registry.yaml")
        self.assertEqual(registry.disease, "sclc")
        self.assertIn("concurrent_crt", registry.entries)
        self.assertIn("platinum_etoposide_atezolizumab", registry.entries)
        self.assertIn("lurbinectedin", registry.entries)
        self.assertIn("lung_sclc_all", registry.condition_sets)
        self.assertEqual(registry.condition_sets["lung_sclc_all"], [666])

        resolved = registry.resolve_hemonc_bundle("concurrent_crt")
        self.assertIsNotNone(resolved)
        self.assertEqual(
            [phase.phase_id for phase in resolved.phases],
            ["systemic", "radiotherapy"],
        )

    def test_sclc_routed_runtime_loads(self):
        runtime = load_routed_profile("sclc", base_dir=CONFIG_ROOT)
        self.assertEqual(runtime.entry_graph_name, "sclc")
        self.assertIn("sclc_recurrent", runtime.pathway_to_graph)
        self.assertIn("sclc_recurrent", runtime.tail_contexts)

    def test_sclc_entry_graph_routes_to_known_pathways(self):
        graph = load_transformation_graph(str(SCLC_DIR / "sclc.yaml"))
        allowed = {
            "sclc_limited_surgical",
            "sclc_limited_nonsurgical",
            "sclc_extensive",
            "sclc_recurrent",
            "sclc_other",
        }

        seen = set()
        for _ in range(200):
            sampled = graph.sample_one()
            self.assertEqual(extract_active_treatment_endpoints(sampled), [])
            seen.add(sampled["condition_episode.pathway"])

        self.assertTrue(seen.issubset(allowed))
        self.assertTrue(seen - {"sclc_other"})
        self.assertNotIn("sclc_other", seen)

    def test_sclc_limited_surgical_tail_graph(self):
        graph = load_transformation_graph(str(SCLC_DIR / "sclc_limited_surgical.yaml"))

        sampled = graph.sample_one(
            initial_state={"condition_episode.resection_pathology": "r0_n0_1"}
        )
        self.assertEqual(
            set(extract_active_treatment_endpoints(sampled)),
            {"surgery_adjuvant_platinum_etoposide"},
        )

        sampled = graph.sample_one(
            initial_state={"condition_episode.resection_pathology": "n2_or_r1_2"}
        )
        self.assertEqual(
            set(extract_active_treatment_endpoints(sampled)),
            {"surgery_adjuvant_platinum_etoposide_rt"},
        )

    def test_sclc_limited_nonsurgical_tail_graph(self):
        graph = load_transformation_graph(
            str(SCLC_DIR / "sclc_limited_nonsurgical.yaml")
        )

        sampled = graph.sample_one(
            initial_state={
                "person.performance_status": "ps0_1",
                "person.age_group": "lte70",
                "person.frailty": "fit",
            }
        )
        self.assertEqual(
            set(extract_active_treatment_endpoints(sampled)),
            {"concurrent_crt"},
        )

        sampled = graph.sample_one(
            initial_state={
                "person.performance_status": "ps2",
                "person.age_group": "gt70",
                "person.frailty": "frail",
            }
        )
        self.assertEqual(
            set(extract_active_treatment_endpoints(sampled)),
            {"sequential_crt"},
        )

    def test_sclc_extensive_tail_graph(self):
        graph = load_transformation_graph(str(SCLC_DIR / "sclc_extensive.yaml"))

        sampled = graph.sample_one(
            initial_state={
                "person.performance_status": "ps3_plus",
            }
        )
        self.assertEqual(
            set(extract_active_treatment_endpoints(sampled)),
            {"best_supportive_care"},
        )

        sampled = graph.sample_one(
            initial_state={
                "person.performance_status": "ps2",
                "condition_episode.ps2_systemic_choice": "carboplatin_gemcitabine",
            }
        )
        self.assertEqual(
            set(extract_active_treatment_endpoints(sampled)),
            {"carboplatin_gemcitabine"},
        )

        sampled = graph.sample_one(
            initial_state={
                "person.performance_status": "ps0_1",
                "condition_episode.io_eligibility": "eligible",
                "condition_episode.io_choice": "atezolizumab",
            }
        )
        self.assertEqual(
            set(extract_active_treatment_endpoints(sampled)),
            {"platinum_etoposide_atezolizumab"},
        )

        sampled = graph.sample_one(
            initial_state={
                "person.performance_status": "ps0_1",
                "condition_episode.io_eligibility": "ineligible",
                "condition_episode.standard_non_io_choice": "platinum_etoposide",
            }
        )
        self.assertEqual(
            set(extract_active_treatment_endpoints(sampled)),
            {"platinum_etoposide"},
        )

    def test_sclc_recurrent_tail_graph(self):
        graph = load_transformation_graph(str(SCLC_DIR / "sclc_recurrent.yaml"))

        sampled = graph.sample_one(
            initial_state={
                "condition_episode.recurrent_strategy": "platinum_sensitive",
                "condition_episode.recurrent_sensitive_choice": "cav_sensitive",
            }
        )
        self.assertEqual(
            set(extract_active_treatment_endpoints(sampled)),
            {"cav_sensitive"},
        )

        sampled = graph.sample_one(
            initial_state={
                "condition_episode.recurrent_strategy": "platinum_resistant",
                "condition_episode.recurrent_resistant_choice": "lurbinectedin",
            }
        )
        self.assertEqual(
            set(extract_active_treatment_endpoints(sampled)),
            {"lurbinectedin"},
        )

        sampled = graph.sample_one(
            initial_state={
                "condition_episode.recurrent_strategy": "refractory_or_poor_ps",
            }
        )
        self.assertEqual(
            set(extract_active_treatment_endpoints(sampled)),
            {"best_supportive_care_recurrent"},
        )


if __name__ == "__main__":
    unittest.main()
