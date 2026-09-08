"""Unit tests for HelmDeployer."""

from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import yaml

from strata.deployers.base_deployer import (
    STEP_APPLY,
    STEP_CHECK,
    STEP_DESTROY,
    STEP_OUTPUT,
    STEP_PLAN,
    STEP_PLAN_DESTROY,
    STEP_SETUP,
    STEP_SHOW_PLAN,
)
from strata.deployers.helm_deployer import (
    HelmDeployer,
    HelmModuleTarget,
    _build_value_overrides,
    _escape_set_value,
    _find_expr_leaves,
    _format_set_path,
    _sanitize_repo_name,
)
from strata.models.common_models import ServiceDeployerType
from strata.utils.resolved_values import ResolvedValues

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_deployer(
    tmp_path: Optional[Path] = None,
    force: bool = False,
    verbose: bool = False,
) -> HelmDeployer:
    """Build a HelmDeployer backed by mock services."""
    stage = MagicMock()
    deployment_service = MagicMock()
    configuration_service = MagicMock()
    build_path = (tmp_path / "build") if tmp_path else Path("/build")
    work_path = tmp_path or Path("/work")
    return HelmDeployer(
        stage=stage,
        deployment_service=deployment_service,
        configuration_service=configuration_service,
        build_path=build_path,
        work_path=work_path,
        verbose=verbose,
        force=force,
    )


def _make_target(
    tmp_path: Optional[Path] = None,
    ns_name: str = "prod",
    module_name: str = "nginx",
    chart_ref: str = "myrepo/nginx",
    chart_version: Optional[str] = None,
    repo_url: Optional[str] = None,
    repo_name: Optional[str] = None,
    is_oci: bool = False,
    values_content: str = "{}\n",
) -> HelmModuleTarget:
    """Create a HelmModuleTarget with the given parameters.

    When ``tmp_path`` is given, a real ``values.yaml`` (``values_content``) is
    written to disk so step methods that read ``target.values_file`` (for
    ``${KEY}`` substitution) don't fail on a nonexistent file.
    """
    base = tmp_path or Path("/build")
    values_file = base / ns_name / module_name / "values.yaml"
    meta_file = base / ns_name / module_name / "meta.yaml"
    if tmp_path is not None:
        values_file.parent.mkdir(parents=True, exist_ok=True)
        values_file.write_text(values_content, encoding="utf-8")
    return HelmModuleTarget(
        ns_name=ns_name,
        module_name=module_name,
        values_file=values_file,
        meta_file=meta_file,
        release_name=module_name,
        chart_namespace=ns_name,
        chart_ref=chart_ref,
        chart_version=chart_version,
        repo_url=repo_url,
        repo_name=repo_name,
        is_oci=is_oci,
    )


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


class TestHelmDeployerMetadata:
    def test_deployer_name_is_helm(self):
        d = _make_deployer()
        assert d.get_deployer_name() == "helm"

    def test_get_supported_steps_contains_all_eight(self):
        d = _make_deployer()
        steps = d.get_supported_steps()
        assert STEP_SETUP in steps
        assert STEP_CHECK in steps
        assert STEP_PLAN in steps
        assert STEP_APPLY in steps
        assert STEP_DESTROY in steps
        assert STEP_PLAN_DESTROY in steps
        assert STEP_SHOW_PLAN in steps
        assert STEP_OUTPUT in steps


# ---------------------------------------------------------------------------
# validate_environment
# ---------------------------------------------------------------------------


class TestHelmDeployerValidateEnvironment:
    def test_success_sets_helm_instance(self):
        d = _make_deployer()
        with patch("strata.deployers.helm_deployer.HelmIntegration") as mock_int:
            instance = MagicMock()
            instance.ensure_available.return_value = (True, "")
            instance.get_version.return_value = "3.14.0"
            mock_int.return_value = instance
            ok, msgs = d.validate_environment()
        assert ok is True
        assert d._helm is instance

    def test_unavailable_returns_false(self):
        d = _make_deployer()
        with patch("strata.deployers.helm_deployer.HelmIntegration") as mock_int:
            instance = MagicMock()
            instance.ensure_available.return_value = (False, "helm not in PATH")
            mock_int.return_value = instance
            ok, msgs = d.validate_environment()
        assert ok is False
        assert any("helm not in PATH" in m for m in msgs)


# ---------------------------------------------------------------------------
# validate_workspace — namespace_filter (CLI --namespace / stage.helm_namespaces)
# ---------------------------------------------------------------------------


