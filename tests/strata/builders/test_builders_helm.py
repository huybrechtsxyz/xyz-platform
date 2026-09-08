"""Unit tests for HelmBuilder."""

from unittest.mock import MagicMock, patch

import yaml

from strata.builders.helm_builder import HelmBuilder
from strata.models.common_models import ServiceDeployerType
from strata.models.module_model import (
    ModuleMountModel,
    ModuleServiceEnvironmentModel,
    ModuleServiceModel,
)

# ---------------------------------------------------------------------------
# Helpers — mirror test_builders_compose.py conventions exactly
# ---------------------------------------------------------------------------


def _mock_deployment_service(validated=True, build_path=None, namespace_services=None):
    svc = MagicMock()
    svc.is_validated.return_value = validated
    svc.get_workspace_service.return_value = MagicMock()
    if build_path:
        svc.get_build_path.return_value = build_path
    svc.get_namespace_services.return_value = namespace_services or {}
    return svc


def _mock_namespace_service(module_refs=None):
    """Return a mock NamespaceService with the given module references."""
    ns_svc = MagicMock()
    ns_svc.is_validated.return_value = True
    ns_svc.model = MagicMock()
    ns_svc.model.spec = MagicMock()
    ns_svc.model.spec.modules = module_refs or []
    return ns_svc


def _module_ref(name: str, file: str):
    ref = MagicMock()
    ref.name = name
    ref.file = file
    return ref


def _make_helm_module(
    name: str, services, release_name=None, kubernetes_namespace=None, source=None, configuration=None
):
    """Return a mock ModuleModel with type=helm and the given services."""
    mod = MagicMock()
    mod.meta = MagicMock()
    mod.meta.name = name
    mod.spec = MagicMock()
    mod.spec.type = ServiceDeployerType.HELM
    mod.spec.services = services
    mod.spec.release_name = release_name
    mod.spec.kubernetes_namespace = kubernetes_namespace
    mod.spec.configuration = configuration
    # Default source: git-based (no chart fields)
    if source is None:
        src = MagicMock()
        src.chart_repository = None
        src.chart_name = None
        src.chart_version = None
        src.source_path = None
        src.repository = None
        mod.spec.source = src
    else:
        mod.spec.source = source
    return mod


def _make_service(
    name: str,
    image=None,
    environment=None,
    mounts=None,
    configuration=None,
):
    svc = MagicMock(spec=ModuleServiceModel)
    svc.name = name
    svc.image = image
    svc.environment = environment
    svc.mounts = mounts
    svc.configuration = configuration
    return svc


def _make_mod_service(validated=True, module=None):
    ms = MagicMock()
    ms.is_validated.return_value = validated
    ms.model = module
    ms.get_validation_errors.return_value = ["validation error"]
    return ms


def _make_pvc_mount(name="data", storage_class="fast-ssd", access_mode="ReadWriteOnce", storage_size="10Gi"):
    """Return a mock ModuleMountModel representing a Kubernetes PVC."""
    mount = MagicMock(spec=ModuleMountModel)
    mount.volume_ref = None
    mount.source_path = None
    mount.storage_class = storage_class
    mount.access_mode = access_mode
    mount.storage_size = storage_size
    mount.name = name
    mount.target_path = "/var/data"
    mount.type = "volume"
    return mount


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------


class TestHelmBuilderInit:
    def test_defaults(self):
        builder = HelmBuilder()
        assert builder.verbose is False
        assert not builder.has_errors()
        assert not builder.has_messages()

    def test_verbose(self):
        builder = HelmBuilder(verbose=True)
        assert builder.verbose is True


# ---------------------------------------------------------------------------
# before_build
# ---------------------------------------------------------------------------


