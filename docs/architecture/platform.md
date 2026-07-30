# VidXP Platform Architecture

Status: accepted
Last validated: 2026-07-28
Scope: CLI, API, MCP, Streamlit, desktop, media ingestion, indexing, search,
artifacts, natural-language query, CPU/GPU execution, and deployment

## 1. Purpose

This document defines the target architecture and delivery gates for VidXP's
application platform. Changes to its ownership boundaries or accepted decisions
require a reviewed architecture update or ADR with supporting evidence.

## 2. Product outcomes

VidXP must support:

1. A local CLI operating directly on local media and repositories.
2. A local interactive UI, currently Streamlit, using the same application layer.
3. A future packaged desktop UI using the same application layer.
4. A remotely accessible HTTP API.
5. A remotely accessible MCP server over Streamable HTTP.
6. Local MCP over stdio for adjacent clients; stdio is not the media path.
7. Durable indexing and artifact jobs with progress and cancellation.
8. CPU execution first and an explicit, tested GPU runtime afterwards.
9. Natural-language questions over all indexed collectors with timestamped evidence.
10. A Coolify-compatible multi-service deployment.
11. Native local operation on supported Apple Silicon macOS without requiring Docker.
12. A thin-client mode for using a self-hosted VidXP server from another machine.

## 3. Non-negotiable invariants

1. CLI, UI, API, MCP, and desktop are adapters. They parse input, authenticate,
   invoke application commands, and format output. They do not implement indexing,
   media persistence, job state, search policy, or artifact generation.
2. FastAPI and MCP are sibling adapters. MCP never calls VidXP's HTTP API internally.
3. Public transports reuse the same Pydantic command and result models.
4. Video bytes are never passed as MCP tool arguments or over stdio.
5. Remote MCP clients reference previously registered media by `media_id`.
6. A failed or cancelled indexing job cannot damage the last committed index.
7. Search reads an immutable committed index generation.
8. Job state and index state are separate.
9. No remote contract exposes arbitrary server filesystem paths.
10. CPU/GPU selection is a validated runtime profile, not a free-form string.
11. Unexpected internal errors are logged with correlation data and masked publicly.
12. Every unbounded collection exposed by an adapter is paginated or capped centrally.
13. Stable dependencies are kept current; compatibility pins require a recorded
    blocker, an owner, and a removal gate.
14. CPU behavior across Linux, Windows, and Apple Silicon is accepted before
    platform-specific acceleration work.

## 4. Dependency direction

```text
Composition root
  ├── Typer CLI adapter
  ├── Streamlit adapter
  ├── Desktop adapter
  ├── FastAPI adapter
  └── MCP adapter
          │
          ▼
Application commands, queries, results, and public errors
  ├── media use cases
  ├── indexing use cases
  ├── search use cases
  ├── natural-language query use cases
  ├── actor use cases
  └── artifact use cases
          │
          ▼
Domain contracts and ports
  ├── MediaCatalog / MediaStore
  ├── IndexCatalog / IndexRepository
  ├── JobService
  ├── ArtifactStore
  ├── CapabilityRegistry
  ├── QueryPlanner / AnswerSynthesizer
  ├── ModelRuntime
  └── ResourceScheduler
          │
          ▼
Infrastructure implementations
  ├── managed local filesystem storage
  ├── Chroma
  ├── DBOS SQLite / Postgres
  ├── FFmpeg / ffprobe / OpenCV
  ├── local and GPU model providers
  └── environment and Docker wiring
```

Only the composition root may import both adapters and concrete infrastructure.
Application and domain modules must not import FastAPI, MCP, Typer, Streamlit,
Chroma, DBOS, OpenCV, or environment variables.

## 5. Composition and settings

Use one immutable `VidXPSettings` model built with `pydantic-settings`.

It owns:

- the per-user application data root
- repository root and active local repository selection
- trusted local import roots
- upload limits and retention
- authentication mode and secrets
- public base URL
- MCP transport configuration
- MCP canonical resource URL, body limit, allowed hosts, and allowed origins
- local/server workflow mode and queue selection
- resolved CPU/GPU runtime request
- model cache path and offline/download policy
- allowed model identifiers
- concurrency and memory-related limits

Settings are constructed once by a composition root and injected. Adapters must not
mutate process environment variables to communicate configuration. Application
objects must not be created at module import time.

### 5.1 Version and upgrade policy

VidXP targets the latest stable, non-pre-release versions available when a delivery
phase begins. As of this validation, the current Python release is 3.14.6. Runtime
images and primary development use the latest 3.14 maintenance release.
The library test matrix covers supported CPython 3.11 through 3.14 while those
versions remain security-supported.

Direct dependency ranges are bounded at the next incompatible major version.
Release environments and images lock exact direct and transitive artifacts. Update
automation opens dependency changes continuously, but an update merges only after
contract, packaging, platform, benchmark, and license gates pass.

A dependency may remain below latest stable only when a reproduced incompatibility
or material regression is recorded. The exception must identify:

- the blocking package and upstream issue
- affected operating systems/runtime profiles
- the newest compatible version
- the replacement or upgrade path
- the test that removes the exception

An ML package does not hold the control plane, Python runtime, or unrelated
capabilities back. When necessary, the package is replaced or isolated behind its
worker/provider contract. Pre-release packages are excluded from production
constraints.

Model weights follow a different rule from libraries: the default is the
best-performing current stable model that passes VidXP's quality, latency, memory,
license, redistribution, and cross-platform benchmarks. It is pinned by immutable
revision and checksum. “Newest” alone is not sufficient to change indexed embedding
semantics.

## 6. Shared public contracts

The application layer owns the canonical Pydantic models. HTTP and MCP may project
transport-only fields such as links or status codes, but may not redefine business
state.

Required identifiers:

- `RepositoryId`
- `MediaId`
- `VideoId`
- `IndexGenerationId`
- `IndexSnapshotId`
- `JobId`
- `ArtifactId`

For the current single-source media pipeline, `VideoId` is the same opaque value
as `MediaId`. A separate video-track identity is introduced only when one media
asset can produce multiple independently addressable video tracks; adapters must
not invent a second identifier before then.

Required shared models:

- `MediaAsset`
- `MediaProbe`
- `IndexPlan`
- `IndexGeneration`
- `IndexSnapshot`
- `IndexGenerationStatus`
- `Job`
- `JobProgress`
- `ProgressEvent`
- `SearchCommand`
- `SearchHit`
- `SearchResult`
- `QueryPlan`
- `QueryAnswer`
- `Evidence`
- `Artifact`
- `Page[T]`
- `RuntimeProfile`

Public models carry a real schema version where compatibility requires one. Adapters
must not append schema fields manually during serialization.

## 7. Public error model

Application errors have:

- stable machine code
- category
- safe public message
- optional safe details
- retryability
- internal cause retained only for logs

Initial categories:

