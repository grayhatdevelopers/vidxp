from __future__ import annotations

import atexit
import asyncio
import json
import os
import sys
import time
from contextlib import AsyncExitStack
from typing import Any
from urllib.parse import urlsplit

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

from vidxp.benchmarks.agent_ablation_score import DEFAULT_MAX_CANDIDATES
from vidxp.local_answers import (
    LocalAnswerConfiguration,
    LocalAnswerError,
    ManagedOllamaSession,
    inspect_local_answers,
    load_local_answer_configuration,
)

from modality_probe import BENCHMARK_ROOT, _load_environment, _required_environment


REPOSITORY_ROOT = BENCHMARK_ROOT.parent.parent
SKILL_PATH = REPOSITORY_ROOT / "plugins" / "vidxp" / "skills" / (
    "vidxp-find-video-evidence"
) / "SKILL.md"
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

_runtime: ManagedOllamaSession | None = None


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
    """Expose one explicitly-owned MCP session as Pydantic-AI tools."""

    def __init__(self, parameters: StdioServerParameters) -> None:
        self.parameters = parameters
        self.session: ClientSession | None = None
        self._transport: AsyncExitStack | None = None
        self.calls: list[dict[str, Any]] = []

    @property
    def id(self) -> str:
        return "vidxp"

    async def open(self) -> None:
        if self._transport is not None:
            return
        transport = AsyncExitStack()
        try:
            read_stream, write_stream = await transport.enter_async_context(
                stdio_client(self.parameters)
            )
            session = await transport.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await session.initialize()
        except BaseException:
            await transport.aclose()
            raise
        self._transport = transport
        self.session = session

    async def close(self) -> None:
        transport = self._transport
        self._transport = None
        self.session = None
        if transport is not None:
            await transport.aclose()

    async def __aenter__(self):
        # Pydantic-AI may enter and exit toolsets in helper tasks. The MCP SDK's
        # AnyIO transport must instead be opened and closed by the same task.
        return self

    async def __aexit__(self, *args: Any) -> None:
        del args

    async def get_tools(self, ctx: RunContext[None]) -> dict[str, ToolsetTool[None]]:
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
        started_at = time.time()
        started = time.perf_counter()
        call = {
            "name": name,
            "arguments": tool_args,
            "started_at": started_at,
        }
        try:
            result = await self.session.call_tool(name, arguments=tool_args)
        except Exception as error:
            self.calls.append(
                {
                    **call,
                    "elapsed_seconds": time.perf_counter() - started,
                    "is_error": True,
                    "error_type": type(error).__name__,
                }
            )
            raise
        self.calls.append(
            {
                **call,
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


def _positive_option(config: dict[str, Any], name: str) -> int:
    value = config.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"The local-SLM provider requires a positive {name} value.")
    return value


def _positive_number_option(config: dict[str, Any], name: str) -> float:
    value = config.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"The local-SLM provider requires a positive {name} value.")
    return float(value)


def _skill_instructions() -> str:
    contents = SKILL_PATH.read_text(encoding="utf-8")
    sections = contents.split("---", 2)
    return sections[2].strip() if len(sections) == 3 else contents.strip()


def _selected_model() -> tuple[LocalAnswerConfiguration, str, str]:
    try:
        configured = load_local_answer_configuration()
    except (OSError, ValueError) as error:
        raise RuntimeError(
            "The saved local-answer configuration is invalid. Rerun "
            "`uv run --no-sync vidxp local-answers prepare --yes`."
        ) from error
    if configured is None:
        raise RuntimeError(
            "No saved local-answer runtime exists. Run `uv run --no-sync vidxp "
            "local-answers prepare --yes`."
        )
    base_url = os.environ.get("VIDXP_SLM_BASE_URL") or configured.base_url
    model_name = os.environ.get("VIDXP_SLM_MODEL") or configured.model
    parsed = urlsplit(base_url)
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("The local-SLM benchmark requires a loopback Ollama endpoint.")
    return configured, base_url, model_name


