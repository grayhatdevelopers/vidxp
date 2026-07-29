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
VIDXP_HTTP_STATIC_BEARER_TOKEN=<random-secret-at-least-32-characters>
VIDXP_UPLOAD_CLEANUP_TOKEN=<different-random-secret-at-least-32-characters>
VIDXP_PUBLIC_API_HOST=api.example.com
VIDXP_UPLOAD_PUBLIC_ENDPOINT=https://uploads.example.com/uploads/
VIDXP_UPLOAD_CORS_ORIGIN_REGEX=^https://app\.example\.com$
```

Optional deployment-wide upload limits use `VIDXP_UPLOAD_MAX_BYTES` for one object
and `VIDXP_UPLOAD_QUOTA_BYTES` for all reserved upload bytes in the singleton
repository. There is no per-principal or per-repository quota setting.

Route the API service's port 8000 to the API hostname. Route only tusd's
`/uploads/` path on port 8080 to the upload hostname. Do not publish PostgreSQL,
Chroma, or the hook service.

The same API origin exposes Streamable HTTP MCP at `/mcp`. Configure the proxy
to preserve `Authorization`, `Accept`, `Content-Type`, `MCP-Protocol-Version`,
`Mcp-Method`, `Mcp-Name`, and `Mcp-Param-*` headers and disable response buffering
for `/mcp`. Static bearer mode intentionally publishes no OAuth metadata; configure
the bearer header in the remote MCP client.

The upload path is a capability URL used to resume an upload. Disable or redact
reverse-proxy access logging for `/uploads/`; VidXP cannot control logs written by
an upstream proxy.

Video bytes do not travel through MCP. Create and resume the upload through the
HTTP/tus endpoints, wait for its `media_id`, and then use that ID with
`start_indexing`.

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
