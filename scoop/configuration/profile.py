from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .transformation_graph import TransformationGraph
from ..utils import ConceptMapper, MeasurementMapper, ObservationMapper
from .config_loader import load_transformation_graph

@dataclass(frozen=True)
class ProfileConfig:
    profile_name: str
    graph_path: Path
    concept_map_path: Path
    measurement_map_path: Path
    observation_map_path: Path


@dataclass
class SimulationContext:
    profile_name: str
    graph: TransformationGraph
    concept_mapper: ConceptMapper
    measurement_mapper: MeasurementMapper
    observation_mapper: ObservationMapper

    def sample_patient(self) -> dict[str, Any]:
        return self.graph.sample_one()

class ProfileLoader:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir

    def load(self, profile: ProfileConfig) -> SimulationContext:
        concept_mapper = ConceptMapper(
            self.base_dir / profile.concept_map_path
        )

        measurement_mapper = MeasurementMapper(
            self.base_dir / profile.measurement_map_path,
            concept_mapper,
        )

        observation_mapper = ObservationMapper(
            self.base_dir / profile.observation_map_path,
            concept_mapper,
        )

        graph = load_transformation_graph(
            f'{self.base_dir / profile.graph_path}'
        )

        return SimulationContext(
            profile_name=profile.profile_name,
            graph=graph,
            concept_mapper=concept_mapper,
            measurement_mapper=measurement_mapper,
            observation_mapper=observation_mapper,
        )

def lung_profile(name: str) -> ProfileConfig:
    return ProfileConfig(
        profile_name=name,
        graph_path=Path("profiles/lung") / f"{name}.yaml",
        concept_map_path=Path("mappers/concept_maps.yaml"),
        measurement_map_path=Path("mappers/measurement_mappers.yaml"),
        observation_map_path=Path("mappers/observation_mappers.yaml"),
    )