import tempfile
import unittest
from pathlib import Path

from reprocheck.fingerprint import FILESYSTEM_FINGERPRINT_ALGORITHM, fingerprint_repository


class FingerprintTests(unittest.TestCase):
    def test_fingerprint_is_stable_and_excludes_skipped_directories_and_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "z.txt").write_bytes(b"last")
            (root / "a.txt").write_bytes(b"first")
            (root / "build").mkdir()
            (root / "build" / "ignored.txt").write_bytes(b"ignored")

            outside_target = root.parent / "reprocheck-fingerprint-outside.txt"
            outside_target.write_bytes(b"outside-v1")
            self.addCleanup(outside_target.unlink, missing_ok=True)
            try:
                (root / "external-link").symlink_to(outside_target)
            except OSError as error:
                self.skipTest(f"symlinks are unavailable: {error}")

            initial = fingerprint_repository(root)

            self.assertEqual(initial.algorithm, FILESYSTEM_FINGERPRINT_ALGORITHM)
            self.assertEqual(initial.file_count, 2)
            self.assertEqual(len(initial.digest), 64)

            (root / "build" / "ignored.txt").write_bytes(b"different ignored bytes")
            outside_target.write_bytes(b"outside-v2")
            after_ignored_changes = fingerprint_repository(root)
            self.assertEqual(after_ignored_changes, initial)

            (root / "a.txt").write_bytes(b"changed")
            after_content_change = fingerprint_repository(root)
            self.assertNotEqual(after_content_change.digest, initial.digest)
            self.assertEqual(after_content_change.file_count, 2)

    def test_fingerprint_is_independent_of_filesystem_iteration_order(self) -> None:
        with (
            tempfile.TemporaryDirectory() as first_directory,
            tempfile.TemporaryDirectory() as second_directory,
        ):
            first_root = Path(first_directory)
            second_root = Path(second_directory)
            for name, content in (("b.txt", b"b"), ("a.txt", b"a"), ("nested/c.txt", b"c")):
                first_path = first_root / name
                second_path = second_root / name
                first_path.parent.mkdir(parents=True, exist_ok=True)
                second_path.parent.mkdir(parents=True, exist_ok=True)
                first_path.write_bytes(content)
                second_path.write_bytes(content)

            self.assertEqual(
                fingerprint_repository(first_root), fingerprint_repository(second_root)
            )

    def test_rejects_a_non_directory_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            file_path = Path(temporary_directory) / "not-a-repository"
            file_path.write_text("content")

            with self.assertRaises(NotADirectoryError):
                fingerprint_repository(file_path)


if __name__ == "__main__":
    unittest.main()
