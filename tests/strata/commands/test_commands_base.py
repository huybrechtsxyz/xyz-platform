#!/usr/bin/env python3
"""Unit tests for BaseCommand's audit-mutating-operation classification (ADR-0066)."""

from unittest.mock import MagicMock, patch

import pytest

from strata.commands.base_command import BaseCommand
from strata.logger import get_audit_log_source, get_logger, shutdown_audit
from strata.models.audit_config_model import AuditConfigModel, AuditJournalModel, AuditPolicyModel


def _op(name: str) -> bool:
    class _Cmd(BaseCommand):
        OPERATION = name

    return _Cmd._is_audit_mutating_operation()


class TestIsAuditMutatingOperation:
    def test_list_suffix_is_read_only(self):
        assert _op("workitem_list") is False
        assert _op("secret_list") is False
        assert _op("deploy_list") is False

    def test_show_suffix_is_read_only(self):
        assert _op("workitem_show") is False
        assert _op("deploy_show") is False

    def test_status_suffix_is_read_only(self):
        assert _op("tools_status") is False
        assert _op("deploy_status") is False
        assert _op("cache_status") is False

    def test_schema_prefix_is_read_only(self):
        assert _op("schema_get") is False
        assert _op("schema_list") is False
        assert _op("schema_export") is False

    def test_mutating_operations_are_audited(self):
        assert _op("deploy_run") is True
        assert _op("build_run") is True
        assert _op("solution_repo_add") is True
        assert _op("secret_put") is True
        assert _op("workitem_approve") is True
        assert _op("new") is True

    def test_history_and_get_operations_are_still_audited(self):
        """Not in ADR-0066's explicit read-only list — left audited deliberately."""
        assert _op("deploy_history") is True
        assert _op("secret_get") is True


def _make_command(tmp_path):
    cmd = BaseCommand(work_path=str(tmp_path))
    cmd.logger = get_logger("test.commands.base")
    return cmd


class TestApplyAuditJournalConfig:
    """ADR-0066 step 6: Phase 1 reconfiguration of the journal from spec.audit.journal."""

    @pytest.fixture(autouse=True)
    def _neutralize_pytest_noop_guard(self, monkeypatch):
        """`configure_audit_log()` no-ops under pytest (see logger/audit.py); this
        class exercises it directly, so neutralise the guard the same way
        `tests/strata/logger/test_audit.py` does — patch the read side, since
        deleting PYTEST_CURRENT_TEST doesn't stick (pytest re-sets it after fixture
        setup, at the start of the "call" phase).
        """
        import os as os_module

        real_get = os_module.environ.get

        def _fake_get(key, default=None):
            if key == "PYTEST_CURRENT_TEST":
                return default
            return real_get(key, default)

        monkeypatch.setattr(os_module.environ, "get", _fake_get)

    def setup_method(self):
        shutdown_audit()

    def teardown_method(self):
        shutdown_audit()

    def _config_service_with(self, journal):
        mock_service = MagicMock()
        mock_service.model.spec.audit = AuditConfigModel(journal=journal) if journal else None
        return mock_service

    def test_noop_when_journal_not_declared(self, tmp_path):
        cmd = _make_command(tmp_path)
        with patch(
            "strata.services.configuration_service.ConfigurationService.get_instance",
            return_value=self._config_service_with(None),
        ):
            cmd._apply_audit_journal_config()
        assert get_audit_log_source() is None

    def test_reconfigures_from_journal_path(self, tmp_path):
        cmd = _make_command(tmp_path)
        journal = AuditJournalModel(path="custom/audit.log")
        with patch(
            "strata.services.configuration_service.ConfigurationService.get_instance",
            return_value=self._config_service_with(journal),
        ):
            cmd._apply_audit_journal_config()
        assert get_audit_log_source() == "spec_audit"

    def test_skips_when_logging_yaml_already_configured(self, tmp_path):
        from strata.logger import configure_audit_log

        cmd = _make_command(tmp_path)
        configure_audit_log(log_path=str(tmp_path / "existing.log"), source="logging_yaml")
        journal = AuditJournalModel(path="custom/audit.log")
        with patch(
            "strata.services.configuration_service.ConfigurationService.get_instance",
            return_value=self._config_service_with(journal),
        ):
            cmd._apply_audit_journal_config()
        # logging.yaml outranks spec.audit.journal — source must remain unchanged
        assert get_audit_log_source() == "logging_yaml"

    def test_resolution_failure_is_non_fatal(self, tmp_path):
        cmd = _make_command(tmp_path)
        with patch(
            "strata.services.configuration_service.ConfigurationService.get_instance",
            side_effect=RuntimeError("boom"),
        ):
            # Should not raise
            cmd._apply_audit_journal_config()


