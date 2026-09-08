"""Tests for build output profile integration in TerraformBuilder and TerraformDeployer."""

import json
from pathlib import Path
from unittest.mock import MagicMock

from strata.builders.terraform_builder import TerraformBuilder
from strata.deployers.terraform_deployer import TerraformDeployer
from strata.models.workspace_model import OutputProfileModel
from strata.utils.resolved_values import ResolvedValues

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_deployment_service(work_path: Path, provisioner_output: OutputProfileModel | None = None):
    """Build a minimal mocked DeploymentService with one Terraform provisioner."""

    prov = MagicMock()
    prov.name = "terraform"
    prov.provisioner = MagicMock()
    prov.provisioner.value = "terraform"
    prov.output = provisioner_output
    prov.source = MagicMock()
    prov.source.repository = None
    prov.source.source_path = "."
    prov.source.target_path = None

    ws_model = MagicMock()
    ws_model.spec.provisioners = [prov]

    ws_service = MagicMock()
    ws_service.model = ws_model

    env_service = MagicMock()
    env_service.model = None
    env_service.get_variables.return_value = []
    env_service.get_features.return_value = []

    svc = MagicMock()
    svc.get_workspace_service.return_value = ws_service
    svc.get_environment_service.return_value = env_service
    svc.is_validated.return_value = True
    svc.get_build_path.return_value = work_path / "build"
    return svc


def _minimal_vars():
    return {
        "workspace": {"workspace_name": "ws"},
        "providers": {"platform_providers": {}},
        "topologies": {"topologies": {}},
        "resources_by_category": {},
        "modules": {"modules": {}},
        "namespaces": {"namespaces": {}},
        "firewalls": {"firewalls": {}},
        "dns": {},
        "networks": {},
        "Tenant": {},
        "required_variables": {"variables": []},
        "required_features": {"features": []},
        "required_secrets": {"secrets": []},
    }


# ---------------------------------------------------------------------------
# TerraformBuilder._planned_files — profile-aware filtering
# ---------------------------------------------------------------------------


class TestPlannedFilesWithProfile:
    def test_no_profile_includes_workspace(self):
        builder = TerraformBuilder()
        files = builder._planned_files(_minimal_vars(), profile=None)
        names = [f for f, _ in files]
        assert "workspace.auto.tfvars.json" in names

    def test_format_none_produces_no_files(self):
        builder = TerraformBuilder()
        profile = OutputProfileModel(format="none")
        files = builder._planned_files(_minimal_vars(), profile=profile)
        assert files == []

    def test_format_custom_no_emits_produces_no_structural_files(self):
        builder = TerraformBuilder()
        profile = OutputProfileModel(format="custom")
        vars_with_providers = dict(_minimal_vars())
        vars_with_providers["providers"] = {"platform_providers": {"azure": {}}}
        files = builder._planned_files(vars_with_providers, profile=profile)
        names = [f for f, _ in files]
        assert "workspace.auto.tfvars.json" not in names
        assert "providers.auto.tfvars.json" not in names

    def test_format_custom_emits_workspace_when_requested(self):
        builder = TerraformBuilder()
        profile = OutputProfileModel(format="custom", emits=["workspace"])
        files = builder._planned_files(_minimal_vars(), profile=profile)
        names = [f for f, _ in files]
        assert "workspace.auto.tfvars.json" in names

    def test_format_strata_produces_workspace_file(self):
        builder = TerraformBuilder()
        profile = OutputProfileModel(format="strata")
        files = builder._planned_files(_minimal_vars(), profile=profile)
        names = [f for f, _ in files]
        assert "workspace.auto.tfvars.json" in names


# ---------------------------------------------------------------------------
# TerraformBuilder — feature flags and flat variables
# ---------------------------------------------------------------------------