- validation
- authentication
- authorization
- not found
- conflict
- unavailable
- resource limit
- cancelled
- internal

FastAPI maps categories to HTTP statuses and a consistent error envelope. MCP maps
expected application failures to controlled tool-execution errors. CLI maps them
to exit codes and terminal messages. No adapter infers product meaning from broad
built-in exceptions.

Every MCP tool is registered through one wrapper that catches expected application
errors and raises the SDK's public `ToolError` with a compact safe JSON payload:

- an assigned safe protocol integer code
- a safe public message
- the stable VidXP application code, retryability, and correlation ID
- safe validation details or the required repository scope when applicable

The wrapper catches every other exception, logs the cause with correlation data, and
raises only a generic internal `MCPError`. `MCPServer` re-raises `MCPError` without
converting the underlying exception to text, so the SDK's default
`str(exception)` leakage path is not used. Expected errors become
`CallToolResult(isError=True)` through the SDK's normal execution-error path and
remain outside the Pydantic success schema.

## 8. Repository layout

Native local interfaces share an operating-system per-user application data
root rather than deriving storage from the process working directory:

```text
VidXP/
  repositories/
    default/
      ...
  models/
```

The CLI's global `--data-dir` option and `VIDXP_DATA_DIR` replace this root.
`VIDXP_INDEX_DIR` and `VIDXP_MODEL_CACHE` remain narrower advanced overrides.
Named-repository configuration uses the operating system's standard roaming
user-configuration directory. Container deployments do not inherit these host
defaults; Compose supplies explicit repository and model-cache volumes.

Within a repository, one `RepositoryLayout` defines all persistent paths:

```text
repository/
  repository.json
  catalog.sqlite3
  media/
    objects/
  indexes/
    store/
    generations/
      <generation-id>/
    snapshots/
      <snapshot-id>.json
    active-snapshot.json
  artifacts/
    objects/
  local-workflows/
```

Model caches do not live inside repositories. For native local operation they
are a sibling under the shared application data root; server deployments mount
their cache explicitly.

Remote API/MCP responses never expose these internal paths.

## 9. Media ingestion and identity

### 9.1 Media asset

`MediaAsset` records:

- stable `media_id`
- content checksum
- original filename
- byte size
- declared and detected MIME/container
- duration, streams and codecs from ffprobe
- managed storage key or approved external source reference
- repository and owner/principal where applicable
- ingest state
- associated `video_id`
- creation and retention timestamps

Checksum is calculated once during ingest and reused for deduplication and indexing.
Untrusted content is not published into the catalog until ffprobe validation succeeds.

### 9.2 Local CLI and desktop

Local clients call `ImportMedia` with a path. Policy decides whether VidXP references
the original file or copies it into managed storage. Paths must resolve beneath
configured import roots when a boundary is required.

Local media does not need to travel through an HTTP upload endpoint.

### 9.3 Remote upload

Remote upload is a four-stage protocol:

1. An authenticated client creates an upload intent.
2. tusd's blocking `pre-create` HTTP hook authenticates the request, validates the
   declared size/type and intent, and assigns an opaque upload ID.
3. tusd returns an HTTPS upload URL. That URL is an unscoped bearer credential for
   subsequent HEAD/PATCH requests because tusd cannot bind resumptions to the user
   who created the upload.
4. The non-blocking `post-finish` hook idempotently upserts completion by upload ID
   and enqueues the durable ffprobe/import workflow.

Application responses carrying upload URLs use `private, no-store` and
`no-referrer`; hook payloads and MCP results do not persist them. The opaque path is
still a bearer credential and may appear in an upstream proxy's access log unless
that deployment disables or redacts logging for `/uploads/`. Hook handlers assume
duplicate and out-of-order delivery and never run ffprobe or encoding inline. A
recovery sweep finds completed uploads whose finish hook was missed. A retention
workflow removes abandoned intents and quarantine objects.

The hook endpoint is private to the Compose network. Client authorization is read
from the hook request body and redacted; client tokens are never stored in tus
metadata. Only the tus upload route is public. A completed upload is not a
`MediaAsset` until durable probe/import succeeds.

The supported server topology uses tusd filestore on a named quarantine volume
shared read-only with the hook service and worker. Managed media and artifacts use
the stack's named content volume. The whole deployment is single-node and one
deployed stack is one repository boundary. Clients upload directly to tusd;
FastAPI does not proxy large video bodies.

S3-compatible storage is deferred and not implemented. If revisited, it would
apply only to upload quarantine, source media, and generated artifacts—never to
embeddings, which remain in Chroma. Although tusd can receive uploads into S3,
VidXP would still require explicit import, processing, publication, recovery,
cleanup, and delivery integration.

tusd 2.10.0 is pinned to a tested release digest, uses an explicit base path/public HTTPS
URL, a default 50 GiB maximum upload size, a deployment-wide upload quota,
restricted CORS origins, disabled downloads, and disabled concatenation. Deployments
may lower the configured maximum. Termination remains enabled so tusd owns its file
locks and deletion.
The blocking `pre-terminate` hook prevents deletion while import is processing and
admits completed/expired cleanup only with the private cleanup credential.

FastAPI retains one small multipart compatibility endpoint with a central hard
maximum of 256 MiB. It rejects oversized declared `Content-Length` before reading
and enforces the same maximum while streaming to quarantine. It is never used for
multi-gigabyte media.

URL ingestion is deferred. If added later, it is an asynchronous downloader with
SSRF controls, redirect and DNS revalidation, size/time limits, quarantine, and
ffprobe validation.

### 9.4 MCP and media

Remote MCP workflow:

```text
HTTP upload/import service -> media_id -> MCP start_indexing(media_id)
```

MCP does not carry video bytes, base64 video, or arbitrary server paths. Stdio may
operate on an existing local `media_id`, but is not a video injection protocol.

## 10. Index generations and repository snapshots

An `IndexGeneration` is an immutable index of one media asset under one capability
and model/runtime manifest. An `IndexSnapshot` is an immutable repository view that
maps each active `media_id` to the generation containing its current searchable
records.

This separation supports incremental libraries. Adding or re-indexing one video does
not rebuild every other video and does not mutate the currently searchable version.

An upsert indexing job:

1. Resolve and validate the media asset.
2. Acquire a lease for that repository and media asset.
3. Build under `indexes/generations/<new-generation-id>`.
4. Persist manifests and checkpoints inside the generation.
5. Validate capability completion and storage consistency.
6. Create a new snapshot by replacing only that media asset's generation mapping.
7. Atomically update `active-snapshot.json`.
8. Release the lease.
9. Retain or garbage-collect unreferenced generations according to policy.

Removing media creates a snapshot without that media mapping; it does not delete
index data before the new snapshot is active. A full rebuild creates generations for
the selected assets and commits one new snapshot after all required generations
succeed.

Failure or cancellation before step 7 leaves the prior snapshot and prior generation
searchable.

