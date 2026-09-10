"""Unit tests for PlatformBuilder."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from strata.builders.platform_builder import PlatformBuilder
from strata.models.platform_artifact_model import PlatformTenantModel


def _mock_deployment_service(validated=True, workspace_service=None, build_path=None):
    """Return a minimal MagicMock DeploymentService."""
    svc = MagicMock()
    svc.is_validated.return_value = validated
    svc.get_workspace_service.return_value = workspace_service
    if build_path is not None:
        svc.get_build_path.return_value = build_path
    return svc


class TestPlatformBuilderInit:
    def test_default_init(self):
        builder = PlatformBuilder()
        assert builder.verbose is False
        assert builder.configuration_service is None
        assert builder._last_platform_model is None

    def test_verbose_flag(self):
        builder = PlatformBuilder(verbose=True)
        assert builder.verbose is True

    def test_configuration_service_stored(self):
        mock_cfg = MagicMock()
        builder = PlatformBuilder(configuration_service=mock_cfg)
        assert builder.configuration_service is mock_cfg

    def test_no_errors_on_init(self):
        builder = PlatformBuilder()
        assert not builder.has_errors()
        assert not builder.has_messages()


class TestPlatformBuilderBeforeBuild:
    def test_not_validated_returns_false(self, tmp_path):
        builder = PlatformBuilder()
        svc = _mock_deployment_service(validated=False)
        result = builder.before_build(svc, tmp_path, tmp_path)
        assert result is False
        assert builder.has_errors()
        assert any("not validated" in e for e in builder.get_errors())

    def test_no_workspace_service_returns_false(self, tmp_path):
        builder = PlatformBuilder()
        svc = _mock_deployment_service(validated=True, workspace_service=None)
        result = builder.before_build(svc, tmp_path, tmp_path)
        assert result is False
        assert builder.has_errors()
        assert any("Workspace" in e for e in builder.get_errors())

    def test_valid_service_returns_true(self, tmp_path):
        builder = PlatformBuilder()
        svc = _mock_deployment_service(validated=True, workspace_service=MagicMock(), build_path=tmp_path / "dep")
        result = builder.before_build(svc, tmp_path, tmp_path)
        assert result is True
        assert not builder.has_errors()

    def test_verbose_adds_message(self, tmp_path):
        builder = PlatformBuilder(verbose=True)
        svc = _mock_deployment_service(validated=True, workspace_service=MagicMock(), build_path=tmp_path / "dep")
        builder.before_build(svc, tmp_path, tmp_path)
        assert builder.has_messages()
        assert any("Pre-build" in m for m in builder.get_messages())


class TestPlatformBuilderBuild:
    def test_dry_run_no_file_write(self, tmp_path):
        builder = PlatformBuilder()
        mock_platform = MagicMock()
        svc = MagicMock()
        svc.get_build_path.return_value = tmp_path / "out"

        with patch.object(builder, "_build_platform", return_value=(mock_platform, ["assembled"])):
            result = builder.build(svc, tmp_path, tmp_path, dry_run=True)

        assert result is True
        assert not builder.has_errors()
        assert builder._last_platform_model is mock_platform
        # No files written
        assert not (tmp_path / "out" / "platform.json").exists()
        # Dry-run messages emitted
        messages = "\n".join(builder.get_messages())
        assert "DRY-RUN" in messages
        assert "platform.json" in messages

    def test_dry_run_reports_build_messages(self, tmp_path):
        builder = PlatformBuilder()
        svc = MagicMock()
        svc.get_build_path.return_value = tmp_path / "out"

        with patch.object(builder, "_build_platform", return_value=(MagicMock(), ["progress note"])):
            builder.build(svc, tmp_path, tmp_path, dry_run=True)

        assert "progress note" in builder.get_messages()

    def test_build_platform_returns_none_returns_false(self, tmp_path):
        builder = PlatformBuilder()
        svc = MagicMock()

        with patch.object(builder, "_build_platform", return_value=(None, ["assembly failed"])):
            result = builder.build(svc, tmp_path, tmp_path, dry_run=False)

        assert result is False
        assert builder._last_platform_model is None

    def test_exception_in_build_returns_false(self, tmp_path):
        builder = PlatformBuilder()
        svc = MagicMock()

        with patch.object(builder, "_build_platform", side_effect=RuntimeError("boom")):
            result = builder.build(svc, tmp_path, tmp_path)

        assert result is False
        assert builder.has_errors()
        assert any("Failed to build platform model" in e for e in builder.get_errors())

    def test_last_platform_model_set_on_success(self, tmp_path):
        builder = PlatformBuilder()
        mock_platform = MagicMock()
        svc = MagicMock()
        svc.get_build_path.return_value = tmp_path / "out"

        with patch.object(builder, "_build_platform", return_value=(mock_platform, [])):
            builder.build(svc, tmp_path, tmp_path, dry_run=True)

        assert builder._last_platform_model is mock_platform

    def test_last_platform_model_none_after_failure(self, tmp_path):
        builder = PlatformBuilder()
        svc = MagicMock()

        with patch.object(builder, "_build_platform", return_value=(None, [])):
            builder.build(svc, tmp_path, tmp_path)

        assert builder._last_platform_model is None


class TestPlatformBuilderAfterBuild:
    def test_dry_run_returns_true(self, tmp_path):
        builder = PlatformBuilder()
        svc = MagicMock()
        result = builder.after_build(svc, tmp_path, tmp_path, dry_run=True)
        assert result is True
        assert not builder.has_errors()

    def test_dry_run_verbose_message(self, tmp_path):
        builder = PlatformBuilder(verbose=True)
        svc = MagicMock()
        builder.after_build(svc, tmp_path, tmp_path, dry_run=True)
        assert builder.has_messages()
        assert any("DRY-RUN" in m for m in builder.get_messages())

    def test_files_present_returns_true(self, tmp_path):
        builder = PlatformBuilder()
        build_dir = tmp_path / "mydeployment-1.0.0"
        build_dir.mkdir()
        (build_dir / "platform.json").write_text("{}")
        (build_dir / "platform.yaml").write_text("")

        svc = MagicMock()
        svc.get_build_path.return_value = build_dir

        result = builder.after_build(svc, tmp_path, tmp_path, dry_run=False)
        assert result is True
        assert not builder.has_errors()

    def test_files_missing_returns_false(self, tmp_path):
        builder = PlatformBuilder()
        build_dir = tmp_path / "mydeployment-1.0.0"
        build_dir.mkdir()
        # No files created

        svc = MagicMock()
        svc.get_build_path.return_value = build_dir

        result = builder.after_build(svc, tmp_path, tmp_path, dry_run=False)
        assert result is False
        assert builder.has_errors()
        assert any("not created" in e for e in builder.get_errors())

    def test_verbose_reports_path_on_success(self, tmp_path):
        builder = PlatformBuilder(verbose=True)
        build_dir = tmp_path / "mydeployment-1.0.0"
        build_dir.mkdir()
        (build_dir / "platform.json").write_text("{}")
        (build_dir / "platform.yaml").write_text("")

        svc = MagicMock()
        svc.get_build_path.return_value = build_dir

        builder.after_build(svc, tmp_path, tmp_path, dry_run=False)
        assert builder.has_messages()


# ---------------------------------------------------------------------------
# Tenant properties/custom merge logic
# ---------------------------------------------------------------------------


def _make_platform_tenant(properties=None, custom=None) -> PlatformTenantModel:
    """Build a minimal PlatformTenantModel with optional properties/custom."""
    return PlatformTenantModel(
        code="acme",
        name="ACME Corporation",
        zones=["eu-west"],
        properties=properties,
        custom=custom,
    )


def _merge(tenant_values, deployment_values):
    """Replicate the builder's base-layer merge: tenant first, deployment overrides."""
    return {**(tenant_values or {}), **(deployment_values or {})} or None


