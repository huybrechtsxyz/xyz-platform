"""Tests for BaseDeployCommand._evaluate_preflight_policies (ADR-0074 Phase 2).

Covers the shared one-shot policy evaluator used by both RunDeployCommand
('deploy_before' phase) and DestroyDeployCommand ('destroy_before' phase) —
evaluated once per invocation, before any stage executes, unlike per-stage
plan/deploy phase policies. One implementation, exercised generically via
BaseDeployCommand directly, plus a thin per-command smoke test confirming
each command wires its own phase name and skips evaluation on --dry-run.
"""

from pathlib import Path
from unittest.mock import MagicMock

from strata.commands.deploy.base_deploy_command import BaseDeployCommand
from strata.commands.deploy.destroy_deploy_command import DestroyDeployCommand
from strata.commands.deploy.run_deploy_command import RunDeployCommand
from strata.models.change_reference_model import ChangeReferenceModel
from strata.models.policy_model import PolicyModel


def _make_policy(phase: str, enforcement: str = "deny", enabled: bool = True) -> PolicyModel:
    return PolicyModel.model_validate(
        {
            "name": "production-change-record",
            "type": "change_reference_required",
            "phase": phase,
            "enforcement": enforcement,
            "enabled": enabled,
        }
    )


def _make_cmd(tmp_path: Path, policies=None, no_config_service: bool = False) -> BaseDeployCommand:
    cmd = BaseDeployCommand(work_path=str(tmp_path))
    if no_config_service:
        cmd._configuration_service = None
        return cmd
    config_service = MagicMock()
    config_service.model.spec.policies = policies or []
    cmd._configuration_service = config_service
    return cmd


class TestEvaluatePreflightPoliciesNoOp:
    def test_no_configuration_service_passes(self, tmp_path: Path) -> None:
        cmd = _make_cmd(tmp_path, no_config_service=True)
        assert cmd._evaluate_preflight_policies("deploy_before") is True

    def test_no_matching_phase_policies_declared_passes(self, tmp_path: Path) -> None:
        cmd = _make_cmd(tmp_path, policies=[])
        assert cmd._evaluate_preflight_policies("deploy_before") is True

    def test_disabled_policy_is_skipped(self, tmp_path: Path) -> None:
        cmd = _make_cmd(tmp_path, policies=[_make_policy("deploy_before", enabled=False)])
        cmd._change_reference = None
        assert cmd._evaluate_preflight_policies("deploy_before") is True

    def test_policy_declared_for_a_different_phase_is_ignored(self, tmp_path: Path) -> None:
        """A 'destroy_before' policy must not fire when evaluating 'deploy_before', and vice versa."""
        cmd = _make_cmd(tmp_path, policies=[_make_policy("destroy_before", enforcement="deny")])
        cmd._change_reference = None
        assert cmd._evaluate_preflight_policies("deploy_before") is True


class TestEvaluatePreflightPoliciesEnforcement:
    def test_deny_blocks_when_no_change_reference_supplied(self, tmp_path: Path) -> None:
        cmd = _make_cmd(tmp_path, policies=[_make_policy("deploy_before", enforcement="deny")])
        cmd._change_reference = None
        assert cmd._evaluate_preflight_policies("deploy_before") is False
        assert any("production-change-record" in e for e in cmd._errors)

    def test_deny_passes_when_change_reference_supplied(self, tmp_path: Path) -> None:
        cmd = _make_cmd(tmp_path, policies=[_make_policy("deploy_before", enforcement="deny")])
        cmd._change_reference = ChangeReferenceModel(
            system="jira",
            id="OPS-1234",
            reason="Restore checkout capacity",
            supplied_by="alice@example.com",
            supplied_at="2026-09-08T10:00:00+00:00",
        )
        assert cmd._evaluate_preflight_policies("deploy_before") is True
        assert cmd._errors == []

    def test_warn_does_not_block_when_no_change_reference_supplied(self, tmp_path: Path) -> None:
        cmd = _make_cmd(tmp_path, policies=[_make_policy("deploy_before", enforcement="warn")])
        cmd._change_reference = None
        assert cmd._evaluate_preflight_policies("deploy_before") is True
        assert cmd._errors == []

    def test_policy_result_recorded_on_manifest(self, tmp_path: Path) -> None:
        cmd = _make_cmd(tmp_path, policies=[_make_policy("deploy_before", enforcement="deny")])
        cmd._change_reference = None
        cmd._evaluate_preflight_policies("deploy_before")
        assert len(cmd._policy_results) == 1
        assert cmd._policy_results[0].policy_type == "change_reference_required"
        assert cmd._policy_results[0].phase == "deploy_before"
        assert cmd._policy_results[0].passed is False

    def test_destroy_before_phase_evaluated_independently_of_deploy_before(self, tmp_path: Path) -> None:
        """A workspace can deny on destroy_before while having no deploy_before policy at all."""
        cmd = _make_cmd(tmp_path, policies=[_make_policy("destroy_before", enforcement="deny")])
        cmd._change_reference = None
        assert cmd._evaluate_preflight_policies("deploy_before") is True  # nothing declared for this phase
        assert cmd._evaluate_preflight_policies("destroy_before") is False  # denied


