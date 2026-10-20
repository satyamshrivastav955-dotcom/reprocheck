import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reprocheck.checks import CheckContext
from reprocheck.checks.base import BaseCheck, read_known_text
from reprocheck.checks.modules.dependencies import LockfilePresenceCheck
from reprocheck.checks.modules.environment import EnvironmentDocumentationCheck
from reprocheck.checks.modules.git import GitignoreHygieneCheck
from reprocheck.discovery import discover_repository
from reprocheck.models import Assessment, Finding, ResultState
from reprocheck.scoring import score_findings

_LIBRARY = (
    "[project]\nname = 'fixture-library'\nversion = '1.0'\n"
    "dependencies = ['example-dependency>=1']\n"
    "[build-system]\nrequires = ['setuptools>=70']\n"
    "build-backend = 'setuptools.build_meta'\n"
)
_COMPOSE = (
    "services:\n  app:\n    image: example:1\n    environment:\n"
    "      SERVICE_URL: ${SERVICE_URL:?required}\n"
    "      SERVICE_TOKEN: ${SERVICE_TOKEN?required}\n"
)
_PYTHON_IGNORE = "__pycache__/\n*.pyc\n.pytest_cache/\n.venv/\nbuild/\ndist/\ncoverage/\n"


class HygieneApplicabilityTests(unittest.TestCase):
    def evaluate(self, check: BaseCheck, files: dict[str, str]) -> Finding:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            for path, content in files.items():
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            context = CheckContext(root=root, discovery=discover_repository(root))
            findings = check.evaluate(context)
            self.assertEqual(len(findings), 1)
            return findings[0]

    def test_pinned_requirements_are_presence_evidence_without_requiring_uv(self) -> None:
        for library in (False, True):
            with self.subTest(library=library):
                files = {"requirements.txt": "example-dependency==1.2.3\nother==4.5\n"}
                if library:
                    files["pyproject.toml"] = _LIBRARY
                finding = self.evaluate(LockfilePresenceCheck(), files)

                self.assertEqual(finding.state, ResultState.PRESENT)
                self.assertEqual(finding.assessment, Assessment.INFO)
                self.assertIn("requirements.txt", [item.source for item in finding.evidence])
                self.assertEqual(score_findings((finding,)).score, 12.5)

    def test_unlocked_requirements_remain_failures(self) -> None:
        for requirement in ("example-dependency", "example-dependency>=1", "example==1.*"):
            with self.subTest(requirement=requirement):
                finding = self.evaluate(
                    LockfilePresenceCheck(),
                    {
                        "pyproject.toml": _LIBRARY,
                        "requirements.txt": requirement + "\n",
                        "requirements-dev.txt": "pytest==8.4.2\n",
                    },
                )
                self.assertEqual(finding.state, ResultState.STATIC_FAIL)
                self.assertEqual(finding.assessment, Assessment.FAIL)
                self.assertEqual(score_findings((finding,)).score, 0.0)

    def test_standard_python_lockfile_is_recognized_and_validated(self) -> None:
        for content, state, assessment in (
            ('lock-version = "1.0"\n', ResultState.PRESENT, Assessment.INFO),
            ("lock-version = [broken\n", ResultState.STATIC_FAIL, Assessment.ERROR),
        ):
            with self.subTest(content=content):
                finding = self.evaluate(
                    LockfilePresenceCheck(),
                    {"pyproject.toml": _LIBRARY, "pylock.toml": content},
                )
                self.assertEqual((finding.state, finding.assessment), (state, assessment))

    def test_existing_alternative_python_lockfile_is_still_presence_only(self) -> None:
        finding = self.evaluate(
            LockfilePresenceCheck(),
            {"pyproject.toml": _LIBRARY, "poetry.lock": "lock-version = '2.0'\n"},
        )
        self.assertEqual(finding.state, ResultState.PRESENT)
        self.assertEqual(finding.assessment, Assessment.INFO)

    def test_tool_configuration_alone_does_not_establish_lock_applicability(self) -> None:
        finding = self.evaluate(
            LockfilePresenceCheck(), {"pyproject.toml": "[tool.ruff]\nline-length = 100\n"}
        )
        self.assertEqual(finding.state, ResultState.NOT_VERIFIED)
        self.assertEqual(finding.assessment, Assessment.UNSUPPORTED)
        self.assertTrue(finding.applicability_reason)
        self.assertTrue(finding.evidence)

    def test_unresolved_requirements_include_is_not_a_pass(self) -> None:
        finding = self.evaluate(
            LockfilePresenceCheck(), {"requirements.txt": "-r constraints/production.txt\n"}
        )
        self.assertEqual(finding.state, ResultState.NOT_VERIFIED)
        self.assertEqual(finding.assessment, Assessment.UNSUPPORTED)

    def test_library_ranges_without_a_locked_environment_still_fail(self) -> None:
        finding = self.evaluate(LockfilePresenceCheck(), {"pyproject.toml": _LIBRARY})
        self.assertEqual(finding.state, ResultState.STATIC_FAIL)
        self.assertEqual(finding.assessment, Assessment.FAIL)

    def test_development_pins_do_not_hide_unlocked_library_dependencies(self) -> None:
        for requirements in (
            {"requirements-dev.txt": "pytest==8.4.2\n"},
            {"requirements.txt": "unrelated-package==1.0\n"},
        ):
            with self.subTest(requirements=requirements):
                finding = self.evaluate(
                    LockfilePresenceCheck(), {"pyproject.toml": _LIBRARY, **requirements}
                )
                self.assertEqual(finding.state, ResultState.STATIC_FAIL)
                self.assertEqual(finding.assessment, Assessment.FAIL)

    def test_python_pin_evidence_does_not_hide_unlocked_node_dependencies(self) -> None:
        finding = self.evaluate(
            LockfilePresenceCheck(),
            {
                "requirements.txt": "example==1.0\n",
                "package.json": '{"dependencies": {"example": "^1"}}\n',
            },
        )
        self.assertEqual(finding.state, ResultState.STATIC_FAIL)
        self.assertEqual([item.details.get("ecosystem") for item in finding.evidence], ["node"])

    def test_unsupported_requirements_stay_uncertain_beside_known_node_lock(self) -> None:
        finding = self.evaluate(
            LockfilePresenceCheck(),
            {
                "requirements.txt": "-r constraints/production.txt\n",
                "package.json": '{"dependencies": {"example": "^1"}}\n',
                "package-lock.json": '{"lockfileVersion": 3}\n',
            },
        )
        self.assertEqual(finding.state, ResultState.NOT_VERIFIED)
        self.assertEqual(finding.assessment, Assessment.UNKNOWN)
        self.assertIn("package-lock.json", [item.source for item in finding.evidence])
        self.assertTrue(finding.applicability_reason)
        self.assertEqual(score_findings((finding,)).denominator, 25.0)
        self.assertEqual(score_findings((finding,)).score, 0.0)

    def test_malformed_python_dependency_metadata_cannot_be_promoted_by_pins(self) -> None:
        finding = self.evaluate(
            LockfilePresenceCheck(),
            {
                "pyproject.toml": "[project]\nname = 'fixture'\ndynamic = 1\n",
                "requirements.txt": "example==1.0\n",
            },
        )
        self.assertEqual(finding.state, ResultState.STATIC_FAIL)
        self.assertEqual(finding.assessment, Assessment.FAIL)

    def test_missing_env_evidence_is_unknown_for_libraries_and_apps(self) -> None:
        for manifest in (_LIBRARY, _LIBRARY + "[project.scripts]\napp = 'app:main'\n"):
            with self.subTest(manifest=manifest):
                finding = self.evaluate(
                    EnvironmentDocumentationCheck(),
                    {"pyproject.toml": manifest, "README.md": "# A Python package\n"},
                )
                self.assertEqual(finding.state, ResultState.NOT_VERIFIED)
                self.assertEqual(finding.assessment, Assessment.UNSUPPORTED)
                self.assertTrue(finding.applicability_reason)
                self.assertEqual(score_findings((finding,)).denominator, 0.0)

    def test_explicit_no_required_environment_documentation_is_positive_evidence(self) -> None:
        for documentation in (
            "No environment variables are required.",
            "This library does not require any environment variables.",
            "No required environment variables.",
        ):
            with self.subTest(documentation=documentation):
                finding = self.evaluate(
                    EnvironmentDocumentationCheck(), {"README.md": documentation + "\n"}
                )
                self.assertEqual(finding.state, ResultState.LOOKS_CORRECT)
                self.assertEqual(finding.assessment, Assessment.PASS)
                self.assertEqual(finding.evidence[0].source, "README.md")
                self.assertEqual(score_findings((finding,)).score, 10.0)

    def test_declared_required_env_without_documentation_fails(self) -> None:
        finding = self.evaluate(EnvironmentDocumentationCheck(), {"compose.yaml": _COMPOSE})
        self.assertEqual(finding.state, ResultState.STATIC_FAIL)
        self.assertEqual(finding.assessment, Assessment.FAIL)
        self.assertEqual(
            finding.evidence[0].details.get("missing_variables"), ["SERVICE_TOKEN", "SERVICE_URL"]
        )

    def test_readme_requirement_declaration_alone_is_not_variable_documentation(self) -> None:
        finding = self.evaluate(
            EnvironmentDocumentationCheck(),
            {"README.md": "Required environment variables: `SERVICE_TOKEN`.\n"},
        )
        self.assertEqual(finding.state, ResultState.STATIC_FAIL)
        self.assertEqual(finding.assessment, Assessment.FAIL)
        self.assertEqual(finding.evidence[0].details.get("missing_variables"), ["SERVICE_TOKEN"])

    def test_scoped_no_environment_claim_does_not_establish_project_wide_evidence(self) -> None:
        finding = self.evaluate(
            EnvironmentDocumentationCheck(),
            {"README.md": "No environment variables are required for the unit tests.\n"},
        )
        self.assertEqual(finding.state, ResultState.NOT_VERIFIED)
        self.assertEqual(finding.assessment, Assessment.UNSUPPORTED)

    def test_template_presence_does_not_hide_undocumented_required_variables(self) -> None:
        finding = self.evaluate(
            EnvironmentDocumentationCheck(),
            {"compose.yaml": _COMPOSE, ".env.example": "SERVICE_URL=\n"},
        )
        self.assertEqual(finding.state, ResultState.STATIC_FAIL)
        self.assertEqual(finding.assessment, Assessment.FAIL)
        self.assertEqual(finding.evidence[0].details.get("missing_variables"), ["SERVICE_TOKEN"])

    def test_required_variables_can_be_documented_across_template_and_readme(self) -> None:
        finding = self.evaluate(
            EnvironmentDocumentationCheck(),
            {
                "compose.yaml": _COMPOSE,
                ".env.example": "SERVICE_URL=\n",
                "README.md": "Environment variables:\n- `SERVICE_TOKEN`: service credential.\n",
            },
        )
        self.assertEqual(finding.state, ResultState.PRESENT)
        self.assertEqual(finding.assessment, Assessment.INFO)

    def test_readme_can_document_required_env_without_a_template(self) -> None:
        finding = self.evaluate(
            EnvironmentDocumentationCheck(),
            {
                "compose.yaml": _COMPOSE,
                "README.md": (
                    "Environment variables:\nSERVICE_URL: endpoint\nSERVICE_TOKEN: token\n"
                ),
            },
        )
        self.assertEqual(finding.state, ResultState.LOOKS_CORRECT)
        self.assertEqual(finding.assessment, Assessment.PASS)

    def test_readme_documenting_only_some_required_env_does_not_pass(self) -> None:
        finding = self.evaluate(
            EnvironmentDocumentationCheck(),
            {
                "compose.yaml": _COMPOSE,
                "README.md": "Environment variables:\nSERVICE_URL: endpoint\n",
            },
        )
        self.assertEqual(finding.state, ResultState.STATIC_FAIL)
        self.assertEqual(finding.assessment, Assessment.FAIL)

    def test_no_required_env_claim_does_not_override_explicit_requirements(self) -> None:
        finding = self.evaluate(
            EnvironmentDocumentationCheck(),
            {
                "compose.yaml": _COMPOSE,
                "README.md": "No environment variables are required.\n",
            },
        )
        self.assertEqual(finding.state, ResultState.STATIC_FAIL)
        self.assertEqual(finding.assessment, Assessment.FAIL)

    def test_empty_template_and_incidental_env_mentions_are_not_passes(self) -> None:
        for files in (
            {".env.example": "# Optional settings\n"},
            {"README.md": "Do not commit your .env file.\n"},
            {"README.md": "Environment variables may be used by consumers.\n"},
        ):
            with self.subTest(files=files):
                finding = self.evaluate(EnvironmentDocumentationCheck(), files)
                self.assertEqual(finding.state, ResultState.NOT_VERIFIED)
                self.assertEqual(finding.assessment, Assessment.UNSUPPORTED)

    def test_optional_and_escaped_compose_variables_do_not_prove_requirements(self) -> None:
        finding = self.evaluate(
            EnvironmentDocumentationCheck(),
            {
                "compose.yaml": (
                    "services:\n  app:\n    image: example:1\n"
                    "    environment:\n      MODE: ${MODE:-development}\n"
                    "      LITERAL: $${LITERAL:?escaped}\n"
                    "# ${COMMENTED:?not a requirement}\n"
                )
            },
        )
        self.assertEqual(finding.state, ResultState.NOT_VERIFIED)
        self.assertEqual(finding.assessment, Assessment.UNSUPPORTED)

    def test_environment_check_reads_only_allowlisted_configuration_and_docs(self) -> None:
        with patch(
            "reprocheck.checks.modules.environment.read_known_text", wraps=read_known_text
        ) as reader:
            finding = self.evaluate(
                EnvironmentDocumentationCheck(),
                {
                    ".env": "SYNTHETIC_PRIVATE_VALUE=placeholder\n",
                    "app.py": "# not an environment declaration\n",
                    "README.md": "No environment variables are required.\n",
                },
            )
        self.assertEqual([call.args[1] for call in reader.call_args_list], ["README.md"])
        self.assertNotIn("placeholder", finding.to_json())

    def test_conventional_python_gitignore_still_passes(self) -> None:
        finding = self.evaluate(
            GitignoreHygieneCheck(),
            {"pyproject.toml": _LIBRARY, ".gitignore": _PYTHON_IGNORE},
        )
        self.assertEqual(finding.state, ResultState.LOOKS_CORRECT)

    def test_bounded_gitignore_equivalences_cover_python_artifacts(self) -> None:
        for bytecode in ("*.py[cod]", "**/*.py[co]"):
            for coverage in (".coverage", ".coverage*", "htmlcov/"):
                with self.subTest(bytecode=bytecode, coverage=coverage):
                    finding = self.evaluate(
                        GitignoreHygieneCheck(),
                        {
                            "pyproject.toml": _LIBRARY,
                            ".gitignore": (
                                f"**/__pycache__/\n{bytecode}\n/.pytest_cache/\n/venv/\n"
                                f"/build/\n/dist/**\n{coverage}\n"
                            ),
                        },
                    )
                    self.assertEqual(finding.state, ResultState.LOOKS_CORRECT)
                    self.assertEqual(finding.assessment, Assessment.PASS)

    def test_non_python_repository_does_not_require_python_bytecode_patterns(self) -> None:
        finding = self.evaluate(
            GitignoreHygieneCheck(),
            {
                "package.json": '{"name": "fixture"}\n',
                ".gitignore": "node_modules/\nbuild/\ndist/\ncoverage/\n",
            },
        )
        self.assertEqual(finding.state, ResultState.LOOKS_CORRECT)

    def test_genuine_gitignore_gaps_are_preserved(self) -> None:
        for missing in ("*.pyc", ".pytest_cache", ".venv", "build", "dist", "coverage"):
            with self.subTest(missing=missing):
                text = "\n".join(
                    line for line in _PYTHON_IGNORE.splitlines() if line.rstrip("/") != missing
                )
                finding = self.evaluate(
                    GitignoreHygieneCheck(), {"pyproject.toml": _LIBRARY, ".gitignore": text}
                )
                self.assertEqual(finding.state, ResultState.STATIC_FAIL)
                self.assertIn(missing, finding.evidence[0].details["missing_patterns"])

    def test_arbitrary_similar_gitignore_patterns_do_not_satisfy_requirements(self) -> None:
        finding = self.evaluate(
            GitignoreHygieneCheck(),
            {
                "pyproject.toml": _LIBRARY,
                ".gitignore": "*py*\n.tox/\n.env\nmybuild/\ndistribution/\ncoverage-notes/\n",
            },
        )
        self.assertEqual(finding.state, ResultState.STATIC_FAIL)
        self.assertEqual(
            finding.evidence[0].details["missing_patterns"],
            ["*.pyc", ".pytest_cache", ".venv", "__pycache__", "build", "coverage", "dist"],
        )

    def test_gitignore_negations_cannot_hide_missing_coverage(self) -> None:
        for negation, missing in (("!.venv/", ".venv"), ("!keep.pyc", "*.pyc")):
            with self.subTest(negation=negation):
                finding = self.evaluate(
                    GitignoreHygieneCheck(),
                    {"pyproject.toml": _LIBRARY, ".gitignore": _PYTHON_IGNORE + negation + "\n"},
                )
                self.assertEqual(finding.state, ResultState.STATIC_FAIL)
                self.assertIn(missing, finding.evidence[0].details["missing_patterns"])

    def test_unrelated_negations_and_later_exclusions_preserve_gitignore_coverage(self) -> None:
        for suffix in ("!README.md\n", "!.venv/\n.venv/\n"):
            with self.subTest(suffix=suffix):
                finding = self.evaluate(
                    GitignoreHygieneCheck(),
                    {"pyproject.toml": _LIBRARY, ".gitignore": _PYTHON_IGNORE + suffix},
                )
                self.assertEqual(finding.state, ResultState.LOOKS_CORRECT)

    def test_nested_gitignore_does_not_substitute_for_root_coverage(self) -> None:
        finding = self.evaluate(
            GitignoreHygieneCheck(),
            {"pyproject.toml": _LIBRARY, "tests/.gitignore": _PYTHON_IGNORE},
        )
        self.assertEqual(finding.state, ResultState.STATIC_FAIL)
        self.assertEqual(finding.assessment, Assessment.FAIL)


if __name__ == "__main__":
    unittest.main()