class TestTenantPropertiesMerge:
    """Tests for the tenant properties/custom base-layer merge formula used in _build_spec.

    The formula is: {**tenant, **deployment} — tenant provides the base,
    deployment keys take precedence for any overlap.
    """

    def test_tenant_only_properties(self):
        result = _merge({"tier": "enterprise"}, None)
        assert result == {"tier": "enterprise"}

    def test_deployment_only_properties(self):
        result = _merge(None, {"region": "eu-west"})
        assert result == {"region": "eu-west"}

    def test_deployment_overrides_tenant_on_overlap(self):
        result = _merge({"tier": "enterprise", "billing": "monthly"}, {"tier": "standard", "region": "eu"})
        assert result["tier"] == "standard"  # deployment wins
        assert result["billing"] == "monthly"  # tenant-only key preserved
        assert result["region"] == "eu"  # deployment-only key present

    def test_both_none_returns_none(self):
        assert _merge(None, None) is None

    def test_both_empty_returns_none(self):
        assert _merge({}, {}) is None

    def test_platform_tenant_model_carries_properties(self):
        """PlatformTenantModel.from_tenant_model propagates properties and custom."""
        from strata.models.tenant_model import TenantModel

        model = TenantModel.model_validate(
            {
                "apiVersion": "strata.huybrechts.xyz/v1",
                "kind": "tenant",
                "meta": {"name": "acme"},
                "spec": {
                    "code": "acme",
                    "name": "ACME Corporation",
                    "zones": ["eu-west"],
                    "properties": {"tier": "enterprise"},
                    "custom": {"feature_x": True},
                },
            }
        )
        pt = PlatformTenantModel.from_tenant_model(model)
        assert pt.properties == {"tier": "enterprise"}
        assert pt.custom == {"feature_x": True}


