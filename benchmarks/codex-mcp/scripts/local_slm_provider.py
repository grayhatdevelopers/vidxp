from __future__ import annotations

import atexit
import asyncio
import os
import sys
import time
from contextlib import AsyncExitStack
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from pydantic import BaseModel, Field, field_validator
from pydantic_ai import Agent, ModelProfile, NativeOutput
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage, UsageLimits

from vidxp.application_models import Job, JobState, JobWaitResult, MediaPage
from vidxp.benchmarks.agent_ablation_score import DEFAULT_MAX_CANDIDATES
from vidxp.infrastructure.ollama_query import (
    create_local_answer_model,
    local_answer_model_settings,
)
from vidxp.local_answers import (
    LocalAnswerConfiguration,
    LocalAnswerError,
    ManagedOllamaSession,
    inspect_local_answers,
    load_local_answer_configuration,
    local_answer_spec,
)

from modality_probe import BENCHMARK_ROOT, _load_environment, _required_environment


REPOSITORY_ROOT = BENCHMARK_ROOT.parent.parent
ALLOWED_TOOLS = frozenset(
    {
        "list_media",
        "search_moments",
        "wait_job",
        "get_job_evidence",
    }
)
ROUTING_INSTRUCTIONS = """
Choose which indexed VidXP evidence types are relevant to locating the supplied
event. Return every relevant type and no unrelated type.

- scene: visible objects, setting, appearance, or visual state
- action: visible movement, activity, or change over time
- sound: non-speech audio, including environmental and mechanical sounds
- speech: spoken words or dialogue

Do not locate the event, predict timestamps, rewrite the query, or summarize
results. Your only task is modality selection.
""".strip()

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