class TestHelmBuilderBeforeBuild:
    def test_not_validated_returns_false(self, tmp_path):
        builder = HelmBuilder()
        svc = _mock_deployment_service(validated=False)
        assert builder.before_build(svc, tmp_path, tmp_path) is False
        assert any("not validated" in e for e in builder.get_errors())

    def test_no_workspace_service_returns_false(self, tmp_path):
        builder = HelmBuilder()
        svc = _mock_deployment_service(validated=True)
        svc.get_workspace_service.return_value = None
        assert builder.before_build(svc, tmp_path, tmp_path) is False
        assert any("Workspace" in e for e in builder.get_errors())

    def test_valid_service_returns_true(self, tmp_path):
        builder = HelmBuilder()
        svc = _mock_deployment_service(validated=True)
        assert builder.before_build(svc, tmp_path, tmp_path) is True
        assert not builder.has_errors()

    def test_verbose_emits_message(self, tmp_path):
        builder = HelmBuilder(verbose=True)
        svc = _mock_deployment_service(validated=True)
        builder.before_build(svc, tmp_path, tmp_path)
        assert any("validation passed" in m for m in builder.get_messages())


# ---------------------------------------------------------------------------
# after_build
# ---------------------------------------------------------------------------


class TestHelmBuilderAfterBuild:
    def test_always_true(self, tmp_path):
        builder = HelmBuilder()
        svc = _mock_deployment_service()
        assert builder.after_build(svc, tmp_path, tmp_path) is True

    def test_dry_run_verbose_emits_message(self, tmp_path):
        builder = HelmBuilder(verbose=True)
        svc = _mock_deployment_service()
        builder.after_build(svc, tmp_path, tmp_path, dry_run=True)
        assert any("DRY-RUN" in m for m in builder.get_messages())


# ---------------------------------------------------------------------------
# _validate_expr_refs (ADR-0075)
# ---------------------------------------------------------------------------


def _mock_env_service(variables=None, features=None, secret_keys=None):
    env_service = MagicMock()
    env_service.model.spec.secrets = [MagicMock(key=k) for k in (secret_keys or [])]
    env_service.get_variables.return_value = [MagicMock(key=k) for k in (variables or [])]
    env_service.get_features.return_value = [MagicMock(key=k) for k in (features or [])]
    return env_service


class TestHelmBuilderValidateExprRefs:
    def test_no_refs_produces_no_errors(self):
        builder = HelmBuilder()
        deployment_service = MagicMock()
        builder._validate_expr_refs({"nginx": {"env": {"TZ": "UTC"}}}, deployment_service, "prod", "nginx")
        assert builder.get_errors() == []
        deployment_service.get_environment_service.assert_not_called()

    def test_declared_var_ref_produces_no_error(self):
        builder = HelmBuilder()
        deployment_service = MagicMock()
        deployment_service.get_environment_service.return_value = _mock_env_service(variables=["APP_VERSION"])
        values_doc = {"nginx": {"env": {"APP_VERSION": "${var:APP_VERSION}"}}}
        builder._validate_expr_refs(values_doc, deployment_service, "prod", "nginx")
        assert builder.get_errors() == []

    def test_declared_secret_ref_produces_no_error(self):
        builder = HelmBuilder()
        deployment_service = MagicMock()
        deployment_service.get_environment_service.return_value = _mock_env_service(secret_keys=["DB_PASSWORD"])
        values_doc = {"nginx": {"env": {"DB_PASSWORD": "${secret:DB_PASSWORD}"}}}
        builder._validate_expr_refs(values_doc, deployment_service, "prod", "nginx")
        assert builder.get_errors() == []

    def test_declared_feature_ref_produces_no_error(self):
        builder = HelmBuilder()
        deployment_service = MagicMock()
        deployment_service.get_environment_service.return_value = _mock_env_service(features=["enable_tls"])
        values_doc = {"nginx": {"env": {"ENABLE_TLS": "${feature:enable_tls}"}}}
        builder._validate_expr_refs(values_doc, deployment_service, "prod", "nginx")
        assert builder.get_errors() == []

    def test_undeclared_ref_produces_error(self):
        builder = HelmBuilder()
        deployment_service = MagicMock()
        deployment_service.get_environment_service.return_value = _mock_env_service()
        values_doc = {"nginx": {"env": {"DB_PASSWORD": "${secret:DB_PASSWORD}"}}}
        builder._validate_expr_refs(values_doc, deployment_service, "prod", "nginx")
        errors = builder.get_errors()
        assert len(errors) == 1
        assert "DB_PASSWORD" in errors[0]
        assert "prod" in errors[0] and "nginx" in errors[0]

    def test_no_environment_service_produces_no_error(self):
        """No environment declared at all — nothing to cross-check against, so this
        is not itself flagged (an unresolvable reference will still fail loud at
        deploy time)."""
        builder = HelmBuilder()
        deployment_service = MagicMock()
        deployment_service.get_environment_service.return_value = None
        values_doc = {"nginx": {"env": {"DB_PASSWORD": "${secret:DB_PASSWORD}"}}}
        builder._validate_expr_refs(values_doc, deployment_service, "prod", "nginx")
        assert builder.get_errors() == []


