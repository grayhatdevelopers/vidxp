import base64
import unittest

from vidxp.core.cursors import (
    MAX_CURSOR_OFFSET,
    CursorError,
    decode_cursor,
    decode_offset_cursor,
    encode_cursor,
    encode_offset_cursor,
)


class CursorTests(unittest.TestCase):
    def test_cursor_rejects_noncanonical_or_malformed_base64(self):
        cursor = encode_cursor("test", {"offset": 1})

        for invalid in (
            cursor.rstrip("="),
            f"{cursor}=",
            f" {cursor}",
            cursor.replace("-", "+").replace("_", "/"),
            "not-base64!",
        ):
            if invalid == cursor:
                continue
            with self.subTest(cursor=invalid):
                with self.assertRaises(CursorError):
                    decode_cursor(invalid, "test")

        decoded = base64.urlsafe_b64decode(cursor)
        noncanonical = base64.b64encode(decoded).decode()
        if noncanonical != cursor:
            with self.assertRaises(CursorError):
                decode_cursor(noncanonical, "test")

    def test_offset_cursor_is_bounded_to_signed_database_bigint(self):
        cursor = encode_offset_cursor(MAX_CURSOR_OFFSET, scope="test")
        self.assertEqual(
            decode_offset_cursor(cursor, scope="test"),
            MAX_CURSOR_OFFSET,
        )

        with self.assertRaises(CursorError):
            encode_offset_cursor(MAX_CURSOR_OFFSET + 1, scope="test")

        oversized = encode_cursor(
            "test",
            {"offset": MAX_CURSOR_OFFSET + 1},
        )
        with self.assertRaises(CursorError):
            decode_offset_cursor(oversized, scope="test")


if __name__ == "__main__":
    unittest.main()
