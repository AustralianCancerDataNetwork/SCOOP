import re
import random
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError
import yaml
import sqlalchemy as sa
from scoop.configuration.config_loader import (
    list_profiles,
    load_routed_profile,
    load_profile_manifest,
    load_transformation_graph,
)
from scoop.configuration.regimen_registry import RegimenRegistry, load_regimen_registry
from scoop.schema.treatment_episode import extract_active_treatment_endpoints
from scoop.utils.variant_selection import (
    apply_default_component_requirements,
    build_component_requirement_expressions,
)
from scoop.utils.hemonc_bundle import assemble_bundle_matches, execute_bundle_query


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "scoop" / "configuration" / "profiles" / "nsclc" / "nsclc_regimen_registry.yaml"
GRAPH_DIR = ROOT / "scoop" / "configuration" / "profiles" / "nsclc"
STAGE_SPECIFIC_GRAPHS = [
    "nsclc_stg1.yaml",
    "nsclc_stg23_resectable.yaml",
    "nsclc_stg3_unresectable.yaml",
    "nsclc_stg4_nodrivers.yaml",
    "nsclc_stg4_driver.yaml",
]
NON_REGISTRY_TREATMENT_STATE_NODES = {"resection_margin"}


class NSCLCRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with REGISTRY_PATH.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
        cls.registry = raw["registry"]
        cls.loaded_registry = load_regimen_registry(REGISTRY_PATH)
        cls.entries = cls.registry["entries"]
        cls.hemonc_groups = cls.registry.get("hemonc_groups", {})
        cls.hemonc_bundle_groups = cls.registry.get("hemonc_bundle_groups", {})

    def test_registry_yaml_loads(self):
        self.assertEqual(self.registry["disease"], "nsclc")
        self.assertEqual(self.registry["version"], 1)

    def test_profile_manifest_loads(self):
        manifest = load_profile_manifest("nsclc", base_dir=ROOT / "scoop" / "configuration")
        self.assertEqual(manifest.profile_id, "nsclc")
        self.assertIn("nsclc_stg4_driver", manifest.graphs)
        self.assertEqual(manifest.entry_graph, "nsclc")
        self.assertEqual(
            manifest.pathways["nsclc_stage4_nodrivers"],
            "nsclc_stg4_nodrivers",
        )
        self.assertIn("nsclc", list_profiles(ROOT / "scoop" / "configuration"))

    def test_routed_runtime_loads_for_nsclc(self):
        runtime = load_routed_profile("nsclc", base_dir=ROOT / "scoop" / "configuration")
        self.assertEqual(runtime.entry_graph_name, "nsclc")
        self.assertIn("nsclc_stage1", runtime.pathway_to_graph)
        self.assertIn("nsclc_stg1", runtime.tail_contexts)

    def test_every_entry_has_required_fields(self):
        required = {
            "public",
            "stage_scope",
            "clinical_group",
            "graph_sources",
            "resolver",
        }
        for entry_id, entry in self.entries.items():
            self.assertTrue(required.issubset(entry.keys()), entry_id)
            self.assertIn("type", entry["resolver"], entry_id)

    def test_aliases_default_to_empty_list_when_omitted(self):
        registry = RegimenRegistry.model_validate(
            {
                "disease": "test",
                "version": 1,
                "entries": {
                    "simple": {
                        "public": True,
                        "stage_scope": [],
                        "clinical_group": "test_group",
                        "graph_sources": [],
                        "resolver": {"type": "none"},
                        "notes": None,
                    }
                },
            }
        )
        self.assertEqual(registry.entries["simple"].aliases, [])

    def test_component_query_tracks_if_category_restrictions_were_omitted(self):
        registry = RegimenRegistry.model_validate(
            {
                "disease": "test",
                "version": 1,
                "entries": {
                    "omitted": {
                        "public": True,
                        "stage_scope": [],
                        "clinical_group": "test_group",
                        "graph_sources": [],
                        "resolver": {
                            "type": "component_query",
                            "default_condition_cuis": [1],
                            "component_lists": [["drug_a"]],
                        },
                    },
                    "explicit_empty": {
                        "public": True,
                        "stage_scope": [],
                        "clinical_group": "test_group",
                        "graph_sources": [],
                        "resolver": {
                            "type": "component_query",
                            "default_condition_cuis": [1],
                            "component_lists": [["drug_a"]],
                            "category_restrictions": {},
                        },
                    },
                },
            }
        )
        omitted = registry.resolve_hemonc_query("omitted")
        explicit_empty = registry.resolve_hemonc_query("explicit_empty")
        self.assertIsNotNone(omitted)
        self.assertIsNotNone(explicit_empty)
        self.assertFalse(omitted.category_restrictions_provided)
        self.assertTrue(explicit_empty.category_restrictions_provided)

    def test_omitted_category_restrictions_use_compatibility_defaults(self):
        normalized = apply_default_component_requirements(
            {
                "component_lists": [["drug_a"]],
                "category_restrictions_provided": False,
            }
        )
        self.assertEqual(
            normalized,
            {
                "component_lists": [["drug_a"]],
                "category_restrictions": {
                    "chemotherapy": {"type": "min", "amount": 0},
                },
            },
        )

    def test_explicit_empty_category_restrictions_remain_strict(self):
        normalized = apply_default_component_requirements(
            {
                "component_lists": [["drug_a"]],
                "category_restrictions": {},
                "category_restrictions_provided": True,
            }
        )
        self.assertEqual(
            normalized["category_restrictions"],
            {
                "radiotherapy": {"type": "exact", "amount": 0},
                "surgery": {"type": "exact", "amount": 0},
                "chemotherapy": {"type": "exact", "amount": 0},
                "immunotherapy": {"type": "exact", "amount": 0},
            },
        )

    def test_explicit_chemotherapy_min_zero_uses_compatibility_defaults(self):
        normalized = apply_default_component_requirements(
            {
                "component_lists": [["drug_a"]],
                "category_restrictions": {
                    "chemotherapy": {"type": "min", "amount": 0},
                },
                "category_restrictions_provided": True,
            }
        )
        self.assertEqual(
            normalized,
            {
                "component_lists": [["drug_a"]],
                "category_restrictions": {
                    "chemotherapy": {"type": "min", "amount": 0},
                },
            },
        )

    def test_invalid_category_restriction_key_is_rejected(self):
        with self.assertRaises(ValidationError):
            RegimenRegistry.model_validate(
                {
                    "disease": "test",
                    "version": 1,
                    "entries": {
                        "invalid": {
                            "public": True,
                            "stage_scope": [],
                            "clinical_group": "test_group",
                            "graph_sources": [],
                            "resolver": {
                                "type": "component_query",
                                "default_condition_cuis": [1],
                                "component_lists": [["drug_a"]],
                                "category_restrictions": {
                                    "invalid_category": {"type": "exact", "amount": 0}
                                },
                            },
                        }
                    },
                }
            )

    def test_component_query_entries_have_required_fields(self):
        for entry_id, entry in self.entries.items():
            resolver = entry["resolver"]
            if resolver["type"] == "component_query":
                if "group_id" in resolver:
                    self.assertIn(resolver["group_id"], self.hemonc_groups, entry_id)
                else:
                    self.assertIn("default_condition_cuis", resolver, entry_id)
                    self.assertIn("component_lists", resolver, entry_id)
                    self.assertIn("category_restrictions", resolver, entry_id)
            elif resolver["type"] == "bundle_recipe":
                self.assertIn("group_id", resolver, entry_id)
                self.assertIn(resolver["group_id"], self.hemonc_bundle_groups, entry_id)
            elif resolver["type"] == "composite":
                self.assertIn("members", resolver, entry_id)

    def test_hemonc_groups_have_required_fields(self):
        self.assertIn("egfr_intervention", self.hemonc_groups)
        for group_id, group in self.hemonc_groups.items():
            self.assertIn("component_lists", group, group_id)
            self.assertIn("category_restrictions", group, group_id)
            self.assertTrue(
                ("condition_set" in group) or ("default_condition_cuis" in group),
                group_id,
            )

    def test_hemonc_bundle_groups_have_required_fields(self):
        self.assertIn("sequential_crt", self.hemonc_bundle_groups)
        for group_id, group in self.hemonc_bundle_groups.items():
            self.assertIn("phases", group, group_id)
            self.assertTrue(group["phases"], group_id)

    def test_loaded_registry_resolves_group_backed_component_queries(self):
        resolved = self.loaded_registry.resolve_hemonc_query("egfr_intervention")
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.group_id, "egfr_intervention")
        self.assertEqual(
            resolved.default_condition_cuis,
            self.registry["condition_sets"]["lung_nsclc_all"],
        )
        self.assertEqual(
            resolved.component_lists,
            [["osimertinib", "gefitinib", "erlotinib", "afatinib", "dacomitinib"]],
        )

    def test_resolved_query_category_restrictions_work_in_hemonc_helper(self):
        resolved = self.loaded_registry.resolve_hemonc_query("egfr_intervention")
        self.assertIsNotNone(resolved)

        categorised = sa.table(
            "categorised_components",
            sa.column("broad_category"),
            sa.column("component_cui"),
            sa.column("canmed_major_class"),
            sa.column("canmed_minor_class"),
            sa.column("main_class"),
            sa.column("component"),
        )
        expressions = build_component_requirement_expressions(
            {
                "component_lists": resolved.component_lists,
                "category_restrictions": resolved.category_restrictions,
            },
            categorised,
        )
        self.assertGreaterEqual(len(expressions), 1)

    def test_targeted_driver_query_normalization_preserves_explicit_output(self):
        resolved = self.loaded_registry.resolve_hemonc_query("egfr_intervention")
        self.assertIsNotNone(resolved)
        normalized = apply_default_component_requirements(
            {
                "component_lists": resolved.component_lists,
                "category_restrictions": resolved.category_restrictions,
                "category_restrictions_provided": resolved.category_restrictions_provided,
            }
        )
        self.assertEqual(normalized["category_restrictions"]["chemotherapy"], {"type": "min", "amount": 1})
        self.assertEqual(normalized["category_restrictions"]["immunotherapy"], {"type": "min", "amount": 0})

    def test_chemo_ici_query_normalization_preserves_explicit_output(self):
        resolved = self.loaded_registry.resolve_hemonc_query("ici_w_chemo_nonsquamous")
        self.assertIsNotNone(resolved)
        normalized = apply_default_component_requirements(
            {
                "component_lists": resolved.component_lists,
                "category_restrictions": resolved.category_restrictions,
                "category_restrictions_provided": resolved.category_restrictions_provided,
            }
        )
        self.assertEqual(normalized["category_restrictions"]["chemotherapy"], {"type": "min", "amount": 0})
        self.assertEqual(
            normalized["category_restrictions"],
            {"chemotherapy": {"type": "min", "amount": 0}},
        )

    def test_legacy_targeted_driver_query_uses_compatibility_defaults(self):
        resolved = self.loaded_registry.resolve_hemonc_query("alk_intervention")
        self.assertIsNotNone(resolved)
        normalized = apply_default_component_requirements(
            {
                "component_lists": resolved.component_lists,
                "category_restrictions": resolved.category_restrictions,
                "category_restrictions_provided": resolved.category_restrictions_provided,
            }
        )
        self.assertEqual(
            normalized["category_restrictions"],
            {"chemotherapy": {"type": "min", "amount": 0}},
        )

    def test_supportive_query_normalization_preserves_explicit_output(self):
        resolved = self.loaded_registry.resolve_hemonc_query("best_supportive_care")
        self.assertIsNotNone(resolved)
        normalized = apply_default_component_requirements(
            {
                "component_lists": resolved.component_lists,
                "category_restrictions": resolved.category_restrictions,
                "category_restrictions_provided": resolved.category_restrictions_provided,
            }
        )
        self.assertEqual(normalized["category_restrictions"]["supportive"], {"type": "min", "amount": 1})
        self.assertEqual(normalized["category_restrictions"]["chemotherapy"], {"type": "exact", "amount": 0})

    def test_loaded_registry_keeps_composites_non_queryable(self):
        self.assertIsNone(
            self.loaded_registry.resolve_hemonc_query("met_intervention")
        )

    def test_loaded_registry_resolves_bundle_recipes(self):
        resolved = self.loaded_registry.resolve_hemonc_bundle(
            "sequential_crt_adjuvant_durvalumab"
        )
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.group_id, "sequential_crt_adjuvant_durvalumab")
        self.assertEqual(resolved.sequence_template, "sequential")
        self.assertEqual(
            [phase.phase_id for phase in resolved.phases],
            ["systemic", "radiotherapy", "adjuvant_durvalumab"],
        )
        self.assertEqual(resolved.phases[0].source_group_id, "nsclc_crt_backbone")
        self.assertEqual(resolved.phases[1].component_terms, ["External beam radiotherapy"])
        self.assertEqual(
            resolved.phases[2].resolved_query.component_lists,
            [["durvalumab"]],
        )

    def test_bundle_assembly_supports_variant_and_sig_phases(self):
        resolved = self.loaded_registry.resolve_hemonc_bundle("sequential_crt")
        self.assertIsNotNone(resolved)

        class FakeSig:
            def __init__(self, sig_id, component, variant_cui=None):
                self.sig_id = sig_id
                self.component = component
                self.variant_cui = variant_cui

        class FakeVariant:
            def __init__(self, variant_cui, regimen, sig_ids):
                self.variant_cui = variant_cui
                self.regimen = regimen
                self.component_sigs = [
                    FakeSig(sig_id, "Cisplatin", variant_cui=variant_cui) for sig_id in sig_ids
                ]

        bundles = assemble_bundle_matches(
            resolved,
            {
                "systemic": [FakeVariant(101, "CRT backbone", [1, 2])],
                "radiotherapy": [FakeSig(99, "External beam radiotherapy")],
            },
        )
        self.assertEqual(len(bundles), 1)
        self.assertEqual(bundles[0].variant_cui, 101)
        self.assertEqual(
            [phase.phase_type for phase in bundles[0].phases],
            ["systemic", "radiotherapy"],
        )
        self.assertIn("External beam radiotherapy", bundles[0].bundle_name)

    def test_execute_bundle_query_filters_incompatible_crt_pairs_by_study(self):
        resolved = self.loaded_registry.resolve_hemonc_bundle("sequential_crt")
        self.assertIsNotNone(resolved)

        class FakeStudy:
            def __init__(self, study):
                self.study = study

        class FakeSig:
            def __init__(self, sig_id, component, studies, variant_cui=None):
                self.sig_id = sig_id
                self.component = component
                self.variant_cui = variant_cui
                self.study_objects = [FakeStudy(study) for study in studies]

        class FakeVariant:
            def __init__(self, variant_cui, regimen, sig_ids, studies):
                self.variant_cui = variant_cui
                self.regimen = regimen
                self.component_sigs = [
                    FakeSig(sig_id, "Cisplatin", studies, variant_cui=variant_cui)
                    for sig_id in sig_ids
                ]

        class FakeResult:
            def __init__(self, records):
                self.records = records

            def unique(self):
                return self

            def scalars(self):
                return self

            def all(self):
                return list(self.records)

        class FakeSession:
            def __init__(self, records_by_stmt):
                self.records_by_stmt = records_by_stmt

            def execute(self, stmt):
                return FakeResult(self.records_by_stmt[stmt])

        systemic_variant = FakeVariant(101, "CRT backbone", [1, 2], ["Shared Trial"])
        compatible_rt = FakeSig(99, "External beam radiotherapy", ["Shared Trial"])
        incompatible_rt = FakeSig(100, "External beam radiotherapy", ["Other Trial"])
        session = FakeSession(
            {
                "systemic": [systemic_variant],
                "radiotherapy": [compatible_rt, incompatible_rt],
            }
        )

        with patch(
            "scoop.utils.hemonc_bundle.build_bundle_phase_statement",
            side_effect=lambda phase, models=None: phase.phase_id,
        ):
            bundles = execute_bundle_query(session, resolved)

        self.assertEqual(len(bundles), 1)
        self.assertEqual(bundles[0].variant_cui, 101)
        self.assertEqual(
            [phase.phase_id for phase in bundles[0].phases],
            ["systemic", "radiotherapy"],
        )

    def test_execute_bundle_query_allows_post_adjuvant_phase_with_distinct_study(self):
        resolved = self.loaded_registry.resolve_hemonc_bundle(
            "sequential_crt_adjuvant_durvalumab"
        )
        self.assertIsNotNone(resolved)

        class FakeStudy:
            def __init__(self, study):
                self.study = study

        class FakeSig:
            def __init__(self, sig_id, component, studies, variant_cui=None):
                self.sig_id = sig_id
                self.component = component
                self.variant_cui = variant_cui
                self.study_objects = [FakeStudy(study) for study in studies]

        class FakeVariant:
            def __init__(self, variant_cui, regimen, component, studies):
                self.variant_cui = variant_cui
                self.regimen = regimen
                self.component_sigs = [
                    FakeSig(variant_cui, component, studies, variant_cui=variant_cui)
                ]

        class FakeResult:
            def __init__(self, records):
                self.records = records

            def unique(self):
                return self

            def scalars(self):
                return self

            def all(self):
                return list(self.records)

        class FakeSession:
            def __init__(self, records_by_stmt):
                self.records_by_stmt = records_by_stmt

            def execute(self, stmt):
                return FakeResult(self.records_by_stmt[stmt])

        session = FakeSession(
            {
                "systemic": [FakeVariant(201, "CRT backbone", "Cisplatin", ["Shared Trial"])],
                "radiotherapy": [
                    FakeSig(301, "External beam radiotherapy", ["Shared Trial"])
                ],
                "adjuvant_durvalumab": [
                    FakeVariant(401, "Durvalumab", "Durvalumab", ["PACIFIC"])
                ],
            }
        )

        with patch(
            "scoop.utils.hemonc_bundle.build_bundle_phase_statement",
            side_effect=lambda phase, models=None: phase.phase_id,
        ):
            bundles = execute_bundle_query(session, resolved)

        self.assertEqual(len(bundles), 1)
        self.assertEqual(
            [phase.phase_id for phase in bundles[0].phases],
            ["systemic", "radiotherapy", "adjuvant_durvalumab"],
        )

    def test_execute_bundle_query_filters_sig_phase_records_by_condition_when_available(self):
        resolved = self.loaded_registry.resolve_hemonc_bundle("sequential_crt")
        self.assertIsNotNone(resolved)
        target_condition_cui = resolved.resolved_condition_cuis[0]

        class FakeCondition:
            def __init__(self, condition_cui):
                self.condition_cui = condition_cui

        class FakeSig:
            def __init__(self, sig_id, component, condition_cuis, variant_cui=None):
                self.sig_id = sig_id
                self.component = component
                self.variant_cui = variant_cui
                self.condition_objects = [
                    FakeCondition(condition_cui) for condition_cui in condition_cuis
                ]

        class FakeVariant:
            def __init__(self, variant_cui, regimen, sig_ids):
                self.variant_cui = variant_cui
                self.regimen = regimen
                self.component_sigs = [
                    FakeSig(sig_id, "Cisplatin", [target_condition_cui], variant_cui=variant_cui)
                    for sig_id in sig_ids
                ]

        class FakeResult:
            def __init__(self, records):
                self.records = records

            def unique(self):
                return self

            def scalars(self):
                return self

            def all(self):
                return list(self.records)

        class FakeSession:
            def __init__(self, records_by_stmt):
                self.records_by_stmt = records_by_stmt

            def execute(self, stmt):
                return FakeResult(self.records_by_stmt[stmt])

        session = FakeSession(
            {
                "systemic": [FakeVariant(101, "CRT backbone", [1, 2])],
                "radiotherapy": [
                    FakeSig(99, "External beam radiotherapy", [target_condition_cui]),
                    FakeSig(100, "External beam radiotherapy", [999999]),
                ],
            }
        )

        with patch(
            "scoop.utils.hemonc_bundle.build_bundle_phase_statement",
            side_effect=lambda phase, models=None: phase.phase_id,
        ):
            bundles = execute_bundle_query(session, resolved)

        self.assertEqual(len(bundles), 1)
        self.assertEqual(
            [phase.sig_ids for phase in bundles[0].phases if phase.phase_id == "radiotherapy"],
            [[(None, 99, "External beam radiotherapy", None)]],
        )

    def test_composite_members_exist(self):
        for entry_id, entry in self.entries.items():
            resolver = entry["resolver"]
            if resolver["type"] == "composite":
                for member in resolver["members"]:
                    self.assertIn(member, self.entries, f"{entry_id} -> {member}")

    def test_public_and_internal_counts(self):
        public_entries = [name for name, entry in self.entries.items() if entry["public"]]
        internal_entries = [name for name, entry in self.entries.items() if not entry["public"]]
        self.assertEqual(len(public_entries), 36)
        self.assertEqual(len(internal_entries), 8)

    def test_public_ids_match_expected_list(self):
        expected = {
            "sbrt",
            "surgery_only",
            "port",
            "port_adjuvant_osimertinib",
            "surgery_adjuvant_osimertinib",
            "neoadjuvant_chemo_ici",
            "adjuvant_pembrolizumab",
            "adjuvant_atezolizumab_pembrolizumab",
            "adjuvant_osimertinib",
            "adjuvant_cisplatin_osimertinib",
            "adjuvant_optchemo_alectinib",
            "radiotherapy",
            "sequential_crt",
            "concurrent_crt",
            "sequential_crt_adjuvant_osimertinib",
            "concurrent_crt_adjuvant_osimertinib",
            "sequential_crt_adjuvant_durvalumab",
            "concurrent_crt_adjuvant_durvalumab",
            "egfr_intervention",
            "alk_intervention",
            "ros1_intervention",
            "braf_intervention",
            "ret_intervention",
            "erbb2_intervention",
            "ntrk_intervention",
            "met_intervention",
            "best_supportive_care",
            "platinum_doublet_3rd_gen_agent",
            "platinum_doublet_nonsquamous",
            "ici_w_chemo_nonsquamous",
            "ici_w_chemo_squamous",
            "ici_monotherapy",
            "chemotherapy_or_atezolizumab_squamous",
            "chemotherapy_or_atezolizumab_nonsquamous",
            "ici_monotherapy_or_ici_with_chemotherapy_squamous",
            "ici_monotherapy_or_ici_with_chemotherapy_nonsquamous",
        }
        public_entries = {name for name, entry in self.entries.items() if entry["public"]}
        self.assertEqual(public_entries, expected)

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
