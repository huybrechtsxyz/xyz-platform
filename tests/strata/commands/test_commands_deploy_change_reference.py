"""Tests for BaseDeployCommand._resolve_change_reference (ADR-0074 Phase 1).

Covers CLI/env-supplied --change-id/--change-system/--change-title/--change-url/
--change-classification/--reason combined with configuration.spec.change_tracking
defaults. Phase 1 is capture-only: nothing is required by default, so the common
"nothing supplied" case must remain a no-op.
"""

from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock

from strata.commands.deploy.run_deploy_command import RunDeployCommand
from strata.models.change_reference_model import ChangeReferenceModel
from strata.models.configuration_model import ChangeTrackingConfigModel


def _make_cmd(
    tmp_path: Path,
    change_id: Optional[str] = None,
    change_system: Optional[str] = None,
    change_title: Optional[str] = None,
    change_url: Optional[str] = None,
    change_classification: Optional[str] = None,
    change_reason: Optional[str] = None,
    change_tracking: Optional[ChangeTrackingConfigModel] = None,
) -> RunDeployCommand:
    cmd = RunDeployCommand(
        work_path=str(tmp_path),
        change_id=change_id,
        change_system=change_system,
        change_title=change_title,
        change_url=change_url,
        change_classification=change_classification,
        change_reason=change_reason,
    )
    if change_tracking is not None:
        config_service = MagicMock()
        config_service.model.spec.change_tracking = change_tracking
        cmd._configuration_service = config_service
    return cmd


class TestResolveChangeReferenceNoInput:
    def test_nothing_supplied_is_a_noop(self, tmp_path: Path) -> None:
        cmd = _make_cmd(tmp_path)
        error = cmd._resolve_change_reference()
        assert error is None
        assert cmd._change_reference is None


class TestResolveChangeReferenceUsageErrors:
    def test_reason_without_change_id_is_an_error(self, tmp_path: Path) -> None:
        cmd = _make_cmd(tmp_path, change_reason="Emergency fix")
        error = cmd._resolve_change_reference()
        assert error is not None
        assert "--change-id" in error
        assert cmd._change_reference is None

    def test_change_id_without_reason_is_an_error(self, tmp_path: Path) -> None:
        cmd = _make_cmd(tmp_path, change_id="OPS-1234")
        error = cmd._resolve_change_reference()
        assert error is not None
        assert "--reason" in error
        assert cmd._change_reference is None

    def test_change_id_without_system_and_no_default_is_an_error(self, tmp_path: Path) -> None:
        cmd = _make_cmd(tmp_path, change_id="OPS-1234", change_reason="Emergency fix")
        error = cmd._resolve_change_reference()
        assert error is not None
        assert "--change-system" in error
        assert cmd._change_reference is None

    def test_change_id_not_matching_configured_pattern_is_an_error(self, tmp_path: Path) -> None:
        change_tracking = ChangeTrackingConfigModel(system="jira", id_pattern=r"^[A-Z][A-Z0-9]+-[0-9]+$")
        cmd = _make_cmd(
            tmp_path,
            change_id="not-a-valid-id",
            change_reason="Emergency fix",
            change_tracking=change_tracking,
        )
        error = cmd._resolve_change_reference()
        assert error is not None
        assert "id_pattern" in error
        assert cmd._change_reference is None


