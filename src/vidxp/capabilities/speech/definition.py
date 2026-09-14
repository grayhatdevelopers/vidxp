from __future__ import annotations

from typing import Any, Mapping

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from vidxp.application_models import CapabilityRole
from vidxp.capabilities.contracts import (
    CapabilityDefinition,
    CapabilityExecutor,
    CapabilityPlugin,
    OperationDefinition,
    PreparationContext,
    module_import_check,
)
from vidxp.capabilities.speech.config import SpeechConfig
from vidxp.capabilities.speech.models import get_embedder, get_whisper_model
from vidxp.capabilities.speech.specs import (
    FASTER_WHISPER_MODEL,
    QWEN3_EMBEDDING_MODEL,
)
from vidxp.capabilities.speech.operations import (
    index_capability,
    search_operation,
)
from vidxp.capabilities.schemas import SearchInput, SearchResult
from vidxp.core.contracts import IndexConfig, VideoSource
from vidxp.core.indexing_common import ProgressCallback, report_preparation
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
    SpeechConfig.model_validate(context.settings)
    prepared = []

    def report(stage: str, message: str) -> None:
        report_preparation(progress, stage, message)

    report(
        "speech_model",
        f"Preparing speech-search model: {QWEN3_EMBEDDING_MODEL.model_id}",
    )
    get_embedder(context.runtime, download=True, progress=progress)
    prepared.append(QWEN3_EMBEDDING_MODEL.model_id)
    report(
        "transcription_model",
        "Preparing transcription model: faster-whisper "
        f"{FASTER_WHISPER_MODEL.model_id}",
    )
    get_whisper_model(context.runtime, download=True, progress=progress)
    prepared.append(FASTER_WHISPER_MODEL.model_id)
    return tuple(prepared)


def model_manifest(
    _config: IndexConfig,
    sources: tuple[VideoSource, ...],
) -> Mapping[str, Any]:
    result: dict[str, Any] = {
        "speech": {
            **QWEN3_EMBEDDING_MODEL.identity(),
        }
    }
    if any(source.transcript is None for source in sources):
        result["transcription"] = FASTER_WHISPER_MODEL.identity()
    return result


DEFINITION = CapabilityDefinition(
    name="speech",
    label="Speech search",
    description="Transcribe and search spoken words with timestamps.",
    extra="speech",
    config_model=SpeechConfig,
    collection_name="speech",
    index_stage="speech_indexing",
    execution_group="speech",
    prepares_models=True,
    roles=(CapabilityRole.searchable, CapabilityRole.queryable),
    model_specs=(QWEN3_EMBEDDING_MODEL, FASTER_WHISPER_MODEL),
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
        runtime_checks=(
            module_import_check(
                "faster-whisper import",
                "faster_whisper",
                "WhisperModel",
                "BatchedInferencePipeline",
            ),
            module_import_check(
                "Sentence Transformers import",
                "sentence_transformers",
                "SentenceTransformer",
            ),
            module_import_check(
                "Hugging Face Hub import",
                "huggingface_hub",
                "snapshot_download",
            ),
        ),
    )


PLUGIN = CapabilityPlugin(
    definition=DEFINITION,
    executor_factory=create_executor,
)