class TestBuildFeatureFlagsVars:
    def test_constant_store_features_resolved(self):
        from strata.models.store_models import FeatureStoreType

        builder = TerraformBuilder()
        feat = MagicMock()
        feat.store = FeatureStoreType.CONSTANT
        feat.key = "enable_vnet"
        feat.value = True

        env_service = MagicMock()
        env_service.get_features.return_value = [feat]
        env_service.model = MagicMock()

        deployment_service = MagicMock()
        deployment_service.get_environment_service.return_value = env_service

        result = builder._build_feature_flags_vars(deployment_service)
        assert result == {"enable_vnet": True}

    def test_no_env_service_returns_empty(self):
        builder = TerraformBuilder()
        deployment_service = MagicMock()
        deployment_service.get_environment_service.return_value = None
        assert builder._build_feature_flags_vars(deployment_service) == {}

    def test_integration_store_features_skipped(self):
        from strata.models.store_models import FeatureStoreType

        builder = TerraformBuilder()
        feat = MagicMock()
        feat.store = FeatureStoreType.FLAGSMITH  # integration-backed, skip at build time
        feat.key = "enable_beta"
        feat.value = None

        env_service = MagicMock()
        env_service.get_features.return_value = [feat]
        env_service.model = MagicMock()

        deployment_service = MagicMock()
        deployment_service.get_environment_service.return_value = env_service

        result = builder._build_feature_flags_vars(deployment_service)
        # Flagsmith-backed flags not emitted at build time
        assert "enable_beta" not in result


# ---------------------------------------------------------------------------
# TerraformBuilder — properties deep merge
# ---------------------------------------------------------------------------


class TestResolveMergedProperties:
    def test_workspace_properties_base(self):
        builder = TerraformBuilder()

        ws_model = MagicMock()
        ws_model.spec.properties = {"owner": "Platform Team", "k8s_version": "1.31"}
        ws_service = MagicMock()
        ws_service.model = ws_model

        env_service = MagicMock()
        env_service.model = None

        svc = MagicMock()
        svc.get_workspace_service.return_value = ws_service
        svc.get_environment_service.return_value = env_service

        result = builder._resolve_merged_properties(svc, "properties")
        assert result["owner"] == "Platform Team"
        assert result["k8s_version"] == "1.31"

    def test_env_deep_merges_over_workspace(self):
        builder = TerraformBuilder()

        ws_model = MagicMock()
        ws_model.spec.properties = {"aks_config": {"size": "Small", "count": 1}}
        ws_service = MagicMock()
        ws_service.model = ws_model

        env_model = MagicMock()
        env_model.spec.properties = {"aks_config": {"count": 3}}
        env_model.spec.overrides = None
        env_service = MagicMock()
        env_service.model = env_model

        svc = MagicMock()
        svc.get_workspace_service.return_value = ws_service
        svc.get_environment_service.return_value = env_service

        result = builder._resolve_merged_properties(svc, "properties")
        assert result["aks_config"]["size"] == "Small"  # from workspace
        assert result["aks_config"]["count"] == 3  # overridden by env


# ---------------------------------------------------------------------------
# TerraformDeployer._write_deploy_time_vars — security: secrets never written
# ---------------------------------------------------------------------------


class TestWriteDeployTimeVars:
    def _make_deployer(self, tmp_path: Path) -> TerraformDeployer:
        from strata.models.deployment_model import DeploymentStageModel

        stage = MagicMock(spec=DeploymentStageModel)
        stage.name = "production"
        stage.provisioner = None
        stage.topology = None
        return TerraformDeployer(
            stage=stage,
            deployment_service=MagicMock(),
            configuration_service=MagicMock(),
            build_path=tmp_path,
            work_path=tmp_path,
        )

    def test_features_written_when_emit_features(self, tmp_path: Path):
        deployer = self._make_deployer(tmp_path)
        resolved = ResolvedValues(features={"enable_vnet": True, "enable_aks": False})
        profile = OutputProfileModel(format="custom", emits=["features"])

        deployer._write_deploy_time_vars(resolved, profile, tmp_path)

        flags_file = tmp_path / "flags.auto.tfvars.json"
        assert flags_file.exists()
        data = json.loads(flags_file.read_text())
        assert data == {"enable_vnet": True, "enable_aks": False}

    def test_variables_written_when_emit_variables(self, tmp_path: Path):
        deployer = self._make_deployer(tmp_path)
        resolved = ResolvedValues(variables={"ARM_TENANT_ID": "abc", "AZURE_LOCATION": "westeurope"})
        profile = OutputProfileModel(format="custom", emits=["variables"])

        deployer._write_deploy_time_vars(resolved, profile, tmp_path)

        vars_file = tmp_path / "variables.auto.tfvars.json"
        assert vars_file.exists()
        data = json.loads(vars_file.read_text())
        assert data["ARM_TENANT_ID"] == "abc"

    def test_secrets_never_written_to_disk(self, tmp_path: Path):
        """Secrets must NEVER appear in any .auto.tfvars.json file."""
        deployer = self._make_deployer(tmp_path)
        resolved = ResolvedValues(
            features={"enable_vnet": True},
            secrets={"ARM_CLIENT_SECRET": "super-secret", "ARM_CLIENT_ID": "client-id"},
        )
        profile = OutputProfileModel(format="custom", emits=["features"])

        deployer._write_deploy_time_vars(resolved, profile, tmp_path)

        # Verify no .auto.tfvars.json file contains any secret value
        for tf_file in tmp_path.glob("*.auto.tfvars.json"):
            content = tf_file.read_text()
            assert "super-secret" not in content
            assert "ARM_CLIENT_SECRET" not in content

    def test_nothing_written_when_profile_does_not_emit(self, tmp_path: Path):
        deployer = self._make_deployer(tmp_path)
        resolved = ResolvedValues(features={"enable_vnet": True}, variables={"KEY": "val"})
        # format=custom with no emits — should_emit returns False for everything
        profile = OutputProfileModel(format="custom")

        deployer._write_deploy_time_vars(resolved, profile, tmp_path)

        assert not (tmp_path / "flags.auto.tfvars.json").exists()
        assert not (tmp_path / "variables.auto.tfvars.json").exists()


