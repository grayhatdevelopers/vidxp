from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from contextlib import AsyncExitStack
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import urlopen

import pydantic_core
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.tools import RunContext, ToolDefinition
from pydantic_ai.toolsets import AbstractToolset, ToolsetTool
from pydantic_ai.usage import UsageLimits

from vidxp.benchmarks.agent_ablation_score import (
    DEFAULT_MAX_CANDIDATES,
    DEFAULT_MAX_CHUNK_SECONDS,
    DEFAULT_MIN_CHUNK_SECONDS,
    DEFAULT_MIN_EVENT_COVERAGE,
    DEFAULT_TARGET_CHUNK_SECONDS,
    score_ablation_boundary,
    score_temporal_grounding,
)
from vidxp.settings import DEFAULT_LOCAL_QUERY_MODEL

from modality_probe import (
    BENCHMARK_ROOT,
    TASKS_PATH,
    _load_environment,
    _required_environment,
)


REPOSITORY_ROOT = BENCHMARK_ROOT.parent.parent
SKILL_PATH = REPOSITORY_ROOT / "plugins" / "vidxp" / "skills" / (
    "vidxp-find-video-evidence"
) / "SKILL.md"
PROMPT_PATH = BENCHMARK_ROOT / "prompts" / "video-evidence.txt"
DEFAULT_SLM_BASE_URL = "http://127.0.0.1:11434/v1"
MAX_MODEL_REQUESTS = 12
MAX_TOOL_CALLS = 10
ALLOWED_TOOLS = frozenset(
    {
        "get_workspace",
        "search_moments",
        "query_video",
        "wait_job",
        "get_job_evidence",
    }
)
TOOL_ARGUMENTS = pydantic_core.SchemaValidator(
    pydantic_core.core_schema.dict_schema(
        pydantic_core.core_schema.str_schema(),
        pydantic_core.core_schema.any_schema(),
    )
)


class Candidate(BaseModel):
    start_seconds: float
    end_seconds: float
    modalities: list[str]
    description: str
    evidence_ids: list[str]


class LocalAgentAnswer(BaseModel):
    video_id: str
    answer: str
    source_job_id: str | None
    candidates: list[Candidate] = Field(max_length=DEFAULT_MAX_CANDIDATES)


class StdioMCPToolset(AbstractToolset[None]):
    """Small Pydantic-AI bridge over the official MCP client."""

    def __init__(self, parameters: StdioServerParameters) -> None:
        self.parameters = parameters
        self.session: ClientSession | None = None
        self.exit_stack: AsyncExitStack | None = None
        self.calls: list[dict[str, Any]] = []

    @property
    def id(self) -> str:
        return "vidxp"

    async def __aenter__(self):
        stack = AsyncExitStack()
        read_stream, write_stream = await stack.enter_async_context(
            stdio_client(self.parameters)
        )
        session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
        await session.initialize()
        self.exit_stack = stack
        self.session = session
        return self

    async def __aexit__(self, *args: Any) -> bool | None:
        if self.exit_stack is not None:
            await self.exit_stack.aclose()
            self.exit_stack = None
            self.session = None
        return None

    async def get_tools(
        self, ctx: RunContext[None]
    ) -> dict[str, ToolsetTool[None]]:
        if self.session is None:
            raise RuntimeError("The VidXP MCP session is not initialized.")
        response = await self.session.list_tools()
        return {
            tool.name: ToolsetTool(
                toolset=self,
                tool_def=ToolDefinition(
                    name=tool.name,
                    description=tool.description,
                    parameters_json_schema=tool.input_schema,
                    return_schema=tool.output_schema,
                ),
                max_retries=ctx.max_retries,
                args_validator=TOOL_ARGUMENTS,
            )
            for tool in response.tools
            if tool.name in ALLOWED_TOOLS
        }

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[None],
        tool: ToolsetTool[None],
    ) -> Any:
        del ctx, tool
        if self.session is None:
            raise RuntimeError("The VidXP MCP session is not initialized.")
        started = time.perf_counter()
        result = await self.session.call_tool(name, arguments=tool_args)
        self.calls.append(
            {
                "name": name,
                "arguments": tool_args,
                "elapsed_seconds": time.perf_counter() - started,
                "is_error": result.is_error,
            }
        )
        if result.is_error:
            messages = [
                item.text
                for item in result.content
                if getattr(item, "type", None) == "text"
            ]
            raise ModelRetry("; ".join(messages) or f"VidXP tool {name} failed.")
        if result.structured_content is not None:
            return result.structured_content
        return [item.model_dump(mode="json", by_alias=True) for item in result.content]


def _positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError("SLM repetitions must be a positive integer.") from error
    if parsed < 1:
        raise ValueError("SLM repetitions must be a positive integer.")
    return parsed