Search resolves `active-snapshot.json` once and queries only generations referenced
by that immutable snapshot. Indexing and search therefore coexist safely.

The Chroma adapter stores generation identity with every record and implements
snapshot-scoped search and garbage collection. Chroma remains replaceable behind the
`IndexRepository` port; snapshot semantics do not depend on Chroma collection layout.
For the embedded adapter, `indexes/store/` is the shared physical Chroma database;
generation directories own manifests and checkpoints, while exact generation record
counts in those manifests are revalidated before committed reads. A missing database,
collection, manifest, or referenced record therefore fails closed instead of creating
an empty replacement during a read.

The manifest is authoritative for completed generation state. Progress files and
workflow events are not used to decide whether a generation or snapshot is valid.

## 11. Durable jobs and process execution

The application exposes one `JobService` contract:

- submit
- get
- list
- cancel
- retry/resume where safe
- publish progress
- retrieve result

DBOS 2.28 is the durable orchestration, queue, and state backend:

- SQLite for local CLI/UI/desktop
- one Postgres database for the API catalog, uploads, snapshots, and DBOS's
  isolated `dbos` schema in API/Coolify
- the same workflow definitions and job contract in both modes
- separate CPU and GPU worker queues
- concurrency one by default for indexing per constrained runtime

DBOS owns durable lifecycle, queueing, recovery, status, attempts, and persisted
progress. `set_event` stores the latest `JobProgress`; append-only DBOS streams are
used only when event history is required. Workflow stream writes are exactly once;
step stream writes may repeat after retry. The index manifest remains authoritative
for index state.

DBOS synchronous workflows and steps run in threads inside the worker process.
DBOS does not provide process isolation or pre-empt blocking CPU/GPU calls. Heavy
work therefore always runs in a dedicated worker process/container, never in the
CLI, desktop, UI, API, or MCP process:

```text
local:
CLI/UI/desktop -> DBOSClient -> supervised local worker process -> SQLite

server:
API/MCP -> DBOSClient -> worker process/container -> Postgres
```

The local client may detach from the durable worker. SQLite is restricted to one
machine and local storage; it is not used on network filesystems or across hosts.

The core runner accepts one execution context with progress and cooperative
cancellation. Queued cancellation removes the DBOS workflow. Cancelling an active
synchronous DBOS step only marks it cancelled and is observed at a later step; it
does not stop the running thread. VidXP therefore bridges durable cancellation to
the runner's `CancellationToken` and polls at progress/batch boundaries. Blocking
model and FFmpeg calls that expose no cancellation point finish before cancellation
is observed. Hard interruption is an operator-level termination of the dedicated
concurrency-one worker followed by supervisor restart; it is not promised as a
safe per-call API feature.

This cooperative/process boundary is execution control, not another job-state
machine. VidXP does not recreate queue/job lifecycle in JSON and locks.

Retry is enabled only for idempotent steps. Generation-based indexing makes retries
safe because incomplete generations are never active.

Local workers use a stable executor ID. Server executor IDs are
`<application-version>-<role>-<ordinal>` and remain stable across replacement of the
same release, while blue/green releases receive distinct IDs. The v1 server topology
uses exactly one CPU executor plus exactly one GPU executor when the GPU profile is
enabled. Each calls `DBOS.listen_queues()` before launch. The API uses `DBOSClient`
and never launches or listens to queues. A same-release replacement recovers work
owned by its stable ID; an old-version worker remains available while its workflows
drain. Scaling a release to multiple ordinals requires DBOS Conductor or a separately
reviewed dead-executor recovery coordinator.

`worker_concurrency=1` limits one executor; repository leases enforce per-repository
exclusion. Queue-global concurrency is configured only where one job across every
worker is intentionally required.

`application_version` combines the VidXP release with a digest of the installed
workflow implementation. Recovery therefore occurs only on a worker running the
same code, including when a prerelease build changes without a package-version
bump. Deployments drain old-version jobs or run blue/green old-version workers until
they finish; breaking workflow changes require an explicit DBOS patch/migration
strategy.

## 12. Resource scheduling and model runtime

`RuntimeProfile` is resolved at startup:

- operating system and architecture
- requested backend: `auto`, `cpu`, `cuda:<index>`, or `mps`
- resolved backend per capability
- precision/compute type per capability
- model cache
- offline/download policy
- maximum concurrent indexing jobs
- maximum concurrent inference queries
- allowed model IDs and revisions

Hardware probing occurs once. Invalid CUDA or MPS configuration fails readiness
clearly instead of falling back silently.

`auto` is allowed only for local CLI/desktop. It resolves once, records the actual
backend used by each capability in the job/index manifest, and never changes during
a job. A capability may resolve differently from another capability on the same
machine; for example, scene embedding can use Apple MPS while transcription uses
CPU. Server/Coolify workers require explicit `cpu` or `cuda:<index>` so scheduling
cannot drift.

Model construction and caching belong to `ModelRuntime`. Adapters and capability
handlers do not each load models independently.

Model downloads are explicit preparation jobs. Doctor/readiness checks inspect
the pinned cache without constructing models or downloading, and ordinary
indexing or query work fails fast with `model_unavailable` when required
artifacts are absent. Preparation publishes byte progress and model-loading
stages through the shared durable job contract. Pinned snapshot and artifact
byte sizes are part of the model contract. Interactive preparation displays
the missing-model sizes, maximum additional cache space, and cache path before
requiring confirmation.

The scheduler bounds concurrent model work. Local execution does not reject work
based on a fixed free-RAM threshold; allocation failures come from the runtime
that attempted the actual operation. API request threadpools are not the model
scheduler.

### 12.1 Scene sampling delivery boundary

The current scene-indexing deliverable uses deterministic time-based sampling at
a configured interval. This is the minimal sampling path required to make scene
indexing predictable and runnable through the complete application; it does not
claim content-aware or shot-aware frame selection.

Content-aware or shot-boundary sampling is deferred. Before it is considered for
the default path, benchmark it against time-based sampling for retrieval quality,
indexing time, materialized frames, and memory use. Add sampling-specific
backpressure only if those measurements show that bounded model/write batches and
the existing scheduler do not adequately control resource use.

## 13. Cross-platform CPU and acceleration

CPU completion is the first platform gate. The supported local targets are:

- Apple Silicon macOS on the current and previous two major macOS releases
- Linux x86-64
- Windows x86-64

Linux ARM64 is accepted only after the same native-wheel and model smoke gates pass.
Intel macOS is not a target for the new local runtime.

The native macOS path does not require Docker. It uses:

- the current stable CPython ARM64 installer/runtime
- a supervised local worker and DBOS SQLite
- embedded Chroma
- local FFmpeg/ffprobe
- PyTorch MPS for capabilities that pass parity tests
- CPU inference where a provider has no stable Metal backend
- native Ollama, which manages its own Metal acceleration

The initial macOS transcription provider is CPU-capable. An MLX provider can replace
or supplement it only after it passes the same transcript/timestamp contracts; the
rest of VidXP does not depend on that optimization.

