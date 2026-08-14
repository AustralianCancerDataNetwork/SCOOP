import unittest
from pathlib import Path

from scoop.profiles.manifest import (
    get_profile_config,
    list_profiles,
    load_routed_profile,
    load_profile_manifest,
    load_transformation_graph,
)
from scoop.patients.treatment_episode import extract_active_treatment_endpoints


ROOT = Path(__file__).resolve().parents[1]
CONFIG_ROOT = ROOT / "scoop"
BLADDER_DIR = ROOT / "scoop" / "profiles" / "bladder"
BLADDER_TAIL_GRAPHS = [
    "bladder_nmibc.yaml",
    "bladder_mibc_localized.yaml",
    "bladder_advanced_first_line.yaml",
    "bladder_advanced_post_platinum.yaml",
]


class BladderProfileTests(unittest.TestCase):
    def test_profile_discovery_lists_bladder(self):
        self.assertIn("bladder", list_profiles(CONFIG_ROOT))

    def test_manifest_loads(self):
        manifest = load_profile_manifest("bladder", base_dir=CONFIG_ROOT)
        self.assertEqual(manifest.profile_id, "bladder")
        self.assertEqual(manifest.default_graph, "bladder")
        self.assertEqual(manifest.entry_graph, "bladder")
        self.assertIn("bladder_nmibc", manifest.graphs)
        self.assertIn("bladder_mibc_localized", manifest.graphs)
        self.assertIn("bladder_advanced_first_line", manifest.graphs)
        self.assertIn("bladder_advanced_post_platinum", manifest.graphs)
        self.assertEqual(
            manifest.pathways["bladder_advanced_post_platinum"],
            "bladder_advanced_post_platinum",
        )

    def test_get_profile_config_uses_manifest(self):
        profile = get_profile_config("bladder", base_dir=CONFIG_ROOT)
        self.assertEqual(profile.profile_id, "bladder")
        self.assertEqual(profile.profile_name, "bladder")
        self.assertEqual(profile.graph_relpath, Path("profiles/bladder/bladder.yaml"))

    def test_bladder_graph_loads(self):
        graph = load_transformation_graph(str(BLADDER_DIR / "bladder.yaml"))
        self.assertGreater(len(graph.nodes), 0)

    def test_bladder_tail_graphs_load(self):
        for graph_name in BLADDER_TAIL_GRAPHS:
            graph = load_transformation_graph(str(BLADDER_DIR / graph_name))
            self.assertGreater(len(graph.nodes), 0, graph_name)

    @unittest.skip(
        "regimen_registry was replaced by protocol_registry.RegistryYaml (f318297). "
        "Needs porting: RegistryYaml has no .disease/.entries, requires a session "
        "argument, and bladder_regimen_registry.yaml is still in the old "
        "registry/disease/entries format."
    )
    def test_bladder_registry_loads(self):
        registry = load_regimen_registry(BLADDER_DIR / "bladder_regimen_registry.yaml")
        self.assertEqual(registry.disease, "bladder")
        self.assertIn("turbt_intravesical_therapy", registry.entries)
        self.assertIn("trimodality_bladder_preservation", registry.entries)
        self.assertIn("erdafitinib", registry.entries)

    def test_bladder_routed_runtime_loads(self):
        runtime = load_routed_profile("bladder", base_dir=CONFIG_ROOT)
        self.assertEqual(runtime.entry_graph_name, "bladder")
        self.assertIn("bladder_nmibc", runtime.pathway_to_graph)
        self.assertIn("bladder_nmibc", runtime.tail_contexts)

    def test_bladder_entry_graph_routes_to_known_pathways(self):
        graph = load_transformation_graph(str(BLADDER_DIR / "bladder.yaml"))
        allowed = {
            "bladder_nmibc",
            "bladder_mibc_localized",
            "bladder_advanced_first_line",
            "bladder_advanced_post_platinum",
            "bladder_other",
        }

        seen = set()
        for _ in range(200):
            sampled = graph.sample_one()
            self.assertEqual(extract_active_treatment_endpoints(sampled), [])
            seen.add(sampled["condition_episode.pathway"])

        self.assertTrue(seen.issubset(allowed))
        self.assertTrue(seen - {"bladder_other"})
        self.assertNotIn("bladder_other", seen)

    def test_bladder_nmibc_tail_graph(self):
        graph = load_transformation_graph(str(BLADDER_DIR / "bladder_nmibc.yaml"))

        sampled = graph.sample_one(
            initial_state={"condition_episode.nmibc_risk_group": "low_intermediate_risk"}
        )
        self.assertEqual(
            set(extract_active_treatment_endpoints(sampled)),
            {"turbt_intravesical_therapy"},
        )

        sampled = graph.sample_one(
            initial_state={
                "condition_episode.nmibc_risk_group": "bcg_unresponsive",
                "condition_episode.cystectomy_eligibility": "ineligible",
            }
        )
        self.assertEqual(
            set(extract_active_treatment_endpoints(sampled)),
            {"pembrolizumab_bcg_unresponsive"},
        )

    def test_bladder_mibc_localized_tail_graph(self):
        graph = load_transformation_graph(
            str(BLADDER_DIR / "bladder_mibc_localized.yaml")
        )

        sampled = graph.sample_one(
            initial_state={
                "condition_episode.local_therapy_strategy": "trimodality",
            }
        )
        self.assertEqual(
            set(extract_active_treatment_endpoints(sampled)),
            {"trimodality_bladder_preservation"},
        )

        sampled = graph.sample_one(
            initial_state={
                "condition_episode.local_therapy_strategy": "cystectomy",
                "condition_episode.post_cystectomy_high_risk": "no",
                "condition_episode.cisplatin_eligibility": "eligible",
            }
        )
        self.assertEqual(
            set(extract_active_treatment_endpoints(sampled)),
            {"neoadjuvant_cisplatin_radical_cystectomy"},
        )

    def test_bladder_advanced_first_line_tail_graph(self):
        graph = load_transformation_graph(
            str(BLADDER_DIR / "bladder_advanced_first_line.yaml")
        )

        sampled = graph.sample_one(
            initial_state={
                "condition_episode.platinum_eligibility": "ineligible",
            }
        )
        self.assertEqual(
            set(extract_active_treatment_endpoints(sampled)),
            {"best_supportive_care_advanced"},
        )

        sampled = graph.sample_one(
            initial_state={
                "condition_episode.platinum_eligibility": "eligible",
                "condition_episode.cisplatin_eligibility": "eligible",
                "condition_episode.platinum_response": "responder",
                "condition_episode.maintenance_avelumab_eligibility": "eligible",
            }
        )
        self.assertEqual(
            set(extract_active_treatment_endpoints(sampled)),
            {"cisplatin_gemcitabine_avelumab_maintenance"},
        )

    def test_bladder_advanced_post_platinum_tail_graph(self):
        graph = load_transformation_graph(
            str(BLADDER_DIR / "bladder_advanced_post_platinum.yaml")
        )

        sampled = graph.sample_one(
            initial_state={
                "condition_occurrence.fgfr2_3_alteration": "altered",
                "condition_episode.post_platinum_choice_fgfr_altered": "erdafitinib",
            }
        )
        self.assertEqual(
            set(extract_active_treatment_endpoints(sampled)),
            {"erdafitinib"},
        )

        sampled = graph.sample_one(
            initial_state={
                "condition_occurrence.fgfr2_3_alteration": "wildtype",
                "condition_episode.post_platinum_choice_standard": "enfortumab_vedotin",
            }
        )
        self.assertEqual(
            set(extract_active_treatment_endpoints(sampled)),
            {"enfortumab_vedotin_wildtype"},
        )


if __name__ == "__main__":
    unittest.main()
