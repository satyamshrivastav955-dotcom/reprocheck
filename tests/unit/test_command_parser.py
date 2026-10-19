import json
import tempfile
import unittest
from pathlib import Path

from reprocheck.command_parser import (
    CommandStatus,
    parse_command_text,
    plan_repository_commands,
)


class CommandParserTests(unittest.TestCase):
    def test_extracts_allow_listed_command_without_executing_shell_text(self) -> None:
        record = parse_command_text("uv run pytest -q", source="README.md")[0]

        self.assertEqual(record.status, CommandStatus.RECOGNIZED)
        self.assertEqual(record.intent, "test")
        self.assertIsNotNone(record.spec)
        self.assertEqual(record.spec.executable, "uv")  # type: ignore[union-attr]
        self.assertEqual(record.spec.args, ("run", "pytest", "-q"))  # type: ignore[union-attr]

        unsupported = parse_command_text(
            "python -c \"open('command-parser-must-not-run', 'w').write('bad')\"",
            source="README.md",
        )
        self.assertEqual(unsupported[0].status, CommandStatus.UNSUPPORTED)
        self.assertFalse(Path("command-parser-must-not-run").exists())

    def test_repository_plan_classifies_missing_and_unsupported_intents(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "README.md").write_text("Run `./run-tests.sh` before release.\n")

            plan = plan_repository_commands(root)

            self.assertEqual([record.intent for record in plan.records], ["test", "build"])
            self.assertEqual(plan.records[0].status, CommandStatus.UNSUPPORTED)
            self.assertEqual(plan.records[1].status, CommandStatus.MISSING)
            self.assertEqual(plan.records[1].spec, None)

    def test_same_command_across_package_readme_and_ci_is_not_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "README.md").write_text("Run `uv run pytest`.\n")
            (root / "package.json").write_text(
                json.dumps({"scripts": {"test": "uv run pytest", "build": "python -m build"}})
            )
            workflow = root / ".github" / "workflows"
            workflow.mkdir(parents=True)
            (workflow / "checks.yml").write_text(
                "jobs:\n  test:\n    steps:\n      - run: uv run pytest\n"
            )

            plan = plan_repository_commands(root)

            self.assertEqual(
                [record.status for record in plan.records],
                [
                    CommandStatus.RECOGNIZED,
                    CommandStatus.RECOGNIZED,
                ],
            )
            self.assertEqual(
                plan.records[0].sources,
                (".github/workflows/checks.yml", "README.md", "package.json"),
            )
            self.assertEqual(plan.records[0].executable, "uv")
            self.assertEqual(plan.records[1].args, ("-m", "build"))

    def test_distinct_recognized_commands_are_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "README.md").write_text("Run `pytest`.\n")
            workflow = root / ".github" / "workflows"
            workflow.mkdir(parents=True)
            (workflow / "checks.yml").write_text(
                "jobs:\n  test:\n    steps:\n      - run: npm test\n"
            )

            record = plan_repository_commands(root).records[0]

            self.assertEqual(record.intent, "test")
            self.assertEqual(record.status, CommandStatus.AMBIGUOUS)
            self.assertEqual(len(record.alternatives), 2)

    def test_multiline_ci_commands_are_extracted_without_yaml_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workflow = root / ".github" / "workflows"
            workflow.mkdir(parents=True)
            (workflow / "checks.yml").write_text(
                "jobs:\n  test:\n    steps:\n      - run: |\n          uv run pytest\n"
            )

            record = plan_repository_commands(root).records[0]

            self.assertEqual(record.status, CommandStatus.RECOGNIZED)
            self.assertEqual(record.executable, "uv")


if __name__ == "__main__":
    unittest.main()
