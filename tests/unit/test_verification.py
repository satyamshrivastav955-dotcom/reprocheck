import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reprocheck.models import CommandSpec, Ecosystem, ResultState
from reprocheck.verification import (
    NODE_DOCKER_IMAGE,
    PYTHON_DOCKER_IMAGE,
    AcquisitionPolicy,
    DockerSubprocessExecutor,
    ExecutionStatus,
    PlannedCommand,
    PlanPhase,
    ProfileError,
    VerificationPlan,
    WorkspaceError,
    execute_plan,
    materialize_workspace,
    plan_verification,
)


class PlannerTests(unittest.TestCase):
    def test_python_uv_plan_is_argv_only_and_marks_acquisition_network(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pyproject.toml").write_text(
                "[project]\nname='sample'\nversion='0.1.0'\n", encoding="utf-8"
            )
            (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
            (root / "tests").mkdir()
            (root / "tests" / "test_sample.py").write_text(
                "def test_ok(): pass\n", encoding="utf-8"
            )

            plan = plan_verification(root)

            self.assertEqual(plan.profile, "python-uv")
            self.assertEqual(plan.ecosystem, Ecosystem.PYTHON)
            self.assertEqual(plan.commands[0].argv, ("uv", "sync", "--locked"))
            self.assertEqual(plan.commands[0].phase, PlanPhase.ACQUISITION)
            self.assertTrue(plan.commands[0].network_required)
            self.assertEqual(plan.commands[1].argv, ("uv", "run", "python", "-m", "pytest"))
            self.assertFalse(plan.commands[1].network_required)

    def test_editable_pip_requires_explicit_profile_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pyproject.toml").write_text(
                "[project]\nname='sample'\nversion='0.1.0'\n", encoding="utf-8"
            )

            with self.assertRaises(ProfileError):
                plan_verification(root)
            plan = plan_verification(root, profile="python-pip-editable", include_tests=False)

            self.assertEqual(plan.commands[0].argv, ("python", "-m", "pip", "install", "-e", "."))
            self.assertIn("explicitly selected", plan.limitations[0])

    def test_node_plan_accepts_only_npm_ci_and_declared_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text(
                '{"name":"sample","scripts":{"test":"node test.js","build":"node build.js"}}',
                encoding="utf-8",
            )
            (root / "package-lock.json").write_text('{"lockfileVersion": 3}', encoding="utf-8")

            plan = plan_verification(root, profile="node-npm")

            self.assertEqual(
                [item.argv for item in plan.commands],
                [
                    ("npm", "ci"),
                    ("npm", "test"),
                    ("npm", "run", "build"),
                ],
            )
            self.assertTrue(plan.acquisition_network_required)
            self.assertFalse(plan.verification_network_required)


