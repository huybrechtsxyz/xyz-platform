"""Tests for ChangeReferenceModel and ChangeTrackingConfigModel (ADR-0074 Phase 1)."""

import pytest
from pydantic import ValidationError

from strata.models.change_reference_model import ChangeReferenceModel
from strata.models.configuration_model import ChangeTrackingConfigModel


class TestChangeReferenceModel:
    def test_minimal_valid_model(self) -> None:
        ref = ChangeReferenceModel(
            system="jira",
            id="OPS-1234",
            reason="Restore checkout capacity",
            supplied_by="alice@example.com",
            supplied_at="2026-09-08T10:00:00+00:00",
        )
        assert ref.system == "jira"
        assert ref.id == "OPS-1234"
        assert ref.title is None
        assert ref.url is None

    def test_missing_required_field_raises(self) -> None:
        with pytest.raises(ValidationError):
            ChangeReferenceModel(system="jira", id="OPS-1234")  # type: ignore[call-arg]

    def test_extra_field_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            ChangeReferenceModel(
                system="jira",
                id="OPS-1234",
                reason="Restore checkout capacity",
                supplied_by="alice@example.com",
                supplied_at="2026-09-08T10:00:00+00:00",
                unknown_field="nope",  # type: ignore[call-arg]
            )

    def test_system_accepts_any_string_not_just_known_providers(self) -> None:
        ref = ChangeReferenceModel(
            system="our_internal_tracker",
            id="INT-1",
            reason="Scheduled maintenance",
            supplied_by="bob@example.com",
            supplied_at="2026-09-08T10:00:00+00:00",
        )
        assert ref.system == "our_internal_tracker"

    def test_classification_is_optional(self) -> None:
        ref = ChangeReferenceModel(
            system="jira",
            id="OPS-1234",
            reason="Restore checkout capacity",
            supplied_by="alice@example.com",
            supplied_at="2026-09-08T10:00:00+00:00",
        )
        assert ref.classification is None

    def test_classification_accepts_any_string_not_a_fixed_enum(self) -> None:
        ref = ChangeReferenceModel(
            system="jira",
            id="OPS-1234",
            reason="Restore checkout capacity",
            classification="our_custom_tier",
            supplied_by="alice@example.com",
            supplied_at="2026-09-08T10:00:00+00:00",
        )
        assert ref.classification == "our_custom_tier"


class TestChangeTrackingConfigModel:
    def test_all_fields_optional(self) -> None:
        cfg = ChangeTrackingConfigModel()
        assert cfg.system is None
        assert cfg.url_template is None
        assert cfg.id_pattern is None
        assert cfg.classifications is None

    def test_valid_id_pattern_accepted(self) -> None:
        cfg = ChangeTrackingConfigModel(id_pattern=r"^[A-Z][A-Z0-9]+-[0-9]+$")
        assert cfg.id_pattern == r"^[A-Z][A-Z0-9]+-[0-9]+$"

    def test_invalid_id_pattern_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ChangeTrackingConfigModel(id_pattern="[unterminated")

    def test_classifications_accepts_org_defined_list(self) -> None:
        cfg = ChangeTrackingConfigModel(classifications=["emergency", "normal", "standard"])
        assert cfg.classifications == ["emergency", "normal", "standard"]
