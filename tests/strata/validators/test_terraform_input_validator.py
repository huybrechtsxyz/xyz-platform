"""Tests for terraform_input_validator — parsing and cross-check logic."""

from strata.validators.terraform_input_validator import (
    STRATA_INJECTED_KEYS,
    TerraformVariable,
    _find_closest,
    check_inputs,
    parse_variables_tf,
)

# ---------------------------------------------------------------------------
# parse_variables_tf
# ---------------------------------------------------------------------------


class TestParseVariablesTf:
    def test_parses_simple_variable(self, tmp_path):
        (tmp_path / "variables.tf").write_text(
            """
variable "cluster_name" {
  type        = string
  description = "Name of the AKS cluster"
}
"""
        )
        result = parse_variables_tf(tmp_path)
        assert "cluster_name" in result
        assert result["cluster_name"].name == "cluster_name"
        assert result["cluster_name"].has_default is False
        assert result["cluster_name"].description == "Name of the AKS cluster"

    def test_parses_variable_with_default(self, tmp_path):
        (tmp_path / "variables.tf").write_text(
            """
variable "enable_monitoring" {
  type    = bool
  default = false
}
"""
        )
        result = parse_variables_tf(tmp_path)
        assert "enable_monitoring" in result
        assert result["enable_monitoring"].has_default is True
        assert result["enable_monitoring"].default_value is False

    def test_parses_sensitive_variable(self, tmp_path):
        (tmp_path / "variables.tf").write_text(
            """
variable "db_password" {
  type      = string
  sensitive = true
}
"""
        )
        result = parse_variables_tf(tmp_path)
        assert result["db_password"].sensitive is True

    def test_parses_multiple_files(self, tmp_path):
        (tmp_path / "variables.tf").write_text(
            """
variable "name" {
  type = string
}
"""
        )
        (tmp_path / "vars_network.tf").write_text(
            """
variable "vnet_cidr" {
  type    = string
  default = "10.0.0.0/16"
}
"""
        )
        result = parse_variables_tf(tmp_path)
        assert "name" in result
        assert "vnet_cidr" in result
        assert result["vnet_cidr"].has_default is True

    def test_ignores_non_variable_blocks(self, tmp_path):
        (tmp_path / "main.tf").write_text(
            """
resource "azurerm_resource_group" "rg" {
  name     = "rg-test"
  location = "westeurope"
}

variable "location" {
  type    = string
  default = "westeurope"
}
"""
        )
        result = parse_variables_tf(tmp_path)
        assert "location" in result
        assert len(result) == 1

    def test_empty_directory_returns_empty(self, tmp_path):
        result = parse_variables_tf(tmp_path)
        assert result == {}

    def test_nonexistent_directory_returns_empty(self, tmp_path):
        result = parse_variables_tf(tmp_path / "nonexistent")
        assert result == {}

    def test_ignores_subdirectory_variables(self, tmp_path):
        """Terraform only reads root module variables."""
        subdir = tmp_path / "modules" / "child"
        subdir.mkdir(parents=True)
        (subdir / "variables.tf").write_text('variable "child_var" { type = string }')
        (tmp_path / "variables.tf").write_text('variable "root_var" { type = string }')

        result = parse_variables_tf(tmp_path)
        assert "root_var" in result
        assert "child_var" not in result

    def test_handles_malformed_tf_gracefully(self, tmp_path):
        """Malformed files are skipped, not crashing."""
        (tmp_path / "bad.tf").write_text("this is not valid HCL {{{{")
        (tmp_path / "good.tf").write_text('variable "ok" { type = string }')

        result = parse_variables_tf(tmp_path)
        assert "ok" in result

    def test_variable_with_validation_blocks(self, tmp_path):
        (tmp_path / "variables.tf").write_text(
            """
variable "environment" {
  type = string
  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "Must be dev, staging, or prod."
  }
}
"""
        )
        result = parse_variables_tf(tmp_path)
        assert result["environment"].validation_rules == 1

    def test_variable_with_null_default(self, tmp_path):
        (tmp_path / "variables.tf").write_text(
            """
variable "optional_tag" {
  type    = string
  default = null
}
"""
        )
        result = parse_variables_tf(tmp_path)
        assert result["optional_tag"].has_default is True
        assert result["optional_tag"].default_value is None


# ---------------------------------------------------------------------------
# check_inputs
# ---------------------------------------------------------------------------