class TestPlatformBuilderNetworks:
    """ADR-0076: kind:network wiring must reach PlatformSpecModel.spec.networks."""

    _WORKSPACE_WITH_NETWORK = {
        "apiVersion": "strata.huybrechts.xyz/v1",
        "kind": "workspace",
        "meta": {"name": "net_ws"},
        "spec": {
            "providers": [
                {"name": "azure", "file": "tests/data/providers/provider-standard.yaml"},
            ],
            "provisioners": [
                {
                    "name": "platform_iac",
                    "provisioner": "terraform",
                    "source": {"source_path": "terraform"},
                }
            ],
            "resources": [
                {"name": "node", "file": "tests/data/resources/resource-standard.yaml"},
            ],
            "topology": [
                {
                    "name": "platform_cluster",
                    "provider": "azure",
                    "provisioner": "platform_iac",
                    "type": "kubernetes",
                    "components": [{"resource": "node"}],
                }
            ],
            "networks": [
                {"name": "net1", "file": "tests/data/network/network-haven.yaml"},
            ],
        },
    }

    def _repo_root(self) -> Path:
        return Path(__file__).parent.parent.parent.parent

    def _load_workspace_service(self, workspace_dict):
        from strata.services.workspace_service import WorkspaceService

        service = WorkspaceService(data=workspace_dict)
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"

        mock_config_service = MagicMock()
        mock_config_service.get_remote_map.return_value = {}
        with patch(
            "strata.services.configuration_service.ConfigurationService.get_instance",
            return_value=mock_config_service,
        ):
            _, success = service.load_workspace_services(objects_path=str(self._repo_root()))
        assert success, "load_workspace_services should succeed"
        return service

    def _mock_deployment_service(self, workspace_service):
        deployment_model = MagicMock()
        deployment_model.meta.name = "net_deployment"
        deployment_model.meta.labels = None
        deployment_model.meta.annotations = None
        deployment_model.spec.lifecycle = None
        deployment_model.spec.tenant = None
        deployment_model.spec.stages = None
        deployment_model.spec.gates = None
        deployment_model.spec.properties = None
        deployment_model.spec.custom = None

        deployment_service = MagicMock()
        deployment_service.model = deployment_model
        deployment_service.get_workspace_service.return_value = workspace_service
        deployment_service.get_environment_service.return_value = None
        return deployment_service

    def test_build_spec_populates_networks(self):
        """A workspace referencing a kind:network file must reach platform.spec.networks."""
        workspace_service = self._load_workspace_service(self._WORKSPACE_WITH_NETWORK)
        deployment_service = self._mock_deployment_service(workspace_service)

        builder = PlatformBuilder()
        spec = builder._build_spec(deployment_service, configuration_model=None, work_path=self._repo_root())

        assert spec.networks is not None
        assert len(spec.networks) == 1
        assert spec.networks[0].name == "haven_network"  # network file's own meta.name (like DNS/firewall)
        assert len(spec.networks[0].networks) >= 1

    def test_build_spec_networks_none_when_not_referenced(self):
        """Workspaces without a networks: section must leave platform.spec.networks unset."""
        workspace_dict = {
            **self._WORKSPACE_WITH_NETWORK,
            "spec": {k: v for k, v in self._WORKSPACE_WITH_NETWORK["spec"].items() if k != "networks"},
        }
        workspace_service = self._load_workspace_service(workspace_dict)
        deployment_service = self._mock_deployment_service(workspace_service)

        builder = PlatformBuilder()
        spec = builder._build_spec(deployment_service, configuration_model=None, work_path=self._repo_root())

        assert spec.networks is None