def _ensure_runtime(
    configured: LocalAnswerConfiguration,
    *,
    base_url: str,
    model_name: str,
) -> bool:
    global _runtime
    if _runtime is None:
        _runtime = ManagedOllamaSession(
            configured.model_copy(update={"base_url": base_url, "model": model_name})
        )
        _runtime.ensure_started()
        return True
    _runtime.ensure_started()
    return False


def _close_runtime() -> None:
    global _runtime
    if _runtime is not None:
        _runtime.close()
    _runtime = None


atexit.register(_close_runtime)


def _require_local_model(
    configured: LocalAnswerConfiguration,
    *,
    base_url: str,
    model_name: str,
) -> dict[str, str]:
    status = inspect_local_answers(
        base_url=base_url,
        model=model_name,
        configuration=configured,
    )
    if not status.ready:
        raise RuntimeError("; ".join(status.errors) or "The local model is not ready.")
    return {"provider": "ollama", "model": model_name, "endpoint_scope": "loopback"}


def _mcp_parameters() -> StdioServerParameters:
    environment = {
        **os.environ,
        "VIDXP_ALLOW_MODEL_DOWNLOADS": "false",
        "VIDXP_MODEL_CACHE": _required_environment("VIDXP_MODEL_CACHE"),
        # The benchmarked local model is the agent. Prevent query_video from
        # silently starting a second, unmetered model inside the MCP process.
        "VIDXP_SLM_BASE_URL": "",
        "VIDXP_SLM_MODEL": "",
    }
    return StdioServerParameters(
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
        env=environment,
        cwd=REPOSITORY_ROOT,
    )


def _trace(calls: list[dict[str, Any]], started_at: float) -> dict[str, Any]:
    return {
        "spans": [
            {
                "name": f"mcp__vidxp__{call['name']}",
                "start_time": call.get("started_at", started_at),
                "attributes": {
                    "codex.mcp.server": "vidxp",
                    "codex.mcp.tool": call["name"],
                    "codex.mcp.input": call["arguments"],
                },
            }
            for call in calls
        ]
    }


def _activity(calls: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "items": [
            {
                "type": "mcp_tool_call",
                "server": "vidxp",
                "tool": call["name"],
                "arguments": call["arguments"],
                "elapsed_seconds": call["elapsed_seconds"],
                "is_error": call["is_error"],
                **(
                    {"error_type": call["error_type"]}
                    if "error_type" in call
                    else {}
                ),
            }
            for call in calls
        ]
    }


def _metadata(
    *,
    calls: list[dict[str, Any]],
    started_at: float,
    model_identity: dict[str, str],
    cold_start: bool,
    model_turns: int | None = None,
) -> dict[str, Any]:
    return {
        "agentRuntime": "local-slm",
        "model": model_identity,
        "modelTurns": model_turns,
        "coldStart": cold_start,
        "trace": _trace(calls, started_at),
        "skillCalls": [
            {
                "name": "vidxp-find-video-evidence",
                "path": str(SKILL_PATH.relative_to(REPOSITORY_ROOT)),
            }
        ],
    }


def _build_agent(
    *,
    base_url: str,
    model_name: str,
    toolset: StdioMCPToolset,
    max_output_tokens: int,
    model_timeout_seconds: float,
) -> Agent[None, LocalAgentAnswer]:
    model = OllamaModel(model_name, provider=OllamaProvider(base_url=base_url))
    return Agent(
        model,
        output_type=LocalAgentAnswer,
        instructions=(
            "Follow the supplied VidXP skill. Use only its MCP tools, keep the "
            "answer grounded in one fresh source job, and finish with the requested "
            "structured result.\n\n" + _skill_instructions()
        ),
        toolsets=[toolset],
        retries=1,
        model_settings={
            "temperature": 0,
            "max_tokens": max_output_tokens,
            "timeout": model_timeout_seconds,
        },
    )


