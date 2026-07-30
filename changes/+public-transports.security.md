Protect public HTTP and MCP transports:

- support constant-time static bearer authentication or cached asymmetric OIDC/JWKS validation with exact issuer, audience, algorithm, HTTPS, and unsafe-URL-character checks
- enforce repository read, write, and admin scopes after authentication
- declare bearer authentication in protected OpenAPI and keep authenticated schemas private
- enforce independent trusted Host, Origin/CORS, request-size, query-length, actor-identifier, and streamed-upload bounds
- return typed public errors with correlation IDs while retaining internal workflow tracebacks
- derive opaque repository-, subject-, and operation-scoped idempotency keys for retries
- deliver authorized media and artifacts through confined handles with checksum ETags and range responses, without exposing storage keys or filesystem paths