class TestRunDeployCommandCallSite:
    """Smoke tests confirming RunDeployCommand wires 'deploy_before' and skips it on --dry-run."""

    def test_dry_run_never_evaluates_preflight_policies(self, tmp_path: Path) -> None:
        cmd = RunDeployCommand(work_path=str(tmp_path))
        cmd._dry_run = True
        cmd._change_reference = None
        cmd._stage = None
        cmd._scope = None
        cmd._namespaces = None
        config_service = MagicMock()
        config_service.model.spec.policies = [_make_policy("deploy_before", enforcement="deny")]
        cmd._configuration_service = config_service

        mock_deployment_service = MagicMock()
        mock_deployment_service.model.spec.stages = []
        cmd._deployment_service = mock_deployment_service

        called = {"count": 0}

        def _spy(phase: str):
            called["count"] += 1
            return False  # would deny if it were ever called

        cmd._evaluate_preflight_policies = _spy  # type: ignore[method-assign]

        result = cmd._execute_provisioning()

        assert called["count"] == 0
        assert result is True  # no stages defined -> "nothing to deploy"

    def test_non_dry_run_evaluates_deploy_before_phase(self, tmp_path: Path) -> None:
        cmd = RunDeployCommand(work_path=str(tmp_path))
        cmd._dry_run = False
        cmd._change_reference = None
        cmd._stage = None
        cmd._scope = None
        cmd._namespaces = None
        config_service = MagicMock()
        config_service.model.spec.policies = []
        cmd._configuration_service = config_service

        mock_deployment_service = MagicMock()
        mock_stage = MagicMock()
        mock_stage.name = "infrastructure"
        mock_stage.scope = None
        mock_deployment_service.model.spec.stages = [mock_stage]
        mock_deployment_service.get_namespace_services.return_value = {}
        cmd._deployment_service = mock_deployment_service

        seen_phases = []

        def _spy(phase: str):
            seen_phases.append(phase)
            return False  # short-circuits _execute_provisioning right after this call

        cmd._evaluate_preflight_policies = _spy  # type: ignore[method-assign]

        result = cmd._execute_provisioning()

        assert seen_phases == ["deploy_before"]
        assert result is False


class TestDestroyDeployCommandCallSite:
    """Smoke tests confirming DestroyDeployCommand wires 'destroy_before' and skips it on --dry-run."""

    def test_dry_run_never_evaluates_preflight_policies(self, tmp_path: Path) -> None:
        cmd = DestroyDeployCommand(work_path=str(tmp_path))
        cmd._dry_run = True
        cmd._change_reference = None
        cmd._stage = None
        cmd._scope = None
        config_service = MagicMock()
        config_service.model.spec.policies = [_make_policy("destroy_before", enforcement="deny")]
        cmd._configuration_service = config_service

        mock_deployment_service = MagicMock()
        mock_deployment_service.model.spec.stages = []
        cmd._deployment_service = mock_deployment_service

        called = {"count": 0}

        def _spy(phase: str):
            called["count"] += 1
            return False

        cmd._evaluate_preflight_policies = _spy  # type: ignore[method-assign]

        result = cmd._execute_provisioning()

        assert called["count"] == 0
        assert result is True  # no stages defined -> "nothing to destroy"

    def test_non_dry_run_evaluates_destroy_before_phase(self, tmp_path: Path) -> None:
        cmd = DestroyDeployCommand(work_path=str(tmp_path))
        cmd._dry_run = False
        cmd._change_reference = None
        cmd._stage = None
        cmd._scope = None
        config_service = MagicMock()
        config_service.model.spec.policies = []
        cmd._configuration_service = config_service

        mock_deployment_service = MagicMock()
        mock_stage = MagicMock()
        mock_stage.name = "infrastructure"
        mock_stage.scope = None
        mock_deployment_service.model.spec.stages = [mock_stage]
        cmd._deployment_service = mock_deployment_service

        seen_phases = []

        def _spy(phase: str):
            seen_phases.append(phase)
            return False  # short-circuits _execute_provisioning right after this call

        cmd._evaluate_preflight_policies = _spy  # type: ignore[method-assign]

        result = cmd._execute_provisioning()

        assert seen_phases == ["destroy_before"]
        assert result is False

    def test_deny_blocks_destroy_before_any_stage_runs(self, tmp_path: Path) -> None:
        """A deny on 'destroy_before' must abort before the stage loop, with no stages executed."""
        cmd = DestroyDeployCommand(work_path=str(tmp_path))
        cmd._dry_run = False
        cmd._change_reference = None
        cmd._stage = None
        cmd._scope = None
        config_service = MagicMock()
        config_service.model.spec.policies = [_make_policy("destroy_before", enforcement="deny")]
        cmd._configuration_service = config_service

        mock_deployment_service = MagicMock()
        # Non-empty stages — if the deny didn't block early, the mock stage loop
        # machinery below would need far more setup and would fail loudly.
        mock_stage = MagicMock()
        mock_stage.name = "infrastructure"
        mock_stage.scope = None
        mock_deployment_service.model.spec.stages = [mock_stage]
        cmd._deployment_service = mock_deployment_service

        result = cmd._execute_provisioning()

        assert result is False
        assert any("production-change-record" in e for e in cmd._errors)