# ---------------------------------------------------------------------------
# build — no-op paths
# ---------------------------------------------------------------------------


class TestHelmBuilderBuildNoOp:
    def test_no_namespaces_returns_true(self, tmp_path):
        builder = HelmBuilder()
        svc = _mock_deployment_service(namespace_services={})
        assert builder.build(svc, tmp_path, tmp_path) is True
        assert not builder.has_errors()

    def test_namespace_without_modules_skipped(self, tmp_path):
        builder = HelmBuilder()
        ns_svc = _mock_namespace_service(module_refs=[])
        dep_svc = _mock_deployment_service(
            build_path=tmp_path,
            namespace_services={"ns1": ns_svc},
        )
        assert builder.build(dep_svc, tmp_path, tmp_path) is True
        assert not builder.has_errors()
        assert not (tmp_path / "ns1").exists()

    def test_non_helm_module_skipped(self, tmp_path):
        """A module with type != helm should produce no output files."""
        non_helm_module = _make_helm_module("mymod", services=[_make_service("web")])
        non_helm_module.spec.type = ServiceDeployerType.COMPOSE  # override to compose

        mod_service = _make_mod_service(module=non_helm_module)
        mod_ref = _module_ref("mymod", "dummy.yaml")

        ns_svc = _mock_namespace_service([mod_ref])
        dep_svc = _mock_deployment_service(build_path=tmp_path, namespace_services={"ns1": ns_svc})

        module_path = tmp_path / "dummy.yaml"
        module_path.write_text("")

        builder = HelmBuilder()
        with (
            patch("strata.builders.helm_builder.resolve_path") as mock_rp,
            patch("strata.builders.helm_builder.ModuleService.load", return_value=mod_service),
        ):
            mock_rp.return_value = module_path
            result = builder.build(dep_svc, tmp_path, tmp_path)

        assert result is True
        assert not (tmp_path / "ns1" / "mymod" / "values.yaml").exists()
        assert not (tmp_path / "ns1" / "mymod" / "meta.yaml").exists()


# ---------------------------------------------------------------------------
# build — output generation
# ---------------------------------------------------------------------------


