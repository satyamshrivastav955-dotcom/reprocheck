"""Static Dockerfile and compose presence/syntax heuristics."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from ...models import (
    Assessment,
    CheckCategory,
    CheckSpec,
    Ecosystem,
    Finding,
    ResultState,
    Severity,
    VerificationClass,
)
from ..base import (
    BaseCheck,
    CheckContext,
    blocked_finding,
    make_finding,
    parse_error_finding,
    read_known_text,
    static_evidence,
    unsupported_finding,
)

CONTAINER_SPEC = CheckSpec(
    check_id="RC-CON-001",
    name="Container definition presence and syntax",
    category=CheckCategory.CONTAINER,
    verification_class=VerificationClass.STATIC,
    description="Detect Dockerfiles or compose files and apply bounded static syntax heuristics.",
    severity=Severity.MEDIUM,
    supported_ecosystems=(Ecosystem.CONTAINER,),
    limitations=(
        "Dockerfile instructions and compose YAML are not fully parsed.",
        "No image build or compose command is executed.",
    ),
    false_positive_policy=(
        "Report only recognized Dockerfile/compose names and conservative structural checks."
    ),
    scoring_group="container",
)

_DOCKER_INSTRUCTIONS = {
    "ADD",
    "ARG",
    "CMD",
    "COPY",
    "ENTRYPOINT",
    "ENV",
    "EXPOSE",
    "FROM",
    "HEALTHCHECK",
    "LABEL",
    "MAINTAINER",
    "ONBUILD",
    "RUN",
    "SHELL",
    "STOPSIGNAL",
    "USER",
    "VOLUME",
    "WORKDIR",
}


def _container_paths(context: CheckContext) -> tuple[str, ...]:
    paths: list[str] = []
    for repo_file in context.discovery.files:
        if repo_file.is_symlink:
            continue
        name = PurePosixPath(repo_file.path).name.lower()
        if (
            name == "dockerfile"
            or name.startswith("dockerfile.")
            or (
                name.startswith(("docker-compose", "compose.")) and name.endswith((".yml", ".yaml"))
            )
        ):
            paths.append(repo_file.path)
    return tuple(sorted(paths))


def _validate_dockerfile(text: str) -> tuple[bool, str | None]:
    if not text.strip():
        return False, "Dockerfile is empty"
    if "\x00" in text:
        return False, "Dockerfile contains NUL bytes"
    found_from = False
    continuation = False
    for number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if continuation:
            continuation = line.endswith("\\")
            continue
        match = re.match(r"([A-Za-z]+)(?:\s+|$)", line)
        if match is None or match.group(1).upper() not in _DOCKER_INSTRUCTIONS:
            return False, f"unknown Dockerfile instruction at line {number}"
        instruction = match.group(1).upper()
        found_from = found_from or instruction == "FROM"
        continuation = line.endswith("\\")
    return (True, None) if found_from else (False, "Dockerfile has no FROM instruction")


def _validate_compose(text: str) -> tuple[bool, str | None]:
    if not text.strip():
        return False, "compose file is empty"
    if "\x00" in text or any(line.startswith("\t") for line in text.splitlines()):
        return False, "compose file contains unsupported NUL or tab indentation"
    if not re.search(r"(?m)^\s*services\s*:", text):
        return False, "compose file has no services key"
    service_lines = [
        line for line in text.splitlines() if re.match(r"^\s{2,}[A-Za-z0-9_.-]+\s*:\s*$", line)
    ]
    return (
        (True, None)
        if service_lines
        else (False, "compose services has no statically recognizable service")
    )


class ContainerDefinitionCheck(BaseCheck):
    spec = CONTAINER_SPEC

    def evaluate(self, context: CheckContext) -> tuple[Finding, ...]:
        if Ecosystem.CONTAINER not in context.discovery.ecosystems:
            return (
                unsupported_finding(
                    self.spec,
                    title="Container definition is not applicable",
                    message="No container ecosystem marker was discovered.",
                    reason="RC-CON-001 applies when Dockerfile or compose markers are discovered.",
                ),
            )
        paths = _container_paths(context)
        if not paths:
            return (
                make_finding(
                    self.spec,
                    title="Container definition is missing",
                    message="No Dockerfile or compose file was discovered.",
                    assessment=Assessment.FAIL,
                    state=ResultState.STATIC_FAIL,
                    evidence=(
                        static_evidence(
                            self.spec, "No recognized container definition was discovered."
                        ),
                    ),
                    remediation=(
                        "Add a Dockerfile or compose definition if container reproducibility "
                        "is required."
                    ),
                ),
            )
        evidence = []
        for path in paths:
            result = read_known_text(context, path)
            if result.status != "ok":
                if result.status == "inaccessible":
                    return (
                        blocked_finding(
                            self.spec,
                            title="Container definition is inaccessible",
                            message=f"Container definition {path} could not be read.",
                            reason=result.error or "container definition is inaccessible",
                            path=path,
                        ),
                    )
                return (
                    parse_error_finding(
                        self.spec,
                        title="Container definition cannot be assessed",
                        message=f"Container definition {path} could not be read safely.",
                        reason=result.error or result.status,
                        path=path,
                    ),
                )
            name = PurePosixPath(path).name.lower()
            valid, error = (
                _validate_dockerfile(result.text)
                if name == "dockerfile" or name.startswith("dockerfile.")
                else _validate_compose(result.text)
            )
            if not valid:
                return (
                    parse_error_finding(
                        self.spec,
                        title="Container definition is malformed",
                        message=f"Container definition {path} failed static syntax heuristics.",
                        reason=error or "unknown container syntax",
                        path=path,
                    ),
                )
            evidence.append(
                static_evidence(
                    self.spec, "Container definition passed bounded syntax heuristics.", source=path
                )
            )
        return (
            make_finding(
                self.spec,
                title="Container definition detected",
                message=(
                    "Dockerfile or compose definitions are present and pass bounded "
                    "syntax heuristics."
                ),
                assessment=Assessment.PASS,
                state=ResultState.LOOKS_CORRECT,
                evidence=tuple(evidence),
                ecosystems=(),
            ),
        )


__all__ = ["CONTAINER_SPEC", "ContainerDefinitionCheck"]
