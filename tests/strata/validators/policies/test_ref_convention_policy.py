#!/usr/bin/env python3
"""Unit tests for RefConventionPolicy."""

from unittest.mock import MagicMock

from strata.models.policy_model import PolicyModel
from strata.models.repository_model import RemoteConventionsModel, RemoteModel, RemoteType
from strata.validators.policies.base_policy import PolicyContext
from strata.validators.policies.ref_convention_policy import RefConventionPolicy


def _policy_model(config=None) -> PolicyModel:
    """Create a test PolicyModel."""
    return PolicyModel(
        name="test_ref_convention",
        type="ref_convention",
        phase="validate",
        enforcement="warn",
        configuration=config,
    )


def _make_remote(name: str, release_pattern=None, quality_pattern=None) -> RemoteModel:
    """Create a test RemoteModel, optionally with conventions declared on it."""
    conventions = None
    if release_pattern or quality_pattern:
        conventions = RemoteConventionsModel(release_pattern=release_pattern, quality_pattern=quality_pattern)
    return RemoteModel(
        name=name,
        type=RemoteType.GITOPS,
        repository="https://example.com/acme/repo.git",
        reference="main",
        source_path=".",
        deploy_path=f"repos/{name}",
        conventions=conventions,
    )


class TestRefConventionPolicyConfiguration:
    def test_passes_when_no_services(self):
        policy = RefConventionPolicy(_policy_model())
        context = PolicyContext(
            phase="validate",
            work_path=None,
            deployment_service=None,
            configuration_service=None,
        )

        result = policy.evaluate(context)

        assert result.passed is True
        assert result.violations == []

    def test_passes_when_no_remotes_have_conventions(self):
        mock_config_service = MagicMock()
        mock_config_service.get_remotes.return_value = [_make_remote("my-service")]
        mock_deployment_service = MagicMock()
        mock_deployment_service.all.return_value = []

        policy = RefConventionPolicy(_policy_model())
        context = PolicyContext(
            phase="validate",
            work_path=None,
            deployment_service=mock_deployment_service,
            configuration_service=mock_config_service,
        )

        result = policy.evaluate(context)

        assert result.passed is True
        assert "skipped" in result.details

    def test_passes_when_no_remotes_declared_at_all(self):
        mock_config_service = MagicMock()
        mock_config_service.get_remotes.return_value = None
        mock_deployment_service = MagicMock()
        mock_deployment_service.all.return_value = []

        policy = RefConventionPolicy(_policy_model())
        context = PolicyContext(
            phase="validate",
            work_path=None,
            deployment_service=mock_deployment_service,
            configuration_service=mock_config_service,
        )

        result = policy.evaluate(context)

        assert result.passed is True


class TestRefConventionPolicyEvaluate:
    """End-to-end: patterns come from spec.remotes[].conventions, not policy config."""

    def test_detects_violation_from_remote_conventions(self):
        mock_config_service = MagicMock()
        mock_config_service.get_remotes.return_value = [_make_remote("my-service", release_pattern=r"^v\d+\.\d+\.\d+$")]
        mock_config_service.environments = []

        mock_override = MagicMock()
        mock_override.name = "my-service"
        mock_override.reference = "main"  # does not match release pattern

        mock_deployment = MagicMock()
        mock_deployment.model.meta.name = "acme"
        mock_deployment.model.spec.overrides.remotes = [mock_override]

        mock_deployment_service = MagicMock()
        mock_deployment_service.all.return_value = [mock_deployment]

        policy = RefConventionPolicy(_policy_model())
        context = PolicyContext(
            phase="validate",
            work_path=None,
            deployment_service=mock_deployment_service,
            configuration_service=mock_config_service,
        )

        result = policy.evaluate(context)

        assert result.passed is False
        assert len(result.violations) == 1
        assert "my-service" in result.violations[0]

    def test_passes_when_reference_matches_remote_conventions(self):
        mock_config_service = MagicMock()
        mock_config_service.get_remotes.return_value = [_make_remote("my-service", release_pattern=r"^v\d+\.\d+\.\d+$")]
        mock_config_service.environments = []

        mock_override = MagicMock()
        mock_override.name = "my-service"
        mock_override.reference = "v1.2.0"

        mock_deployment = MagicMock()
        mock_deployment.model.meta.name = "acme"
        mock_deployment.model.spec.overrides.remotes = [mock_override]

        mock_deployment_service = MagicMock()
        mock_deployment_service.all.return_value = [mock_deployment]

        policy = RefConventionPolicy(_policy_model())
        context = PolicyContext(
            phase="validate",
            work_path=None,
            deployment_service=mock_deployment_service,
            configuration_service=mock_config_service,
        )

        result = policy.evaluate(context)

        assert result.passed is True
        assert result.violations == []


