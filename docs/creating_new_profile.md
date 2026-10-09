# Create or adapt a disease profile

A SCOOP profile brings together a patient pathway, treatment decisions, and the files that connect those decisions to source treatment candidates. Build the clinical pathway first, then add treatment mappings only where the source data supports them.

## Files in a profile

Profiles live in `scoop/profiles/<profile_id>/`:

| File | Purpose |
| --- | --- |
| `profile.yaml` | Names the profile's graphs, pathways, treatment registry, intervention catalog, and vocabulary maps. |
| Graph YAML files | Describe patient and disease fields, their distributions, and the decisions reached along each pathway. |
| `*_protocol_registry.yaml` | Defines HemOnc-backed treatment phases and protocols. |
| `*_interventions.yaml` | Connects treatment decision endpoints to phases, protocols, alternatives, recipes, or non-HemOnc outcomes. |

For a working example, open the [NSCLC profile folder](../scoop/profiles/nsclc/). The [treatment matching guide](hemonc_grouping_model.md) explains how its registry and intervention catalog fit together.

## A practical authoring sequence

1. **Outline the clinical pathway.** Record the source guideline, relevant patient fields, branch conditions, and treatment endpoints. Keep the endpoint names stable and understandable; they connect graph decisions to treatment mappings.
2. **Write and review the graph.** Add only fields needed for the decisions being modeled. Keep clinical source references close to the design review, and have the pathway reviewed by an appropriate domain expert.
3. **Define supported treatment phases.** For each endpoint, decide whether a HemOnc phase or multi-phase protocol can represent it. Set condition context and candidate requirements. Leave a mapping unresolved when the source data does not support a defensible match.
4. **Connect endpoints to interventions.** Map graph treatment endpoints to phases, protocols, explicit alternatives, recipes, or non-HemOnc outcomes. Declare patient context fields that are required to resolve a decision.
5. **Register the files and validate them.** Add the graph paths, treatment files, and pathway routes to `profile.yaml`, then validate the canonical YAML as described in [Validate profile YAML](../schema/linkml/README.md).

## Review candidate requirements

Treatment requirements are filters for finding source candidates. They are not a full clinical eligibility model. Before accepting a mapping, review:

- whether the condition context is specific enough;
- whether required components and broad categories describe the intended treatment;
- whether each alternative is truly optional, rather than a combination;
- whether a multi-phase protocol or recipe preserves the intended order; and
- what the profile does when required patient context or source data is missing.

Do not broaden requirements simply to make an endpoint return candidates. A visible unresolved mapping is more informative than an unsupported match.

## Validate and exercise the profile

Install the optional authoring tools and run the canonical validation commands from the [schema guide](../schema/linkml/README.md). Structural validation checks that the YAML conforms to the authoring schemas. Runtime loading also checks references between files, graph endpoint coverage, and profile paths.

Before sharing a profile, exercise representative pathways with fixed random seeds and review both the selected endpoint and its candidate treatment records. This needs the vocabulary and HemOnc data sources configured for the environment. Check edge cases such as missing context, no matching candidates, alternative interventions, and unresolved schedule timing.

