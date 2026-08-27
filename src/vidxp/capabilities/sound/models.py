from __future__ import annotations

from dataclasses import dataclass
import sys
from typing import Any, Callable, Sequence

from vidxp.capabilities.sound.specs import (
    FINELAP_MODEL,
    ROBERTA_CONFIG,
    ROBERTA_MERGES,
    ROBERTA_VOCAB,
)
from vidxp.core.indexing_common import report_preparation
from vidxp.model_contracts import loaded_compute_precision
from vidxp.ports import ModelRuntimePort


@dataclass(frozen=True)
class FineLAPProvider:
    model: Any
    device: str

    def encode_audio(
        self,
        pcm_windows: Sequence[bytes],
    ) -> tuple[Any, Any]:
        """Return normalized global and dense embeddings in one audio pass."""
        import torch
        import torchaudio

        mels = []
        for pcm in pcm_windows:
            waveform = (
                torch.frombuffer(bytearray(pcm), dtype=torch.int16)
                .to(torch.float32)
                / 32768.0
            )
            if waveform.numel() < 400:
                waveform = torch.nn.functional.pad(
                    waveform,
                    (0, 400 - waveform.numel()),
                )
            waveform = waveform - waveform.mean()
            mel = torchaudio.compliance.kaldi.fbank(
                waveform.unsqueeze(0),
                htk_compat=True,
                sample_frequency=16_000,
                use_energy=False,
                window_type="hanning",
                num_mel_bins=128,
                dither=0.0,
                frame_shift=10,
            )
            if mel.shape[0] < 1024:
                mel = torch.nn.functional.pad(
                    mel,
                    (0, 0, 0, 1024 - mel.shape[0]),
                )
            else:
                mel = mel[:1024, :]
            mels.append((mel - (-4.268)) / (4.569 * 2))
        mel_batch = torch.stack(mels, dim=0).unsqueeze(1).to(self.device)

        with torch.inference_mode():
            outputs = self.model.audio_encoder.extract_features(mel_batch)
            raw = outputs["x"] if isinstance(outputs, dict) else outputs
            batch, tokens, width = raw[:, 1:, :].shape
            patches = raw[:, 1:, :].reshape(
                batch,
                tokens // 8,
                8,
                width,
            ).mean(dim=2)
            features = torch.cat([raw[:, 0:1, :], patches], dim=1)
            global_embeddings = torch.nn.functional.normalize(
                self.model.global_audio_proj(features[:, 0, :]),
                dim=-1,
            )
            dense_embeddings = self.model.local_audio_proj(features[:, 1:, :])
            if self.model.local_audio_proj_type == "rnn":
                dense_embeddings = dense_embeddings[0]
            if self.model.config.normalize_dense_audio_embeds:
                dense_embeddings = torch.nn.functional.normalize(
                    dense_embeddings,
                    dim=-1,
                )
        return global_embeddings.cpu(), dense_embeddings.cpu()

    def encode_text(self, query: str) -> list[float]:
        import torch

        with torch.inference_mode():
            embedding = self.model.get_global_text_embeds(
                [query],
                device=self.device,
            )
        return embedding.cpu().numpy().tolist()[0]


def _load_finelap_class(snapshot: str, module_cache: str) -> type:
    from transformers import AutoConfig
    from transformers import dynamic_module_utils

    dynamic_module_utils.HF_MODULES_CACHE = module_cache
    dynamic_module_utils.init_hf_modules()

    config = AutoConfig.from_pretrained(
        snapshot,
        trust_remote_code=True,
        local_files_only=True,
    )
    class_reference = config.auto_map["AutoModel"]
    return dynamic_module_utils.get_class_from_dynamic_module(
        class_reference,
        snapshot,
        local_files_only=True,
    )


def _load_finelap_model(
    model_class: type,
    snapshot: str,
    *,
    config_path: str,
    vocab_path: str,
    merges_path: str,
) -> Any:
    """Load pinned FineLAP code without an implicit RoBERTa download.

    FineLAP's checkpoint already contains the trained RoBERTa weights, but its
    constructor asks Transformers to fetch a second copy of roberta-base. The
    two small tokenizer assets are prepared explicitly; the temporary module
    factories only initialize the architecture that the FineLAP checkpoint
    immediately fills.
    """
    from transformers import AutoConfig, RobertaConfig, RobertaModel
    from transformers import RobertaTokenizer

    module = sys.modules[model_class.__module__]

    class OfflineRobertaModel:
        @classmethod
        def from_pretrained(cls, *_args: Any, **_kwargs: Any) -> Any:
            return RobertaModel(
                RobertaConfig.from_json_file(config_path),
                add_pooling_layer=False,
            )

    class OfflineRobertaTokenizer:
        @classmethod
        def from_pretrained(cls, *_args: Any, **_kwargs: Any) -> Any:
            return RobertaTokenizer(
                vocab_file=vocab_path,
                merges_file=merges_path,
                model_max_length=512,
            )

    module.RobertaModel = OfflineRobertaModel
    module.RobertaTokenizer = OfflineRobertaTokenizer
    config = AutoConfig.from_pretrained(
        snapshot,
        trust_remote_code=True,
        local_files_only=True,
    )
    return model_class.from_pretrained(
        snapshot,
        config=config,
        local_files_only=True,
    )


def get_sound_model(
    runtime: ModelRuntimePort,
    *,
    download: bool = False,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> FineLAPProvider:
    device = runtime.device_for("sound")
    key = FINELAP_MODEL.key(device)

    def load() -> FineLAPProvider:
        snapshot = runtime.resolve_model(
            FINELAP_MODEL,
            download=download,
            progress=progress,
        )
        config_path = runtime.resolve_artifact(
            ROBERTA_CONFIG,
            download=download,
            progress=progress,
        )
        vocab_path = runtime.resolve_artifact(
            ROBERTA_VOCAB,
            download=download,
            progress=progress,
        )
        merges_path = runtime.resolve_artifact(
            ROBERTA_MERGES,
            download=download,
            progress=progress,
        )
        report_preparation(
            progress,
            "loading_model",
            f"Loading {FINELAP_MODEL.model_id}.",
        )
        model_class = _load_finelap_class(
            str(snapshot),
            str(runtime.model_cache / "transformers_modules"),
        )
        model = _load_finelap_model(
            model_class,
            str(snapshot),
            config_path=str(config_path),
            vocab_path=str(vocab_path),
            merges_path=str(merges_path),
        ).to(device)
        model.eval()
        runtime.record_compute_precision(
            FINELAP_MODEL.capability,
            loaded_compute_precision(
                model,
                fallback=FINELAP_MODEL.weights_precision,
            ),
        )
        return FineLAPProvider(model=model, device=device)

    return runtime.get_or_load(key, load)
