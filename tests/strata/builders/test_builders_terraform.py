"""Unit tests for TerraformBuilder."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from strata.builders.terraform_builder import TerraformBuilder
from strata.models.common_models import ProvisionerType, SourceModel
from strata.models.workspace_model import WorkspaceIacBackendModel, WorkspaceIacModel


def _mock_svc(validated=True, build_path=None):
    svc = MagicMock()
    svc.is_validated.return_value = validated
    if build_path:
        svc.get_build_path.return_value = build_path
    return svc


def _minimal_vars_dict(resource_types=None):
    """Return a minimal _build_terraform_vars result."""
    return {
        "workspace": {"workspace_name": "ws"},
        "providers": {"platform_providers": {}},
        "topologies": {"topologies": {}},
        "resources_by_category": {rt: {} for rt in (resource_types or [])},
        "modules": {"modules": {}},
        "required_variables": {"variables": []},
        "required_features": {"features": []},
        "required_secrets": {"secrets": []},
    }


class TestTerraformBuilderInit:
    def test_default_init(self):
        builder = TerraformBuilder()
        assert builder.verbose is False
        assert builder.variable_refs == {}
        assert builder.feature_refs == {}
        assert builder.secret_refs == {}
        assert not builder.has_errors()
        assert not builder.has_messages()

    def test_verbose_flag(self):
        builder = TerraformBuilder(verbose=True)
        assert builder.verbose is True


class TestTerraformBuilderBeforeBuild:
    def test_not_validated_returns_false(self, tmp_path):
        builder = TerraformBuilder()
        svc = _mock_svc(validated=False)
        result = builder.before_build(svc, tmp_path, tmp_path)
        assert result is False
        assert builder.has_errors()
        assert any("not validated" in e for e in builder.get_errors())

    def test_dry_run_skips_file_check(self, tmp_path):
        builder = TerraformBuilder()
        svc = _mock_svc(validated=True, build_path=tmp_path / "out")
        # No platform.json on disk — should not matter in dry_run
        result = builder.before_build(svc, tmp_path, tmp_path, dry_run=True)
        assert result is True
        assert not builder.has_errors()

    def test_platform_json_missing_returns_false(self, tmp_path):
        build_dir = tmp_path / "dep-1.0.0"
        build_dir.mkdir()
        builder = TerraformBuilder()
        svc = _mock_svc(validated=True, build_path=build_dir)
        result = builder.before_build(svc, tmp_path, tmp_path, dry_run=False)
        assert result is False
        assert builder.has_errors()
        assert any("not found" in e for e in builder.get_errors())

    def test_platform_json_present_returns_true(self, tmp_path):
        build_dir = tmp_path / "dep-1.0.0"
        build_dir.mkdir()
        (build_dir / "platform.json").write_text("{}")
        builder = TerraformBuilder()
        svc = _mock_svc(validated=True, build_path=build_dir)
        result = builder.before_build(svc, tmp_path, tmp_path, dry_run=False)
        assert result is True
        assert not builder.has_errors()

    def test_verbose_adds_message_on_success(self, tmp_path):
        builder = TerraformBuilder(verbose=True)
        svc = _mock_svc(validated=True, build_path=tmp_path / "out")
        builder.before_build(svc, tmp_path, tmp_path, dry_run=True)
        assert builder.has_messages()
        assert any("Pre-build" in m for m in builder.get_messages())


class TestTerraformBuilderBuild:
    def test_dry_run_with_platform_model_returns_true(self, tmp_path):
        builder = TerraformBuilder()
        build_dir = tmp_path / "dep-1.0.0"
        svc = _mock_svc(validated=True, build_path=build_dir)
        platform_model = MagicMock()

        with patch.object(builder, "_build_terraform_vars", return_value=_minimal_vars_dict()):
            result = builder.build(svc, tmp_path, tmp_path, dry_run=True, platform_model=platform_model)

        assert result is True
        assert not builder.has_errors()
        messages = "\n".join(builder.get_messages())
        assert "DRY-RUN" in messages

    def test_dry_run_lists_planned_resource_files(self, tmp_path):
        builder = TerraformBuilder()
        build_dir = tmp_path / "dep-1.0.0"
        svc = _mock_svc(build_path=build_dir)
        platform_model = MagicMock()
        vars_dict = _minimal_vars_dict(resource_types=["vm", "storage"])

        with patch.object(builder, "_build_terraform_vars", return_value=vars_dict):
            builder.build(svc, tmp_path, tmp_path, dry_run=True, platform_model=platform_model)

        messages = "\n".join(builder.get_messages())
        assert "resx_vm.auto.tfvars.json" in messages
        assert "resx_storage.auto.tfvars.json" in messages

    def test_dry_run_reports_requirement_counts(self, tmp_path):
        builder = TerraformBuilder()
        build_dir = tmp_path / "dep-1.0.0"
        svc = _mock_svc(build_path=build_dir)
        platform_model = MagicMock()
        vars_dict = _minimal_vars_dict()
        vars_dict["required_variables"] = {"variables": [{"key": "v1"}, {"key": "v2"}]}
        vars_dict["required_secrets"] = {"secrets": [{"key": "s1"}]}

        with patch.object(builder, "_build_terraform_vars", return_value=vars_dict):
            builder.build(svc, tmp_path, tmp_path, dry_run=True, platform_model=platform_model)

        messages = "\n".join(builder.get_messages())
        assert "2 variable(s)" in messages
        assert "1 secret(s)" in messages

    def test_platform_json_missing_without_model_returns_false(self, tmp_path):
        build_dir = tmp_path / "dep-1.0.0"
        build_dir.mkdir()
        # No platform.json
        builder = TerraformBuilder()
        svc = _mock_svc(build_path=build_dir)
        result = builder.build(svc, tmp_path, tmp_path, dry_run=False, platform_model=None)
        assert result is False
        assert builder.has_errors()
        assert any("Platform model not found" in e for e in builder.get_errors())

    def test_exception_in_build_returns_false(self, tmp_path):
        builder = TerraformBuilder()
        svc = _mock_svc(build_path=tmp_path)
        platform_model = MagicMock()

        with patch.object(builder, "_build_terraform_vars", side_effect=RuntimeError("boom")):
            result = builder.build(svc, tmp_path, tmp_path, dry_run=True, platform_model=platform_model)

        assert result is False
        assert builder.has_errors()
        assert any("Failed to build Terraform artifacts" in e for e in builder.get_errors())

    def test_refs_reset_on_each_build(self, tmp_path):
        builder = TerraformBuilder()
        builder.variable_refs = {"old_var": {"key": "old_var"}}
        svc = _mock_svc(build_path=tmp_path)
        platform_model = MagicMock()

        with patch.object(builder, "_build_terraform_vars", return_value=_minimal_vars_dict()):
            builder.build(svc, tmp_path, tmp_path, dry_run=True, platform_model=platform_model)

        assert builder.variable_refs == {}


class TestTerraformBuilderAfterBuild:
    def test_dry_run_returns_true(self, tmp_path):
        builder = TerraformBuilder()
        svc = _mock_svc()
        result = builder.after_build(svc, tmp_path, tmp_path, dry_run=True)
        assert result is True
        assert not builder.has_errors()

    def test_dry_run_verbose_message(self, tmp_path):
        builder = TerraformBuilder(verbose=True)
        svc = _mock_svc()
        builder.after_build(svc, tmp_path, tmp_path, dry_run=True)
        assert builder.has_messages()
        assert any("DRY-RUN" in m for m in builder.get_messages())

    def test_all_base_files_present_returns_true(self, tmp_path):
        terraform_dir = tmp_path / "dep-1.0.0" / "terraform"
        terraform_dir.mkdir(parents=True)
        base_files = [
            "workspace.auto.tfvars.json",
            "providers.auto.tfvars.json",
            "topologies.auto.tfvars.json",
            "modules.auto.tfvars.json",
            "namespaces.auto.tfvars.json",
            "firewalls.auto.tfvars.json",
            "dns.auto.tfvars.json",
            "dns_secret_records.auto.tfvars.json",
            "networks.auto.tfvars.json",
            "tf_required_variables.json",
            "tf_required_features.json",
            "tf_required_secrets.json",
        ]
        for f in base_files:
            (terraform_dir / f).write_text("{}")

        svc = _mock_svc(build_path=tmp_path / "dep-1.0.0")
        builder = TerraformBuilder()
        result = builder.after_build(svc, tmp_path, tmp_path, dry_run=False)
        assert result is True
        assert not builder.has_errors()

    def test_missing_files_returns_false(self, tmp_path):
        terraform_dir = tmp_path / "dep-1.0.0" / "terraform"
        terraform_dir.mkdir(parents=True)
        # Only write workspace; builder expects providers.auto.tfvars.json too
        (terraform_dir / "workspace.auto.tfvars.json").write_text("{}")

        svc = _mock_svc(build_path=tmp_path / "dep-1.0.0")
        builder = TerraformBuilder()
        # Simulate that build() planned these files (one of which is missing)
        builder._written_file_names = ["workspace.auto.tfvars.json", "providers.auto.tfvars.json"]
        result = builder.after_build(svc, tmp_path, tmp_path, dry_run=False)
        assert result is False
        assert builder.has_errors()
        assert any("missing" in e for e in builder.get_errors())

    def test_verbose_reports_counts(self, tmp_path):
        terraform_dir = tmp_path / "dep-1.0.0" / "terraform"
        terraform_dir.mkdir(parents=True)
        # Only workspace is always written; simulate a minimal build output
        (terraform_dir / "workspace.auto.tfvars.json").write_text("{}")
        (terraform_dir / "resx_vm.auto.tfvars.json").write_text("{}")

        svc = _mock_svc(build_path=tmp_path / "dep-1.0.0")
        builder = TerraformBuilder(verbose=True)
        builder._written_file_names = ["workspace.auto.tfvars.json"]
        builder.after_build(svc, tmp_path, tmp_path, dry_run=False)
        assert builder.has_messages()


class TestTerraformBuilderTracking:
    def test_track_variable_new_key(self):
        builder = TerraformBuilder()
        builder._track_variable("db_host", "Database host", ["resource_a"])
        assert "db_host" in builder.variable_refs
        entry = builder.variable_refs["db_host"]
        assert entry["key"] == "db_host"
        assert entry["required"] is True
        assert entry["suggested_env_var"] == "TF_VAR_db_host"
        assert "resource_a" in entry["used_by"]

    def test_track_variable_merges_used_by(self):
        builder = TerraformBuilder()
        builder._track_variable("db_host", "Database host", ["resource_a"])
        builder._track_variable("db_host", "Database host", ["resource_b"])
        used_by = builder.variable_refs["db_host"]["used_by"]
        assert "resource_a" in used_by
        assert "resource_b" in used_by

    def test_track_feature_new_key(self):
        builder = TerraformBuilder()
        builder._track_feature("enable_logging", "Logging flag", ["mod_a"])
        assert "enable_logging" in builder.feature_refs
        assert builder.feature_refs["enable_logging"]["key"] == "enable_logging"

    def test_track_feature_merges_used_by(self):
        builder = TerraformBuilder()
        builder._track_feature("flag", "desc", ["a"])
        builder._track_feature("flag", "desc", ["b"])
        assert "a" in builder.feature_refs["flag"]["used_by"]
        assert "b" in builder.feature_refs["flag"]["used_by"]

    def test_track_secret_new_key(self):
        builder = TerraformBuilder()
        builder._track_secret("api_key", "API secret", ["svc_a"])
        assert "api_key" in builder.secret_refs
        assert builder.secret_refs["api_key"]["key"] == "api_key"

    def test_document_required_variables_empty(self):
        builder = TerraformBuilder()
        result = builder._document_required_variables()
        assert result == {"variables": []}

    def test_document_required_variables_with_entries(self):
        builder = TerraformBuilder()
        builder._track_variable("v1", "desc1", ["a"])
        builder._track_variable("v2", "desc2", ["b"])
        result = builder._document_required_variables()
        keys = [e["key"] for e in result["variables"]]
        assert "v1" in keys
        assert "v2" in keys

    def test_document_required_features_empty(self):
        builder = TerraformBuilder()
        result = builder._document_required_features()
        assert result == {"features": []}

    def test_document_required_secrets_empty(self):
        builder = TerraformBuilder()
        result = builder._document_required_secrets()
        assert result == {"secrets": []}


class TestTerraformBuilderWorkspaceVars:
    def _make_platform(self, workspace_name="ws", workspace_labels=None, deployment_labels=None):
        platform = MagicMock()
        platform.spec.workspace.name = workspace_name
        platform.spec.workspace.labels = workspace_labels or {}
        platform.spec.workspace.annotations = {}
        platform.spec.workspace.tags = []
        platform.meta.labels = deployment_labels or {}
        platform.meta.name = "dep"
        platform.meta.annotations = {}
        platform.meta.tags = []
        platform.apiVersion = "strata.huybrechts.xyz/v1"
        return platform

    def test_workspace_name_in_result(self):
        builder = TerraformBuilder()
        platform = self._make_platform(workspace_name="my_ws")
        result = builder._build_workspace_vars(platform, [])
        assert result["workspace_name"] == "my_ws"

    def test_version_defaults_to_1_0_0(self):
        builder = TerraformBuilder()
        platform = self._make_platform(workspace_labels={})
        result = builder._build_workspace_vars(platform, [])
        assert result["workspace_version"] == "1.0.0"

    def test_version_from_workspace_labels(self):
        builder = TerraformBuilder()
        platform = self._make_platform(workspace_labels={"version": "2.5.0"})
        result = builder._build_workspace_vars(platform, [])
        assert result["workspace_version"] == "2.5.0"

    def test_environment_from_deployment_labels(self):
        builder = TerraformBuilder()
        platform = self._make_platform(deployment_labels={"environment": "staging"})
        result = builder._build_workspace_vars(platform, [])
        assert result["environment"] == "staging"

    def test_environment_defaults_to_production(self):
        builder = TerraformBuilder()
        platform = self._make_platform(deployment_labels={})
        result = builder._build_workspace_vars(platform, [])
        assert result["environment"] == "production"


class TestTerraformBuilderDnsVars:
    """Regression tests: var:/secret:/output_key: DNS record values must resolve or be bucketed, never silently dropped."""

    def _make_platform(self, dns_zones):
        platform = MagicMock()
        platform.spec.dns_zones = dns_zones
        return platform

    def _make_record(
        self, name="@", rtype="A", value=None, var=None, secret=None, output_key=None, ttl=None, priority=None
    ):
        record = MagicMock()
        record.name = name
        record.type = MagicMock(value=rtype)
        record.value = value
        record.var = var
        record.secret = secret
        record.output_key = output_key
        record.ttl = ttl
        record.priority = priority
        return record

    def _make_dns(self, name, zone_name, records):
        zone = MagicMock()
        zone.name = zone_name
        zone.ttl = 3600
        zone.records = records
        dns = MagicMock()
        dns.name = name
        dns.annotations = {}
        dns.labels = {}
        dns.tags = []
        dns.provider = "inwx"
        dns.zones = [zone]
        return dns

    def test_output_key_record_bucketed_not_dropped(self):
        builder = TerraformBuilder()
        record = self._make_record(name="@", rtype="A", output_key="hearth_public_ip")
        platform = self._make_platform([self._make_dns("dns1", "example.com", [record])])
        result = builder._build_dns_vars(platform, [])
        rec = result["dns_zones"]["dns1"]["zones"]["example.com"]["records"][0]
        assert rec["value"] is None
        output_entry = result["dns_output_records"]["dns1"]["example.com"]["@_A"]
        assert output_entry["output_key"] == "hearth_public_ip"

    def test_no_output_records_when_no_output_key_used(self):
        builder = TerraformBuilder()
        record = self._make_record(value="1.2.3.4")
        platform = self._make_platform([self._make_dns("dns1", "example.com", [record])])
        result = builder._build_dns_vars(platform, [])
        assert result["dns_output_records"] == {}

    def test_planned_files_includes_dns_output_records_file(self):
        builder = TerraformBuilder()
        terraform_vars = {
            "workspace": {},
            "dns": {
                "dns_zones": {"dns1": {}},
                "dns_secret_records": {},
                "dns_output_records": {"dns1": {"example.com": {"@_A": {"output_key": "hearth_public_ip"}}}},
            },
        }
        files = dict(builder._planned_files(terraform_vars))
        assert "dns_output_records.auto.tfvars.json" in files
        assert files["dns_output_records.auto.tfvars.json"] == {
            "dns_output_records": {"dns1": {"example.com": {"@_A": {"output_key": "hearth_public_ip"}}}}
        }

    def test_planned_files_omits_dns_output_records_file_when_empty(self):
        builder = TerraformBuilder()
        terraform_vars = {
            "workspace": {},
            "dns": {"dns_zones": {"dns1": {}}, "dns_secret_records": {}, "dns_output_records": {}},
        }
        files = dict(builder._planned_files(terraform_vars))
        assert "dns_output_records.auto.tfvars.json" not in files


def _make_provisioner(source_path: str, repository: str | None = None) -> WorkspaceIacModel:
    """Build a WorkspaceIacModel with a terraform provisioner for testing."""
    source = SourceModel(source_path=source_path, repository=repository)
    return WorkspaceIacModel(name="platform_iac", provisioner=ProvisionerType.TERRAFORM, source=source)


def _make_deployment_svc(provisioner: WorkspaceIacModel, build_path: Path) -> MagicMock:
    """Return a mock DeploymentService that surfaces the given provisioner."""
    workspace_model = MagicMock()
    workspace_model.spec.provisioners = [provisioner]

    workspace_service = MagicMock()
    workspace_service.model = workspace_model

    deployment_svc = MagicMock()
    deployment_svc.get_workspace_service.return_value = workspace_service
    deployment_svc.get_build_path.return_value = build_path
    return deployment_svc


class TestCopyProvisionerSourceSingleRepo:
    """_copy_provisioner_source resolves to work_path when repository is absent."""

    def test_no_repository_resolves_to_work_path(self, tmp_path):
        """Source dir must be work_path/source_path when repository is not set."""
        src_dir = tmp_path / "terraform"
        src_dir.mkdir()
        build_path = tmp_path / "build"
        build_path.mkdir()

        prov = _make_provisioner(source_path="terraform")
        depl_svc = _make_deployment_svc(prov, build_path)

        builder = TerraformBuilder()
        with patch.object(builder, "_build_template_context", return_value={}):
            result = builder._copy_provisioner_source(depl_svc, build_path, tmp_path, repo_map={}, dry_run=True)

        assert result is True
        assert not builder.has_errors()
        messages = "\n".join(builder.get_messages())
        assert str(tmp_path / "terraform") in messages

    def test_no_repository_missing_src_dir_returns_false(self, tmp_path):
        """Error reported when source_path does not exist and repository is absent."""
        build_path = tmp_path / "build"
        build_path.mkdir()

        prov = _make_provisioner(source_path="nonexistent")
        depl_svc = _make_deployment_svc(prov, build_path)

        builder = TerraformBuilder()
        with patch.object(builder, "_build_template_context", return_value={}):
            result = builder._copy_provisioner_source(depl_svc, build_path, tmp_path, repo_map={}, dry_run=True)

        assert result is False
        assert builder.has_errors()
        assert any("nonexistent" in e for e in builder.get_errors())

    def test_with_repository_uses_repo_map(self, tmp_path):
        """When repository is set and present in repo_map, repo_map root is used."""
        repo_root = tmp_path / "my-repo"
        src_dir = repo_root / "terraform"
        src_dir.mkdir(parents=True)
        build_path = tmp_path / "build"
        build_path.mkdir()

        prov = _make_provisioner(source_path="terraform", repository="my_repo")
        depl_svc = _make_deployment_svc(prov, build_path)

        builder = TerraformBuilder()
        with patch.object(builder, "_build_template_context", return_value={}):
            result = builder._copy_provisioner_source(
                depl_svc, build_path, tmp_path, repo_map={"my_repo": str(repo_root)}, dry_run=True
            )

        assert result is True
        messages = "\n".join(builder.get_messages())
        assert str(src_dir) in messages


def _make_provisioner_with_ref(
    source_path: str, repository: str | None = None, reference: str | None = None
) -> WorkspaceIacModel:
    """Build a WorkspaceIacModel with a terraform provisioner and optional ref pinning."""
    source = SourceModel(source_path=source_path, repository=repository, reference=reference)
    return WorkspaceIacModel(name="platform_iac", provisioner=ProvisionerType.TERRAFORM, source=source)


class TestCopyProvisionerSourceRefPinning:
    """_copy_provisioner_source handles source.reference for ref-pinned extraction."""

    def test_dry_run_with_reference_reports_ref(self, tmp_path):
        """In dry-run mode, when source.reference is set, message mentions the ref."""
        build_path = tmp_path / "build"
        build_path.mkdir()

        prov = _make_provisioner_with_ref(source_path="terraform", repository="my_repo", reference="v1.4.0")
        depl_svc = _make_deployment_svc(prov, build_path)

        repo_root = tmp_path / "my-repo"
        repo_root.mkdir()

        builder = TerraformBuilder()
        with patch.object(builder, "_build_template_context", return_value={}):
            result = builder._copy_provisioner_source(
                depl_svc, build_path, tmp_path, repo_map={"my_repo": str(repo_root)}, dry_run=True
            )

        assert result is True
        messages = "\n".join(builder.get_messages())
        assert "v1.4.0" in messages
        assert "DRY-RUN" in messages

    def test_no_reference_uses_standard_copy(self, tmp_path):
        """Without source.reference, standard working-tree copy is used."""
        src_dir = tmp_path / "terraform"
        src_dir.mkdir()
        (src_dir / "main.tf").write_text("resource {}")
        build_path = tmp_path / "build"
        build_path.mkdir()

        prov = _make_provisioner_with_ref(source_path="terraform", reference=None)
        depl_svc = _make_deployment_svc(prov, build_path)

        builder = TerraformBuilder()
        with patch.object(builder, "_build_template_context", return_value={}):
            result = builder._copy_provisioner_source(depl_svc, build_path, tmp_path, repo_map={}, dry_run=False)

        assert result is True
        assert (build_path / "terraform" / "main.tf").exists()

    def test_reference_on_non_git_dir_falls_back_to_copy(self, tmp_path):
        """When source.reference is set but repo_root has no .git, falls back to tree copy."""
        repo_root = tmp_path / "my-repo"
        src_dir = repo_root / "terraform"
        src_dir.mkdir(parents=True)
        (src_dir / "main.tf").write_text("resource {}")
        build_path = tmp_path / "build"
        build_path.mkdir()

        prov = _make_provisioner_with_ref(source_path="terraform", repository="my_repo", reference="v1.0.0")
        depl_svc = _make_deployment_svc(prov, build_path)

        builder = TerraformBuilder()
        with patch.object(builder, "_build_template_context", return_value={}):
            result = builder._copy_provisioner_source(
                depl_svc, build_path, tmp_path, repo_map={"my_repo": str(repo_root)}, dry_run=False
            )

        assert result is True
        assert (build_path / "terraform" / "main.tf").exists()
        messages = "\n".join(builder.get_messages())
        assert "not a git repository" in messages


class TestEmitVariableValue:
    """Tests for TerraformBuilder._emit_variable_value — typed emission logic."""

    def test_no_type_emits_value_as_is(self):
        """Without type field, value passes through unchanged."""
        from strata.models.store_models import VariableStoreModel

        var = VariableStoreModel(key="k", store="constant", value="hello")
        result = TerraformBuilder._emit_variable_value(var)
        assert result == "hello"

    def test_no_type_dict_value_passes_through(self):
        """Without type, dict values pass through as-is (backward compat)."""
        from strata.models.store_models import VariableStoreModel

        val = {"nested": {"key": "value"}}
        var = VariableStoreModel(key="k", store="constant", value=val)
        result = TerraformBuilder._emit_variable_value(var)
        assert result == val

    def test_type_string_stringifies(self):
        """type=string forces string serialization."""
        from strata.models.store_models import VariableStoreModel

        var = VariableStoreModel(key="k", store="constant", value=42, type="string")
        result = TerraformBuilder._emit_variable_value(var)
        assert result == "42"

    def test_type_number_emits_native(self):
        from strata.models.store_models import VariableStoreModel

        var = VariableStoreModel(key="k", store="constant", value=42, type="number")
        result = TerraformBuilder._emit_variable_value(var)
        assert result == 42
        assert isinstance(result, int)

    def test_type_bool_emits_native(self):
        from strata.models.store_models import VariableStoreModel

        var = VariableStoreModel(key="k", store="constant", value=True, type="bool")
        result = TerraformBuilder._emit_variable_value(var)
        assert result is True

    def test_type_object_emits_dict(self):
        from strata.models.store_models import VariableStoreModel

        val = {"pools": {"default": {"size": "Standard_D4s_v3"}}}
        var = VariableStoreModel(key="k", store="constant", value=val, type="object")
        result = TerraformBuilder._emit_variable_value(var)
        assert result == val
        assert isinstance(result, dict)

    def test_type_list_emits_list(self):
        from strata.models.store_models import VariableStoreModel

        val = ["10.0.0.0/8", "172.16.0.0/12"]
        var = VariableStoreModel(key="k", store="constant", value=val, type="list")
        result = TerraformBuilder._emit_variable_value(var)
        assert result == val
        assert isinstance(result, list)

    def test_type_map_emits_dict(self):
        from strata.models.store_models import VariableStoreModel

        val = {"env": "prod", "team": "platform"}
        var = VariableStoreModel(key="k", store="constant", value=val, type="map")
        result = TerraformBuilder._emit_variable_value(var)
        assert result == val


# ---------------------------------------------------------------------------
# Input validation — per-provisioner secret scoping regression tests
#
# _collect_declared_input_keys() previously swept EVERY secret declared anywhere
# in environment.yaml into every Terraform provisioner's "declared inputs" set,
# regardless of which stage(s)/provisioner actually use them. This false-positived
# on any secret meant only for a different provisioner (Ansible/Compose/Helm)
# sharing the same environment file. The fix scopes secrets to the stage(s) that
# resolve to *this* provisioner, mirroring ResolvedValues.for_stage()'s deploy-time
# stage.secrets allowlist.
# ---------------------------------------------------------------------------


def _make_stage(name="stage1", provisioner=None, topology=None, secrets=None):
    stage = MagicMock()
    stage.name = name
    stage.provisioner = provisioner
    stage.topology = topology
    stage.secrets = secrets
    return stage


class TestStagesForProvisioner:
    def test_explicit_stage_provisioner_match(self):
        prov = _make_provisioner(source_path="terraform")
        prov.name = "haven_iac"
        other_prov = _make_provisioner(source_path="ansible")
        other_prov.name = "haven_app"
        workspace_model = MagicMock()
        workspace_model.spec.provisioners = [prov, other_prov]
        workspace_model.spec.topology = []

        matching = _make_stage(name="provision", provisioner="haven_iac")
        non_matching = _make_stage(name="configure", provisioner="haven_app")

        builder = TerraformBuilder()
        result = builder._stages_for_provisioner(workspace_model, [matching, non_matching], prov)

        assert result == [matching]

    def test_topology_resolves_to_provisioner(self):
        prov = _make_provisioner(source_path="terraform")
        prov.name = "haven_iac"
        workspace_model = MagicMock()
        workspace_model.spec.provisioners = [prov]
        topo = MagicMock()
        topo.name = "infra"
        topo.provisioner = "haven_iac"
        workspace_model.spec.topology = [topo]

        stage = _make_stage(name="provision", topology="infra")

        builder = TerraformBuilder()
        result = builder._stages_for_provisioner(workspace_model, [stage], prov)

        assert result == [stage]

    def test_sole_provisioner_fallback_when_stage_has_neither(self):
        prov = _make_provisioner(source_path="terraform")
        prov.name = "haven_iac"
        workspace_model = MagicMock()
        workspace_model.spec.provisioners = [prov]
        workspace_model.spec.topology = []

        stage = _make_stage(name="provision", provisioner=None, topology=None)

        builder = TerraformBuilder()
        result = builder._stages_for_provisioner(workspace_model, [stage], prov)

        assert result == [stage]

    def test_no_fallback_when_multiple_provisioners_and_stage_unset(self):
        prov = _make_provisioner(source_path="terraform")
        prov.name = "haven_iac"
        other_prov = _make_provisioner(source_path="ansible")
        other_prov.name = "haven_app"
        workspace_model = MagicMock()
        workspace_model.spec.provisioners = [prov, other_prov]
        workspace_model.spec.topology = []

        stage = _make_stage(name="ambiguous", provisioner=None, topology=None)

        builder = TerraformBuilder()
        result = builder._stages_for_provisioner(workspace_model, [stage], prov)

        assert result == []

    def test_stage_referencing_other_provisioner_excluded(self):
        prov = _make_provisioner(source_path="terraform")
        prov.name = "haven_iac"
        other_prov = _make_provisioner(source_path="ansible")
        other_prov.name = "haven_app"
        workspace_model = MagicMock()
        workspace_model.spec.provisioners = [prov, other_prov]
        workspace_model.spec.topology = []

        stage = _make_stage(name="configure", provisioner="haven_app")

        builder = TerraformBuilder()
        result = builder._stages_for_provisioner(workspace_model, [stage], prov)

        assert result == []


class TestAllowedSecretKeysForStages:
    def test_no_stages_falls_back_to_all_keys(self):
        """No scoping signal available — legacy unscoped behavior, not silently empty."""
        all_keys = {"A", "B", "C"}
        result = TerraformBuilder._allowed_secret_keys_for_stages([], all_keys)
        assert result == all_keys

    def test_wildcard_returns_all_keys(self):
        all_keys = {"A", "B", "C"}
        stage = _make_stage(secrets=["*"])
        result = TerraformBuilder._allowed_secret_keys_for_stages([stage], all_keys)
        assert result == all_keys

    def test_union_of_multiple_stages(self):
        all_keys = {"A", "B", "C"}
        stage1 = _make_stage(name="s1", secrets=["A"])
        stage2 = _make_stage(name="s2", secrets=["B"])
        result = TerraformBuilder._allowed_secret_keys_for_stages([stage1, stage2], all_keys)
        assert result == {"A", "B"}

    def test_stage_with_no_secrets_contributes_nothing(self):
        all_keys = {"A", "B"}
        stage = _make_stage(secrets=None)
        result = TerraformBuilder._allowed_secret_keys_for_stages([stage], all_keys)
        assert result == set()

    def test_unrelated_secret_excluded(self):
        """The core regression case: a secret meant for another provisioner
        (not in this stage's allowlist) must not appear in the scoped result."""
        all_keys = {"VAULTWARDEN_ADMIN_TOKEN", "TERRAFORM_ONLY_SECRET"}
        stage = _make_stage(secrets=["TERRAFORM_ONLY_SECRET"])
        result = TerraformBuilder._allowed_secret_keys_for_stages([stage], all_keys)
        assert result == {"TERRAFORM_ONLY_SECRET"}
        assert "VAULTWARDEN_ADMIN_TOKEN" not in result


class TestCollectDeclaredInputKeysScoping:
    def _mock_env_service(self, variables=None, features=None, secret_keys=None):
        env_service = MagicMock()
        env_service.model.spec.secrets = [MagicMock(key=k) for k in (secret_keys or [])]
        env_service.get_variables.return_value = [MagicMock(key=k) for k in (variables or [])]
        env_service.get_features.return_value = [MagicMock(key=k) for k in (features or [])]
        return env_service

    def test_variables_and_features_always_included_unscoped(self):
        """Variables/features are never stage-scoped at deploy time (ResolvedValues.for_stage
        passes them through unfiltered) — they must be collected regardless of matching_stages."""
        deployment_service = MagicMock()
        deployment_service.get_environment_service.return_value = self._mock_env_service(
            variables=["region"], features=["dark_mode"], secret_keys=["OTHER_APP_SECRET"]
        )
        stage = _make_stage(secrets=["UNRELATED"])

        builder = TerraformBuilder()
        keys = builder._collect_declared_input_keys(deployment_service, [stage])

        assert "region" in keys
        assert "dark_mode" in keys

    def test_secrets_scoped_to_matching_stage_allowlist(self):
        deployment_service = MagicMock()
        deployment_service.get_environment_service.return_value = self._mock_env_service(
            secret_keys=["TF_SECRET", "ANSIBLE_ONLY_SECRET"]
        )
        stage = _make_stage(secrets=["TF_SECRET"])

        builder = TerraformBuilder()
        keys = builder._collect_declared_input_keys(deployment_service, [stage])

        assert "TF_SECRET" in keys
        assert "ANSIBLE_ONLY_SECRET" not in keys

    def test_no_matching_stages_falls_back_to_all_secrets(self):
        """No per-provisioner scoping signal (e.g. no stages: defined at all) —
        preserves the legacy behavior rather than silently under-validating."""
        deployment_service = MagicMock()
        deployment_service.get_environment_service.return_value = self._mock_env_service(secret_keys=["ANY_SECRET"])

        builder = TerraformBuilder()
        keys = builder._collect_declared_input_keys(deployment_service, [])

        assert "ANY_SECRET" in keys

    def test_default_matching_stages_none_falls_back_to_all_secrets(self):
        """Calling without matching_stages at all preserves pre-fix behavior."""
        deployment_service = MagicMock()
        deployment_service.get_environment_service.return_value = self._mock_env_service(secret_keys=["ANY_SECRET"])

        builder = TerraformBuilder()
        keys = builder._collect_declared_input_keys(deployment_service)

        assert "ANY_SECRET" in keys


class TestValidateInputsProvisionerScoping:
    """End-to-end regression test for the reported bug: a Terraform provisioner's
    variables.tf validation must not fail on a secret declared in environment.yaml
    that's only ever allowlisted for a different (e.g. Ansible) provisioner's stage."""

    def _write_variables_tf(self, prov_dir: Path, var_names):
        prov_dir.mkdir(parents=True, exist_ok=True)
        body = "\n".join(f'variable "{name}" {{\n  type = string\n}}\n' for name in var_names)
        (prov_dir / "variables.tf").write_text(body, encoding="utf-8")

    def _make_deployment_service(self, tmp_path, prov, other_prov, stages, secret_keys):
        workspace_model = MagicMock()
        workspace_model.spec.provisioners = [prov, other_prov]
        workspace_model.spec.topology = []

        workspace_service = MagicMock()
        workspace_service.model = workspace_model

        env_service = MagicMock()
        env_service.model.spec.secrets = [MagicMock(key=k) for k in secret_keys]
        env_service.get_variables.return_value = []
        env_service.get_features.return_value = []

        deployment_service = MagicMock()
        deployment_service.get_workspace_service.return_value = workspace_service
        deployment_service.get_environment_service.return_value = env_service
        deployment_service.get_build_path.return_value = tmp_path
        deployment_service.model.spec.stages = stages
        return deployment_service

    def test_ansible_only_secret_does_not_fail_terraform_validation(self, tmp_path):
        prov = _make_provisioner(source_path="terraform")
        prov.name = "haven_iac"
        other_prov = _make_provisioner(source_path="ansible")
        other_prov.name = "haven_app"

        prov_dir = tmp_path / "terraform"
        self._write_variables_tf(prov_dir, ["TF_ONLY_VAR"])

        tf_stage = _make_stage(name="provision", provisioner="haven_iac", secrets=["TF_ONLY_VAR"])
        ansible_stage = _make_stage(name="configure", provisioner="haven_app", secrets=["VAULTWARDEN_ADMIN_TOKEN"])

        deployment_service = self._make_deployment_service(
            tmp_path,
            prov,
            other_prov,
            stages=[tf_stage, ansible_stage],
            secret_keys=["TF_ONLY_VAR", "VAULTWARDEN_ADMIN_TOKEN"],
        )

        builder = TerraformBuilder()
        ok = builder._validate_inputs(deployment_service, tmp_path)

        assert ok is True, builder.get_errors()
        assert not builder.get_errors()

    def test_genuine_typo_still_reported_as_error(self, tmp_path):
        """Regression safety: a secret allowlisted for THIS stage but genuinely
        missing from variables.tf must still be reported."""
        prov = _make_provisioner(source_path="terraform")
        prov.name = "haven_iac"
        other_prov = _make_provisioner(source_path="ansible")
        other_prov.name = "haven_app"

        prov_dir = tmp_path / "terraform"
        self._write_variables_tf(prov_dir, ["SOME_OTHER_VAR"])

        tf_stage = _make_stage(name="provision", provisioner="haven_iac", secrets=["TYPO_SECRET"])

        deployment_service = self._make_deployment_service(
            tmp_path,
            prov,
            other_prov,
            stages=[tf_stage],
            secret_keys=["TYPO_SECRET"],
        )

        builder = TerraformBuilder()
        ok = builder._validate_inputs(deployment_service, tmp_path)

        assert ok is False
        assert any("TYPO_SECRET" in e for e in builder.get_errors())


class TestValidateInputsBackendConfigExclusion:
    """Regression tests: keys referenced only by a provisioner's
    ``backend.configuration`` ``${var:KEY}``/``${secret:KEY}`` expressions are
    backend plumbing (consumed via ``-backend-config`` at deploy time, never
    passed as Terraform root-module inputs) and must not be flagged as
    "not declared in variables.tf"."""

    def _write_variables_tf(self, prov_dir: Path, var_names):
        prov_dir.mkdir(parents=True, exist_ok=True)
        body = "\n".join(f'variable "{name}" {{\n  type = string\n}}\n' for name in var_names)
        (prov_dir / "variables.tf").write_text(body, encoding="utf-8")

    def _make_deployment_service(self, tmp_path, prov, variable_keys, secret_keys=None):
        workspace_model = MagicMock()
        workspace_model.spec.provisioners = [prov]
        workspace_model.spec.topology = []

        workspace_service = MagicMock()
        workspace_service.model = workspace_model

        env_service = MagicMock()
        env_service.model.spec.secrets = [MagicMock(key=k) for k in (secret_keys or [])]
        env_service.get_variables.return_value = [MagicMock(key=k) for k in variable_keys]
        env_service.get_features.return_value = []

        deployment_service = MagicMock()
        deployment_service.get_workspace_service.return_value = workspace_service
        deployment_service.get_environment_service.return_value = env_service
        deployment_service.get_build_path.return_value = tmp_path
        deployment_service.model.spec.stages = []
        return deployment_service

    def test_backend_only_variable_excluded_from_undeclared_check(self, tmp_path):
        """The exact bug-report scenario: backend.configuration references four
        ``${var:...}`` keys that are declared as environment variables but are
        deliberately absent from variables.tf. The build must succeed."""
        prov = _make_provisioner(source_path="terraform")
        prov.name = "infra"
        prov.backend = WorkspaceIacBackendModel(
            type="azurerm",
            configuration={
                "resource_group_name": "${var:tf_state_resource_group}",
                "storage_account_name": "${var:tf_state_storage_account}",
                "container_name": "${var:tf_state_container_name}",
                "key": "${var:tf_state_key}",
            },
        )

        prov_dir = tmp_path / "terraform"
        self._write_variables_tf(prov_dir, ["real_input"])

        deployment_service = self._make_deployment_service(
            tmp_path,
            prov,
            variable_keys=[
                "real_input",
                "tf_state_resource_group",
                "tf_state_storage_account",
                "tf_state_container_name",
                "tf_state_key",
            ],
        )

        builder = TerraformBuilder()
        ok = builder._validate_inputs(deployment_service, tmp_path)

        assert ok is True, builder.get_errors()
        assert not builder.get_errors()

    def test_backend_secret_key_also_excluded(self, tmp_path):
        prov = _make_provisioner(source_path="terraform")
        prov.name = "infra"
        prov.backend = WorkspaceIacBackendModel(
            type="azurerm",
            configuration={"sas_token": "${secret:tf_state_sas_token}"},
        )

        prov_dir = tmp_path / "terraform"
        self._write_variables_tf(prov_dir, ["real_input"])

        deployment_service = self._make_deployment_service(
            tmp_path,
            prov,
            variable_keys=["real_input"],
            secret_keys=["tf_state_sas_token"],
        )

        builder = TerraformBuilder()
        ok = builder._validate_inputs(deployment_service, tmp_path)

        assert ok is True, builder.get_errors()

    def test_genuine_undeclared_variable_still_reported_with_backend_present(self, tmp_path):
        """Backend exclusion must be scoped to the keys it actually references —
        an unrelated undeclared variable is still an error."""
        prov = _make_provisioner(source_path="terraform")
        prov.name = "infra"
        prov.backend = WorkspaceIacBackendModel(
            type="azurerm",
            configuration={"resource_group_name": "${var:tf_state_resource_group}"},
        )

        prov_dir = tmp_path / "terraform"
        self._write_variables_tf(prov_dir, ["real_input"])

        deployment_service = self._make_deployment_service(
            tmp_path,
            prov,
            variable_keys=["real_input", "tf_state_resource_group", "typo_var"],
        )

        builder = TerraformBuilder()
        ok = builder._validate_inputs(deployment_service, tmp_path)

        assert ok is False
        assert any("typo_var" in e for e in builder.get_errors())
        assert not any("tf_state_resource_group" in e for e in builder.get_errors())

    def test_no_backend_configured_behaves_as_before(self, tmp_path):
        prov = _make_provisioner(source_path="terraform")
        prov.name = "infra"

        prov_dir = tmp_path / "terraform"
        self._write_variables_tf(prov_dir, ["real_input"])

        deployment_service = self._make_deployment_service(
            tmp_path,
            prov,
            variable_keys=["real_input"],
        )

        builder = TerraformBuilder()
        ok = builder._validate_inputs(deployment_service, tmp_path)

        assert ok is True, builder.get_errors()
