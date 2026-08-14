import unittest
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import scoop
from omop_alchemy.cdm import model as omop_model
from scoop.vocabulary.gateway import modifier_mapper
from scoop.profiles.manifest import (
    ProfileLoader,
    get_profile_config,
)
from scoop.patients import condition_occurrence as condition_occurrence_module
from scoop.patients.condition_episode import Condition_Episode
from scoop.patients import episode_base as episode_base_module
from scoop.patients.episode_base import Episode_Base
from scoop.patients.condition_occurrence import Condition_Occurrence
from scoop.patients.build import build_case_bundle, build_condition_episode
from scoop.patients.person import Person
from scoop.patients.treatment_episode import build_graph_outcome
from scoop.vocabulary import condition_resolver as condition_resolver_module
from scoop.vocabulary.condition_map import ResolvedConditionConcept
from scoop.vocabulary.modifier_map import ResolvedModifierConcept


# The entries/alias/resolver layer left with the deleted legacy registry module
# (f318297). RegistrySpec exposes source_phases/union_phases/protocols only, and
# ProfileLoader no longer attaches a registry to SimulationContext, so anything
# routing a graph endpoint through a registry entry cannot run yet.
REGISTRY_LAYER_PENDING = (
    "Pending registry redesign: RegistrySpec has no entries / get_entry / "
    "resolve_alias / resolve_hemonc_bundle equivalent, and ProfileLoader no "
    "longer attaches a registry to SimulationContext."
)


class RegimenAssignmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        def fake_resolve(*, morphology, topography=None):
            if morphology == "squamous" and topography == "upper":
                return ResolvedConditionConcept(
                    concept_id=44500984,
                    concept_code="8070/3-C34.1",
                    concept_name="Squamous cell carcinoma, NOS, of upper lobe, lung",
                    vocabulary_id="ICDO3",
                )
            if morphology == "unknown_histology":
                return ResolvedConditionConcept(
                    concept_id=44504998,
                    concept_code="8000/3-C34.9",
                    concept_name="Neoplasm, malignant of lung, NOS",
                    vocabulary_id="ICDO3",
                )
            return ResolvedConditionConcept(
                concept_id=44505016,
                concept_code="8140/3-C34.9",
                concept_name="Adenocarcinoma, NOS, of lung, NOS",
                vocabulary_id="ICDO3",
            )

        def fake_stage_sample(label, *, rng=None):
            concepts = {
                "stage_0": ResolvedModifierConcept(
                    concept_id=1633755,
                    concept_code="Stage 0a",
                    concept_name="Stage 0a",
                    vocabulary_id="Cancer Modifier",
                ),
                "stage_i": ResolvedModifierConcept(
                    concept_id=1633307,
                    concept_code="Stage IA",
                    concept_name="Stage IA",
                    vocabulary_id="Cancer Modifier",
                ),
                "stage_ii": ResolvedModifierConcept(
                    concept_id=1634210,
                    concept_code="Stage IIA",
                    concept_name="Stage IIA",
                    vocabulary_id="Cancer Modifier",
                ),
                "stage_iii": ResolvedModifierConcept(
                    concept_id=1633651,
                    concept_code="Stage IIIA",
                    concept_name="Stage IIIA",
                    vocabulary_id="Cancer Modifier",
                ),
                "stage_iv": ResolvedModifierConcept(
                    concept_id=1633309,
                    concept_code="Stage IVA",
                    concept_name="Stage IVA",
                    vocabulary_id="Cancer Modifier",
                ),
            }
            return concepts[label]

        def fake_metastasis_sample(label, *, rng=None):
            if label != "stage_iv":
                raise KeyError(label)
            return ResolvedModifierConcept(
                concept_id=36769181,
                concept_code="M1b",
                concept_name="Distant metastasis category M1b",
                vocabulary_id="Cancer Modifier",
            )

        cls._condition_map_patch = patch.object(
            condition_resolver_module,
            "_get_lung_condition_map",
            return_value=SimpleNamespace(resolve=fake_resolve),
        )
        cls._stage_modifier_patch = patch.object(
            modifier_mapper("cancer_stage"),
            "sample",
            side_effect=fake_stage_sample,
        )
        cls._metastasis_modifier_patch = patch.object(
            modifier_mapper("metastasis"),
            "sample",
            side_effect=fake_metastasis_sample,
        )
        cls._condition_map_patch.start()
        cls._stage_modifier_patch.start()
        cls._metastasis_modifier_patch.start()
        cls.root = Path(__file__).resolve().parents[1]
        cls.config_root = cls.root / "scoop"
        loader = ProfileLoader(cls.config_root)
        cls.context = loader.load(
            get_profile_config(
                "nsclc",
                graph_name="nsclc_stg23_resectable",
                base_dir=cls.config_root,
            )
        )
        cls.registry = getattr(cls.context, "regimen_registry", None)

    @classmethod
    def tearDownClass(cls):
        cls._condition_map_patch.stop()
        cls._stage_modifier_patch.stop()
        cls._metastasis_modifier_patch.stop()

    def test_package_import_smoke(self):
        self.assertTrue(hasattr(scoop, "__version__"))

    @unittest.skip(REGISTRY_LAYER_PENDING)
    def test_registry_loader_sets_entry_ids(self):
        direct_registry = load_regimen_registry(
            self.config_root / "profiles" / "nsclc" / "nsclc_regimen_registry.yaml"
        )
        entry = self.registry.get_entry("egfr_intervention")
        self.assertEqual(entry.entry_id, "egfr_intervention")
        self.assertEqual(direct_registry.get_entry("egfr_intervention").entry_id, "egfr_intervention")
        self.assertEqual(
            self.registry.resolve_alias("ici_monotherapy_or_ici_with_chemotherapy_squamous"),
            "ici_monotherapy_or_ici_with_chemotherapy_squamous",
        )

    @unittest.skip(REGISTRY_LAYER_PENDING)
    def test_profile_loader_attaches_registry_to_context(self):
        self.assertEqual(self.context.profile_name, "nsclc_stg23_resectable")
        self.assertIsNotNone(self.context.regimen_registry)

    @unittest.skip(REGISTRY_LAYER_PENDING)
    def test_build_graph_outcome_canonicalizes_alias(self):
        sample = {
            "condition_occurrence.morphology": "squamous",
            "condition_occurrence.pdl1": "high",
            "treatment_episode.ici_monotherapy_or_ici_with_chemotherapy_squamous": "ici_monotherapy_or_w_chemo_sqcc",
        }
        outcome = build_graph_outcome(
            graph_sample=sample,
            registry=self.registry,
            source_profile="nsclc_stg4_nodrivers",
        )
        self.assertEqual(
            outcome.primary_category_id,
            "ici_monotherapy_or_ici_with_chemotherapy_squamous",
        )
        self.assertIsNone(outcome.graph_alias_used)

    # Would pass vacuously: with no registry attached, build_graph_outcome raises
    # before it ever counts endpoints, so it no longer tests what it claims.
    @unittest.skip(REGISTRY_LAYER_PENDING)
    def test_build_graph_outcome_requires_single_endpoint(self):
        sample = {
            "treatment_episode.egfr_intervention": "egfr_int",
            "treatment_episode.alk_intervention": "alk_int",
        }
        with self.assertRaises(ValueError):
            build_graph_outcome(graph_sample=sample, registry=self.registry)

    @unittest.skip(REGISTRY_LAYER_PENDING)
    def test_condition_occurrence_assigns_registry_backed_regimen(self):
        sample = {
            "condition_occurrence.morphology": "nonsquamous",
            "condition_occurrence.biomarker": "egfr",
            "condition_occurrence.cancer_stage": "stage_iv",
            "treatment_episode.egfr_intervention": "egfr_int",
        }
        condition = Condition_Occurrence(
            person_id=1,
            graph_sample=sample,
            source_profile="nsclc_stg4_driver",
        )
        self.assertEqual(condition.regimen.regimen_type, "egfr_intervention")
        self.assertEqual(condition.regimen.regimen_category_id, "egfr_intervention")
        self.assertEqual(condition.regimen.registry_resolver_type, "component_query")
        self.assertEqual(condition.regimen.graph_endpoint, "egfr_intervention")
        self.assertEqual(condition.regimen.source_profile, "nsclc_stg4_driver")

    @unittest.skip(REGISTRY_LAYER_PENDING)
    def test_condition_occurrence_assigns_bundle_backed_regimen(self):
        sample = {
            "condition_occurrence.morphology": "nonsquamous",
            "condition_occurrence.cancer_stage": "stage_iii",
            "treatment_episode.radiotherapy": "rt_only",
        }
        condition = Condition_Occurrence(
            person_id=1,
            graph_sample=sample,
            source_profile="nsclc_stg3_unresectable",
        )
        self.assertEqual(condition.regimen.regimen_type, "radiotherapy")
        self.assertEqual(condition.regimen.registry_resolver_type, "bundle_recipe")
        self.assertEqual(condition.regimen.bundle_recipe_id, "radiotherapy")
        self.assertEqual(
            [phase["phase_id"] for phase in condition.regimen.bundle_phase_summary],
            ["radiotherapy"],
        )

    # Still reaches the unresolved fallback, but for the wrong reason (no registry
    # at all, rather than no active endpoints), so it would pass vacuously.
    @unittest.skip(REGISTRY_LAYER_PENDING)
    def test_condition_occurrence_uses_unresolved_fallback_for_non_registry_sample(self):
        sample = {
            "condition_occurrence.cancer_stage": "stage_0",
            "condition_occurrence.morphology": "nonsquamous",
        }
        condition = Condition_Occurrence(
            person_id=1,
            graph_sample=sample,
            source_profile="nsclc",
        )
        self.assertEqual(condition.regimen.regimen_type, "registry_unresolved")
        self.assertEqual(condition.regimen.registry_resolver_type, "none")
        self.assertIn("No active treatment_episode endpoints", condition.regimen.registry_notes)

    def test_person_can_be_created_without_auto_condition(self):
        person = Person(
            sex="female",
            age=65,
            auto_create_condition=False,
        )
        self.assertIsNone(person.condition)

    def test_build_case_bundle_can_stop_before_treatment_episode(self):
        sample = {
            "person.sex": "female",
            "person.age": 64,
            "person.smoking_history": "never",
            "person.weight": 61.5,
            "condition_occurrence.morphology": "nonsquamous",
            "condition_occurrence.cancer_stage": "stage_iv",
            "condition_occurrence.biomarker": "egfr",
            "treatment_episode.egfr_intervention": "egfr_int",
        }
        bundle = build_case_bundle(
            graph_sample=sample,
            include_treatment_episode=False,
        )
        self.assertIsNotNone(bundle.person)
        self.assertIsNotNone(bundle.condition_occurrence)
        self.assertIsNone(bundle.treatment_episode)
        self.assertIs(bundle.person.condition, bundle.condition_occurrence)
        self.assertIsNone(bundle.condition_occurrence.regimen)

    @unittest.skip(REGISTRY_LAYER_PENDING)
    def test_build_case_bundle_can_include_treatment_episode(self):
        sample = {
            "person.sex": "female",
            "person.age": 64,
            "person.smoking_history": "never",
            "person.weight": 61.5,
            "condition_occurrence.morphology": "nonsquamous",
            "condition_occurrence.cancer_stage": "stage_iv",
            "condition_occurrence.biomarker": "egfr",
            "treatment_episode.egfr_intervention": "egfr_int",
        }
        bundle = build_case_bundle(
            graph_sample=sample,
            include_treatment_episode=True,
        )
        self.assertEqual(bundle.treatment_episode.regimen_type, "egfr_intervention")
        self.assertIs(bundle.condition_occurrence.regimen, bundle.treatment_episode)

    def test_condition_occurrence_resolves_lung_icdo3_concept(self):
        condition = Condition_Occurrence(
            person_id=1,
            morphology="squamous",
            topog="upper",
            condition_domain="lung",
            auto_assign_regimen=False,
        )
        self.assertEqual(condition.condition_concept_id, 44500984)
        self.assertEqual(condition.condition_source_concept_id, 44500984)
        self.assertEqual(condition.condition_source_value, "8070/3-C34.1")

    def test_condition_occurrence_falls_back_to_generic_lung_malignancy(self):
        condition = Condition_Occurrence(
            person_id=1,
            morphology="unknown_histology",
            topog="unknown_topog",
            condition_domain="lung",
            auto_assign_regimen=False,
        )
        self.assertEqual(condition.condition_source_value, "8000/3-C34.9")
        self.assertEqual(condition.condition_concept_id, 44504998)

    def test_condition_occurrence_uses_profile_to_select_domain_resolver(self):
        condition = Condition_Occurrence(
            person_id=1,
            morphology="squamous",
            topog="upper",
            source_profile="nsclc_stg4_driver",
            auto_assign_regimen=False,
        )
        self.assertEqual(condition.condition_concept_id, 44500984)
        self.assertEqual(condition.condition_source_value, "8070/3-C34.1")

    def test_condition_occurrence_does_not_apply_lung_mapping_to_other_domains(self):
        condition = Condition_Occurrence(
            person_id=1,
            morphology="urothelial",
            topog="upper",
            condition_domain="bladder",
            auto_assign_regimen=False,
        )
        self.assertIsNone(condition.condition_concept_id)
        self.assertIsNone(condition.condition_source_concept_id)
        self.assertIsNone(condition.condition_source_value)

    def test_condition_occurrence_finalization_runs_in_dependency_order(self):
        call_order: list[str] = []

        def record_resolve(self):
            call_order.append("resolve")
            return self

        def record_episode(self):
            call_order.append("episode")
            return self

        def record_treatment(self):
            call_order.append("treatment")
            return self

        with patch.object(
            Condition_Occurrence,
            "_resolve_condition_concept",
            autospec=True,
            side_effect=record_resolve,
        ), patch.object(
            Condition_Occurrence,
            "_create_overarching_condition_episode",
            autospec=True,
            side_effect=record_episode,
        ), patch.object(
            Condition_Occurrence,
            "_assign_treatment",
            autospec=True,
            side_effect=record_treatment,
        ):
            Condition_Occurrence(
                person_id=1,
                condition_domain="lung",
                auto_assign_regimen=False,
            )

        self.assertEqual(call_order, ["resolve", "episode", "treatment"])

    def test_person_model_dump_emits_measurements_and_observations(self):
        person = Person(
            sex="female",
            age=65,
            smoking_history="never",
            weight=63.0,
            variant="egfr",
            auto_create_condition=False,
        )
        dumped = person.model_dump()
        measurements = dumped["measurements"]
        observations = dumped["observations"]

        weight_record = next(
            measurement
            for measurement in measurements
            if measurement["measurement_concept_id"] == 4099154
        )
        variant_record = next(
            measurement
            for measurement in measurements
            if measurement["measurement_concept_id"] == 4180182
        )
        smoking_record = observations[0]

        self.assertEqual(weight_record["value_as_number"], 63.0)
        self.assertEqual(weight_record["unit_concept_id"], 9529)
        self.assertEqual(variant_record["value_as_concept_id"], 35962802)
        self.assertEqual(smoking_record["observation_concept_id"], 43054909)
        self.assertIsNotNone(smoking_record["value_as_concept_id"])

    def test_condition_model_dump_emits_laterality_measurement(self):
        condition = Condition_Occurrence(
            person_id=1,
            morphology="squamous",
            laterality="left",
            topog="upper",
            condition_domain="lung",
            auto_assign_regimen=False,
        )
        dumped = condition.model_dump(exclude={"person", "regimen"})
        laterality_record = next(
            measurement
            for measurement in dumped["measurements"]
            if measurement["measurement_concept_id"] == 35918306
        )
        self.assertEqual(laterality_record["value_as_concept_id"], 45883143)
        self.assertEqual(laterality_record["value_source_value"], "left")
        self.assertEqual(
            laterality_record["measurement_event_id"],
            condition.condition_occurrence_id,
        )
        self.assertEqual(laterality_record["meas_event_field_concept_id"], 1147127)

    def test_condition_model_dump_emits_biomarker_measurement(self):
        condition = Condition_Occurrence(
            person_id=1,
            morphology="nonsquamous",
            biomarker="egfr",
            condition_domain="lung",
            auto_assign_regimen=False,
        )
        dumped = condition.model_dump(exclude={"person", "regimen"})
        biomarker_record = next(
            measurement
            for measurement in dumped["measurements"]
            if measurement["measurement_concept_id"] == 4180182
            and measurement["value_as_concept_id"] == 35962802
        )
        self.assertEqual(biomarker_record["value_source_value"], "egfr")

    def test_condition_model_dump_emits_met_biomarker_measurement(self):
        condition = Condition_Occurrence(
            person_id=1,
            morphology="nonsquamous",
            biomarker="met",
            condition_domain="lung",
            auto_assign_regimen=False,
        )
        dumped = condition.model_dump(exclude={"person", "regimen"})
        biomarker_record = next(
            measurement
            for measurement in dumped["measurements"]
            if measurement["measurement_concept_id"] == 4180182
            and measurement["value_as_concept_id"] == 35961878
        )
        self.assertEqual(biomarker_record["value_source_value"], "met")

    def test_condition_model_dump_emits_pdl1_measurement_from_proxy_bin(self):
        condition = Condition_Occurrence(
            person_id=1,
            morphology="nonsquamous",
            pdl1="high",
            condition_domain="lung",
            auto_assign_regimen=False,
        )
        dumped = condition.model_dump(exclude={"person", "regimen"})
        pdl1_record = next(
            measurement
            for measurement in dumped["measurements"]
            if measurement["measurement_concept_id"] == 1092026
        )
        self.assertEqual(pdl1_record["value_as_number"], 75.0)
        self.assertEqual(pdl1_record["unit_concept_id"], 8554)
        self.assertEqual(pdl1_record["value_source_value"], "high")

    def test_condition_model_dump_emits_pdl1_measurement_from_numeric_percent(self):
        condition = Condition_Occurrence(
            person_id=1,
            morphology="nonsquamous",
            pdl1="42%",
            condition_domain="lung",
            auto_assign_regimen=False,
        )
        dumped = condition.model_dump(exclude={"person", "regimen"})
        pdl1_record = next(
            measurement
            for measurement in dumped["measurements"]
            if measurement["measurement_concept_id"] == 1092026
        )
        self.assertEqual(pdl1_record["value_as_number"], 42.0)
        self.assertEqual(pdl1_record["unit_concept_id"], 8554)
        self.assertEqual(pdl1_record["value_source_value"], "42%")

    def test_condition_model_dump_emits_sampled_stage_measurement(self):
        condition = Condition_Occurrence(
            person_id=1,
            morphology="nonsquamous",
            cancer_stage="stage_ii",
            condition_domain="lung",
            auto_assign_regimen=False,
        )
        dumped = condition.model_dump(exclude={"person", "regimen"})
        stage_record = next(
            measurement
            for measurement in dumped["measurements"]
            if measurement["measurement_concept_id"] == 734320
        )
        self.assertEqual(stage_record["value_as_concept_id"], 1634210)
        self.assertEqual(stage_record["value_source_value"], "stage_ii")
        self.assertEqual(
            stage_record["measurement_event_id"],
            condition.condition_occurrence_id,
        )
        self.assertEqual(stage_record["meas_event_field_concept_id"], 1147127)

    def test_condition_model_dump_emits_metastasis_measurement_for_stage_iv(self):
        condition = Condition_Occurrence(
            person_id=1,
            morphology="nonsquamous",
            cancer_stage="stage_iv",
            condition_domain="lung",
            auto_assign_regimen=False,
        )
        dumped = condition.model_dump(exclude={"person", "regimen"})
        metastasis_record = next(
            measurement
            for measurement in dumped["measurements"]
            if measurement["measurement_concept_id"] == 36769181
        )
        self.assertEqual(metastasis_record["value_source_value"], "M1b")
        self.assertEqual(
            metastasis_record["measurement_event_id"],
            condition.condition_occurrence_id,
        )
        self.assertEqual(metastasis_record["meas_event_field_concept_id"], 1147127)

    def test_condition_model_dump_omits_metastasis_measurement_for_non_stage_iv(self):
        condition = Condition_Occurrence(
            person_id=1,
            morphology="nonsquamous",
            cancer_stage="stage_iii",
            condition_domain="lung",
            auto_assign_regimen=False,
        )
        dumped = condition.model_dump(exclude={"person", "regimen"})
        metastasis_records = [
            measurement
            for measurement in dumped["measurements"]
            if measurement["measurement_concept_id"] == 36769181
        ]
        self.assertEqual(metastasis_records, [])

    def test_build_condition_episode_creates_initial_episode_and_link(self):
        sampled_date = date(2021, 4, 9)
        with patch.object(
            condition_occurrence_module,
            "random_date",
            return_value=sampled_date,
        ) as mock_random_date:
            condition = Condition_Occurrence(
                person_id=1,
                morphology="squamous",
                topog="upper",
                year_of_diagnosis=2021,
                condition_domain="lung",
                auto_assign_regimen=False,
            )
            episode = build_condition_episode(condition)

        self.assertIsInstance(episode, Condition_Episode)
        self.assertEqual(episode.person_id, condition.person_id)
        self.assertEqual(episode.episode_concept_id, 32528)
        self.assertEqual(episode.episode_type_concept_id, 32533)
        self.assertEqual(episode.episode_object_concept_id, condition.condition_concept_id)
        self.assertEqual(episode.episode_start_date, sampled_date)
        self.assertEqual(len(episode.episode_events), 1)
        mock_random_date.assert_called_once_with(2021)

        episode_event = episode.episode_events[0]
        self.assertEqual(episode_event.episode_id, episode.episode_id)
        self.assertEqual(episode_event.event_id, condition.condition_occurrence_id)
        self.assertEqual(episode_event.episode_event_field_concept_id, 1147127)

    def test_episode_base_from_object_dispatches_condition_occurrence(self):
        with patch.object(
            condition_occurrence_module,
            "random_date",
            return_value=date(2021, 7, 14),
        ):
            condition = Condition_Occurrence(
                person_id=1,
                morphology="squamous",
                topog="upper",
                year_of_diagnosis=2021,
                condition_domain="lung",
                auto_assign_regimen=False,
            )
            episode = Episode_Base.from_object(condition)

        self.assertIsInstance(episode, Condition_Episode)
        self.assertEqual(episode.condition_occurrence_id, condition.condition_occurrence_id)
        self.assertEqual(episode.episode_object_concept_id, condition.condition_concept_id)

    def test_condition_model_dump_derives_diagnosis_year_and_start_date(self):
        expected_year = date.today().year - 3
        sampled_date = date(expected_year, 6, 15)

        with patch.object(
            condition_occurrence_module,
            "random_date",
            return_value=sampled_date,
        ) as mock_random_date:
            condition = Condition_Occurrence(
                person_id=1,
                morphology="squamous",
                topog="upper",
                years_since_diagnosis=3,
                condition_domain="lung",
                auto_assign_regimen=False,
            )
            dumped = condition.model_dump(exclude={"person", "regimen"})

        self.assertEqual(dumped["year_of_diagnosis"], expected_year)
        self.assertEqual(dumped["condition_start_date"], sampled_date)
        mock_random_date.assert_called_once_with(expected_year)

    def test_condition_concept_bundle_transform_respects_existing_values(self):
        condition = Condition_Occurrence(
            person_id=1,
            morphology="squamous",
            topog="upper",
            condition_domain="lung",
            condition_source_value="custom-source",
            auto_assign_regimen=False,
        )

        self.assertEqual(condition.condition_concept_id, 44500984)
        self.assertEqual(condition.condition_source_concept_id, 44500984)
        self.assertEqual(condition.condition_source_value, "custom-source")

    def test_condition_to_omop_bundle_includes_related_episode_rows(self):
        sampled_date = date(2021, 4, 9)
        with patch.object(
            condition_occurrence_module,
            "random_date",
            return_value=sampled_date,
        ):
            condition = Condition_Occurrence(
                person_id=1,
                morphology="squamous",
                laterality="left",
                topog="upper",
                year_of_diagnosis=2021,
                condition_domain="lung",
                auto_assign_regimen=False,
            )

        bundle = condition.to_omop_bundle(include_related=True)

        self.assertEqual(len(bundle.condition_occurrences), 1)
        self.assertEqual(len(bundle.episodes), 1)
        self.assertEqual(len(bundle.episode_events), 1)
        self.assertTrue(
            any(
                measurement.measurement_concept_id == 35918306
                for measurement in bundle.measurements
            )
        )

        condition_row = bundle.condition_occurrences[0]
        self.assertIsInstance(condition_row, omop_model.Condition_Occurrence)
        self.assertEqual(condition_row.condition_occurrence_id, condition.condition_occurrence_id)
        self.assertEqual(condition_row.condition_start_date, sampled_date)
        self.assertEqual(condition_row.condition_start_datetime, datetime(2021, 4, 9))

        episode_row = bundle.episodes[0]
        self.assertIsInstance(episode_row, omop_model.Episode)
        self.assertEqual(episode_row.episode_object_concept_id, condition.condition_concept_id)
        self.assertEqual(episode_row.episode_start_date, sampled_date)
        self.assertEqual(episode_row.episode_start_datetime, datetime(2021, 4, 9))

    def test_person_to_omop_bundle_merges_related_condition_outputs(self):
        sampled_date = date(2020, 2, 2)
        with patch.object(
            condition_occurrence_module,
            "random_date",
            return_value=sampled_date,
        ):
            person = Person(
                sex="female",
                age=65,
                smoking_history="never",
                weight=63.0,
                variant="egfr",
                auto_create_condition=True,
            )

        bundle = person.to_omop_bundle(include_related=True)

        self.assertIsInstance(bundle.person, omop_model.Person)
        self.assertEqual(bundle.person.person_id, person.person_id)
        self.assertEqual(len(bundle.condition_occurrences), 1)
        self.assertEqual(len(bundle.episodes), 1)
        self.assertGreaterEqual(len(bundle.measurements), 1)
        self.assertGreaterEqual(len(bundle.observations), 1)


if __name__ == "__main__":
    unittest.main()
