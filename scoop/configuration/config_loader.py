import yaml
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from ..utils import ConceptMapper, MeasurementMapper, ObservationMapper

from .transformation_graph import TransformationGraph

@dataclass(frozen=True)
class ProfileConfig:
    profile_name: str
    graph_relpath: Path
    concept_map_relpath: Path
    measurement_map_relpath: Path
    observation_map_relpath: Path

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
            self.base_dir / profile.concept_map_relpath
        )

        measurement_mapper = MeasurementMapper(
            self.base_dir / profile.measurement_map_relpath,
            concept_mapper,
        )

        observation_mapper = ObservationMapper(
            self.base_dir / profile.observation_map_relpath,
            concept_mapper,
        )

        graph = load_transformation_graph(
            f'{self.base_dir / profile.graph_relpath}'
        )

        return SimulationContext(
            profile_name=profile.profile_name,
            graph=graph,
            concept_mapper=concept_mapper,
            measurement_mapper=measurement_mapper,
            observation_mapper=observation_mapper,
        )


# TODO: for later - chain multiple profiles together

# class Pipeline:
#     def __init__(self, graphs: list[TransformationGraph]):
#         self.graphs = graphs

#     def sample(self) -> dict[str, Any]:
#         state: dict[str, Any] = {}
#         for graph in self.graphs:
#             out = graph.sample_one()
#             state.update(out)
#         return state

# def load_pipeline(self, profiles: list[ProfileConfig]) -> SimulationContext:
#     graphs = [load_transformation_graph(p.graph_path) for p in profiles]
#     ...

def lung_profile(name: str) -> ProfileConfig:
    return ProfileConfig(
        profile_name=name,
        graph_relpath=Path("profiles/lung") / f"{name}.yaml",
        concept_map_relpath=Path("mappers/concept_maps.yaml"),
        measurement_map_relpath=Path("mappers/measurement_mappers.yaml"),
        observation_map_relpath=Path("mappers/observation_mappers.yaml"),
    )


def load_transformation_graph(yaml_path: str) -> TransformationGraph:
    """
    Load the YAML graph into a TransformationGraph,
    build a DAG, validate, and return a GraphEngine instance.
    """
    with open(yaml_path, 'r') as f:
        raw = yaml.safe_load(f)

    tg = TransformationGraph.model_validate(raw['graph'])#(**raw['graph'])
    return tg