class TestHelmDeployerValidateWorkspaceNamespaceFilter:
    """namespace_filter (set post-construction by BaseDeployCommand._create_deployer)
    must scope which namespaces validate_workspace() considers — filtered-out
    namespaces should never even be inspected (is_validated() not called)."""

    def _ns_service(self, validated: bool = False):
        ns_service = MagicMock()
        ns_service.is_validated.return_value = validated
        return ns_service

    def test_no_filter_considers_every_namespace(self):
        d = _make_deployer()
        ns_prod = self._ns_service()
        ns_staging = self._ns_service()
        d.deployment_service.get_namespace_services.return_value = {"prod": ns_prod, "staging": ns_staging}
        assert d.namespace_filter is None

        ok, messages = d.validate_workspace()

        assert ok is True
        ns_prod.is_validated.assert_called_once()
        ns_staging.is_validated.assert_called_once()
        assert not any("Namespace filter active" in m for m in messages)

    def test_filter_excludes_non_matching_namespaces(self):
        d = _make_deployer()
        ns_prod = self._ns_service()
        ns_staging = self._ns_service()
        d.deployment_service.get_namespace_services.return_value = {"prod": ns_prod, "staging": ns_staging}
        d.namespace_filter = ["prod"]

        ok, messages = d.validate_workspace()

        assert ok is True
        ns_prod.is_validated.assert_called_once()
        ns_staging.is_validated.assert_not_called()
        assert any("Namespace filter active" in m for m in messages)

    def test_empty_filter_list_is_treated_as_no_filter(self):
        d = _make_deployer()
        ns_prod = self._ns_service()
        d.deployment_service.get_namespace_services.return_value = {"prod": ns_prod}
        d.namespace_filter = []

        ok, messages = d.validate_workspace()

        assert ok is True
        ns_prod.is_validated.assert_called_once()
        assert not any("Namespace filter active" in m for m in messages)


class TestHelmDeployerModuleDirResolution:
    """ADR-0071: values_file/meta_file/chart_ref resolution goes through
    SolutionController.get_module_build_path() when a solution_controller is
    supplied, instead of independently re-deriving deployment_build_path/ns/module
    three separate times."""

    def _setup(self, tmp_path, module_dir, solution_controller=None):
        d = _make_deployer(tmp_path)
        d.solution_controller = solution_controller

        module_dir.mkdir(parents=True, exist_ok=True)
        (module_dir / "values.yaml").write_text("{}\n", encoding="utf-8")
        (module_dir / "meta.yaml").write_text("releaseName: nginx\nnamespace: prod\n", encoding="utf-8")

        (tmp_path / "module.yaml").write_text("", encoding="utf-8")

        module = MagicMock()
        module.meta.name = "nginx"
        module.spec.type = ServiceDeployerType.HELM

        module_ref = MagicMock()
        module_ref.name = "nginx"
        module_ref.file = "module.yaml"

        ns_service = MagicMock()
        ns_service.is_validated.return_value = True
        ns_service.model.spec.modules = [module_ref]

        d.deployment_service.get_namespace_services.return_value = {"prod": ns_service}
        d.deployment_service.get_build_path.return_value = tmp_path / "build"

        mod_service = MagicMock()
        mod_service.is_validated.return_value = True
        mod_service.model = module

        return d, mod_service

    def test_uses_get_module_build_path_when_solution_controller_present(self, tmp_path):
        custom_dir = tmp_path / "custom" / "prod" / "nginx"
        solution_controller = MagicMock()
        solution_controller.get_module_build_path.return_value = custom_dir

        d, mod_service = self._setup(tmp_path, custom_dir, solution_controller=solution_controller)

        with (
            patch("strata.deployers.helm_deployer.resolve_path", return_value=tmp_path / "module.yaml"),
            patch("strata.deployers.helm_deployer.ModuleService.load", return_value=mod_service),
        ):
            ok, messages = d.validate_workspace()

        assert ok is True, messages
        assert d._helm_modules[0].values_file == custom_dir / "values.yaml"
        assert d._helm_modules[0].chart_ref == str(custom_dir)
        solution_controller.get_module_build_path.assert_any_call(d.deployment_service, d.build_path, "prod", "nginx")

    def test_falls_back_to_default_shape_when_no_solution_controller(self, tmp_path):
        default_dir = (tmp_path / "build") / "prod" / "nginx"
        d, mod_service = self._setup(tmp_path, default_dir, solution_controller=None)

        with (
            patch("strata.deployers.helm_deployer.resolve_path", return_value=tmp_path / "module.yaml"),
            patch("strata.deployers.helm_deployer.ModuleService.load", return_value=mod_service),
        ):
            ok, messages = d.validate_workspace()

        assert ok is True, messages
        assert d._helm_modules[0].values_file == default_dir / "values.yaml"
        assert d._helm_modules[0].chart_ref == str(default_dir)


