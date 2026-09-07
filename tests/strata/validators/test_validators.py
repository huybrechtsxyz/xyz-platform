"""Tests for BaseValidator and PlatformValidator."""

from pathlib import Path

import pytest

from strata.validators.base_validator import BaseValidator
from strata.validators.platform_validator import PlatformValidator

# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------


def _data(relative_path: str) -> Path:
    return Path(__file__).parent.parent.parent / "data" / relative_path


# ---------------------------------------------------------------------------
# Concrete subclass for testing BaseValidator (abstract)
# ---------------------------------------------------------------------------


class _ConcreteValidator(BaseValidator):
    """Minimal concrete implementation for testing the abstract base."""

    def before_validate(self, work_path: Path) -> bool:
        return True

    def validate(self, work_path: Path) -> bool:
        return True

    def after_validate(self, work_path: Path) -> bool:
        return True


# ---------------------------------------------------------------------------
# BaseValidator
# ---------------------------------------------------------------------------


class TestBaseValidatorInit:
    def test_errors_empty_on_init(self):
        v = _ConcreteValidator()
        assert v._errors == []

    def test_messages_empty_on_init(self):
        v = _ConcreteValidator()
        assert v._messages == []

    def test_has_errors_false_initially(self):
        v = _ConcreteValidator()
        assert v.has_errors() is False

    def test_has_messages_false_initially(self):
        v = _ConcreteValidator()
        assert v.has_messages() is False


class TestBaseValidatorErrorMessageHelpers:
    def test_has_errors_true_after_append(self):
        v = _ConcreteValidator()
        v._errors.append("bad")
        assert v.has_errors() is True

    def test_get_errors_returns_list(self):
        v = _ConcreteValidator()
        v._errors.append("e1")
        assert v.get_errors() == ["e1"]

    def test_has_messages_true_after_append(self):
        v = _ConcreteValidator()
        v._messages.append("info")
        assert v.has_messages() is True

    def test_get_messages_returns_list(self):
        v = _ConcreteValidator()
        v._messages.append("m1")
        assert v.get_messages() == ["m1"]

    def test_two_instances_are_independent(self):
        v1 = _ConcreteValidator()
        v2 = _ConcreteValidator()
        v1._errors.append("only v1")
        assert v2.has_errors() is False

    def test_validate_is_abstract(self):
        """Cannot instantiate BaseValidator directly."""
        with pytest.raises(TypeError):
            BaseValidator()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# PlatformValidator — before_validate
# ---------------------------------------------------------------------------