class TestHelmBuilderBuildOutput:
    """Verify generated values.yaml and meta.yaml contents."""

    def _run_build(
        self,
        tmp_path,
        services,
        namespace="testns",
        module_name="mymod",
        release_name=None,
        kubernetes_namespace=None,
        source=None,
        configuration=None,
    ):
        """Helper: build one namespace with one helm module; return (values_doc, meta_doc)."""
        helm_module = _make_helm_module(
            module_name,
            services=services,
            release_name=release_name,
            kubernetes_namespace=kubernetes_namespace,
            source=source,
            configuration=configuration,
        )
        mod_service = _make_mod_service(module=helm_module)
        mod_ref = _module_ref(module_name, "module.yaml")

        ns_svc = _mock_namespace_service([mod_ref])
        dep_svc = _mock_deployment_service(build_path=tmp_path, namespace_services={namespace: ns_svc})

        module_path = tmp_path / "module.yaml"
        module_path.write_text("")

        builder = HelmBuilder()
        with (
            patch("strata.builders.helm_builder.resolve_path") as mock_rp,
            patch("strata.builders.helm_builder.ModuleService.load", return_value=mod_service),
        ):
            mock_rp.return_value = module_path
            ok = builder.build(dep_svc, tmp_path, tmp_path)

        assert ok is True, builder.get_errors()

        values_file = tmp_path / namespace / module_name / "values.yaml"
        meta_file = tmp_path / namespace / module_name / "meta.yaml"
        assert values_file.exists(), f"values.yaml not written: {list((tmp_path / namespace).rglob('*'))}"
        assert meta_file.exists(), "meta.yaml not written"

        return yaml.safe_load(values_file.read_text()), yaml.safe_load(meta_file.read_text())

    # --- values.yaml: service key naming ---

    def test_minimal_service(self, tmp_path):
        """A service with only an image produces a top-level key in values.yaml."""
        svc = _make_service("web", image="nginx:alpine")
        values, _ = self._run_build(tmp_path, [svc])
        assert "mymod-web" in values

    def test_service_key_no_prefix_when_equal(self, tmp_path):
        """When module name == service name the prefix is omitted."""
        svc = _make_service("mymod", image="mymod:latest")
        values, _ = self._run_build(tmp_path, [svc])
        assert "mymod" in values
        assert "mymod-mymod" not in values

    # --- values.yaml: env block ---

    def test_env_value_literal(self, tmp_path):
        env = MagicMock(spec=ModuleServiceEnvironmentModel)
        env.key = "TZ"
        env.value = "Europe/Brussels"
        env.var = None
        env.secret = None
        env.feature = None
        svc = _make_service("app", image="app:1", environment=[env])
        values, _ = self._run_build(tmp_path, [svc])
        assert values["mymod-app"]["env"]["TZ"] == "Europe/Brussels"

    def test_env_var_emits_substitution(self, tmp_path):
        env = MagicMock(spec=ModuleServiceEnvironmentModel)
        env.key = "APP_VERSION"
        env.value = None
        env.var = "APP_VERSION"
        env.secret = None
        env.feature = None
        svc = _make_service("app", image="app:1", environment=[env])
        values, _ = self._run_build(tmp_path, [svc])
        assert values["mymod-app"]["env"]["APP_VERSION"] == "${APP_VERSION}"

    def test_env_secret_emits_substitution(self, tmp_path):
        env = MagicMock(spec=ModuleServiceEnvironmentModel)
        env.key = "DB_PASSWORD"
        env.value = None
        env.var = None
        env.secret = "DB_PASSWORD"
        env.feature = None
        svc = _make_service("db", image="postgres:16", environment=[env])
        values, _ = self._run_build(tmp_path, [svc])
        assert values["mymod-db"]["env"]["DB_PASSWORD"] == "${DB_PASSWORD}"

    def test_env_feature_emits_substitution(self, tmp_path):
        env = MagicMock(spec=ModuleServiceEnvironmentModel)
        env.key = "ENABLE_METRICS"
        env.value = None
        env.var = None
        env.secret = None
        env.feature = "enable_metrics"
        svc = _make_service("app", image="app:1", environment=[env])
        values, _ = self._run_build(tmp_path, [svc])
        assert values["mymod-app"]["env"]["ENABLE_METRICS"] == "${enable_metrics}"

    # --- values.yaml: persistence block ---

    def test_pvc_persistence_block(self, tmp_path):
        """A mount with storage_class produces a persistence block."""
        mount = _make_pvc_mount(name="data", storage_class="fast-ssd", access_mode="ReadWriteOnce", storage_size="10Gi")
        svc = _make_service("db", image="postgres:16", mounts=[mount])
        values, _ = self._run_build(tmp_path, [svc])
        persistence = values["mymod-db"]["persistence"]
        assert "data" in persistence
        assert persistence["data"]["storageClass"] == "fast-ssd"
        assert persistence["data"]["accessMode"] == "ReadWriteOnce"
        assert persistence["data"]["size"] == "10Gi"

    def test_non_pvc_mount_excluded_from_persistence(self, tmp_path):
        """A mount without storage_class should NOT produce a persistence block."""
        mount = MagicMock(spec=ModuleMountModel)
        mount.volume_ref = "data"
        mount.source_path = None
        mount.storage_class = None
        mount.access_mode = None
        mount.storage_size = None
        mount.name = "data"
        mount.target_path = "/var/data"
        mount.type = "volume"
        svc = _make_service("app", image="app:1", mounts=[mount])
        values, _ = self._run_build(tmp_path, [svc])
        assert "persistence" not in values.get("mymod-app", {})

    # --- values.yaml: configuration merged verbatim ---

    def test_configuration_merged_verbatim(self, tmp_path):
        svc = _make_service("app", image="app:1", configuration={"replicaCount": 2, "resources": {"cpu": "500m"}})
        values, _ = self._run_build(tmp_path, [svc])
        assert values["mymod-app"]["replicaCount"] == 2
        assert values["mymod-app"]["resources"] == {"cpu": "500m"}

    # --- meta.yaml ---

    def test_meta_uses_release_name_when_set(self, tmp_path):
        svc = _make_service("web", image="nginx:alpine")
        _, meta = self._run_build(tmp_path, [svc], release_name="my-release")
        assert meta["releaseName"] == "my-release"

    def test_meta_falls_back_to_module_name_for_release(self, tmp_path):
        svc = _make_service("web", image="nginx:alpine")
        _, meta = self._run_build(tmp_path, [svc], module_name="mymod", release_name=None)
        assert meta["releaseName"] == "mymod"

    def test_meta_uses_kubernetes_namespace_when_set(self, tmp_path):
        svc = _make_service("web", image="nginx:alpine")
        _, meta = self._run_build(tmp_path, [svc], kubernetes_namespace="prod-ns")
        assert meta["namespace"] == "prod-ns"

    def test_meta_falls_back_to_namespace_name(self, tmp_path):
        svc = _make_service("web", image="nginx:alpine")
        _, meta = self._run_build(tmp_path, [svc], namespace="testns", kubernetes_namespace=None)
        assert meta["namespace"] == "testns"

    # --- meta.yaml: chart coordinates ---

    def test_meta_includes_chart_coordinates_for_registry_source(self, tmp_path):
        """When source is chart-based, meta.yaml includes chartName/Version/Repository."""
        src = MagicMock()
        src.chart_repository = "https://argoproj.github.io/argo-helm"
        src.chart_name = "argo-cd"
        src.chart_version = "7.8.0"
        src.source_path = None
        src.repository = None
        svc = _make_service("server", image="argoproj/argocd:v2")
        _, meta = self._run_build(tmp_path, [svc], release_name="argocd", source=src)
        assert meta["chartName"] == "argo-cd"
        assert meta["chartVersion"] == "7.8.0"
        assert meta["chartRepository"] == "https://argoproj.github.io/argo-helm"

    def test_meta_omits_chart_coordinates_for_git_source(self, tmp_path):
        """When source is git-based (no chart_repository), meta.yaml has no chart keys."""
        svc = _make_service("web", image="nginx:alpine")
        _, meta = self._run_build(tmp_path, [svc])
        assert "chartName" not in meta
        assert "chartVersion" not in meta
        assert "chartRepository" not in meta

    # --- values.yaml: module-level configuration ---

    def test_module_configuration_merged_into_values_with_services(self, tmp_path):
        """spec.configuration merges on top of service-generated values."""
        svc = _make_service("web", image="nginx:alpine")
        config = {"global": {"domain": "example.com"}, "replicaCount": 3}
        values, _ = self._run_build(tmp_path, [svc], configuration=config)
        assert values["global"] == {"domain": "example.com"}
        assert values["replicaCount"] == 3
        # Service entry still present
        assert "mymod-web" in values

    def test_module_configuration_creates_values_for_serviceless_module(self, tmp_path):
        """spec.configuration creates values.yaml even when no services exist."""
        config = {"server": {"insecure": True}, "redis": {"enabled": False}}
        values, _ = self._run_build(tmp_path, services=None, configuration=config)
        assert values["server"] == {"insecure": True}
        assert values["redis"] == {"enabled": False}

    def test_no_configuration_no_services_no_values_file(self, tmp_path):
        """Without services or configuration, values.yaml is not written."""
        helm_module = _make_helm_module("mod", services=None)
        mod_service = _make_mod_service(module=helm_module)
        mod_ref = _module_ref("mod", "module.yaml")

        ns_svc = _mock_namespace_service([mod_ref])
        dep_svc = _mock_deployment_service(build_path=tmp_path, namespace_services={"ns1": ns_svc})

        module_path = tmp_path / "module.yaml"
        module_path.write_text("")

        builder = HelmBuilder()
        with (
            patch("strata.builders.helm_builder.resolve_path") as mock_rp,
            patch("strata.builders.helm_builder.ModuleService.load", return_value=mod_service),
        ):
            mock_rp.return_value = module_path
            ok = builder.build(dep_svc, tmp_path, tmp_path)

        assert ok is True
        assert not (tmp_path / "ns1" / "mod" / "values.yaml").exists()
        assert (tmp_path / "ns1" / "mod" / "meta.yaml").exists()

    # --- dry_run ---

    def test_dry_run_no_files_written(self, tmp_path):
        helm_module = _make_helm_module("mod", services=[_make_service("web", image="nginx:alpine")])
        mod_service = _make_mod_service(module=helm_module)
        mod_ref = _module_ref("mod", "module.yaml")

        ns_svc = _mock_namespace_service([mod_ref])
        dep_svc = _mock_deployment_service(build_path=tmp_path, namespace_services={"ns1": ns_svc})

        module_path = tmp_path / "module.yaml"
        module_path.write_text("")

        builder = HelmBuilder()
        with (
            patch("strata.builders.helm_builder.resolve_path") as mock_rp,
            patch("strata.builders.helm_builder.ModuleService.load", return_value=mod_service),
        ):
            mock_rp.return_value = module_path
            ok = builder.build(dep_svc, tmp_path, tmp_path, dry_run=True)

        assert ok is True
        assert not (tmp_path / "ns1" / "mod" / "values.yaml").exists()
        assert not (tmp_path / "ns1" / "mod" / "meta.yaml").exists()


