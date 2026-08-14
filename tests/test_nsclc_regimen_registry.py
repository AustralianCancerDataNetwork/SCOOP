import re
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError
import yaml

from scoop.profiles.manifest import (
    list_profiles,
    load_routed_profile,
    load_profile_manifest,
    load_transformation_graph,
)
from scoop.protocols.spec import (
    DEFAULT_CATEGORY_RESTRICTIONS,
    EXCLUDE_RESTRICTION,
    CategoryRestriction,
    ComponentRequirements,
    ProtocolYaml,
    RegistryYaml,
    SourcePhaseYaml,
    apply_default_component_requirements,
)
from scoop.patients.treatment_episode import extract_active_treatment_endpoints


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "scoop" / "profiles" / "nsclc" / "nsclc_protocol_registry.yaml"
GRAPH_DIR = ROOT / "scoop" / "profiles" / "nsclc"
STAGE_SPECIFIC_GRAPHS = [
    "nsclc_stg1.yaml",
    "nsclc_stg23_resectable.yaml",
    "nsclc_stg3_unresectable.yaml",
    "nsclc_stg4_nodrivers.yaml",
    "nsclc_stg4_driver.yaml",
]
NON_REGISTRY_TREATMENT_STATE_NODES = {"resection_margin"}

# The registry resolves condition names to HemOnc CUIs through a live session.
# These tests stub that lookup so registry parsing can be exercised without a
# database; the values are arbitrary sentinels, not real CUIs.
CUI_LOOKUP = "scoop.protocols.spec.condition_cuis_by_names"
STUB_CONDITION_CUIS = [900001, 900002]
STUB_SESSION = object()


class NSCLCRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with REGISTRY_PATH.open("r", encoding="utf-8") as handle:
            cls.raw = yaml.safe_load(handle)
        cls.registry = cls.raw["nsclc"]
        # `entries` is a legacy block the new RegistryYaml does not parse. It is
        # still read directly from YAML by the graph-coverage test below.
        cls.entries = cls.registry["entries"]
        with patch(CUI_LOOKUP, return_value=list(STUB_CONDITION_CUIS)) as cui_lookup:
            cls.loaded_registry = RegistryYaml.from_yaml_path(REGISTRY_PATH, STUB_SESSION)
        cls.cui_lookup = cui_lookup

    # --- registry loading ---------------------------------------------------

    def test_registry_yaml_loads(self):
        self.assertEqual(self.loaded_registry.label, "nsclc")
        self.assertEqual(self.loaded_registry.version, self.registry["version"])

    def test_registry_populates_every_section(self):
        registry = self.loaded_registry
        self.assertEqual(set(registry.source_phases), set(self.registry["source_phases"]))
        self.assertEqual(set(registry.union_phases), set(self.registry["phase_combinations"]))
        self.assertEqual(set(registry.protocols), set(self.registry["protocols"]))

    def test_default_conditions_resolved_through_session(self):
        self.assertEqual(self.loaded_registry.default_conditions, STUB_CONDITION_CUIS)
        self.cui_lookup.assert_called()

    def test_source_phases_are_labelled_from_yaml_keys(self):
        for phase_id, phase in self.loaded_registry.source_phases.items():
            self.assertEqual(phase.source_phase_id, phase_id)

    def test_source_phases_inherit_default_conditions_when_omitted(self):
        for phase_id, phase in self.loaded_registry.source_phases.items():
            if "conditions" not in (self.registry["source_phases"][phase_id] or {}):
                self.assertEqual(phase.condition_cuis, STUB_CONDITION_CUIS, phase_id)

    def test_protocols_reference_known_source_phases(self):
        known = set(self.loaded_registry.source_phases)
        for protocol_id, protocol in self.loaded_registry.protocols.items():
            self.assertEqual(protocol.protocol_id, protocol_id)
            for phase_id in protocol.source_phase_ids:
                self.assertIn(phase_id, known, f"{protocol_id} -> {phase_id}")

    def test_union_phases_reference_known_source_phases(self):
        known = set(self.loaded_registry.source_phases)
        for union_id, union in self.loaded_registry.union_phases.items():
            self.assertEqual(union.phase_id, union_id)
            for phase_id in union.source_phase_ids:
                self.assertIn(phase_id, known, f"{union_id} -> {phase_id}")

    def test_from_yaml_path_is_not_hardcoded_to_nsclc(self):
        """Registry parsing keys off the top-level label, not a fixed profile."""
        relabelled = {"bladder": self.raw["nsclc"]}
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "bladder_protocol_registry.yaml"
            target.write_text(yaml.safe_dump(relabelled), encoding="utf-8")
            with patch(CUI_LOOKUP, return_value=list(STUB_CONDITION_CUIS)):
                registry = RegistryYaml.from_yaml_path(target, STUB_SESSION)
        self.assertEqual(registry.label, "bladder")
        self.assertEqual(set(registry.protocols), set(self.loaded_registry.protocols))

    # --- condition resolution -----------------------------------------------

    def test_condition_names_are_resolved_through_the_session(self):
        with patch(CUI_LOOKUP, return_value=[7]) as lookup:
            phase = SourcePhaseYaml.generate(
                {"conditions": "Lung cancer"}, "phase", [], STUB_SESSION
            )
        lookup.assert_called_once_with(["Lung cancer"], STUB_SESSION)
        self.assertEqual(phase.condition_cuis, [7])

    def test_condition_cuis_pass_through_without_a_session(self):
        phase = SourcePhaseYaml.generate({"conditions": [11, 12]}, "phase", [], None)
        self.assertEqual(phase.condition_cuis, [11, 12])

    def test_condition_names_without_a_session_fail_validation(self):
        with self.assertRaises(ValidationError):
            SourcePhaseYaml.generate({"conditions": ["Lung cancer"]}, "phase", [], None)

    # --- component requirement defaults --------------------------------------

    def test_omitted_category_restrictions_use_neutral_anchor(self):
        requirements = apply_default_component_requirements(None)
        self.assertEqual(
            requirements.category_restrictions,
            {"chemotherapy": CategoryRestriction(type="min", amount=0)},
        )
        self.assertEqual(requirements.component_lists, [])

    def test_provided_category_restrictions_overlay_full_defaults(self):
        requirements = apply_default_component_requirements(
            {"category_restrictions": {"surgery": {"type": "min", "amount": 1}}}
        )
        self.assertEqual(
            requirements.category_restrictions["surgery"],
            CategoryRestriction(type="min", amount=1),
        )
        for category in DEFAULT_CATEGORY_RESTRICTIONS:
            if category != "surgery":
                self.assertEqual(
                    requirements.category_restrictions[category], EXCLUDE_RESTRICTION, category
                )

    def test_supportive_is_never_excluded_by_default(self):
        self.assertNotIn("supportive", DEFAULT_CATEGORY_RESTRICTIONS)
        requirements = apply_default_component_requirements(
            {"category_restrictions": {"surgery": {"type": "min", "amount": 1}}}
        )
        self.assertNotIn("supportive", requirements.category_restrictions)

    def test_component_lists_survive_default_application(self):
        requirements = apply_default_component_requirements(
            {"component_lists": [["cisplatin", "etoposide"]]}
        )
        self.assertEqual(requirements.component_lists, [["cisplatin", "etoposide"]])

    # --- categorisation ------------------------------------------------------

    def test_categorise_reports_only_non_excluded_categories(self):
        self.assertEqual(
            self.loaded_registry.source_phases["surgery_only"].categorise(), {"surgery"}
        )

    def test_categorise_returns_none_without_category_restrictions(self):
        phase = SourcePhaseYaml.model_validate(
            {
                "study_restriction": None,
                "type": None,
                "component_requirements": ComponentRequirements(),
            }
        )
        self.assertIsNone(phase.categorise())

    # --- input coercion and validation ---------------------------------------

    def test_component_lists_rescue_flat_list_and_scalar(self):
        self.assertEqual(
            ComponentRequirements.model_validate(
                {"component_lists": ["carboplatin"]}
            ).component_lists,
            [["carboplatin"]],
        )
        self.assertEqual(
            ComponentRequirements.model_validate(
                {"component_lists": "carboplatin"}
            ).component_lists,
            [["carboplatin"]],
        )

    def test_regimen_list_rescues_scalar_input(self):
        phase = SourcePhaseYaml.model_validate(
            {"study_restriction": None, "type": None, "regimens": "pembrolizumab"}
        )
        self.assertEqual(phase.regimens, ["pembrolizumab"])

    def test_source_phase_rejects_unknown_fields(self):
        with self.assertRaises(ValidationError):
            SourcePhaseYaml.model_validate(
                {"study_restriction": None, "type": None, "unexpected": True}
            )

    def test_component_requirements_reject_unknown_fields(self):
        with self.assertRaises(ValidationError):
            ComponentRequirements.model_validate({"not_a_field": 1})

    def test_invalid_category_restriction_key_is_rejected(self):
        with self.assertRaises(ValidationError):
            ComponentRequirements.model_validate(
                {"category_restrictions": {"not_a_category": {"type": "min", "amount": 1}}}
            )

    def test_invalid_category_restriction_type_is_rejected(self):
        with self.assertRaises(ValidationError):
            CategoryRestriction.model_validate({"type": "at_most", "amount": 1})

    def test_protocol_generate_reads_phases_and_optional_phases(self):
        protocol = ProtocolYaml.generate("port_then_osi", {"phases": ["surgery_only", "port"]})
        self.assertEqual(protocol.protocol_id, "port_then_osi")
        self.assertEqual(protocol.source_phase_ids, ["surgery_only", "port"])
        self.assertIsNone(protocol.optional_phases)

    # --- profile wiring -------------------------------------------------------

    def test_profile_manifest_loads(self):
        manifest = load_profile_manifest("nsclc", base_dir=ROOT / "scoop")
        self.assertEqual(manifest.profile_id, "nsclc")
        self.assertIn("nsclc_stg4_driver", manifest.graphs)
        self.assertEqual(manifest.entry_graph, "nsclc")
        self.assertEqual(
            manifest.pathways["nsclc_stage4_nodrivers"],
            "nsclc_stg4_nodrivers",
        )
        self.assertIn("nsclc", list_profiles(ROOT / "scoop"))

    def test_routed_runtime_loads_for_nsclc(self):
        runtime = load_routed_profile("nsclc", base_dir=ROOT / "scoop")
        self.assertEqual(runtime.entry_graph_name, "nsclc")
        self.assertIn("nsclc_stage1", runtime.pathway_to_graph)
        self.assertIn("nsclc_stg1", runtime.tail_contexts)

    def test_stage_specific_graph_endpoints_are_covered(self):
        pattern = re.compile(r"^\s+treatment_episode\.([A-Za-z0-9_]+):", re.MULTILINE)
        covered = set(self.entries)
        for graph_name in STAGE_SPECIFIC_GRAPHS:
            text = (GRAPH_DIR / graph_name).read_text(encoding="utf-8")
            endpoints = set(pattern.findall(text))
            for endpoint in endpoints - NON_REGISTRY_TREATMENT_STATE_NODES:
                self.assertIn(endpoint, covered, f"{graph_name}: {endpoint}")

    def test_stage_specific_graphs_load_into_transformation_graphs(self):
        for graph_name in STAGE_SPECIFIC_GRAPHS:
            graph = load_transformation_graph(str(GRAPH_DIR / graph_name))
            self.assertGreater(len(graph.nodes), 0, graph_name)

    def test_combined_nsclc_graph_can_sample(self):
        graph = load_transformation_graph(str(GRAPH_DIR / "nsclc.yaml"))
        for _ in range(10):
            sampled = graph.sample_one()
            self.assertIsInstance(sampled, dict)
            self.assertIn("condition_occurrence.stage_7th", sampled)
            self.assertIn("condition_occurrence.pdl1_binary_50", sampled)
            self.assertIn("person.performance_status_frail", sampled)
            self.assertIn("person.performance_status_v_frail", sampled)
            self.assertIn("condition_episode.group_stage", sampled)
            self.assertIn("condition_episode.pathway", sampled)
            self.assertEqual(extract_active_treatment_endpoints(sampled), [])

    def test_combined_nsclc_entry_graph_routes_to_known_pathways(self):
        graph = load_transformation_graph(str(GRAPH_DIR / "nsclc.yaml"))
        allowed = {
            "nsclc_stage1",
            "nsclc_stage23_resectable",
            "nsclc_stage3_unresectable",
            "nsclc_stage4_driver",
            "nsclc_stage4_nodrivers",
            "nsclc_other",
        }

        random.seed(17)
        seen: set[str] = set()
        for _ in range(500):
            sampled = graph.sample_one()
            seen.add(sampled["condition_episode.pathway"])

        self.assertTrue(seen.issubset(allowed))
        self.assertTrue(seen - {"nsclc_other"})

    def test_combined_nsclc_entry_graph_no_longer_emits_stage_0_or_nsclc_other(self):
        graph = load_transformation_graph(str(GRAPH_DIR / "nsclc.yaml"))

        random.seed(17)
        seen_stages: set[str] = set()
        seen_pathways: set[str] = set()
        for _ in range(500):
            sampled = graph.sample_one()
            seen_stages.add(sampled["condition_occurrence.cancer_stage"])
            seen_pathways.add(sampled["condition_episode.pathway"])

        self.assertNotIn("stage_0", seen_stages)
        self.assertNotIn("nsclc_other", seen_pathways)

    def test_combined_nsclc_entry_graph_limits_non_metastatic_biomarkers(self):
        graph = load_transformation_graph(str(GRAPH_DIR / "nsclc.yaml"))

        random.seed(23)
        allowed_non_metastatic = {"egfr", "alk", "none"}
        for _ in range(1000):
            sampled = graph.sample_one()
            if sampled["condition_episode.group_stage"] == "metastatic":
                continue
            self.assertIn(
                sampled["condition_occurrence.biomarker"],
                allowed_non_metastatic,
            )

    def test_stage4_driver_tail_graph_produces_endpoints_with_inherited_state(self):
        graph = load_transformation_graph(str(GRAPH_DIR / "nsclc_stg4_driver.yaml"))
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

    def test_stage23_resectable_tail_graph_produces_endpoints_with_inherited_state(self):
        graph = load_transformation_graph(
            str(GRAPH_DIR / "nsclc_stg23_resectable.yaml")
        )
        cases = [
            (
                "neoadjuvant_chemo_ici",
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
                "adjuvant_atezolizumab_pembrolizumab",
                {
                    "person.chemo_ici_eligible": "ineligible",
                    "condition_occurrence.pdl1_binary": "present",
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
                "adjuvant_cisplatin_osimertinib",
                {
                    "condition_occurrence.biomarker": "egfr",
                    "person.performance_status": "ps1",
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

    def test_stage3_unresectable_tail_graph_produces_endpoints_with_inherited_state(self):
        graph = load_transformation_graph(
            str(GRAPH_DIR / "nsclc_stg3_unresectable.yaml")
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
            (
                "sequential_crt_adjuvant_osimertinib",
                {
                    "person.performance_status_v_frail": "not_v_frail",
                    "person.performance_status": "ps2",
                    "condition_occurrence.biomarker": "egfr",
                    "condition_occurrence.pdl1_binary": "present",
                },
            ),
            (
                "concurrent_crt_adjuvant_osimertinib",
                {
                    "person.performance_status_v_frail": "not_v_frail",
                    "person.performance_status": "ps1",
                    "condition_occurrence.biomarker": "egfr",
                    "condition_occurrence.pdl1_binary": "present",
                },
            ),
            (
                "sequential_crt_adjuvant_durvalumab",
                {
                    "person.performance_status_v_frail": "not_v_frail",
                    "person.performance_status": "ps2",
                    "condition_occurrence.biomarker": "none",
                    "condition_occurrence.pdl1_binary": "present",
                },
            ),
            (
                "concurrent_crt_adjuvant_durvalumab",
                {
                    "person.performance_status_v_frail": "not_v_frail",
                    "person.performance_status": "ps1",
                    "condition_occurrence.biomarker": "none",
                    "condition_occurrence.pdl1_binary": "present",
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

    def test_stage1_tail_graph_produces_expected_endpoint_patterns(self):
        graph = load_transformation_graph(str(GRAPH_DIR / "nsclc_stg1.yaml"))
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
                {"port"},
                {
                    "person.performance_status_v_frail": "not_v_frail",
                    "condition_occurrence.biomarker": "none",
                    "treatment_episode.resection_margin": "r1",
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

    def test_stage4_driver_tail_graph_ignores_non_actionable_biomarkers(self):
        graph = load_transformation_graph(str(GRAPH_DIR / "nsclc_stg4_driver.yaml"))

        for biomarker in ("none", "kras"):
            sampled = graph.sample_one(
                initial_state={"condition_occurrence.biomarker": biomarker}
            )
            self.assertEqual(
                extract_active_treatment_endpoints(sampled),
                [],
                biomarker,
            )

    def test_stage4_nodrivers_tail_graph_produces_endpoints_with_inherited_state(self):
        graph = load_transformation_graph(str(GRAPH_DIR / "nsclc_stg4_nodrivers.yaml"))
        cases = [
            (
                "best_supportive_care",
                {
                    "person.performance_status_v_frail": "v_frail",
                },
            ),
            (
                "platinum_doublet_3rd_gen_agent",
                {
                    "condition_occurrence.morphology": "squamous",
                    "person.performance_status_v_frail": "not_v_frail",
                    "person.ici_eligible": "ineligible",
                },
            ),
            (
                "platinum_doublet_nonsquamous",
                {
                    "condition_occurrence.morphology": "nonsquamous",
                    "person.performance_status_v_frail": "not_v_frail",
                    "person.ici_eligible": "ineligible",
                },
            ),
            (
                "ici_w_chemo_nonsquamous",
                {
                    "condition_occurrence.pdl1": "absent",
                    "condition_occurrence.pdl1_binary_50": "lt50",
                    "condition_occurrence.morphology": "nonsquamous",
                    "person.ici_eligible": "eligible",
                    "person.performance_status_frail": "not_frail",
                    "person.performance_status_v_frail": "not_v_frail",
                    "person.performance_status": "ps0",
                },
            ),
            (
                "ici_w_chemo_squamous",
                {
                    "condition_occurrence.pdl1": "absent",
                    "condition_occurrence.pdl1_binary_50": "lt50",
                    "condition_occurrence.morphology": "squamous",
                    "person.ici_eligible": "eligible",
                    "person.performance_status_frail": "not_frail",
                    "person.performance_status_v_frail": "not_v_frail",
                    "person.performance_status": "ps0",
                },
            ),
            (
                "ici_monotherapy",
                {
                    "person.performance_status": "ps2",
                    "person.performance_status_frail": "frail",
                    "person.performance_status_v_frail": "not_v_frail",
                    "person.ici_eligible": "eligible",
                    "condition_occurrence.pdl1": "high",
                },
            ),
            (
                "chemotherapy_or_atezolizumab_squamous",
                {
                    "person.performance_status": "ps2",
                    "person.performance_status_v_frail": "not_v_frail",
                    "person.ici_eligible": "eligible",
                    "condition_occurrence.morphology": "squamous",
                    "condition_occurrence.pdl1_binary_50": "lt50",
                    "condition_occurrence.pdl1": "low",
                },
            ),
            (
                "chemotherapy_or_atezolizumab_nonsquamous",
                {
                    "person.performance_status": "ps2",
                    "person.performance_status_v_frail": "not_v_frail",
                    "person.ici_eligible": "eligible",
                    "condition_occurrence.morphology": "nonsquamous",
                    "condition_occurrence.pdl1_binary_50": "lt50",
                    "condition_occurrence.pdl1": "low",
                },
            ),
            (
                "ici_monotherapy_or_ici_with_chemotherapy_squamous",
                {
                    "person.performance_status_frail": "not_frail",
                    "person.performance_status_v_frail": "not_v_frail",
                    "person.ici_eligible": "eligible",
                    "condition_occurrence.pdl1": "high",
                    "condition_occurrence.morphology": "squamous",
                    "person.performance_status": "ps0",
                },
            ),
            (
                "ici_monotherapy_or_ici_with_chemotherapy_nonsquamous",
                {
                    "person.performance_status_frail": "not_frail",
                    "person.performance_status_v_frail": "not_v_frail",
                    "person.ici_eligible": "eligible",
                    "condition_occurrence.pdl1": "high",
                    "condition_occurrence.morphology": "nonsquamous",
                    "person.performance_status": "ps0",
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

    def test_lung_yaml_files_do_not_repeat_treatment_node_names(self):
        pattern = re.compile(r"^\s{4}(treatment_episode\.[A-Za-z0-9_]+):", re.MULTILINE)
        for yaml_path in GRAPH_DIR.glob("*.yaml"):
            text = yaml_path.read_text(encoding="utf-8")
            seen: set[str] = set()
            duplicates: set[str] = set()
            for node_name in pattern.findall(text):
                if node_name in seen:
                    duplicates.add(node_name)
                seen.add(node_name)
            self.assertFalse(duplicates, f"{yaml_path.name}: duplicate treatment nodes {sorted(duplicates)}")


if __name__ == "__main__":
    unittest.main()
