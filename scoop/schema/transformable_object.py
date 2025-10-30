from pydantic import BaseModel
from typing import Callable
from ..utils import measurement_mapper, observation_mapper


class TransformableBase(BaseModel):
    """Base class providing declarative field transformations on dump."""
    __transformations__: dict[str, dict] = {}
    __base_transformations__: dict[str, dict] = {}

    def model_dump(self, *args, **kwargs):
        data = super().model_dump(*args, **kwargs)
        measurements = []
        observations = []

        for out_field, spec in self.__transformations__.items():
            fn: Callable = spec["transform"]
            drop: bool = spec.get("drop", False)
            # Compute new value
            val = fn(self)
            if 'measurement' in out_field:
                measurements.append(val)
            elif 'observation' in out_field:
                observations.append(val)
            else:   
                data[out_field] = val
            # Optionally remove inputs
            if drop:
                for f in spec.get("inputs", []):
                    data.pop(f, None)

            data['measurements'] = [m for m in measurements if m is not None]
            data['observations'] = [o for o in observations if o is not None]

        return data
    

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs):
        """Called automatically after Pydantic has finished initializing the subclass."""
        super().__pydantic_init_subclass__(**kwargs)

        # Get any base (non-measurement) transforms from subclass
        base = getattr(cls, "__base_transformations__", {}) or {}

        # Build measurement and observation transforms for declared fields

        measurement_transforms = {
            f"{name}_measurement": {
                "inputs": [name],
                "transform": (lambda p, field=field, src=name:
                              field.to_measurement(p, getattr(p, src, None))),
                "drop": True,
            }
            for name, field in measurement_mapper.fields.items()
            if name in cls.model_fields  
        }


        observation_transforms = {
            f"{name}_observation": {
                "inputs": [name],
                "transform": (lambda p, field=field, src=name:
                              field.to_observation(p, getattr(p, src, None))),
                "drop": True,
            }
            for name, field in observation_mapper.fields.items()
            if name in cls.model_fields  
        }

        cls.__transformations__ = {**base, **measurement_transforms, **observation_transforms}