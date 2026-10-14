"""Preflight profile regimen components against the configured OMOP vocabulary."""

from __future__ import annotations

import argparse
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from hemonc_alchemy.toolkit.analytics.treatment.classification import (
    RAD_SIG_CLASS_VALUE,
    sig_class_value,
)
from sqlalchemy.orm import Session

from ...vocabulary.gateway import VocabularyGateway
from ..candidates import RegimenCandidate
from ..spec import PhaseSpec, RegistrySpec
from .gateway import HemOncGateway, SqlHemOncGateway


@dataclass(frozen=True)
class ComponentLintIssue:
    name: str
    hemonc_cui: int | None
    phases: tuple[str, ...]
    reason: str


def _component_key(sig: object) -> tuple[str, int | None, str] | None:
    name = str(getattr(sig, "component", "") or "").strip()
    if not name:
        return None
    cui_value = getattr(sig, "component_cui", None)
    cui = int(cui_value) if cui_value is not None else None
    domain = "procedure" if sig_class_value(sig) == RAD_SIG_CLASS_VALUE else "drug"
    return name, cui, domain


def _phase_component_uses(
    phase_id: str,
    phase: PhaseSpec,
    gateway: HemOncGateway,
) -> dict[tuple[str, int | None, str], set[str]]:
    uses: dict[tuple[str, int | None, str], set[str]] = defaultdict(set)
    for candidate in gateway.candidates_for_phase(phase):
        if not isinstance(candidate, RegimenCandidate):
            candidate = RegimenCandidate.model_validate(candidate)
        for sig in candidate.sigs:
            key = _component_key(sig)
            if key is not None:
                uses[key].add(phase_id)
    return uses


def _component_uses(
    registry: RegistrySpec,
    hemonc_gateway: HemOncGateway,
) -> dict[tuple[str, int | None, str], set[str]]:
    phases_by_component: dict[tuple[str, int | None, str], set[str]] = defaultdict(set)
    for phase_id, phase in registry.source_phases.items():
        for key, phase_ids in _phase_component_uses(
            phase_id, phase, hemonc_gateway
        ).items():
            phases_by_component[key].update(phase_ids)
    return phases_by_component


def _unmapped_components(
    uses: dict[tuple[str, int | None, str], set[str]],
    vocabulary_gateway: VocabularyGateway,
) -> tuple[ComponentLintIssue, ...]:
    issues: list[ComponentLintIssue] = []
    for (name, cui, domain), phases in sorted(uses.items()):
        try:
            vocabulary_gateway.resolve_component_concept(domain, name, cui)
        except LookupError as exc:
            issues.append(
                ComponentLintIssue(
                    name=name,
                    hemonc_cui=cui,
                    phases=tuple(sorted(phases)),
                    reason=str(exc),
                )
            )
    return tuple(issues)


def lint_profile_components(
    registry: RegistrySpec,
    hemonc_gateway: HemOncGateway,
    vocabulary_gateway: VocabularyGateway,
) -> tuple[ComponentLintIssue, ...]:
    """Return candidate components that cannot resolve through the OMOP gateway."""

    return _unmapped_components(
        _component_uses(registry, hemonc_gateway), vocabulary_gateway
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check all candidate components in a profile against OMOP vocabulary."
    )
    parser.add_argument("--profile", default="nsclc")
    args = parser.parse_args(argv)
    from ...config import create_hemonc_engine, get_hemonc_context
    from ...profiles.manifest import load_profile

    context = load_profile(args.profile)
    if context.registry_spec is None:
        parser.error(f"Profile {args.profile!r} has no regimen registry")
    _, resolved = get_hemonc_context()
    engine = create_hemonc_engine(resolved)
    try:
        with Session(engine) as session:
            gateway = SqlHemOncGateway(session=session)
            issues = lint_profile_components(
                context.registry_spec, gateway, context.vocabulary_gateway
            )
    finally:
        engine.dispose()
    for issue in issues:
        print(
            f"UNMAPPABLE: {issue.name} (HemOnc CUI {issue.hemonc_cui}); "
            f"phases={','.join(issue.phases)}; {issue.reason}"
        )
    print(f"Checked profile {args.profile}: {len(issues)} unmappable components.")
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