CUDA is a later delivery phase, after the CPU application, API/MCP, media, jobs,
index snapshots, and deployment paths are complete. It uses the same application
contracts with a distinct Linux worker image:

- the latest mutually compatible stable Python, PyTorch, CTranslate2, CUDA, and
  cuDNN versions at the start of the GPU phase
- Docker/Compose GPU device reservation
- explicit precision and batch defaults per capability
- worker readiness that verifies CUDA without downloading models

The GPU phase does not inherit the old PyTorch 2.8/WhisperX matrix. If a package
requires an obsolete Python/PyTorch/CUDA stack, replace or isolate that package
instead of downgrading the platform by default. `torchaudio` and `torchvision` are
installed only when a selected provider directly requires them.

PyTorch CPU/CUDA variants are selected through separate locked constraints and
explicit package indexes. An `extra-index-url` is not used where it can mix CPU and
CUDA artifacts.

Capability routing is explicit after acceleration acceptance:

- scene and text embeddings: CPU everywhere; MPS on supported Macs and CUDA on the
  GPU worker after parity tests
- transcription: CPU everywhere; CUDA through the selected CTranslate2 provider
  after parity tests; an Apple MLX provider is a separate optimization
- actor: CPU until its ONNX GPU provider passes clustering/search parity
- SLM/Ollama: CPU/Metal locally; CPU by default in the server profile

API/MCP never receives CUDA access. When the GPU profile is enabled, only
`worker-gpu` receives CUDA access. An optional Ollama CUDA profile can receive a
different explicit `device_id`. Sharing one device between the indexing worker and
Ollama requires a later cross-process VRAM admission design.

Readiness reports platform, architecture, provider versions, selected backend,
precision, and device details. CUDA readiness reports driver, compiled CUDA,
device index/capability, and `torch.cuda.is_available()`. MPS readiness reports
`torch.backends.mps.is_available()`. Unsupported requested acceleration fails
instead of silently using CPU.

Model weights are not baked into the default release image. First-use download and
offline/preload behavior are documented and observable. Preloaded model images are
not part of this architecture.

## 14. Capability system

Capability definitions contain domain metadata only:

- name and description
- supported index stages
- operations and their shared input/result models
- execution group
- required media/index data
- model/runtime requirements
- query affordances and expected cost

They do not contain Typer factories or MCP/FastAPI metadata.

Built-ins are registered by the composition root. External capabilities use Python
package entry points. Execution grouping is explicit; it is not inferred from Python
callable identity.

Capabilities depend on storage/model/media ports. Chroma and model packages are
infrastructure implementations.

External packages register under entry-point group `vidxp.capabilities`. Each entry
point loads a zero-argument factory returning a transport-neutral
`CapabilityPlugin` containing a `CapabilityDefinition`, application executor
factory, and declared VidXP capability-contract version. The definition remains
pure metadata; the executor factory binds operations to handlers that depend only on
application/domain ports.

External capabilities are trusted in-process code, not sandboxed plugins. They are
disabled by default in remote deployments and enabled by an allowlist of distribution
and entry-point name. Built-in names cannot be overridden. Duplicate names,
unsupported contract versions, import failures, and missing runtime requirements
fail readiness with distribution provenance. Discovery/loading occurs only in the
composition root and is sorted deterministically.

### 14.1 Collector dependency decisions

- Scene: remove `clip-anytorch` and its `setuptools<81` compatibility pin. Use
  Transformers with `google/siglip2-base-patch16-224` at immutable revision
  `75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2`. The 2025 SigLIP 2 paper reports
  retrieval improvements over SigLIP across model scales, and MIEB independently
  places SigLIP-family models among strong multimodal retrieval encoders. That
  evidence is recent and matches the image/text retrieval task closely enough that
  downloading several large candidates for another selection benchmark is not
  justified in this delivery phase. VidXP's existing scene benchmark remains the
  regression gate.
- Dialogue: replace WhisperX as the default provider because its stable release
  blocks current Python and constrains an older Torch family. The first replacement
  is latest stable `faster-whisper`/CTranslate2 using batched transcription, VAD, and
  word timestamps. The default is
  `dropbox-dash/faster-whisper-large-v3-turbo` at immutable revision
  `0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf`. Its transcript/timestamp output is
  tested against the existing
  dialogue contract. Forced alignment is an optional provider behind a separate
  contract; it cannot hold base transcription or Python back. Sentence embeddings
  use Qwen3-Embedding-0.6B at immutable revision
  `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`; its published multilingual MTEB
  retrieval results materially exceed the older multilingual E5 baseline and its
  Apache-2.0 license permits the intended deployment.
- Actor: replace `face_recognition`/dlib with OpenCV Zoo YuNet plus SFace through
  OpenCV's maintained DNN APIs. Model files are retrieved with `pooch`, pinned to
  OpenCV Zoo commit `47534e27c9851bb1128ccc0102f1145e27f23f98`, and verified
  against recorded SHA-256 digests before use.
  Migration requires clustering/search parity, a new index-schema/model identity,
  and CPU performance gates. Actor remains CPU-routed until the ONNX GPU provider
  passes the same gates.
- Storage: embedded Chroma `PersistentClient` is local CLI/desktop only. Server
  deployments use the latest stable Chroma client/server release behind the
  `IndexRepository` port.

`ModelRuntime` records provider, canonical model ID, immutable revision/digest,
weight checksum, license identifier, precision/quantization, cache state, and offline
policy in every generation manifest. Transitive VAD/alignment/model downloads are
rejected unless allowlisted.

## 15. Search

`SearchMoments` is a central application query:

- query text
- optional modalities/capabilities
- filters from an allowlisted typed grammar
- page size/cursor
- optional reranking profile

Results are normalized into `SearchHit`:

- `media_id`
- `video_id`
- source/index generation
- modality
- start/end time
- score and score semantics
- displayable text/metadata
- optional preview reference

The application owns limits, score normalization, multimodal fusion and deterministic
ordering. UI/API/MCP may request stricter limits but cannot loosen policy.

Actor cluster and detection queries use the same pagination conventions.

## 16. Natural-language query layer

`QueryVideo` is an application use case, not an MCP agent loop and not direct Chroma
access.

Flow:

1. Accept question, media/repository scope, and policy.
2. Ask an injected local SLM planner for a strictly typed `QueryPlan`.
3. Validate that the plan uses registered operations and safe parameters only.
4. Execute retrieval through application search/capability operations.
5. Fuse overlapping intervals with deterministic reciprocal-rank fusion.
6. Ask the answer synthesizer for a grounded answer.
7. Return `QueryAnswer` with timestamped citations and supporting hits.