class TestForwardCommandExecutedAuditEvent:
    """ADR-0066 gap 3: command.executed now routes through AuditController.forward()."""

    def setup_method(self):
        shutdown_audit()

    def teardown_method(self):
        shutdown_audit()

    def _config_service_with_audit(self, audit_config):
        mock_service = MagicMock()
        mock_service.model.spec.audit = audit_config
        return mock_service

    def test_noop_by_default_gate_disabled(self, tmp_path):
        """command.executed defaults to disabled — forward() should return before
        building an envelope or touching resolve_actor()."""
        cmd = _make_command(tmp_path)
        with (
            patch(
                "strata.services.configuration_service.ConfigurationService.get_instance",
                return_value=self._config_service_with_audit(None),
            ),
            patch("strata.controllers.actor_controller.resolve_actor") as mock_resolve_actor,
        ):
            cmd._forward_command_executed_audit_event(success=True, duration_seconds=1.5)

        mock_resolve_actor.assert_not_called()

    def test_forwards_when_gate_enabled(self, tmp_path):
        cmd = _make_command(tmp_path)
        audit_config = AuditConfigModel(policy=AuditPolicyModel(events={"command.executed": True}))
        with (
            patch(
                "strata.services.configuration_service.ConfigurationService.get_instance",
                return_value=self._config_service_with_audit(audit_config),
            ),
            patch("strata.controllers.actor_controller.resolve_actor", return_value="test-user"),
            patch("strata.controllers.audit_controller.AuditController._resolve_sinks", return_value=[]),
            patch("strata.logger.audit") as mock_journal,
        ):
            cmd._forward_command_executed_audit_event(success=True, duration_seconds=1.5)

        mock_journal.assert_called_once()
        args, kwargs = mock_journal.call_args
        assert args[0] == "command.executed"
        envelope = kwargs["detail"]
        assert envelope["type"] == "xyz.huybrechts.strata.command.executed"
        # self.OPERATION travels inside the payload, not as the event type
        assert envelope["data"]["strata"]["operation"] == cmd.OPERATION
        assert envelope["data"]["strata"]["success"] is True

    def test_exception_in_forward_does_not_raise(self, tmp_path):
        """_finalize() has no surrounding try/except at the execute() call site —
        this method itself must never let an exception escape."""
        cmd = _make_command(tmp_path)
        with patch("strata.controllers.audit_controller.AuditController.forward", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError):
                # The method itself doesn't swallow — _finalize()'s own try/except does.
                cmd._forward_command_executed_audit_event(success=True, duration_seconds=1.5)

    def test_finalize_swallows_forward_exception(self, tmp_path, capsys):
        """The actual call site in _finalize() wraps this in try/except (non-fatal)."""
        cmd = _make_command(tmp_path)
        cmd.OPERATION = "deploy_run"  # a mutating operation
        with patch.object(cmd, "_forward_command_executed_audit_event", side_effect=RuntimeError("boom")):
            # Should not raise — _finalize() itself must complete successfully.
            cmd._finalize(success=True, show_footer=False)

    def test_config_resolution_failure_falls_back_to_defaults(self, tmp_path):
        """A ConfigurationService failure must not prevent forward() from running with
        its own default (all-disabled) AuditConfigModel."""
        cmd = _make_command(tmp_path)
        with (
            patch(
                "strata.services.configuration_service.ConfigurationService.get_instance",
                side_effect=RuntimeError("boom"),
            ),
            patch("strata.controllers.actor_controller.resolve_actor") as mock_resolve_actor,
        ):
            # Should not raise; gate stays disabled by default so resolve_actor is never reached.
            cmd._forward_command_executed_audit_event(success=True, duration_seconds=1.5)

        mock_resolve_actor.assert_not_called()


