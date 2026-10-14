from pathlib import Path

from scoop.visualization.model_export import build_view_model
from scoop.visualization.regimen_structure_export import build_regimen_structure


def test_visualization_exports_load_typed_profile_resources():
    root = Path(__file__).parents[1] / "scoop"

    structure = build_regimen_structure(base_dir=root)
    view = build_view_model(base_dir=root)

    assert {profile["id"] for profile in structure["profiles"]} == {
        "bladder",
        "nsclc",
        "sclc",
    }
    assert {profile.id for profile in view.profiles} == {
        "bladder",
        "nsclc",
        "sclc",
    }
    assert any(entry.profile_id == "nsclc" for entry in view.registry_entries)
    assert any(group.profile_id == "nsclc" for group in view.groups)
