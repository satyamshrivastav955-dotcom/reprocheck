"""Python profile regressions: complete acquisition, runner choice, and safe preview."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reprocheck.verification import (
    ExecutionStatus,
    PlanPhase,
    ProfileError,
    execute_plan,
    plan_verification,
)


class PythonProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.write("pyproject.toml", "[project]\nname='sample'\nversion='0.1.0'\n")
        self.write("tests/test_sample.py", "def test_ok(): pass\n")

    def write(self, relative: str, content: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def pip_plan(self, **kwargs):
        return plan_verification(self.root, profile="python-pip-editable", **kwargs)

    def test_dev_and_test_requirements_are_acquired_before_offline_pytest(self) -> None:
        self.write("requirements-dev.txt", "ruff==0.8.0\n")
        self.write("requirements-test.txt", "pytest==8.3.0\n")

        plan = self.pip_plan(timeout_seconds=42)

        self.assertEqual(
            [item.argv for item in plan.commands],
            [
                (
                    "python",
                    "-m",
                    "pip",
                    "install",
                    "-e",
                    ".",
                    "-r",
                    "requirements-dev.txt",
                    "-r",
                    "requirements-test.txt",
                ),
                ("python", "-m", "pytest"),
            ],
        )
        self.assertEqual(
            [(item.phase, item.network_required) for item in plan.commands],
            [(PlanPhase.ACQUISITION, True), (PlanPhase.VERIFICATION, False)],
        )
        self.assertTrue(plan.acquisition_network_required)
        self.assertFalse(plan.verification_network_required)
        self.assertTrue(all(item.command.cwd == "." for item in plan.commands))
        self.assertTrue(all(item.command.timeout_seconds == 42 for item in plan.commands))

    def test_each_conventional_requirements_file_is_sufficient(self) -> None:
        for filename in ("requirements-dev.txt", "requirements-test.txt"):
            with self.subTest(filename=filename):
                self.write(filename, "pytest>=8,<9\n")
                plan = self.pip_plan()
                self.assertEqual(
                    plan.commands[0].argv,
                    ("python", "-m", "pip", "install", "-e", ".", "-r", filename),
                )
                (self.root / filename).unlink()

    def test_declared_extras_are_selected_without_inventing_extra_names(self) -> None:
        self.write(
            "pyproject.toml",
            "[project]\nname='sample'\nversion='0.1.0'\n"
            "[project.optional-dependencies]\n"
            "docs=['sphinx']\ntest=['pytest>=8']\ndev=['ruff']\n",
        )
        self.assertEqual(
            self.pip_plan().commands[0].argv,
            ("python", "-m", "pip", "install", "-e", ".[dev,test]"),
        )

    def test_requirements_and_extras_are_both_installed(self) -> None:
        self.write("requirements-dev.txt", "coverage\n")
        self.write(
            "pyproject.toml",
            "[project]\nname='sample'\nversion='0.1.0'\n"
            "[project.optional-dependencies]\ntests=['pytest']\n",
        )
        self.assertEqual(
            self.pip_plan().commands[0].argv,
            (
                "python",
                "-m",
                "pip",
                "install",
                "-e",
                ".[tests]",
                "-r",
                "requirements-dev.txt",
            ),
        )

    def test_runtime_pytest_dependency_does_not_invent_extras(self) -> None:
        self.write(
            "pyproject.toml",
            "[project]\nname='sample'\nversion='0.1.0'\ndependencies=['pytest>=8']\n",
        )
        plan = self.pip_plan()
        self.assertEqual(plan.commands[0].argv, ("python", "-m", "pip", "install", "-e", "."))
        self.assertEqual(plan.commands[1].argv, ("python", "-m", "pytest"))

    def test_setup_cfg_declares_extras_without_executing_setup_py(self) -> None:
        (self.root / "pyproject.toml").unlink()
        self.write("setup.py", "raise AssertionError('must not execute metadata')\n")
        self.write(
            "setup.cfg",
            "[metadata]\nname=sample\n[options.extras_require]\n"
            "test =\n    pytest>=8\ndev =\n    coverage\n",
        )
        self.assertEqual(
            self.pip_plan().commands[0].argv,
            ("python", "-m", "pip", "install", "-e", ".[dev,test]"),
        )

    def test_pep735_groups_are_rejected_clearly_not_installed_as_extras(self) -> None:
        for groups in (
            "dev=['pytest']",
            "test=['pytest']",
            "test=[{include-group='base'}]\nbase=['pytest']",
            "test=[{include-group='test'}]",
        ):
            with self.subTest(groups=groups):
                self.write(
                    "pyproject.toml",
                    f"[project]\nname='sample'\nversion='0.1.0'\n[dependency-groups]\n{groups}\n",
                )
                with self.assertRaisesRegex(
                    ProfileError, "dependency-groups.*requirements|PEP 735"
                ):
                    self.pip_plan()

    def test_groups_are_not_silently_omitted_when_extra_also_exists(self) -> None:
        self.write(
            "pyproject.toml",
            "[project]\nname='sample'\nversion='0.1.0'\n"
            "[project.optional-dependencies]\ntest=['pytest']\n"
            "[dependency-groups]\ndev=['hypothesis']\n",
        )
        with self.assertRaisesRegex(ProfileError, "PEP 735|dependency-groups"):
            self.pip_plan()

    def test_no_test_dependencies_or_runner_is_an_actionable_profile_error(self) -> None:
        with self.assertRaisesRegex(ProfileError, "requirements|runner|test dependencies"):
            self.pip_plan()

    def test_nonpytest_dependencies_do_not_imply_pytest(self) -> None:
        self.write("requirements-test.txt", "nose2==0.15.0\n")
        with self.assertRaisesRegex(ProfileError, "runner|pytest|unittest"):
            self.pip_plan()

    def test_pytest_config_without_installable_pytest_is_rejected(self) -> None:
        self.write("pytest.ini", "[pytest]\ntestpaths=tests\n")
        with self.assertRaisesRegex(ProfileError, "pytest.*depend|depend.*pytest"):
            self.pip_plan()

    def test_recognized_pytest_configs_use_the_declared_dependency(self) -> None:
        self.write("requirements-test.txt", "pytest==8.3.0\n")
        for filename, config in (
            ("pytest.ini", "[pytest]\ntestpaths=tests\n"),
            (".pytest.ini", "[pytest]\n"),
            ("setup.cfg", "[tool:pytest]\ntestpaths=tests\n"),
            ("tox.ini", "[pytest]\n"),
            ("tox.ini", "[testenv]\ncommands=python -m pytest\n"),
            ("tox.ini", "[testenv]\ncommands=pytest\n"),
        ):
            with self.subTest(filename=filename, config=config):
                self.write(filename, config)
                self.assertEqual(self.pip_plan().commands[1].argv, ("python", "-m", "pytest"))
                (self.root / filename).unlink()
        self.write(
            "pyproject.toml",
            "[project]\nname='sample'\nversion='0.1.0'\n"
            "[tool.pytest.ini_options]\ntestpaths=['tests']\n",
        )
        self.assertEqual(self.pip_plan().commands[1].argv, ("python", "-m", "pytest"))

    def test_pytest_plugin_name_or_conditional_requirement_is_not_pytest(self) -> None:
        for requirement in ("pytest-cov>=4", "pytest; python_version < '3.0'"):
            with self.subTest(requirement=requirement):
                self.write("requirements-test.txt", requirement + "\n")
                with self.assertRaises(ProfileError):
                    self.pip_plan()

    def test_unittest_config_selects_stdlib_runner_without_pytest_dependency(self) -> None:
        self.write(
            "tests/test_sample.py",
            "import unittest\nclass Sample(unittest.TestCase):\n"
            "    def test_ok(self): self.assertTrue(True)\n",
        )
        self.write("tox.ini", "[testenv]\ncommands = python -m unittest discover -s tests\n")
        plan = self.pip_plan()
        self.assertEqual(
            [item.argv for item in plan.commands],
            [
                ("python", "-m", "pip", "install", "-e", "."),
                ("python", "-m", "unittest", "discover", "-s", "tests"),
            ],
        )
        self.assertFalse(plan.commands[1].network_required)

    def test_explicit_unittest_runner_wins_over_pytest_dev_dependency(self) -> None:
        self.write("requirements-dev.txt", "pytest\n")
        self.write("tox.ini", "[testenv]\ncommands = python -m unittest discover -s tests\n")
        self.assertEqual(
            self.pip_plan().commands[1].argv,
            ("python", "-m", "unittest", "discover", "-s", "tests"),
        )

    def test_conflicting_runner_configs_are_rejected(self) -> None:
        self.write("requirements-test.txt", "pytest\n")
        self.write("pytest.ini", "[pytest]\n")
        self.write("tox.ini", "[testenv]\ncommands = python -m unittest discover -s tests\n")
        with self.assertRaisesRegex(ProfileError, "conflict|runner"):
            self.pip_plan()

    def test_unsupported_configured_runner_is_not_replaced_with_pytest(self) -> None:
        self.write("requirements-dev.txt", "pytest\nnose2\n")
        self.write("tox.ini", "[testenv]\ncommands = nose2\n")
        with self.assertRaisesRegex(ProfileError, "runner|commands|unsupported"):
            self.pip_plan()

    def test_tox_only_dependencies_are_not_silently_omitted(self) -> None:
        self.write("requirements-dev.txt", "pytest\n")
        self.write(
            "tox.ini",
            "[testenv]\ndeps=hypothesis\ncommands=python -m unittest discover -s tests\n",
        )
        with self.assertRaisesRegex(ProfileError, "tox.*dep|dep.*tox"):
            self.pip_plan()

    def test_tox_repeating_acquired_requirements_does_not_change_the_plan(self) -> None:
        self.write("requirements-dev.txt", "pytest==8.3.0\n")
        self.write("requirements-test.txt", "hypothesis==6.100.0\n")
        baseline = self.pip_plan()
        for deps in (
            "-r requirements-dev.txt",
            "-r requirements-test.txt",
            "\n    -r requirements-dev.txt\n    -r requirements-test.txt",
            "\n    # acquired by the pip profile\n    --requirement=requirements-dev.txt",
        ):
            with self.subTest(deps=deps):
                self.write("tox.ini", f"[testenv]\ndeps={deps}\ncommands=python -m pytest\n")
                with patch("subprocess.Popen") as popen:
                    plan = self.pip_plan()
                    popen.assert_not_called()
                self.assertEqual(plan.commands, baseline.commands)
                self.assertTrue(plan.acquisition_network_required)
                self.assertFalse(plan.verification_network_required)

    def test_tox_literal_dependencies_already_acquired_from_metadata_are_covered(self) -> None:
        self.write(
            "pyproject.toml",
            "[project]\nname='sample'\nversion='0.1.0'\n"
            "dependencies=['coverage==7.6.0']\n"
            "[project.optional-dependencies]\ntest=['pytest==8.3.0']\n",
        )
        baseline = self.pip_plan()
        self.write(
            "tox.ini",
            "[testenv]\ndeps=\n    coverage==7.6.0\n    pytest==8.3.0\ncommands=pytest\n",
        )
        self.assertEqual(self.pip_plan().commands, baseline.commands)

    def test_tox_literal_dependencies_already_acquired_from_nested_requirements_are_covered(
        self,
    ) -> None:
        self.write("requirements-dev.txt", "-r req/test.txt\n")
        self.write("req/test.txt", "pytest==8.3.0\nhypothesis==6.100.0\n")
        baseline = self.pip_plan()
        self.write(
            "tox.ini",
            "[testenv]\ndeps=\n    -r requirements-dev.txt\n    hypothesis==6.100.0\n"
            "commands=pytest\n",
        )
        self.assertEqual(self.pip_plan().commands, baseline.commands)

    def test_tox_uncovered_or_unsafe_dependencies_and_extras_still_fail(self) -> None:
        self.write("requirements-dev.txt", "pytest==8.3.0\n")
        self.write("unselected.txt", "pytest==8.3.0\n")
        self.write("pins.txt", "hypothesis==6.100.0\n")
        self.write("requirements-test.txt", "-c pins.txt\ncoverage\n")
        for option in (
            "deps=-r unselected.txt",
            "deps=-r missing.txt",
            "deps=-r ../requirements-dev.txt",
            "deps=-r https://example.test/requirements-dev.txt",
            "deps=-r {toxinidir}/requirements-dev.txt",
            "deps=pytest>=8",
            "deps=pytest[extra]==8.3.0",
            "deps=hypothesis==6.100.0",
            "deps={env:TEST_DEPS}",
            "deps=\n    -r requirements-dev.txt\n    missing-package",
            "extras=test",
            "dependency_groups=test",
        ):
            with self.subTest(option=option):
                self.write("tox.ini", f"[testenv]\n{option}\ncommands=pytest\n")
                with patch("subprocess.Popen") as popen:
                    with self.assertRaises(ProfileError):
                        self.pip_plan()
                    popen.assert_not_called()

    def test_covered_tox_deps_do_not_allow_arbitrary_tox_commands(self) -> None:
        self.write("requirements-dev.txt", "pytest\n")
        self.write(
            "tox.ini",
            "[testenv]\ndeps=-r requirements-dev.txt\ncommands=python arbitrary.py\n",
        )
        with patch("subprocess.Popen") as popen:
            with self.assertRaisesRegex(ProfileError, "unsupported.*commands"):
                self.pip_plan()
            popen.assert_not_called()

    def test_conflicting_dependency_metadata_is_not_assumed_to_be_installed(self) -> None:
        self.write("setup.cfg", "[options.extras_require]\ntest=pytest\n")
        with self.assertRaisesRegex(ProfileError, "ambiguous|conflict"):
            self.pip_plan()

    def test_conditional_setup_cfg_test_extras_are_not_silently_omitted(self) -> None:
        (self.root / "pyproject.toml").unlink()
        self.write("setup.py", "raise AssertionError('never execute')\n")
        self.write("requirements-dev.txt", "pytest\n")
        self.write(
            "setup.cfg",
            "[options.extras_require]\ntest:python_version<'3' = hypothesis\n",
        )
        with self.assertRaises(ProfileError):
            self.pip_plan()

    def test_skipping_tests_keeps_only_editable_acquisition(self) -> None:
        self.write("requirements-test.txt", "-r ../outside.txt\n")
        self.write(
            "pyproject.toml",
            "[project]\nname='sample'\nversion='0.1.0'\n[dependency-groups]\ndev=['pytest']\n",
        )
        plan = self.pip_plan(include_tests=False)
        self.assertEqual(
            [item.argv for item in plan.commands],
            [("python", "-m", "pip", "install", "-e", ".")],
        )

    def test_no_tests_keeps_install_only_with_limitation(self) -> None:
        (self.root / "tests/test_sample.py").unlink()
        plan = self.pip_plan()
        self.assertEqual(len(plan.commands), 1)
        self.assertTrue(any("No repository tests" in item for item in plan.limitations))

    def test_safe_nested_requirements_are_inspected_without_flattening_argv(self) -> None:
        self.write("requirements-dev.txt", "-r requirements/testing.txt\n")
        self.write("requirements/testing.txt", "pytest==8.3.0\n")
        self.assertEqual(
            self.pip_plan().commands[0].argv,
            ("python", "-m", "pip", "install", "-e", ".", "-r", "requirements-dev.txt"),
        )

    def test_constraints_do_not_count_as_installed_runner(self) -> None:
        self.write("requirements-test.txt", "-c constraints.txt\ncoverage\n")
        self.write("constraints.txt", "pytest==8.3.0\n")
        with self.assertRaises(ProfileError):
            self.pip_plan()

    def test_constraints_and_long_include_options_preserve_exact_install_argv(self) -> None:
        self.write("requirements-dev.txt", "--requirement=req/test.txt\n")
        self.write("req/test.txt", "--constraint pins.txt\npytest\n")
        self.write("req/pins.txt", "pytest==8.3.0\n")
        self.assertEqual(
            [item.argv for item in self.pip_plan().commands],
            [
                ("python", "-m", "pip", "install", "-e", ".", "-r", "requirements-dev.txt"),
                ("python", "-m", "pytest"),
            ],
        )

    def test_requirements_includes_are_local_bounded_and_acyclic(self) -> None:
        for include in (
            "../outside.txt",
            "/etc/passwd",
            "C:/outside.txt",
            "https://example.test/req.txt",
            "missing.txt",
            "requirements-dev.txt",
        ):
            with self.subTest(include=include):
                self.write("requirements-dev.txt", f"pytest\n-r {include}\n")
                with self.assertRaises(ProfileError):
                    self.pip_plan()

    def test_requirements_include_depth_is_bounded(self) -> None:
        self.write("requirements-dev.txt", "pytest\n-r req/0.txt\n")
        for index in range(40):
            self.write(f"req/{index}.txt", f"-r {index + 1}.txt\n")
        self.write("req/40.txt", "coverage\n")
        with self.assertRaisesRegex(ProfileError, "bound|depth|many"):
            self.pip_plan()

    def test_environment_substitution_cannot_redirect_an_include(self) -> None:
        self.write("requirements-dev.txt", "pytest\n-r ${HOME}/test.txt\n")
        self.write("${HOME}/test.txt", "coverage\n")
        with self.assertRaises(ProfileError):
            self.pip_plan()

    def test_internal_symlink_metadata_is_rejected(self) -> None:
        self.write("actual.txt", "pytest\n")
        try:
            (self.root / "requirements-dev.txt").symlink_to(self.root / "actual.txt")
        except OSError as error:
            self.skipTest(f"symlinks are unavailable: {error}")
        with self.assertRaises(ProfileError):
            self.pip_plan()

    def test_external_symlink_and_nonregular_requirements_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "requirements.txt"
            target.write_text("pytest\n", encoding="utf-8")
            try:
                (self.root / "requirements-test.txt").symlink_to(target)
            except OSError as error:
                self.skipTest(f"symlinks are unavailable: {error}")
            with self.assertRaises(ProfileError):
                self.pip_plan()
        (self.root / "requirements-test.txt").unlink()
        (self.root / "requirements-test.txt").mkdir()
        with self.assertRaises(ProfileError):
            self.pip_plan()

    def test_unsupported_requirements_do_not_create_a_partially_known_plan(self) -> None:
        for requirement in (
            "-e ../outside",
            "../outside.whl",
            "package @ file:///outside",
            "--index-url https://example.test/simple",
            "pytest \\",
        ):
            with self.subTest(requirement=requirement):
                self.write("requirements-test.txt", f"pytest\n{requirement}\n")
                with self.assertRaisesRegex(ProfileError, "unsupported dependency"):
                    self.pip_plan()

    def test_dynamic_or_malformed_dependency_metadata_is_a_profile_error(self) -> None:
        for metadata in (
            "dynamic=['optional-dependencies']",
            "dynamic=['dependencies']",
            "dependencies='pytest'",
            "dependencies=[42]",
            "[project.optional-dependencies]\ntest='pytest'",
            "[project.optional-dependencies]\ntest=[{include-group='test'}]",
            "[dependency-groups]\ntest=['pytest']",
        ):
            with self.subTest(metadata=metadata):
                self.write(
                    "pyproject.toml",
                    f"[project]\nname='sample'\nversion='0.1.0'\n{metadata}\n",
                )
                with self.assertRaises(ProfileError):
                    self.pip_plan()

    def test_symlink_directory_in_requirements_include_is_rejected(self) -> None:
        self.write("actual/test.txt", "pytest\n")
        try:
            (self.root / "linked").symlink_to(self.root / "actual", target_is_directory=True)
        except OSError as error:
            self.skipTest(f"symlinks are unavailable: {error}")
        self.write("requirements-dev.txt", "pytest\n-r linked/test.txt\n")
        with self.assertRaises(ProfileError):
            self.pip_plan()

    def test_python_metadata_reads_are_bounded_and_parse_errors_are_profile_errors(self) -> None:
        for relative, content, message in (
            ("pyproject.toml", "[project\n", "cannot parse pyproject"),
            ("pyproject.toml", "#" * (1024 * 1024 + 1), "bounded"),
            ("requirements-dev.txt", "#" * (1024 * 1024 + 1), "bounded"),
        ):
            with self.subTest(relative=relative, size=len(content)):
                self.write(
                    "pyproject.toml",
                    "[project]\nname='sample'\nversion='0.1.0'\ndependencies=['pytest']\n",
                )
                self.write(relative, content)
                with self.assertRaisesRegex(ProfileError, message):
                    self.pip_plan()

    def test_uv_requires_real_root_lockfile_and_manifest(self) -> None:
        self.write("nested/uv.lock", "version = 1\n")
        with self.assertRaisesRegex(ProfileError, "uv.lock"):
            plan_verification(self.root, profile="python-uv")
        self.write("uv.lock", "version = 1\n")
        (self.root / "pyproject.toml").rename(self.root / "nested/pyproject.toml")
        with self.assertRaises(ProfileError):
            plan_verification(self.root, profile="python-uv")

    def test_uv_missing_lockfile_suggests_explicit_pip_after_dependency_inspection(self) -> None:
        self.write("requirements-test.txt", "pytest\n")
        with patch("subprocess.Popen") as popen:
            with self.assertRaises(ProfileError) as raised:
                plan_verification(self.root, profile="python-uv")
            popen.assert_not_called()
        message = str(raised.exception)
        self.assertIn("requires both pyproject.toml and uv.lock", message)
        self.assertIn("--profile python-pip-editable", message)
        self.assertIn("test dependencies", message)

    def test_uv_missing_lockfile_preserves_pip_dependency_error_in_guidance(self) -> None:
        with self.assertRaises(ProfileError) as raised:
            plan_verification(self.root, profile="python-uv")
        message = str(raised.exception)
        self.assertIn("requires both pyproject.toml and uv.lock", message)
        self.assertIn("--profile python-pip-editable", message)
        self.assertIn("cannot infer", message)
        self.assertIn("test dependencies", message)

    def test_uv_missing_lockfile_preserves_group_rejection_in_guidance(self) -> None:
        self.write(
            "pyproject.toml",
            "[project]\nname='sample'\nversion='0.1.0'\n[dependency-groups]\ntest=['pytest']\n",
        )
        with self.assertRaises(ProfileError) as raised:
            plan_verification(self.root, profile="python-uv")
        message = str(raised.exception)
        self.assertIn("requires both pyproject.toml and uv.lock", message)
        self.assertIn("--profile python-pip-editable", message)
        self.assertIn("PEP 735", message)

    def test_uv_locked_argv_and_default_selection_remain_compatible(self) -> None:
        self.write("uv.lock", "version = 1\n")
        plan = plan_verification(self.root)
        self.assertEqual(plan.profile, "python-uv")
        self.assertEqual(
            [item.argv for item in plan.commands],
            [("uv", "sync", "--locked"), ("uv", "run", "python", "-m", "pytest")],
        )
        self.assertTrue(plan.commands[0].network_required)
        self.assertFalse(plan.commands[1].network_required)

    def test_pip_stays_explicit_and_preview_never_executes(self) -> None:
        self.write("requirements-test.txt", "pytest\n")
        with (
            patch("subprocess.Popen") as popen,
            patch("reprocheck.verification.executor.platform.platform", return_value="test OS"),
        ):
            with self.assertRaises(ProfileError):
                plan_verification(self.root)
            plan = self.pip_plan()
            with patch("reprocheck.verification.executor.materialize_workspace") as workspace:
                denied = execute_plan(plan, approved=False, allow_host_exec=True)
                blocked = execute_plan(plan, approved=True, allow_host_exec=False)
            workspace.assert_not_called()
            popen.assert_not_called()
        self.assertEqual(denied.status, ExecutionStatus.DENIED)
        self.assertEqual(denied.commands, ())
        self.assertEqual(blocked.status, ExecutionStatus.BLOCKED)
        self.assertEqual(blocked.commands, ())


if __name__ == "__main__":
    unittest.main()