# ---------------------------------------------------------------------------
# Steps require init (_helm must be set)
# ---------------------------------------------------------------------------


class TestHelmDeployerStepsRequireInit:
    """All steps guard via _ready() — they must fail when _helm is None."""

    def test_setup_requires_init(self):
        d = _make_deployer()
        ok, msgs = d.setup()
        assert ok is False
        assert any("not initialized" in m for m in msgs)

    def test_check_requires_init(self):
        d = _make_deployer()
        ok, msgs = d.check()
        assert ok is False
        assert any("not initialized" in m for m in msgs)

    def test_plan_requires_init(self):
        d = _make_deployer()
        ok, msgs = d.plan()
        assert ok is False
        assert any("not initialized" in m for m in msgs)

    def test_apply_requires_init(self):
        d = _make_deployer()
        ok, msgs = d.apply()
        assert ok is False
        assert any("not initialized" in m for m in msgs)

    def test_destroy_requires_init(self):
        d = _make_deployer()
        ok, msgs = d.destroy()
        assert ok is False
        assert any("not initialized" in m for m in msgs)

    def test_plan_destroy_requires_init(self):
        d = _make_deployer()
        ok, msgs = d.plan_destroy()
        assert ok is False
        assert any("not initialized" in m for m in msgs)

    def test_output_requires_init(self):
        d = _make_deployer()
        ok, data, msgs = d.output()
        assert ok is False
        assert any("not initialized" in m for m in msgs)


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------


class TestHelmDeployerSetup:
    def test_no_registry_sources_skips_repo_update(self):
        d = _make_deployer()
        d._helm = MagicMock()
        d._helm_modules = [_make_target(repo_url=None, repo_name=None)]
        ok, msgs = d.setup()
        assert ok is True
        assert any("No chart registries to update" in m for m in msgs)
        d._helm._run_integration.assert_not_called()

    def test_registry_source_calls_repo_add_and_update(self):
        d = _make_deployer()
        d._helm = MagicMock()
        d._helm._run_integration.return_value = MagicMock(returncode=0, stdout="", stderr="")
        d._helm_modules = [
            _make_target(
                repo_url="https://charts.example.com",
                repo_name="charts-example-com",
            )
        ]
        ok, msgs = d.setup()
        assert ok is True
        all_args = [c[0][0] for c in d._helm._run_integration.call_args_list]
        assert any("add" in args for args in all_args)
        assert any("update" in args for args in all_args)

    def test_deduplicates_repos(self):
        d = _make_deployer()
        d._helm = MagicMock()
        d._helm._run_integration.return_value = MagicMock(returncode=0, stdout="", stderr="")
        # Two targets referencing the same repository
        d._helm_modules = [
            _make_target(
                ns_name="prod",
                module_name="nginx",
                repo_url="https://charts.example.com",
                repo_name="charts-example-com",
            ),
            _make_target(
                ns_name="prod",
                module_name="redis",
                repo_url="https://charts.example.com",
                repo_name="charts-example-com",
            ),
        ]
        d.setup()
        # repo add should be called exactly once (deduplicated)
        add_calls = [c for c in d._helm._run_integration.call_args_list if "add" in c[0][0]]
        assert len(add_calls) == 1

    def test_oci_source_skips_repo_add(self):
        d = _make_deployer()
        d._helm = MagicMock()
        d._helm_modules = [
            _make_target(
                repo_url="oci://ghcr.io/some-org/charts",
                repo_name=None,
                is_oci=True,
            )
        ]
        ok, msgs = d.setup()
        assert ok is True
        d._helm._run_integration.assert_not_called()
        assert any("OCI chart(s) detected" in m for m in msgs)

    def test_mixed_oci_and_classic_sources_only_adds_classic(self):
        d = _make_deployer()
        d._helm = MagicMock()
        d._helm._run_integration.return_value = MagicMock(returncode=0, stdout="", stderr="")
        d._helm_modules = [
            _make_target(
                ns_name="prod",
                module_name="nginx",
                repo_url="https://charts.example.com",
                repo_name="charts-example-com",
            ),
            _make_target(
                ns_name="prod",
                module_name="immich",
                repo_url="oci://ghcr.io/immich-app/immich-charts",
                repo_name=None,
                is_oci=True,
            ),
        ]
        d.setup()
        add_calls = [c for c in d._helm._run_integration.call_args_list if "add" in c[0][0]]
        assert len(add_calls) == 1
        assert "charts-example-com" in add_calls[0][0][0]


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


