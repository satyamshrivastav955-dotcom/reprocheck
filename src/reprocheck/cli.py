"""Read-only command-line interface for ReproCheck.

Exit codes are stable API:

* ``0`` — no policy finding meets ``--fail-on``;
* ``1`` — a policy finding meets ``--fail-on`` or verification does not pass;
* ``2`` — usage or input/path error;
* ``3`` — unexpected internal error;
* ``130`` — interrupted by the user.

The default policy threshold is ``high``.  ``scan`` performs only bounded
filesystem reads through the existing discovery, fingerprint, and static
check APIs.  It does not execute repository-controlled commands and does not
create verification runs or evidence receipts.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path
from typing import Final, NoReturn, Protocol, Sequence, cast

from .checks import CheckContext, default_registry
from .command_parser import plan_repository_commands
from .discovery import build_repository_identity, discover_repository
from .fingerprint import fingerprint_repository
from .models import Assessment, Finding, JSONValue, Report, ResultState, Severity
from .receipts import (
    ReceiptFormatError,
    build_receipt,
    compare_receipts,
    load_receipt,
    validate_receipt,
)
from .reporting import render_json, render_markdown, render_terminal, sorted_findings
from .scoring import ScoreSummary, score_report
from .verification import (
    ExperimentStatus,
    ProfileError,
    VerificationPlan,
    execute_plan,
    plan_verification,
    run_experiment,
)
from .welcome import render_welcome

VERSION: Final[str] = "0.1.0"
DEFAULT_FAIL_ON: Final[Severity] = Severity.HIGH

_SEVERITY_ORDER: Final[dict[Severity, int]] = {
    Severity.INFO: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}
_POLICY_ASSESSMENTS: Final[frozenset[Assessment]] = frozenset(
    {Assessment.FAIL, Assessment.WARN, Assessment.ERROR, Assessment.BLOCKED}
)


class _UsageError(Exception):
    """Internal argparse signal converted into the documented exit code."""


class _ParserExit(Exception):
    def __init__(self, status: int) -> None:
        super().__init__()
        self.status = status


class _ReconfigurableStream(Protocol):
    def reconfigure(self, *, encoding: str) -> object:
        """Set the stream encoding when the runtime supports it."""


def _ensure_utf8_stdout() -> None:
    """Keep structured stdout UTF-8 on native Windows and redirected streams."""

    stream = cast(object, sys.stdout)
    if not hasattr(stream, "reconfigure"):
        return
    try:
        cast(_ReconfigurableStream, stream).reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        return


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise _UsageError(message)

    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:
        if message:
            self._print_message(message, sys.stderr if status else sys.stdout)
        raise _ParserExit(status)


def _argument_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(prog="reprocheck", description="Read-only reproducibility scans.")
    parser.add_argument("--version", action="version", version=f"reprocheck {VERSION}")
    subparsers = parser.add_subparsers(dest="command")
    scan = subparsers.add_parser("scan", help="inspect a repository without executing it")
    scan.add_argument("path", metavar="PATH", help="repository directory to inspect")
    scan.add_argument(
        "--format",
        choices=("terminal", "json", "markdown"),
        default="terminal",
        help="report format (default: terminal)",
    )
    scan.add_argument("--output", metavar="FILE", help="write the report to FILE")
    scan.add_argument(
        "--fail-on",
        choices=tuple(severity.value for severity in Severity),
        default=DEFAULT_FAIL_ON.value,
        help="exit 1 for policy findings at or above this severity (default: high)",
    )
    verify = subparsers.add_parser(
        "verify", help="plan and optionally execute approved clean-room commands"
    )
    verify.add_argument("path", metavar="PATH", help="repository directory to verify")
    verify.add_argument("--profile", help="verification profile, such as python-uv or node-npm")
    verify.add_argument(
        "--backend",
        choices=("docker", "host"),
        default="docker",
        help="execution backend (default: docker; host requires --allow-host-exec)",
    )
    verify.add_argument(
        "--approve",
        action="store_true",
        help="explicitly approve execution of the displayed command plan",
    )
    verify.add_argument(
        "--allow-host-exec",
        action="store_true",
        help="explicitly accept residual host-execution risk when --backend host is selected",
    )
    verify.add_argument("--receipt", metavar="FILE", help="write the evidence receipt to FILE")
    verify.add_argument("--output", metavar="FILE", help="write the verification report to FILE")
    verify.add_argument(
        "--format",
        choices=("terminal", "json", "markdown"),
        default="terminal",
        help="report format (default: terminal)",
    )
    verify.add_argument(
        "--fail-on",
        choices=tuple(severity.value for severity in Severity),
        default=DEFAULT_FAIL_ON.value,
        help="exit 1 for policy findings at or above this severity (default: high)",
    )
    experiment = subparsers.add_parser(
        "experiment", help="run the approved verification profile twice and compare evidence"
    )
    experiment.add_argument("path", metavar="PATH", help="repository directory to experiment")
    experiment.add_argument("--profile", help="verification profile, such as python-uv")
    experiment.add_argument(
        "--backend",
        choices=("docker", "host"),
        default="docker",
        help="execution backend (default: docker; host requires --allow-host-exec)",
    )
    experiment.add_argument(
        "--allow-host-exec",
        action="store_true",
        help="explicitly accept residual host-execution risk when --backend host is selected",
    )
    experiment.add_argument(
        "--artifact",
        action="append",
        default=[],
        metavar="PATH",
        help="workspace-relative artifact file or directory to compare; may be repeated",
    )
    experiment.add_argument(
        "--approve",
        action="store_true",
        help="explicitly approve both independent experiment runs",
    )
    experiment.add_argument(
        "--receipt", metavar="FILE", help="write the experiment receipt to FILE"
    )
    experiment.add_argument("--output", metavar="FILE", help="write the experiment report to FILE")
    experiment.add_argument(
        "--format",
        choices=("terminal", "json", "markdown"),
        default="terminal",
        help="report format (default: terminal)",
    )
    experiment.add_argument(
        "--fail-on",
        choices=tuple(severity.value for severity in Severity),
        default=DEFAULT_FAIL_ON.value,
        help="exit 1 for policy findings at or above this severity (default: high)",
    )
    receipts = subparsers.add_parser("receipt", help="validate or compare evidence receipts")
    receipt_commands = receipts.add_subparsers(dest="receipt_command")
    validate = receipt_commands.add_parser("validate", help="validate a receipt hash")
    validate.add_argument("path", metavar="FILE")
    validate.add_argument("--repository", metavar="PATH", help="also compare repository content")
    compare = receipt_commands.add_parser("compare", help="compare two receipts")
    compare.add_argument("first", metavar="FIRST")
    compare.add_argument("second", metavar="SECOND")
    return parser


def _aggregate_state(findings: Sequence[Finding]) -> ResultState:
    states = {finding.state for finding in findings}
    if ResultState.STATIC_FAIL in states:
        return ResultState.STATIC_FAIL
    if ResultState.NOT_VERIFIED in states:
        return ResultState.NOT_VERIFIED
    if ResultState.PRESENT in states:
        return ResultState.PRESENT
    if ResultState.LOOKS_CORRECT in states:
        return ResultState.LOOKS_CORRECT
    return ResultState.NOT_VERIFIED


def _aggregate_assessment(findings: Sequence[Finding]) -> Assessment:
    assessments = {finding.assessment for finding in findings}
    for assessment in (
        Assessment.ERROR,
        Assessment.BLOCKED,
        Assessment.FAIL,
        Assessment.WARN,
        Assessment.UNKNOWN,
        Assessment.UNSUPPORTED,
        Assessment.NOT_APPLICABLE,
    ):
        if assessment in assessments:
            return assessment
    if Assessment.PASS in assessments or Assessment.INFO in assessments:
        return Assessment.PASS
    return Assessment.UNKNOWN


def _score_summary(report: Report) -> ScoreSummary:
    return score_report(report)


def scan_repository(repository: Path) -> Report:
    """Build one deterministic static report for ``repository``."""

    root = Path(repository)
    identity = build_repository_identity(root)
    discovery = discover_repository(root)
    fingerprint = fingerprint_repository(root)
    context = CheckContext(repository_root=Path(identity.root), discovery=discovery)
    registry = default_registry()
    findings = sorted_findings(registry.evaluate(context))
    command_plan = plan_repository_commands(Path(identity.root), discovery=discovery)

    report = Report(
        repository=identity,
        findings=findings,
        state=_aggregate_state(findings),
        assessment=_aggregate_assessment(findings),
        checks=registry.specs,
        discovery=discovery,
        verification_runs=(),
        evidence_receipt=None,
        generated_at=None,
        summary={
            "scan_phase": "static",
            "content_fingerprint": fingerprint.to_dict(),
            "command_plan": command_plan.to_dict(),
        },
    )
    score = _score_summary(report)
    return replace(report, summary={**report.summary, "score_summary": score.to_dict()})


def build_report(repository: Path) -> Report:
    """Named alias for :func:`scan_repository`."""

    return scan_repository(repository)


def _render(report: Report, output_format: str) -> str:
    score = _score_summary(report)
    if output_format == "json":
        return render_json(report, score)
    if output_format == "markdown":
        return render_markdown(report, score)
    return render_terminal(report, score)


def policy_findings(report: Report, fail_on: Severity) -> tuple[Finding, ...]:
    """Return actionable findings at or above the selected policy threshold."""

    threshold = _SEVERITY_ORDER[fail_on]
    return tuple(
        finding
        for finding in sorted_findings(report.findings)
        if finding.assessment in _POLICY_ASSESSMENTS
        and _SEVERITY_ORDER[finding.severity] >= threshold
    )


def _write_or_print(rendered: str, output: str | None) -> None:
    if output is None:
        _ensure_utf8_stdout()
        sys.stdout.write(rendered)
        return
    Path(output).write_text(rendered, encoding="utf-8", newline="\n")


def _show_plan(plan: VerificationPlan) -> None:
    """Display every proposed command before an execution decision."""

    print("REPROCHECK VERIFICATION PLAN", file=sys.stderr)
    print("approval: required; no commands run without --approve", file=sys.stderr)
    for item in plan.commands:
        argv = " ".join(item.argv)
        print(
            f"- phase={item.phase.value} network={str(item.network_required).lower()} argv={argv}",
            file=sys.stderr,
        )
    if not plan.commands:
        print("- no commands were planned", file=sys.stderr)
    for limitation in plan.limitations:
        print(f"limitation: {limitation}", file=sys.stderr)


def _show_experiment_plan(plan: VerificationPlan, artifact_paths: tuple[str, ...]) -> None:
    """Display the shared plan and two-run experiment policy before approval."""

    print("REPROCHECK EXPERIMENT PLAN", file=sys.stderr)
    print("runs: 2 independent workspaces; approval: required", file=sys.stderr)
    _show_plan(plan)
    if artifact_paths:
        for path in artifact_paths:
            print(f"- artifact={path}", file=sys.stderr)
    else:
        print(
            "limitation: no artifacts selected; a reproducibility comparison cannot complete",
            file=sys.stderr,
        )


def verify_repository(
    repository: Path,
    *,
    profile: str | None,
    backend: str,
    approved: bool,
    allow_host_exec: bool,
    receipt_path: str | None,
) -> Report:
    """Build a static report, show a plan, and execute only with explicit consent."""

    static_report = scan_repository(repository)
    plan = plan_verification(repository, profile=profile)
    _show_plan(plan)
    result = execute_plan(
        plan,
        approved=approved,
        allow_host_exec=allow_host_exec,
        backend=backend,
    )
    receipt = build_receipt(
        repository,
        plan=plan,
        result=result,
        findings=static_report.findings,
    )
    if receipt_path is not None:
        Path(receipt_path).write_text(
            receipt.to_json(indent=2) + "\n", encoding="utf-8", newline="\n"
        )
    if result.status.value == "pass":
        state = ResultState.VERIFIED_PASS
        assessment = Assessment.PASS
    elif result.status.value == "fail":
        state = ResultState.VERIFIED_FAIL
        assessment = Assessment.FAIL
    elif result.status.value in {"blocked", "denied", "timeout"}:
        state = ResultState.NOT_VERIFIED
        assessment = Assessment.BLOCKED
    else:
        state = ResultState.NOT_VERIFIED
        assessment = Assessment.ERROR
    summary: dict[str, JSONValue] = dict(static_report.summary)
    summary.update(
        {
            "verification_profile": plan.profile,
            "verification_status": result.status.value,
            "verification_limitations": cast(JSONValue, list(result.limitations)),
            "receipt_id": receipt.receipt_id,
        }
    )
    final_report = replace(
        static_report,
        state=state,
        assessment=assessment,
        verification_runs=(result.run,),
        evidence_receipt=receipt,
        summary=summary,
    )
    return replace(
        final_report,
        summary={**final_report.summary, "score_summary": score_report(final_report).to_dict()},
    )


def experiment_repository(
    repository: Path,
    *,
    profile: str | None,
    backend: str,
    approved: bool,
    allow_host_exec: bool,
    artifact_paths: tuple[str, ...],
    receipt_path: str | None,
) -> Report:
    """Build a static report and run the selected verification plan twice."""

    static_report = scan_repository(repository)
    preview_plan = plan_verification(repository, profile=profile)
    _show_experiment_plan(preview_plan, artifact_paths)
    experiment = run_experiment(
        repository,
        profile=profile,
        backend=backend,
        approved=approved,
        artifact_paths=artifact_paths,
        allow_host_exec=allow_host_exec,
    )
    execution_limitations = tuple(
        limitation for result in experiment.results for limitation in result.limitations
    )
    receipt = build_receipt(
        repository,
        plan=experiment.plan,
        verification_runs=experiment.runs,
        evidence=experiment.evidence,
        findings=static_report.findings,
        limitations=(*experiment.limitations, *execution_limitations),
        content_fingerprint=experiment.source_fingerprint,
        experiment=experiment.to_receipt_details(),
    )
    if receipt_path is not None:
        Path(receipt_path).write_text(
            receipt.to_json(indent=2) + "\n", encoding="utf-8", newline="\n"
        )

    if experiment.classification == ExperimentStatus.REPRODUCIBLE:
        state = ResultState.VERIFIED_PASS
        assessment = Assessment.PASS
        verification_status = "pass"
    elif experiment.classification in {
        ExperimentStatus.DRIFT_DETECTED,
        ExperimentStatus.PASS_FAIL_DISAGREEMENT,
        ExperimentStatus.BOTH_FAILED,
    }:
        state = ResultState.VERIFIED_FAIL
        assessment = Assessment.FAIL
        verification_status = "fail"
    elif experiment.classification == ExperimentStatus.INFRASTRUCTURE_FAILURE:
        state = ResultState.NOT_VERIFIED
        assessment = Assessment.BLOCKED
        verification_status = "not_verified"
    else:
        state = ResultState.NOT_VERIFIED
        assessment = Assessment.ERROR
        verification_status = "not_verified"

    summary: dict[str, JSONValue] = dict(static_report.summary)
    summary.update(
        {
            "verification_profile": experiment.profile,
            "verification_status": verification_status,
            "experiment_status": experiment.classification,
            "experiment_mode": "python-two-run",
            "experiment_backend": experiment.backend,
            "experiment_run_count": len(experiment.results),
            "experiment_source_fingerprint": experiment.source_fingerprint.to_dict(),
            "experiment_plan_fingerprint": experiment.plan_fingerprint,
            "experiment_comparison": experiment.comparison,
            "experiment_limitations": list(experiment.limitations),
            "receipt_id": receipt.receipt_id,
        }
    )
    final_report = replace(
        static_report,
        state=state,
        assessment=assessment,
        verification_runs=experiment.runs,
        evidence_receipt=receipt,
        summary=summary,
    )
    return replace(
        final_report,
        summary={**final_report.summary, "score_summary": score_report(final_report).to_dict()},
    )


def _receipt_command(arguments: argparse.Namespace) -> int:
    try:
        if arguments.receipt_command == "validate":
            receipt = load_receipt(Path(arguments.path))
            validation = validate_receipt(
                receipt,
                repository=Path(arguments.repository) if arguments.repository else None,
            )
            if validation.valid:
                print("valid")
                return 0
            print("invalid")
            for error in validation.errors:
                print(f"- {error}", file=sys.stderr)
            return 1
        if arguments.receipt_command == "compare":
            comparison = compare_receipts(
                load_receipt(Path(arguments.first)), load_receipt(Path(arguments.second))
            )
            print("equal" if comparison.equal else "changed")
            for category in comparison.changed:
                print(f"- {category}")
            return 1 if comparison.has_changes else 0
    except (ReceiptFormatError, OSError, ValueError) as error:
        print(f"reprocheck: receipt error: {error}", file=sys.stderr)
        return 2
    print("reprocheck: error: receipt validate or compare is required", file=sys.stderr)
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return one of the documented stable exit codes."""

    parser = _argument_parser()
    try:
        arguments = parser.parse_args(argv)
    except _ParserExit as signal:
        return signal.status
    except _UsageError as error:
        parser.print_usage(sys.stderr)
        print(f"reprocheck: error: {error}", file=sys.stderr)
        return 2

    if arguments.command is None:
        sys.stdout.write(render_welcome(VERSION, stream=sys.stdout))
        return 0

    if arguments.command == "receipt":
        return _receipt_command(arguments)
    if arguments.command not in {"scan", "verify", "experiment"}:
        parser.print_usage(sys.stderr)
        print("reprocheck: error: a command is required", file=sys.stderr)
        return 2

    try:
        if arguments.command == "verify":
            report = verify_repository(
                Path(arguments.path),
                profile=arguments.profile,
                backend=arguments.backend,
                approved=arguments.approve,
                allow_host_exec=arguments.allow_host_exec,
                receipt_path=arguments.receipt,
            )
        elif arguments.command == "experiment":
            report = experiment_repository(
                Path(arguments.path),
                profile=arguments.profile,
                backend=arguments.backend,
                approved=arguments.approve,
                allow_host_exec=arguments.allow_host_exec,
                artifact_paths=tuple(arguments.artifact),
                receipt_path=arguments.receipt,
            )
        else:
            report = scan_repository(Path(arguments.path))
        rendered = _render(report, arguments.format)
        _write_or_print(rendered, arguments.output)
        if (
            arguments.command in {"verify", "experiment"}
            and report.summary.get("verification_status") != "pass"
        ):
            return 1
        fail_on = Severity(arguments.fail_on)
        return 1 if policy_findings(report, fail_on) else 0
    except KeyboardInterrupt:
        return 130
    except (
        FileNotFoundError,
        NotADirectoryError,
        PermissionError,
        ProfileError,
        ValueError,
        OSError,
    ) as error:
        print(f"reprocheck: input error: {error}", file=sys.stderr)
        return 2
    except Exception as error:  # pragma: no cover - exercised by integration failures
        print(f"reprocheck: internal error: {error}", file=sys.stderr)
        return 3


__all__ = [
    "DEFAULT_FAIL_ON",
    "VERSION",
    "build_report",
    "main",
    "policy_findings",
    "scan_repository",
    "verify_repository",
]
