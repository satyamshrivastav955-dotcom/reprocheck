import os
import sys
import tempfile
import unittest
from pathlib import Path

from reprocheck.models import CommandSpec, Ecosystem
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


if __name__ == "__main__":
    unittest.main()