class TestPlatformValidatorBeforeValidate:
    def test_file_not_found_returns_false(self, tmp_path):
        v = PlatformValidator(tmp_path / "nonexistent.yaml")
        result = v.before_validate(tmp_path)
        assert result is False
        assert any("not found" in e for e in v.get_errors())

    def test_invalid_yaml_returns_false(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("{not: valid: yaml:", encoding="utf-8")
        v = PlatformValidator(bad)
        result = v.before_validate(tmp_path)
        assert result is False
        assert any("YAML" in e for e in v.get_errors())

    def test_non_dict_yaml_returns_false(self, tmp_path):
        f = tmp_path / "list.yaml"
        f.write_text("- item1\n- item2\n", encoding="utf-8")
        v = PlatformValidator(f)
        result = v.before_validate(tmp_path)
        assert result is False
        assert any("mapping" in e for e in v.get_errors())

    def test_missing_kind_returns_false(self, tmp_path):
        f = tmp_path / "nokind.yaml"
        f.write_text("apiVersion: strata.huybrechts.xyz/v1\nmeta:\n  name: test\n", encoding="utf-8")
        v = PlatformValidator(f)
        result = v.before_validate(tmp_path)
        assert result is False
        assert any("kind" in e for e in v.get_errors())

    def test_unknown_kind_returns_false(self, tmp_path):
        f = tmp_path / "unk.yaml"
        f.write_text("kind: definitely_not_a_real_kind\n", encoding="utf-8")
        v = PlatformValidator(f)
        result = v.before_validate(tmp_path)
        assert result is False
        assert any("definitely_not_a_real_kind" in e for e in v.get_errors())

    def test_valid_workspace_file_returns_true(self, tmp_path):
        src = _data("workspaces/workspace-standard.yaml")
        v = PlatformValidator(src)
        result = v.before_validate(tmp_path)
        assert result is True
        assert v.has_errors() is False

    def test_detected_kind_set_after_before_validate(self, tmp_path):
        src = _data("workspaces/workspace-standard.yaml")
        v = PlatformValidator(src)
        v.before_validate(tmp_path)
        assert v.detected_kind is not None
        assert v.detected_kind.value == "workspace"

    def test_valid_deployment_file_detected(self, tmp_path):
        src = _data("deployments/deployment-standard.yaml")
        v = PlatformValidator(src)
        result = v.before_validate(tmp_path)
        assert result is True
        assert v.detected_kind.value == "deployment"

    def test_valid_environment_file_detected(self, tmp_path):
        src = _data("environments/environment-standard.yaml")
        v = PlatformValidator(src)
        result = v.before_validate(tmp_path)
        assert result is True
        assert v.detected_kind.value == "environment"

    def test_valid_namespace_file_detected(self, tmp_path):
        src = _data("namespaces/namespace-standard.yaml")
        v = PlatformValidator(src)
        result = v.before_validate(tmp_path)
        assert result is True
        assert v.detected_kind.value == "namespace"

    def test_valid_module_file_detected(self, tmp_path):
        src = _data("modules/module-standard.yaml")
        v = PlatformValidator(src)
        result = v.before_validate(tmp_path)
        assert result is True
        assert v.detected_kind.value == "module"

    def test_valid_firewall_file_detected(self, tmp_path):
        src = _data("firewalls/firewall-standard.yaml")
        v = PlatformValidator(src)
        result = v.before_validate(tmp_path)
        assert result is True
        assert v.detected_kind.value == "firewall"

    def test_valid_dns_file_detected(self, tmp_path):
        src = _data("dns/dns-standard.yaml")
        v = PlatformValidator(src)
        result = v.before_validate(tmp_path)
        assert result is True
        assert v.detected_kind.value == "dns"

    def test_valid_resource_file_detected(self, tmp_path):
        src = _data("resources/resource-standard.yaml")
        v = PlatformValidator(src)
        result = v.before_validate(tmp_path)
        assert result is True
        assert v.detected_kind.value == "resource"

    def test_valid_provider_file_detected(self, tmp_path):
        src = _data("providers/provider-standard.yaml")
        v = PlatformValidator(src)
        result = v.before_validate(tmp_path)
        assert result is True
        assert v.detected_kind.value == "provider"


# ---------------------------------------------------------------------------
# PlatformValidator — validate (Phase 1 only, no configuration_service)
# ---------------------------------------------------------------------------


class TestPlatformValidatorValidate:
    def test_validate_without_before_validate_returns_false(self, tmp_path):
        v = PlatformValidator(tmp_path / "x.yaml")
        result = v.validate(tmp_path)
        assert result is False
        assert any("before_validate" in e for e in v.get_errors())

    def test_validate_workspace_standard_passes(self, tmp_path):
        src = _data("workspaces/workspace-standard.yaml")
        v = PlatformValidator(src)
        assert v.before_validate(tmp_path)
        result = v.validate(tmp_path)
        assert result is True
        assert v.has_errors() is False
        assert v.service is not None

    def test_validate_deployment_standard_passes(self, tmp_path):
        src = _data("deployments/deployment-standard.yaml")
        v = PlatformValidator(src)
        assert v.before_validate(tmp_path)
        result = v.validate(tmp_path)
        assert result is True

    def test_validate_environment_standard_passes(self, tmp_path):
        src = _data("environments/environment-standard.yaml")
        v = PlatformValidator(src)
        assert v.before_validate(tmp_path)
        result = v.validate(tmp_path)
        assert result is True

    def test_validate_namespace_standard_passes(self, tmp_path):
        src = _data("namespaces/namespace-standard.yaml")
        v = PlatformValidator(src)
        assert v.before_validate(tmp_path)
        result = v.validate(tmp_path)
        assert result is True

    def test_validate_module_standard_passes(self, tmp_path):
        src = _data("modules/module-standard.yaml")
        v = PlatformValidator(src)
        assert v.before_validate(tmp_path)
        result = v.validate(tmp_path)
        assert result is True

    def test_validate_firewall_standard_passes(self, tmp_path):
        src = _data("firewalls/firewall-standard.yaml")
        v = PlatformValidator(src)
        assert v.before_validate(tmp_path)
        result = v.validate(tmp_path)
        assert result is True

    def test_validate_dns_standard_passes(self, tmp_path):
        src = _data("dns/dns-standard.yaml")
        v = PlatformValidator(src)
        assert v.before_validate(tmp_path)
        result = v.validate(tmp_path)
        assert result is True

    def test_validate_resource_standard_passes(self, tmp_path):
        src = _data("resources/resource-standard.yaml")
        v = PlatformValidator(src)
        assert v.before_validate(tmp_path)
        result = v.validate(tmp_path)
        assert result is True

    def test_validate_provider_standard_passes(self, tmp_path):
        src = _data("providers/provider-standard.yaml")
        v = PlatformValidator(src)
        assert v.before_validate(tmp_path)
        result = v.validate(tmp_path)
        assert result is True

    def test_validate_invalid_workspace_fails(self, tmp_path):
        src = _data("workspaces/workspace-invalid.yaml")
        v = PlatformValidator(src)
        v.before_validate(tmp_path)
        result = v.validate(tmp_path)
        assert result is False

    def test_validate_service_property_set_on_success(self, tmp_path):
        src = _data("workspaces/workspace-standard.yaml")
        v = PlatformValidator(src)
        v.before_validate(tmp_path)
        v.validate(tmp_path)
        assert v.service is not None


# ---------------------------------------------------------------------------
# PlatformValidator — partial deployments with --deep (configuration_service set)
# ---------------------------------------------------------------------------


class TestPlatformValidatorPartialDeploymentDeepCheck:
    """Regression tests: --deep skips full Phase 2 semantic validation for
    `spec.partial: true` deployments, but a lighter-weight check should still
    catch an `environments[]` entry that doesn't resolve to a file on disk."""

    def _write_partial_deployment(self, tmp_path: Path, environments: list) -> Path:
        import yaml

        data = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "deployment",
            "meta": {"name": "base"},
            "spec": {"partial": True, "environments": environments},
        }
        f = tmp_path / "base.yaml"
        f.write_text(yaml.dump(data), encoding="utf-8")
        return f

    def test_dangling_environments_ref_fails_with_deep(self, tmp_path):
        from unittest.mock import MagicMock

        f = self._write_partial_deployment(tmp_path, ["missing.yaml"])
        config_svc = MagicMock()
        config_svc.model = None
        v = PlatformValidator(f, configuration_service=config_svc)
        assert v.before_validate(tmp_path) is True
        result = v.validate(tmp_path)
        assert result is False
        assert any("missing.yaml" in e for e in v.get_errors())

    def test_present_environments_ref_passes_with_deep(self, tmp_path):
        from unittest.mock import MagicMock

        (tmp_path / "env.yaml").write_text("# placeholder\n", encoding="utf-8")
        f = self._write_partial_deployment(tmp_path, ["env.yaml"])
        config_svc = MagicMock()
        config_svc.model = None
        v = PlatformValidator(f, configuration_service=config_svc)
        assert v.before_validate(tmp_path) is True
        result = v.validate(tmp_path)
        assert result is True
        assert v.has_errors() is False

    def test_dangling_environments_ref_not_checked_without_deep(self, tmp_path):
        """Without --deep (no configuration_service), the lightweight check is
        skipped too — matches the scope of Phase 2 (only runs under --deep)."""
        f = self._write_partial_deployment(tmp_path, ["missing.yaml"])
        v = PlatformValidator(f, configuration_service=None)
        assert v.before_validate(tmp_path) is True
        result = v.validate(tmp_path)
        assert result is True


