import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reprocheck.fingerprint import fingerprint_repository
from reprocheck.models import ContentFingerprint
from reprocheck.verification import ExperimentStatus, run_experiment


def _write_fixture(root: Path) -> None:
    (root / "pyproject.toml").write_text(
        "[project]\nname = 'experiment-fixture'\nversion = '0.1.0'\n",
        encoding="utf-8",
    )
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_placeholder.py").write_text(
        "def test_placeholder(): pass\n", encoding="utf-8"
    )


def _write_fake_uv(bin_directory: Path, *, mode: str) -> None:
    script = bin_directory / ("uv.py" if sys.platform == "win32" else "uv")
    script.write_text(
        "#! /usr/bin/env python3\n"
        "from pathlib import Path\n"
        "import uuid\n"
        "import sys\n"
        f"mode = {mode!r}\n"
        "args = sys.argv[1:]\n"
        "if args[:2] == ['sync', '--locked']:\n"
        "    raise SystemExit(0)\n"
        "if args[:3] == ['run', 'python', '-m']:\n"
        "    counter = Path(__file__).with_name('counter')\n"
        "    count = int(counter.read_text()) if counter.exists() else 0\n"
        "    counter.write_text(str(count + 1))\n"
        "    if mode == 'fail':\n"
        "        raise SystemExit(3)\n"
        "    if mode == 'first-pass-second-fail' and count > 0:\n"
        "        raise SystemExit(3)\n"
        "    if mode == 'stable':\n"
        "        value = 'stable-artifact\\n'\n"
        "    else:\n"
        "        value = str(uuid.uuid4()) + '\\n'\n"
        "    Path('artifact.txt').write_text(value, encoding='utf-8')\n"
        "    raise SystemExit(0)\n"
        "raise SystemExit(2)\n",
        encoding="utf-8",
    )
    if sys.platform == "win32":
        (bin_directory / "uv.cmd").write_text(
            f'@"{sys.executable}" "%~dp0uv.py" %*\n', encoding="utf-8"
        )
    else:
        script.chmod(0o755)


class ExperimentTests(unittest.TestCase):
    def _run(self, *, mode: str, approved: bool = True):
        with (
            tempfile.TemporaryDirectory() as directory,
            tempfile.TemporaryDirectory(dir=Path.cwd()) as bin_path,
        ):
            root = Path(directory)
            bin_directory = Path(bin_path)
            _write_fixture(root)
            _write_fake_uv(bin_directory, mode=mode)
            original_path = os.environ.get("PATH", "")
            os.environ["PATH"] = str(bin_directory) + os.pathsep + original_path
            try:
                return run_experiment(
                    root,
                    profile="python-uv",
                    backend="host",
                    approved=approved,
                    allow_host_exec=True,
                    artifact_paths=("artifact.txt",),
                )
            finally:
                os.environ["PATH"] = original_path

    def test_two_passing_runs_with_equal_artifacts_are_reproducible(self) -> None:
        result = self._run(mode="stable")

        self.assertEqual(result.classification, ExperimentStatus.REPRODUCIBLE)
        self.assertEqual(len(result.results), 2)
        self.assertEqual(
            [run.run_id for run in result.runs], ["python-uv-run-1", "python-uv-run-2"]
        )
        self.assertEqual(result.artifacts[0].digest, result.artifacts[1].digest)

    def test_two_passing_runs_with_different_artifacts_detect_drift(self) -> None:
        result = self._run(mode="drift")

        self.assertEqual(result.classification, ExperimentStatus.DRIFT_DETECTED)
        self.assertTrue(all(item.complete for item in result.artifacts))
        self.assertNotEqual(result.artifacts[0].digest, result.artifacts[1].digest)

    def test_without_approval_no_run_is_started(self) -> None:
        result = self._run(mode="stable", approved=False)

        self.assertEqual(result.classification, ExperimentStatus.INCOMPLETE)
        self.assertEqual(result.results, ())

    def test_docker_unavailability_is_infrastructure_failure_for_both_runs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_fixture(root)
            result = run_experiment(
                root,
                profile="python-uv",
                backend="docker",
                approved=True,
                artifact_paths=("artifact.txt",),
                executor_options={"docker_executable": "reprocheck-docker-missing"},
            )

        self.assertEqual(result.classification, ExperimentStatus.INFRASTRUCTURE_FAILURE)
        self.assertEqual(len(result.results), 2)

    def test_one_failed_test_run_is_a_pass_fail_disagreement(self) -> None:
        result = self._run(mode="first-pass-second-fail")

        self.assertEqual(result.classification, ExperimentStatus.PASS_FAIL_DISAGREEMENT)
        self.assertEqual([item.status.value for item in result.results], ["pass", "fail"])

    def test_two_failed_test_runs_are_reported_separately(self) -> None:
        result = self._run(mode="fail")

        self.assertEqual(result.classification, ExperimentStatus.BOTH_FAILED)
        self.assertEqual([item.status.value for item in result.results], ["fail", "fail"])

    def test_snapshot_mismatch_invalidates_the_comparison_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_fixture(root)
            original = fingerprint_repository(root)
            changed = ContentFingerprint(
                algorithm=original.algorithm,
                digest="0" * 64,
                file_count=original.file_count,
            )
            with patch(
                "reprocheck.verification.experiment.fingerprint_repository",
                side_effect=(original, changed),
            ):
                result = run_experiment(
                    root,
                    profile="python-uv",
                    backend="host",
                    approved=True,
                    allow_host_exec=True,
                    artifact_paths=("artifact.txt",),
                )

        self.assertEqual(result.classification, ExperimentStatus.COMPARISON_INVALID)
        self.assertEqual(result.results, ())


if __name__ == "__main__":
    unittest.main()
