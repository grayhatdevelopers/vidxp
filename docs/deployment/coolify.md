# Coolify deployment

`compose.coolify.yaml` is a prebuilt-image Compose deployment. It has no build
contexts and does not download models in the API process.

## Images

- `VIDXP_CONTROL_IMAGE`: the VidXP `control` target for the API, private tusd
  hooks, MCP server, readiness checks, and migrations. It contains no PyTorch or
  Chroma server.
- `VIDXP_WORKER_IMAGE`: the VidXP `worker` target with CPU capabilities and
  Chroma's HTTP-only client.
- PostgreSQL 18.3, Chroma 1.5.9, tusd 2.10.0, and the optional self-hosted
  Ollama 0.32.5 service are pinned by digest in the Compose file.

Use immutable VidXP release tags or digests for both first-party image variables.

## Required variables

```dotenv
VIDXP_CONTROL_IMAGE=ghcr.io/grayhatdevelopers/vidxp:<release>-control
VIDXP_WORKER_IMAGE=ghcr.io/grayhatdevelopers/vidxp:<release>-worker
POSTGRES_PASSWORD=<random-secret>
VIDXP_HTTP_AUTH_MODE=static
VIDXP_HTTP_STATIC_BEARER_TOKEN=<random-secret-at-least-32-characters>
VIDXP_ARTIFACT_DOWNLOAD_SECRET=<artifact-download-secret-at-least-32-characters>
VIDXP_UPLOAD_CLEANUP_TOKEN=<different-random-secret-at-least-32-characters>
VIDXP_PUBLIC_API_HOST=api.example.com
VIDXP_UPLOAD_PUBLIC_ENDPOINT=https://uploads.example.com/uploads/
VIDXP_UPLOAD_HANDOFF_PUBLIC_URL=https://api.example.com/upload-handoff
VIDXP_UPLOAD_HANDOFF_SECRET=<third-random-secret-at-least-32-characters>
VIDXP_UPLOAD_CORS_ORIGIN_REGEX=^(https://api\.example\.com|https://app\.example\.com)$
VIDXP_MCP_MAX_RESOURCE_BYTES=16777216
```

The values above select private, single-tenant static bearer authentication.
For hosted ChatGPT or Claude connectors, configure the existing OIDC resource
server instead:

```dotenv
VIDXP_HTTP_AUTH_MODE=oidc
VIDXP_HTTP_STATIC_BEARER_TOKEN=
VIDXP_HTTP_OIDC_ISSUER=https://identity.example.com/
VIDXP_HTTP_OIDC_AUDIENCE=vidxp-api
VIDXP_HTTP_OIDC_JWKS_URL=https://identity.example.com/.well-known/jwks.json
VIDXP_HTTP_REQUIRED_SCOPES=["vidxp.read","vidxp.write"]
VIDXP_MCP_PUBLIC_URL=https://api.example.com/mcp
```

The OIDC provider must issue access tokens and scopes accepted by the intended
host. VidXP validates issuer, audience/resource, signature, expiry, and scopes;
it does not implement an identity provider. Leave every `VIDXP_HTTP_OIDC_*`
value and `VIDXP_MCP_PUBLIC_URL` unset in static mode.

Compose derives `VIDXP_ARTIFACT_DOWNLOAD_PUBLIC_URL` as
`https://${VIDXP_PUBLIC_API_HOST}/artifact-download`. The API issues 15-minute
links by default (`VIDXP_ARTIFACT_DOWNLOAD_TTL_SECONDS=900`); deployments may
set a value from 60 seconds through 24 hours. Keep the artifact-download secret
distinct from API, upload, and cleanup credentials.

`VIDXP_MCP_MAX_RESOURCE_BYTES` bounds every in-memory MCP resource read. Keep
video delivery on the range-capable HTTPS artifact route; local stdio callers
can use the verified local path. If neither projection is available, oversized
resources fail with structured remediation instead of being read into memory.

