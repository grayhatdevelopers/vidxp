from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence

from vidxp.core.manifest import sha256_file, utc_now, write_json_atomic


def _benchmark_uuid4(*parts: str) -> str:
    digest = bytearray(
        hashlib.sha256("\0".join(parts).encode("utf-8")).digest()[:16]
    )
    digest[6] = (digest[6] & 0x0F) | 0x40
    digest[8] = (digest[8] & 0x3F) | 0x80
    return digest.hex()


def benchmark_generation_id(
    benchmark: str,
    split: str,
    run_id: str,
) -> str:
    """Return a stable UUID4-shaped identity for a resumable benchmark run."""

    return _benchmark_uuid4("generation", benchmark, split, run_id)


def benchmark_media_id(benchmark: str, official_video_id: str) -> str:
    """Map an official dataset video key to VidXP's opaque media ID shape."""

    return _benchmark_uuid4("media", benchmark, official_video_id)


def verify_artifact(
    path: str | Path,
    *,
    name: str,
    expected_sha256: str | Sequence[str],
    source: str,
    revision: str,
) -> dict[str, Any]:
    artifact = Path(path)
    if not artifact.is_file():
        raise FileNotFoundError(f"{name} not found: {artifact}")
    actual_sha256 = sha256_file(artifact)
    accepted = (
        (expected_sha256,)
        if isinstance(expected_sha256, str)
        else tuple(expected_sha256)
    )
    if actual_sha256 not in accepted:
        raise ValueError(
            f"{name} checksum mismatch: expected one of "
            f"{', '.join(accepted)}, "
            f"received {actual_sha256}."
        )
    return {
        "name": name,
        "path": str(artifact.resolve()),
        "source": source,
        "revision": revision,
        "sha256": actual_sha256,
        "size_bytes": artifact.stat().st_size,
    }


def ensure_adapter_outputs(run_directory: Path) -> None:
    run_directory.mkdir(parents=True, exist_ok=True)
    for name in ("timings.jsonl", "failures.jsonl", "evaluator.log"):
        (run_directory / name).touch(exist_ok=True)


def record_adapter_manifest(
    run_directory: Path,
    *,
    benchmark: str,
    subset: Mapping[str, Any],
    artifacts: Sequence[Mapping[str, Any]],
    state: str,
    details: Mapping[str, Any] | None = None,
) -> None:
    manifest_path = run_directory / "manifest.json"
    if not manifest_path.is_file():
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["benchmark_adapter"] = {
        "benchmark": benchmark,
        "state": state,
        "subset": dict(subset),
        "artifacts": [dict(artifact) for artifact in artifacts],
        "updated_at": utc_now(),
        **dict(details or {}),
    }
    write_json_atomic(manifest_path, manifest)


def append_failure(
    run_directory: Path,
    *,
    stage: str,
    error: BaseException,
) -> None:
    payload = {
        "stage": stage,
        "error": f"{type(error).__name__}: {error}",
        "failed_at": utc_now(),
    }
    with (run_directory / "failures.jsonl").open(
        "a",
        encoding="utf-8",
    ) as destination:
        destination.write(
            json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
        )


def run_logged_evaluator(
    command: Sequence[str],
    *,
    cwd: str | Path,
    log_path: str | Path,
    environment: Mapping[str, str] | None = None,
    note: str | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(environment or {})
    completed = subprocess.run(
        [str(item) for item in command],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    lines = [
        f"command: {subprocess.list2cmdline([str(item) for item in command])}",
        f"cwd: {Path(cwd).resolve()}",
        f"return_code: {completed.returncode}",
    ]
    if note:
        lines.extend(("note:", note))
    lines.extend(
        (
            "stdout:",
            completed.stdout.rstrip(),
            "stderr:",
            completed.stderr.rstrip(),
        )
    )
    Path(log_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError(
            f"Official evaluator exited with code {completed.returncode}. "
            f"See {log_path}."
        )
    return completed