class WorkspaceAndExecutorTests(unittest.TestCase):
    def _command_plan(
        self, root: Path, *args: str, timeout: float | None = 2.0
    ) -> VerificationPlan:
        command = CommandSpec(
            executable=sys.executable,
            args=args,
            cwd=".",
            timeout_seconds=timeout,
        )
        return VerificationPlan(
            repository=root,
            profile="test-command",
            ecosystem=Ecosystem.PYTHON,
            commands=(
                PlannedCommand(
                    command=command,
                    phase=PlanPhase.VERIFICATION,
                    network_required=False,
                    purpose="Run a test command",
                ),
            ),
            acquisition=AcquisitionPolicy(network_required=False),
        )

    def test_workspace_copies_regular_files_and_rejects_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "regular.txt").write_text("safe", encoding="utf-8")
            with materialize_workspace(root) as workspace:
                self.assertEqual((workspace / "regular.txt").read_text(encoding="utf-8"), "safe")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.txt"
            target.write_text("target", encoding="utf-8")
            try:
                (root / "link.txt").symlink_to(target)
            except OSError as error:
                self.skipTest(f"symlinks are unavailable: {error}")
            with self.assertRaises(WorkspaceError):
                materialize_workspace(root)

    def test_approval_and_host_policy_gate_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "input.txt").write_text("input", encoding="utf-8")
            plan = self._command_plan(
                root,
                "-c",
                "import pathlib; print(pathlib.Path('input.txt').read_text()); "
                "print('api_key=hidden')",
            )

            denied = execute_plan(plan, approved=False, allow_host_exec=True)
            self.assertEqual(denied.status, ExecutionStatus.DENIED)
            self.assertEqual(denied.commands, ())

            blocked = execute_plan(plan, approved=True)
            self.assertEqual(blocked.status, ExecutionStatus.BLOCKED)
            self.assertEqual(blocked.commands, ())

            approved = execute_plan(
                plan,
                approved=True,
                allow_host_exec=True,
                max_output_bytes=256,
            )
            self.assertEqual(approved.status, ExecutionStatus.PASS)
            self.assertEqual(approved.commands[0].exit_code, 0)
            self.assertIn("<REDACTED_VALUE>", approved.commands[0].stdout)
            self.assertTrue(any("not a complete sandbox" in item for item in approved.limitations))

    def test_executor_times_out_and_does_not_inherit_secret_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = self._command_plan(
                root,
                "-c",
                "import os, time; print(os.getenv('REPROCHECK_TEST_SECRET', 'missing')); "
                "time.sleep(2)",
                timeout=0.05,
            )
            old_value = os.environ.get("REPROCHECK_TEST_SECRET")
            os.environ["REPROCHECK_TEST_SECRET"] = "must-not-be-inherited"
            try:
                result = execute_plan(plan, approved=True, allow_host_exec=True)
            finally:
                if old_value is None:
                    os.environ.pop("REPROCHECK_TEST_SECRET", None)
                else:
                    os.environ["REPROCHECK_TEST_SECRET"] = old_value

            self.assertEqual(result.status, ExecutionStatus.TIMEOUT)
            self.assertTrue(result.commands[0].timed_out)
            self.assertNotIn("must-not-be-inherited", result.commands[0].stdout)

    def test_timeout_cleans_up_the_real_materialized_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            captured: dict[str, Path] = {}

            from reprocheck.verification import executor as executor_module

            real_materialize = executor_module.materialize_workspace

            def observe_workspace(source: Path) -> object:
                workspace = real_materialize(source)
                captured["path"] = workspace.path
                return workspace

            with patch.object(
                executor_module, "materialize_workspace", side_effect=observe_workspace
            ):
                result = execute_plan(
                    self._command_plan(
                        root,
                        "-c",
                        "import time; time.sleep(1)",
                        timeout=0.05,
                    ),
                    approved=True,
                    allow_host_exec=True,
                )

            self.assertEqual(result.status, ExecutionStatus.TIMEOUT)
            self.assertIn("path", captured)
            self.assertFalse(captured["path"].exists())

    def test_failure_and_infrastructure_error_have_distinct_statuses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            failed = execute_plan(
                self._command_plan(root, "-c", "raise SystemExit(3)"),
                approved=True,
                allow_host_exec=True,
            )
            self.assertEqual(failed.status, ExecutionStatus.FAIL)

            missing = VerificationPlan(
                repository=root,
                profile="test-infra",
                ecosystem=Ecosystem.PYTHON,
                commands=(
                    PlannedCommand(
                        command=CommandSpec(executable="reprocheck-command-does-not-exist"),
                        phase=PlanPhase.VERIFICATION,
                        network_required=False,
                        purpose="Run an infrastructure probe",
                    ),
                ),
                acquisition=AcquisitionPolicy(network_required=False),
            )
            infrastructure_error = execute_plan(
                missing,
                approved=True,
                allow_host_exec=True,
            )
            self.assertEqual(infrastructure_error.status, ExecutionStatus.INFRA_ERROR)

    def test_output_is_bounded_and_workspace_cwd_cannot_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            noisy = execute_plan(
                self._command_plan(
                    root,
                    "-c",
                    "print('x' * 100000)",
                ),
                approved=True,
                allow_host_exec=True,
                max_output_bytes=128,
            )
            self.assertEqual(noisy.status, ExecutionStatus.PASS)
            self.assertLessEqual(len(noisy.commands[0].stdout), 128)

            traversal_plan = VerificationPlan(
                repository=root,
                profile="traversal",
                ecosystem=Ecosystem.PYTHON,
                commands=(
                    PlannedCommand(
                        command=CommandSpec(
                            executable=sys.executable,
                            args=("-c", "print('must not run')"),
                            cwd="../",
                        ),
                        phase=PlanPhase.VERIFICATION,
                        network_required=False,
                        purpose="Run a test command",
                    ),
                ),
                acquisition=AcquisitionPolicy(network_required=False),
            )
            escaped = execute_plan(
                traversal_plan,
                approved=True,
                allow_host_exec=True,
            )
            self.assertEqual(escaped.status, ExecutionStatus.INFRA_ERROR)
            self.assertEqual(escaped.commands[0].exit_code, None)

    def test_docker_unavailability_is_blocked_without_host_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = self._command_plan(root, "-c", "print('must not run')")
            blocked = execute_plan(
                plan,
                approved=True,
                backend="docker",
                docker_executable="reprocheck-docker-not-installed",
            )
            self.assertEqual(blocked.status, ExecutionStatus.BLOCKED)
            self.assertEqual(blocked.commands, ())

    def test_docker_backend_uses_profile_images_and_resource_controls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = self._command_plan(root, "-c", "print('safe')")
            planned = plan.commands[0]
            executor = DockerSubprocessExecutor(docker_image=PYTHON_DOCKER_IMAGE)

            argv = executor._docker_argv(  # noqa: SLF001
                planned,
                workspace=root,
                environment={"PATH": "/usr/bin"},
                image=PYTHON_DOCKER_IMAGE,
            )

            self.assertIn("--network", argv)
            self.assertIn("none", argv)
            self.assertIn("--cap-drop=ALL", argv)
            self.assertIn("--security-opt=no-new-privileges", argv)
            self.assertIn("--cpus", argv)
            self.assertIn("--memory", argv)
            self.assertIn("--pids-limit", argv)
            self.assertIn("--tmpfs", argv)
            self.assertNotEqual(PYTHON_DOCKER_IMAGE, NODE_DOCKER_IMAGE)

    def test_docker_does_not_forward_windows_path_or_misclassify_startup_errors(self) -> None:
        # The Docker backend mounts /tmp with noexec. Keep the explicit Docker
        # stub on the project workspace filesystem so the test models a usable
        # executable backend rather than Docker's intentional /tmp restriction.
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            plan = VerificationPlan(
                repository=root,
                profile="python-uv",
                ecosystem=Ecosystem.PYTHON,
                commands=(
                    PlannedCommand(
                        command=CommandSpec(executable="uv", args=("sync", "--locked"), cwd="."),
                        phase=PlanPhase.ACQUISITION,
                        network_required=True,
                        purpose="Synchronize the locked Python environment",
                    ),
                ),
                acquisition=AcquisitionPolicy(network_required=True),
            )
            if sys.platform == "win32":
                launcher = root / "docker-stub-launcher.cmd"
                launcher_py = root / "docker-stub-launcher.py"
                launcher_py.write_text(
                    "import sys\n"
                    "args = sys.argv[1:]\n"
                    "for index, value in enumerate(args[:-1]):\n"
                    "    if value == '--env' and args[index + 1].startswith('PATH='):\n"
                    "        raise SystemExit(97)\n"
                    "if 'uv' not in args:\n"
                    "    raise SystemExit(98)\n"
                    "print('uv launched')\n",
                    encoding="utf-8",
                )
                launcher.write_text(f'@"{sys.executable}" "{launcher_py}" %*\n', encoding="utf-8")
            else:
                launcher = root / "docker-stub-launcher"
                launcher.write_text(
                    f"#!{sys.executable}\n"
                    "import sys\n"
                    "args = sys.argv[1:]\n"
                    "for index, value in enumerate(args[:-1]):\n"
                    "    if value == '--env' and args[index + 1].startswith('PATH='):\n"
                    "        raise SystemExit(97)\n"
                    "if 'uv' not in args:\n"
                    "    raise SystemExit(98)\n"
                    "print('uv launched')\n",
                    encoding="utf-8",
                )
                launcher.chmod(0o755)
            windows_path = r"C:\Windows\system32;C:\repo\.venv-win\Scripts"
            with patch.dict(os.environ, {"PATH": windows_path}, clear=False):
                result = execute_plan(
                    plan,
                    approved=True,
                    backend="docker",
                    docker_executable=str(launcher),
                )

            self.assertEqual(result.status, ExecutionStatus.PASS)
            self.assertEqual(result.run.state, ResultState.VERIFIED_PASS)
            self.assertIn("uv launched", result.commands[0].stdout)
            self.assertNotIn("PATH", result.commands[0].command.env)
            self.assertNotIn(windows_path, result.commands[0].command.to_json())

            if sys.platform == "win32":
                startup_failure = root / "docker-stub-startup-failure.cmd"
                startup_failure_py = root / "docker-stub-startup-failure.py"
                startup_failure_py.write_text(
                    "import sys\n"
                    "sys.stderr.write(\"The command 'docker' could not be found in this WSL 2 "
                    'distro.\\n")\n'
                    "raise SystemExit(126)\n",
                    encoding="utf-8",
                )
                startup_failure.write_text(
                    f'@"{sys.executable}" "{startup_failure_py}" %*\n', encoding="utf-8"
                )
            else:
                startup_failure = root / "docker-stub-startup-failure"
                startup_failure.write_text(
                    f"#!{sys.executable}\n"
                    "import sys\n"
                    "sys.stderr.write(\"The command 'docker' could not be found in this WSL 2 "
                    'distro.\\n")\n'
                    "raise SystemExit(126)\n",
                    encoding="utf-8",
                )
                startup_failure.chmod(0o755)
            with patch.dict(os.environ, {"PATH": windows_path}, clear=False):
                infrastructure_error = execute_plan(
                    plan,
                    approved=True,
                    backend="docker",
                    docker_executable=str(startup_failure),
                )

            self.assertEqual(infrastructure_error.status, ExecutionStatus.INFRA_ERROR)
            self.assertEqual(infrastructure_error.run.state, ResultState.NOT_VERIFIED)
            self.assertEqual(
                infrastructure_error.commands[0].result_state, ResultState.NOT_VERIFIED
            )
            self.assertNotEqual(
                infrastructure_error.commands[0].result_state, ResultState.VERIFIED_FAIL
            )


if __name__ == "__main__":
    unittest.main()
