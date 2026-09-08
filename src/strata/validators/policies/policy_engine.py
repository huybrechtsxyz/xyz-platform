#!/usr/bin/env python3
"""Policy engine — instantiates and evaluates policies for a given phase."""

from typing import Dict, List, Type

from strata.logger import get_logger
from strata.models.policy_model import PolicyModel
from strata.validators.policies.base_policy import BasePolicy, PolicyContext, PolicyResult


class PolicyEngine:
    """Evaluates all enabled policies for a given lifecycle phase.

    Usage::

        engine = PolicyEngine(policy_models)
        results = engine.evaluate("plan", context)
        if engine.has_denials(results):
            # block the pipeline

    Custom policy types can be registered via :meth:`register_type` — this is
    used by auto-discovered ``.strata/policies/*.py`` files.
    """

    # Class-level registry for custom policy types registered by user plugins.
    _custom_types: Dict[str, Type[BasePolicy]] = {}

    def __init__(self, policies: List[PolicyModel]) -> None:
        self.logger = get_logger(__name__)
        self._policies: List[BasePolicy] = [self._create(p) for p in policies if p.enabled]

    def evaluate(self, phase: str, context: PolicyContext) -> List[PolicyResult]:
        """Run all policies whose ``phase`` matches and return their results."""
        results: List[PolicyResult] = []
        for policy in self._policies:
            if policy.phase != phase:
                continue
            result = policy.evaluate(context)
            result.policy_type = policy.policy.type
            self.logger.debug(
                "policy_evaluated",
                policy=policy.name,
                phase=phase,
                passed=result.passed,
                enforcement=policy.enforcement,
                violations=result.violations,
            )
            results.append(result)
        return results

    def has_denials(self, results: List[PolicyResult]) -> bool:
        """Return True if any deny-enforcement policy failed."""
        return any(not r.passed and r.enforcement == "deny" for r in results)

    @classmethod
    def register_type(cls, type_name: str, policy_class: Type[BasePolicy]) -> None:
        """Register a custom policy type for plugin discovery.

        Called by ``.strata/policies/*.py`` files in their ``register()`` function.
        """
        cls._custom_types[type_name] = policy_class

    def _create(self, policy_model: PolicyModel) -> BasePolicy:
        """Dispatch policy type to its concrete implementation."""
        from strata.validators.policies.ai_review_policy import AiReviewPolicy
        from strata.validators.policies.change_reference_required_policy import ChangeReferenceRequiredPolicy
        from strata.validators.policies.checkov_policy import CheckovPolicy
        from strata.validators.policies.cost_threshold_policy import CostThresholdPolicy
        from strata.validators.policies.cve_max_severity_policy import CveMaxSeverityPolicy
        from strata.validators.policies.layer_agreement_policy import LayerAgreementPolicy
        from strata.validators.policies.naming_policy import NamingPolicy
        from strata.validators.policies.opa_policy import OPAPolicy
        from strata.validators.policies.path_convention_policy import PathConventionPolicy
        from strata.validators.policies.ref_convention_policy import RefConventionPolicy
        from strata.validators.policies.required_labels_policy import RequiredLabelsPolicy
        from strata.validators.policies.resource_type_restrictions_policy import ResourceTypeRestrictionsPolicy
        from strata.validators.policies.sbom_allowed_registries_policy import SbomAllowedRegistriesPolicy
        from strata.validators.policies.sbom_denied_packages_policy import SbomDeniedPackagesPolicy
        from strata.validators.policies.sbom_license_policy import SbomLicensePolicy
        from strata.validators.policies.sbom_max_components_policy import SbomMaxComponentsPolicy
        from strata.validators.policies.sbom_pinned_versions_policy import SbomPinnedVersionsPolicy
        from strata.validators.policies.script_policy import ScriptPolicy
        from strata.validators.policies.tenant_zone_policy import TenantZonePolicy

        _builtin = {
            "tenant_zone": TenantZonePolicy,
            "required_labels": RequiredLabelsPolicy,
            "naming_pattern": NamingPolicy,
            "ref_convention": RefConventionPolicy,
            "resource_type_restrictions": ResourceTypeRestrictionsPolicy,
            "script": ScriptPolicy,
            "sbom_pinned_versions": SbomPinnedVersionsPolicy,
            "sbom_allowed_registries": SbomAllowedRegistriesPolicy,
            "sbom_denied_packages": SbomDeniedPackagesPolicy,
            "sbom_max_components": SbomMaxComponentsPolicy,
            "sbom_license": SbomLicensePolicy,
            "cve_max_severity": CveMaxSeverityPolicy,
            "cost_threshold": CostThresholdPolicy,
            "checkov": CheckovPolicy,
            "opa": OPAPolicy,
            "path_convention": PathConventionPolicy,
            "layer_agreement": LayerAgreementPolicy,
            "ai_review": AiReviewPolicy,
            "change_reference_required": ChangeReferenceRequiredPolicy,
        }

        policy_class = _builtin.get(policy_model.type) or self._custom_types.get(policy_model.type)
        if policy_class is not None:
            return policy_class(policy_model)  # type: ignore[abstract]

        available = sorted(set(list(_builtin.keys()) + list(self._custom_types.keys())))
        raise ValueError(f"Unknown policy type: '{policy_model.type}'. Available types: {', '.join(available)}")