# ---------------------------------------------------------------------------
# build — error paths
# ---------------------------------------------------------------------------


class TestHelmBuilderBuildErrors:
    def test_module_file_not_found(self, tmp_path):
        mod_ref = _module_ref("mod", "missing.yaml")
        ns_svc = _mock_namespace_service([mod_ref])
        dep_svc = _mock_deployment_service(build_path=tmp_path, namespace_services={"ns1": ns_svc})

        builder = HelmBuilder()
        with patch("strata.builders.helm_builder.resolve_path") as mock_rp:
            mock_rp.return_value = tmp_path / "missing.yaml"  # does not exist
            ok = builder.build(dep_svc, tmp_path, tmp_path)

        assert ok is False
        assert any("not found" in e for e in builder.get_errors())

    def test_module_validation_failed(self, tmp_path):
        mod_ref = _module_ref("mod", "module.yaml")
        ns_svc = _mock_namespace_service([mod_ref])
        dep_svc = _mock_deployment_service(build_path=tmp_path, namespace_services={"ns1": ns_svc})

        module_path = tmp_path / "module.yaml"
        module_path.write_text("")

        invalid_mod_svc = _make_mod_service(validated=False, module=None)

        builder = HelmBuilder()
        with (
            patch("strata.builders.helm_builder.resolve_path") as mock_rp,
            patch("strata.builders.helm_builder.ModuleService.load", return_value=invalid_mod_svc),
        ):
            mock_rp.return_value = module_path
            ok = builder.build(dep_svc, tmp_path, tmp_path)

        assert ok is False
        assert any("validation failed" in e for e in builder.get_errors())