async def _run_agent(prompt: str, config: dict[str, Any]) -> dict[str, Any]:
    configured, base_url, model_name = _selected_model()
    cold_start = _ensure_runtime(
        configured,
        base_url=base_url,
        model_name=model_name,
    )
    model_identity = _require_local_model(
        configured,
        base_url=base_url,
        model_name=model_name,
    )
    toolset = StdioMCPToolset(_mcp_parameters())
    started_at = time.time()
    await toolset.open()
    try:
        agent = _build_agent(
            base_url=base_url,
            model_name=model_name,
            toolset=toolset,
            max_output_tokens=_positive_option(config, "maxOutputTokens"),
            model_timeout_seconds=_positive_number_option(
                config,
                "modelTimeoutSeconds",
            ),
        )
        try:
            result = await agent.run(
                prompt,
                usage_limits=UsageLimits(
                    request_limit=_positive_option(config, "maxModelRequests"),
                    tool_calls_limit=_positive_option(config, "maxToolCalls"),
                ),
            )
        except Exception as error:
            return {
                "error": f"{type(error).__name__}: {error}",
                "metadata": _metadata(
                    calls=toolset.calls,
                    started_at=started_at,
                    model_identity=model_identity,
                    cold_start=cold_start,
                ),
                "raw": _activity(toolset.calls),
            }
    finally:
        await toolset.close()

    usage = result.usage()
    token_usage: dict[str, Any] = {
        "prompt": usage.input_tokens,
        "completion": usage.output_tokens,
        "total": usage.input_tokens + usage.output_tokens,
        "cached": usage.cache_read_tokens,
        "numRequests": usage.requests,
    }
    reasoning_tokens = usage.details.get("reasoning_tokens")
    if isinstance(reasoning_tokens, int):
        token_usage["completionDetails"] = {"reasoning": reasoning_tokens}
    calls = toolset.calls
    return {
        "output": result.output.model_dump_json(),
        "format": "json",
        "cost": 0,
        "tokenUsage": token_usage,
        "metadata": _metadata(
            calls=calls,
            started_at=started_at,
            model_identity=model_identity,
            cold_start=cold_start,
            model_turns=usage.requests,
        ),
        "raw": {
            **_activity(calls),
            "usage": {
                "requests": usage.requests,
                "tool_calls": usage.tool_calls,
            },
        },
    }


def call_api(
    prompt: str,
    options: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one local agent case and return Promptfoo's provider response contract."""

    del context
    _load_environment()
    config = (options or {}).get("config", {})
    try:
        return asyncio.run(_run_agent(prompt, config))
    except (LocalAnswerError, RuntimeError, ValueError) as error:
        return {"error": str(error)}


def check_configuration() -> dict[str, Any]:
    """Validate the prepared local runtime without starting inference."""

    _load_environment()
    configured, base_url, model_name = _selected_model()
    if configured.executable is None or not configured.executable.is_file():
        raise RuntimeError("The saved local-answer executable is missing.")
    if configured.model_directory is None or not configured.model_directory.is_dir():
        raise RuntimeError("The saved local-answer model directory is missing.")
    for name in (
        "VIDXP_EVAL_DATA_DIR",
        "VIDXP_EVAL_INDEX_DIR",
        "VIDXP_MODEL_CACHE",
        "VIDXP_MCP_COMMAND",
        "VIDXP_EVAL_MACHINE_ID",
    ):
        _required_environment(name)
    # Construct the exact agent type without starting the runtime or inference.
    # This catches installed Pydantic-AI API incompatibilities during preflight.
    _build_agent(
        base_url=base_url,
        model_name=model_name,
        toolset=StdioMCPToolset(_mcp_parameters()),
        max_output_tokens=1,
        model_timeout_seconds=1,
    )
    return {
        "model": model_name,
        "base_url": base_url,
        "runtime": str(configured.executable),
        "model_directory": str(configured.model_directory),
    }


if __name__ == "__main__":
    if sys.argv[1:] != ["--check"]:
        raise SystemExit("Usage: local_slm_provider.py --check")
    try:
        print(json.dumps(check_configuration(), sort_keys=True))
    except (RuntimeError, ValueError) as error:
        raise SystemExit(str(error)) from error
