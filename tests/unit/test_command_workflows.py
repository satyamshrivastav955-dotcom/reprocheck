"""Regressions for bounded CI coverage and command-source consistency (B/C)."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reprocheck.checks import CheckContext
from reprocheck.checks.modules.ci import CiWorkflowCheck
from reprocheck.checks.modules.commands import (
    CommandConsistencyCheck,
    CommandDiscoverabilityCheck,
    command_observations,
)
from reprocheck.command_parser import CommandStatus, plan_repository_commands
from reprocheck.discovery import discover_repository
from reprocheck.models import Assessment, ResultState


def _workflow(command: str, *, setup: str = "actions/setup-python@v5") -> str:
    return (
        "name: checks\non: [push]\njobs:\n  checks:\n"
        "    runs-on: ubuntu-latest\n    steps:\n"
        f"      - uses: {setup}\n      - run: {command}\n"
    )


class CommandWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def write(self, path: str, text: str) -> None:
        destination = self.root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")

    def context(self) -> CheckContext:
        return CheckContext(self.root, discover_repository(self.root))

    def ci(self):
        return CiWorkflowCheck().evaluate(self.context())[0]

    def consistency(self):
        return CommandConsistencyCheck().evaluate(self.context())[0]

    def test_ci_resolves_literal_make_prerequisites_to_real_test_leaves(self) -> None:
        self.write(".github/workflows/ci.yml", _workflow("make ci"))
        self.write(
            "Makefile",
            ".PHONY: ci lint unit\nci: lint unit\nlint:\n\truff check src\n"
            "unit:\n\tuv run --locked --offline python -m pytest tests/unit -q\n",
        )
        finding = self.ci()
        self.assertEqual(finding.assessment, Assessment.PASS)
        evidence = json.dumps([item.to_dict() for item in finding.evidence])
        self.assertIn("make ci", evidence)
        self.assertIn("pytest tests/unit", evidence)
        self.assertIn("Makefile", evidence)
        self.assertIn("unit", evidence)

    def test_ci_resolves_literal_make_recipe_calls_without_execution(self) -> None:
        self.write(".github/workflows/ci.yml", _workflow("make ci"))
        self.write("Makefile", "ci:\n\tmake unit\nunit:\n\tpython -m unittest discover -s tests\n")
        with patch("subprocess.run", side_effect=AssertionError("scan executed a command")):
            self.assertEqual(self.ci().assessment, Assessment.PASS)

    def test_declared_uv_default_establishes_canonical_make_test_not_fallback(self) -> None:
        self.write(
            "Makefile",
            ".PHONY: test lint typecheck format-check\nUV ?= uv\n"
            "test: lint typecheck format-check\n"
            "\t$(UV) run --locked --offline python -m pytest\n"
            "lint:\n\t$(UV) run --locked --offline ruff check src tests\n"
            "typecheck:\n\t$(UV) run --locked --offline mypy src\n"
            "format-check:\n\t$(UV) run --locked --offline ruff format --check src tests\n",
        )
        self.write(
            "README.md",
            "Run the canonical quality gate:\n```sh\nmake test\n```\n\n"
            "On a machine without GNU Make, use this fallback:\n"
            "```sh\npython3 -m unittest discover -s tests\n```\n",
        )
        self.write(".github/workflows/ci.yml", _workflow("make test"))
        with patch("subprocess.run", side_effect=AssertionError("scan executed a command")):
            self.assertEqual(self.ci().assessment, Assessment.PASS)
            finding = self.consistency()
            self.assertEqual(finding.assessment, Assessment.PASS)
            self.assertEqual(
                CommandDiscoverabilityCheck().evaluate(self.context())[0].assessment,
                Assessment.PASS,
            )
        observations, errors = command_observations(self.context())
        self.assertFalse(errors)
        self.assertEqual(
            {(item.source, item.excerpt, item.commands) for item in observations},
            {
                ("README.md", "make test", (("pytest",),)),
                (".github/workflows/ci.yml", "make test", (("pytest",),)),
            },
        )
        resolved = [
            entry for item in finding.evidence for entry in item.details.get("resolved", [])
        ]
        self.assertTrue(all(entry["make_defaults"] == {"UV": "uv"} for entry in resolved))
        self.assertTrue(
            all("override" in item.details["make_default_policy"] for item in finding.evidence)
        )

    def test_simple_make_assignment_operators_expand_literal_tokens(self) -> None:
        for operator in ("=", ":=", "?="):
            for reference in ("$(RUNNER)", "${RUNNER}"):
                with self.subTest(operator=operator, reference=reference):
                    self.write(
                        "Makefile",
                        f"RUNNER {operator} python -m pytest\ntest:\n\t{reference} tests -q\n",
                    )
                    self.write("README.md", "Run `make test`.\n")
                    self.write(".github/workflows/ci.yml", _workflow("pytest tests"))
                    self.assertEqual(self.consistency().assessment, Assessment.PASS)
                    observations, _ = command_observations(self.context())
                    self.assertEqual(
                        {identity for item in observations for identity in item.commands},
                        {("pytest", "tests")},
                    )

    def test_make_assignment_source_order_and_reference_chains(self) -> None:
        for assignments, expected in (
            ("BASE = pytest\nRUNNER = $(BASE)\nBASE = python -m unittest\n", "unittest"),
            ("BASE = pytest\nRUNNER := $(BASE)\nBASE = python -m unittest\n", "pytest"),
            ("RUNNER = pytest\nRUNNER ?= python -m unittest\n", "pytest"),
            ("RUNNER ?= python -m unittest\nRUNNER := pytest\n", "pytest"),
            ("RUNNER = $(BASE)\nBASE = pytest\n", "pytest"),
        ):
            with self.subTest(assignments=assignments):
                self.write("Makefile", assignments + "test:\n\t$(RUNNER)\n")
                self.write(".github/workflows/ci.yml", _workflow("make test"))
                self.assertEqual(self.ci().assessment, Assessment.PASS)
                observations, _ = command_observations(self.context())
                self.assertEqual([item.commands for item in observations], [((expected,),)])

    def test_make_defaults_expand_prerequisites_at_declaration_time(self) -> None:
        self.write(
            "Makefile",
            "SUITE = unit\nci: $(SUITE)\nSUITE = integration\n"
            "unit:\n\tpytest tests/unit\nintegration:\n\tpytest tests/integration\n",
        )
        self.write(".github/workflows/ci.yml", _workflow("make ci"))
        self.write("README.md", "Run `pytest tests/unit`.\n")
        self.assertEqual(self.ci().assessment, Assessment.PASS)
        self.assertEqual(self.consistency().assessment, Assessment.PASS)

    def test_make_defaults_do_not_consult_host_environment(self) -> None:
        self.write("Makefile", "RUNNER ?= pytest\ntest:\n\t$(RUNNER)\n")
        self.write(".github/workflows/ci.yml", _workflow("make test"))
        with patch.dict(os.environ, {"RUNNER": "npm test"}):
            self.assertEqual(self.ci().assessment, Assessment.PASS)
            observations, _ = command_observations(self.context())
        self.assertEqual([item.commands for item in observations], [(("pytest",),)])

    def test_dynamic_or_unknown_make_variables_remain_uncertain(self) -> None:
        assignments = (
            "",
            "RUNNER = $(UNKNOWN)\n",
            "RUNNER := $(UNKNOWN)\n",
            "RUNNER = $(RUNNER)\n",
            "RUNNER = $(OTHER)\nOTHER = $(RUNNER)\n",
            "RUNNER = $(shell echo pytest)\n",
            "RUNNER := $(wildcard test*)\n",
            "RUNNER = ${HOME}/pytest\n",
            "RUNNER = $RUNNER\n",
            "RUNNER = $$RUNNER\n",
            "RUNNER = pytest; echo done\n",
            "RUNNER += pytest\n",
            "include defaults.mk\nRUNNER = pytest\n",
            "override RUNNER = pytest\n",
            "export RUNNER = pytest\n",
            "RUNNER = pytest\ntest: RUNNER = npm test\n",
        )
        for assignment in assignments:
            with self.subTest(assignment=assignment):
                self.write("Makefile", assignment + "test:\n\t$(RUNNER)\n")
                self.write("README.md", "Run `make test`.\n")
                self.write(".github/workflows/ci.yml", _workflow("make test"))
                self.assertEqual(self.ci().state, ResultState.NOT_VERIFIED)
                self.assertEqual(self.consistency().state, ResultState.NOT_VERIFIED)

    def test_make_variable_expansion_is_bounded(self) -> None:
        chain = "".join(f"V{index} = $(V{index + 1})\n" for index in range(24))
        repeated = "V0 = pytest\n" + "".join(
            f"V{index} = $(V{index - 1})$(V{index - 1})\n" for index in range(1, 14)
        )
        for variables, reference in ((chain + "V24 = pytest\n", "$(V0)"), (repeated, "$(V13)")):
            with self.subTest(reference=reference):
                self.write("Makefile", variables + f"test:\n\t{reference}\n")
                self.write(".github/workflows/ci.yml", _workflow("make test"))
                self.assertEqual(self.ci().state, ResultState.NOT_VERIFIED)

    def test_literal_make_defaults_do_not_hide_real_runner_conflicts(self) -> None:
        self.write("Makefile", "RUNNER := pytest\ntest:\n\t$(RUNNER) tests/unit\n")
        self.write("README.md", "Run `make test`.\n")
        self.write(".github/workflows/ci.yml", _workflow("python -m unittest discover"))
        self.assertEqual(self.consistency().assessment, Assessment.WARN)

    def test_make_command_line_variable_overrides_remain_unsupported(self) -> None:
        self.write("Makefile", "RUNNER ?= pytest\ntest:\n\t$(RUNNER)\n")
        for command in ("make RUNNER=pytest test", "make test RUNNER=pytest"):
            with self.subTest(command=command):
                self.write(".github/workflows/ci.yml", _workflow(command))
                self.assertEqual(self.ci().state, ResultState.NOT_VERIFIED)

    def test_ci_accepts_direct_unittest_and_npm_test(self) -> None:
        for command in ("python3 -m unittest discover -s tests", "npm test"):
            with self.subTest(command=command):
                self.write(".github/workflows/ci.yml", _workflow(command))
                self.assertEqual(self.ci().assessment, Assessment.PASS)

    def test_make_name_or_test_words_do_not_establish_ci_test_coverage(self) -> None:
        for command, makefile in (
            ("make test", "test:\n\techo test\n"),
            ("make ci", "ci:\n\techo pytest\n"),
            ("make ci", "ci: build\nbuild:\n\tnpm run build\n"),
            ("make ci", "ci: lint\nlint:\n\tnpm run lint\n"),
            ("npm run lint", ""),
            ("echo 'npm test'", ""),
        ):
            with self.subTest(command=command, makefile=makefile):
                self.write(".github/workflows/ci.yml", _workflow(command))
                self.write("Makefile", makefile)
                self.assertNotEqual(self.ci().assessment, Assessment.PASS)

    def test_ci_requires_setup_beyond_checkout(self) -> None:
        self.write(".github/workflows/ci.yml", _workflow("pytest", setup="actions/checkout@v4"))
        self.assertEqual(self.ci().assessment, Assessment.FAIL)

    def test_ci_does_not_borrow_setup_from_other_jobs_or_workflows(self) -> None:
        self.write(
            ".github/workflows/ci.yml",
            "jobs:\n  install:\n    runs-on: ubuntu-latest\n    steps:\n"
            "      - run: npm ci\n  tests:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - run: npm test\n",
        )
        self.assertNotEqual(self.ci().assessment, Assessment.PASS)
        self.write(".github/workflows/ci.yml", _workflow("echo release"))
        self.write(".github/workflows/tests.yml", _workflow("pytest", setup="actions/checkout@v4"))
        self.assertNotEqual(self.ci().assessment, Assessment.PASS)

    def test_ci_names_comments_env_and_action_inputs_are_not_run_commands(self) -> None:
        self.write(
            ".github/workflows/release.yml",
            _workflow("echo release") + "      # pytest\n      - name: npm test\n"
            "        uses: example/release@v1\n        with:\n"
            "          run: pytest\n        env:\n          COMMAND: npm test\n",
        )
        self.assertEqual(self.ci().assessment, Assessment.FAIL)
        observations, errors = command_observations(self.context())
        self.assertFalse(errors)
        self.assertFalse(observations)

    def test_ci_handles_run_blocks_and_dependency_install_setup(self) -> None:
        self.write(
            ".github/workflows/ci.yml",
            "jobs:\n  tests:\n    runs-on: ubuntu-latest\n    steps:\n"
            "      - run: python -m pip install -e .\n"
            "      - run: |\n          # npm test is not the runner\n"
            "          python -m pytest\n",
        )
        finding = self.ci()
        self.assertEqual(finding.assessment, Assessment.PASS)
        self.assertNotIn("npm", json.dumps([item.to_dict() for item in finding.evidence]))

    def test_install_and_tests_in_the_same_literal_run_block(self) -> None:
        self.write(
            ".github/workflows/ci.yml",
            "jobs:\n  tests:\n    runs-on: ubuntu-latest\n    steps:\n"
            "      - run: |\n          npm ci\n          npm test\n",
        )
        self.assertEqual(self.ci().assessment, Assessment.PASS)

    def test_ci_dynamic_make_features_are_uncertain_even_with_a_test_leaf(self) -> None:
        makefiles = (
            "test:\n\t$(UV) run pytest\n",
            "include other.mk\ntest:\n\tpytest\n",
            "test:\n\tfor suite in unit; do pytest; done\n",
            "test: loop\n\tpytest\nloop: test\n",
            "test:\n\tpytest && echo done\n",
            "test: $(SUITES)\n\tpytest\n",
        )
        for makefile in makefiles:
            with self.subTest(makefile=makefile):
                self.write(".github/workflows/ci.yml", _workflow("make test"))
                self.write("Makefile", makefile)
                finding = self.ci()
                self.assertEqual(finding.state, ResultState.NOT_VERIFIED)
                self.assertNotEqual(finding.assessment, Assessment.PASS)

    def test_ci_does_not_follow_missing_or_external_makefiles(self) -> None:
        for command in ("make test", "make -f ../outside.mk test", "make -C elsewhere test"):
            with self.subTest(command=command):
                self.write(".github/workflows/ci.yml", _workflow(command))
                self.assertEqual(self.ci().state, ResultState.NOT_VERIFIED)

    def test_command_consistency_keeps_test_and_build_families_separate(self) -> None:
        self.write("README.md", "Run `pytest` for tests.\nBuild with `npm run build`.\n")
        self.write(".github/workflows/ci.yml", _workflow("pytest") + "      - run: npm run build\n")
        self.assertEqual(self.consistency().assessment, Assessment.PASS)

    def test_profile_tables_and_competitor_prose_are_not_project_commands(self) -> None:
        self.write(
            "README.md",
            "Run `pytest` to test this checkout.\n\n"
            "| Supported profile | Commands planned |\n|---|---|\n"
            "| node-npm | `npm test` / `npm run build` |\n\n"
            "Other tools support npm test and cargo test.\n"
            "The `pytest` runner is complemented by `npm test` in other projects.\n"
            "```python\nexample = 'npm test'\n```\n",
        )
        self.write(".github/workflows/ci.yml", _workflow("pytest"))
        finding = self.consistency()
        self.assertEqual(finding.assessment, Assessment.PASS)
        self.assertNotIn("npm", json.dumps([item.to_dict() for item in finding.evidence]))

    def test_repository_proposals_share_the_bounded_source_extraction(self) -> None:
        self.write(
            "README.md",
            "Run `pytest`.\n\n| Profile | Command |\n|---|---|\n| Node | `npm test` |\n"
            "The `pytest` runner is supported.\n\nAlternative runner:\n```sh\nnpm test\n```\n",
        )
        self.write(
            ".github/workflows/ci.yml",
            _workflow("pytest") + "      - uses: example/upload@v1\n"
            "        with:\n          run: cargo test\n",
        )
        record = plan_repository_commands(self.root).records[0]
        self.assertEqual(record.status, CommandStatus.RECOGNIZED)
        self.assertEqual(record.executable, "pytest")

    def test_explicit_fallback_and_alternative_snippets_are_not_contradictions(self) -> None:
        for label in ("Fallback when pytest is unavailable:", "Alternative runner (optional):"):
            with self.subTest(label=label):
                self.write(
                    "README.md",
                    "Run `pytest`.\n\n" + label + "\n\n```sh\nnpm test\n```\n",
                )
                self.write(".github/workflows/ci.yml", _workflow("pytest"))
                self.assertEqual(self.consistency().assessment, Assessment.PASS)

    def test_literal_make_wrapper_matches_its_concrete_runner(self) -> None:
        self.write("README.md", "Run `make ci`.\n")
        self.write("Makefile", "ci: unit\nunit:\n\tuv run python -m pytest tests\n")
        self.write(".github/workflows/ci.yml", _workflow("python -m pytest tests"))
        self.assertEqual(self.consistency().assessment, Assessment.PASS)
        finding = CommandDiscoverabilityCheck().evaluate(self.context())[0]
        self.assertEqual(finding.assessment, Assessment.PASS)
        self.assertIn("make ci", json.dumps([item.to_dict() for item in finding.evidence]))

    def test_package_wrapper_is_compatible_only_with_its_established_runner(self) -> None:
        self.write("package.json", json.dumps({"scripts": {"test": "pytest tests"}}))
        self.write("README.md", "Run `npm test`.\n")
        self.write(".github/workflows/ci.yml", _workflow("pytest tests"))
        self.assertEqual(self.consistency().assessment, Assessment.PASS)
        self.write(".github/workflows/ci.yml", _workflow("cargo test"))
        self.assertEqual(self.consistency().assessment, Assessment.WARN)

    def test_package_wrapper_in_multiline_run_keeps_its_established_runner(self) -> None:
        self.write("package.json", json.dumps({"scripts": {"test": "pytest tests"}}))
        self.write("README.md", "Run `pytest tests`.\n")
        self.write(".github/workflows/ci.yml", _workflow("|\n          npm ci\n          npm test"))
        self.assertEqual(self.consistency().assessment, Assessment.PASS)

    def test_genuine_runner_conflicts_keep_command_line_evidence(self) -> None:
        self.write("README.md", "# Development\n\nRun `pytest tests`.\n")
        self.write(".github/workflows/ci.yml", _workflow("npm test"))
        finding = self.consistency()
        self.assertEqual(finding.assessment, Assessment.WARN)
        excerpts = [item.details.get("excerpt", "") for item in finding.evidence]
        self.assertTrue(any("pytest tests" in value for value in excerpts))
        self.assertTrue(any("npm test" in value for value in excerpts))
        self.assertTrue(any("line" in item.details for item in finding.evidence))

    def test_different_python_runners_and_make_test_targets_remain_conflicts(self) -> None:
        for readme_command, ci_command, makefile in (
            ("pytest", "python -m unittest discover", ""),
            (
                "make unit",
                "make integration",
                "unit:\n\tpytest tests/unit\nintegration:\n\tpytest tests/integration\n",
            ),
        ):
            with self.subTest(readme_command=readme_command):
                self.write("README.md", f"Run `{readme_command}`.\n")
                self.write(".github/workflows/ci.yml", _workflow(ci_command))
                self.write("Makefile", makefile)
                self.assertEqual(self.consistency().assessment, Assessment.WARN)

    def test_unresolved_make_wrapper_does_not_pass_consistency(self) -> None:
        self.write("README.md", "Run `make test`.\n")
        self.write(".github/workflows/ci.yml", _workflow("make test"))
        self.write("Makefile", "test:\n\t$(RUNNER) tests\n")
        self.assertEqual(self.consistency().state, ResultState.NOT_VERIFIED)

    def test_single_source_does_not_become_an_independent_conflict(self) -> None:
        self.write("README.md", "Run `pytest` and `npm test`.\n")
        self.assertEqual(self.consistency().state, ResultState.NOT_VERIFIED)

    def test_setup_after_test_and_setup_action_names_in_prose_do_not_pass(self) -> None:
        self.write(
            ".github/workflows/ci.yml",
            "jobs:\n  tests:\n    runs-on: ubuntu-latest\n    steps:\n"
            "      - run: pytest\n      - uses: actions/setup-python@v5\n",
        )
        self.assertNotEqual(self.ci().assessment, Assessment.PASS)

    def test_custom_working_directory_does_not_resolve_the_root_makefile(self) -> None:
        self.write("Makefile", "test:\n\tpytest\n")
        self.write(
            ".github/workflows/ci.yml",
            "defaults:\n  run:\n    working-directory: other\n" + _workflow("make test"),
        )
        self.assertEqual(self.ci().state, ResultState.NOT_VERIFIED)
        self.write(
            ".github/workflows/ci.yml",
            _workflow("make test") + "        working-directory: other\n",
        )
        self.assertEqual(self.ci().state, ResultState.NOT_VERIFIED)

    def test_recursive_make_and_globs_are_bounded_and_uncertain(self) -> None:
        for makefile in (
            "test:\n\tmake test\n",
            "test:\n\tpytest tests/*.py\n",
            "SHELL := custom-shell\ntest:\n\tpytest\n",
        ):
            with self.subTest(makefile=makefile):
                self.write("Makefile", makefile)
                self.write(".github/workflows/ci.yml", _workflow("make test"))
                self.assertEqual(self.ci().state, ResultState.NOT_VERIFIED)

    def test_unrelated_uv_run_commands_do_not_create_test_observations(self) -> None:
        self.write("README.md", "```sh\nuv run app --help\n```\n")
        self.write(".github/workflows/release.yml", _workflow("uv run app release"))
        observations, errors = command_observations(self.context())
        self.assertFalse(errors)
        self.assertFalse(observations)

    def test_presentation_flags_and_python_uv_wrappers_are_compatible(self) -> None:
        self.write("README.md", "Run `uv run --locked --offline python -m pytest tests -q`.\n")
        self.write(".github/workflows/ci.yml", _workflow("python3 -m pytest tests -v"))
        self.assertEqual(self.consistency().assessment, Assessment.PASS)

    def test_different_noncomparable_intent_does_not_create_a_conflict(self) -> None:
        self.write("README.md", "Run `pytest`.\nBuild with `npm run build` and `cargo build`.\n")
        self.write(".github/workflows/ci.yml", _workflow("pytest"))
        self.assertEqual(self.consistency().assessment, Assessment.PASS)


if __name__ == "__main__":
    unittest.main()