class TestCheckInputs:
    def _vars(self, *names, defaults=None):
        """Helper to create a dict of TerraformVariables."""
        defaults = defaults or set()
        return {
            name: TerraformVariable(
                name=name,
                has_default=(name in defaults),
                default_value="default" if name in defaults else None,
            )
            for name in names
        }

    def test_all_inputs_match(self):
        module_vars = self._vars("cluster_name", "enable_ha", "region")
        declared = {"cluster_name", "enable_ha", "region"}
        result = check_inputs(declared, module_vars)
        assert not result.has_errors
        assert result.errors == []
        assert result.warnings == []

    def test_undeclared_input_is_error(self):
        module_vars = self._vars("cluster_name", "enable_ha")
        declared = {"cluster_name", "enable_ha", "typo_var"}
        result = check_inputs(declared, module_vars)
        assert result.has_errors
        assert any("typo_var" in e for e in result.errors)

    def test_typo_suggestion_provided(self):
        module_vars = self._vars("enabled_monitoring", "cluster_name")
        declared = {"enabld_monitoring", "cluster_name"}
        result = check_inputs(declared, module_vars)
        assert result.has_errors
        assert any("did you mean" in e for e in result.errors)
        assert any("enabled_monitoring" in e for e in result.errors)

    def test_required_variable_not_supplied_is_warning(self):
        module_vars = self._vars("cluster_name", "subscription_id")
        declared = {"cluster_name"}
        result = check_inputs(declared, module_vars)
        assert not result.has_errors  # warnings don't block
        assert any("subscription_id" in w for w in result.warnings)
        assert any("Required" in w for w in result.warnings)

    def test_optional_variable_not_supplied_is_info(self):
        module_vars = self._vars("cluster_name", "tags", defaults={"tags"})
        declared = {"cluster_name"}
        result = check_inputs(declared, module_vars)
        assert not result.has_errors
        assert result.warnings == []  # tags has default, not a warning
        assert any("tags" in i for i in result.info)

    def test_excluded_keys_not_checked(self):
        module_vars = self._vars("cluster_name")
        declared = {"cluster_name", "strata_injected"}
        result = check_inputs(declared, module_vars, excluded_keys={"strata_injected"})
        assert not result.has_errors

    def test_excluded_keys_not_warned_as_unsupplied(self):
        module_vars = self._vars("workspace_name", "cluster_name")
        declared = {"cluster_name"}
        result = check_inputs(declared, module_vars, excluded_keys={"workspace_name"})
        assert not result.has_errors
        assert not any("workspace_name" in w for w in result.warnings)

    def test_empty_declared_keys(self):
        module_vars = self._vars("a", "b", defaults={"b"})
        result = check_inputs(set(), module_vars)
        assert not result.has_errors
        assert any("'a'" in w for w in result.warnings)  # required, not supplied

    def test_empty_module_vars(self):
        declared = {"some_key"}
        result = check_inputs(declared, {})
        assert result.has_errors  # some_key not in empty module
        assert any("some_key" in e for e in result.errors)

    def test_multiple_errors(self):
        module_vars = self._vars("a", "b", "c")
        declared = {"a", "typo1", "typo2"}
        result = check_inputs(declared, module_vars)
        assert len(result.errors) == 2


# ---------------------------------------------------------------------------
# _find_closest
# ---------------------------------------------------------------------------


class TestFindClosest:
    def test_close_match(self):
        assert _find_closest("enabld", {"enabled", "disabled", "cluster"}) == "enabled"

    def test_no_match(self):
        assert _find_closest("xyz", {"abc", "def", "ghi"}) is None

    def test_exact_match_not_needed(self):
        # Exact matches won't happen in practice (they'd pass the check)
        assert _find_closest("enabled", {"enabled", "disabled"}) == "enabled"

    def test_underscore_typo(self):
        assert _find_closest("enable_ha", {"enabled_ha", "region"}) == "enabled_ha"


# ---------------------------------------------------------------------------
# STRATA_INJECTED_KEYS
# ---------------------------------------------------------------------------


class TestStrataInjectedKeys:
    def test_contains_workspace_name(self):
        assert "workspace_name" in STRATA_INJECTED_KEYS

    def test_contains_platform_providers(self):
        assert "platform_providers" in STRATA_INJECTED_KEYS

    def test_is_frozen(self):
        assert isinstance(STRATA_INJECTED_KEYS, frozenset)