def _require_local_model(base_url: str, model_name: str) -> dict[str, Any]:
    parsed = urlsplit(base_url)
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("The SLM benchmark requires a loopback Ollama endpoint.")
    management_url = base_url.removesuffix("/v1").rstrip("/") + "/api/tags"
    try:
        with urlopen(management_url, timeout=5) as response:  # noqa: S310
            payload = json.load(response)
    except Exception as error:
        raise RuntimeError(
            "The local-answer service is not running. Enable Local grounded "
            "answers in VidXP Desktop setup, leave VidXP open, and retry."
        ) from error
    models = payload.get("models", []) if isinstance(payload, dict) else []
    installed = {
        value
        for item in models
        if isinstance(item, dict)
        for value in (item.get("name"), item.get("model"))
        if isinstance(value, str)
    }
    if model_name not in installed:
        raise RuntimeError(
            f"The managed local-answer model {model_name} is not installed. "
            "Enable Local grounded answers in VidXP Desktop setup and retry."
        )
    return {"provider": "ollama", "model": model_name, "endpoint_scope": "loopback"}


def _skill_instructions() -> str:
    contents = SKILL_PATH.read_text(encoding="utf-8")
    sections = contents.split("---", 2)
    return sections[2].strip() if len(sections) == 3 else contents.strip()


def _task_prompt(task: dict[str, Any]) -> str:
    values = {
        **task,
        "target_chunk_seconds": DEFAULT_TARGET_CHUNK_SECONDS,
        "min_chunk_seconds": DEFAULT_MIN_CHUNK_SECONDS,
        "max_chunk_seconds": DEFAULT_MAX_CHUNK_SECONDS,
    }
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    for name, value in values.items():
        prompt = prompt.replace("{{ " + name + " }}", str(value))
    return prompt


def _trace(calls: list[dict[str, Any]], started_at: float) -> dict[str, Any]:
    return {
        "spans": [
            {
                "name": f"mcp__vidxp__{call['name']}",
                "start_time": started_at,
                "attributes": {
                    "codex.mcp.server": "vidxp",
                    "codex.mcp.tool": call["name"],
                    "codex.mcp.input": call["arguments"],
                },
            }
            for call in calls
        ]
    }


