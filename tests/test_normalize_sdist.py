import io
import hashlib
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory
import unittest

from utils.normalize_sdist import normalize_sdist


class NormalizeSdistTests(unittest.TestCase):
    def test_rewrites_archive_with_reproducible_metadata(self):
        with TemporaryDirectory() as directory:
            archive_path = Path(directory) / "example.tar.gz"
            with tarfile.open(archive_path, "w:gz") as archive:
                for name, content in (
                    ("example/z-last.txt", b"last"),
                    ("example/a-first.txt", b"first"),
                ):
                    member = tarfile.TarInfo(name)
                    member.size = len(content)
                    member.uid = 501
                    member.gid = 20
                    member.uname = "builder"
                    member.gname = "staff"
                    member.mtime = 1
                    archive.addfile(member, io.BytesIO(content))

            normalize_sdist(archive_path, 1_700_000_000)
            first_digest = hashlib.sha256(archive_path.read_bytes()).digest()
            normalize_sdist(archive_path, 1_700_000_000)
            self.assertEqual(
                hashlib.sha256(archive_path.read_bytes()).digest(),
                first_digest,
            )

            with archive_path.open("rb") as compressed:
                compressed.seek(4)
                self.assertEqual(compressed.read(4), b"\0\0\0\0")

            with tarfile.open(archive_path, "r:gz") as archive:
                members = archive.getmembers()
                self.assertEqual(
                    [member.name for member in members],
                    ["example/a-first.txt", "example/z-last.txt"],
                )
                for member in members:
                    self.assertEqual(member.uid, 0)
                    self.assertEqual(member.gid, 0)
                    self.assertEqual(member.uname, "")
                    self.assertEqual(member.gname, "")
                    self.assertEqual(member.mtime, 1_700_000_000)
                self.assertEqual(
                    archive.extractfile("example/a-first.txt").read(),
                    b"first",
                )
                self.assertEqual(
                    archive.extractfile("example/z-last.txt").read(),
                    b"last",
                )

    def test_rejects_negative_source_date_epoch(self):
        with self.assertRaisesRegex(ValueError, "must be non-negative"):
            normalize_sdist(Path("unused.tar.gz"), -1)


if __name__ == "__main__":
    unittest.main()
