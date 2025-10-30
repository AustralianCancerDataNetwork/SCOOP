import yaml
from pathlib import Path
from typing import Any

class ConceptMap:
    """Utility for mapping text labels to OMOP concept IDs."""
    def __init__(self, mapping: dict[str, Any], default: int = 0):
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

    def __init__(self, mapping: str | Path | dict[str, Any], default: int = 0):
        if isinstance(mapping, (str, Path)):
            with open(mapping, "r") as f:
                mapping = yaml.safe_load(f)

        # mapping is expected to be dict[domain, dict[label, concept_id]]
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
