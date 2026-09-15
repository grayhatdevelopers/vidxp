"""Timed word transcript: the shared source for dialogue segments."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from vidxp.core.manifest import write_json_atomic


TRANSCRIPT_SCHEMA_VERSION = 1
TRANSCRIPT_FILE = "speech_transcript.json"


@dataclass(frozen=True)
class TimedWord:
    """One recognized word with its video time range.

    Speaker labels can attach later without changing text or timing (#86).
    """

    text: str
    start: float
    end: float
    index: int


def _valid_interval(start: Any, end: Any, label: str) -> tuple[float, float]:
    start_value = float(start)
    end_value = float(end)
    if start_value < 0 or end_value <= start_value:
        raise ValueError(
            f"{label} must have a non-negative, non-zero interval; "
            f"received [{start_value}, {end_value}]."
        )
    return start_value, end_value


def _word_text(word: Mapping[str, Any]) -> str:
    return str(word.get("word", word.get("text", ""))).strip()


def flatten_transcript_words(
    segments: Sequence[Mapping[str, Any]],
) -> list[TimedWord]:
    """Normalize Whisper (or supplied) segments into ordered timed words."""

    words: list[TimedWord] = []
    for segment_index, segment in enumerate(segments):
        raw_words = segment.get("words") or []
        timestamped = [
            word
            for word in raw_words
            if _word_text(word)
            and word.get("start") is not None
            and word.get("end") is not None
        ]
        if timestamped:
            for word in timestamped:
                start, end = _valid_interval(
                    word["start"],
                    word["end"],
                    f"Transcript word {len(words)} in segment {segment_index}",
                )
                words.append(
                    TimedWord(
                        text=_word_text(word),
                        start=start,
                        end=end,
                        index=len(words),
                    )
                )
            continue

        text = str(segment.get("text", "")).strip()
        if not text:
            continue
        if segment.get("start") is None or segment.get("end") is None:
            raise ValueError(
                f"Transcript segment {segment_index} lacks start/end timestamps."
            )
        start, end = _valid_interval(
            segment["start"],
            segment["end"],
            f"Transcript segment {segment_index}",
        )
        tokens = text.split()
        if not tokens:
            continue
        duration = end - start
        for offset, token in enumerate(tokens):
            words.append(
                TimedWord(
                    text=token,
                    start=start + duration * offset / len(tokens),
                    end=start + duration * (offset + 1) / len(tokens),
                    index=len(words),
                )
            )
    return words


def save_transcript(
    run_directory: str | Path,
    words: Sequence[TimedWord],
    *,
    language: str | None = None,
) -> Path:
    path = Path(run_directory) / TRANSCRIPT_FILE
    write_json_atomic(
        path,
        {
            "schema_version": TRANSCRIPT_SCHEMA_VERSION,
            "language": language,
            "words": [
                {
                    "index": word.index,
                    "word": word.text,
                    "start": word.start,
                    "end": word.end,
                }
                for word in words
            ],
        },
    )
    return path


def load_transcript(run_directory: str | Path) -> list[TimedWord]:
    path = Path(run_directory) / TRANSCRIPT_FILE
    payload = json.loads(path.read_text(encoding="utf-8"))
    if int(payload.get("schema_version", 0)) != TRANSCRIPT_SCHEMA_VERSION:
        raise ValueError(f"Unsupported speech transcript schema in {path}.")
    words = []
    for item in payload.get("words") or ():
        start, end = _valid_interval(
            item["start"],
            item["end"],
            f"Stored transcript word {item.get('index', len(words))}",
        )
        words.append(
            TimedWord(
                text=str(item["word"]).strip(),
                start=start,
                end=end,
                index=int(item.get("index", len(words))),
            )
        )
    return words