# ---------------------------------------------------------------------------
# PlatformValidator — after_validate
# ---------------------------------------------------------------------------


class TestPlatformValidatorAfterValidate:
    def test_after_validate_returns_true_when_no_hooks(self, tmp_path):
        src = _data("workspaces/workspace-standard.yaml")
        v = PlatformValidator(src)
        v.before_validate(tmp_path)
        v.validate(tmp_path)
        result = v.after_validate(tmp_path)
        assert result is True

    def test_after_validate_no_errors_on_clean_file(self, tmp_path):
        src = _data("workspaces/workspace-standard.yaml")
        v = PlatformValidator(src)
        v.before_validate(tmp_path)
        v.validate(tmp_path)
        v.after_validate(tmp_path)
        assert v.has_errors() is False


# ---------------------------------------------------------------------------
# PlatformValidator — full pipeline (before + validate + after)
# ---------------------------------------------------------------------------


class TestPlatformValidatorFullPipeline:
    def _run(self, file_path: Path, work_path: Path) -> bool:
        v = PlatformValidator(file_path)
        if not v.before_validate(work_path):
            return False
        if not v.validate(work_path):
            return False
        return v.after_validate(work_path)

    def test_workspace_standard_full_pipeline(self, tmp_path):
        assert self._run(_data("workspaces/workspace-standard.yaml"), tmp_path) is True

    def test_deployment_standard_full_pipeline(self, tmp_path):
        assert self._run(_data("deployments/deployment-standard.yaml"), tmp_path) is True

    def test_environment_standard_full_pipeline(self, tmp_path):
        assert self._run(_data("environments/environment-standard.yaml"), tmp_path) is True

    def test_namespace_standard_full_pipeline(self, tmp_path):
        assert self._run(_data("namespaces/namespace-standard.yaml"), tmp_path) is True

    def test_module_standard_full_pipeline(self, tmp_path):
        assert self._run(_data("modules/module-standard.yaml"), tmp_path) is True

    def test_firewall_standard_full_pipeline(self, tmp_path):
        assert self._run(_data("firewalls/firewall-standard.yaml"), tmp_path) is True

    def test_resource_standard_full_pipeline(self, tmp_path):
        assert self._run(_data("resources/resource-standard.yaml"), tmp_path) is True

    def test_provider_standard_full_pipeline(self, tmp_path):
        assert self._run(_data("providers/provider-standard.yaml"), tmp_path) is True

    def test_invalid_workspace_pipeline_stops_at_validate(self, tmp_path):
        v = PlatformValidator(_data("workspaces/workspace-invalid.yaml"))
        assert v.before_validate(tmp_path) is True  # kind is valid
        assert v.validate(tmp_path) is False

    def test_missing_file_pipeline_stops_at_before(self, tmp_path):
        v = PlatformValidator(tmp_path / "missing.yaml")
        assert v.before_validate(tmp_path) is False
        assert v.has_errors()


# ---------------------------------------------------------------------------
# PlatformValidator — properties
# ---------------------------------------------------------------------------


class TestPlatformValidatorProperties:
    def test_detected_kind_none_before_before_validate(self, tmp_path):
        v = PlatformValidator(tmp_path / "any.yaml")
        assert v.detected_kind is None

    def test_service_none_before_validate(self, tmp_path):
        v = PlatformValidator(tmp_path / "any.yaml")
        assert v.service is None