def _git_revision() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def _run_benchmark(repetitions: int) -> dict[str, Any]:
    _load_environment()
    base_url = os.environ.get("VIDXP_SLM_BASE_URL", DEFAULT_SLM_BASE_URL)
    model_name = os.environ.get("VIDXP_SLM_MODEL", DEFAULT_LOCAL_QUERY_MODEL)
    model_identity = _require_local_model(base_url, model_name)
    tasks = json.loads(TASKS_PATH.read_text(encoding="utf-8"))[1:]
    mcp_environment = {
        **os.environ,
        "VIDXP_ALLOW_MODEL_DOWNLOADS": "false",
        "VIDXP_MODEL_CACHE": _required_environment("VIDXP_MODEL_CACHE"),
    }
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
        env=mcp_environment,
        cwd=REPOSITORY_ROOT,
    )
    toolset = StdioMCPToolset(parameters)
    model = OllamaModel(model_name, provider=OllamaProvider(base_url=base_url))
    agent = Agent(
        model,
        output_type=LocalAgentAnswer,
        instructions=(
            "Follow the supplied VidXP skill. Use only its MCP tools, keep the "
            "answer grounded in one fresh source job, and finish with the requested "
            "structured result.\n\n" + _skill_instructions()
        ),
        toolsets=[toolset],
        retries=1,
        model_settings={"temperature": 0, "max_tokens": 2048, "timeout": 180},
    )

    started_at = datetime.now(timezone.utc)
    records: list[dict[str, Any]] = []
    async with agent:
        for repetition in range(1, repetitions + 1):
            offset = (repetition - 1) % len(tasks)
            for task in tasks[offset:] + tasks[:offset]:
                call_start = len(toolset.calls)
                run_started_at = time.time()
                started = time.perf_counter()
                try:
                    result = await agent.run(
                        _task_prompt(task),
                        usage_limits=UsageLimits(
                            request_limit=MAX_MODEL_REQUESTS,
                            tool_calls_limit=MAX_TOOL_CALLS,
                        ),
                    )
                except Exception as error:
                    elapsed = time.perf_counter() - started
                    records.append(
                        {
                            "task_id": task["id"],
                            "repetition": repetition,
                            "elapsed_seconds": elapsed,
                            "error_type": type(error).__name__,
                            "error": str(error)[:1000],
                            "mcp_calls": toolset.calls[call_start:],
                            "quality_passed": False,
                            "boundary_passed": False,
                        }
                    )
                    print(
                        f"{task['id']} repetition {repetition}: failed in "
                        f"{elapsed:.2f}s ({type(error).__name__})",
                        flush=True,
                    )
                    continue
                elapsed = time.perf_counter() - started
                output = result.output.model_dump(mode="json")
                calls = toolset.calls[call_start:]
                scoring_context = {
                    "vars": {
                        **task,
                        "modalities": json.dumps(task["modalities"]),
                        "expected_vidxp": True,
                        "allow_media_shell": False,
                        "target_chunk_seconds": DEFAULT_TARGET_CHUNK_SECONDS,
                        "min_chunk_seconds": DEFAULT_MIN_CHUNK_SECONDS,
                        "max_chunk_seconds": DEFAULT_MAX_CHUNK_SECONDS,
                        "min_event_coverage": DEFAULT_MIN_EVENT_COVERAGE,
                        "max_candidates": DEFAULT_MAX_CANDIDATES,
                    },
                    "trace": _trace(calls, run_started_at),
                }
                quality = score_temporal_grounding(json.dumps(output), scoring_context)
                boundary = score_ablation_boundary(json.dumps(output), scoring_context)
                records.append(
                    {
                        "task_id": task["id"],
                        "repetition": repetition,
                        "elapsed_seconds": elapsed,
                        "output": output,
                        "usage": asdict(result.usage()),
                        "mcp_calls": calls,
                        "metrics": quality["namedScores"],
                        "quality_passed": quality["pass"],
                        "boundary_passed": boundary["pass"],
                        "boundary_reason": boundary["reason"],
                    }
                )
                print(
                    f"{task['id']} repetition {repetition}: "
                    f"{'hit' if quality['pass'] else 'miss'} in {elapsed:.2f}s",
                    flush=True,
                )

    finished_at = datetime.now(timezone.utc)
    valid = [record for record in records if record["boundary_passed"]]
    hits = sum(record["quality_passed"] for record in valid)
    run_id = "slm-" + started_at.strftime("%Y-%m-%dT%H-%M-%SZ")
    result = {
        "schema_version": 1,
        "run_id": run_id,
        "status": "complete",
        "machine_id": _required_environment("VIDXP_EVAL_MACHINE_ID"),
        "git_revision": _git_revision(),
        "started_at": started_at.isoformat(),
        "completed_at": finished_at.isoformat(),
        "task_manifest_sha256": _sha256(TASKS_PATH),
        "skill_sha256": _sha256(SKILL_PATH),
        "condition": "vidxp-local-slm-mcp",
        "model": model_identity,
        "constraints": {
            "preindexed_media": True,
            "external_agent_calls": 0,
            "external_provider_cost_usd": 0,
            "candidate_limit": DEFAULT_MAX_CANDIDATES,
            "candidate_window_seconds": DEFAULT_TARGET_CHUNK_SECONDS,
            "network_endpoint": "loopback Ollama only",
            "available_agent_tools": sorted(ALLOWED_TOOLS),
            "local_media_path_available_to_agent": False,
            "max_model_requests_per_task": MAX_MODEL_REQUESTS,
            "max_tool_calls_per_task": MAX_TOOL_CALLS,
        },
        "summary": {
            "runs": len(records),
            "boundary_valid_runs": len(valid),
            "failed_runs": sum("error" in record for record in records),
            "hits": hits,
            "bounded_chunk_hit_at_3": hits / len(valid) if valid else None,
            "bounded_chunk_hit_at_1": (
                sum(record["metrics"]["bounded_chunk_hit_at_1"] for record in valid)
                / len(valid)
                if valid
                else None
            ),
            "mean_elapsed_seconds": (
                sum(record["elapsed_seconds"] for record in valid) / len(valid)
                if valid
                else None
            ),
            "local_input_tokens": sum(
                record.get("usage", {}).get("input_tokens", 0) for record in records
            ),
            "local_output_tokens": sum(
                record.get("usage", {}).get("output_tokens", 0) for record in records
            ),
            "local_model_requests": sum(
                record.get("usage", {}).get("requests", 0) for record in records
            ),
            "mcp_calls": sum(len(record["mcp_calls"]) for record in records),
        },
        "records": records,
    }
    destination = REPOSITORY_ROOT / "docs" / "benchmarking" / "runs" / f"{run_id}.json"
    destination.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(destination), "summary": result["summary"]}, indent=2))
    return result


def run_benchmark(repetitions: int) -> dict[str, Any]:
    return asyncio.run(_run_benchmark(repetitions))


if __name__ == "__main__":
    try:
        count = _positive_integer(sys.argv[1] if len(sys.argv) > 1 else "3")
        if len(sys.argv) > 2:
            raise ValueError("Usage: local_slm_benchmark.py [repetitions]")
        run_benchmark(count)
    except (RuntimeError, ValueError) as error:
        raise SystemExit(str(error)) from error
