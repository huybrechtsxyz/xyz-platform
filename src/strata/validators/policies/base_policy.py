#!/usr/bin/env python3
"""Base abstractions for the strata policy engine.

Defines the ``PolicyContext`` data container, the ``PolicyResult`` output
structure, and the ``BasePolicy`` ABC that all concrete policies implement.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from strata.models.policy_model import PolicyModel


@dataclass
class PolicyContext:
    """Data available to a policy during evaluation.

    Different phases populate different fields — policies should check for
    ``None`` before accessing phase-specific data.
    """

    phase: str
    work_path: Optional[Path]
    deployment_service: Optional[Any] = None  # DeploymentService — Any avoids circular imports
    configuration_service: Optional[Any] = None  # ConfigurationService
    platform_artifact: Optional[Any] = None  # PlatformArtifactModel
    plan_data: Optional[Dict[str, Any]] = None  # terraform show -json output
    build_path: Optional[Path] = None
    sbom_components: Optional[List[Any]] = None  # List[SbomComponentModel]
    cve_audit_result: Optional[Any] = None  # CveAuditResultModel — populated when --audit ran before policies
    cost_data: Optional[Dict[str, Any]] = None  # cost.json contents — populated when cost.json exists in build/
    file_path: Optional[Path] = None  # the file currently being validated (path_convention policy)
    change_reference: Optional[Any] = None  # ChangeReferenceModel — populated for phase 'deploy_before' (ADR-0074)


@dataclass
class PolicyResult:
    """Outcome of a single policy evaluation."""

    passed: bool
    policy_name: str
    enforcement: str  # deny | warn | audit
    policy_type: str = ""
    violations: List[str] = field(default_factory=list)
    details: Optional[Dict[str, Any]] = None


class BasePolicy(ABC):
    """Abstract base for all strata policies.

    Concrete policies receive their ``PolicyModel`` at construction time and
    implement ``evaluate()`` to inspect the ``PolicyContext`` and return a
    ``PolicyResult``.
    """

    def __init__(self, policy_model: PolicyModel) -> None:
        self.policy = policy_model

    @abstractmethod
    def evaluate(self, context: PolicyContext) -> PolicyResult:
        """Evaluate the policy against the given context."""
        raise NotImplementedError

    @property
    def phase(self) -> str:
        return self.policy.phase

    @property
    def enforcement(self) -> str:
        return self.policy.enforcement

    @property
    def name(self) -> str:
        return str(self.policy.name)
