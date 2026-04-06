from typing import Any, Callable

from ..configuration import measurement_mapper, observation_mapper
from pydantic import BaseModel


class TransformableBase(BaseModel):
    """Base class providing declarative field transformations on dump."""
    __transformations__: dict[str, dict] = {}
    __persisted_transforms__: dict[str, dict] = {}
    __persisted_bundle_transforms__: dict[str, dict] = {}
    __emitted_transforms__: dict[str, dict] = {}
    __base_transformations__: dict[str, dict] = {}

    def model_post_init(self, __context: Any) -> None:
        self._apply_persisted_bundle_transforms()
        self._apply_persisted_transforms()

    def _apply_persisted_bundle_transforms(self) -> None:
        for name, spec in self.__persisted_bundle_transforms__.items():
            self._apply_named_persisted_bundle_transform(name)

    def _apply_named_persisted_bundle_transform(self, name: str) -> None:
        spec = self.__persisted_bundle_transforms__[name]
        outputs = tuple(spec.get("outputs", ()))
        if not outputs:
            raise ValueError(f"Persisted bundle transform '{name}' must declare outputs.")

        if (
            spec.get("when_missing", True)
            and all(getattr(self, out_field, None) is not None for out_field in outputs)
        ):
            return

        value = spec["transform"](self)
        if value is None:
            return
        if not isinstance(value, dict):
            raise TypeError(
                f"Persisted bundle transform '{name}' must return a dict keyed by output field."
            )

        for out_field in outputs:
            if out_field not in value:
                raise KeyError(
                    f"Persisted bundle transform '{name}' did not provide '{out_field}'."
                )
            if spec.get("when_missing", True) and getattr(self, out_field, None) is not None:
                continue
            setattr(self, out_field, value[out_field])

    def _apply_persisted_transforms(self) -> None:
        for out_field, spec in self.__persisted_transforms__.items():
            if spec.get("when_missing", True) and getattr(self, out_field, None) is not None:
                continue
            value = spec["transform"](self)
            setattr(self, out_field, value)

    def _collect_emitted_outputs(self) -> tuple[dict[str, Any], list[Any], list[Any], set[str]]:
        field_updates: dict[str, Any] = {}
        measurements = []
        observations = []
        dropped_inputs: set[str] = set()

        for out_field, spec in self.__transformations__.items():
            fn: Callable = spec["transform"]
            drop: bool = spec.get("drop", False)
            val = fn(self)

            kind = spec.get("kind")
            if kind is None:
                if "measurement" in out_field:
                    kind = "measurement"
                elif "observation" in out_field:
                    kind = "observation"
                else:
                    kind = "field"

            if kind == "measurement":
                measurements.append(val)
            elif kind == "observation":
                observations.append(val)
            else:
                field_updates[out_field] = val

            if drop:
                dropped_inputs.update(spec.get("inputs", []))

        return field_updates, measurements, observations, dropped_inputs

    def model_dump(self, *args, **kwargs):
        data = super().model_dump(*args, **kwargs)
        field_updates, measurements, observations, dropped_inputs = self._collect_emitted_outputs()

        data.update(field_updates)
        for field_name in dropped_inputs:
            data.pop(field_name, None)

        has_measurement_output = any(
            (spec.get("kind") == "measurement") or ("measurement" in out_field and spec.get("kind") is None)
            for out_field, spec in self.__transformations__.items()
        )
        has_observation_output = any(
            (spec.get("kind") == "observation") or ("observation" in out_field and spec.get("kind") is None)
            for out_field, spec in self.__transformations__.items()
        )

        if has_measurement_output:
            data["measurements"] = [m.model_dump() for m in measurements if m is not None]
        if has_observation_output:
            data["observations"] = [o.model_dump() for o in observations if o is not None]

        return data
    

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs):
        """Called automatically after Pydantic has finished initializing the subclass."""
        super().__pydantic_init_subclass__(**kwargs)

        emitted = getattr(cls, "__emitted_transforms__", {}) or {}
        base = getattr(cls, "__base_transformations__", {}) or {}
        emitted_transforms = {**base, **emitted}

        # Build measurement and observation transforms for declared fields
        measurement_transforms = {
            f"{name}_measurement": {
                "inputs": [name],
                "transform": (lambda p, field=field, src=name:
                              field.to_measurement(p, getattr(p, src, None))),
                "drop": field.drop,
                "kind": "measurement",
            }
            for name, field in measurement_mapper.fields.items()
            if name in cls.model_fields  
        }

        observation_transforms = {
            f"{name}_observation": {
                "inputs": [name],
                "transform": (lambda p, field=field, src=name:
                              field.to_observation(p, getattr(p, src, None))),
                "drop": field.drop,
                "kind": "observation",
            }
            for name, field in observation_mapper.fields.items()
            if name in cls.model_fields  
        }

        cls.__transformations__ = {
            **emitted_transforms,
            **measurement_transforms,
            **observation_transforms,
        }
