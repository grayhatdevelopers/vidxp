Local execution processes now receive an explicit non-secret settings
projection through a one-use, owner-readable bootstrap file. HTTP
credentials are excluded from both inherited environment variables and
process arguments.

Worker hardening also adds:

- startup backoff after a failed launch
- live bounded rotation for repository-scoped worker logs
- cleanup of timed-out worker processes
- executable, build, provider and execution-configuration identity checks
  before reusing a worker
- child-side identity verification and DBOS environment isolation
- stale credential-bootstrap cleanup and an explicit local-worker stop command
- readiness checks against the owned local executor
- fail-fast API startup when the owned local executor cannot start
- preserved internal workflow tracebacks while public job errors remain typed
