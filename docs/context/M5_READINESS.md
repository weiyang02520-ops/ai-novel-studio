# M5 Readiness Interfaces

- `ContextItem` records source, type, priority, text size, token estimate and optional revision.
- `collect_project_context` safely selects project, rules, outline, named character/world and optional memory sources.
- FACT_SOURCE outranks DERIVED_MEMORY; memory never overwrites canonical material.
- `collect_recent_chapters(project, count, max_chars)` is explicit and strictly bounded; default collection includes no chapter body.
- Character and world lookup supports safe slug or exact H1 display name.
- A future Writer must receive a separate, explicitly authorized draft tool boundary.
- `MutationService` manages knowledge files; it is not a Writer draft service.
- M5 remains **NOT AUTHORIZED**.
