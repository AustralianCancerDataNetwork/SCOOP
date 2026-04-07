# HemOnc Grouping Model

This page explains the different grouping layers SCOOP now uses for HemOnc-backed treatment selection.

The goal is to make it clear:

1. What each grouping concept means.
2. How it is expressed in configuration or code.
3. How it is turned into query expressions or assembled treatment options.

This is a conceptual map of the current production structure, including both the
variant-query path and the now-executable bundle path for non-variant
scenarios.

## Why There Are Multiple Grouping Layers

SCOOP has to support more than one kind of treatment selection problem.

Some treatment categories can be retrieved directly from HemOnc variants:

- targeted therapy families
- platinum doublets
- ICI monotherapy
- supportive-care-like variant groups

Other categories are not naturally one HemOnc variant:

- radiotherapy-only scenarios
- surgery plus adjuvant therapy
- sequential CRT
- concurrent CRT
- CRT plus adjuvant durvalumab or osimertinib

Because of that, SCOOP uses two parallel structures:

- a `variant` query path
- a `sig_bundle` recipe path

The variant path is already executable end to end.

The bundle path is executable end to end, using a lightweight assembly model:

- phase candidates are fetched per phase
- filtering may be applied in Python or SQL, depending on requirement complexity
- bundle compatibility is enforced with pragmatic heuristics rather than a deeply clinical sequencing engine

## The Grouping Stack

From smallest to largest, the current grouping concepts are:

1. Condition set
2. Component term
3. Component list
4. Category restriction
5. HemOnc query group
6. Component-query resolver
7. Composite resolver
8. Sig component phase
9. Bundle phase
10. HemOnc bundle group
11. Bundle-recipe resolver
12. Assembled treatment bundle
13. Registry entry

Each level is described below.

## 1. Condition Set

### Definition

A condition set is the disease-context filter for a query or bundle phase.

It answers:

"Which HemOnc conditions should this treatment definition apply to?"

### How It Is Expressed

In the registry YAML:

```yaml
condition_sets:
  lung_nsclc_all:
    - 30824
    - 30898
    - 154361
```

For variant-backed queries, condition sets become:

```python
Variants.study_objects.any(Studies.condition_cui.in_(condition_cuis))
```
For sig-component phases, the candidate condition set is selected:

```python
select(Sigs).where(Sigs.component.ilike(...))
```

and the returned rows are then filtered in Python using discoverable condition metadata such as:

- `sig.condition_objects`
- `sig.study_objects`
- `sig.variant_context`

## 2. Component Term

### Definition

A component term is one search token such as:

- `osimertinib`
- `Platinum agent`
- `Immunostimulatory monoclonal antibody`
- `External beam radiotherapy`
- `Surgery`

It is the smallest user-facing matching unit.

### How It Is Expressed

In variant query config:

```yaml
component_lists:
  - [osimertinib]
```

In bundle-phase config:

```yaml
component_terms: [External beam radiotherapy]
```

For variant queries, a component term is expanded across multiple searchable string columns:

- `canmed_major_class`
- `canmed_minor_class`
- `main_class`
- `component`

For sig-component phases:

- only `Sigs.component.ilike(term)` is used

## 3. Component List

### Definition

A component list is an OR-group of acceptable component terms.

For example:

```yaml
- [cisplatin, carboplatin]
```

means:

"match at least one of these"

In `component_lists`, each inner list is one OR-group.

Multiple component lists are combined with AND semantics.

So:

```yaml
component_lists:
  - [Platinum agent]
  - [pembrolizumab, durvalumab]
```

means:

- at least one platinum-agent match
- and at least one ICI match

Each component list becomes a grouped `HAVING` expression requiring at least one matching component CUI in the variant’s categorized components.

## 4. Category Restriction

### Definition

A category restriction constrains how many components of a broad category may appear in a variant.

Supported categories are:

- `chemotherapy`
- `radiotherapy`
- `immunotherapy`
- `surgery`
- `supportive`

Supported operators are:

- `exact`
- `min`

### How It Is Expressed

Example:

```yaml
category_restrictions:
  chemotherapy:
    type: exact
    amount: 2
  immunotherapy:
    type: min
    amount: 1
```

The variant query path first classifies components into broad categories, then applies count constraints in SQL `HAVING`.

For example:

- `exact: 2` becomes `category_count == 2`
- `min: 1` becomes `category_count >= 1`

## 5. HemOnc Query Group

### Definition

A HemOnc query group is a reusable named definition for a variant-backed candidate family.

It answers:

"What set of HemOnc variants belong to this clinical regimen family?"

### How It Is Expressed

