Run long operations through durable, inspectable jobs:

- execute indexing, fused search, grounded questions, model preparation, snippets, and actor overlays through versioned DBOS workflows
- use SQLite-backed local jobs and PostgreSQL-backed server jobs with the same typed progress, cancellation, retry, error, and result contracts
- pin search and actor-overlay work to the exact immutable snapshot selected at submission
- freeze descending job pages so concurrent submissions do not shift later cursors
- materialize actor-cluster summaries and advance actor-detection cursors without rescanning all retained detections for every page
- bind workflow recovery to the package version and implementation digest while keeping legacy atomic-search records readable and recoverable
- supervise detached local workers without creating a second job-state store
