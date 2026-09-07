#!/usr/bin/env python3
"""Unit tests for DeploymentService."""

from pathlib import Path

import pytest

from strata.models.configuration_model import ConfigurationModel
from strata.models.deployment_model import DeploymentModel
from strata.services.deployment_service import DeploymentService


def _make_config_with_integrations(integrations: list | None = None, remotes: list | None = None) -> ConfigurationModel:
    """Build a ConfigurationModel with the given integrations and remotes."""
    spec: dict = {}
    if integrations:
        spec["integrations"] = integrations
    if remotes:
        spec["remotes"] = remotes
    data = {
        "apiVersion": "strata.huybrechts.xyz/v1",
        "kind": "configuration",
        "meta": {"name": "test_config"},
        "spec": spec,
    }
    return ConfigurationModel.model_validate(data)


def _data(relative_path: str) -> str:
    return str(Path(__file__).parent.parent.parent / "data" / relative_path)


class TestDeploymentService:
    @pytest.fixture
    def service(self):
        return DeploymentService(_data("deployments/deployment-standard.yaml"))

    @pytest.fixture
    def invalid_service(self):
        return DeploymentService(_data("deployments/deployment-invalid.yaml"))

    def test_get_model_class(self, service):
        assert service._get_model_class() == DeploymentModel

    def test_validate_standard(self, service):
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"
        assert errors == []
        assert service.is_validated()
        assert service.model is not None

    def test_validate_sets_model(self, service):
        service.validate()
        assert isinstance(service.model, DeploymentModel)

    def test_get_kind_after_validate(self, service):
        service.validate()
        assert service.get_kind() == "deployment"

    def test_get_name_after_validate(self, service):
        service.validate()
        assert service.get_name() == "valid_platform"

    def test_validate_invalid_file(self, invalid_service):
        is_valid, errors = invalid_service.validate()
        assert not is_valid
        assert len(errors) > 0

    def test_validate_empty_data(self):
        service = DeploymentService(data={})
        is_valid, errors = service.validate()
        assert not is_valid
        assert len(errors) > 0

    def test_validate_in_memory_data(self):
        data = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "deployment",
            "meta": {"name": "test_deploy"},
            "spec": {
                "workspace": {"name": "test_workspace", "file": "workspace.yaml"},
                "environments": ["environment.yaml"],
                "layers": {"segments": {"environment": "dev"}},
                "stages": [{"name": "dev"}],
            },
        }
        service = DeploymentService(data=data)
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"

    def test_validate_dynamic_no_config_model(self, service):
        """Phase 2 without configuration_model always passes."""
        is_valid, errors = service._validate_dynamic()
        assert is_valid
        assert errors == []

    def test_check_environment_refs_exist_before_validate_returns_empty(self):
        """No model loaded yet (Phase 1 not run) -> no crash, empty list."""
        svc = DeploymentService(data={})
        assert svc.check_environment_refs_exist(work_path="/nonexistent") == []

    def test_check_environment_refs_exist_no_environments_entries(self, tmp_path):
        """Partial deployment with no environments[] at all -> nothing to check."""
        data = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "deployment",
            "meta": {"name": "base"},
            "spec": {"partial": True},
        }
        svc = DeploymentService(data=data)
        svc.validate()
        assert svc.check_environment_refs_exist(work_path=str(tmp_path)) == []

    def test_check_environment_refs_exist_missing_file_reports_error(self, tmp_path):
        """Partial deployment whose environments[] entry doesn't exist on disk."""
        data = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "deployment",
            "meta": {"name": "base"},
            "spec": {"partial": True, "environments": ["missing.yaml"]},
        }
        svc = DeploymentService(data=data)
        svc.validate()
        errors = svc.check_environment_refs_exist(work_path=str(tmp_path))
        assert len(errors) == 1
        assert "missing.yaml" in errors[0]

    def test_check_environment_refs_exist_present_file_passes(self, tmp_path):
        """Partial deployment whose environments[] entry exists on disk -> no errors."""
        (tmp_path / "env.yaml").write_text("# placeholder\n")
        data = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "deployment",
            "meta": {"name": "base"},
            "spec": {"partial": True, "environments": ["env.yaml"]},
        }
        svc = DeploymentService(data=data)
        svc.validate()
        assert svc.check_environment_refs_exist(work_path=str(tmp_path)) == []

    def test_tenant_field_absent_phase2_passes(self, tmp_path):
        """When tenant is not set, Phase 2 adds no tenant-related errors."""
        data = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "deployment",
            "meta": {"name": "shared_deploy"},
            "spec": {
                "workspace": {"name": "ws", "file": "workspace.yaml"},
                "environments": ["env.yaml"],
            },
        }
        svc = DeploymentService(data=data)
        svc.validate()  # Phase 1
        is_valid, errors = svc._validate_dynamic(work_path=str(tmp_path))
        # Tenant is absent — no error should carry the "tenant:" label
        assert not any(e.startswith("tenant:") for e in errors)

    def test_tenant_file_missing_fails_phase2(self, tmp_path):
        """Phase 2 fails when tenant file does not exist on disk."""
        data = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "deployment",
            "meta": {"name": "test_deploy"},
            "spec": {
                "workspace": {"name": "ws", "file": "workspace.yaml"},
                "environments": ["env.yaml"],
                "tenant": "acme",
            },
        }
        svc = DeploymentService(data=data)
        svc.validate()  # Phase 1
        is_valid, errors = svc._validate_dynamic(work_path=str(tmp_path))
        assert not is_valid
        assert any("acme" in e for e in errors)

    def test_tenant_file_present_passes_phase2(self, tmp_path):
        """Phase 2 passes when tenant file exists on disk."""
        tenant_dir = tmp_path / "tenants"
        tenant_dir.mkdir()
        (tenant_dir / "acme.yaml").write_text("# placeholder\n")
        env_file = tmp_path / "env.yaml"
        env_file.write_text("# placeholder\n")
        data = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "deployment",
            "meta": {"name": "test_deploy"},
            "spec": {
                "workspace": {"name": "ws", "file": "workspace.yaml"},
                "environments": ["env.yaml"],
                "tenant": "acme",
            },
        }
        svc = DeploymentService(data=data)
        svc.validate()  # Phase 1
        is_valid, errors = svc._validate_dynamic(work_path=str(tmp_path))
        # workspace.yaml is still missing, but no tenant error
        assert not any("acme" in e for e in errors)

    def test_tenant_file_resolved_via_custom_configured_convention(self, tmp_path):
        """When configuration.spec.paths declares a resolves: tenant convention,
        Phase 2 looks for the tenant file at that custom location instead of
        the built-in tenants/{code}.yaml."""
        from strata.models.configuration_model import ConfigurationModel

        # Custom layout: customers/{code}/customer.yaml — NOT tenants/acme.yaml
        customer_dir = tmp_path / "customers" / "acme"
        customer_dir.mkdir(parents=True)
        (customer_dir / "customer.yaml").write_text("# placeholder\n")
        env_file = tmp_path / "env.yaml"
        env_file.write_text("# placeholder\n")

        config_model = ConfigurationModel.model_validate(
            {
                "apiVersion": "strata.huybrechts.xyz/v1",
                "kind": "configuration",
                "meta": {"name": "cfg"},
                "spec": {
                    "paths": [
                        {
                            "name": "tenant-location",
                            "resolves": "tenant",
                            "scope": "customers/**",
                            "pattern": "customers/{code}/customer.yaml",
                        }
                    ]
                },
            }
        )

        data = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "deployment",
            "meta": {"name": "test_deploy"},
            "spec": {
                "workspace": {"name": "ws", "file": "workspace.yaml"},
                "environments": ["env.yaml"],
                "tenant": "acme",
            },
        }
        svc = DeploymentService(data=data)
        svc.validate()  # Phase 1
        is_valid, errors = svc._validate_dynamic(work_path=str(tmp_path), configuration_model=config_model)
        # The tenant file exists at the CUSTOM location, so no tenant-file error
        assert not any("acme" in e for e in errors)

    def test_tenant_file_missing_at_custom_configured_location_fails(self, tmp_path):
        """Custom convention declared but the file isn't at that custom location
        (e.g. it's still sitting at the old tenants/acme.yaml) -> Phase 2 fails,
        proving the built-in path is no longer consulted once a custom
        convention is declared."""
        from strata.models.configuration_model import ConfigurationModel

        # File left at the OLD built-in location — should NOT be found.
        tenant_dir = tmp_path / "tenants"
        tenant_dir.mkdir()
        (tenant_dir / "acme.yaml").write_text("# placeholder\n")

        config_model = ConfigurationModel.model_validate(
            {
                "apiVersion": "strata.huybrechts.xyz/v1",
                "kind": "configuration",
                "meta": {"name": "cfg"},
                "spec": {
                    "paths": [
                        {
                            "name": "tenant-location",
                            "resolves": "tenant",
                            "scope": "customers/**",
                            "pattern": "customers/{code}/customer.yaml",
                        }
                    ]
                },
            }
        )

        data = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "deployment",
            "meta": {"name": "test_deploy"},
            "spec": {
                "workspace": {"name": "ws", "file": "workspace.yaml"},
                "environments": ["env.yaml"],
                "tenant": "acme",
            },
        }
        svc = DeploymentService(data=data)
        svc.validate()  # Phase 1
        is_valid, errors = svc._validate_dynamic(work_path=str(tmp_path), configuration_model=config_model)
        assert not is_valid
        assert any("customers" in e and "acme" in e for e in errors)


