from __future__ import annotations

from typing import Any, Mapping

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from vidxp.capabilities.contracts import (
    CapabilityDefinition,
    CapabilityExecutor,
    CapabilityPlugin,
    OperationDefinition,
    PreparationContext,
)
from vidxp.capabilities.dialogue.config import DialogueConfig, dialogue_config
from vidxp.capabilities.dialogue.models import get_embedder, get_whisper_model
from vidxp.capabilities.dialogue.operations import (
    index_capability,
    search_operation,
)
from vidxp.capabilities.schemas import SearchInput, SearchResult
from vidxp.core.contracts import IndexConfig, VideoSource
from vidxp.core.indexing_common import ProgressCallback
def filter_requirements_for_source(
    source: VideoSource,
    requirements: tuple[Requirement, ...],
) -> tuple[Requirement, ...]:
    if source.transcript is not None:
        needed = {"chromadb", "sentence-transformers"}
        return tuple(
            requirement
            for requirement in requirements
            if canonicalize_name(requirement.name) in needed
        )
    return requirements


def prepare_models(
    context: PreparationContext,
    progress: ProgressCallback | None,
) -> tuple[str, ...]:
    settings = DialogueConfig.model_validate(context.settings)
    prepared = []

    def report(stage: str, message: str) -> None:
        if progress is not None:
            progress(
                {
                    "state": "preparing",
                    "stage": stage,
                    "message": message,
                }
            )

    report(
        "dialogue_model",
        f"Preparing dialogue model: {settings.sentence_model}",
    )
    get_embedder(
        context.runtime,
        settings.sentence_model,
        settings.sentence_revision,
    )
    prepared.append(settings.sentence_model)
    report(
        "transcription_model",
        f"Preparing transcription model: faster-whisper {settings.whisper_model}",
    )
    get_whisper_model(
        context.runtime,
        settings.whisper_model,
        settings.whisper_revision,
    )
    prepared.append(settings.whisper_model)
    return tuple(prepared)


def model_manifest(
    config: IndexConfig,
    sources: tuple[VideoSource, ...],
) -> Mapping[str, Any]:
    settings = dialogue_config(config)
    result: dict[str, Any] = {
        "dialogue": {
            "provider": "sentence-transformers",
            "model": settings.sentence_model,
            "revision": settings.sentence_revision,
        }
    }
    if any(source.transcript is None for source in sources):
        result["transcription"] = {
            "provider": "faster-whisper",
            "model": settings.whisper_model,
            "revision": settings.whisper_revision,
        }
    return result


DEFINITION = CapabilityDefinition(
    name="dialogue",
    description="Index and search spoken dialogue.",
    extra="dialogue",
    config_model=DialogueConfig,
    collection_name="dialogue",
    index_stage="dialogue_indexing",
    execution_group="dialogue",
    prepares_models=True,
    operations={
        "search": OperationDefinition(
            input_model=SearchInput,
            output_model=SearchResult,
        )
    },
)


def create_executor() -> CapabilityExecutor:
    return CapabilityExecutor(
        indexer=index_capability,
        operations={"search": search_operation},
        requirement_filter=filter_requirements_for_source,
        prepare=prepare_models,
        model_manifest=model_manifest,
    )


PLUGIN = CapabilityPlugin(
    definition=DEFINITION,
    executor_factory=create_executor,
)
