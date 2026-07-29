from __future__ import annotations

import httpx
from openai import OpenAIError
from pydantic import ValidationError
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.exceptions import AgentRunError
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.providers.ollama import OllamaProvider

from vidxp.application_models import (
    DraftAnswer,
    QueryModelIdentity,
    QueryPlan,
    QueryPlanningRequest,
    QuerySynthesisRequest,
)
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
    ) -> None:
        model = OllamaModel(
            model_name,
            provider=OllamaProvider(
                base_url=base_url,
                http_client=http_client,
            ),
        )
        retries = {"output": output_retries}
        self._identity = QueryModelIdentity(
            provider="ollama",
            model=model_name,
        )
        self._planner = Agent(
            model,
            output_type=NativeOutput(QueryPlan),
            instructions=_PLANNING_INSTRUCTIONS,
            retries=retries,
            model_settings={
                "temperature": 0,
                "max_tokens": 1024,
                "timeout": timeout_seconds,
            },
        )
        self._synthesizer = Agent(
            model,
            output_type=NativeOutput(DraftAnswer),
            instructions=_SYNTHESIS_INSTRUCTIONS,
            retries=retries,
            model_settings={
                "temperature": 0,
                "max_tokens": 2048,
                "timeout": timeout_seconds,
            },
        )

    @property
    def identity(self) -> QueryModelIdentity:
        return self._identity

    def plan(self, request: QueryPlanningRequest) -> QueryPlan:
        return self._run(self._planner, request.model_dump_json())

    def synthesize(self, request: QuerySynthesisRequest) -> DraftAnswer:
        return self._run(self._synthesizer, request.model_dump_json())

    @staticmethod
    def _run(agent: Agent, prompt: str):
        try:
            return agent.run_sync(prompt).output
        except (
            AgentRunError,
            OpenAIError,
            httpx.HTTPError,
            TimeoutError,
            ValidationError,
        ) as exc:
            raise QueryProviderError(
                "The configured Ollama query model is unavailable."
            ) from exc
