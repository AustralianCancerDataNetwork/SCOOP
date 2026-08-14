# clinical_mapper_base.py
import yaml
import re
from pathlib import Path
from typing import Any, Type, Callable, TypeAlias
from .concept_map import ConceptMapper
from .modifier_map import ModifierMapper
from ..patients.clinical_field_base import ClinicalFieldType

ValueMap: TypeAlias = Callable[[Any], Any]


def _build_numeric_proxy_map(entry: dict[str, Any]) -> ValueMap:
    raw_map = entry.get("value_proxy_map", {})
    if not isinstance(raw_map, dict):
        raise ValueError("value_proxy_map must be a dict when provided")

    proxy_map = {str(k).lower(): float(v) for k, v in raw_map.items()}
    passthrough_numeric = bool(entry.get("passthrough_numeric", True))

    def mapper(value: Any) -> float | None:
        if value is None:
            return None
        if passthrough_numeric and isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            if passthrough_numeric:
                numeric_match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)%?", stripped)
                if numeric_match:
                    return float(numeric_match.group(1))
            return proxy_map.get(stripped.lower())
        return None

    return mapper

class ClinicalMapperBase:
    """
    Generic YAML loader and container for clinical field definitions.
    Subclasses (MeasurementMapper, ObservationMapper) must set:
      - record_name (for logging / repr)
      - field_class (e.g. MeasurementField, ObservationField)
    """

    record_name: str = "clinical_field"
    field_class: Type[Any]

    def __init__(
        self,
        mapping: str | Path | dict[str, Any],
        concept_mapper: ConceptMapper,
        modifier_mapper: ModifierMapper | None = None,
    ):
        
        if isinstance(mapping, (str, Path)):
            with open(mapping, "r") as f:
                mapping = yaml.safe_load(f)

        self.fields: dict[str, Any] = {}
        if not isinstance(mapping, dict):
            raise ValueError("Mapping must be a dict or path to valid YAML file")
        
        for name, entry in mapping.items():
            concept_id_key = f"{self.record_name}_concept_id" if self.record_name != "clinical_field" else "concept_id"
            concept_id = entry.get(concept_id_key)
            if concept_id is None:
                raise ValueError(f"Missing {concept_id_key} for field '{name}'")

            type_ = entry.get("type", "categorical")
            try:
                field_type = ClinicalFieldType(type_)
            except ValueError:
                raise ValueError(f"Unknown clinical field type '{type_}' for field {name}")

            unit_concept_id = entry.get("unit_concept_id")
            value_map_name = entry.get("value_map")
            modifier_map_name = entry.get("modifier_map")
            event_id_attr = entry.get("event_id_attr")
            event_field_concept_id = entry.get("event_field_concept_id")
            value_map: ValueMap
            
            if value_map_name:
                value_map = concept_mapper(value_map_name)
            elif modifier_map_name:
                if modifier_mapper is None:
                    raise ValueError(
                        f"Field '{name}' declares modifier_map='{modifier_map_name}' "
                        "but no ModifierMapper was provided."
                    )
                modifier_domain = modifier_mapper(modifier_map_name)
                value_map = lambda value, domain=modifier_domain: domain.sample(str(value)).concept_id
            elif field_type == ClinicalFieldType.NUMERIC and "value_proxy_map" in entry:
                value_map = _build_numeric_proxy_map(entry)
            else:
                value_map = float if type_ == "numeric" else lambda x: x

            field = self.field_class(
                concept_id=concept_id,
                value_map=value_map,
                field_type=field_type,
                unit_concept_id=unit_concept_id,
                source_field=name,
                event_id_attr=str(event_id_attr) if event_id_attr is not None else None,
                event_field_concept_id=(
                    int(event_field_concept_id)
                    if event_field_concept_id is not None
                    else None
                ),
                drop=entry.get("drop", True),
            )
            self.fields[name] = field

    def __call__(self, name: str):
        """Return a Field object for the given source name."""
        try:
            return self.fields[name]
        except KeyError as e:
            raise KeyError(f"No {self.record_name} definition found for '{name}'") from e

    def __getitem__(self, name: str):
        return self(name)

    def __repr__(self):
        return f"<{self.__class__.__name__} n={len(self.fields)}>"

from ..patients.measurement import MeasurementField
from ..patients.observation import ObservationField

class MeasurementMapper(ClinicalMapperBase):
    record_name = "measurement"
    field_class = MeasurementField

class ObservationMapper(ClinicalMapperBase):
    record_name = "observation"
    field_class = ObservationField