# ---------------------------------------------------------------------------
# build — local chart source (copytree + Jinja2 templating)
# ---------------------------------------------------------------------------


class TestHelmBuilderLocalChartTemplates:
    """Regression tests: a copied local chart's templates/ dir must never be
    Jinja2-rendered, since it uses Helm's own Go-template syntax
    (e.g. `{{ .Release.Name }}`), which is invalid Jinja2 and previously
    crashed the entire build (see `TemplateSyntaxError: unexpected '.'`)."""

    def _build_with_local_chart(self, tmp_path, namespace="testns", module_name="mymod"):
        chart_dir = tmp_path / "charts" / "mychart"
        (chart_dir / "templates").mkdir(parents=True)
        (chart_dir / "templates" / "deployment.yaml").write_text(
            "metadata:\n  name: {{ .Release.Name }}\n",
            encoding="utf-8",
        )
        (chart_dir / "Chart.yaml").write_text("name: mychart\nversion: 0.1.0\n", encoding="utf-8")

        src = MagicMock()
        src.chart_repository = None
        src.chart_name = None
        src.chart_version = None
        src.source_path = "charts/mychart"
        src.repository = None
        src.reference = None

        helm_module = _make_helm_module(module_name, services=[], source=src)
        mod_service = _make_mod_service(module=helm_module)
        mod_ref = _module_ref(module_name, "module.yaml")
        ns_svc = _mock_namespace_service([mod_ref])
        dep_svc = _mock_deployment_service(build_path=tmp_path, namespace_services={namespace: ns_svc})

        module_path = tmp_path / "module.yaml"
        module_path.write_text("")

        builder = HelmBuilder()
        with (
            patch("strata.builders.helm_builder.resolve_path") as mock_rp,
            patch("strata.builders.helm_builder.ModuleService.load", return_value=mod_service),
        ):
            mock_rp.return_value = module_path
            ok = builder.build(dep_svc, tmp_path, tmp_path)

        return builder, ok, tmp_path / namespace / module_name

    def test_build_succeeds_with_go_template_syntax_in_templates_dir(self, tmp_path):
        builder, ok, _ = self._build_with_local_chart(tmp_path)
        assert ok is True, builder.get_errors()

    def test_templates_dir_left_untouched(self, tmp_path):
        _, _, module_dir = self._build_with_local_chart(tmp_path)
        rendered = (module_dir / "templates" / "deployment.yaml").read_text(encoding="utf-8")
        assert rendered == "metadata:\n  name: {{ .Release.Name }}\n"

    def test_files_outside_templates_dir_are_still_copied(self, tmp_path):
        _, _, module_dir = self._build_with_local_chart(tmp_path)
        assert (module_dir / "Chart.yaml").read_text(encoding="utf-8") == "name: mychart\nversion: 0.1.0\n"