class TestResolveChangeReferenceSuccess:
    def test_minimal_valid_input_builds_change_reference(self, tmp_path: Path) -> None:
        cmd = _make_cmd(
            tmp_path,
            change_id="OPS-1234",
            change_system="jira",
            change_reason="Restore checkout capacity",
        )
        error = cmd._resolve_change_reference()
        assert error is None
        assert isinstance(cmd._change_reference, ChangeReferenceModel)
        ref = cmd._change_reference
        assert ref.system == "jira"
        assert ref.id == "OPS-1234"
        assert ref.reason == "Restore checkout capacity"
        assert ref.title is None
        assert ref.url is None
        assert ref.supplied_by
        assert ref.supplied_at

    def test_system_defaults_from_configuration(self, tmp_path: Path) -> None:
        change_tracking = ChangeTrackingConfigModel(system="jira")
        cmd = _make_cmd(
            tmp_path,
            change_id="OPS-1234",
            change_reason="Restore checkout capacity",
            change_tracking=change_tracking,
        )
        error = cmd._resolve_change_reference()
        assert error is None
        assert cmd._change_reference is not None
        assert cmd._change_reference.system == "jira"

    def test_explicit_change_system_overrides_configured_default(self, tmp_path: Path) -> None:
        change_tracking = ChangeTrackingConfigModel(system="jira")
        cmd = _make_cmd(
            tmp_path,
            change_id="ADO-42",
            change_system="azure_devops",
            change_reason="Restore checkout capacity",
            change_tracking=change_tracking,
        )
        error = cmd._resolve_change_reference()
        assert error is None
        assert cmd._change_reference is not None
        assert cmd._change_reference.system == "azure_devops"

    def test_url_resolved_from_configured_template(self, tmp_path: Path) -> None:
        change_tracking = ChangeTrackingConfigModel(
            system="jira",
            url_template="https://jira.example.com/browse/{id}",
        )
        cmd = _make_cmd(
            tmp_path,
            change_id="OPS-1234",
            change_reason="Restore checkout capacity",
            change_tracking=change_tracking,
        )
        error = cmd._resolve_change_reference()
        assert error is None
        assert cmd._change_reference is not None
        assert cmd._change_reference.url == "https://jira.example.com/browse/OPS-1234"

    def test_explicit_change_url_overrides_configured_template(self, tmp_path: Path) -> None:
        change_tracking = ChangeTrackingConfigModel(
            system="jira",
            url_template="https://jira.example.com/browse/{id}",
        )
        cmd = _make_cmd(
            tmp_path,
            change_id="OPS-1234",
            change_url="https://custom.example.com/OPS-1234",
            change_reason="Restore checkout capacity",
            change_tracking=change_tracking,
        )
        error = cmd._resolve_change_reference()
        assert error is None
        assert cmd._change_reference is not None
        assert cmd._change_reference.url == "https://custom.example.com/OPS-1234"

    def test_change_id_matching_configured_pattern_succeeds(self, tmp_path: Path) -> None:
        change_tracking = ChangeTrackingConfigModel(system="jira", id_pattern=r"^[A-Z][A-Z0-9]+-[0-9]+$")
        cmd = _make_cmd(
            tmp_path,
            change_id="OPS-1234",
            change_reason="Restore checkout capacity",
            change_tracking=change_tracking,
        )
        error = cmd._resolve_change_reference()
        assert error is None
        assert cmd._change_reference is not None

    def test_change_title_is_captured(self, tmp_path: Path) -> None:
        cmd = _make_cmd(
            tmp_path,
            change_id="OPS-1234",
            change_system="jira",
            change_title="Production connection pool exhaustion",
            change_reason="Restore checkout capacity",
        )
        error = cmd._resolve_change_reference()
        assert error is None
        assert cmd._change_reference is not None
        assert cmd._change_reference.title == "Production connection pool exhaustion"

    def test_change_classification_is_captured(self, tmp_path: Path) -> None:
        cmd = _make_cmd(
            tmp_path,
            change_id="OPS-1234",
            change_system="jira",
            change_classification="emergency",
            change_reason="Restore checkout capacity",
        )
        error = cmd._resolve_change_reference()
        assert error is None
        assert cmd._change_reference is not None
        assert cmd._change_reference.classification == "emergency"

    def test_change_classification_accepted_when_in_configured_allowlist(self, tmp_path: Path) -> None:
        change_tracking = ChangeTrackingConfigModel(
            system="jira",
            classifications=["emergency", "normal", "standard"],
        )
        cmd = _make_cmd(
            tmp_path,
            change_id="OPS-1234",
            change_classification="normal",
            change_reason="Restore checkout capacity",
            change_tracking=change_tracking,
        )
        error = cmd._resolve_change_reference()
        assert error is None
        assert cmd._change_reference is not None
        assert cmd._change_reference.classification == "normal"

    def test_change_classification_optional_even_when_allowlist_configured(self, tmp_path: Path) -> None:
        change_tracking = ChangeTrackingConfigModel(
            system="jira",
            classifications=["emergency", "normal", "standard"],
        )
        cmd = _make_cmd(
            tmp_path,
            change_id="OPS-1234",
            change_reason="Restore checkout capacity",
            change_tracking=change_tracking,
        )
        error = cmd._resolve_change_reference()
        assert error is None
        assert cmd._change_reference is not None
        assert cmd._change_reference.classification is None


class TestResolveChangeReferenceClassificationErrors:
    def test_change_classification_not_in_configured_allowlist_is_an_error(self, tmp_path: Path) -> None:
        change_tracking = ChangeTrackingConfigModel(
            system="jira",
            classifications=["emergency", "normal", "standard"],
        )
        cmd = _make_cmd(
            tmp_path,
            change_id="OPS-1234",
            change_classification="bogus",
            change_reason="Restore checkout capacity",
            change_tracking=change_tracking,
        )
        error = cmd._resolve_change_reference()
        assert error is not None
        assert "classifications" in error
        assert cmd._change_reference is None
