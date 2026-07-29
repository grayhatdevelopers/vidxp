from __future__ import annotations

from typing import Any, Mapping

from vidxp.capabilities.contracts import (
    CapabilityDefinition,
    CapabilityExecutor,
    CapabilityPlugin,
    OperationDefinition,
    PreparationContext,
    module_import_check,
)
from vidxp.capabilities.scene.config import SceneConfig
from vidxp.capabilities.scene.indexing import VISUAL_PROCESSOR
from vidxp.capabilities.scene.models import get_scene_model
from vidxp.capabilities.scene.specs import SIGLIP2_MODEL
from vidxp.capabilities.scene.operations import search_operation
from vidxp.capabilities.schemas import SearchInput, SearchResult
from vidxp.capabilities.visual import index_capabilities
from vidxp.core.contracts import IndexConfig, VideoSource
from vidxp.core.indexing_common import ProgressCallback

def prepare_models(
    context: PreparationContext,
    progress: ProgressCallback | None,
) -> tuple[str, ...]:
    SceneConfig.model_validate(context.settings)
    if progress is not None:
        progress(
            {
                "state": "preparing",
                "stage": "scene_model",
                "message": (
                    f"Preparing scene model: SigLIP2 {SIGLIP2_MODEL.model_id}"
                ),
            }
        )
    get_scene_model(context.runtime, download=True, progress=progress)
    return (SIGLIP2_MODEL.model_id,)


def model_manifest(
    config: IndexConfig,
    _sources: tuple[VideoSource, ...],
) -> Mapping[str, Any]:
    return {
        "scene": {
            **SIGLIP2_MODEL.identity(),
        }
    }


DEFINITION = CapabilityDefinition(
    name="scene",
    description="Index and search visual scenes.",
    extra="scene",
    config_model=SceneConfig,
    collection_name="scene",
    index_stage="visual_indexing",
    execution_group="visual",
    prepares_models=True,
    model_specs=(SIGLIP2_MODEL,),
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
            module_import_check(
                "Transformers import",
                "transformers",
                "AutoModel",
                "AutoProcessor",
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