class TestValidateLayers:
    """Tests for DeploymentService.validate_layers() — public wrapper around
    _validate_deployment_layers() (ADR-0072), used by PromoteController so a
    layer-resolution failure can be checked without running full Phase 2
    validation (see promote_controller.py's _load_registered_deployments())."""

    def _config_with_hub_scheme(self) -> ConfigurationModel:
        return ConfigurationModel.model_validate(
            {
                "apiVersion": "strata.huybrechts.xyz/v1",
                "kind": "configuration",
                "meta": {"name": "test_config"},
                "spec": {
                    "paths": [
                        {
                            "name": "hub-scheme",
                            "scope": "deploy/hubs/*",
                            "pattern": "deploy/hubs/{hub}/{ring}",
                            "resolves": "layers",
                            "segments": [{"name": "hub"}, {"name": "ring"}],
                        }
                    ]
                },
            }
        )

    def _deployment(self, path: str, follows: str = "hub-scheme") -> DeploymentService:
        data = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "deployment",
            "meta": {"name": "test_deploy"},
            "spec": {"layers": {"follows": follows}},
        }
        svc = DeploymentService(path=path, data=data)
        svc.validate()  # Phase 1
        return svc

    def test_no_configuration_model_returns_empty_list(self, tmp_path):
        svc = self._deployment(str(tmp_path / "deploy" / "hubs" / "hub1" / "prd" / "deploy.yaml"))
        assert svc.validate_layers(None, str(tmp_path)) == []

    def test_valid_layers_returns_no_errors(self, tmp_path):
        config = self._config_with_hub_scheme()
        svc = self._deployment(str(tmp_path / "deploy" / "hubs" / "hub1" / "prd" / "deploy.yaml"))
        assert svc.validate_layers(config, str(tmp_path)) == []

    def test_unknown_follows_returns_error(self, tmp_path):
        config = self._config_with_hub_scheme()
        svc = self._deployment(
            str(tmp_path / "deploy" / "hubs" / "hub1" / "prd" / "deploy.yaml"), follows="nonexistent-scheme"
        )
        errors = svc.validate_layers(config, str(tmp_path))
        assert len(errors) == 1
        assert "nonexistent-scheme" in errors[0]

    def test_matches_private_method_directly(self, tmp_path):
        """The public wrapper must not diverge from _validate_deployment_layers()
        (ADR-0073's convention rule: one shared implementation, not a second copy)."""
        config = self._config_with_hub_scheme()
        svc = self._deployment(str(tmp_path / "deploy" / "hubs" / "hub1" / "prd" / "deploy.yaml"))
        assert svc.validate_layers(config, str(tmp_path)) == svc._validate_deployment_layers(config, str(tmp_path))


