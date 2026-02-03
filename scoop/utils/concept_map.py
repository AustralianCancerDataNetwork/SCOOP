import yaml
from pathlib import Path
from typing import TypeAlias, Mapping, cast


ConceptId = int
Label = str
Domain = str

ConceptMapping: TypeAlias = Mapping[Domain, Mapping[Label, ConceptId]]


def _validate_concept_mapping(obj: object) -> ConceptMapping:
    if not isinstance(obj, dict):
        raise ValueError("Concept mapping must be a dict")

    mapping = cast(dict[object, object], obj)

    for domain, inner in mapping.items():
        if not isinstance(domain, str):
            raise ValueError("Domain keys must be strings")

        if not isinstance(inner, dict):
            raise ValueError(f"Concept map for '{domain}' must be a dict")

        inner_map = cast(dict[object, object], inner)

        for label, cid in inner_map.items():
            if not isinstance(label, str):
                raise ValueError(f"Label '{label}' in '{domain}' must be a string")
            if not isinstance(cid, int):
                raise ValueError(f"Concept ID for '{label}' in '{domain}' must be int")

    return cast(ConceptMapping, mapping)


class ConceptMap:
    """Utility for mapping text labels to OMOP concept IDs."""
    def __init__(self, mapping: Mapping[str, int], default: int = 0):
        self.mapping = {k.lower(): v for k, v in mapping.items()}
        self.default = default

    def __call__(self, value: str | None) -> int:
        if not type(value) is str:
            return self.default
        return self.mapping.get(value.lower(), self.default)

    def __repr__(self):
        return f"<ConceptMap n={len(self.mapping)} default={self.default}>"

class ConceptMapper:
    """Container for multiple concept maps (domains)."""

    def __init__(self, mapping: str | Path | ConceptMapping, default: int = 0):
        if isinstance(mapping, (str, Path)):
            with open(mapping, "r") as f:
                mapping = yaml.safe_load(f)

        mapping = _validate_concept_mapping(mapping)

        self.maps: dict[str, ConceptMap] = {
            domain: ConceptMap(inner_map, default=default)
            for domain, inner_map in mapping.items()
        }

    def __call__(self, domain: str) -> ConceptMap:
        """Return a ConceptMap for a given domain."""
        try:
            return self.maps[domain]
        except KeyError as e:
            raise KeyError(f"No concept map found for domain '{domain}'") from e

    def __repr__(self):
        return f"<ConceptMapper domains={list(self.maps)}>"