Pydantic AI is the selected typed-call layer. The first local provider is self-hosted
Ollama 0.32.5, pinned to a tested multi-architecture image digest, through
`pydantic-ai-slim[openai]` and `OllamaModel`. Ollama Cloud is excluded because it
does not enforce the local JSON-Schema path required here. `QueryPlan` and the
model-only `DraftAnswer` use `NativeOutput`, strict Pydantic models, a bounded
request/output retry budget, and explicit time/token limits. VidXP validates the
draft and constructs `QueryAnswer`; the model receives no executable tools.
Pydantic AI does not replace VidXP's capability registry or search storage.

When the SLM is unavailable or produces an invalid plan, a deterministic fallback
searches applicable modalities and returns evidence. Generated text can never become
a filesystem path, model identifier, arbitrary filter operator, or executable
operation.

Before returning a generated answer, the application verifies every citation against
the retrieved hit ID, media ID, generation, and time interval. Missing, invented, or
out-of-range citations invalidate the generated answer and trigger the deterministic
evidence fallback. Typed JSON alone is never treated as proof of grounding.

The result records the configured provider/model identity and plan for
reproducibility.

The default SLM model ID, immutable revision/digest, and quantization are
intentionally unset until candidate models pass:

- both output schemas
- adversarial-plan rejection
- grounded-citation verification
- CPU RAM and latency limits
- GPU VRAM limits where applicable
- offline-cache behavior
- license and redistribution review

Until that gate passes, the provider is fixed to self-hosted Ollama but no arbitrary
caller-provided model string is accepted. Ollama runs as an internal optional `slm`
Compose profile with a persistent model cache; deterministic evidence retrieval
remains available when it is disabled.

## 17. Artifact and snippet delivery

`Artifact` records:

- `artifact_id`
- source `media_id`
- owning job
- type/profile
- MIME and byte size
- checksum/storage key
- state
- creation/expiry

Media and artifacts are served from the managed local content volume through
protected Starlette `FileResponse`, including byte-range support.

Every delivery request is handled only after repository authorization. Artifact
delivery resolves an `ArtifactStore` key beneath its configured root, rejects
symlink/path escapes, and only then constructs `FileResponse`.

Local source playback applies the identical configured-root and symlink/path-escape
checks to the `MediaStore` key before constructing `FileResponse`.

Search results normally use an authorized range-enabled source URL and client-side
seeking. VidXP does not generate a clip for every search hit.

`CreateSnippet` is an asynchronous artifact job used for download/share/MCP resource
links. FFmpeg output is cached by source checksum, interval and encoding profile,
written to a temporary key, validated, and atomically published.

Actor overlays and other rendered media use the same artifact workflow. Public
rendering commands never accept output paths. The separate CLI artifact-download
command may copy an already-authorized managed artifact to an explicit user
destination; API downloads remain protected responses and MCP returns a lazy
resource link instead of embedding video bytes in the tool result.

## 18. FastAPI adapter

FastAPI owns:

- HTTP routing and content negotiation
- authentication dependency and principal projection
- request/correlation IDs
- transport-level body rejection
- status codes and headers
- OpenAPI
- small multipart compatibility
- protected local artifact responses

FastAPI calls application commands directly. It has no job manager, media store,
index lock, model cache, or MCP client.

Use shared application models directly where the HTTP shape matches. Add transport
models only for HTTP-specific links or envelopes.

Provide:

- `/health` for minimal process liveness
- `/ready` for a minimal aggregate readiness result
- an authenticated runtime-readiness resource for component and device details

Readiness does not download model weights.

## 19. Authentication profiles

### 19.1 Local

- CLI/UI/desktop in-process: no network authentication.
- Local HTTP bound to loopback: optional static bearer.
- Local stdio MCP: operating-system process boundary.

### 19.2 Private self-hosted

A shared static bearer token is permitted as an explicitly documented single-tenant
mode. It uses constant-time verification and produces a typed principal. It is not
described as OAuth and does not imply users, scopes, or revocation.

For HTTP API and MCP in this mode, one outer ASGI authentication middleware validates
the token before dispatch. The only public-path exceptions are the minimal
`/health` and `/ready` endpoints, which disclose no configuration or component
details. It does not publish fake OAuth issuer/resource metadata.

The private tusd hook callback is also excluded from public bearer middleware, but
is not a public exception: the reverse proxy does not route it from the public
listener and only the isolated Compose hook network can reach it. The hook handler
validates the original client authorization carried in the `pre-create` hook event
against the upload intent. Later hook events are authorized by the opaque upload ID
and previously authenticated intent, not by assuming that the client bearer will be
present on every resumed request. Hook contents and credentials are redacted.

### 19.3 Remote/multi-user

An external OIDC provider issues access tokens. VidXP validates issuer, audience,
expiry and scopes and produces the same typed principal used by API and MCP.

The MCP server implements OAuth resource-server and protected-resource metadata
required by the protocol. Tokens received by MCP are not forwarded to an internal
HTTP API.

VidXP's MCP `TokenVerifier`, not the SDK metadata configuration, validates:

- allowed signature algorithm and signature through cached JWKS
- exact issuer
- `aud` or RFC 8707 resource equal to the canonical public MCP URL
- expiration and not-before
- required scopes

Only after those checks does it construct the SDK access token and shared VidXP
principal from subject/client ID, scopes, and allowlisted claims.

Reverse-proxy authentication may be an additional gate but does not replace MCP
resource-server behavior.

## 20. MCP adapter

Use the official MCP Python SDK v2 high-level server.

Primary remote transport:

- Streamable HTTP
- explicit `stateless_http=True`
- `json_response=False` so disconnect cancellation uses the streaming/SSE path
- application state persisted outside MCP sessions
- explicit lifecycle through the application composition root
- an initial independent MCP request-body limit of 4 MiB

Local transport:

- stdio for clients running beside VidXP
- identical tools and shared contracts
- media referenced by `media_id`

Initial curated tools:

- `list_capabilities`
- `get_capability`
- `list_media`
- `get_media`
- `get_index_status`
- `start_indexing`
- `search_moments`
- `query_video`
- `list_jobs`
- `get_job`
- `retry_job`
- `cancel_job`

Media and job discovery let an agent recover registered assets and durable work
without carrying IDs across sessions. Generic job polling, retry, and cancellation
cover indexing, search, and query without duplicating operation contracts. Video
bytes remain on the HTTP/tus ingestion boundary rather than crossing MCP.

Tool results use real output schemas and structured content. Descriptions remain
short and agent-oriented; full API response schemas are not embedded as prose.

Every public tool has an explicit Pydantic result annotation and leaves structured
output enabled. Public tools do not return bare primitives/lists because the SDK
wraps them in a synthetic `result` object. Contract tests cover runtime-only Pydantic
validators in addition to emitted JSON Schema.

Protocol request cancellation is propagated to in-flight search/query work where
safe. It does not silently cancel an already accepted durable indexing job.
Cancellation is cooperative for synchronous CPU/GPU work; blocking functions are
never treated as pre-emptible merely because AnyIO runs them in a thread.

