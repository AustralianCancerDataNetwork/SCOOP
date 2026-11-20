# SCOOP

SCOOP is a Python library designed to generate synthetic oncology patient data conforming to the OMOP Common Data Model (CDM). It provides tools to create realistic patient profiles, including demographics, diagnoses, treatments, and outcomes, facilitating dataset alignment and software development for healthcare analytics without compromising patient privacy.
## Key Features
* HemOnc integration — realistic longitudinal roll-out of systemic therapy regimens.
* Configurable attributes — define demographic, clinical, and molecular distributions via YAML or code.
* CDM compatibility — outputs data natively aligned to OMOP tables and concept identifiers.
* Composable design — easily extend distributions, conditional logic, and transformation rules for new disease domains.

## Principles
The functionality of SCOOP should be considered in four broad phases:
1. **Sampling**: Drawing samples from defined statistical distributions to create individual patient attributes.
2. **Simulation**: Rules-based roll out of patient trajectories based on clinical logic and treatment regimens, aligned to generated attributes.
3. **Synthesis**: Applying defined transformations to the sampled attributes to generate the final patient data
4. **Serialisation**: Converting the generated patient data into a format compatible with the OMOP CDM for storage and analysis.


## Flow

1. a Person object is created with basic attributes (sampled from flat distributions - no dependencies)
2. a Condition object is created for that person, with attributes sampled conditionally based on the Person attributes
3. a Regimen object may optionally be created for that condition x person combination, sampled conditionally based on both Person and Condition attributes
4. exposure events (procedures, drug administration events) are created based on the Regimen object
5. treatment response and outcome events are created based on the Condition and Regimen objects
6. all objects are serialised to OMOP CDM tables

## Start here

Install the project dependencies with [uv](https://docs.astral.sh/uv/):

```bash
uv sync
```

## Choose a guide

- [Documentation home](docs/index.md): find the guide for your task.
- [Treatment schedules](docs/scheduling-rollout.md): understand dates, repeated cycles, optional treatment, and unresolved timing.
- [How treatment matching works](docs/hemonc_grouping_model.md): understand how a treatment decision connects to HemOnc regimen candidates.
- [Create a profile](docs/creating_new_profile.md): add or adapt a disease profile.
- [Validate profile YAML](schema/linkml/README.md): check the canonical authoring files.

## Profiles

Profiles are YAML files under [`scoop/profiles/`](scoop/profiles/). NSCLC is the most complete example of the current graph, treatment registry, intervention catalog, and routed-profile format. See [Create a profile](docs/creating_new_profile.md) before copying or extending one.

## Export a local synthetic cohort

`onco-scoop export-synthetic-cohort` samples a routed profile and writes a new, self-contained SQLite file with OMOP rows, the HemOnc snapshot, source-event links, and run metadata. It retains patients whose schedule cannot be resolved. Repeating courses use a finite cycle limit; fixed courses keep their source-defined end.

The source HemOnc and OMOP vocabulary tables are found through an [oa-configurator](https://pypi.org/project/oa-configurator/) config file. Both must live in the same physical database (one DuckDB file, or one PostgreSQL database). A DuckDB config looks like this; keep the path absolute:

```toml
[connections.hemonc_stack]
dialect = "duckdb"
database_name = "/absolute/path/to/scoop/data/hemonc-stack.duckdb"

[databases.hemonc_db]
kind = "generic"
connection = "hemonc_stack"
schema_name = "hemonc"

[databases.cdm_db]
kind = "cdm"
connection = "hemonc_stack"
cdm_schema = "cdm"
vocab_schema = "omop"

[tools.scoop]
hemonc_db = "hemonc_db"

[tools.hemonc_alchemy]
hemonc_db = "hemonc_db"
omop_db = "cdm_db"

[tools.omop_alchemy]
cdm_db = "cdm_db"
```

Install the locked runtime dependencies, point `OA_CONFIG_PATH` at the local
DuckDB config, set the HemOnc SQLite snapshot, then run:

```bash
uv sync --locked
export OA_CONFIG_PATH="$PWD/data/scoop.duckdb.toml"
export HEMONC_SNAPSHOT="$PWD/data/hemonc-snapshot.sqlite"
uv run onco-scoop export-synthetic-cohort \
  --profile nsclc \
  --count 10000 \
  --seed 0 \
  --max-cycles 35 \
  --output data/synthetic_nsclc_from_cli.sqlite
```

For PostgreSQL, point the connection at a `postgresql` database instead; the driver is already installed. The output must not already exist; the command builds a temporary file, checks SQLite integrity and foreign keys, then renames it into place. Use `--help` for all options.

Add `--quiet` to show only an in-place progress bar instead of progress messages and the JSON summary.

## Development

Install development dependencies and run the test suite:

```bash
uv sync --extra dev
uv run pytest -q
```