class TestHelmDeployerCheck:
    def test_no_modules_returns_true_with_message(self):
        d = _make_deployer()
        d._helm = MagicMock()
        d._helm_modules = []
        ok, msgs = d.check()
        assert ok is True
        assert any("No helm modules" in m for m in msgs)

    def test_local_chart_calls_helm_lint(self, tmp_path):
        d = _make_deployer(tmp_path)
        d._helm = MagicMock()
        d._helm._run_integration.return_value = MagicMock(returncode=0, stdout="", stderr="")
        d._helm_modules = [_make_target(tmp_path, repo_url=None)]
        ok, msgs = d.check()
        assert ok is True
        all_args = [c[0][0] for c in d._helm._run_integration.call_args_list]
        assert any("lint" in args for args in all_args)

    def test_registry_chart_skips_lint(self):
        d = _make_deployer()
        d._helm = MagicMock()
        d._helm_modules = [_make_target(repo_url="https://charts.example.com", repo_name="charts-example-com")]
        ok, msgs = d.check()
        assert ok is True
        d._helm._run_integration.assert_not_called()
        assert any("skipped" in m for m in msgs)

    def test_lint_failure_returns_false(self, tmp_path):
        d = _make_deployer(tmp_path)
        d._helm = MagicMock()
        d._helm._run_integration.return_value = MagicMock(returncode=1, stdout="", stderr="lint error: invalid chart")
        d._helm_modules = [_make_target(tmp_path, repo_url=None)]
        ok, msgs = d.check()
        assert ok is False

    def test_oci_chart_skips_lint_with_oci_message(self):
        d = _make_deployer()
        d._helm = MagicMock()
        d._helm_modules = [
            _make_target(
                repo_url="oci://ghcr.io/some-org/charts",
                repo_name=None,
                is_oci=True,
            )
        ]
        ok, msgs = d.check()
        assert ok is True
        d._helm._run_integration.assert_not_called()
        assert any("OCI chart" in m and "skipped" in m for m in msgs)


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------


class TestHelmDeployerPlan:
    def test_no_modules_returns_true(self):
        d = _make_deployer()
        d._helm = MagicMock()
        d._helm_modules = []
        ok, msgs = d.plan()
        assert ok is True
        assert any("No helm modules" in m for m in msgs)

    def test_dry_run_command_constructed_correctly(self, tmp_path):
        d = _make_deployer(tmp_path)
        d._helm = MagicMock()
        d._helm._run_integration.return_value = MagicMock(returncode=0, stdout="", stderr="")
        target = _make_target(tmp_path, ns_name="staging", module_name="app")
        d._helm_modules = [target]
        ok, msgs = d.plan()
        assert ok is True
        args = d._helm._run_integration.call_args[0][0]
        assert "upgrade" in args
        assert "--dry-run" in args
        assert "--install" in args
        assert "--namespace" in args
        assert target.chart_namespace in args

    def test_chart_version_included_when_set(self, tmp_path):
        d = _make_deployer(tmp_path)
        d._helm = MagicMock()
        d._helm._run_integration.return_value = MagicMock(returncode=0, stdout="", stderr="")
        d._helm_modules = [_make_target(tmp_path, chart_version="1.2.3")]
        d.plan()
        args = d._helm._run_integration.call_args[0][0]
        assert "--version" in args
        assert "1.2.3" in args

    def test_dry_run_failure_returns_false(self, tmp_path):
        d = _make_deployer(tmp_path)
        d._helm = MagicMock()
        d._helm._run_integration.return_value = MagicMock(returncode=1, stdout="", stderr="chart not found")
        d._helm_modules = [_make_target(tmp_path)]
        ok, msgs = d.plan()
        assert ok is False


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


