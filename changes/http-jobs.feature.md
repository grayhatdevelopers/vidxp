Expose durable work through the HTTP control plane:

- submit indexing, artifact generation, and model preparation only through the
  existing DBOS job service
- derive workflow IDs from opaque repository-, subject-, and operation-scoped
  idempotency keys
- make HTTP retries idempotent while preserving the original validated job
  request