MCP does not implement its own queue. Persisted VidXP jobs remain the authoritative
long-running operation contract.

### 20.1 ASGI mounting and lifecycle

The remote process exposes API and MCP on one public origin while keeping them as
sibling adapters.

The MCP ASGI app is created with:

- Streamable HTTP path `/mcp`
- stateless HTTP enabled
- JSON-only responses disabled
- configured MCP body limit
- explicit transport-security settings

It is mounted at the outer application's root after concrete FastAPI routes. This
keeps both `/mcp` and the RFC 9728 host-root
`/.well-known/oauth-protected-resource/mcp` route reachable.

The composition-root lifespan enters `mcp.session_manager.run()` exactly once and
orders shared application startup, MCP startup, MCP shutdown, and application
shutdown explicitly. A mounted Starlette child lifespan is not relied on, and MCP
does not start a second copy of shared database/storage/model resources.

The public/proxy Host and Origin policy is configured explicitly. Binding to
`0.0.0.0` must not disable DNS-rebinding protections. Deployment validation confirms
that the Coolify proxy preserves `Accept`, `MCP-Protocol-Version`, `Mcp-Method`, and
`Mcp-Name`, and does not buffer MCP streaming responses.

## 21. Streamlit and desktop adapters

Streamlit retains:

- widgets and page/session state
- display formatting
- local user interaction

It loses:

- fixed global media/artifact paths
- direct file persistence
- index readiness policy
- process and cancellation ownership
- hardcoded capability branching
- model/storage access

The desktop application uses the same application commands, per-user data-root
layout, and a local DBOS SQLite workflow store, but heavy jobs run in a
supervised separate worker process. It manages client/worker lifecycle,
displays model preparation progress, and detaches from durable work or requests
cooperative cancellation during shutdown. Closing the UI never waits on an
uncancellable model thread inside the UI process.

The Phase 11 adapter is a small Tauri v2 shell. Its first-run configuration
selects capability extras, optional interfaces, model preparation, and model
storage, while the processing application is the exact published VidXP package
installed into a versioned uv-managed environment. When selected, the Streamlit
adapter is the local human interface on a random loopback port; remote loopback
content receives no Tauri IPC access. Runtime activation is atomic and a failed
configuration retains the prior environment. Tauri owns the Streamlit process
and asks the repository-scoped worker to drain and shut down on exit.

The first desktop release is an online bootstrap with a native tray supervisor
and no updater. It targets Windows x86-64 NSIS, Apple Silicon DMG, and Linux
x86-64 AppImage.
Target-specific uv binaries are release inputs verified against the pinned
upstream checksum. FFmpeg/ffprobe remain validated system dependencies until
target build provenance, codec selection, and redistribution licenses are
recorded; the application does not silently fetch an unaudited build.

### 21.1 Operating modes

The same command and result contracts support three composition profiles:

1. **Native local:** the CLI, Streamlit, or desktop adapter composes the application
   with a local worker, DBOS SQLite, embedded Chroma, and a platform app-data
   repository. Media can be imported from allowlisted local paths. This is the
   default Apple Silicon experience and does not require Docker.
2. **Remote client (planned):** a thin CLI, UI, or desktop adapter will connect
   to a self-hosted VidXP deployment without local models or a vector database.
   The current release rejects `VIDXP_MODE=remote` instead of silently composing
   local storage. Remote agents are supported now through the Streamable HTTP MCP
   endpoint, and other clients can use the authenticated HTTP/tus APIs directly.
3. **Self-hosted server:** API/MCP, workers, workflow state, media ingestion, and
   vector search run as the Coolify stack. The same server also supports ordinary
   Compose outside Coolify.

The planned client configuration is limited to server base URL, authentication,
repository, timeouts, and local upload source. Its transport must implement the
same client-facing command interface as the native application facade without
duplicating validation, indexing, search, or artifact policy.

The distributable Python package has a lightweight client/core installation and
explicit local/server extras. Installing the thin client must not install PyTorch,
model providers, Chroma server components, or CUDA packages. Platform lock files
resolve the local worker dependencies independently from the control plane and thin
client.

## 22. Deployment topology

Supported Coolify/Compose server stack:

```text
api-mcp
  ├── FastAPI routes
  └── MCP Streamable HTTP mount

hooks
  └── private tusd callback and recovery sweep

worker-cpu
  └── DBOS CPU queue, stable executor ID, concurrency 1

postgres
  ├── application catalog, uploads, and snapshot metadata
  └── DBOS durable workflow state in the `dbos` schema

chroma
  └── private vector-search server with its own volume

tusd
  └── public /uploads/ route and HTTP hook caller

single-node media/artifact/quarantine volumes
```

This is one node, one application stack, and one repository. PostgreSQL, Chroma,
tusd, and the named content volumes are deployment components of that stack, not
provider plug-in points. A separate repository requires a separate stack and
separate databases and volumes. Arbitrary hosted databases, externally shared
Chroma, multiple API/worker replicas, failover, and provider compatibility are
outside the supported topology.

Server code connects only to the internal Compose service names `postgres` and
`chroma`. Their endpoints are fixed rather than exposed through
`VIDXP_DATABASE_URL`, `VIDXP_CHROMA_SERVER_URL`, or equivalent user settings.

Optional application services:

- GPU worker supplementing the CPU worker
- internal CPU-default Ollama `slm` profile
- external OIDC provider configuration

Postgres, Chroma, and hook endpoints remain private. Only API/MCP and the tus upload
route are published. Long-running services have healthchecks; one-shot storage,
migration, and Chroma-readiness gates must complete before their consumers start.
Required secrets use `${VAR:?required}` rather than insecure defaults. Public
routing cannot reach the tusd callback path; tests exercise that denial
independently of the private-network hook flow.

Release artifacts are:

- the native `vidxp` CLI/client package for supported macOS, Linux, and Windows
- an explicit local-worker extra and platform lock for native CPU indexing
- `compose.local.yaml`: an optional containerized Linux evaluation/development
  profile; it is not required for native macOS use
- `compose.coolify.yaml`: immutable prebuilt API/MCP, worker, Postgres, Chroma and
  tusd images with no build contexts; the same file is valid with ordinary Compose
- `compose.gpu.yaml`: supplemental GPU worker override
- optional `slm` profile

Use immutable release tags or digests. API/MCP and workers share an application
release but use purpose-specific image targets. API/MCP carries no heavy model or
CUDA dependencies; workers and the optional `slm` service do.

Deployment v1 is exactly one API/MCP replica and one node. Named
media/artifact/quarantine volumes are part of this topology. The fixed internal
service names and storage paths are not promises of arbitrary external-provider,
multi-replica, or high-availability compatibility.

The one-click template consumes published tags/digests and must pass a real Coolify
deployment, upgrade, rollback, persistent-data and healthcheck test.

## 23. Code to retain, move, or remove

Baseline facts that the rebuild must not mistake for target behavior:

