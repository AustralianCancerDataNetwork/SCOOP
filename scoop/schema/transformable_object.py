from pydantic import BaseModel
from typing import Callable

class TransformableBase(BaseModel):
    """Base class providing declarative field transformations on dump."""
    __transformations__: dict[str, dict] = {}

    def model_dump(self, *args, **kwargs):
        data = super().model_dump(*args, **kwargs)

        for out_field, spec in self.__transformations__.items():
            fn: Callable = spec["transform"]
            drop: bool = spec.get("drop", False)
            # Compute new value
            data[out_field] = fn(self)
            # Optionally remove inputs
            if drop:
                for f in spec.get("inputs", []):
                    data.pop(f, None)

        return data
