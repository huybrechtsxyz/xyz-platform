"""Tests for DeploymentService.load_environment_only (ADR-0026 Path B).

Verifies the lightweight environment-only loader used by ``deploy show`` and
``values list/get/resolve`` never touches the workspace, while still producing
a fully usable EnvironmentService (declared variables/secrets/features) and
merge provenance — exactly what ``load_deploy_services()`` would set, minus
the workspace/provider/resource/module resolution.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from strata.models.configuration_model import ConfigurationModel
from strata.services.configuration_service import ConfigurationService
from strata.services.deployment_service import DeploymentService


def _repo_root() -> Path:
    return Path(__file__).parent.parent.parent.parent


def _load_deployment() -> DeploymentService:
    # Deliberately bypasses DeploymentService.load()'s process-lifetime L1 cache
    # (strata.utils.service_cache) — each test needs its own fresh instance since
    # load_environment_only() mutates instance state (_environment_service).
    deployment_file = _repo_root() / "tests" / "data" / "deployments" / "deployment-standard.yaml"
    data = yaml.safe_load(deployment_file.read_text(encoding="utf-8"))
    ds = DeploymentService(path=str(deployment_file), data=data)
    ok, errors = ds.validate()
    assert ok, errors
    return ds


def _patched_config_service(config_model=None):
    fake_config = MagicMock()
    fake_config.get_remote_map.return_value = {}
    fake_config.model = config_model
    return patch.object(ConfigurationService, "get_instance", return_value=fake_config)


class TestLoadEnvironmentOnly:
    def test_loads_declared_variables_without_workspace(self) -> None:
        ds = _load_deployment()
        with _patched_config_service():
            ok = ds.load_environment_only(str(_repo_root()))
        assert ok is True
        assert ds._workspace_service is None

        env_service = ds.get_environment_service()
        assert env_service is not None
        keys = {v.key for v in env_service.get_variables()}
        assert keys == {"WORKSPACE", "DATACENTER", "KAMATERA_MANAGER_ID"}

    def test_declared_secrets_are_present_but_unresolved(self) -> None:
        ds = _load_deployment()
        with _patched_config_service():
            assert ds.load_environment_only(str(_repo_root())) is True

        env_service = ds.get_environment_service()
        secret_keys = {s.key for s in env_service.get_secrets()}
        assert "TERRAFORM_API_TOKEN" in secret_keys
        # declarations only — no store call happened here (that's ValueController's job)

    def test_second_call_is_a_cache_hit_noop(self) -> None:
        ds = _load_deployment()
        with _patched_config_service():
            assert ds.load_environment_only(str(_repo_root())) is True
            first_env_service = ds.get_environment_service()
            assert ds.load_environment_only(str(_repo_root())) is True
        assert ds.get_environment_service() is first_env_service

    def test_invalid_objects_path_returns_false(self) -> None:
        ds = _load_deployment()
        ok = ds.load_environment_only(str(_repo_root() / "does-not-exist-dir"))
        assert ok is False

    def test_apply_remote_overrides_works_without_workspace(self) -> None:
        """apply_remote_overrides() only needs the environment service — unlike
        apply_environment_overrides(), it must not require a workspace to be loaded."""
        ds = _load_deployment()
        with _patched_config_service():
            assert ds.load_environment_only(str(_repo_root())) is True

        # No remote overrides declared in the fixture environment — should succeed trivially.
        ok, errors = ds.apply_remote_overrides()
        assert ok is True
        assert errors == []


class TestTenantEnvironmentMerge:
    """Regression tests: ``spec.tenant`` merges the referenced tenant's own
    ``spec.environments[]`` as a base layer ahead of the deployment's own
    environment files, including when a custom ``resolves: tenant`` spec.paths
    convention is declared. Previously ``load_environment_only()`` (and
    ``load_deploy_services()``) hardcoded the built-in ``tenants/{code}.yaml``
    lookup rather than using the config-aware ``resolve_tenant_file_path()``
    helper (already used by the Phase 2 zone check and the build phase), so a
    workspace using a custom tenant path convention would silently find no
    tenant file and merge nothing."""

    @staticmethod
    def _write_env(path: Path, name: str, variables: dict) -> None:
        data = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "environment",
            "meta": {"name": name},
            "spec": {
                "variables": [{"key": key, "store": "constant", "value": value} for key, value in variables.items()]
            },
        }
        path.write_text(yaml.dump(data), encoding="utf-8")

    @staticmethod
    def _write_tenant(path: Path, code: str, environments: list) -> None:
        data = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "tenant",
            "meta": {"name": code},
            "spec": {"code": code, "name": code, "zones": ["eu-west"], "environments": environments},
        }
        path.write_text(yaml.dump(data), encoding="utf-8")

    @staticmethod
    def _load_deployment_with_tenant(tenant_code: str) -> DeploymentService:
        data = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "deployment",
            "meta": {"name": "test_deploy"},
            "spec": {"tenant": tenant_code, "environments": ["deployment.env.yaml"]},
        }
        ds = DeploymentService(data=data)
        ok, errors = ds.validate()
        assert ok, errors
        return ds

    def test_tenant_environments_merged_at_builtin_path(self, tmp_path):
        """Baseline: tenant file at the built-in tenants/{code}.yaml location."""
        self._write_env(tmp_path / "deployment.env.yaml", "deployment_env", {"WORKSPACE": "platform"})
        tenant_dir = tmp_path / "tenants"
        tenant_dir.mkdir()
        self._write_tenant(tenant_dir / "c0062.yaml", "c0062", ["tenants/c0062.env.yaml"])
        self._write_env(tenant_dir / "c0062.env.yaml", "tenant_env", {"customer_code": "c0062"})

        ds = self._load_deployment_with_tenant("c0062")
        with _patched_config_service():
            assert ds.load_environment_only(str(tmp_path)) is True

        env_service = ds.get_environment_service()
        values = {v.key: v.value for v in env_service.get_variables()}
        assert values.get("customer_code") == "c0062"

    def test_tenant_environments_merged_with_custom_path_convention(self, tmp_path):
        """Regression: tenant file resolved via a custom `resolves: tenant`
        spec.paths convention (e.g. customers/{code}/tenant.yaml) must still be
        found, and its environments[] merged in."""
        self._write_env(tmp_path / "deployment.env.yaml", "deployment_env", {"WORKSPACE": "platform"})
        customer_dir = tmp_path / "customers" / "c0062"
        customer_dir.mkdir(parents=True)
        self._write_tenant(customer_dir / "tenant.yaml", "c0062", ["customers/c0062/tenant.env.yaml"])
        self._write_env(customer_dir / "tenant.env.yaml", "tenant_env", {"customer_code": "c0062"})

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
                            "pattern": "customers/{code}/tenant.yaml",
                        }
                    ]
                },
            }
        )

        ds = self._load_deployment_with_tenant("c0062")
        with _patched_config_service(config_model):
            assert ds.load_environment_only(str(tmp_path)) is True

        env_service = ds.get_environment_service()
        values = {v.key: v.value for v in env_service.get_variables()}
        assert values.get("customer_code") == "c0062"

    def test_tenant_file_not_found_at_builtin_path_when_custom_convention_declared(self, tmp_path):
        """Once a custom convention is declared, the built-in tenants/{code}.yaml
        location is no longer consulted — mirrors DeploymentService Phase 2
        behavior for the same convention (see test_services_deployment.py)."""
        self._write_env(tmp_path / "deployment.env.yaml", "deployment_env", {"WORKSPACE": "platform"})
        # Tenant file left at the OLD built-in location only.
        tenant_dir = tmp_path / "tenants"
        tenant_dir.mkdir()
        self._write_tenant(tenant_dir / "c0062.yaml", "c0062", ["tenants/c0062.env.yaml"])
        self._write_env(tenant_dir / "c0062.env.yaml", "tenant_env", {"customer_code": "c0062"})

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
                            "pattern": "customers/{code}/tenant.yaml",
                        }
                    ]
                },
            }
        )

        ds = self._load_deployment_with_tenant("c0062")
        with _patched_config_service(config_model):
            assert ds.load_environment_only(str(tmp_path)) is True

        env_service = ds.get_environment_service()
        values = {v.key: v.value for v in env_service.get_variables()}
        assert "customer_code" not in values