- project metadata currently requires Python `>=3.10,<3.14`; the target baseline is
  the current stable Python release with the supported matrix defined in section 5.1
- the current Dockerfile/Compose produce one CPU-only Streamlit image/service
- server storage currently uses embedded `chromadb.PersistentClient`
- model loaders use per-module `lru_cache`, not one runtime/resource scheduler
- WhisperX compute type is hardcoded to `float32`
- repository configuration mixes deployment device choice into persistent identity
- the capability registry statically imports every built-in
- `CapabilityDefinition` currently carries CLI and implementation callables
- `clip-anytorch` requires the obsolete `setuptools<81` compatibility pin

Retain and adapt:

- capability-specific model/config logic
- frame/audio processing
- FFmpeg/ffprobe helpers
- manifest/checkpoint concepts
- Chroma storage initially
- existing search operations
- CLI rendering and Typer declarations

Move behind contracts:

- Chroma access
- repository layout
- media probing and persistence
- model loading/caching
- indexing execution
- actor rendering
- capability registration

Remove instead of preserving:

- adapter-owned job state machines
- adapter-owned media stores
- Streamlit process/global persistence policy
- MCP-to-FastAPI internal HTTP calls
- generated OpenAPI-to-MCP mirroring
- environment mutation as application configuration
- module-level application singletons
- progress files as index validity
- destructive in-place active-index replacement
- raw server paths in remote contracts
- caller-selected output paths
- duplicated transport-specific business models
- hardcoded central capability imports as the extension mechanism

## 24. Verification strategy

### 24.1 Contract tests

The same command fixture must produce equivalent CLI JSON, API JSON and MCP
structured results after removing transport-only fields.

### 24.2 Index durability

Test:

- failure before generation commit
- cancellation at each indexing stage
- process death and recovery
- search during replacement
- incremental add/re-index/delete
- atomic active-snapshot switch
- incomplete generation cleanup

The prior snapshot and affected media generation must remain searchable in every
pre-commit failure.

### 24.3 Job behavior

Run the same workflow contract against:

- SQLite local mode
- Postgres multi-process mode
- queued cancellation
- active cooperative cancellation
- worker restart
- idempotent retry
- CPU and GPU queue routing
- UI/client exit while a local worker continues
- cooperative cancellation latency at every runner checkpoint
- blocking-step cancellation behavior
- stable-executor restart recovery
- release-version drain and recovery

### 24.4 Media

Test:

- resumed upload
- documented bearer-URL behavior and log/referrer redaction
- checksum/deduplication
- invalid extension with real video
- valid extension with invalid content
- interrupted and expired uploads
- duplicate/out-of-order hook delivery
- missed-finish-hook recovery sweep
- abandoned intent/quarantine cleanup
- completion remaining unpublished until durable ffprobe succeeds
- import-root escape
- retained external source disappearance

### 24.5 MCP

Validate with the official client over:

- Streamable HTTP from a separate process
- stdio
- structured output schemas
- union/optional schema fidelity
- auth metadata and audience rejection
- protocol cancellation
- cancellation through the deployed non-buffering proxy
- durable job polling after session/client restart
- bounded actor/search responses
- host/origin rejection and required MCP proxy headers

### 24.6 Capabilities and artifacts

Capability discovery tests:

- no installed plugin
- allowlisted plugin
- disabled plugin is not imported
- duplicate built-in/name
- incompatible contract version
- import failure
- deterministic behavior on supported Python 3.11 through 3.14

Managed artifact delivery tests cover authorization, path/symlink containment,
missing or corrupt content, GET/HEAD/range headers, and MIME/disposition metadata.

### 24.7 Platforms and installation profiles

Run clean-environment native tests for the latest stable Python on:

- Apple Silicon macOS
- Linux x86-64
- Windows x86-64

The Apple Silicon gate covers native package installation, FFmpeg/ffprobe,
separate-process DBOS worker recovery, embedded Chroma persistence, scene
CPU/MPS routing, transcription CPU routing, actor ONNX CPU routing, cancellation,
and clean uninstall. It also runs an end-to-end remote-client flow against the
Compose server: resumable media upload, indexing, polling, search, and artifact
retrieval.

The thin-client environment must import and execute without model, Chroma server,
CUDA, or local-worker dependencies. The local-worker environment must run without
CUDA libraries. Package metadata, platform locks, and release images are tested
from an empty cache so undeclared system or developer-machine dependencies cannot
mask failures.

### 24.8 CPU/GPU

Before model downloads:

- build CPU and GPU targets
- run clean-image `pip check`
- generate SBOM and license/model manifests
- prove the CPU image contains no CUDA runtime
- inspect installed PyTorch/CUDA compatibility
- run CUDA availability/readiness probes
- validate Compose GPU reservation
- validate worker queue routing with a synthetic job
- prove actor work routes to CPU
- validate OOM, cancellation and worker-restart behavior without model downloads

After CPU functionality is complete:

- download one pinned model set
- run selected scene and transcription provider smoke tests on a supported GPU
- compare golden outputs against CPU
- record VRAM, precision, throughput and fallback behavior

## 25. Implementation sequence and gates

### Phase 1: contracts and composition

- settings and application factory
- shared identifiers/models/errors
- repository layout
- capability registry cleanup
- lightweight client/core, local-worker, and server dependency groups
- current-Python package metadata and platform lock generation

Gate: no adapter imports concrete storage/models, cross-adapter schema tests pass,
and the thin-client environment installs and imports without ML/server dependencies.

### Phase 2: current CPU provider baseline

- replace obsolete scene, dialogue, and actor provider dependencies
- pin licensed model revisions and record index identities
- model runtime and resource scheduler
- embedded Chroma adapter on the latest stable compatible release
- native CPU packaging for Apple Silicon macOS, Linux x86-64, and Windows x86-64

Gate: clean environments on the latest stable Python pass dependency, import,
license, and representative CPU capability smoke tests on all supported platforms.
Apple Silicon also passes any enabled MPS parity test; unsupported acceleration
remains explicit and does not block its CPU path.

### Phase 3: durable index foundation

- immutable generations
- authoritative manifests
- immutable multi-media snapshots
- active-snapshot pointer
- repository leases

Gate: incremental add/re-index/delete works and injected failure/cancellation never
damages the active snapshot.

### Phase 4: media and artifacts

- media catalog/store
- local import
- artifact catalog/store
- source playback and snippet contract

Gate: application operations use IDs, not public filesystem paths.

### Phase 5: workflow engine

- DBOS job service
- SQLite local mode
- Postgres worker mode
- typed progress/cancellation

Gate: restart, cancel and retry tests pass without adapter-owned job state.

### Phase 6: thin API

- FastAPI adapter
- auth profiles
- readiness
- small-upload compatibility
- artifact delivery

Gate: API process performs no indexing/model work and owns no business persistence.

### Phase 7: remote ingestion and deployment

