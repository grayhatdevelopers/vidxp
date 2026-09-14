from __future__ import annotations

import argparse
import platform
import subprocess
from importlib import metadata
from importlib.util import find_spec


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def verify_minimal(executable: str) -> None:
    from vidxp.capabilities.registry import create_capability_registry

    required_capabilities = {"speech", "scene", "actor"}
    require(
        required_capabilities.issubset(create_capability_registry().names()),
        "minimal wheel does not expose the expected capability registry",
    )
    help_text = subprocess.check_output([executable, "--help"], text=True)
    require("benchmark" in help_text, "minimal CLI does not expose benchmarks")
    benchmark_help = subprocess.check_output(
        [executable, "benchmark", "--help"], text=True
    )
    require(
        "official benchmark adapters" in benchmark_help,
        "minimal CLI benchmark help is incomplete",
    )
    unexpected = [
        module
        for module in (
            "chromadb",
            "cv2",
            "faster_whisper",
            "sentence_transformers",
            "streamlit",
            "torch",
            "transformers",
        )
        if find_spec(module) is not None
    ]
    require(not unexpected, f"minimal wheel installed optional modules: {unexpected}")


def verify_cpu() -> None:
    import chromadb
    import cv2
    import faster_whisper
    import huggingface_hub
    import pooch
    import psutil
    import sentence_transformers
    import torch
    import transformers

    from vidxp.runtime import resolve_backends

    required_attributes = (
        (cv2, "FaceDetectorYN"),
        (cv2, "FaceRecognizerSF"),
        (faster_whisper, "WhisperModel"),
        (faster_whisper, "BatchedInferencePipeline"),
        (sentence_transformers.SentenceTransformer, "encode_query"),
        (sentence_transformers.SentenceTransformer, "encode_document"),
        (transformers, "AutoModel"),
        (transformers, "AutoProcessor"),
        (chromadb, "PersistentClient"),
        (huggingface_hub, "snapshot_download"),
        (pooch, "retrieve"),
    )
    missing = [
        f"{owner.__name__}.{attribute}"
        for owner, attribute in required_attributes
        if not hasattr(owner, attribute)
    ]
    require(not missing, f"CPU runtime APIs are missing: {missing}")
    require(psutil.virtual_memory().available > 0, "memory probe returned no capacity")
    require(torch.version.cuda is None, f"CPU torch exposes CUDA {torch.version.cuda}")

    installed = {
        distribution.metadata["Name"].lower().replace("_", "-")
        for distribution in metadata.distributions()
        if distribution.metadata.get("Name")
    }
    leaks = sorted(
        name
        for name in installed
        if name in {"cuda-toolkit", "cuda-bindings", "triton"}
        or name.startswith(("nvidia-", "pytorch-triton"))
    )
    require(not leaks, f"CPU runtime contains GPU packages: {leaks}")

    profile = resolve_backends("cpu")
    require(profile.torch_device == "cpu", "CPU profile selected another device")
    if platform.system() == "Darwin":
        require(platform.machine() == "arm64", "macOS runtime is not Apple Silicon")


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify installed VidXP runtimes.")
    parser.add_argument("profile", choices=("minimal", "cpu"))
    parser.add_argument("--vidxp-executable")
    args = parser.parse_args()

    if args.profile == "minimal":
        if not args.vidxp_executable:
            parser.error("minimal verification requires --vidxp-executable")
        verify_minimal(args.vidxp_executable)
    else:
        verify_cpu()


if __name__ == "__main__":
    main()
