<p align="center">
   <a href ="https://github.com/grayhatdevelopers/vidxp">
      <img src="./docs/images/logo.png" alt="logo" width="200">
   </a>
</p>

<h1 align="center">VidXP</h1>

<p align="center">
  <em>Search video by what was said, what appeared on screen, and recurring faces.</em>
</p>

<div align="center">
  <p>
    VidXP is a local-first video indexing and search engine distributed as a Python
    package. 
  </p>

</div>
<hr/>
  <br/>You can use it:
  <ul style="display:inline-block; text-align:left;">
    <li>From the command line</li>
    <li>Through its browser interface</li>
    <li>As an indexing and retrieval layer inside another application</li>
    <li>Through the HTTP API or MCP, with a desktop interface on the roadmap</li>
  </ul>

<p align="center">
  <strong>Dialogue search · Scene search · Actor grouping · CLI · Browser UI · Python API</strong>
</p>

<p align="center">
  <a href="https://pypi.org/project/vidxp/">
    <img src="https://img.shields.io/pypi/v/vidxp" alt="PyPI version">
  </a>
  <a href="https://pypi.org/project/vidxp/">
    <img src="https://img.shields.io/pypi/pyversions/vidxp" alt="Supported Python versions">
  </a>
  <a href="https://github.com/grayhatdevelopers/vidxp/actions/workflows/ci.yml?query=branch%3Amain">
    <img src="https://github.com/grayhatdevelopers/vidxp/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI status">
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/github/license/grayhatdevelopers/vidxp" alt="MIT license">
  </a>
  <a href="https://grayhat.studio/discord">
    <img src="https://img.shields.io/discord/867124708473700363?logo=discord&logoColor=white" alt="Discord">
  </a>
</p>

## Why VidXP

Finding one moment in a video should not require scrubbing through the entire
timeline. VidXP builds a searchable index from three kinds of evidence:

- **Dialogue:** semantic search over timestamped transcripts.
- **Scenes:** text-to-frame search.
- **Actors:** groups similar detected faces and exports a highlighted video for a selected cluster.

After the required model weights are available, video processing and search run completely locally, for your privacy and security.

Some ideas on how to use VidXP:
- Use it as way to find your favorite relatives in a huge folder of wedding videos (been there, done that)
- Use it in your application, allow users to search videos (an idea: use it alongside a video-editing application)
- Use it as an "understanding" layer so your LLM / agent can understand videos

