"""Tests for ChangeReferenceRequiredPolicy (ADR-0074 Phase 2).

Evaluated at the 'deploy_before' phase — a plain existence check against
PolicyContext.change_reference, no plan/deployer/stage context needed.
"""

from strata.models.policy_model import PolicyModel
from strata.validators.policies.base_policy import PolicyContext
from strata.validators.policies.change_reference_required_policy import ChangeReferenceRequiredPolicy


def make_policy(enforcement: str = "deny") -> PolicyModel:
    return PolicyModel.model_validate(
        {
            "name": "production-change-record",
            "type": "change_reference_required",
            "phase": "deploy_before",
            "enforcement": enforcement,
        }
    )


class TestChangeReferenceRequiredPolicy:
    def test_passes_when_change_reference_present(self) -> None:
        policy = ChangeReferenceRequiredPolicy(make_policy())
        context = PolicyContext(phase="deploy_before", work_path=None, change_reference=object())
        result = policy.evaluate(context)
        assert result.passed is True
        assert result.violations == []

    def test_fails_when_change_reference_missing(self) -> None:
        policy = ChangeReferenceRequiredPolicy(make_policy())
        context = PolicyContext(phase="deploy_before", work_path=None, change_reference=None)
        result = policy.evaluate(context)
        assert result.passed is False
        assert len(result.violations) == 1
        assert "change" in result.violations[0].lower()

    def test_enforcement_level_is_passed_through(self) -> None:
        policy = ChangeReferenceRequiredPolicy(make_policy(enforcement="warn"))
        context = PolicyContext(phase="deploy_before", work_path=None, change_reference=None)
        result = policy.evaluate(context)
        assert result.enforcement == "warn"
        assert result.passed is False