In `hemonc_groups`:

```yaml
hemonc_groups:
  egfr_intervention:
    basis: variant
    condition_set: lung_nsclc_all
    component_lists:
      - [osimertinib, gefitinib, erlotinib, afatinib, dacomitinib]
    category_restrictions:
      chemotherapy:
        type: min
        amount: 1
```

`RegimenRegistry.resolve_group_query()` materializes this into a `ResolvedHemOncQuery`.

That resolved query can then be turned into:

- a variant CUI subquery
- a `Variants` SQLAlchemy statement
- executed variant results

## 6. Component-Query Resolver

### Definition

A component-query resolver is the link from a registry entry to a variant-backed HemOnc query.

It says:

"This treatment category is retrieved directly from HemOnc variants using these query requirements."

### How It Is Expressed

Either by reference:

```yaml
resolver:
  type: component_query
  group_id: egfr_intervention
```

Or inline:

```yaml
resolver:
  type: component_query
  default_condition_cuis: [642]
  component_lists:
    - [atezolizumab]
```

The resolver is turned into a `ResolvedHemOncQuery` by `RegimenRegistry.resolve_hemonc_query()`.


## 7. Composite Resolver

### Definition

A composite resolver is a union of registry categories.

It answers:

"This public treatment endpoint is a menu composed of several helper categories."

### How It Is Expressed

```yaml
resolver:
  type: composite
  members:
    - ptdb_nonsquamous
    - single_chemo_nonsquamous
    - atezolizumab
```

It is not directly compiled into one SQL expression. Instead, it is a semantic grouping that can be expanded by downstream logic.

## 8. Sig Component Phase

### Definition

A sig component phase is the simplest non-variant bundle phase.

It directly selects HemOnc sig rows by component name.

Typical examples:

- surgery phase
- radiotherapy phase

### How It Is Expressed

Inside a bundle recipe:

```yaml
- phase_id: radiotherapy
  phase_type: radiotherapy
  timing: post
  source_type: sig_component
  component_terms: [External beam radiotherapy]
```

It becomes a coarse SQLAlchemy `select(Sigs)` statement filtered by:

- `Sigs.component.ilike(...)`

and then a Python-side compatibility filter that keeps rows whose condition
metadata overlaps the phase condition set, while failing open when no usable
condition metadata is exposed.

## 9. Bundle Phase

### Definition

A bundle phase is one ordered piece of a non-variant treatment recipe.

It answers:

"What kind of treatment happens in this part of the bundle, and where do its candidates come from?"

Supported phase sources currently are:

- `group`
- `entry`
- `sig_component`

Supported phase types currently are:

- `systemic`
- `radiotherapy`
- `surgery`
- `supportive`

Supported timings currently are:

- `standalone`
- `pre`
- `concurrent`
- `post`

### How It Is Expressed

Example:

```yaml
- phase_id: systemic
  phase_type: systemic
  timing: concurrent
  source_type: group
  source_group_id: nsclc_crt_backbone
```

During registry resolution:

- `group` phases become embedded `ResolvedHemOncQuery` objects
- `entry` phases become embedded `ResolvedHemOncQuery` objects by resolving the referenced entry
- `sig_component` phases retain component-term matching information

## 10. HemOnc Bundle Group

### Definition

A HemOnc bundle group is a reusable named non-variant treatment recipe.

It answers:

"How should this treatment category be assembled from systemic and/or locoregional phases?"

### How It Is Expressed

In `hemonc_bundle_groups`:

```yaml
hemonc_bundle_groups:
  sequential_crt:
    basis: sig_bundle
    assembly_mode: variant_plus_rt
    sequence_template: sequential
    condition_set: lung_nsclc_all
    phases:
      - phase_id: systemic
        phase_type: systemic
        timing: pre
        source_type: group
        source_group_id: nsclc_crt_backbone
      - phase_id: radiotherapy
        phase_type: radiotherapy
        timing: post
        source_type: sig_component
        component_terms: [External beam radiotherapy]
```

`RegimenRegistry.resolve_hemonc_bundle()` turns it into a `ResolvedHemOncBundle`.

`build_bundle_query_artifacts()` compiles each phase into a phase-specific SQL
statement, and `execute_bundle_query()` executes those statements and assembles
final bundle candidates.

So the bundle path is currently:

- declaratively expressive
- queryable phase by phase
- executable end to end through runtime assembly

## 11. Bundle-Recipe Resolver

### Definition

A bundle-recipe resolver is the link from a registry entry to a non-variant bundle recipe.

It says:

"This treatment endpoint should be built as an assembled sig bundle rather than retrieved as one variant query."

