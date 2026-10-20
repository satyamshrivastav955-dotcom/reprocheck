"""Docker pip phases must share installed dependencies without sharing host state."""

import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from reprocheck.verification import execute_plan, plan_verification
from reprocheck.verification import executor as executor_module
from reprocheck.verification.executor import VerificationStatus, _ProcessOutcome


class PipDockerEnvironmentTests(unittest.TestCase):
    def _plan(self, root: Path):
        (root / "pyproject.toml").write_text(
            "[project]\nname='fixture'\nversion='0.1'\ndependencies=[]\n", encoding="utf-8"
        )
        (root / "requirements-test.txt").write_text("pytest==8.4.2\n", encoding="utf-8")
        (root / "tests").mkdir()
        (root / "tests/test_fixture.py").write_text("def test_fixture(): pass\n", encoding="utf-8")
        return plan_verification(root, profile="python-pip-editable")

    def test_docker_phases_share_workspace_packages_and_record_trusted_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = self._plan(Path(directory))
            original_argv = tuple(command.argv for command in plan.commands)
            host = {
                "PATH": "/untrusted-host/bin",
                "HOME": "/untrusted-home",
                "PYTHONUSERBASE": "/untrusted-python",
                "PIP_USER": "0",
                "REPROCHECK_TEST_SECRET": "fixture-private-value",
            }
            with (
                patch.dict(os.environ, host),
                patch.object(executor_module.shutil, "which", return_value="docker"),
                patch.object(
                    executor_module,
                    "_environment_fingerprint",
                    wraps=executor_module._environment_fingerprint,
                ) as fingerprint,
                patch.object(
                    executor_module,
                    "_run_argv",
                    return_value=_ProcessOutcome(0, "", "", False),
                ) as run,
            ):
                result = execute_plan(plan, approved=True, backend="docker")
            self.assertEqual(result.status, VerificationStatus.PASS)
            self.assertEqual(run.call_count, 2)
            mounts = []
            for index, call in enumerate(run.call_args_list):
                argv = call.args[0]
                environment = dict(
                    argv[position + 1].split("=", 1)
                    for position, token in enumerate(argv)
                    if token == "--env"
                )
                self.assertEqual(environment["PYTHONUSERBASE"], "/workspace/.reprocheck-python")
                self.assertEqual(environment["PIP_USER"], "1")
                self.assertTrue(
                    environment["PATH"].startswith("/workspace/.reprocheck-python/bin:")
                )
                self.assertNotIn("HOME", environment)
                self.assertNotIn("REPROCHECK_TEST_SECRET", environment)
                self.assertNotIn("/untrusted-host", environment["PATH"])
                self.assertEqual(dict(result.commands[index].command.env), environment)
                self.assertEqual(tuple(argv[-len(original_argv[index]) :]), original_argv[index])
                self.assertEqual(
                    argv[argv.index("--network") + 1], "bridge" if index == 0 else "none"
                )
                mounts.append(argv[argv.index("--mount") + 1])
            self.assertEqual(mounts[0], mounts[1])
            self.assertIn("dst=/workspace", mounts[0])
            recorded = fingerprint.call_args.args[0]
            self.assertEqual(recorded["PYTHONUSERBASE"], "/workspace/.reprocheck-python")
            self.assertEqual(recorded["PIP_USER"], "1")
            self.assertTrue(any("user-site support" in item for item in result.limitations))

    def test_pip_controls_do_not_leak_into_host_uv_node_or_custom_profiles(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = self._plan(Path(directory))
            with (
                patch.object(executor_module.shutil, "which", return_value="docker"),
                patch.object(
                    executor_module,
                    "_run_argv",
                    return_value=_ProcessOutcome(0, "", "", False),
                ),
            ):
                results = [execute_plan(plan, approved=True, backend="host", allow_host_exec=True)]
                results.extend(
                    execute_plan(replace(plan, profile=profile), approved=True, backend="docker")
                    for profile in ("python-uv", "node-npm", "custom")
                )
            for result in results:
                with self.subTest(profile=result.profile, backend=result.environment.tool_versions):
                    self.assertEqual(result.status, VerificationStatus.PASS)
                    for command in result.commands:
                        self.assertNotIn("PIP_USER", command.command.env)
                        self.assertNotIn("PYTHONUSERBASE", command.command.env)
                        self.assertNotIn(".reprocheck-python", command.command.env.get("PATH", ""))

    def test_pip_environment_does_not_bypass_approval_or_host_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = self._plan(Path(directory))
            with patch.object(executor_module, "_run_argv") as run:
                denied = execute_plan(plan, approved=False, backend="docker")
                blocked = execute_plan(plan, approved=True, backend="host")
            self.assertEqual(denied.status, VerificationStatus.DENIED)
            self.assertEqual(blocked.status, VerificationStatus.BLOCKED)
            run.assert_not_called()