class TestValidateSyncStages:
    """Tests for _validate_sync_stages Phase-6 cross-reference validation."""

    def _make_deployment(self, stages=None, workspace_file="workspace.yaml"):
        data = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "deployment",
            "meta": {"name": "test_sync_deploy"},
            "spec": {
                "workspace": {"name": "ws", "file": workspace_file},
                "environments": ["env.yaml"],
                "stages": stages or [],
            },
        }
        svc = DeploymentService(data=data)
        svc.validate()  # Phase 1 — populate self.model
        return svc

    # ------------------------------------------------------------------ #
    # No sync fields → no errors regardless of what config provides       #
    # ------------------------------------------------------------------ #

    def test_no_sync_stages_passes(self):
        svc = self._make_deployment(stages=[{"name": "infra", "provisioner": "platform_iac"}])
        errors = svc._validate_sync_stages(configuration_model=None, work_path=None)
        assert errors == []

    def test_empty_stages_passes(self):
        svc = self._make_deployment(stages=[])
        errors = svc._validate_sync_stages(configuration_model=None, work_path=None)
        assert errors == []

    # ------------------------------------------------------------------ #
    # backend.integration validation                                       #
    # ------------------------------------------------------------------ #

    def test_valid_integration_with_sync_capability_passes(self):
        config = _make_config_with_integrations(
            integrations=[{"name": "argocd", "type": "argocd", "capabilities": ["sync"]}]
        )
        svc = self._make_deployment(
            stages=[
                {
                    "name": "gitops",
                    "provisioner": "argocd",
                    "backend": {"integration": "argocd", "remote": "gitops-repo"},
                }
            ]
        )
        svc._repo_map = {"gitops-repo": "/tmp/repo"}
        errors = svc._validate_sync_stages(configuration_model=config, work_path=None)
        assert errors == []

    def test_missing_integration_reports_error(self):
        config = _make_config_with_integrations(
            integrations=[{"name": "argocd", "type": "argocd", "capabilities": ["sync"]}]
        )
        svc = self._make_deployment(
            stages=[
                {
                    "name": "gitops",
                    "provisioner": "flux",
                    "backend": {"integration": "flux_int", "remote": "gitops-repo"},
                }
            ]
        )
        svc._repo_map = {"gitops-repo": "/tmp/repo"}
        errors = svc._validate_sync_stages(configuration_model=config, work_path=None)
        assert any("flux_int" in e and "not found" in e for e in errors), errors

    def test_integration_without_sync_capability_reports_error(self):
        config = _make_config_with_integrations(
            integrations=[{"name": "argocd", "type": "argocd", "capabilities": ["api"]}]
        )
        svc = self._make_deployment(
            stages=[
                {
                    "name": "gitops",
                    "provisioner": "argocd",
                    "backend": {"integration": "argocd", "remote": "gitops-repo"},
                }
            ]
        )
        svc._repo_map = {"gitops-repo": "/tmp/repo"}
        errors = svc._validate_sync_stages(configuration_model=config, work_path=None)
        assert any("sync" in e and "capability" in e for e in errors), errors

    def test_no_config_model_skips_integration_check(self):
        """When configuration_model is None integration cross-ref is not validated."""
        svc = self._make_deployment(
            stages=[
                {
                    "name": "gitops",
                    "provisioner": "argocd",
                    "backend": {"integration": "does_not_exist", "remote": "gitops-repo"},
                }
            ]
        )
        svc._repo_map = {"gitops-repo": "/tmp/repo"}
        errors = svc._validate_sync_stages(configuration_model=None, work_path=None)
        assert errors == []

    # ------------------------------------------------------------------ #
    # backend.remote validation                                            #
    # ------------------------------------------------------------------ #

    def test_valid_remote_passes(self):
        svc = self._make_deployment(
            stages=[
                {
                    "name": "gitops",
                    "provisioner": "argocd",
                    "backend": {"integration": "argocd", "remote": "my-repo"},
                }
            ]
        )
        svc._repo_map = {"my-repo": "/path/to/repo"}
        errors = svc._validate_sync_stages(configuration_model=None, work_path=None)
        assert errors == []

    def test_unknown_remote_reports_error(self):
        svc = self._make_deployment(
            stages=[
                {
                    "name": "gitops",
                    "provisioner": "argocd",
                    "backend": {"integration": "argocd", "remote": "missing-repo"},
                }
            ]
        )
        svc._repo_map = {"other-repo": "/path/to/other"}
        errors = svc._validate_sync_stages(configuration_model=None, work_path=None)
        assert any("missing-repo" in e and "not a registered remote" in e for e in errors), errors

    def test_remote_in_config_repo_map_passes(self):
        """Remotes from configuration.get_remote_map() are valid too."""
        config = _make_config_with_integrations(
            integrations=[{"name": "argocd", "type": "argocd", "capabilities": ["sync"]}],
            remotes=[
                {
                    "name": "config-repo",
                    "type": "gitops",
                    "repository": "https://example.com/cfg",
                    "reference": "main",
                    "source_path": "cfg",
                    "deploy_path": "cfg",
                }
            ],
        )
        svc = self._make_deployment(
            stages=[
                {
                    "name": "gitops",
                    "provisioner": "argocd",
                    "backend": {"integration": "argocd", "remote": "config-repo"},
                }
            ]
        )
        svc._repo_map = {}  # config provides the remote, not solution
        errors = svc._validate_sync_stages(configuration_model=config, work_path=None)
        assert errors == []

    # ------------------------------------------------------------------ #
    # namespace validation (best-effort)                                   #
    # ------------------------------------------------------------------ #

    def test_namespace_without_workspace_skips_gracefully(self, tmp_path):
        """When workspace YAML is missing, namespace validation is silently skipped."""
        svc = self._make_deployment(
            stages=[{"name": "gitops", "provisioner": "argocd", "namespace": "backend"}],
            workspace_file="workspace.yaml",
        )
        # workspace.yaml does NOT exist in tmp_path
        errors = svc._validate_sync_stages(configuration_model=None, work_path=str(tmp_path))
        assert errors == []

    def test_valid_namespace_in_workspace_passes(self, tmp_path):
        """When namespace is defined in workspace.spec.namespaces, no error is reported."""
        ws_data = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "workspace",
            "meta": {"name": "test_ws"},
            "spec": {
                "namespaces": [
                    {"name": "backend", "file": "namespaces/backend.yaml"},
                    {"name": "frontend", "file": "namespaces/frontend.yaml"},
                ],
                "provisioners": [],
            },
        }
        import yaml

        ws_file = tmp_path / "workspace.yaml"
        ws_file.write_text(yaml.dump(ws_data))

        svc = self._make_deployment(
            stages=[{"name": "gitops", "provisioner": "argocd", "namespace": "backend"}],
            workspace_file=str(ws_file),
        )
        errors = svc._validate_sync_stages(configuration_model=None, work_path=str(tmp_path))
        assert errors == []

    def test_unknown_namespace_reports_error(self, tmp_path):
        """When namespace is NOT in workspace.spec.namespaces, an error is reported."""
        ws_data = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "workspace",
            "meta": {"name": "test_ws"},
            "spec": {
                "namespaces": [{"name": "backend", "file": "namespaces/backend.yaml"}],
                "provisioners": [],
            },
        }
        import yaml

        ws_file = tmp_path / "workspace.yaml"
        ws_file.write_text(yaml.dump(ws_data))

        svc = self._make_deployment(
            stages=[{"name": "gitops", "provisioner": "argocd", "namespace": "unknown-ns"}],
            workspace_file=str(ws_file),
        )
        errors = svc._validate_sync_stages(configuration_model=None, work_path=str(tmp_path))
        assert any("unknown-ns" in e and "not found" in e for e in errors), errors