class TestHelmBuilderLocalChartRefPinning:
    """ADR-0071: local chart copy honors source.reference for ref-pinned extraction,
    same as Terraform/Ansible/Bicep. Previously silently ignored — declaring
    `reference:` on a helm module's local chart source validated successfully but
    had zero effect (a real bug, not just a design gap)."""

    def _build(self, tmp_path, reference, repository=None, namespace="testns", module_name="mymod", dry_run=False):
        chart_dir = tmp_path / "charts" / "mychart"
        chart_dir.mkdir(parents=True)
        (chart_dir / "Chart.yaml").write_text("name: mychart\nversion: 0.1.0\n", encoding="utf-8")

        src = MagicMock()
        src.chart_repository = None
        src.chart_name = None
        src.chart_version = None
        src.source_path = "charts/mychart"
        src.repository = repository
        src.reference = reference

        helm_module = _make_helm_module(module_name, services=[], source=src)
        mod_service = _make_mod_service(module=helm_module)
        mod_ref = _module_ref(module_name, "module.yaml")
        ns_svc = _mock_namespace_service([mod_ref])
        dep_svc = _mock_deployment_service(build_path=tmp_path, namespace_services={namespace: ns_svc})

        module_path = tmp_path / "module.yaml"
        module_path.write_text("")

        builder = HelmBuilder()
        with (
            patch("strata.builders.helm_builder.resolve_path") as mock_rp,
            patch("strata.builders.helm_builder.ModuleService.load", return_value=mod_service),
        ):
            mock_rp.return_value = module_path
            ok = builder.build(dep_svc, tmp_path, tmp_path, dry_run=dry_run)

        return builder, ok, tmp_path / namespace / module_name

    def test_dry_run_with_reference_reports_ref(self, tmp_path):
        builder, ok, module_dir = self._build(tmp_path, reference="v1.4.0", dry_run=True)

        assert ok is True, builder.get_errors()
        messages = "\n".join(builder.get_messages())
        assert "v1.4.0" in messages
        assert "DRY-RUN" in messages
        assert not module_dir.exists()

    def test_reference_on_non_git_dir_falls_back_to_copy(self, tmp_path):
        """work_path (used as repo_root here, since no repository/repo_map) is not a
        git repo — falls back to copying from the working tree, same end result."""
        builder, ok, module_dir = self._build(tmp_path, reference="v1.0.0")

        assert ok is True, builder.get_errors()
        assert (module_dir / "Chart.yaml").exists()
        messages = "\n".join(builder.get_messages())
        assert "not a git repository" in messages

    def test_no_reference_uses_standard_copy(self, tmp_path):
        builder, ok, module_dir = self._build(tmp_path, reference=None)

        assert ok is True, builder.get_errors()
        assert (module_dir / "Chart.yaml").exists()


