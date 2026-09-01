# Deploy VidXP with Coolify

Use this guide to run VidXP as a server on one Coolify host. The deployment
provides an HTTPS API and MCP server, browser uploads, CPU video processing,
and persistent storage.

One deployed stack holds one VidXP repository. Run another complete stack with
separate databases and volumes when you need a separate repository. This setup
does not support GPU processing, multiple worker nodes, automatic failover, or
hosted replacements for its bundled databases.

## Before you start

Prepare:

- a Coolify host with enough CPU, memory, and storage for models, uploaded
  videos, indexes, and backups;
- an API hostname, such as `api.example.com`;
- an upload address, such as `https://uploads.example.com/uploads/`;
- one published VidXP release version or image digest; and
- either a private API token or an OIDC identity provider.

A private token works for applications that can send an `Authorization`
header. Hosted ChatGPT and similar connectors normally need OIDC so users can
authorize access through an identity provider.

The Compose file starts these services:

| Service | Responsibility |
|---|---|
| `api` | HTTP API and Streamable HTTP MCP |
| `worker` | Model preparation, indexing, search, and result generation |
| `tusd` | Resumable browser uploads |
| `hooks` | Private upload-completion handling |
| `postgres` | Application and job data |
| `chroma` | Search indexes |

PostgreSQL, Chroma, tusd, and the optional Ollama service are already pinned by
digest in `compose.coolify.yaml`. The Compose file uses published images and
does not build VidXP from the repository.

## 1. Configure images, storage secrets, and addresses

Add the following values to the Coolify deployment environment. When managing
the same Compose file locally, place them in an ignored `.env` file.

Replace every placeholder. Use a different random value of at least 32
characters for each secret.

```dotenv
# Use the same published release for both VidXP images.
VIDXP_CONTROL_IMAGE=ghcr.io/grayhatdevelopers/vidxp:<release>-control
VIDXP_WORKER_IMAGE=ghcr.io/grayhatdevelopers/vidxp:<release>-worker

# Keep every secret distinct.
POSTGRES_PASSWORD=<random-database-password>
VIDXP_ARTIFACT_DOWNLOAD_SECRET=<random-artifact-secret>
VIDXP_UPLOAD_CLEANUP_TOKEN=<random-cleanup-token>
VIDXP_UPLOAD_HANDOFF_SECRET=<random-handoff-secret>

# Replace the example hostnames with your public addresses.
VIDXP_PUBLIC_API_HOST=api.example.com
VIDXP_UPLOAD_PUBLIC_ENDPOINT=https://uploads.example.com/uploads/
VIDXP_UPLOAD_HANDOFF_PUBLIC_URL=https://api.example.com/upload-handoff
VIDXP_UPLOAD_CORS_ORIGIN_REGEX=^(https://api\.example\.com|https://app\.example\.com)$
VIDXP_MCP_MAX_RESOURCE_BYTES=16777216
```

The upload endpoint must end in `/uploads/`. The handoff address must end in
`/upload-handoff`.

`VIDXP_UPLOAD_CORS_ORIGIN_REGEX` lists the browser origins allowed to open an
upload session. Keep the parentheses, separate origins with `|`, and escape
each dot as `\.`. Add only exact HTTPS origins you control.

## 2. Choose authentication

### Private token

Use this mode for a private application that can store and send one API token:

```dotenv
VIDXP_HTTP_AUTH_MODE=static
VIDXP_HTTP_STATIC_BEARER_TOKEN=<random-private-api-token>
```

Treat the token as a password. Do not put it in a browser page, repository,
screenshot, or log.

### Hosted connector

Use OIDC for hosted ChatGPT or another client that signs users in through an
identity provider:

```dotenv
VIDXP_HTTP_AUTH_MODE=oidc
VIDXP_HTTP_STATIC_BEARER_TOKEN=
VIDXP_HTTP_OIDC_ISSUER=https://identity.example.com/
VIDXP_HTTP_OIDC_AUDIENCE=vidxp-api
VIDXP_HTTP_OIDC_JWKS_URL=https://identity.example.com/.well-known/jwks.json
VIDXP_HTTP_REQUIRED_SCOPES=["vidxp.read","vidxp.write"]
VIDXP_MCP_PUBLIC_URL=https://api.example.com/mcp
```

Replace the example identity-provider values. That provider must issue access
tokens with the configured issuer, audience, and scopes. VidXP validates those
tokens but does not provide user accounts or a login service.

Do not set the OIDC variables or `VIDXP_MCP_PUBLIC_URL` in private-token mode.

## 3. Publish the API and upload routes

Configure Coolify's proxy to publish only these destinations:

| Public route | Internal destination |
|---|---|
| API hostname, including `/mcp`, `/upload-handoff`, and `/artifact-download` | `api:8000` |
| Upload hostname `/uploads/` | `tusd:8080/uploads/` |

Do not publish PostgreSQL, Chroma, `hooks`, or Ollama.

For `/mcp`, preserve these request headers:

```text
Authorization
Accept
Content-Type
MCP-Protocol-Version
Mcp-Method
Mcp-Name
Mcp-Param-*
```

Disable response buffering for `/mcp` so clients receive streamed responses
without waiting for the complete message.

Upload and artifact links grant temporary access to one session or file.
Anyone who obtains the complete unexpired link can use it. Disable or redact
proxy access logs for `/uploads/` and `/upload-handoff`, and never rewrite a
public artifact link to an internal hostname.

## 4. Deploy and check the stack

