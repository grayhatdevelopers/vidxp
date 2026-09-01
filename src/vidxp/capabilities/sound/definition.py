from __future__ import annotations

from typing import Any, Mapping

from vidxp.application_models import CapabilityRole
from vidxp.capabilities.contracts import (
    CapabilityDefinition,
    CapabilityExecutor,
    CapabilityPlugin,
    OperationDefinition,
    PreparationContext,
    module_import_check,
)
from vidxp.capabilities.schemas import SearchInput, SearchResult
from vidxp.capabilities.sound.config import SoundConfig
from vidxp.capabilities.sound.models import get_sound_model
from vidxp.capabilities.sound.operations import index_capability, search_operation
from vidxp.capabilities.sound.specs import FINELAP_MODEL, SOUND_MODEL_SPECS
from vidxp.core.contracts import IndexConfig, VideoSource
from vidxp.core.indexing_common import ProgressCallback, report_preparation


def prepare_models(
    context: PreparationContext,
    progress: ProgressCallback | None,
) -> tuple[str, ...]:
    SoundConfig.model_validate(context.settings)
    report_preparation(
        progress,
        "sound_model",
        f"Preparing sound model: {FINELAP_MODEL.model_id}",
    )
    get_sound_model(context.runtime, download=True, progress=progress)
    return tuple(spec.model_id for spec in SOUND_MODEL_SPECS)


def model_manifest(
    _config: IndexConfig,
    _sources: tuple[VideoSource, ...],
) -> Mapping[str, Any]:
    return {
        "sound": FINELAP_MODEL.identity(),
        "sound_text_assets": [
            spec.identity() for spec in SOUND_MODEL_SPECS[1:]
        ],
    }


DEFINITION = CapabilityDefinition(
    name="sound",
    label="Sound event search",
    description="Index and search music, environmental sounds, and audio events.",
    extra="sound",
    config_model=SoundConfig,
    collection_name="sound",
    index_stage="sound_indexing",
    execution_group="sound",
    prepares_models=True,
    roles=(CapabilityRole.searchable, CapabilityRole.queryable),
    model_specs=SOUND_MODEL_SPECS,
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
        prepare=prepare_models,
        model_manifest=model_manifest,
        runtime_checks=(
            module_import_check("PyAV audio import", "av", "AudioResampler"),
            module_import_check("NumPy import", "numpy"),
            module_import_check("Torch import", "torch"),
            module_import_check("TorchAudio import", "torchaudio"),
            module_import_check("timm import", "timm"),
            module_import_check(
                "Transformers FineLAP import",
                "transformers",
                "AutoConfig",
                "RobertaModel",
                "RobertaTokenizer",
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