After `create_clip` completes, `get_artifact_download` returns a native MCP
resource when the artifact fits `VIDXP_MCP_MAX_RESOURCE_BYTES`; oversized
artifacts use the configured HTTPS download projection. Its bearer capability is
carried in the URL fragment, exchanged
for a `Secure`, `HttpOnly`, `SameSite=Strict` cookie, and removed from browser
history before the content request. The public route requires neither an API
token nor browser login; possession of the complete unexpired link is authority
to download that one repository-bound MP4 or MKV. GET, HEAD, ranges, ETag, and
resume requests remain valid until expiry. Redact fragments in client-side
telemetry and do not rewrite the public URL to an internal service hostname.

`VIDXP_UPLOAD_HANDOFF_PUBLIC_URL` must be the externally reachable HTTPS API
URL ending exactly in `/upload-handoff`. Keep its secret distinct from the MCP
bearer and upload-cleanup credentials. `create_media_upload` returns the upload
session link as ordinary MCP structured and text output; VidXP does not use native
URL elicitation. Its fragment contains a short-lived capability, and possession of
the complete link authorizes the browser session. Treat it as a bearer secret; the
page removes the fragment from browser history after bootstrap.

The CORS value intentionally accepts only this grouped list of exact HTTPS
origins with escaped dots; HTTP is accepted only for loopback development. VidXP
validates that restricted syntax instead of
using Python's broader regex dialect, and tusd evaluates the same value with
Go's RE2 engine.

Upload policy defaults are 50 GiB per file (`VIDXP_UPLOAD_MAX_BYTES`), 10 files per
session (`VIDXP_UPLOAD_SESSION_MAX_FILES`), 100 GiB per session
(`VIDXP_UPLOAD_SESSION_MAX_BYTES`), a 24-hour session lifetime
(`VIDXP_UPLOAD_SESSION_TTL_SECONDS`), and 100 GiB of repository-wide reserved bytes
(`VIDXP_UPLOAD_QUOTA_BYTES`). The session byte limit must be at least the per-file
limit. VidXP enforces file count, per-file size, aggregate session size, and
repository quota atomically when the browser selects each file. There is no
per-principal quota setting.

Selection failures use stable API error codes and actionable messages:

- `upload_file_too_large`: the selected file exceeds the per-file limit.
- `upload_session_file_limit`: the session reached its file-count limit.
- `upload_session_byte_limit`: the selection would exceed aggregate bytes.
- `upload_quota_exceeded`: the repository reservation would exceed quota.
- `upload_client_key_conflict`: a stable client key was replayed with different
  metadata.
- `upload_session_closed` or `upload_session_expired`: request a new session or
  continue only already-authorized transfers as appropriate.

Invalid filenames, non-positive sizes, malformed MIME types, or client keys that
do not match the documented safe character set are rejected by request validation
before quota is reserved or an intent is created.

Route the API service's port 8000 to the API hostname. Route only tusd's
`/uploads/` path on port 8080 to the upload hostname. Do not publish PostgreSQL,
Chroma, or the hook service.

The same API origin exposes Streamable HTTP MCP at `/mcp`. Configure the proxy
to preserve `Authorization`, `Accept`, `Content-Type`, `MCP-Protocol-Version`,
`Mcp-Method`, `Mcp-Name`, and `Mcp-Param-*` headers and disable response buffering
for `/mcp`. Static bearer mode intentionally publishes no OAuth metadata and is
only for clients that can set a private bearer header. OIDC mode publishes the
MCP protected-resource metadata used by hosted connector authentication.

Remote MCP request handling is stateless. Upload progress and child lifecycle are
stored durably outside the transport, so this workflow needs neither an in-memory
MCP session timeout nor sticky routing by `Mcp-Session-Id`.

The upload path is a capability URL used to resume an upload. Disable or redact
reverse-proxy access logging for `/uploads/`; VidXP cannot control logs written by
an upstream proxy.

Video bytes do not travel through MCP. A remote MCP client calls
`create_media_upload` with an idempotency key and gives the returned HTTPS session
to the user. Uppy Dashboard supports multiple selections, pause, resume, retry,
accessible controls, and browser recovery. The browser supplies the actual metadata
after selection; VidXP creates and reserves each child atomically. Automatic
indexing defaults to the deployed repository's advertised capability set. The
client polls only `get_media_upload` until each child is searchable (or failed);
`modalities` can narrow the set and `index_after_import=false` is the explicit
registration-only opt-out.