# ---------------------------------------------------------------------------
# TerraformDeployer._resolve_backend_expr
# ---------------------------------------------------------------------------


class TestResolveBackendExpr:
    def _make_deployer(self, tmp_path: Path) -> TerraformDeployer:
        from strata.models.deployment_model import DeploymentStageModel

        stage = MagicMock(spec=DeploymentStageModel)
        stage.name = "production"
        stage.provisioner = None
        stage.topology = None
        deployer = TerraformDeployer(
            stage=stage,
            deployment_service=MagicMock(),
            configuration_service=MagicMock(),
            build_path=tmp_path,
            work_path=tmp_path,
        )
        deployer.resolved_values = ResolvedValues(
            variables={"TF_RG": "my-rg", "TF_SA": "my-storage"},
            secrets={"TF_STATE_KEY": "tfstate-secret"},
        )
        return deployer

    def test_var_expression_resolved(self, tmp_path: Path):
        deployer = self._make_deployer(tmp_path)
        assert deployer._resolve_backend_expr("${var:TF_RG}") == ("my-rg", [])

    def test_secret_expression_resolved(self, tmp_path: Path):
        deployer = self._make_deployer(tmp_path)
        assert deployer._resolve_backend_expr("${secret:TF_STATE_KEY}") == ("tfstate-secret", [])

    def test_plain_value_unchanged(self, tmp_path: Path):
        deployer = self._make_deployer(tmp_path)
        assert deployer._resolve_backend_expr("tfstate") == ("tfstate", [])

    def test_unresolved_expr_is_an_error_not_a_pass_through(self, tmp_path: Path):
        deployer = self._make_deployer(tmp_path)
        value, errors = deployer._resolve_backend_expr("${var:MISSING_KEY}")
        assert value == "${var:MISSING_KEY}"
        assert len(errors) == 1
        assert "MISSING_KEY" in errors[0]

    def test_no_resolved_values_is_an_error(self, tmp_path: Path):
        deployer = self._make_deployer(tmp_path)
        deployer.resolved_values = None
        value, errors = deployer._resolve_backend_expr("${var:TF_RG}")
        assert value == "${var:TF_RG}"
        assert len(errors) == 1

    def test_build_backend_config_resolves_expressions(self, tmp_path: Path):
        from unittest.mock import MagicMock

        deployer = self._make_deployer(tmp_path)
        iac = MagicMock()
        iac.backend.configuration = {
            "resource_group_name": "${var:TF_RG}",
            "storage_account_name": "${var:TF_SA}",
            "key": "${secret:TF_STATE_KEY}",
        }
        result, errors = deployer._build_backend_config(iac)
        assert errors == []
        assert result == {
            "resource_group_name": "my-rg",
            "storage_account_name": "my-storage",
            "key": "tfstate-secret",
        }
