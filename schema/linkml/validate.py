"""Validate canonical regimen specifications with the LinkML runtime.

This is authoring tooling rather than a runtime SCOOP dependency. Run it from the
repository root with::

    uv run --extra authoring python schema/linkml/validate.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml


SCHEMA_PATH = Path(__file__).with_name("regimen_spec.yaml")
EXAMPLE_PATH = Path(__file__).parent / "examples" / "nsclc_registry.yaml"


def validate_registry(
    data_path: Path,
    *,
    schema_path: Path = SCHEMA_PATH,
    target_class: str = "RegistrySpec",
) -> Any:
    """Validate one YAML document and return LinkML's validation report."""

    try:
        from linkml.validator import validate
    except ImportError as exc:  # pragma: no cover - depends on the optional extra
        raise RuntimeError(
            "LinkML authoring dependencies are not installed; run "
            "`uv sync --extra authoring` first."
        ) from exc

    with data_path.open(encoding="utf-8") as stream:
        instance = yaml.safe_load(stream)

    return validate(instance, str(schema_path), target_class=target_class)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate canonical SCOOP regimen YAML with LinkML."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="YAML files to validate (defaults to the canonical NSCLC example).",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=SCHEMA_PATH,
        help=f"LinkML schema path (default: {SCHEMA_PATH}).",
    )
    parser.add_argument(
        "--target-class",
        default="RegistrySpec",
        help="LinkML target class (default: RegistrySpec).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    paths = args.paths or [EXAMPLE_PATH]
    failures = 0

    for path in paths:
        try:
            report = validate_registry(
                path,
                schema_path=args.schema,
                target_class=args.target_class,
            )
        except (OSError, RuntimeError, yaml.YAMLError) as exc:
            print(f"invalid: {path}: {exc}", file=sys.stderr)
            failures += 1
            continue

        if report.results:
            print(f"invalid: {path}", file=sys.stderr)
            for result in report.results:
                print(f"  - {result.message}", file=sys.stderr)
            failures += 1
        else:
            print(f"valid: {path}")

    return failures


if __name__ == "__main__":
    raise SystemExit(main())
