"""Visualization helpers for exporting SCOOP model view data."""

import importlib

__all__ = [
    "BundleRecord",
    "EdgeRecord",
    "GraphRecord",
    "GroupRecord",
    "GraphNodeRecord",
    "ProfileRecord",
    "RegistryEntryRecord",
    "ResolverRecord",
    "SampleRecord",
    "SampleStepRecord",
    "ViewModel",
    "build_view_model",
]


_LAZY_EXPORTS = {
    "BundleRecord": "BundleRecord",
    "EdgeRecord": "EdgeRecord",
    "GraphRecord": "GraphRecord",
    "GroupRecord": "GroupRecord",
    "GraphNodeRecord": "GraphNodeRecord",
    "ProfileRecord": "ProfileRecord",
    "RegistryEntryRecord": "RegistryEntryRecord",
    "ResolverRecord": "ResolverRecord",
    "SampleRecord": "SampleRecord",
    "SampleStepRecord": "SampleStepRecord",
    "ViewModel": "ViewModel",
    "build_view_model": "build_view_model",
}


def __getattr__(name: str):
    try:
        attribute_name = _LAZY_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module '{__name__}' has no attribute '{name}'") from exc

    module = importlib.import_module(".model_export", __name__)
    value = getattr(module, attribute_name)
    globals()[name] = value
    return value
