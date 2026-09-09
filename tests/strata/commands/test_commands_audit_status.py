"""Tests for `strata audit status` (ADR-0066 step 6)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from strata.commands.audit.status_audit_command import StatusAuditCommand
from strata.logger import get_logger
from strata.models.audit_config_model import AuditConfigModel, AuditJournalModel, AuditPolicyModel, AuditSinkModel


def _make_command(tmp_path) -> StatusAuditCommand:
    cmd = StatusAuditCommand(work_path=str(tmp_path))
    cmd.logger = get_logger("test.commands.audit.status")
    return cmd


def _mock_config_service(audit_config=None, integrations=None):
    """*integrations* is a list of (name, type) tuples — real type strings so
    IntegrationFactory.is_known_type() checks behave as they would in production.
    """
    mock_service = MagicMock()
    mock_service.model.spec.audit = audit_config
    declared = []
    for name, type_str in integrations or []:
        integration = MagicMock()
        integration.name = name
        integration.type = type_str
        declared.append(integration)
    mock_service.model.spec.integrations = declared
    return mock_service


class TestJournalResolution:
    def test_defaults_to_bootstrap_when_nothing_declared(self, tmp_path):
        cmd = _make_command(tmp_path)
        with patch(
            "strata.services.configuration_service.ConfigurationService.load",
            return_value=_mock_config_service(None),
        ):
            assert cmd._execute() is True
        assert cmd._journal["source"] == "bootstrap"
        assert cmd._journal["path"].endswith("audit.log")

    def test_spec_audit_journal_is_reported(self, tmp_path):
        cmd = _make_command(tmp_path)
        audit_config = AuditConfigModel(journal=AuditJournalModel(path="custom/audit.log", rotation="daily"))
        with patch(
            "strata.services.configuration_service.ConfigurationService.load",
            return_value=_mock_config_service(audit_config),
        ):
            assert cmd._execute() is True
        assert cmd._journal["source"] == "spec_audit"
        assert cmd._journal["path"].replace("\\", "/").endswith("custom/audit.log")
        assert cmd._journal["rotation"] == "daily"

    def test_logging_yaml_audit_section_outranks_spec_audit(self, tmp_path):
        strata_dir = tmp_path / ".strata"
        strata_dir.mkdir()
        (strata_dir / "logging.yaml").write_text("audit:\n  path: machine-local.log\n")

        cmd = _make_command(tmp_path)
        audit_config = AuditConfigModel(journal=AuditJournalModel(path="custom/audit.log"))
        with patch(
            "strata.services.configuration_service.ConfigurationService.load",
            return_value=_mock_config_service(audit_config),
        ):
            assert cmd._execute() is True
        assert cmd._journal["source"] == "logging_yaml"
        assert "note" in cmd._journal


class TestPolicyGateReporting:
    def test_reports_all_closed_set_event_types(self, tmp_path):
        cmd = _make_command(tmp_path)
        with patch(
            "strata.services.configuration_service.ConfigurationService.load",
            return_value=_mock_config_service(None),
        ):
            cmd._execute()
        assert cmd._gate["deployment.completed"] is True
        assert cmd._gate["command.executed"] is False

    def test_reflects_policy_overrides(self, tmp_path):
        cmd = _make_command(tmp_path)
        audit_config = AuditConfigModel(policy=AuditPolicyModel(events={"command.executed": True}))
        with patch(
            "strata.services.configuration_service.ConfigurationService.load",
            return_value=_mock_config_service(audit_config),
        ):
            cmd._execute()
        assert cmd._gate["command.executed"] is True


class TestSinkReporting:
    def test_reports_declared_sinks(self, tmp_path):
        cmd = _make_command(tmp_path)
        audit_config = AuditConfigModel(sinks=[AuditSinkModel(name="s1", integration="my-webhook")])
        with patch(
            "strata.services.configuration_service.ConfigurationService.load",
            return_value=_mock_config_service(audit_config, integrations=[("my-webhook", "webhook")]),
        ):
            cmd._execute()
        assert cmd._sinks == [
            {
                "name": "s1",
                "integration": "my-webhook",
                "integration_type": "webhook",
                "enabled": True,
                "events": None,
                "integration_declared": True,
            }
        ]

    def test_flags_sink_with_missing_integration(self, tmp_path):
        cmd = _make_command(tmp_path)
        audit_config = AuditConfigModel(sinks=[AuditSinkModel(name="s1", integration="ghost")])
        with patch(
            "strata.services.configuration_service.ConfigurationService.load",
            return_value=_mock_config_service(audit_config, integrations=[]),
        ):
            cmd._execute()
        assert cmd._sinks[0]["integration_declared"] is False
        assert cmd._sinks[0]["integration_type"] is None

    def test_flags_sink_whose_integration_has_a_nonexistent_type(self, tmp_path):
        """Regression test: the sink's `integration:` name matched a real
        spec.integrations[] entry, but that entry's `type` doesn't map to any
        registered integration class (IntegrationModel.type is a free-form string,
        not an enum, so a typo like this passes Pydantic validation) — this used to
        be silently reported as integration_declared: true."""
        cmd = _make_command(tmp_path)
        audit_config = AuditConfigModel(sinks=[AuditSinkModel(name="s1", integration="my-sink")])
        with patch(
            "strata.services.configuration_service.ConfigurationService.load",
            return_value=_mock_config_service(audit_config, integrations=[("my-sink", "totally_bogus_type")]),
        ):
            cmd._execute()
        assert cmd._sinks[0]["integration_declared"] is False
        assert cmd._sinks[0]["integration_type"] == "totally_bogus_type"

    def test_no_sinks_configured(self, tmp_path):
        cmd = _make_command(tmp_path)
        with patch(
            "strata.services.configuration_service.ConfigurationService.load",
            return_value=_mock_config_service(None),
        ):
            cmd._execute()
        assert cmd._sinks == []


class TestExecuteResilience:
    def test_configuration_load_failure_is_non_fatal(self, tmp_path):
        cmd = _make_command(tmp_path)
        with patch(
            "strata.services.configuration_service.ConfigurationService.load",
            side_effect=RuntimeError("boom"),
        ):
            assert cmd._execute() is True
        assert cmd._journal["source"] == "bootstrap"


class TestCliWiring:
    def test_status_command_runs_via_cli(self, tmp_path):
        from click.testing import CliRunner

        from strata.commands.cli_audit import audit_group

        runner = CliRunner()
        result = runner.invoke(audit_group, ["status", "--work-path", str(tmp_path), "--output", "json"])
        assert result.exit_code == 0
        assert '"journal"' in result.output
        assert '"policy"' in result.output
