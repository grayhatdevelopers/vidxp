Add durable background jobs:

- run indexing, snippets, actor overlays, and model preparation through DBOS workflows
- use the same typed job, progress, cancellation, and result contracts locally and on servers
- persist local jobs in SQLite and support PostgreSQL-backed worker deployments
- keep CPU and GPU work on explicit queues with concurrency controlled by DBOS
- supervise the detached local worker without creating a second job-state store
