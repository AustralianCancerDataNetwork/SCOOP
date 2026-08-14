# SCOOP Docs

This folder is for project-facing documentation that explains how to extend SCOOP's profile system.

Current pages:

- [Creating A New Profile](./creating_new_profile.md)
- [HemOnc Grouping Model](./hemonc_grouping_model.md)
- [Lung Profile Config Map](./lung_profile_config_map.md)

The project currently has its most developed example under [`scoop/profiles/nsclc/`](../scoop/profiles/nsclc), where the NSCLC work shows the current pattern:

- stage-specific transformation graphs
- a regimen registry
- supporting exploratory notebooks for HemOnc-backed regimen discovery
- local source guideline PDFs used to anchor the design