class TestHelmDeployerApply:
    def test_no_modules_returns_true(self):
        d = _make_deployer()
        d._helm = MagicMock()
        d._helm_modules = []
        ok, msgs = d.apply()
        assert ok is True
        assert any("No helm modules" in m for m in msgs)

    def test_install_command_constructed_correctly(self, tmp_path):
        d = _make_deployer(tmp_path)
        d._helm = MagicMock()
        d._helm._run_integration.return_value = MagicMock(returncode=0, stdout="", stderr="")
        target = _make_target(tmp_path, ns_name="prod", module_name="nginx")
        d._helm_modules = [target]
        ok, msgs = d.apply()
        assert ok is True
        args = d._helm._run_integration.call_args[0][0]
        assert "upgrade" in args
        assert "--install" in args
        assert "--create-namespace" in args
        assert "--namespace" in args
        assert target.chart_namespace in args

    def test_install_failure_returns_false(self, tmp_path):
        d = _make_deployer(tmp_path)
        d._helm = MagicMock()
        d._helm._run_integration.return_value = MagicMock(returncode=1, stdout="", stderr="connection refused")
        d._helm_modules = [_make_target(tmp_path)]
        ok, msgs = d.apply()
        assert ok is False

    def test_chart_version_included_when_set(self, tmp_path):
        d = _make_deployer(tmp_path)
        d._helm = MagicMock()
        d._helm._run_integration.return_value = MagicMock(returncode=0, stdout="", stderr="")
        d._helm_modules = [_make_target(tmp_path, chart_version="2.0.0")]
        d.apply()
        args = d._helm._run_integration.call_args[0][0]
        assert "--version" in args
        assert "2.0.0" in args


# ---------------------------------------------------------------------------
# destroy
# ---------------------------------------------------------------------------


class TestHelmDeployerDestroy:
    def test_destroy_requires_force(self):
        d = _make_deployer(force=False)
        d._helm = MagicMock()
        d._helm_modules = [_make_target()]
        ok, msgs = d.destroy()
        assert ok is False
        assert any("--force" in m for m in msgs)

    def test_no_modules_returns_true(self):
        d = _make_deployer(force=True)
        d._helm = MagicMock()
        d._helm_modules = []
        ok, msgs = d.destroy()
        assert ok is True
        assert any("No helm modules" in m for m in msgs)

    def test_uninstall_command_constructed_correctly(self):
        d = _make_deployer(force=True)
        d._helm = MagicMock()
        d._helm._run_integration.return_value = MagicMock(returncode=0, stdout="", stderr="")
        target = _make_target(ns_name="prod", module_name="nginx")
        d._helm_modules = [target]
        ok, msgs = d.destroy()
        assert ok is True
        args = d._helm._run_integration.call_args[0][0]
        assert "uninstall" in args
        assert "--namespace" in args
        assert target.chart_namespace in args

    def test_uninstall_failure_returns_false(self):
        d = _make_deployer(force=True)
        d._helm = MagicMock()
        d._helm._run_integration.return_value = MagicMock(returncode=1, stdout="", stderr="release not found")
        d._helm_modules = [_make_target()]
        ok, msgs = d.destroy()
        assert ok is False


# ---------------------------------------------------------------------------
# plan_destroy
# ---------------------------------------------------------------------------


class TestHelmDeployerPlanDestroy:
    def test_no_modules_returns_true(self):
        d = _make_deployer()
        d._helm = MagicMock()
        d._helm_modules = []
        ok, msgs = d.plan_destroy()
        assert ok is True
        assert any("No helm modules" in m for m in msgs)

    def test_get_manifest_called_per_module(self):
        d = _make_deployer()
        d._helm = MagicMock()
        d._helm._run_integration.return_value = MagicMock(returncode=0, stdout="apiVersion: v1", stderr="")
        target = _make_target(ns_name="prod", module_name="nginx")
        d._helm_modules = [target]
        ok, msgs = d.plan_destroy()
        assert ok is True
        args = d._helm._run_integration.call_args[0][0]
        assert "get" in args
        assert "manifest" in args
        assert "--namespace" in args
        assert target.chart_namespace in args

    def test_missing_release_treated_as_info_only(self):
        """returncode=1 (release not installed) is not an error — plan_destroy still succeeds."""
        d = _make_deployer()
        d._helm = MagicMock()
        d._helm._run_integration.return_value = MagicMock(returncode=1, stdout="", stderr="release: not found")
        d._helm_modules = [_make_target()]
        ok, msgs = d.plan_destroy()
        assert ok is True
        assert any("not installed" in m for m in msgs)


# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------


class TestHelmDeployerOutput:
    def test_no_modules_returns_empty_dict(self):
        d = _make_deployer()
        d._helm = MagicMock()
        d._helm_modules = []
        ok, data, msgs = d.output()
        assert ok is True
        assert data == {}

    def test_get_values_called_per_module(self):
        d = _make_deployer()
        d._helm = MagicMock()
        d._helm._run_integration.return_value = MagicMock(returncode=0, stdout="replicaCount: 2\n", stderr="")
        target = _make_target(ns_name="prod", module_name="nginx")
        d._helm_modules = [target]
        ok, data, msgs = d.output()
        assert ok is True
        key = f"{target.ns_name}/{target.module_name}"
        assert key in data
        assert data[key] == {"replicaCount": 2}

    def test_failed_get_values_returns_empty_dict_for_module(self):
        d = _make_deployer()
        d._helm = MagicMock()
        d._helm._run_integration.return_value = MagicMock(returncode=1, stdout="", stderr="release: not found")
        target = _make_target(ns_name="prod", module_name="nginx")
        d._helm_modules = [target]
        ok, data, msgs = d.output()
        assert ok is True
        key = f"{target.ns_name}/{target.module_name}"
        assert data[key] == {}