- tusd integration
- Coolify Compose
- immutable image targets

Gate: interrupted multi-part upload resumes and completed media can be indexed by ID.
The gate also covers bearer upload-URL redaction, duplicate/out-of-order hooks,
missed-finish recovery, abandoned-upload retention, and the ffprobe publication
boundary.

### Phase 8: MCP

- official SDK v2 server
- Streamable HTTP and stdio
- curated typed tools
- remote auth

Gate: separate-process remote client completes media-id indexing, polling and search;
schemas and structured results pass conformance checks.

### Phase 9: multimodal and SLM query

- fused search
- typed query planning
- evidence-grounded answer synthesis
- deterministic fallback

Gate: every answer cites retrievable media intervals and invalid plans cannot escape
the operation grammar.

### Phase 10: GPU

- GPU worker image/profile
- runtime validation
- pinned model smoke tests
- resource limits

The 2026-07-29 evaluation selects PyTorch 2.13 `cu126` as the first compatible
NVIDIA baseline. CUDA 12.8 has no Torch 2.13 distribution, while CUDA 13 does
not share current CTranslate2/faster-whisper's CUDA 12 runtime contract. The
full worker set resolves on Python 3.14 to Torch 2.13.0, CTranslate2 4.8.1,
faster-whisper 1.2.1, and cuDNN 9.10.2. Implementation remains deferred until a
CUDA host and prepared model cache are available; no model or image was pulled
during the evaluation. See `docs/deployment/gpu-evaluation.md`.

Gate: GPU readiness and execution are explicit; no silent CPU fallback.
Clean-image dependency/SBOM checks, CPU-without-CUDA proof, selected scene and
transcription provider smoke tests, CPU actor routing, OOM handling, cancellation,
and worker restart must all pass.

### Phase 11: desktop

- desktop adapter
- platform repository lifecycle
- packaging and update strategy

Gate: desktop uses the same commands/jobs/media/indexes without UI-owned business
logic.

## 26. Decision validation ledger

Validated on 2026-07-28:

- **Official MCP SDK 2.0:** accepted. A separate-process Streamable HTTP probe and a
  stdio probe passed tool discovery/calls, Pydantic discriminated unions, optionals,
  constraints, `outputSchema`, and `structuredContent`. Required corrections for
  mount lifespan, root metadata path, token audience validation, safe errors,
  streaming cancellation, host/origin policy, and the MCP-specific body limit are
  incorporated above.
- **External FastMCP 3.4.5:** rejected for this phase because its server extra
  requires MCP SDK `<2`. The similarly named high-level server in official SDK v2
  is now `MCPServer`; it supplies typed schemas, structured results, Streamable
  HTTP, stdio, OAuth resource-server behavior, transport security, and request
  limits without generating tools from HTTP routes.
- **fastapi-mcp 0.4:** rejected because it generates an OpenAPI mirror and invokes
  the FastAPI application through an internal ASGI HTTP client. That duplicates
  transport contracts instead of using VidXP's shared application services.
- **DBOS 2.28:** accepted as durable queue/workflow state, not as process isolation.
  A Windows/Python 3.13 probe exercised SQLite queue, progress, stable-executor
  crash recovery, and a separate local worker process. Postgres 16 API-client plus
  distinct CPU/GPU worker processes and queue routing were exercised. These probes
  validate behavior, not the target Python floor or release lock. Active synchronous
  cancellation was proven non-preemptive; the separate worker/cooperative semantics
  above are therefore mandatory.
- **Current control-plane dependency baseline:** compatible at audit time.
  `fastapi==0.140.13`, `mcp==2.0.0`, `dbos==2.28.0`,
  `pydantic-settings==2.14.2`, and
  `pydantic-ai-slim[openai]==2.19.0` resolved together on Python 3.13. These are
  observations, not permanent pins. Implementation re-resolves the latest stable
  releases on the latest Python 3.14 maintenance release and records only justified
  compatibility exceptions. Release images lock the resulting direct and
  transitive artifacts.
- **MCP licensing:** accepted. The audited MCP stack is permissively licensed.
- **DBOS licensing:** accepted with distribution compliance work. DBOS is MIT, but
  mandatory psycopg/psycopg-binary dependencies are LGPL-3.0-only. Release artifacts
  must carry required notices and satisfy binary redistribution/source obligations.
- **tusd:** accepted with the capability-URL and hook semantics documented above.
  HTTP hooks are duplicate/out-of-order capable; the durable importer and sweep own
  correctness.
- **Artifacts:** accepted for the managed local content volume. Protected Starlette
  range delivery, authorization, storage-key containment, integrity checks, and
  response metadata remain mandatory.
- **Pydantic AI/Ollama:** accepted as the local SLM provider boundary. Typed native
  output is supported. The exact model remains deliberately blocked by the
  no-large-model-download constraint until Phase 9's measured gate.
- **Python and ML compatibility:** the current Python feature release is the
  platform baseline. Stable PyTorch, CTranslate2, ONNX Runtime, and OpenCV releases
  publish current-Python artifacts for the primary platform set, subject to the
  clean-environment runtime gates above. The current stable WhisperX release blocks
  that Python baseline and constrains an older Torch family, so it is rejected as
  the default transcription provider rather than allowed to hold the platform back.
  `faster-whisper`/CTranslate2 is accepted as the CPU transcription provider and
  must still pass the Python 3.14, Apple Silicon, transcript, timestamp, and license
  release gates.
- **CPU model selection:** accepted without a new multi-model download run.
  SigLIP 2 and MIEB provide recent task-matched evidence for the scene encoder;
  Qwen3-Embedding publishes stronger multilingual retrieval results than the older
  available baselines; faster-whisper publishes CPU/GPU speed and memory comparisons
  against Whisper implementations; and OpenCV Zoo publishes the selected detector
  and recognizer with evaluation data. VidXP runs regression and compatibility
  smoke tests now; a new comparison benchmark is required only when newer evidence
  conflicts, deployment measurements regress, or a provider change is proposed.
- **GPU package matrix:** intentionally deferred. The previously proposed Python
  3.11/PyTorch 2.8/WhisperX/CUDA 12.8 matrix is rejected as a target. Phase 10
  resolves the latest mutually compatible stable stack from the then-current CPU
  baseline and accepts it only through clean-image, CUDA, model, output-parity,
  cancellation, and recovery tests. No model weights or CUDA image were downloaded
  during this audit.
- **Chroma deployment:** embedded `PersistentClient` is accepted only locally.
  Coolify uses a private Chroma server and client/server adapter.
- **Capability entry points:** accepted as standard trusted-code discovery with the
  exact group, allowlist, version and collision rules above.

Before a release image is published, audit locked direct/transitive packages,
container bases, system libraries, model weights/revisions/quantizations, automatic
alignment/VAD downloads, notices, and redistribution terms. Emit an SBOM and model
manifest for every image. Package metadata alone is not treated as model-license
evidence.
