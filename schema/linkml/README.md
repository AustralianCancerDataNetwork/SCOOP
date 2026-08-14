# LinkML regimen specification

`regimen_spec.yaml` is the human-readable schema for the canonical target regimen
specification. It is deliberately separate from the runtime Pydantic implementation in
`scoop/protocols/spec.py`.

The current registry files use a few source-oriented spellings:

- the profile name is the outer YAML key rather than a `label` field;
- `conditions` becomes `default_conditions` in the typed model;
- `phase_combinations` becomes `union_phases`;
- phase and protocol references are keyed mappings rather than explicit objects; and
- component alternatives are currently represented as nested lists.

The LinkML shape makes those concepts explicit. The adapter between the current YAML
spelling and this canonical shape belongs in the specification-loading layer. This schema
does not introduce a second clinical model, and it does not validate the legacy bladder or
SCLC registry formats.

## Validation

From the repository root:

```bash
uv run --extra authoring python schema/linkml/validate.py
```

Pass one or more authoring files to validate them instead of the canonical example:

```bash
uv run --extra authoring python schema/linkml/validate.py path/to/registry.yaml
```

The `authoring` extra contains the LinkML validator and runtime packages. LinkML is
intentionally not part of SCOOP's runtime dependencies.

The schema covers structural constraints and vocabulary. Runtime rules such as resolving
phase references, applying category defaults, and resolving condition names through a
gateway remain enforced by `RegistrySpec.from_path()` and its tests.
