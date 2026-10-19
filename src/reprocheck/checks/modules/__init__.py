"""Built-in static check implementations."""

from .ci import CI_SPEC, CiWorkflowCheck
from .commands import (
    COMMAND_SPEC,
    CONSISTENCY_SPEC,
    CommandConsistencyCheck,
    CommandDiscoverabilityCheck,
)
from .container import CONTAINER_SPEC, ContainerDefinitionCheck
from .dependencies import (
    LOCK_SPEC,
    MANIFEST_SPEC,
    LockfilePresenceCheck,
    RecognizedDependencyManifestCheck,
)
from .discovery import DISCOVERY_SPEC, EcosystemApplicabilityCheck
from .environment import ENV_SPEC, EnvironmentDocumentationCheck
from .git import GIT_SPEC, GitignoreHygieneCheck
from .package import PACKAGE_SPEC, PackageConsistencyCheck
from .runtime import RUNTIME_SPEC, RuntimeDeclarationCheck
from .security import PATH_SPEC, SECURITY_SPEC, LikelySecretCheck, SuspiciousPathCheck

BUILTIN_CHECKS = (
    EcosystemApplicabilityCheck(),
    RecognizedDependencyManifestCheck(),
    LockfilePresenceCheck(),
    RuntimeDeclarationCheck(),
    CommandDiscoverabilityCheck(),
    CommandConsistencyCheck(),
    EnvironmentDocumentationCheck(),
    CiWorkflowCheck(),
    ContainerDefinitionCheck(),
    SuspiciousPathCheck(),
    LikelySecretCheck(),
    GitignoreHygieneCheck(),
    PackageConsistencyCheck(),
)

__all__ = [
    "BUILTIN_CHECKS",
    "CI_SPEC",
    "COMMAND_SPEC",
    "CONSISTENCY_SPEC",
    "CONTAINER_SPEC",
    "DISCOVERY_SPEC",
    "ENV_SPEC",
    "GIT_SPEC",
    "LOCK_SPEC",
    "MANIFEST_SPEC",
    "PACKAGE_SPEC",
    "PATH_SPEC",
    "RUNTIME_SPEC",
    "SECURITY_SPEC",
    "CiWorkflowCheck",
    "CommandConsistencyCheck",
    "CommandDiscoverabilityCheck",
    "ContainerDefinitionCheck",
    "EcosystemApplicabilityCheck",
    "EnvironmentDocumentationCheck",
    "GitignoreHygieneCheck",
    "LikelySecretCheck",
    "LockfilePresenceCheck",
    "PackageConsistencyCheck",
    "RecognizedDependencyManifestCheck",
    "RuntimeDeclarationCheck",
    "SuspiciousPathCheck",
]