# ---------------------------------------------------------------------------
# show_plan
# ---------------------------------------------------------------------------


class TestHelmDeployerShowPlan:
    def test_always_returns_empty_dict(self):
        d = _make_deployer()
        ok, data, msgs = d.show_plan()
        assert ok is True
        assert data == {}
        assert msgs == []


# ---------------------------------------------------------------------------
# _sanitize_repo_name
# ---------------------------------------------------------------------------


class TestSanitizeRepoName:
    def test_strips_https_scheme(self):
        result = _sanitize_repo_name("https://charts.example.com")
        assert result.startswith("charts")

    def test_strips_http_scheme(self):
        result = _sanitize_repo_name("http://charts.example.com")
        assert result.startswith("charts")

    def test_replaces_dots_with_dashes(self):
        result = _sanitize_repo_name("https://charts.example.com")
        assert "." not in result
        assert "charts-example-com" in result

    def test_truncates_to_20_chars(self):
        result = _sanitize_repo_name("https://long-charts-repository-name.example.com")
        assert len(result) <= 20

    def test_no_leading_trailing_dashes(self):
        result = _sanitize_repo_name("https://charts.example.com")
        assert not result.startswith("-")
        assert not result.endswith("-")


# ---------------------------------------------------------------------------
# _find_expr_leaves
# ---------------------------------------------------------------------------


class TestFindExprLeaves:
    def test_finds_var_leaf(self):
        doc = {"nginx": {"env": {"APP_VERSION": "${var:APP_VERSION}"}}}
        leaves = _find_expr_leaves(doc)
        assert leaves == [(["nginx", "env", "APP_VERSION"], "${var:APP_VERSION}")]

    def test_finds_secret_leaf(self):
        doc = {"nginx": {"env": {"DB_PASSWORD": "${secret:DB_PASSWORD}"}}}
        leaves = _find_expr_leaves(doc)
        assert leaves == [(["nginx", "env", "DB_PASSWORD"], "${secret:DB_PASSWORD}")]

    def test_ignores_plain_values(self):
        doc = {"nginx": {"env": {"TZ": "Europe/Brussels"}}}
        assert _find_expr_leaves(doc) == []

    def test_not_scoped_to_env_keys_unlike_old_bare_token_shape(self):
        """Unlike the retired untyped ${KEY} shape, the typed prefix makes an
        unrestricted full-tree walk safe — references outside 'env' dicts are
        found too (e.g. raw pass-through configuration)."""
        doc = {"nginx": {"configuration": {"image": "${var:IMAGE_TAG}"}}, "topLevel": "${feature:SOME_FLAG}"}
        leaves = _find_expr_leaves(doc)
        assert (["nginx", "configuration", "image"], "${var:IMAGE_TAG}") in leaves
        assert (["topLevel"], "${feature:SOME_FLAG}") in leaves
        assert len(leaves) == 2

    def test_ignores_bare_untyped_token(self):
        """The old ${KEY} (no kind prefix) shape is retired — not matched."""
        doc = {"nginx": {"env": {"DB_PASSWORD": "${DB_PASSWORD}"}}}
        assert _find_expr_leaves(doc) == []

    def test_finds_multiple_leaves(self):
        doc = {
            "nginx": {"env": {"DB_PASSWORD": "${secret:DB_PASSWORD}"}},
            "redis": {"env": {"API_KEY": "${secret:API_KEY}", "TZ": "UTC"}},
        }
        leaves = _find_expr_leaves(doc)
        assert (["nginx", "env", "DB_PASSWORD"], "${secret:DB_PASSWORD}") in leaves
        assert (["redis", "env", "API_KEY"], "${secret:API_KEY}") in leaves
        assert len(leaves) == 2

    def test_walks_list_indices(self):
        doc = {"args": ["--flag", "${var:REGION}"]}
        leaves = _find_expr_leaves(doc)
        assert leaves == [(["args", 1], "${var:REGION}")]

    def test_finds_leaf_with_multiple_refs(self):
        doc = {"url": "postgres://${var:DB_HOST}:${secret:DB_PASSWORD}@host"}
        leaves = _find_expr_leaves(doc)
        assert leaves == [(["url"], "postgres://${var:DB_HOST}:${secret:DB_PASSWORD}@host")]

    def test_handles_empty_and_none_input(self):
        assert _find_expr_leaves({}) == []
        assert _find_expr_leaves(None) == []


