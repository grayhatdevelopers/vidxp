from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


BENCHMARK_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = BENCHMARK_ROOT / "tasks" / "longvale-part9-pilot.json"
REQUIRED_MODALITIES = frozenset({"scene", "action", "sound", "speech"})
REQUIRED_TOOLS = frozenset(
    {"get_runtime_readiness", "get_workspace", "search_moments", "wait_job", "get_job_evidence"}
)


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is required for MCP preflight.")
    return value


def _structured(result: Any, tool: str) -> dict[str, Any]:
    if getattr(result, "is_error", False):
        raise RuntimeError(f"{tool} returned an MCP error.")
    content = getattr(result, "structured_content", None)
    if not isinstance(content, dict):
        raise RuntimeError(f"{tool} did not return structured content.")
    return content


async def _preflight() -> None:
    tasks = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    filenames = {Path(task["media_relpath"]).name for task in tasks}
    server_environment = dict(os.environ)
    server_environment["VIDXP_MODEL_CACHE"] = _required_environment(
        "VIDXP_MODEL_CACHE"
    )
    server_environment["VIDXP_ALLOW_MODEL_DOWNLOADS"] = "false"
    parameters = StdioServerParameters(
        command=_required_environment("VIDXP_MCP_COMMAND"),
        args=[
            "--repository",
            os.environ.get("VIDXP_EVAL_REPOSITORY", "default"),
            "--index-directory",
            _required_environment("VIDXP_EVAL_INDEX_DIR"),
            "--data-dir",
            _required_environment("VIDXP_EVAL_DATA_DIR"),
            "--device",
            os.environ.get("VIDXP_EVAL_DEVICE", "cpu"),
        ],
        env=server_environment,
    )
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = {tool.name for tool in (await session.list_tools()).tools}
            missing_tools = sorted(REQUIRED_TOOLS - tools)
            if missing_tools:
                raise RuntimeError(
                    f"VidXP MCP is missing required tools: {', '.join(missing_tools)}"
                )

            readiness = _structured(
                await session.call_tool("get_runtime_readiness"),
                "get_runtime_readiness",
            )
            dependencies = readiness.get("dependencies")
            checks = dependencies.get("checks") if isinstance(dependencies, dict) else None
            if not isinstance(checks, list):
                raise RuntimeError("Runtime readiness did not report model checks.")
            failed_models = sorted(
                str(check.get("capability"))
                for check in checks
                if isinstance(check, dict)
                and check.get("kind") == "model"
                and str(check.get("capability", "")).split(".", 1)[0]
                in REQUIRED_MODALITIES
                and check.get("ok") is not True
            )
            covered_modalities = {
                str(check.get("capability", "")).split(".", 1)[0]
                for check in checks
                if isinstance(check, dict) and check.get("kind") == "model"
            }
            missing_model_checks = sorted(REQUIRED_MODALITIES - covered_modalities)
            if failed_models or missing_model_checks:
                details = [
                    *(f"unready: {name}" for name in failed_models),
                    *(f"unchecked: {name}" for name in missing_model_checks),
                ]
                raise RuntimeError(
                    "Required MCP models are not ready in VIDXP_MODEL_CACHE ("
                    + ", ".join(details)
                    + ")."
                )

            workspace = _structured(
                await session.call_tool("get_workspace", {"page_size": 100}),
                "get_workspace",
            )
            capability_readiness = {
                item.get("name"): item.get("models_ready")
                for item in workspace.get("capabilities", [])
                if isinstance(item, dict)
            }
            unavailable = sorted(
                modality
                for modality in REQUIRED_MODALITIES
                if capability_readiness.get(modality) is not True
            )
            if unavailable:
                raise RuntimeError(
                    "MCP workspace reports unready models for: "
                    + ", ".join(unavailable)
                )

            media = {
                item.get("original_filename"): item
                for item in workspace.get("media", [])
                if isinstance(item, dict)
            }
            missing_media = sorted(filenames - media.keys())
            if missing_media:
                raise RuntimeError(
                    "MCP workspace is missing pilot media: "
                    + ", ".join(missing_media)
                )
            for filename in sorted(filenames):
                item = media[filename]
                indexed = {
                    capability.get("name")
                    for capability in item.get("capabilities", [])
                    if isinstance(capability, dict) and capability.get("indexed") is True
                }
                if (
                    item.get("state") != "ready"
                    or item.get("in_active_snapshot") is not True
                    or not REQUIRED_MODALITIES.issubset(indexed)
                ):
                    raise RuntimeError(
                        f"MCP workspace is not fully indexed for {filename}."
                    )

    print(
        "VidXP MCP ready: exact server environment, required models, "
        f"{len(filenames)} indexed pilot videos, and evidence tools verified."
    )


if __name__ == "__main__":
    asyncio.run(_preflight())
