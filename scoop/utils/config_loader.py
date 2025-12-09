import yaml
from scoop.configuration.transformation_graph import TransformationGraph


def load_transformation_graph(yaml_path: str) -> TransformationGraph:
    """
    Load the YAML graph into a TransformationGraph,
    build a DAG, validate, and return a GraphEngine instance.
    """
    with open(yaml_path, 'r') as f:
        raw = yaml.safe_load(f)

    tg = TransformationGraph.model_validate(raw['graph'])#(**raw['graph'])
    return tg
