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
from vidxp.capabilities.action.config import VideoPrismConfig
from vidxp.capabilities.action.indexing import VISUAL_PROCESSOR
from vidxp.capabilities.action.models import get_videoprism_model
from vidxp.capabilities.action.operations import search_operation
from vidxp.capabilities.action.specs import VIDEOPRISM_MODEL
from vidxp.capabilities.visual import index_capabilities
from vidxp.core.contracts import IndexConfig, VideoSource
from vidxp.core.indexing_common import ProgressCallback, report_preparation


def prepare_models(
    context: PreparationContext,
    progress: ProgressCallback | None,
) -> tuple[str, ...]:
    VideoPrismConfig.model_validate(context.settings)
    report_preparation(
        progress,
        "videoprism_model",
        f"Preparing VideoPrism {VIDEOPRISM_MODEL.model_id}",
    )
    get_videoprism_model(context.runtime, download=True, progress=progress)
    return (VIDEOPRISM_MODEL.model_id,)


def model_manifest(
    config: IndexConfig,
    _sources: tuple[VideoSource, ...],
) -> Mapping[str, Any]:
    return {"action": VIDEOPRISM_MODEL.identity()}


DEFINITION = CapabilityDefinition(
    name="action",
    label="Action and motion search",
    description="Index and search multi-frame actions and motion.",
    extra="action",
    config_model=VideoPrismConfig,
    collection_name="action",
    index_stage="visual_indexing",
    execution_group="visual",
    prepares_models=True,
    roles=(CapabilityRole.searchable, CapabilityRole.queryable),
    model_specs=(VIDEOPRISM_MODEL,),
    operations={
        "search": OperationDefinition(
            input_model=SearchInput,
            output_model=SearchResult,
        )
    },
)


def create_executor() -> CapabilityExecutor:
    return CapabilityExecutor(
        indexer=index_capabilities,
        index_processor=VISUAL_PROCESSOR,
        operations={"search": search_operation},
        prepare=prepare_models,
        model_manifest=model_manifest,
        runtime_checks=(
            module_import_check("OpenCV import", "cv2", "VideoCapture"),
            module_import_check("Torch import", "torch"),
            module_import_check("Torchvision import", "torchvision"),
            module_import_check(
                "Transformers VideoPrism import",
                "transformers",
                "VideoPrismClipModel",
                "VideoPrismProcessor",
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
