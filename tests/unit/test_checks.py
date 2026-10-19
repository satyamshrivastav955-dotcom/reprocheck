import json
import tempfile
import unittest
from pathlib import Path

from reprocheck.checks import CheckContext, CheckRegistry, default_registry
from reprocheck.checks.modules.security import LikelySecretCheck, SuspiciousPathCheck
from reprocheck.discovery import discover_repository
from reprocheck.models import (
    Assessment,
    CheckCategory,
    ResultState,
    Severity,
    VerificationClass,
)


def _context(root: Path) -> CheckContext:
    return CheckContext(repository_root=root, discovery=discover_repository(root))


def _write_reproducible_python_repository(root: Path) -> None:
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("def main() -> None:\n    return None\n")
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "test.yml").write_text(
        "name: test\n"
        "on: [push]\n"
        "jobs:\n"
        "  test:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - uses: actions/setup-python@v5\n"
        "      - run: uv run pytest\n"
    )
    (root / "pyproject.toml").write_text(
        "[project]\n"
        "name = 'fixture'\n"
        "version = '0.1.0'\n"
        "requires-python = '>=3.12,<3.13'\n"
        "[project.scripts]\n"
        "test = 'app:main'\n"
        "[build-system]\n"
        "requires = ['hatchling']\n"
        "build-backend = 'hatchling.build'\n"
    )
    (root / ".python-version").write_text("3.12.4\n")
    (root / "uv.lock").write_text("version = 1\n")
    (root / ".env.example").write_text("APP_MODE=\n")
    (root / "Dockerfile").write_text(
        "FROM python:3.12-slim\n"
        "WORKDIR /app\n"
        "COPY . .\n"
        "RUN python -m compileall src\n"
        "CMD ['python', '-m', 'app']\n"
    )
    (root / ".gitignore").write_text(
        "__pycache__/\n.venv/\n.pytest_cache/\n*.pyc\ndist/\nbuild/\ncoverage/\n"
    )
    (root / "README.md").write_text("Run `uv run pytest` to test the project.\n")


class CheckRegistryTests(unittest.TestCase):
    def test_default_registry_has_stable_sorted_static_specs(self) -> None:
        registry = default_registry()
        ids = [check.spec.check_id for check in registry.checks]

        self.assertEqual(
            ids,
            [
                "RC-CI-001",
                "RC-CMD-001",
                "RC-CMD-002",
                "RC-CON-001",
                "RC-DEP-001",
                "RC-DEP-002",
                "RC-DISC-001",
                "RC-ENV-001",
                "RC-GIT-001",
                "RC-PATH-001",
                "RC-PKG-001",
                "RC-RUN-001",
                "RC-SEC-001",
            ],
        )
        for spec in registry.specs:
            with self.subTest(spec=spec.check_id):
                self.assertEqual(spec.verification_class, VerificationClass.STATIC)
                self.assertTrue(spec.description)
                self.assertTrue(spec.limitations)
                self.assertTrue(spec.false_positive_policy)
                self.assertTrue(spec.scoring_group)
                self.assertIsInstance(spec.category, CheckCategory)
                self.assertIsInstance(spec.severity, Severity)

    def test_duplicate_registration_is_rejected_without_mutating_registry(self) -> None:
        check = default_registry().checks[0]
        registry = CheckRegistry((check,))

        with self.assertRaises(ValueError):
            registry.register(check)

        self.assertEqual(len(registry.checks), 1)
        self.assertIs(registry.get(check.spec.check_id), check)


class StaticCheckBehaviorTests(unittest.TestCase):
    def test_reproducible_python_fixture_produces_bounded_static_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            _write_reproducible_python_repository(root)

            findings = default_registry().evaluate(_context(root))
            by_id = {finding.check_id: finding for finding in findings}

            for check_id in (
                "RC-CI-001",
                "RC-CMD-001",
                "RC-CMD-002",
                "RC-CON-001",
                "RC-GIT-001",
                "RC-PKG-001",
                "RC-RUN-001",
            ):
                with self.subTest(check_id=check_id):
                    self.assertEqual(by_id[check_id].state, ResultState.LOOKS_CORRECT)
            self.assertEqual(by_id["RC-DEP-001"].state, ResultState.PRESENT)
            self.assertEqual(by_id["RC-DEP-002"].state, ResultState.PRESENT)
            self.assertEqual(by_id["RC-ENV-001"].state, ResultState.PRESENT)
            self.assertEqual(by_id["RC-DISC-001"].state, ResultState.LOOKS_CORRECT)
            self.assertEqual(by_id["RC-PATH-001"].state, ResultState.LOOKS_CORRECT)
            self.assertEqual(by_id["RC-SEC-001"].state, ResultState.LOOKS_CORRECT)

            for finding in findings:
                for evidence in finding.evidence:
                    if evidence.source is not None:
                        self.assertFalse(Path(evidence.source).is_absolute())

    def test_missing_lockfile_is_a_static_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "pyproject.toml").write_text(
                "[project]\nname = 'fixture'\nrequires-python = '>=3.12'\n"
            )

            finding = default_registry().get("RC-DEP-002").evaluate(_context(root))[0]  # type: ignore[union-attr]

            self.assertEqual(finding.state, ResultState.STATIC_FAIL)
            self.assertEqual(finding.assessment, Assessment.FAIL)

    def test_malformed_structured_lockfile_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "pyproject.toml").write_text(
                "[project]\nname = 'fixture'\nrequires-python = '>=3.12'\n"
            )
            (root / "uv.lock").write_text("not = [valid\n")

            finding = default_registry().get("RC-DEP-002").evaluate(_context(root))[0]  # type: ignore[union-attr]

            self.assertEqual(finding.state, ResultState.STATIC_FAIL)
            self.assertEqual(finding.assessment, Assessment.ERROR)

    def test_secret_and_machine_path_evidence_is_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            raw_secret = "super-secret-value-1234567890"
            machine_path = "/" + "home/alice/private/project"
            (root / "notes.txt").write_text(f"API_KEY={raw_secret}\npath={machine_path}\n")
            context = _context(root)

            secret = LikelySecretCheck().evaluate(context)[0]
            path = SuspiciousPathCheck().evaluate(context)[0]

            self.assertEqual(secret.state, ResultState.STATIC_FAIL)
            self.assertEqual(secret.assessment, Assessment.FAIL)
            self.assertNotIn(raw_secret, json.dumps(secret.to_dict()))
            self.assertEqual(path.state, ResultState.STATIC_FAIL)
            self.assertNotIn(machine_path, json.dumps(path.to_dict()))

    def test_malformed_manifest_does_not_pass_as_static_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "package.json").write_text('{"name": "broken"\n')

            finding = default_registry().get("RC-DEP-001").evaluate(_context(root))[0]  # type: ignore[union-attr]

            self.assertEqual(finding.state, ResultState.STATIC_FAIL)
            self.assertEqual(finding.assessment, Assessment.ERROR)


if __name__ == "__main__":
    unittest.main()