class TestForwardPolicyViolationAuditEvent:
    """ADR-0066 follow-up: policy.violated forwarding, shared by validate/build/deploy/check_policy."""

    def _make_result(self, **overrides):
        from strata.validators.policies.base_policy import PolicyResult

        defaults = {
            "passed": False,
            "policy_name": "no-public-ip",
            "enforcement": "deny",
            "policy_type": "naming",
            "violations": ["resource X exposes a public IP"],
        }
        defaults.update(overrides)
        return PolicyResult(**defaults)

    def test_calls_audit_controller_with_execution_id(self, tmp_path):
        cmd = _make_command(tmp_path)
        result = self._make_result()

        with patch("strata.controllers.audit_controller.AuditController.forward_policy_violation") as mock_fwd:
            cmd._forward_policy_violation_audit_event(result)

        mock_fwd.assert_called_once()
        args, kwargs = mock_fwd.call_args
        assert args[0] is result
        assert kwargs["execution_id"] == cmd._execution_id

    def test_includes_deployment_name_when_deployment_service_present(self, tmp_path):
        cmd = _make_command(tmp_path)
        result = self._make_result()

        deployment_service = MagicMock()
        deployment_service.model.meta.name = "haven-prd"
        cmd._deployment_service = deployment_service

        with patch("strata.controllers.audit_controller.AuditController.forward_policy_violation") as mock_fwd:
            cmd._forward_policy_violation_audit_event(result)

        assert mock_fwd.call_args.kwargs["deployment"] == "haven-prd"

    def test_deployment_is_none_without_deployment_service(self, tmp_path):
        cmd = _make_command(tmp_path)
        result = self._make_result()

        with patch("strata.controllers.audit_controller.AuditController.forward_policy_violation") as mock_fwd:
            cmd._forward_policy_violation_audit_event(result)

        assert mock_fwd.call_args.kwargs["deployment"] is None

    def test_never_raises_on_failure(self, tmp_path):
        cmd = _make_command(tmp_path)
        result = self._make_result()

        with patch(
            "strata.controllers.audit_controller.AuditController.forward_policy_violation",
            side_effect=RuntimeError("boom"),
        ):
            # Should not raise
            cmd._forward_policy_violation_audit_event(result)


class TestLoadDeploymentService:
    """Shared spec.extends resolution + Phase-1 load (ADR 0039), used by both
    BaseBuildCommand and BaseDeployCommand so a file that passes
    `strata validate --deep` (which resolves extends) is buildable/deployable too."""

    def _write(self, path, spec, meta=None):
        import yaml

        doc = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "deployment",
            "meta": {"name": "test", **(meta or {})},
            "spec": spec,
        }
        path.write_text(yaml.dump(doc, default_flow_style=False), encoding="utf-8")
        return path

    def test_loads_plain_deployment_without_extends(self, tmp_path):
        f = self._write(tmp_path / "leaf.yaml", {"workspace": {"name": "ws", "file": "workspace.yaml"}})
        cmd = _make_command(tmp_path)

        result = cmd._load_deployment_service_with_extends(f, {})

        assert result is not None
        assert cmd._errors == []

    def test_resolves_extends_before_loading(self, tmp_path):
        self._write(
            tmp_path / "base.yaml",
            {
                "partial": True,
                "workspace": {"name": "ws", "file": "workspace.yaml"},
                "stages": [{"name": "core", "provisioner": "core"}],
            },
        )
        child = self._write(
            tmp_path / "child.yaml",
            {"extends": "base.yaml", "environments": [{"file": "env.yaml"}]},
        )
        cmd = _make_command(tmp_path)

        result = cmd._load_deployment_service_with_extends(child, {})

        assert result is not None
        assert cmd._errors == []
        assert result.model is not None
        assert result.model.spec.workspace is not None
        assert result.model.spec.workspace.name == "ws"
        assert result.model.spec.partial is not True

    def test_reports_error_on_broken_extends_reference(self, tmp_path):
        child = self._write(tmp_path / "child.yaml", {"extends": "missing-base.yaml"})
        cmd = _make_command(tmp_path)

        result = cmd._load_deployment_service_with_extends(child, {})

        assert result is None
        assert len(cmd._errors) == 1
        assert "extends" in cmd._errors[0].lower()

    def test_reports_pydantic_validation_errors(self, tmp_path):
        f = tmp_path / "bad.yaml"
        f.write_text(
            "apiVersion: strata.huybrechts.xyz/v1\nkind: deployment\nmeta:\n  name: x\nspec:\n  unknown_field: true\n",
            encoding="utf-8",
        )
        cmd = _make_command(tmp_path)

        result = cmd._load_deployment_service_with_extends(f, {})

        assert result is None
        assert cmd._errors
