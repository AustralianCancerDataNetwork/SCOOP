# Validate profile YAML

The LinkML schemas check the structure of SCOOP's canonical treatment registry, intervention catalog, and profile manifest. They are authoring tools; LinkML is not needed to run SCOOP.

From the repository root, install the optional authoring dependencies:

```bash
uv sync --extra authoring
```

Validate the checked-in NSCLC files:

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

To validate another file, replace the final path in the matching command. Use the corresponding schema and target class for that file type.

## What validation checks

Schema validation catches missing fields, invalid value types, and unsupported YAML structure. SCOOP's runtime loaders also check references between phases, protocols, interventions, and graph endpoints, and confirm that profile paths resolve. A file can pass schema validation and still fail runtime checks.

See [Create or adapt a disease profile](../../docs/creating_new_profile.md) for the authoring workflow.