After adding the environment and proxy routes, use Coolify's deploy action.

For a local Compose-managed check of the same file, run:

```bash
docker compose --env-file .env -f compose.coolify.yaml config --quiet
docker compose --env-file .env -f compose.coolify.yaml pull
docker compose --env-file .env -f compose.coolify.yaml up -d --wait
docker compose --env-file .env -f compose.coolify.yaml ps
```

The migration and readiness jobs should finish successfully. PostgreSQL, API,
hooks, worker, and tusd should report healthy. The completed `chroma-ready` job
confirms that Chroma is available.

Check the public addresses:

```text
https://api.example.com/health
https://api.example.com/ready
https://api.example.com/docs
https://api.example.com/mcp
```

`/ready` confirms that the server can accept requests. Model readiness is
checked separately after the next step.

## 5. Download worker models

VidXP does not download models during deployment. After the stack is healthy,
submit a model-preparation job through the authenticated API.

| Feature | Approximate download |
|---|---:|
| Speech search | 2.64 GiB |
| Scene search | 1.43 GiB |
| Action search | 0.93 GiB |
| Actor matching | 37 MiB |

The example below prepares every built-in search feature. In the shell running
the request, set `VIDXP_API_TOKEN` to the private API token configured above.
The API capability for multi-frame action and motion search is `action`.

```bash
curl --fail-with-body \
  --request POST \
  "https://api.example.com/api/v1/jobs/model-preparation" \
  --header "Authorization: Bearer ${VIDXP_API_TOKEN}" \
  --header "Idempotency-Key: initial-cpu-models-v1" \
  --header "Content-Type: application/json" \
  --data '{"modalities":["speech","scene","action","actor"],"capability_options":{}}'
```

The `202 Accepted` response includes a `job_id`. Insert it into the wait
request:

```bash
curl --fail-with-body \
  --header "Authorization: Bearer ${VIDXP_API_TOKEN}" \
  "https://api.example.com/api/v1/jobs/<job-id>/wait?timeout_seconds=30"
```

If another wait is needed, append
`&after_observation_token=<observation-token>` with the token from the previous
response. Once the job finishes, check `/api/v1/runtime/readiness`.

An authenticated MCP client can perform the same operation with
`prepare_models`, `wait_job`, and `get_job`.

## 6. Review upload limits

Start with the defaults unless the host has a smaller storage budget:

| Setting | Default |
|---|---:|
| `VIDXP_UPLOAD_MAX_BYTES` | 50 GiB per file |
| `VIDXP_UPLOAD_SESSION_MAX_FILES` | 10 files per session |
| `VIDXP_UPLOAD_SESSION_MAX_BYTES` | 100 GiB per session |
| `VIDXP_UPLOAD_QUOTA_BYTES` | 100 GiB reserved across the repository |
| `VIDXP_UPLOAD_SESSION_TTL_SECONDS` | 24 hours |

Override a value in the deployment environment when needed. The session byte
limit cannot be smaller than the per-file limit.

VidXP removes temporary access tokens from browser history after an upload or
download begins, but an upstream proxy can still leak a URL through its logs.
Keep the log restrictions from the proxy step in place.

`VIDXP_MCP_MAX_RESOURCE_BYTES` sets the largest file returned directly in an
MCP response. Larger videos use the resumable HTTPS artifact route instead of
being loaded completely into memory.

## 7. Optional generated answers

Search and timestamped evidence work without a language model. To let VidXP
plan searches and generate written claims from citable textual evidence,
configure the internal Ollama address:

```dotenv
VIDXP_SLM_BASE_URL=http://ollama:11434/v1
```

VidXP uses the official `qwen3.5:4b-q4_K_M` Ollama build by default. Set
`VIDXP_SLM_MODEL` only to make an intentional operator override.

Start Ollama and explicitly download the model:

```bash
docker compose --env-file .env -f compose.coolify.yaml --profile slm up -d ollama
docker compose --env-file .env -f compose.coolify.yaml --profile slm exec ollama \
  ollama pull qwen3.5:4b-q4_K_M
docker compose --env-file .env -f compose.coolify.yaml up -d worker
```

Do not publish Ollama through the proxy. Compose never downloads this model
automatically. The Q4_K_M artifact is approximately 3.4 GB and local inference
has no per-request API charge, but it consumes the server's storage, memory,
compute time, and electricity.

## Back up and upgrade

Before changing versions, back up PostgreSQL and these named volumes:

- `chroma-data`;
- `content-data`;
- `upload-quarantine`; and
- `model-cache`.

Keep the backup until imports, indexes, searches, uploads, and downloads work
on the new version.

To upgrade, change both VidXP image variables to the same published version,
pull the images, and deploy again. Do not mix control and worker versions. Wait
for the migration job to finish before treating the API and worker as ready.

The supported server uses the Compose service names `postgres` and `chroma`.
Do not set `VIDXP_DATABASE_URL` or `VIDXP_CHROMA_SERVER_URL`, and do not replace
these services with hosted alternatives in this topology.

## Validate a Compose change

This section is for contributors. Because `compose.coolify.yaml` requires
images, secrets, hostnames, and upload addresses, a bare
`docker compose config` command fails.

Validate with a complete environment that is not committed:

```bash
docker compose --env-file /path/to/vidxp.env \
  -f compose.coolify.yaml config --quiet
```

This command validates configuration only. Before merging a deployment change,
also test the affected image pull, migration, health check, model preparation,
upload, artifact download, persistent data, upgrade, and rollback paths on a
real deployment.
