import random, yaml
from typing import Dict, List, Any
from functools import reduce

def normalize(d: Dict[str, float]) -> Dict[str, float]:
    total = sum(d.values())
    return {k: v / total for k, v in d.items()}

def get_attr_path(obj: Any, path: str, default=None):
    try:
        return reduce(getattr, path.split("."), obj)
    except AttributeError:
        return default

def combine_prob_vectors(vectors: List[List[float]], mode: str = "multiply") -> List[float]:
    if not vectors:
        raise ValueError("No conditional vectors provided for combination.")
    if mode == "multiply":
        combined = [1.0] * len(vectors[0])
        for vec in vectors:
            combined = [a * b for a, b in zip(combined, vec)]
    elif mode == "average":
        n = len(vectors)
        combined = [sum(col) / n for col in zip(*vectors)]
    else:
        raise ValueError(f"Unknown combination mode: {mode}")
    total = sum(combined)
    return [v / total for v in combined]

class ConditionalMatrixDistribution:
    def __init__(self, distributions: Dict[str, Dict[str, Any]]):
        """
        distributions: mapping of key → dict containing
          - type: str
          - labels: list[str]
          - class_probabilities: dict
        """
        self.distributions = distributions

    @classmethod
    def from_yaml(cls, path: str):
        """Load all conditional distributions from YAML file."""
        with open(path, "r") as f:
            data = yaml.safe_load(f)
        return cls(data)
    
    def sample(self, key: str, context_obj: Any) -> str:
        """Combine multiple condition vectors based on context and sample from final distribution."""
        if key not in self.distributions:
            raise KeyError(f"Distribution '{key}' not found in config")

        conditional_vectors = []
           
        conf = self.distributions[key]
        if conf.get("type") != "conditional":
            raise ValueError(f"Distribution '{key}' must have type 'conditional'")

        labels = conf["labels"]
        class_probs = conf["class_probabilities"]
        combination = conf.get("combination", "multiply")

        for obj_name, attributes in class_probs.items():
            obj = context_obj.get(obj_name, None)
            if obj is None:
                continue
            for attr_name, table in attributes.items():
                val = getattr(obj, attr_name, None)
                probs = table.get(val, table.get("default"))
                if probs:
                    conditional_vectors.append(probs)
        combined = combine_prob_vectors(conditional_vectors, combination)
        # sample
        labels, weights = zip(*zip(labels, combined))
        return {key: c for c in random.choices(labels, weights=weights, k=1)[0]}
