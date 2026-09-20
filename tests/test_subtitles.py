from __future__ import annotations

import json
import unittest

from vidxp.application_models import SubtitleCue
from vidxp.subtitles import (
    format_srt,
    format_srt_timestamp,
    format_vtt,
    format_vtt_timestamp,
    records_to_cues,
)


class SubtitlesTests(unittest.TestCase):
    def test_format_srt_timestamp(self) -> None:
        self.assertEqual(format_srt_timestamp(0.0), "00:00:00,000")
        self.assertEqual(format_srt_timestamp(1.234), "00:00:01,234")
        self.assertEqual(format_srt_timestamp(65.5), "00:01:05,500")
        self.assertEqual(format_srt_timestamp(3661.042), "01:01:01,042")
        self.assertEqual(format_srt_timestamp(-5.0), "00:00:00,000")

    def test_format_vtt_timestamp(self) -> None:
        self.assertEqual(format_vtt_timestamp(0.0), "00:00:00.000")
        self.assertEqual(format_vtt_timestamp(1.234), "00:00:01.234")
        self.assertEqual(format_vtt_timestamp(65.5), "00:01:05.500")
        self.assertEqual(format_vtt_timestamp(3661.042), "01:01:01.042")
        self.assertEqual(format_vtt_timestamp(-5.0), "00:00:00.000")

    def test_format_srt_empty(self) -> None:
        self.assertEqual(format_srt([]), "")

    def test_format_srt_cues(self) -> None:
        cues = [
            SubtitleCue(start=1.0, end=3.5, text="Hello world!"),
            SubtitleCue(start=4.0, end=6.2, text="This is VidXP subtitle export."),
        ]
        result = format_srt(cues)
        expected = (
            "1\n"
            "00:00:01,000 --> 00:00:03,500\n"
            "Hello world!\n\n"
            "2\n"
            "00:00:04,000 --> 00:00:06,200\n"
            "This is VidXP subtitle export.\n"
        )
        self.assertEqual(result, expected)

    def test_format_vtt_empty(self) -> None:
        self.assertEqual(format_vtt([]), "WEBVTT\n")

    def test_format_vtt_cues(self) -> None:
        cues = [
            SubtitleCue(start=0.5, end=2.0, text="Welcome to VidXP."),
        ]
        result = format_vtt(cues)
        expected = (
            "WEBVTT\n\n"
            "1\n"
            "00:00:00.500 --> 00:00:02.000\n"
            "Welcome to VidXP.\n"
        )
        self.assertEqual(result, expected)

    def test_records_to_cues(self) -> None:
        records = [
            {
                "metadata": {"start": 5.0, "end": 7.0, "text": "Second phrase"},
            },
            {
                "metadata": {"start": 1.0, "end": 3.0, "text": "First phrase"},
            },
            {
                # Missing metadata start/end should be skipped
                "metadata": {"text": "Invalid phrase"},
            },
            {
                # Empty text should be skipped
                "metadata": {"start": 8.0, "end": 9.0, "text": "   "},
            },
            {
                # end < start should be skipped
                "metadata": {"start": 10.0, "end": 9.0, "text": "Backwards"},
            },
            {
                # Document fallback
                "metadata": {"start": 12.0, "end": 14.0},
                "document": "Document phrase",
            },
        ]
        cues = records_to_cues(records)
        self.assertEqual(len(cues), 3)
        self.assertEqual(cues[0].text, "First phrase")
        self.assertEqual(cues[0].start, 1.0)
        self.assertEqual(cues[0].end, 3.0)
        self.assertEqual(cues[1].text, "Second phrase")
        self.assertEqual(cues[1].start, 5.0)
        self.assertEqual(cues[2].text, "Document phrase")
        self.assertEqual(cues[2].start, 12.0)


if __name__ == "__main__":
    unittest.main()
