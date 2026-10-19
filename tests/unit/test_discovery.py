import tempfile
import unittest
from pathlib import Path, PurePosixPath

from reprocheck.discovery import build_repository_identity, discover_repository
from reprocheck.models import Ecosystem


class DiscoveryTests(unittest.TestCase):
    def test_discovers_files_markers_and_safe_symlink_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text("print('ok')\n")
            (root / "src" / "component.tsx").write_text("export const ok = true;\n")
            (root / "pyproject.toml").write_text("[project]\nname = 'demo'\n")
            (root / "requirements-dev.txt").write_text("pytest\n")
            (root / "package.json").write_text("{}\n")
            (root / "tsconfig.json").write_text("{}\n")
            (root / "Dockerfile").write_text("FROM scratch\n")
            (root / "compose.yaml").write_text("services: {}\n")
            (root / "go.mod").write_text("module example\n")
            (root / "Cargo.toml").write_text("[package]\nname = 'demo'\n")
            (root / "pom.xml").write_text("<project />\n")

            for directory_name in (
                ".git",
                ".venv",
                "node_modules",
                "__pycache__",
                "dist",
                "build",
                ".pytest_cache",
                "coverage",
            ):
                ignored_directory = root / directory_name
                ignored_directory.mkdir()
                (ignored_directory / "ignored.py").write_text("ignored\n")

            outside_target = root.parent / "reprocheck-discovery-outside.txt"
            outside_target.write_text("outside\n")
            self.addCleanup(outside_target.unlink, missing_ok=True)
            try:
                (root / "internal-link").symlink_to("src/app.py")
                (root / "external-link").symlink_to(outside_target)
                (root / "linked-directory").symlink_to(root / "src", target_is_directory=True)
            except OSError as error:
                self.skipTest(f"symlinks are unavailable: {error}")

            summary = discover_repository(root)

            paths = [repo_file.path for repo_file in summary.files]
            self.assertEqual(paths, sorted(paths))
            self.assertTrue(all(not path.startswith("/") for path in paths))
            self.assertTrue(all(PurePosixPath(path).as_posix() == path for path in paths))
            self.assertNotIn(".git/ignored.py", paths)
            self.assertNotIn("node_modules/ignored.py", paths)
            self.assertNotIn("linked-directory/app.py", paths)

            by_path = {repo_file.path: repo_file for repo_file in summary.files}
            self.assertEqual(by_path["src/app.py"].language, "python")
            self.assertEqual(by_path["src/component.tsx"].language, "typescript")
            self.assertFalse(by_path["src/app.py"].is_symlink)
            self.assertTrue(by_path["internal-link"].is_symlink)
            self.assertEqual(by_path["internal-link"].language, "symlink")
            self.assertTrue(by_path["external-link"].is_symlink)
            self.assertEqual(by_path["external-link"].language, "symlink-external")
            self.assertIsNone(by_path["external-link"].sha256)

            self.assertEqual(
                summary.ecosystems,
                (
                    Ecosystem.PYTHON,
                    Ecosystem.NODE,
                    Ecosystem.CONTAINER,
                    Ecosystem.GO,
                    Ecosystem.RUST,
                    Ecosystem.JAVA,
                ),
            )
            self.assertEqual(summary.root, str(root.resolve()))
            self.assertEqual(summary.total_files, len(summary.files))
            self.assertEqual(summary.ignored_files, 3)

    def test_rejects_a_non_directory_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            file_path = Path(temporary_directory) / "not-a-repository"
            file_path.write_text("content")

            with self.assertRaises(NotADirectoryError):
                discover_repository(file_path)

    def test_repository_identity_is_filesystem_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)

            identity = build_repository_identity(root)

            self.assertEqual(identity.root, str(root.resolve()))
            self.assertIsNone(identity.repository_id)
            self.assertIsNone(identity.revision)
            self.assertIsNone(identity.branch)
            self.assertIsNone(identity.remote_url)
            self.assertIsNone(identity.is_dirty)


if __name__ == "__main__":
    unittest.main()