The page exchanges its fragment capability for an `HttpOnly`, `Secure`,
`SameSite=Strict` session cookie without a login or manual API-token field. Each
file receives a separate one-time, five-minute tus creation grant; the initiating
MCP bearer never enters the page or tusd. Keep
access logging disabled or redacted for `/uploads/`, because the tus resume URL
is itself a bearer capability. The page's Content Security Policy permits only
self-hosted scripts and stylesheets, the style attributes Uppy Dashboard needs
for dimensions/progress/transitions, and connections to the configured tus
origin. Inline scripts remain blocked, so both public URLs must be correct before
startup.

## Prepare worker models

Start the stack, then explicitly prepare the model set before accepting
indexing requests. Submit preparation through the authenticated API so the
durable job executes on the worker and writes to the shared model volume:

| Selected models | Maximum pinned download |
|---|---:|
| Dialogue + scene + actor | Approximately 4.11 GiB |

The request below is the operator's explicit authorization for those
downloads. Ensure the `model-cache` volume has enough additional capacity:

```bash
curl --fail-with-body \
  --request POST \
  "https://${VIDXP_PUBLIC_API_HOST}/api/v1/jobs/model-preparation" \
  --header "Authorization: Bearer ${VIDXP_HTTP_STATIC_BEARER_TOKEN}" \
  --header "Idempotency-Key: initial-cpu-models-v1" \
  --header "Content-Type: application/json" \
  --data '{"modalities":["dialogue","scene","actor"],"capability_options":{}}'
```

The `202 Accepted` response contains the durable `job_id` and a `Location`
header. Poll that location until the job succeeds:

```bash
curl --fail-with-body \
  --header "Authorization: Bearer ${VIDXP_HTTP_STATIC_BEARER_TOKEN}" \
  "https://${VIDXP_PUBLIC_API_HOST}/api/v1/jobs/<job-id>"
```

The Streamable HTTP MCP `prepare_models` and `get_job` tools expose the same
operation for an authenticated agent client. Check
`/api/v1/runtime/readiness` afterward; `/ready` covers control-plane
availability and does not claim that every optional model is prepared.

## Optional grounded query model

Grounded retrieval works without a language model and returns timestamped
evidence. To enable generated claims, choose a model only after evaluating its
schema adherence, resource use, license, and grounding behavior. Set both:

```dotenv
VIDXP_SLM_BASE_URL=http://ollama:11434/v1
VIDXP_SLM_MODEL=<evaluated-local-model>
```

Then start the optional service and explicitly prepare the selected model:

```bash
docker compose -f compose.coolify.yaml --profile slm up -d ollama
docker compose -f compose.coolify.yaml --profile slm exec ollama \
  ollama pull <evaluated-local-model>
docker compose -f compose.coolify.yaml up -d worker
```

The Compose deployment never pulls an SLM implicitly. Ollama is internal and
should not be published through the proxy.

## Start and verify

```bash
docker compose -f compose.coolify.yaml pull
docker compose -f compose.coolify.yaml up -d --wait
docker compose -f compose.coolify.yaml ps
```

The migration and readiness containers should exit successfully. PostgreSQL, API,
hooks, worker, and tusd should report healthy; Chroma is checked by the completed
`chroma-ready` gate.

This is the supported server topology: one node, one API/MCP service, one hook
service, one CPU worker, and the bundled PostgreSQL, Chroma, tusd, and named
volumes. It is not a multi-replica, failover, or provider-portability design. Back
up PostgreSQL and the named data volumes before replacing a release.

Treat each deployed stack as its singleton repository boundary. The current
PostgreSQL catalog and Chroma collections intentionally contain no repository
namespace. Deploy another complete stack, with separate databases and volumes, for
a separate repository.

VidXP server mode connects to the internal Compose service names `postgres` and
`chroma`. Those endpoints are fixed by the supported topology: do not set
`VIDXP_DATABASE_URL` or `VIDXP_CHROMA_SERVER_URL`, and do not substitute hosted
PostgreSQL, hosted Chroma, or alternative database providers.
