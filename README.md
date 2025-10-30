# SCOOP: Synthetic Creation of OMOP Oncology Patients

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