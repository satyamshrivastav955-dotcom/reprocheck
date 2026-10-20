import tempfile
import unittest
from pathlib import Path

from reprocheck.verification.artifacts import (
    ArtifactError,
    artifacts_match,
    collect_artifacts,
    normalize_artifact_paths,
)


class ArtifactManifestTests(unittest.TestCase):
    def test_directory_manifest_is_stable_and_detects_byte_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "dist"
            output.mkdir()
            (output / "b.txt").write_text("b", encoding="utf-8")
            (output / "a.txt").write_text("a", encoding="utf-8")

            first = collect_artifacts(root, ("dist",))
            second = collect_artifacts(root, ("dist",))

            self.assertTrue(first.complete)
            self.assertEqual(first, second)
            self.assertEqual([item.path for item in first.files], ["dist/a.txt", "dist/b.txt"])

            (output / "a.txt").write_text("changed", encoding="utf-8")
            changed = collect_artifacts(root, ("dist",))
            self.assertTrue(changed.complete)
            self.assertFalse(artifacts_match(first, changed))

    def test_missing_empty_and_unselected_artifacts_are_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "empty").mkdir()

            missing = collect_artifacts(root, ("missing.txt",))
            empty = collect_artifacts(root, ("empty",))
            unselected = collect_artifacts(root, ())

            self.assertFalse(missing.complete)
            self.assertEqual(missing.missing, ("missing.txt",))
            self.assertFalse(empty.complete)
            self.assertFalse(unselected.complete)

    def test_artifact_paths_cannot_escape_or_follow_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "safe.txt").write_text("safe", encoding="utf-8")
            with self.assertRaises(ArtifactError):
                collect_artifacts(root, ("../safe.txt",))

            outside = root.parent / "reprocheck-artifact-outside.txt"
            outside.write_text("outside", encoding="utf-8")
            self.addCleanup(outside.unlink, missing_ok=True)
            try:
                (root / "link.txt").symlink_to(outside)
            except OSError as error:
                self.skipTest(f"symlinks are unavailable: {error}")
            with self.assertRaises(ArtifactError):
                collect_artifacts(root, ("link.txt",))

    def test_artifact_path_validation_is_deterministic_before_execution(self) -> None:
        self.assertEqual(
            normalize_artifact_paths(("dist/b.txt", "dist", "dist/a.txt")),
            ("dist", "dist/a.txt", "dist/b.txt"),
        )
        with self.assertRaises(ArtifactError):
            normalize_artifact_paths(("/absolute/output.txt",))
        with self.assertRaises(ArtifactError):
            normalize_artifact_paths(("../outside.txt",))
        with self.assertRaises(ArtifactError):
            normalize_artifact_paths((r"C:\outside.txt",))


if __name__ == "__main__":
    unittest.main()
