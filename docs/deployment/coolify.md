# Coolify deployment

`compose.coolify.yaml` is a prebuilt-image Compose deployment. It has no build
contexts and does not download models in the API process.

## Images

- `VIDXP_CONTROL_IMAGE`: the VidXP `control` target for the API, private tusd
  hooks, MCP server, readiness checks, and migrations. It contains no PyTorch or
  Chroma server.
- `VIDXP_WORKER_IMAGE`: the VidXP `worker` target with CPU capabilities and
  Chroma's HTTP-only client.
- PostgreSQL 18.3, Chroma 1.5.9, and tusd 2.10.0 are pinned by digest in the
  Compose file.

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

## Start and verify

```bash
docker compose -f compose.coolify.yaml pull
docker compose -f compose.coolify.yaml up -d --wait
docker compose -f compose.coolify.yaml ps
```

The migration and readiness containers should exit successfully. PostgreSQL, API,
hooks, worker, and tusd should report healthy; Chroma is checked by the completed
`chroma-ready` gate.

This profile is intentionally single-node. Its named content, upload, model, and
Chroma volumes are not a multi-replica storage design. Back up the PostgreSQL and
named data volumes before replacing a release.