class ModalityRoute(BaseModel):
    modalities: list[Literal["scene", "action", "sound", "speech"]] = Field(
        min_length=1,
        max_length=4,
    )

    @field_validator("modalities")
    @classmethod
    def _unique_modalities(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("Routing modalities must be unique.")
        return values


# Promptfoo imports providers without registering the module in sys.modules.
# Resolve postponed field annotations while this module namespace is available.
LocalAgentAnswer.model_rebuild(_types_namespace=globals())
ModalityRoute.model_rebuild(_types_namespace=globals())


class StdioMCPClient:
    """Call the fixed VidXP retrieval workflow and retain an auditable trace."""

    def __init__(self, parameters: StdioServerParameters) -> None:
        self.parameters = parameters
        self.session: ClientSession | None = None
        self._transport: AsyncExitStack | None = None
        self.calls: list[dict[str, Any]] = []

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

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name not in ALLOWED_TOOLS:
            raise RuntimeError(f"The local-SLM harness cannot call MCP tool {name}.")
        if self.session is None:
            raise RuntimeError("The VidXP MCP session is not initialized.")
        started_at = time.time()
        started = time.perf_counter()
        call = {
            "name": name,
            "arguments": arguments,
            "started_at": started_at,
        }
        try:
            result = await self.session.call_tool(name, arguments=arguments)
        except Exception as error:
            self.calls.append(
                {
                    **call,
                    "elapsed_seconds": time.perf_counter() - started,
                    "is_error": True,
                    "error_type": type(error).__name__,
                    "error_message": str(error)[:1000],
                }
            )
            raise
        messages = [
            item.text
            for item in result.content
            if getattr(item, "type", None) == "text"
        ]
        recorded_call = {
            **call,
            "elapsed_seconds": time.perf_counter() - started,
            "is_error": result.is_error,
        }
        if result.is_error:
            recorded_call["error_message"] = (
                "; ".join(messages) or f"VidXP tool {name} failed."
            )[:1000]
        self.calls.append(recorded_call)
        if result.is_error:
            raise RuntimeError("; ".join(messages) or f"VidXP tool {name} failed.")
        if not isinstance(result.structured_content, dict):
            raise RuntimeError(f"VidXP tool {name} returned no structured result.")
        return result.structured_content


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
    context_tokens: int,
) -> bool:
    global _runtime
    if _runtime is None:
        _runtime = ManagedOllamaSession(
            configured.model_copy(update={"base_url": base_url, "model": model_name}),
            context_tokens=context_tokens,
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
                **(
                    {"error_message": call["error_message"]}
                    if "error_message" in call
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
    model_settings: dict[str, Any],
    cold_start: bool,
    selected_modalities: list[str] | None = None,
    model_turns: int | None = None,
) -> dict[str, Any]:
    return {
        "agentRuntime": "local-slm",
        "agentRole": "modality-router",
        "model": model_identity,
        "modelSettings": model_settings,
        "modelTurns": model_turns,
        "coldStart": cold_start,
        "instructionProfile": "targeted-modality-router-v1",
        "selectedModalities": selected_modalities,
        "trace": _trace(calls, started_at),
        "skillCalls": [],
    }


def _token_usage(usage: RunUsage) -> dict[str, Any]:
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
    return token_usage


async def _loaded_context_tokens(*, base_url: str, model_name: str) -> int | None:
    """Read the context Ollama actually allocated to the loaded benchmark model."""

    parsed = urlsplit(base_url)
    management_root = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{management_root}/api/ps")
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    models = payload.get("models", [])
    if not isinstance(models, list):
        return None
    for model in models:
        if not isinstance(model, dict):
            continue
        if model.get("name") not in {model_name, f"{model_name}:latest"} and model.get(
            "model"
        ) not in {model_name, f"{model_name}:latest"}:
            continue
        context_length = model.get("context_length")
        if isinstance(context_length, int) and context_length > 0:
            return context_length
    return None


async def _record_actual_context(
    *,
    base_url: str,
    model_name: str,
    requested_context_tokens: int,
    model_settings: dict[str, Any],
) -> dict[str, Any]:
    actual = await _loaded_context_tokens(base_url=base_url, model_name=model_name)
    recorded = {**model_settings, "loadedContextTokens": actual}
    if actual is not None and actual < requested_context_tokens:
        raise RuntimeError(
            "Ollama loaded the benchmark model with "
            f"{actual} context tokens, below the required "
            f"{requested_context_tokens}. Stop the existing Ollama service so the "
            "saved VidXP runtime can start with the benchmark configuration."
        )
    return recorded


def _build_router(
    *,
    base_url: str,
    model_name: str,
    max_output_tokens: int,
    model_timeout_seconds: float,
) -> Agent[None, ModalityRoute]:
    model = create_local_answer_model(
        base_url=base_url,
        model_name=model_name,
    )

    settings = dict(
        local_answer_model_settings(
            max_tokens=max_output_tokens,
            timeout_seconds=model_timeout_seconds,
        )
    )
    return Agent(
        model,
        output_type=NativeOutput(ModalityRoute),
        instructions=ROUTING_INSTRUCTIONS,
        retries=0,
        model_settings=settings,
    )


def _benchmark_inputs(context: dict[str, Any] | None) -> tuple[str, str, str]:
    variables = (context or {}).get("vars")
    if not isinstance(variables, dict):
        raise ValueError("Promptfoo did not provide structured benchmark variables.")
    video_id = variables.get("video_id")
    query = variables.get("query")
    media_relpath = variables.get("media_relpath")
    if not isinstance(video_id, str) or not video_id.strip():
        raise ValueError("The local-SLM case requires a video_id variable.")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("The local-SLM case requires a query variable.")
    if not isinstance(media_relpath, str) or not media_relpath.strip():
        raise ValueError("The local-SLM case requires a media_relpath variable.")
    filename = media_relpath.replace("\\", "/").rsplit("/", 1)[-1]
    if not filename:
        raise ValueError("The local-SLM media_relpath has no filename.")
    return video_id.strip(), query.strip(), filename


async def _retrieve_evidence(
    *,
    client: StdioMCPClient,
    video_id: str,
    filename: str,
    query: str,
    modalities: list[str],
) -> LocalAgentAnswer:
    media_page = MediaPage.model_validate(
        await client.call("list_media", {"filename": filename})
    )
    exact_matches = [
        item
        for item in media_page.items
        if item.original_filename == filename
    ]
    if len(exact_matches) != 1:
        raise RuntimeError(
            f"Expected one registered VidXP video named {filename}, found "
            f"{len(exact_matches)}."
        )
    media_id = exact_matches[0].media_id

    submitted = Job.model_validate(
        await client.call(
            "search_moments",
            {
                "command": {
                    "media_id": media_id,
                    "query": query,
                    "modalities": modalities,
                    "top_k": DEFAULT_MAX_CANDIDATES,
                },
                "idempotency_key": f"local-slm-{uuid4().hex}",
            },
        )
    )
    job_id = submitted.job_id
    terminal = submitted.terminal
    state = submitted.state
    error = submitted.error

    observation_token: str | None = None
    while not terminal:
        wait_arguments: dict[str, Any] = {
            "job_id": job_id,
            "timeout_seconds": 30,
        }
        if observation_token is not None:
            wait_arguments["after_observation_token"] = observation_token
        waited = JobWaitResult.model_validate(
            await client.call("wait_job", wait_arguments)
        )
        terminal = waited.job.terminal
        state = waited.job.state
        error = waited.job.error
        observation_token = waited.job.observation_token

    if state != JobState.succeeded:
        raise RuntimeError(
            f"VidXP search job {job_id} ended in state {state.value}: "
            f"{error.message if error is not None else 'no error detail'}"
        )

    evidence = await client.call("get_job_evidence", {"job_id": job_id})
    board = evidence.get("board")
    if not isinstance(board, dict):
        raise RuntimeError("VidXP returned an invalid evidence board result.")
    tiles = board.get("tiles")
    if not isinstance(tiles, list):
        raise RuntimeError("VidXP evidence board returned an invalid tile collection.")

    candidates: list[Candidate] = []
    seen_evidence: set[str] = set()
    ready_tiles = sorted(
        (tile for tile in tiles if isinstance(tile, dict) and tile.get("state") == "ready"),
        key=lambda tile: tile.get("rank", 10**9),
    )
    for tile in ready_tiles:
        evidence_id = tile.get("evidence_id")
        if not isinstance(evidence_id, str) or evidence_id in seen_evidence:
            continue
        tile_modalities = tile.get("modalities")
        if not isinstance(tile_modalities, list) or not all(
            isinstance(modality, str) for modality in tile_modalities
        ):
            raise RuntimeError("VidXP evidence tile returned invalid modalities.")
        description = tile.get("display_text")
        rank = tile.get("rank")
        candidates.append(
            Candidate(
                start_seconds=tile.get("start"),
                end_seconds=tile.get("end"),
                modalities=tile_modalities,
                description=(
                    description
                    if isinstance(description, str) and description
                    else f"VidXP ranked evidence candidate {rank}."
                ),
                evidence_ids=[evidence_id],
            )
        )
        seen_evidence.add(evidence_id)
        if len(candidates) == DEFAULT_MAX_CANDIDATES:
            break

    return LocalAgentAnswer(
        video_id=video_id,
        answer=(
            f"VidXP returned {len(candidates)} ranked evidence candidate"
            f"{'s' if len(candidates) != 1 else ''}."
            if candidates
            else "VidXP returned no ready evidence candidates."
        ),
        source_job_id=job_id,
        candidates=candidates,
    )


async def _run_provider(
    *,
    config: dict[str, Any],
    context: dict[str, Any] | None,
) -> dict[str, Any]:
    video_id, query, filename = _benchmark_inputs(context)
    configured, base_url, model_name = _selected_model()
    context_tokens = _positive_option(config, "contextTokens")
    max_output_tokens = _positive_option(config, "maxOutputTokens")
    defaults = local_answer_spec().defaults
    recorded_model_settings = {
        "contextTokens": context_tokens,
        "maxOutputTokens": max_output_tokens,
        "reasoningEffort": "none",
        "temperature": defaults.temperature,
        "topP": defaults.top_p,
        "presencePenalty": defaults.presence_penalty,
        "role": "modality-selection-only",
    }
    cold_start = _ensure_runtime(
        configured,
        base_url=base_url,
        model_name=model_name,
        context_tokens=context_tokens,
    )
    model_identity = _require_local_model(
        configured,
        base_url=base_url,
        model_name=model_name,
    )
    started_at = time.time()
    usage = RunUsage()
    selected_modalities: list[str] | None = None
    client = StdioMCPClient(_mcp_parameters())
    try:
        router = _build_router(
            base_url=base_url,
            model_name=model_name,
            max_output_tokens=max_output_tokens,
            model_timeout_seconds=_positive_number_option(
                config,
                "modelTimeoutSeconds",
            ),
        )
        routed = await router.run(
            query,
            usage_limits=UsageLimits(request_limit=1),
            usage=usage,
        )
        selected_modalities = list(routed.output.modalities)
        await client.open()
        answer = await _retrieve_evidence(
            client=client,
            video_id=video_id,
            filename=filename,
            query=query,
            modalities=selected_modalities,
        )
    except Exception as error:
        recorded_model_settings = await _record_actual_context(
            base_url=base_url,
            model_name=model_name,
            requested_context_tokens=context_tokens,
            model_settings=recorded_model_settings,
        )
        return {
            "error": f"{type(error).__name__}: {error}",
            "cost": 0,
            "tokenUsage": _token_usage(usage),
            "metadata": _metadata(
                calls=client.calls,
                started_at=started_at,
                model_identity=model_identity,
                model_settings=recorded_model_settings,
                cold_start=cold_start,
                selected_modalities=selected_modalities,
                model_turns=usage.requests,
            ),
            "raw": {
                **_activity(client.calls),
                "usage": {
                    "requests": usage.requests,
                    "tool_calls": len(client.calls),
                },
            },
        }
    finally:
        await client.close()

    recorded_model_settings = await _record_actual_context(
        base_url=base_url,
        model_name=model_name,
        requested_context_tokens=context_tokens,
        model_settings=recorded_model_settings,
    )
    calls = client.calls
    return {
        "output": answer.model_dump_json(),
        "format": "json",
        "cost": 0,
        "tokenUsage": _token_usage(usage),
        "metadata": _metadata(
            calls=calls,
            started_at=started_at,
            model_identity=model_identity,
            model_settings=recorded_model_settings,
            cold_start=cold_start,
            selected_modalities=selected_modalities,
            model_turns=usage.requests,
        ),
        "raw": {
            **_activity(calls),
            "usage": {
                "requests": usage.requests,
                "tool_calls": len(calls),
            },
        },
    }


def call_api(
    prompt: str,
    options: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one router-assisted VidXP case through Promptfoo."""

    del prompt
    _load_environment()
    config = (options or {}).get("config", {})
    try:
        return asyncio.run(_run_provider(config=config, context=context))
    except (LocalAnswerError, RuntimeError, ValueError) as error:
        return {"error": str(error)}


async def _check_provider_wiring(*, base_url: str, model_name: str) -> int:
    """Exercise router schemas and MCP discovery without real model inference."""

    client = StdioMCPClient(_mcp_parameters())
    await client.open()
    try:
        if client.session is None:
            raise RuntimeError("The VidXP MCP session is not initialized.")
        response = await client.session.list_tools()
        tools = {tool.name for tool in response.tools}
        missing = ALLOWED_TOOLS.difference(tools)
        if missing:
            raise RuntimeError(
                "The local-SLM harness is missing VidXP MCP tools: "
                f"{', '.join(sorted(missing))}."
            )
        router = _build_router(
            base_url=base_url,
            model_name=model_name,
            max_output_tokens=1,
            model_timeout_seconds=1,
        )
        if router.model.profile.get("openai_chat_supports_max_completion_tokens"):
            raise RuntimeError(
                "The local-SLM agent is not using Ollama's max_tokens request field."
            )
        test_model = TestModel(
            custom_output_text=ModalityRoute(
                modalities=["scene"]
            ).model_dump_json(),
            profile=ModelProfile(supports_json_schema_output=True),
        )
        result = await router.run("A visible landscape.", model=test_model)
        if result.usage.requests != 1:
            raise RuntimeError("The local-SLM usage contract is unavailable.")
        parameters = test_model.last_model_request_parameters
        if parameters is None or parameters.function_tools:
            raise RuntimeError("The local-SLM router unexpectedly exposes tools.")
        if result.output.modalities != ["scene"] or client.calls:
            raise RuntimeError("The local-SLM no-inference wiring check was not isolated.")
        return len(ALLOWED_TOOLS)
    finally:
        await client.close()


def check_configuration() -> dict[str, Any]:
    """Validate the prepared runtime and agent wiring without model inference."""

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
    ModalityRoute.model_json_schema()
    LocalAgentAnswer.model_json_schema()
    tool_count = asyncio.run(
        _check_provider_wiring(base_url=base_url, model_name=model_name)
    )
    return {
        "model": model_name,
        "base_url": base_url,
        "runtime": str(configured.executable),
        "model_directory": str(configured.model_directory),
        "mcp_tools": tool_count,
    }


if __name__ == "__main__":
    if sys.argv[1:] != ["--check"]:
        raise SystemExit("Usage: local_slm_provider.py --check")
    try:
        checked = check_configuration()
        print(
            "Local SLM router ready without inference: "
            f"{checked['model']}, one structured routing decision, "
            f"{checked['mcp_tools']} harness-owned MCP tools, and deterministic "
            "evidence output verified."
        )
    except (RuntimeError, ValueError) as error:
        raise SystemExit(str(error)) from error
