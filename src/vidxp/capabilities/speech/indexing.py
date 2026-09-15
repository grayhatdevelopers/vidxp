from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from vidxp.capabilities.speech.config import SegmentationMode, speech_config
from vidxp.capabilities.speech.models import get_embedder, get_whisper_model
from vidxp.capabilities.speech.segmentation import (
    DialoguePhrase,
    build_dialogue_phrases_from_words,
)
from vidxp.capabilities.speech.specs import (
    FASTER_WHISPER_MODEL,
    QWEN3_EMBEDDING_MODEL,
)
from vidxp.capabilities.speech.transcript import (
    flatten_transcript_words,
    save_transcript,
)
from vidxp.core.contracts import (
    CancellationToken,
    IndexConfig,
    StorageRecord,
    VideoSource,
    stable_source_id,
)
from vidxp.core.indexing_common import ProgressCallback, report_progress
from vidxp.ports import IndexStore, ModelRuntimePort


def build_dialogue_phrases(
    segments: Sequence[Mapping[str, Any]],
    *,
    words_per_phrase: int,
    segmentation_mode: SegmentationMode = "fixed_words",
    window_stride_words: int = 2,
) -> list[DialoguePhrase]:
    """Build searchable phrases from Whisper or supplied transcript segments."""

    return build_dialogue_phrases_from_words(
        flatten_transcript_words(segments),
        segmentation_mode=segmentation_mode,
        words_per_phrase=words_per_phrase,
        window_stride_words=window_stride_words,
    )


def transcribe_video(
    input_path: str | Path,
    *,
    config: IndexConfig,
    cancellation: CancellationToken,
    runtime: ModelRuntimePort,
    progress: ProgressCallback | None,
) -> tuple[list[Mapping[str, Any]], str | None]:
    import av
    from faster_whisper import BatchedInferencePipeline

    settings = speech_config(config)
    cancellation.raise_if_cancelled()
    with av.open(str(input_path)) as container:
        if not container.streams.audio:
            report_progress(
                progress,
                "dialogue_skipped",
                "No audio stream was found; speech indexing was skipped.",
            )
            return [], None
    report_progress(
        progress,
        "preparing_transcription_model",
        "Preparing transcription model: faster-whisper "
        f"{FASTER_WHISPER_MODEL.model_id}.",
    )
    whisper_model = get_whisper_model(runtime)
    report_progress(
        progress,
        "transcribing_audio",
        "Transcribing and timestamping the video audio.",
    )
    segments, info = BatchedInferencePipeline(
        model=whisper_model
    ).transcribe(
        str(input_path),
        batch_size=settings.transcription_batch_size,
        word_timestamps=True,
        vad_filter=True,
    )
    result = []
    for segment in segments:
        cancellation.raise_if_cancelled()
        result.append(
            {
                "text": segment.text,
                "start": segment.start,
                "end": segment.end,
                "words": [
                    {
                        "word": word.word,
                        "start": word.start,
                        "end": word.end,
                    }
                    for word in (segment.words or ())
                    if word.start is not None and word.end is not None
                ],
            }
        )
    return result, str(info.language)


def _speech_records(
    phrases: Sequence[DialoguePhrase],
    vectors,
    config: IndexConfig,
) -> list[StorageRecord]:
    records = []
    for phrase, vector in zip(phrases, vectors):
        source_id = stable_source_id(
            config.run_id,
            str(config.video_id),
            "speech",
            phrase.local_id,
            generation_id=config.generation_id,
        )
        records.append(
            StorageRecord(
                source_id=source_id,
                embedding=vector.tolist(),
                document=phrase.text,
                metadata={
                    **config.record_identity("speech", source_id),
                    "phrase_id": phrase.phrase_id,
                    "text": phrase.text,
                    "start": phrase.start,
                    "end": phrase.end,
                    "word_start": phrase.word_start,
                    "word_end": phrase.word_end,
                    "segmentation_mode": phrase.segmentation_mode,
                },
            )
        )
    return records


def index_speech(
    source: VideoSource,
    *,
    config: IndexConfig,
    storage: IndexStore,
    cancellation: CancellationToken,
    runtime: ModelRuntimePort,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    if config.video_id is None:
        raise ValueError("IndexConfig.video_id is required for indexing.")
    settings = speech_config(config)

    language = None
    if source.transcript is not None:
        segments = list(source.transcript)
    else:
        if source.path is None:
            raise ValueError(
                "Speech indexing requires a transcript or video path."
            )
        segments, language = transcribe_video(
            source.path,
            config=config,
            cancellation=cancellation,
            runtime=runtime,
            progress=progress,
        )

    words = flatten_transcript_words(segments)
    if words:
        save_transcript(
            config.run_directory,
            words,
            language=language,
        )

    phrases = build_dialogue_phrases_from_words(
        words,
        segmentation_mode=settings.segmentation_mode,
        words_per_phrase=settings.words_per_phrase,
        window_stride_words=settings.window_stride_words,
    )
    if not phrases:
        return {
            "dialogue_phrases": 0,
            "transcript_words": len(words),
            "segmentation_mode": settings.segmentation_mode,
            "language": language,
        }

    report_progress(
        progress,
        "preparing_speech_model",
        f"Preparing speech-search model: {QWEN3_EMBEDDING_MODEL.model_id}.",
        0,
        len(phrases),
    )
    encoder = get_embedder(runtime)
    report_progress(
        progress,
        "speech_indexing",
        "Indexing speech phrases.",
        0,
        len(phrases),
    )
    stored = 0
    for offset in range(0, len(phrases), settings.embedding_batch_size):
        cancellation.raise_if_cancelled()
        group = phrases[offset:offset + settings.embedding_batch_size]
        vectors = encoder.encode_document(
            [phrase.text for phrase in group],
            batch_size=len(group),
            convert_to_numpy=True,
            normalize_embeddings=settings.normalize_embeddings,
        )
        stored += storage.upsert(
            "speech",
            _speech_records(group, vectors, config),
            batch_size=config.storage_batch_size,
            cancellation=cancellation,
        )
        report_progress(
            progress,
            "speech_indexing",
            "Indexing speech phrases.",
            stored,
            len(phrases),
        )
    return {
        "dialogue_phrases": stored,
        "transcript_words": len(words),
        "segmentation_mode": settings.segmentation_mode,
        "language": language,
    }
