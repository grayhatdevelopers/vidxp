"""Build searchable dialogue segments from a timed word transcript."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from vidxp.capabilities.speech.config import SegmentationMode
from vidxp.capabilities.speech.transcript import TimedWord

# Punctuation that ends a spoken sentence when attached to a word.
_SENTENCE_END = re.compile(r'[.!?…]["\')\]]*$')


@dataclass(frozen=True)
class DialoguePhrase:
    phrase_id: int
    text: str
    start: float
    end: float
    word_start: int
    word_end: int
    segmentation_mode: SegmentationMode

    @property
    def local_id(self) -> str:
        """Stable ID for the same transcript span and segmentation mode."""

        return (
            f"{self.segmentation_mode}:"
            f"w{self.word_start:08d}-{self.word_end:08d}"
        )


def _phrase_from_words(
    words: Sequence[TimedWord],
    *,
    mode: SegmentationMode,
    phrase_id: int,
) -> DialoguePhrase:
    if not words:
        raise ValueError("A dialogue phrase requires at least one word.")
    return DialoguePhrase(
        phrase_id=phrase_id,
        text=" ".join(word.text for word in words),
        start=words[0].start,
        end=words[-1].end,
        word_start=words[0].index,
        word_end=words[-1].index,
        segmentation_mode=mode,
    )


def build_dialogue_phrases_from_words(
    words: Sequence[TimedWord],
    *,
    segmentation_mode: SegmentationMode = "fixed_words",
    words_per_phrase: int = 5,
    window_stride_words: int = 2,
) -> list[DialoguePhrase]:
    """Segment timed words into searchable phrases.

    ``fixed_words`` is the historical five-word baseline. Other modes exist so
    retrieval quality can be compared (see issues #76 and #89).
    """

    if words_per_phrase <= 0:
        raise ValueError("words_per_phrase must be greater than zero.")
    if window_stride_words <= 0:
        raise ValueError("window_stride_words must be greater than zero.")
    if not words:
        return []

    phrases: list[DialoguePhrase] = []
    if segmentation_mode == "fixed_words":
        for offset in range(0, len(words), words_per_phrase):
            phrases.append(
                _phrase_from_words(
                    words[offset:offset + words_per_phrase],
                    mode="fixed_words",
                    phrase_id=len(phrases),
                )
            )
        return phrases

    if segmentation_mode == "overlapping_windows":
        offset = 0
        while True:
            phrases.append(
                _phrase_from_words(
                    words[offset:offset + words_per_phrase],
                    mode="overlapping_windows",
                    phrase_id=len(phrases),
                )
            )
            if offset + words_per_phrase >= len(words):
                break
            offset += window_stride_words
            if offset >= len(words):
                break
        return phrases

    if segmentation_mode == "sentence":
        current: list[TimedWord] = []
        for word in words:
            current.append(word)
            if _SENTENCE_END.search(word.text) or len(current) >= words_per_phrase:
                phrases.append(
                    _phrase_from_words(
                        current,
                        mode="sentence",
                        phrase_id=len(phrases),
                    )
                )
                current = []
        if current:
            phrases.append(
                _phrase_from_words(
                    current,
                    mode="sentence",
                    phrase_id=len(phrases),
                )
            )
        return phrases

    raise ValueError(
        "segmentation_mode must be one of: fixed_words, "
        "overlapping_windows, sentence."
    )