### How It Is Expressed

```yaml
resolver:
  type: bundle_recipe
  group_id: sequential_crt
```

The resolver is turned into a `ResolvedHemOncBundle` by `RegimenRegistry.resolve_hemonc_bundle()`.

From there, the system can:

- inspect phases
- compile phase SQL
- execute bundle phases
- assemble final bundle candidates
- carry bundle metadata into treatment assignment

## 12. Assembled Treatment Bundle

### Definition

An assembled treatment bundle is the runtime object representing one candidate non-variant treatment option.

It is the production analogue of the exploratory `SigBundle` concept.

### How It Is Expressed

It is not written directly in YAML.

It is produced by runtime assembly from a `ResolvedHemOncBundle` plus actual phase results.

- `AssembledBundlePhase`
- `AssembledTreatmentBundle`

It does not become SQL itself.

Instead, it is produced after SQL results are gathered for each phase.

Current assembly is implemented by `execute_bundle_query()` plus
`assemble_bundle_matches()`, which together:

- execute phase-level SQL statements
- apply Python-side phase filtering
- takes phase result lists
- forms Cartesian products across phases
- apply lightweight cross-phase compatibility rules
- creates assembled bundle objects
- deduplicates identical phase combinations

### Important Note

This is an executable assembly path, which is intentionally
lightweight rather than a full clinical reasoning engine.

It does enforce:

- bundle execution from a resolved recipe
- Python-side phase filtering by condition context when metadata is available
- basic study-sharing checks for systemic plus radiotherapy combinations
- allowance for post-CRT adjuvant phases to come from distinct evidence
- deduplication by assembled phase signatures

It does not yet fully enforce:

- rich regimen-identity deduplication beyond phase signatures
- strong concurrent vs sequential semantics derived from HemOnc evidence alone
- more advanced clinical compatibility rules across all multi-phase scenarios

## 13. Registry Entry

### Definition

A registry entry is the canonical public or internal treatment category.

It is the stable contract between:

- graph endpoints
- HemOnc retrieval logic
- downstream treatment assignment

### How It Is Expressed

In `entries`:

```yaml
entries:
  concurrent_crt:
    public: true
    clinical_group: unresectable_stage_iii
    resolver:
      type: bundle_recipe
      group_id: concurrent_crt
```

### How It Becomes An Expression

The registry entry itself is not a SQL expression.

It delegates to one of the resolver types:

- `component_query`
- `bundle_recipe`
- `composite`
- `none`

## Current Expression Paths

There are currently two main expression pipelines.

### Variant Query Pipeline

`condition_set` -> `component_lists` + `category_restrictions` -> `ResolvedHemOncQuery` -> SQLAlchemy `Variants` query -> executable results

This is the most complete path today.

### Bundle Recipe Pipeline

`condition_set` + ordered `phases` -> `ResolvedHemOncBundle` -> one SQLAlchemy statement per phase -> Python-side phase filtering -> runtime assembly into `AssembledTreatmentBundle`

This is the newer path.

It is suitable for:

- describing non-variant treatment structures
- compiling the component parts of those structures
- executing those component parts
- assembling bundle candidates
- surfacing bundle metadata in treatment assignment

It is not complete for:

- distinguishing sequential vs concurrent scenarios through query semantics alone
- expressing richer clinical compatibility logic than the current heuristic checks

## Current Mapping Between Old And New Concepts

The exploratory notebooks used these major concepts:

- `ComponentReqs`
- `ScenarioYaml`
- `TreatmentScenario`
- `SigBundle`
- `TreatmentGroup`
- `combinations`

## Practical Rules Of Thumb

Use:

- `component_query` when one HemOnc variant family already captures the treatment category
- `bundle_recipe` when the category needs ordered systemic/RT/surgery assembly
- `composite` when the category is a union of helper categories
- `none` when the category is real but not yet mapped

Use `sig_component` phases for:

- surgery
- RT-only components
- other direct sig-level additions that are not naturally variant-backed

Use `group` phases for:

- systemic families already expressible as variant queries
- adjuvant additions like durvalumab or osimertinib
- CRT backbones that should stay query-driven

## Known Gaps

The current production structure is intentionally cleaner than the exploratory
version, but a few capabilities are still incomplete.

### Sequence semantics gap

`concurrent` and `sequential` are currently expressed as phase metadata, not enforced through query logic.

### Sig-phase matching gap

Variant queries can match on classes and component strings, while sig-component phases currently only match on `Sigs.component`.

### Composite-inside-bundles gap

Bundle phases cannot yet directly reference a composite entry or perform scenario-union logic (previously `combinations` feature).
