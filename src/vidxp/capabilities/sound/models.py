from __future__ import annotations

from dataclasses import dataclass
import sys
from typing import Any, Callable, Sequence

from vidxp.capabilities.sound.specs import (
    FINELAP_MODEL,
    PE_A_FRAME_HOP_SAMPLES,
    PE_A_FRAME_MODEL,
    PE_A_SAMPLE_RATE,
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


@dataclass(frozen=True)
class PEAFrameProvider:
    model: Any
    processor: Any
    device: str

    def encode_audio(self, pcm_windows: Sequence[bytes]) -> tuple[Any, ...]:
        """Return the checkpoint's 40 ms audio-frame embeddings."""
        import numpy as np
        import torch

        waveforms = []
        for pcm in pcm_windows:
            waveform = (
                np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
            )
            if waveform.size < PE_A_FRAME_HOP_SAMPLES:
                waveform = np.pad(
                    waveform,
                    (0, PE_A_FRAME_HOP_SAMPLES - waveform.size),
                )
            waveforms.append(waveform)
        inputs = self.processor.feature_extractor(
            waveforms,
            sampling_rate=PE_A_SAMPLE_RATE,
            padding=True,
            return_tensors="pt",
        )
        inputs = {name: value.to(self.device) for name, value in inputs.items()}
        with torch.inference_mode():
            embeddings = self.model.get_audio_embeds(**inputs).cpu()
        valid_embeddings = []
        for pcm, embedding in zip(pcm_windows, embeddings):
            valid_frames = min(
                embedding.shape[0],
                (len(pcm) // 2 + PE_A_FRAME_HOP_SAMPLES - 1)
                // PE_A_FRAME_HOP_SAMPLES,
            )
            valid_embeddings.append(embedding[:valid_frames])
        return tuple(valid_embeddings)

    def encode_text(self, query: str) -> list[float]:
        """Return PE-A's text vector from the same frame-level score space."""
        import torch

        inputs = self.processor.tokenizer(
            [query],
            return_tensors="pt",
            padding=True,
            truncation=True,
        )
        inputs = {name: value.to(self.device) for name, value in inputs.items()}
        with torch.inference_mode():
            # Transformers 5.14's convenience method omits the hidden-state
            # request it consumes. This is the same path used by model.forward.
            outputs = self.model.text_model(
                **inputs,
                output_hidden_states=True,
                return_dict=True,
            )
            embedding = self.model.text_audio_head(
                outputs.hidden_states[-1][:, 0]
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


def _offline_roberta_tokenizer(*, vocab_path: str, merges_path: str) -> Any:
    from transformers import RobertaTokenizer

    tokenizer = RobertaTokenizer(
        vocab=vocab_path,
        merges=merges_path,
        model_max_length=512,
    )
    if not tokenizer("sound", add_special_tokens=False)["input_ids"]:
        raise RuntimeError("The prepared FineLAP tokenizer has no lexical tokens.")
    return tokenizer


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
            return _offline_roberta_tokenizer(
                vocab_path=vocab_path,
                merges_path=merges_path,
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


def get_finelap_model(
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


def get_sound_model(
    runtime: ModelRuntimePort,
    *,
    download: bool = False,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> PEAFrameProvider:
    device = runtime.device_for("sound")
    key = PE_A_FRAME_MODEL.key(device)

    def load() -> PEAFrameProvider:
        from transformers import PeAudioFrameLevelModel, PeAudioProcessor

        snapshot = runtime.resolve_model(
            PE_A_FRAME_MODEL,
            download=download,
            progress=progress,
        )
        report_preparation(
            progress,
            "loading_model",
            f"Loading {PE_A_FRAME_MODEL.model_id}.",
        )
        common = {"local_files_only": True}
        model = PeAudioFrameLevelModel.from_pretrained(snapshot, **common).to(
            device
        )
        model.eval()
        runtime.record_compute_precision(
            PE_A_FRAME_MODEL.capability,
            loaded_compute_precision(
                model,
                fallback=PE_A_FRAME_MODEL.weights_precision,
            ),
        )
        return PEAFrameProvider(
            model=model,
            processor=PeAudioProcessor.from_pretrained(snapshot, **common),
            device=device,
        )

    return runtime.get_or_load(key, load)
