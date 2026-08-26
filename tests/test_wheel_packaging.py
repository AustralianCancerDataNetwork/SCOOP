"""Installed-wheel release-asset smoke test.

Source-checkout tests can pass while an installed package still fails to find
its own operational YAML, because `scoop/profiles/**/*.yaml` and
`scoop/vocabulary/resources/*.yaml` live in plain data directories (no
`__init__.py`) that setuptools does not package by default. This is the
release-asset gate called for by the 2026-08-15 migration code review
(Finding F1): it builds the current tree into a wheel, installs it into an
isolated environment, and proves that profile discovery, NSCLC registry
loading, and vocabulary mapper resources all work from the *installed*
location rather than the source tree.

A wheel's own metadata cannot carry `[tool.uv.sources]` path/editable
overrides (those are project-level dev config, not part of a built
distribution's dependency list), so installing the wheel directly with its
declared dependencies cannot resolve the private `hemonc-import` source. This
test instead installs the project's already-resolved dependency set via
`uv export` (which does flatten local/editable sources correctly), then
overlays the just-built wheel with `--no-deps --force-reinstall` so `scoop`
itself is guaranteed to come from the wheel, not the source tree or an
editable install.

This still needs the project's real dependencies to be resolvable (the local
`hemonc-import` path, and `oa-configurator`/`omop-alchemy` from PyPI) and is
skipped, rather than failed, when that isn't the case (e.g. a sandbox missing
the sibling `hemonc_import` checkout), so it does not block unrelated work.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

SMOKE_SCRIPT = textwrap.dedent(
    """
    from scoop.profiles.manifest import list_profiles
    from scoop.profiles.paths import CONFIG_ROOT, PROFILES_ROOT
    from scoop.protocols.classification import BroadCategory, ComponentFact, classify
    from scoop.protocols.spec import RegistrySpec
    from scoop.vocabulary.gateway import DefaultVocabularyGateway

    # Prove this is the installed copy, not the source checkout.
    assert "site-packages" in str(CONFIG_ROOT), f"not an installed location: {CONFIG_ROOT}"
    assert PROFILES_ROOT.is_dir(), f"missing profiles dir: {PROFILES_ROOT}"

    profiles = list_profiles()
    assert "nsclc" in profiles, profiles

    registry_path = PROFILES_ROOT / "nsclc" / "nsclc_protocol_registry.yaml"
    registry = RegistrySpec.from_path(registry_path)
    assert registry.source_phases, "canonical NSCLC registry loaded with no phases"

    # No database, no live session: mapper construction must still find its
    # shipped YAML resources under the installed package.
    gateway = DefaultVocabularyGateway(CONFIG_ROOT, engine_factory=lambda: None)
    mappers = gateway.get_mappers()
    assert mappers.concept_mapper is not None

    # Same class of gap as the profile/vocabulary YAML above: the classifier's
    # rule YAML lives under scoop/protocols/classification/rules and must
    # ship in the wheel too.
    result = classify(ComponentFact(canmed_major_class="Alkylating agent"))
    assert result.category == BroadCategory.CHEMOTHERAPY, result

    print("WHEEL_SMOKE_OK")
    """
)


def _uv_available() -> bool:
    return shutil.which("uv") is not None


@pytest.mark.skipif(not _uv_available(), reason="uv is required to build/install the wheel")
def test_installed_wheel_loads_profiles_and_vocabulary_resources(tmp_path):
    dist_dir = tmp_path / "dist"
    build = subprocess.run(
        ["uv", "build", "--wheel", "-o", str(dist_dir)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if build.returncode != 0:
        pytest.skip(f"could not build wheel in this environment:\n{build.stderr}")

    wheels = sorted(dist_dir.glob("*.whl"))
    assert wheels, "uv build produced no wheel"
    wheel_path = wheels[-1]

    # Export the project's real, already-resolved runtime dependencies
    # (excluding the root project itself) so the isolated environment gets
    # the same hemonc-import/oa-configurator/omop-alchemy the project uses,
    # without re-deriving them from the wheel's own (source-agnostic) metadata.
    requirements_path = tmp_path / "requirements.txt"
    export = subprocess.run(
        [
            "uv", "export", "--no-hashes", "--no-emit-project",
            "-o", str(requirements_path),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if export.returncode != 0:
        pytest.skip(f"could not export locked dependencies:\n{export.stderr}")

    env_dir = tmp_path / "venv"
    # Match the interpreter already running this test (this project's own
    # dev environment), rather than whatever `uv venv` would pick as a
    # system default -- a newer interpreter than the project targets may
    # lack wheels for some of these dependencies on this platform.
    venv_create = subprocess.run(
        ["uv", "venv", "--python", sys.executable, str(env_dir)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert venv_create.returncode == 0, venv_create.stderr
    python = env_dir / "bin" / "python"

    install_deps = subprocess.run(
        ["uv", "pip", "install", "--python", str(python), "-r", str(requirements_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if install_deps.returncode != 0:
        pytest.skip(
            "could not install the project's real dependencies in this "
            f"environment:\n{install_deps.stderr}"
        )

    install_wheel = subprocess.run(
        [
            "uv", "pip", "install",
            "--python", str(python),
            "--no-deps", "--force-reinstall",
            str(wheel_path),
        ],
        capture_output=True,
        text=True,
    )
    assert install_wheel.returncode == 0, install_wheel.stderr

    result = subprocess.run(
        [str(python), "-c", SMOKE_SCRIPT],
        cwd=tmp_path,  # not repo root: prove it isn't reading the source tree
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"installed-wheel smoke script failed:\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "WHEEL_SMOKE_OK" in result.stdout
