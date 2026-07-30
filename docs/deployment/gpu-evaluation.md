# GPU worker evaluation

Status: evaluated on 2026-07-29; implementation and model execution are
intentionally deferred.

No model, CUDA image, or Python wheel was pulled for this evaluation. Dependency
checks used registry metadata only.

## Decision

The first NVIDIA worker should use the supported CUDA 12.6 line:

| Component | Evaluated version or policy | Decision |
| --- | --- | --- |
| Python | 3.14.6 | Keep the project-wide runtime |
| PyTorch | 2.13.0 `cu126` | Accept; latest stable Torch with its supported CUDA 12 fallback |
| CTranslate2 | 4.8.1 | Accept; current wheel supports CUDA 12 |
| faster-whisper | 1.2.1 | Accept with CUDA 12 cuBLAS and cuDNN 9 |
| cuDNN | 9.10.2.21 from the resolved `cu126` stack | Accept |
| Actor models | OpenCV CPU | Keep CPU-only; do not route through CUDA |
| Ollama | Separate optional service | Do not couple to the indexing worker GPU |

CUDA 12.8 is rejected because the registry has no PyTorch 2.13 `cu128`
distribution. CUDA 12.9 resolved during metadata probing, but PyTorch 2.13's
published support direction retains CUDA 12.6 and moves the default to CUDA
13. CUDA 13 is rejected for the first worker because current CTranslate2 and
faster-whisper require the CUDA 12 family. Using 12.6 is a justified
compatibility exception, not an old default retained without review.

The metadata-only resolver command was:

```text
uv pip compile pyproject.toml
  --extra server-worker
  --no-sources
  --python-version 3.14
  --python-platform x86_64-manylinux_2_28
  --torch-backend cu126
  --default-index https://pypi.org/simple
  --index-strategy first-index
```

The exact implementation lock must be generated from `pyproject.toml` with the
`server-worker` extra and committed separately from the CPU `uv.lock`.
`--no-sources` is mandatory: without it, the current `tool.uv.sources` CPU
override wins and produces `torch+cpu` even when `--torch-backend cu126` is
present. `tool.uv.sources` must remain CPU-only for ordinary development and
publishing; the CUDA index is a release-resolver input, never a direct package
URL.

## Capability routing

| Capability operation | Device policy | Precision gate |
| --- | --- | --- |
| Scene SigLIP2 embedding/search | `cuda:<index>` | Start with the current float32 contract; evaluate lower precision separately |
| Dialogue Qwen3 embedding/search | `cuda:<index>` | Use bfloat16 only when `torch.cuda.is_bf16_supported()` passes; otherwise use an explicitly tested fallback |
| Dialogue transcription | CUDA device and index through faster-whisper | float16 |
| Actor detection/recognition and overlays | CPU | float32 |
| Media import and snippet extraction | CPU | Not applicable |
| SLM query planning/synthesis | External Ollama endpoint or deterministic fallback | Owned by the Ollama deployment |

The existing durable queue split is directionally correct: CUDA repositories
submit model jobs to the GPU queue, while actor overlays, media import, and
snippets remain CPU jobs. An index job can still execute actor work on the GPU
worker process, but `ModelRuntime.device_for("actor")` keeps that operation on
CPU.

## Required implementation boundary

The GPU worker should remain a supplemental deployment, not alter the default
CPU/Coolify stack:

1. Generate a target-specific GPU requirements lock using uv's `cu126`
   resolver backend.
2. Add a dedicated worker image target based on the existing Python 3.14
   runtime. The resolved NVIDIA Python libraries supply CUDA 12.6/cuDNN 9;
   configure their library directories for CTranslate2 before Python starts.
3. Add `compose.gpu.yaml` with one explicit `device_ids` entry and
   `capabilities: [gpu]`. Do not expose all host GPUs implicitly.
4. Run only `vidxp-worker --role gpu` in that service and set
   `VIDXP_RUNTIME_BACKEND=cuda:0` inside the container's visible-device
   namespace.
5. Keep the CPU worker for CPU-queue jobs. Do not replace it with the GPU
   worker.

The host requires the NVIDIA Container Toolkit and a driver compatible with the
selected CUDA 12 runtime. Release qualification should use the CUDA 12.6
toolkit driver floor rather than relying only on CUDA 12 minor-version
compatibility.

## Readiness gaps to close

Current worker health checks only verify PostgreSQL and Chroma. A GPU worker
must additionally fail readiness unless all of these are true:

- the requested backend is exactly `cuda:<index>`;
- `torch.backends.cuda.is_built()` and `torch.cuda.is_available()` are true;
- the selected index exists;
- the device capability appears in `torch.cuda.get_arch_list()`;
- the resolved PyTorch build reports CUDA 12.6;
- CTranslate2 reports at least one CUDA device;
- device name, capability, total memory, Torch version, CUDA version, cuDNN
  version, and selected precision policy are exposed in structured readiness.

`auto` must continue to select CPU. There must be no silent CPU fallback after
an explicit CUDA request.

## Validation required before enablement

No performance comparison is needed to enable the first functional GPU worker.
The release gate is correctness and failure behavior:

- build the locked image without CPU Torch or CUDA 13 packages;
- start on a clean NVIDIA host and prove explicit device isolation;
- run dependency/readiness checks without model downloads;
- with already prepared model assets, smoke one scene index/search and one
  dialogue transcription/search;
- verify actor processing remains on CPU;
- compare CPU and CUDA result shape, provenance, and retrieval tolerances;
- translate PyTorch OOM into a typed resource-limit failure;
- verify cancellation, worker restart, and DBOS recovery after OOM;
- record peak device memory and wall time without claiming an effectiveness
  improvement.

Blackwell, multi-GPU scheduling, MIG, ROCm, CUDA desktop installers, lower
precision tuning, and Ollama GPU sharing remain separate evaluations.

## Current blockers

Implementation must not begin until:

- the target GPU/driver matrix is selected for CI;
- the separate CUDA lock format and update workflow are agreed;
- a CUDA host is available for readiness and no-model dependency validation;
- model assets are already cached for the two functional smokes; and
- CUDA/cuDNN redistribution notices and the generated image SBOM are reviewed.