[![Video Screenshot](./docs/images/video-screenshot.jpeg)](https://www.linkedin.com/feed/update/urn:li:activity:7343569473720725505/)


## Current capabilities

| Capability | Available now | Result |
|---|---|---|
| Dialogue search | Transcription, word alignment, semantic phrase indexing | Matching video time |
| Scene search | Text search over sampled video frames | Matching frame and time |
| Actor grouping | Within-video face detection and clustering | Clustered detections and highlighted output video |
| Interfaces | Typer CLI, Streamlit browser interface, Python and HTTP APIs | Interactive or programmatic use |
| Index management | Saved progress, ready/failed state, cancellation, isolated programmatic runs | Traceable and reusable indexes |

## Quick start

VidXP supports Python 3.11 through 3.14 and requires FFmpeg for media
processing. See the [installation guide](INSTALLATION_GUIDE.md) for
platform-specific local-worker installation, model preparation, and
troubleshooting.

Install the lightweight command line in an isolated environment with
[pipx](https://packaging.python.org/en/latest/guides/installing-stand-alone-command-line-tools/).

```bash
pipx install vidxp
```

For a local CPU worker and browser UI, a source checkout is the strictest
cross-platform path because the lock routes only Torch through its official CPU
index on Linux and Windows:

```bash
uv sync --frozen --extra local-worker --extra frontend
uv run vidxp doctor
```

On Apple Silicon macOS 14+, the published package can be installed directly
because PyPI provides the native CPU/MPS Torch wheel:

```bash
pipx install "vidxp[local-worker,frontend]"
```

Linux and Windows published installs require a staged CPU Torch install; plain
`pipx install "vidxp[local-worker]"` can otherwise resolve PyPI's CUDA-enabled
Linux Torch build. The exact commands are in the
[installation guide](INSTALLATION_GUIDE.md#install-a-published-local-worker).

Confirm the package and prepare the pinned models:

```bash
vidxp --version
vidxp doctor
vidxp prepare
```

## Index and search

Build an index containing dialogue, scene, and actor information:

```bash
vidxp media import samplevideo.mp4 --json
vidxp index create <media-id>
```

The import command copies and validates the video in managed local storage and
returns its stable `media_id`. Indexing, search results, and generated artifacts
use that ID instead of exposing repository file paths.

Search the completed index:

```bash
vidxp search dialogue "the bread just came out of the oven"
vidxp search scene "a yellow taxi on a city street" --top-k 5
vidxp actors list
vidxp actors render <cluster-id> --json
vidxp artifacts snippet <media-id> 30 45 --json
vidxp artifacts show <artifact-id>
```

Index only selected capabilities or sample fewer visual frames:

```bash
vidxp index create <media-id> --modality scene --frame-stride 5
```

Repeat `--modality` to combine `dialogue`, `scene`, and `actor`.
Indexing, artifact rendering, and model preparation run as durable background
jobs. Commands wait by default; add `--detach` to return after queueing, then
inspect or control the job separately:

```bash
vidxp jobs list
vidxp jobs show <job-id>
vidxp jobs cancel <job-id>
vidxp jobs retry <job-id>
```

Run `vidxp --help` or any command followed by `--help` for the complete command
reference.

Use named repositories to keep index locations and devices centrally
configured:

```bash
vidxp repositories add team --index-dir ./indexes/team --device cuda --use
vidxp repositories list
```

## Browser interface

Install the `frontend` extra and start:

```bash
vidxp ui
```

The command uses the active named repository, starts a local Streamlit server,
and remains active until stopped.
The interface can upload a video, start or cancel indexing, restore saved
progress after a page reload, and search the capabilities available in the
completed index.

## HTTP API

Install the server profile and run the app factory:

```bash
uv sync --frozen --extra server
uv run vidxp-api
```

The local default binds to `127.0.0.1:8000` without network authentication.
Its OpenAPI document and interactive reference are available at
`/openapi.json` and `/docs`.

The API process is a model-free control plane. Under `/api/v1`, it exposes
bounded media import and delivery, capability metadata, index status, durable
job submission and control, artifact delivery, and authenticated readiness.
Indexing and other model work execute only through DBOS workers. Remote search
is submitted as a durable job so model work never runs in the API/MCP process.

Large remote media uses an upload intent plus the deployment profile's `tusd`
service. Create the intent at `/api/v1/media/uploads`, then use the returned
creation URL and `Upload-Metadata` with a normal tus client. Poll the intent URL
until it supplies a `media_id`. The multipart compatibility endpoint remains
capped at 256 MiB. Media imports, upload intents, job creation, and job retries
require an opaque `Idempotency-Key`; keys are scoped to the stable repository
identity, authenticated subject, and operation.

```bash
curl -F "upload=@samplevideo.mp4" \
  -H "Idempotency-Key: import-samplevideo-2026-07-28" \
  http://127.0.0.1:8000/api/v1/media
curl -X POST http://127.0.0.1:8000/api/v1/jobs/index \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: 12345678-1234-4234-8123-123456789abc" \
  -d '{"media_id":"<media-id>","modalities":["scene"]}'
```

Non-loopback and server-mode deployments must configure static bearer or OIDC
authentication. Static mode requires a token of at least 32 characters. OIDC
mode validates a fixed issuer, audience, asymmetric algorithm allowlist, JWKS
URL, token lifetime, subject, and configured baseline scopes through PyJWT.
Routes then enforce repository-wide `vidxp.read`, `vidxp.write`, or
`vidxp.admin` authorization. These are shared-repository permissions, not
per-media ownership rules.

```text
VIDXP_MODE=server
VIDXP_RUNTIME_BACKEND=cpu
VIDXP_DATABASE_URL=postgresql://...
VIDXP_HTTP_BIND_HOST=0.0.0.0
VIDXP_HTTP_AUTH_MODE=static
VIDXP_HTTP_STATIC_BEARER_TOKEN=<random-secret-of-at-least-32-characters>
VIDXP_HTTP_TRUSTED_HOSTS=["api.example.com"]
VIDXP_MCP_ALLOWED_HOSTS=["api.example.com"]
```

Use `Authorization: Bearer <token>` for every route except `/health` and
`/ready`. Configure `VIDXP_HTTP_ALLOWED_ORIGINS` only for browser origins that
must call `/api/*`; MCP has its own Host and Origin policy. Authenticated profiles
keep the interactive `/docs` UI disabled, while the protected `/openapi.json`
contract remains available to authenticated clients.

## MCP

The server profile exposes a curated Streamable HTTP endpoint at `/mcp`. Its
tools list capabilities, inspect index state, submit idempotent indexing and
search jobs, poll jobs, and explicitly cancel jobs. The MCP adapter calls the
same application and durable-job services as the CLI and API; it does not mirror
OpenAPI or call the HTTP API internally.

Remote video bytes still use HTTP/tus. Upload and resume the video, wait for a
`media_id`, and then pass that ID to `start_indexing`. MCP never carries video
bytes, base64 video, or server file paths.

Static deployments use the configured bearer token as an MCP request header.
OIDC deployments must also set the canonical resource URL:

```text
VIDXP_MCP_PUBLIC_URL=https://api.example.com/mcp
VIDXP_MCP_ALLOWED_HOSTS=["api.example.com"]
VIDXP_MCP_ALLOWED_ORIGINS=[]
```

For adjacent local agents, install the MCP extra and use stdio:

```bash
pipx install "vidxp[all,mcp]"
vidxp-mcp --repository default
```

The stdio process owns its local repository/job lifecycle and exits cleanly when
the client closes the transport.

## Container

Stable releases are available from GitHub Container Registry. Start the local
interface with persistent index and model storage by running:

```bash
docker compose up
```

See the [installation guide](INSTALLATION_GUIDE.md#run-the-container) for model
preparation, configuration, and direct `docker run` usage.

For the prebuilt API/worker deployment, see the
[Coolify deployment guide](docs/deployment/coolify.md).

## Use VidXP as a Python package

The programmatic API supports isolated multi-video runs, supplied timestamped
transcripts, resumable per-video checkpoints, and metadata-rich top-k results.

```python
from vidxp.core import IndexConfig, VideoSource
from vidxp.core.runner import run_index
from vidxp.capabilities.scene.operations import search_scene

config = IndexConfig(
    dataset="my-library",
    split="local",
    run_id="demo",
    enabled_modalities=("scene",),
    frame_stride=5,
)

run_index(
    [
        VideoSource(video_id="video-1", path="videos/first.mp4"),
        VideoSource(video_id="video-2", path="videos/second.mp4"),
    ],
    config,
)

results = search_scene("a person enters a taxi", config=config, top_k=5)
for hit in results.hits:
    print(hit.video_id, hit.start, hit.end, hit.score)
```

The [Python indexing and retrieval contract](docs/benchmarking/core_contract.md)
documents configuration, stored metadata, result fields, and run layout.

## Recommended specs

> Coming soon

---

## Roadmap

VidXP is an evolving beta. We'd love to hear your feedback and where you'd like to see the project go.

| Area | Current foundation | Direction |
|---|---|---|
| Search results | Top result in the CLI; structured top-k Python results | Rich ranked results, metadata, previews, and filtering across interfaces |
| Temporal search | Frame and transcript-phrase timestamps | Better time ranges, scene boundaries, aggregation, and ranking |
| Video collections | Validated managed-media catalog and persistent multi-video snapshots | Remote resumable ingestion and richer library management |
| Actor workflows | Face clustering and highlighted video export | Cluster browsing, labeling, actor search, and stronger tracking |
| Speaker context | Timestamped dialogue search | Active-speaker detection and links between speech and visible people |
| Product experience | CLI and browser indexing/search | Clearer progress, result navigation, recovery, and long-running job controls |
| Evaluation | DiDeMo and HiREST baselines | Combined and component benchmarks, beginning with a LongVALE pilot |

## Models and local data

| Capability | Model |
|---|---|
| Dialogue embeddings | `Qwen/Qwen3-Embedding-0.6B` |
| Transcription | `mobiuslabsgmbh/faster-whisper-large-v3-turbo` |
| Scene search | `google/siglip2-base-patch16-224` |
| Actor detection | OpenCV Zoo YuNet |
| Actor recognition | OpenCV Zoo SFace |

VidXP maintains the standard local CLI/UI repository in `chroma_data/`. Each
local import is streamed into managed storage, validated with `ffprobe`, and
published through the repository catalog before it can be indexed. Each successful
indexing run creates an immutable generation and atomically publishes a multi-media
snapshot. Actor overlay videos are immutable cataloged artifacts. Re-indexing
replaces only that media item's active generation; removing or clearing media
publishes a new snapshot without deleting retained generations or media. Failed
and cancelled runs do not replace the active snapshot. Model caches normally live
outside this directory and outside the virtual environment. Provider revisions
and weight checksums are pinned in capability specs and recorded in each generation
manifest.

## Documentation and project links

- [Installation and troubleshooting](INSTALLATION_GUIDE.md)
- [Benchmarking status and results](docs/benchmarking/README.md)
- [Adding a capability](docs/adding-a-capability.md)
- [Changelog](CHANGELOG.md)
- [Issue tracker](https://github.com/grayhatdevelopers/vidxp/issues)
- [MIT license](LICENSE)


## Contributing

See [CONTRIBUTING.md](./docs/CONTRIBUTING.md) for guidelines, maintainers, and how to submit PRs. AI/vibe-coded PRs welcome!

## Credits

Built by Grayhat Developers PVT Ltd. 2026. Maintained by the community.

Email: info@grayhat.studio

<a href="https://github.com/grayhatdevelopers/vidxp/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=grayhatdevelopers/vidxp" />
</a>
