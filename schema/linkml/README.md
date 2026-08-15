# LinkML authoring specifications

`regimen_spec.yaml` is the human-readable schema for the canonical target regimen
specification. It is deliberately separate from the runtime Pydantic implementation in
`scoop/protocols/spec.py`.

The authoring schemas are intentionally separated by concept:

- `regimen_spec.yaml` defines canonical treatment phases and protocols;
- `intervention_spec.yaml` defines decision-to-treatment intervention mappings; and
- `profile_manifest.yaml` defines the graph, registry, intervention, and vocabulary files
  that compose a disease profile.

The legacy bladder and SCLC registry files still use a few source-oriented spellings:

- the profile name is the outer YAML key rather than a `label` field;
- `conditions` becomes `default_conditions` in the typed model;
- `phase_combinations` becomes `union_phases`;
- phase and protocol references are keyed mappings rather than explicit objects; and
- component alternatives are currently represented as nested lists.

The NSCLC registry, intervention catalog, and profile manifest now use the canonical
shapes directly. The adapters remain relevant only to legacy profile migration; they are
not part of the NSCLC loading path. These schemas do not introduce a second clinical
model, and the legacy bladder and SCLC registry formats remain outside the regimen schema
until those profiles are migrated.

## Validation

From the repository root:

```bash
uv run --extra authoring python schema/linkml/validate.py
```

Pass one or more authoring files to validate them instead of the canonical example:

```bash
uv run --extra authoring python schema/linkml/validate.py path/to/registry.yaml
```

Validate the intervention catalog example:

```bash
uv run --extra authoring python schema/linkml/validate.py \
  --schema schema/linkml/intervention_spec.yaml \
  --target-class InterventionCatalog \
  schema/linkml/examples/nsclc_interventions.yaml
```

Validate the profile-manifest example:

```bash
uv run --extra authoring python schema/linkml/validate.py \
  --schema schema/linkml/profile_manifest.yaml \
  --target-class ProfileDocument \
  schema/linkml/examples/nsclc_profile.yaml
```

The checked-in NSCLC authoring set can be validated directly:

```bash
uv run --extra authoring python schema/linkml/validate.py \
  --schema schema/linkml/regimen_spec.yaml \
  --target-class RegistrySpec \
  scoop/profiles/nsclc/nsclc_protocol_registry.yaml

uv run --extra authoring python schema/linkml/validate.py \
  --schema schema/linkml/intervention_spec.yaml \
  --target-class InterventionCatalog \
  scoop/profiles/nsclc/nsclc_interventions.yaml

uv run --extra authoring python schema/linkml/validate.py \
  --schema schema/linkml/profile_manifest.yaml \
  --target-class ProfileDocument \
  scoop/profiles/nsclc/profile.yaml
```

The `authoring` extra contains the LinkML validator and runtime packages. LinkML is
intentionally not part of SCOOP's runtime dependencies.

The schemas cover structural constraints and vocabulary. Runtime rules such as resolving
phase/protocol references, intervention graph coverage, profile path resolution, and
resolving condition names through a gateway remain enforced by the runtime loaders and
their tests.
