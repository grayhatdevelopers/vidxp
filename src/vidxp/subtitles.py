from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from vidxp.application_models import SubtitleCue


def format_srt_timestamp(seconds: float) -> str:
    """Format seconds into SubRip timestamp format: HH:MM:SS,mmm."""
    total_ms = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(total_ms, 3600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def format_vtt_timestamp(seconds: float) -> str:
    """Format seconds into WebVTT timestamp format: HH:MM:SS.mmm."""
    total_ms = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(total_ms, 3600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def format_srt(cues: Sequence[SubtitleCue]) -> str:
    """Render a sequence of subtitle cues into SubRip (.srt) format."""
    if not cues:
        return ""
    blocks: list[str] = []
    for index, cue in enumerate(cues, start=1):
        start = format_srt_timestamp(cue.start)
        end = format_srt_timestamp(cue.end)
        text = cue.text.strip()
        blocks.append(f"{index}\n{start} --> {end}\n{text}")
    return "\n\n".join(blocks) + "\n"


def format_vtt(cues: Sequence[SubtitleCue]) -> str:
    """Render a sequence of subtitle cues into WebVTT (.vtt) format."""
    if not cues:
        return "WEBVTT\n"
    blocks: list[str] = ["WEBVTT"]
    for index, cue in enumerate(cues, start=1):
        start = format_vtt_timestamp(cue.start)
        end = format_vtt_timestamp(cue.end)
        text = cue.text.strip()
        blocks.append(f"{index}\n{start} --> {end}\n{text}")
    return "\n\n".join(blocks) + "\n"


def records_to_cues(records: Iterable[Mapping[str, Any]]) -> list[SubtitleCue]:
    """Convert index storage records into sorted SubtitleCue items."""
    cues: list[SubtitleCue] = []
    for rec in records:
        meta = rec.get("metadata") or {}
        start_val = meta.get("start")
        end_val = meta.get("end")
        if start_val is None or end_val is None:
            continue
        start = float(start_val)
        end = float(end_val)
        text = str(meta.get("text", rec.get("document", ""))).strip()
        if text and end >= start:
            cues.append(SubtitleCue(start=start, end=end, text=text))
    cues.sort(key=lambda cue: (cue.start, cue.end))
    return cues