class TestRefConventionPolicyValidation:
    def test_validates_release_pattern(self):
        config = {
            "remotes": [
                {
                    "name": "my-service",
                    "release_pattern": r"^v\d+\.\d+\.\d+$",
                }
            ]
        }
        policy = RefConventionPolicy(_policy_model(config=config))

        # Mock remote override
        mock_remote = MagicMock()
        mock_remote.name = "my-service"
        mock_remote.reference = "v1.2.0"

        violations = policy._check_remotes([mock_remote], config["remotes"][0:], "deployment 'test'")
        assert violations == []

    def test_fails_on_non_matching_release_pattern(self):
        config = {
            "remotes": [
                {
                    "name": "my-service",
                    "release_pattern": r"^v\d+\.\d+\.\d+$",
                }
            ]
        }
        policy = RefConventionPolicy(_policy_model(config=config))

        # Mock remote override with non-matching reference
        mock_remote = MagicMock()
        mock_remote.name = "my-service"
        mock_remote.reference = "main"  # Should not match release pattern

        # Build patterns dict
        patterns = {}
        for cfg in config["remotes"]:
            name = cfg.get("name")
            if name:
                patterns[name] = {
                    "release": cfg.get("release_pattern"),
                    "quality": cfg.get("quality_pattern"),
                }

        violations = policy._check_remotes([mock_remote], patterns, "environment 'prd'")
        assert len(violations) == 1
        assert "does not match expected pattern" in violations[0]

    def test_accepts_quality_pattern(self):
        config = {
            "remotes": [
                {
                    "name": "my-service",
                    "release_pattern": r"^v\d+\.\d+\.\d+$",
                    "quality_pattern": r"^tested(-\d+)?$",
                }
            ]
        }
        policy = RefConventionPolicy(_policy_model(config=config))

        # Mock remote override with quality tag
        mock_remote = MagicMock()
        mock_remote.name = "my-service"
        mock_remote.reference = "tested-20260621"

        # Build patterns dict
        patterns = {}
        for cfg in config["remotes"]:
            name = cfg.get("name")
            if name:
                patterns[name] = {
                    "release": cfg.get("release_pattern"),
                    "quality": cfg.get("quality_pattern"),
                }

        violations = policy._check_remotes([mock_remote], patterns, "environment 'staging'")
        assert violations == []

    def test_skips_remotes_not_in_configuration(self):
        config = {
            "remotes": [
                {
                    "name": "my-service",
                    "release_pattern": r"^v\d+\.\d+\.\d+$",
                }
            ]
        }
        policy = RefConventionPolicy(_policy_model(config=config))

        # Mock remote not in config
        mock_remote = MagicMock()
        mock_remote.name = "other-service"
        mock_remote.reference = "main"

        # Build patterns dict
        patterns = {}
        for cfg in config["remotes"]:
            name = cfg.get("name")
            if name:
                patterns[name] = {
                    "release": cfg.get("release_pattern"),
                    "quality": cfg.get("quality_pattern"),
                }

        violations = policy._check_remotes([mock_remote], patterns, "deployment 'test'")
        # Should skip remote not in patterns
        assert violations == []

    def test_multiple_remotes(self):
        config = {
            "remotes": [
                {
                    "name": "my-service",
                    "release_pattern": r"^v\d+\.\d+\.\d+$",
                },
                {
                    "name": "tf-landscape",
                    "release_pattern": r"^v\d+\.\d+\.\d+$",
                },
            ]
        }
        policy = RefConventionPolicy(_policy_model(config=config))

        # Mock multiple remotes, one valid, one invalid
        mock_remote1 = MagicMock()
        mock_remote1.name = "my-service"
        mock_remote1.reference = "v1.2.0"  # Valid

        mock_remote2 = MagicMock()
        mock_remote2.name = "tf-landscape"
        mock_remote2.reference = "main"  # Invalid

        # Build patterns dict
        patterns = {}
        for cfg in config["remotes"]:
            name = cfg.get("name")
            if name:
                patterns[name] = {
                    "release": cfg.get("release_pattern"),
                    "quality": cfg.get("quality_pattern"),
                }

        violations = policy._check_remotes([mock_remote1, mock_remote2], patterns, "environment 'prod'")
        assert len(violations) == 1
        assert "tf-landscape" in violations[0]


class TestRefConventionPolicyRegex:
    def test_pattern_matching(self):
        policy = RefConventionPolicy(_policy_model())

        # Valid patterns
        assert policy._matches_pattern("v1.2.0", r"^v\d+\.\d+\.\d+$") is True
        assert policy._matches_pattern("tested", r"^tested$") is True
        assert policy._matches_pattern("tested-20260621", r"^tested(-\d+)?$") is True
        assert policy._matches_pattern("rc-1", r"^rc-\d+$") is True

        # Invalid patterns
        assert policy._matches_pattern("main", r"^v\d+\.\d+\.\d+$") is False
        assert policy._matches_pattern("v1.2", r"^v\d+\.\d+\.\d+$") is False
        assert policy._matches_pattern("release-2026-01-01", r"^tested$") is False

    def test_invalid_regex_pattern_returns_false(self):
        policy = RefConventionPolicy(_policy_model())

        # Invalid regex
        assert policy._matches_pattern("any-ref", "(?P<invalid)") is False
