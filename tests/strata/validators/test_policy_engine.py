"""Tests for PolicyEngine — phase routing, enforcement levels, and filtering."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from strata.models.policy_model import PolicyModel
from strata.validators.policies.base_policy import BasePolicy, PolicyContext, PolicyResult
from strata.validators.policies.policy_engine import PolicyEngine

# ---------------------------------------------------------------------------
# Stub policies — bypass _create() for engine routing tests
# ---------------------------------------------------------------------------


class _AlwaysPassPolicy(BasePolicy):
    """Test stub that always returns a passing result."""

    @property
    def name(self) -> str:
        return self.policy.name

    def evaluate(self, context) -> PolicyResult:
        return PolicyResult(
            passed=True,
            policy_name=self.name,
            enforcement=self.enforcement,
            violations=[],
        )


class _AlwaysFailPolicy(BasePolicy):
    """Test stub that always returns a failing result with one violation."""

    @property
    def name(self) -> str:
        return self.policy.name

    def evaluate(self, context) -> PolicyResult:
        return PolicyResult(
            passed=False,
            policy_name=self.name,
            enforcement=self.enforcement,
            violations=["violation: policy check failed"],
        )


class _TestableEngine(PolicyEngine):
    """Engine subclass that accepts pre-built policy instances, bypassing _create().

    This lets tests exercise evaluate() and has_denials() without needing to
    register real policy types in the factory.
    """

    def __init__(self, policy_instances):
        self.logger = MagicMock()
        self._policies = list(policy_instances)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_policy_model(
    name="test_policy",
    type="tenant_zone",
    phase="plan",
    enforcement="deny",
    enabled=True,
) -> "PolicyModel":
    return PolicyModel(
        name=name,
        type=type,
        phase=phase,
        enforcement=enforcement,
        enabled=enabled,
    )


def _make_context(phase="plan") -> "PolicyContext":
    return PolicyContext(
        phase=phase,
        work_path=Path("/tmp"),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPolicyEngine:
    def test_no_policies_returns_empty(self):
        """Engine with no policies produces an empty results list."""
        engine = _TestableEngine([])
        results = engine.evaluate("plan", _make_context())

        assert results == []

    def test_filters_by_phase(self):
        """Only policies matching the requested phase are evaluated."""
        plan_model = _make_policy_model(name="plan_policy", phase="plan")
        build_model = _make_policy_model(name="build_policy", phase="build")
        engine = _TestableEngine(
            [
                _AlwaysPassPolicy(plan_model),
                _AlwaysPassPolicy(build_model),
            ]
        )

        results = engine.evaluate("plan", _make_context("plan"))

        assert len(results) == 1
        assert results[0].policy_name == "plan_policy"

    def test_disabled_policy_skipped(self):
        """Disabled policies are excluded during PolicyEngine.__init__."""
        enabled_model = _make_policy_model(name="enabled_policy", enabled=True)
        disabled_model = _make_policy_model(name="disabled_policy", enabled=False)

        def _create_stub(m):
            return _AlwaysPassPolicy(m)

        with patch.object(PolicyEngine, "_create", side_effect=_create_stub):
            engine = PolicyEngine([enabled_model, disabled_model])

        assert len(engine._policies) == 1
        assert engine._policies[0].policy.name == "enabled_policy"

    def test_has_denials_false_when_all_pass(self):
        """has_denials() returns False when all deny-level policies pass."""
        model = _make_policy_model(enforcement="deny")
        engine = _TestableEngine([_AlwaysPassPolicy(model)])
        results = engine.evaluate("plan", _make_context())

        assert engine.has_denials(results) is False

    def test_has_denials_true_when_deny_fails(self):
        """has_denials() returns True when a deny-level policy fails."""
        model = _make_policy_model(enforcement="deny")
        engine = _TestableEngine([_AlwaysFailPolicy(model)])
        results = engine.evaluate("plan", _make_context())

        assert engine.has_denials(results) is True

    def test_has_denials_false_when_only_warn_fails(self):
        """warn enforcement does not count as a denial — pipeline continues."""
        model = _make_policy_model(enforcement="warn")
        engine = _TestableEngine([_AlwaysFailPolicy(model)])
        results = engine.evaluate("plan", _make_context())

        assert engine.has_denials(results) is False

    def test_has_denials_false_when_only_audit_fails(self):
        """audit enforcement does not count as a denial — result recorded only."""
        model = _make_policy_model(enforcement="audit")
        engine = _TestableEngine([_AlwaysFailPolicy(model)])
        results = engine.evaluate("plan", _make_context())

        assert engine.has_denials(results) is False

    def test_multiple_policies_all_run(self):
        """All matching policies are evaluated even when the first one fails."""
        model_a = _make_policy_model(name="policy_a", phase="plan", enforcement="deny")
        model_b = _make_policy_model(name="policy_b", phase="plan", enforcement="warn")
        engine = _TestableEngine(
            [
                _AlwaysFailPolicy(model_a),
                _AlwaysFailPolicy(model_b),
            ]
        )

        results = engine.evaluate("plan", _make_context())

        assert len(results) == 2
        names = {r.policy_name for r in results}
        assert names == {"policy_a", "policy_b"}

    def test_unknown_type_raises_value_error(self):
        """PolicyEngine._create() raises ValueError for unrecognised policy types."""
        model = _make_policy_model(type="unknown_garbage_xyz")

        with pytest.raises(ValueError):
            PolicyEngine([model])

    def test_change_reference_required_type_resolves(self):
        """PolicyEngine._create() resolves 'change_reference_required' (ADR-0074 Phase 2)."""
        from strata.validators.policies.change_reference_required_policy import ChangeReferenceRequiredPolicy

        model = _make_policy_model(type="change_reference_required", phase="deploy_before")
        engine = PolicyEngine([model])

        assert isinstance(engine._policies[0], ChangeReferenceRequiredPolicy)