class TestHelmBuilderSolutionController:
    """ADR-0071: module_dir resolution goes through
    SolutionController.get_module_build_path() when a solution_controller is supplied,
    instead of independently re-deriving deployment_build_path/namespace/module."""

    def test_uses_get_module_build_path_when_solution_controller_present(self, tmp_path):
        namespace, module_name = "testns", "mymod"
        helm_module = _make_helm_module(module_name, services=[_make_service("web", image="nginx")])
        mod_service = _make_mod_service(module=helm_module)
        mod_ref = _module_ref(module_name, "module.yaml")

        ns_svc = _mock_namespace_service([mod_ref])
        dep_svc = _mock_deployment_service(build_path=tmp_path, namespace_services={namespace: ns_svc})

        module_path = tmp_path / "module.yaml"
        module_path.write_text("")

        custom_dest = tmp_path / "custom" / namespace / module_name
        solution_controller = MagicMock()
        solution_controller.get_module_build_path.return_value = custom_dest

        builder = HelmBuilder()
        with (
            patch("strata.builders.helm_builder.resolve_path") as mock_rp,
            patch("strata.builders.helm_builder.ModuleService.load", return_value=mod_service),
        ):
            mock_rp.return_value = module_path
            ok = builder.build(dep_svc, tmp_path, tmp_path, solution_controller=solution_controller)

        assert ok is True, builder.get_errors()
        assert (custom_dest / "values.yaml").exists()
        assert (custom_dest / "meta.yaml").exists()
        solution_controller.get_module_build_path.assert_any_call(dep_svc, tmp_path, namespace, module_name)

    def test_falls_back_to_default_shape_when_no_solution_controller(self, tmp_path):
        namespace, module_name = "testns", "mymod"
        helm_module = _make_helm_module(module_name, services=[_make_service("web", image="nginx")])
        mod_service = _make_mod_service(module=helm_module)
        mod_ref = _module_ref(module_name, "module.yaml")

        ns_svc = _mock_namespace_service([mod_ref])
        dep_svc = _mock_deployment_service(build_path=tmp_path, namespace_services={namespace: ns_svc})

        module_path = tmp_path / "module.yaml"
        module_path.write_text("")

        builder = HelmBuilder()
        with (
            patch("strata.builders.helm_builder.resolve_path") as mock_rp,
            patch("strata.builders.helm_builder.ModuleService.load", return_value=mod_service),
        ):
            mock_rp.return_value = module_path
            ok = builder.build(dep_svc, tmp_path, tmp_path, solution_controller=None)

        assert ok is True, builder.get_errors()
        assert (tmp_path / namespace / module_name / "values.yaml").exists()
