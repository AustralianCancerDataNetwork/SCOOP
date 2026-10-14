from __future__ import annotations
from importlib.resources import files
from pathlib import Path

# SCOOP profile/resource paths and path utilities
#
# Resolved via importlib.resources rather than Path(__file__).parents[N]: for a
# regular (non-namespace) installed package this still yields a concrete
# pathlib.Path to the installed `scoop` directory, but it does so through the
# standard resource-location API instead of hardcoded parent-directory arithmetic
# that assumes a flat unpacked source tree.
CONFIG_ROOT = Path(files("scoop"))
PROFILES_DIRNAME = Path("profiles")
MAPPERS_DIRNAME = Path("vocabulary") / "resources"
PROFILES_ROOT = CONFIG_ROOT / PROFILES_DIRNAME
MAPPERS_ROOT = CONFIG_ROOT / MAPPERS_DIRNAME
# SCOOP configuration artifact filenames
PROFILE_MANIFEST_NAME = "profile.yaml"
CONCEPT_MAP_FILENAME = "concept_maps.yaml"
MEASUREMENT_MAP_FILENAME = "measurement_mappers.yaml"
OBSERVATION_MAP_FILENAME = "observation_mappers.yaml"
CONDITION_MAP_FILENAME = "condition_mappers.yaml"
MODIFIER_MAP_FILENAME = "modifier_mappers.yaml"
EPISODE_MAP_FILENAME = "episode_mappers.yaml"
# SCOOP configuration artifact paths
CONCEPT_MAP_PATH = MAPPERS_ROOT / CONCEPT_MAP_FILENAME
MEASUREMENT_MAP_PATH = MAPPERS_ROOT / MEASUREMENT_MAP_FILENAME
OBSERVATION_MAP_PATH = MAPPERS_ROOT / OBSERVATION_MAP_FILENAME
CONDITION_MAP_PATH = MAPPERS_ROOT / CONDITION_MAP_FILENAME
MODIFIER_MAP_PATH = MAPPERS_ROOT / MODIFIER_MAP_FILENAME
EPISODE_MAP_PATH = MAPPERS_ROOT / EPISODE_MAP_FILENAME
# Default relative paths for profile mappers (relative to profile directory)
DEFAULT_PROFILE_CONCEPT_MAP_RELPATH = MAPPERS_DIRNAME / CONCEPT_MAP_FILENAME
DEFAULT_PROFILE_MEASUREMENT_MAP_RELPATH = (
    MAPPERS_DIRNAME / MEASUREMENT_MAP_FILENAME
)
DEFAULT_PROFILE_OBSERVATION_MAP_RELPATH = (
    MAPPERS_DIRNAME / OBSERVATION_MAP_FILENAME
)

def resolve_config_root(base_dir: Path | None = None) -> Path:
    return Path(base_dir) if base_dir is not None else CONFIG_ROOT

def profile_artifact_relpath(profile_id: str, relpath: str | Path) -> Path:
    return PROFILES_DIRNAME / profile_id / Path(relpath)

def profile_manifest_path(
    profile_id: str,
    base_dir: Path | None = None,
) -> Path:
    return resolve_config_root(base_dir) / PROFILES_DIRNAME / profile_id / PROFILE_MANIFEST_NAME
