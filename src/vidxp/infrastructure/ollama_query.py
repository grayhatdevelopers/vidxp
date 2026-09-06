from __future__ import annotations

import httpx
from openai import OpenAIError
from pydantic import ValidationError
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.exceptions import AgentRunError
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.models.openai import OpenAIChatModelSettings
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.ollama import OllamaProvider

from vidxp.application_models import (
    DraftAnswer,
    QueryModelIdentity,
    QueryPlan,
    QueryPlanningRequest,
    QuerySynthesisRequest,
)
from vidxp.local_answers import LocalAnswerError, ManagedOllamaSession
from vidxp.ports import QueryProviderError


_PLANNING_INSTRUCTIONS = """
Return exactly one search_moments step for every allowed modality, using a
short retrieval query derived from the question. Include actor_overview
exactly once when actor_overview_allowed is true. Do not invent modalities,
filters, identifiers, limits, paths, model names, or operations.
""".strip()

_SYNTHESIS_INSTRUCTIONS = """
Write concise factual claims supported only by the supplied evidence. Every
claim must cite one or more evidence_id values exactly as supplied. Do not
infer visual facts from similarity scores, introduce outside knowledge, or
mention evidence that was not supplied.
""".strip()


def local_answer_model_settings(
    *,
    max_tokens: int,
    timeout_seconds: float,
) -> OpenAIChatModelSettings:
    """Return the shared request settings for VidXP's managed local model."""

    return {
        "temperature": 0,
        "openai_reasoning_effort": "none",
        "max_tokens": max_tokens,
        "timeout": timeout_seconds,
    }


def create_local_answer_model(
    *,
    base_url: str,
    model_name: str,
    http_client: httpx.AsyncClient | None = None,
) -> OllamaModel:
    """Create the managed Ollama model with its compatible request profile."""

    return OllamaModel(
        model_name,
        provider=OllamaProvider(
            base_url=base_url,
            http_client=http_client,
        ),
        profile=OpenAIModelProfile(
            openai_chat_supports_max_completion_tokens=False,
        ),
    )


class OllamaQueryModel:
    """Structured-output adapter for a configured self-hosted Ollama server."""

    def __init__(
        self,
        *,
        base_url: str,
        model_name: str,
        timeout_seconds: float,
        output_retries: int,
        http_client: httpx.AsyncClient | None = None,
        runtime: ManagedOllamaSession | None = None,
    ) -> None:
        model = create_local_answer_model(
            base_url=base_url,
            model_name=model_name,
            http_client=http_client,
        )
        retries = {"output": output_retries}
        self._identity = QueryModelIdentity(
            provider="ollama",
            model=model_name,
        )
        self._runtime = runtime
        self._planner = Agent(
            model,
            output_type=NativeOutput(QueryPlan),
            instructions=_PLANNING_INSTRUCTIONS,
            retries=retries,
            model_settings=local_answer_model_settings(
                max_tokens=1024,
                timeout_seconds=timeout_seconds,
            ),
        )
        self._synthesizer = Agent(
            model,
            output_type=NativeOutput(DraftAnswer),
            instructions=_SYNTHESIS_INSTRUCTIONS,
            retries=retries,
            model_settings=local_answer_model_settings(
                max_tokens=2048,
                timeout_seconds=timeout_seconds,
            ),
        )

    @property
    def identity(self) -> QueryModelIdentity:
        return self._identity

    def plan(self, request: QueryPlanningRequest) -> QueryPlan:
        return self._run(self._planner, request.model_dump_json())

    def synthesize(self, request: QuerySynthesisRequest) -> DraftAnswer:
        return self._run(self._synthesizer, request.model_dump_json())

    def _run(self, agent: Agent, prompt: str):
        try:
            if self._runtime is not None:
                self._runtime.ensure_started()
            return agent.run_sync(prompt).output
        except (
            AgentRunError,
            LocalAnswerError,
            OpenAIError,
            httpx.HTTPError,
            TimeoutError,
            ValidationError,
        ) as exc:
            raise QueryProviderError(
                "The configured Ollama query model is unavailable."
            ) from exc

    def close(self) -> None:
        if self._runtime is not None:
            self._runtime.close()