class TestValidateHelmStageNamespaces:
    """Tests for _validate_helm_stage_namespaces — stage.helm_namespaces allowlist.

    Unrelated to TestValidateSyncStages' namespace checks above: this validates
    the plural, helm-provisioner-only 'helm_namespaces' field, not the singular
    sync-only 'namespace' field.
    """

    def _make_deployment(self, stages=None, workspace_file="workspace.yaml"):
        data = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "deployment",
            "meta": {"name": "test_helm_ns_deploy"},
            "spec": {
                "workspace": {"name": "ws", "file": workspace_file},
                "environments": ["env.yaml"],
                "stages": stages or [],
            },
        }
        svc = DeploymentService(data=data)
        svc.validate()  # Phase 1 — populate self.model
        return svc

    def test_no_helm_namespaces_passes(self):
        svc = self._make_deployment(stages=[{"name": "apps", "provisioner": "helm"}])
        errors = svc._validate_helm_stage_namespaces(work_path=None, configuration_model=None)
        assert errors == []

    def test_empty_stages_passes(self):
        svc = self._make_deployment(stages=[])
        errors = svc._validate_helm_stage_namespaces(work_path=None, configuration_model=None)
        assert errors == []

    def test_without_workspace_skips_gracefully(self, tmp_path):
        """When workspace YAML is missing, namespace validation is silently skipped."""
        svc = self._make_deployment(
            stages=[{"name": "apps", "provisioner": "helm", "helm_namespaces": ["immich"]}],
            workspace_file="workspace.yaml",
        )
        # workspace.yaml does NOT exist in tmp_path
        errors = svc._validate_helm_stage_namespaces(work_path=str(tmp_path), configuration_model=None)
        assert errors == []

    def test_valid_helm_namespaces_pass(self, tmp_path):
        ws_data = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "workspace",
            "meta": {"name": "test_ws"},
            "spec": {
                "namespaces": [
                    {"name": "immich", "file": "namespaces/immich.yaml"},
                    {"name": "media", "file": "namespaces/media.yaml"},
                ],
                "provisioners": [],
            },
        }
        import yaml

        ws_file = tmp_path / "workspace.yaml"
        ws_file.write_text(yaml.dump(ws_data))

        svc = self._make_deployment(
            stages=[{"name": "apps", "provisioner": "helm", "helm_namespaces": ["immich", "media"]}],
            workspace_file=str(ws_file),
        )
        errors = svc._validate_helm_stage_namespaces(work_path=str(tmp_path), configuration_model=None)
        assert errors == []

    def test_unknown_helm_namespace_reports_error(self, tmp_path):
        ws_data = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "workspace",
            "meta": {"name": "test_ws"},
            "spec": {
                "namespaces": [{"name": "immich", "file": "namespaces/immich.yaml"}],
                "provisioners": [],
            },
        }
        import yaml

        ws_file = tmp_path / "workspace.yaml"
        ws_file.write_text(yaml.dump(ws_data))

        svc = self._make_deployment(
            stages=[{"name": "apps", "provisioner": "helm", "helm_namespaces": ["immich", "documents"]}],
            workspace_file=str(ws_file),
        )
        errors = svc._validate_helm_stage_namespaces(work_path=str(tmp_path), configuration_model=None)
        assert any("documents" in e and "not found" in e for e in errors), errors
        # Only the unknown entry is reported, not the known-good "immich"
        assert not any(e.startswith("Stage 'apps': helm_namespaces ['immich'") for e in errors), errors
