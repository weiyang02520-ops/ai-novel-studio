# M5 Implemented Interfaces

- `ContextItem` records source, type, priority, text size, token estimate and optional revision.
- `collect_project_context` safely selects project, rules, outline, named character/world and optional memory sources.
- FACT_SOURCE outranks DERIVED_MEMORY; memory never overwrites canonical material.
- `collect_recent_chapters(project, count, max_chars)` is explicit and strictly bounded; default collection includes no chapter body.
- Character and world lookup supports safe slug or exact H1 display name.
- Writer uses a separate AI DraftService boundary and never calls the manual draft API.
- `MutationService` manages knowledge files; it is not a Writer draft service.
- Dynamic ContextBudget, Writer TaskCard, streaming partial/resume, rewrite and continue are implemented.
- M5 Writer outputs only `origin=ai, status=draft`; M6 remains **NOT AUTHORIZED**.