# ---------------------------------------------------------------------------
# _escape_set_value
# ---------------------------------------------------------------------------


class TestEscapeSetValue:
    def test_escapes_comma(self):
        assert _escape_set_value("a,b") == "a\\,b"

    def test_escapes_dot(self):
        assert _escape_set_value("a.b") == "a\\.b"

    def test_escapes_equals_and_braces(self):
        assert _escape_set_value("a=b{c}") == "a\\=b\\{c\\}"

    def test_backslash_escaped_first_avoids_double_escaping(self):
        # A literal backslash must become \\ before any other escaping happens,
        # otherwise the backslash inserted for a later char would itself be
        # re-escaped.
        assert _escape_set_value("a\\b,c") == "a\\\\b\\,c"


# ---------------------------------------------------------------------------
# _format_set_path
# ---------------------------------------------------------------------------


class TestFormatSetPath:
    def test_dotted_path(self):
        assert _format_set_path(["nginx", "env", "DB_PASSWORD"]) == "nginx.env.DB_PASSWORD"

    def test_list_index(self):
        assert _format_set_path(["args", 1]) == "args[1]"


# ---------------------------------------------------------------------------
# _build_value_overrides
# ---------------------------------------------------------------------------


class TestBuildValueOverrides:
    def test_no_expressions_returns_original_file_unchanged(self, tmp_path):
        values_file = tmp_path / "values.yaml"
        values_file.write_text("nginx:\n  env:\n    TZ: UTC\n", encoding="utf-8")
        effective_file, args, errors = _build_value_overrides(
            values_file, ResolvedValues(variables={}), "prod", "nginx"
        )
        assert effective_file == values_file
        assert args == []
        assert errors == []

    def test_secret_leaf_produces_set_string_and_leaves_file_unchanged(self, tmp_path):
        values_file = tmp_path / "values.yaml"
        values_file.write_text("nginx:\n  env:\n    DB_PASSWORD: ${secret:DB_PASSWORD}\n", encoding="utf-8")
        resolved = ResolvedValues(secrets={"DB_PASSWORD": "hunter2"})
        effective_file, args, errors = _build_value_overrides(values_file, resolved, "prod", "nginx")
        assert errors == []
        assert effective_file == values_file
        assert args == ["--set-string", "nginx.env.DB_PASSWORD=hunter2"]

    def test_var_leaf_is_written_to_resolved_values_file(self, tmp_path):
        values_file = tmp_path / "values.yaml"
        values_file.write_text("nginx:\n  env:\n    APP_VERSION: ${var:APP_VERSION}\n", encoding="utf-8")
        resolved = ResolvedValues(variables={"APP_VERSION": "1.2.3"})
        effective_file, args, errors = _build_value_overrides(values_file, resolved, "prod", "nginx")
        assert errors == []
        assert args == []
        assert effective_file == tmp_path / "values.resolved.yaml"
        assert effective_file.exists()
        written = yaml.safe_load(effective_file.read_text(encoding="utf-8"))
        assert written == {"nginx": {"env": {"APP_VERSION": "1.2.3"}}}

    def test_feature_leaf_is_written_to_resolved_values_file(self, tmp_path):
        values_file = tmp_path / "values.yaml"
        values_file.write_text("nginx:\n  env:\n    ENABLE_TLS: ${feature:enable_tls}\n", encoding="utf-8")
        resolved = ResolvedValues(features={"enable_tls": True})
        effective_file, args, errors = _build_value_overrides(values_file, resolved, "prod", "nginx")
        assert errors == []
        assert args == []
        written = yaml.safe_load(effective_file.read_text(encoding="utf-8"))
        assert written == {"nginx": {"env": {"ENABLE_TLS": "true"}}}

    def test_leaf_mixing_var_and_secret_routes_as_secret_shaped(self, tmp_path):
        """A leaf containing both a var: and a secret: reference is treated as a
        whole as secret-shaped — routed entirely via --set-string, never written
        to the resolved values file on disk."""
        values_file = tmp_path / "values.yaml"
        values_file.write_text(
            "nginx:\n  env:\n    URL: postgres://${var:DB_HOST}:${secret:DB_PASSWORD}@host\n", encoding="utf-8"
        )
        resolved = ResolvedValues(variables={"DB_HOST": "db.local"}, secrets={"DB_PASSWORD": "hunter2"})
        effective_file, args, errors = _build_value_overrides(values_file, resolved, "prod", "nginx")
        assert errors == []
        assert effective_file == values_file
        assert args == ["--set-string", "nginx.env.URL=postgres://db\\.local:hunter2@host"]

    def test_unresolved_reference_produces_error_and_no_arg(self, tmp_path):
        values_file = tmp_path / "values.yaml"
        values_file.write_text("nginx:\n  env:\n    DB_PASSWORD: ${secret:DB_PASSWORD}\n", encoding="utf-8")
        _effective_file, args, errors = _build_value_overrides(values_file, ResolvedValues(), "prod", "nginx")
        assert args == []
        assert len(errors) == 1
        assert "DB_PASSWORD" in errors[0]

    def test_missing_resolved_values_produces_error(self, tmp_path):
        values_file = tmp_path / "values.yaml"
        values_file.write_text("nginx:\n  env:\n    DB_PASSWORD: ${secret:DB_PASSWORD}\n", encoding="utf-8")
        _effective_file, args, errors = _build_value_overrides(values_file, None, "prod", "nginx")
        assert args == []
        assert len(errors) == 1

    def test_missing_file_produces_error(self, tmp_path):
        values_file = tmp_path / "does-not-exist.yaml"
        _effective_file, args, errors = _build_value_overrides(values_file, ResolvedValues(), "prod", "nginx")
        assert args == []
        assert len(errors) == 1


# ---------------------------------------------------------------------------
# plan()/apply()/check() — end-to-end ${var:}/${secret:}/${feature:} wiring
# ---------------------------------------------------------------------------


class TestHelmDeployerValueSubstitution:
    def test_plan_appends_set_string_for_resolved_secret(self, tmp_path):
        d = _make_deployer(tmp_path)
        d._helm = MagicMock()
        d._helm._run_integration.return_value = MagicMock(returncode=0, stdout="", stderr="")
        d.resolved_values = ResolvedValues(secrets={"DB_PASSWORD": "hunter2"})
        d._helm_modules = [
            _make_target(tmp_path, values_content="nginx:\n  env:\n    DB_PASSWORD: ${secret:DB_PASSWORD}\n")
        ]
        ok, msgs = d.plan()
        assert ok is True
        args = d._helm._run_integration.call_args[0][0]
        assert "--set-string" in args
        assert "nginx.env.DB_PASSWORD=hunter2" in args

    def test_apply_uses_resolved_values_file_for_var_only_substitution(self, tmp_path):
        d = _make_deployer(tmp_path)
        d._helm = MagicMock()
        d._helm._run_integration.return_value = MagicMock(returncode=0, stdout="", stderr="")
        d.resolved_values = ResolvedValues(variables={"APP_VERSION": "1.2.3"})
        target = _make_target(tmp_path, values_content="nginx:\n  env:\n    APP_VERSION: ${var:APP_VERSION}\n")
        d._helm_modules = [target]
        ok, msgs = d.apply()
        assert ok is True
        args = d._helm._run_integration.call_args[0][0]
        assert "--set-string" not in args
        f_index = args.index("-f")
        assert args[f_index + 1] == str(target.values_file.with_name("values.resolved.yaml"))

    def test_apply_fails_on_unresolved_reference_without_calling_helm(self, tmp_path):
        d = _make_deployer(tmp_path)
        d._helm = MagicMock()
        d.resolved_values = ResolvedValues()
        d._helm_modules = [
            _make_target(tmp_path, values_content="nginx:\n  env:\n    DB_PASSWORD: ${secret:DB_PASSWORD}\n")
        ]
        ok, msgs = d.apply()
        assert ok is False
        assert any("DB_PASSWORD" in m for m in msgs)
        d._helm._run_integration.assert_not_called()

    def test_check_appends_set_string_for_local_chart(self, tmp_path):
        d = _make_deployer(tmp_path)
        d._helm = MagicMock()
        d._helm._run_integration.return_value = MagicMock(returncode=0, stdout="", stderr="")
        d.resolved_values = ResolvedValues(secrets={"APP_VERSION": "1.2.3"})
        d._helm_modules = [
            _make_target(
                tmp_path,
                repo_url=None,
                values_content="nginx:\n  env:\n    APP_VERSION: ${secret:APP_VERSION}\n",
            )
        ]
        ok, msgs = d.check()
        assert ok is True
        args = d._helm._run_integration.call_args[0][0]
        assert "--set-string" in args
        assert "nginx.env.APP_VERSION=1\\.2\\.3" in args
