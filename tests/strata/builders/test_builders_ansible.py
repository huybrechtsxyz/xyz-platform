"""Unit tests for AnsibleBuilder."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from strata.builders.ansible_builder import AnsibleBuilder
from strata.models.common_models import ProvisionerType, SourceModel
from strata.models.workspace_model import WorkspaceIacModel


def _mock_svc(validated=True, build_path=None, has_ansible_provisioner=True):
    svc = MagicMock()
    svc.is_validated.return_value = validated
    if build_path:
        svc.get_build_path.return_value = build_path

    ws_service = MagicMock()
    if has_ansible_provisioner:
        prov = MagicMock()
        prov.provisioner = ProvisionerType.ANSIBLE
        ws_service.model.spec.provisioners = [prov]
    else:
        ws_service.model.spec.provisioners = []
    svc.get_workspace_service.return_value = ws_service

    return svc


def _minimal_vars_dict(resource_types=None):
    """Return a minimal _build_ansible_vars result with empty sections."""
    return {
        "workspace": {"strata_workspace": {"name": "ws"}},
        "providers": {"strata_providers": {}},
        "topologies": {"strata_topologies": {}},
        "resources": {"strata_resources": {}},
        "resources_by_type": {rt: {} for rt in (resource_types or [])},
        "modules": {"strata_modules": {}},
        "namespaces": {"strata_namespaces": {}},
        "firewalls": {"strata_firewalls": {}},
        "dns": {"strata_dns_zones": {}},
        "networks": {"strata_networks": {}},
    }


def _full_vars_dict(resource_types=None):
    """Return a _build_ansible_vars result with non-empty sections."""
    return {
        "workspace": {"strata_workspace": {"name": "ws"}},
        "providers": {"strata_providers": {"p1": {"type": "hetzner"}}},
        "topologies": {"strata_topologies": {"t1": {"type": "vm"}}},
        "resources": {"strata_resources": {"r1": {"type": "server"}}},
        "resources_by_type": {rt: {"r1": {}} for rt in (resource_types or [])},
        "modules": {"strata_modules": {"m1": {}}},
        "namespaces": {"strata_namespaces": {"ns1": {}}},
        "firewalls": {"strata_firewalls": {"fw1": {}}},
        "dns": {"strata_dns_zones": {"dns1": {}}},
        "networks": {"strata_networks": {"net1": {}}},
    }


class TestAnsibleBuilderInit:
    def test_default_init(self):
        builder = AnsibleBuilder()
        assert builder.verbose is False
        assert not builder.has_errors()
        assert not builder.has_messages()

    def test_verbose_flag(self):
        builder = AnsibleBuilder(verbose=True)
        assert builder.verbose is True


class TestAnsibleBuilderBeforeBuild:
    def test_not_validated_returns_false(self, tmp_path):
        builder = AnsibleBuilder()
        svc = _mock_svc(validated=False)
        result = builder.before_build(svc, tmp_path, tmp_path)
        assert result is False
        assert builder.has_errors()
        assert any("not validated" in e for e in builder.get_errors())

    def test_dry_run_skips_file_check(self, tmp_path):
        builder = AnsibleBuilder()
        svc = _mock_svc(validated=True, build_path=tmp_path / "out")
        result = builder.before_build(svc, tmp_path, tmp_path, dry_run=True)
        assert result is True
        assert not builder.has_errors()

    def test_platform_json_missing_returns_false(self, tmp_path):
        build_dir = tmp_path / "dep-1.0.0"
        build_dir.mkdir()
        builder = AnsibleBuilder()
        svc = _mock_svc(validated=True, build_path=build_dir)
        result = builder.before_build(svc, tmp_path, tmp_path, dry_run=False)
        assert result is False
        assert builder.has_errors()
        assert any("not found" in e for e in builder.get_errors())

    def test_platform_json_present_returns_true(self, tmp_path):
        build_dir = tmp_path / "dep-1.0.0"
        build_dir.mkdir()
        (build_dir / "platform.json").write_text("{}")
        builder = AnsibleBuilder()
        svc = _mock_svc(validated=True, build_path=build_dir)
        result = builder.before_build(svc, tmp_path, tmp_path, dry_run=False)
        assert result is True
        assert not builder.has_errors()

    def test_verbose_adds_message_on_success(self, tmp_path):
        builder = AnsibleBuilder(verbose=True)
        svc = _mock_svc(validated=True, build_path=tmp_path / "out")
        builder.before_build(svc, tmp_path, tmp_path, dry_run=True)
        assert builder.has_messages()
        assert any("Pre-build" in m for m in builder.get_messages())


class TestAnsibleBuilderBuild:
    def test_dry_run_with_platform_model_returns_true(self, tmp_path):
        builder = AnsibleBuilder()
        build_dir = tmp_path / "dep-1.0.0"
        svc = _mock_svc(validated=True, build_path=build_dir)
        platform_model = MagicMock()

        with patch.object(builder, "_build_ansible_vars", return_value=_minimal_vars_dict()):
            result = builder.build(svc, tmp_path, tmp_path, dry_run=True, platform_model=platform_model)

        assert result is True
        assert not builder.has_errors()
        messages = "\n".join(builder.get_messages())
        assert "DRY-RUN" in messages

    def test_dry_run_lists_planned_resource_type_files(self, tmp_path):
        builder = AnsibleBuilder()
        build_dir = tmp_path / "dep-1.0.0"
        svc = _mock_svc(build_path=build_dir)
        platform_model = MagicMock()
        vars_dict = _full_vars_dict(resource_types=["objectstorage", "virtualmachine"])

        with patch.object(builder, "_build_ansible_vars", return_value=vars_dict):
            builder.build(svc, tmp_path, tmp_path, dry_run=True, platform_model=platform_model)

        messages = "\n".join(builder.get_messages())
        assert "strata_resx_objectstorage.yml" in messages
        assert "strata_resx_virtualmachine.yml" in messages

    def test_platform_json_missing_without_model_returns_false(self, tmp_path):
        build_dir = tmp_path / "dep-1.0.0"
        build_dir.mkdir()
        builder = AnsibleBuilder()
        svc = _mock_svc(build_path=build_dir)
        result = builder.build(svc, tmp_path, tmp_path, dry_run=False, platform_model=None)
        assert result is False
        assert builder.has_errors()
        assert any("Platform model not found" in e for e in builder.get_errors())

    def test_exception_in_build_returns_false(self, tmp_path):
        builder = AnsibleBuilder()
        svc = _mock_svc(build_path=tmp_path)
        platform_model = MagicMock()

        with patch.object(builder, "_build_ansible_vars", side_effect=RuntimeError("boom")):
            result = builder.build(svc, tmp_path, tmp_path, dry_run=True, platform_model=platform_model)

        assert result is False
        assert builder.has_errors()
        assert any("Failed to build Ansible artifacts" in e for e in builder.get_errors())

    def test_writes_yaml_files(self, tmp_path):
        builder = AnsibleBuilder()
        ansible_dir = tmp_path / "ansible"
        svc = _mock_svc(build_path=tmp_path)
        platform_model = MagicMock()
        vars_dict = _full_vars_dict(resource_types=["objectstorage"])

        with patch.object(builder, "_build_ansible_vars", return_value=vars_dict):
            with patch.object(builder, "_resolve_ansible_paths", return_value=[ansible_dir]):
                with patch.object(builder, "_copy_provisioner_source", return_value=True):
                    result = builder.build(svc, tmp_path, tmp_path, dry_run=False, platform_model=platform_model)

        assert result is True
        assert (ansible_dir / "strata_workspace.yml").exists()
        assert (ansible_dir / "strata_providers.yml").exists()
        assert (ansible_dir / "strata_topologies.yml").exists()
        assert (ansible_dir / "strata_resources.yml").exists()
        assert (ansible_dir / "strata_modules.yml").exists()
        assert (ansible_dir / "strata_namespaces.yml").exists()
        assert (ansible_dir / "strata_firewalls.yml").exists()
        assert (ansible_dir / "strata_dns.yml").exists()
        assert (ansible_dir / "strata_networks.yml").exists()
        assert (ansible_dir / "strata_resx_objectstorage.yml").exists()

    def test_skips_empty_section_files(self, tmp_path):
        """Empty data sections must NOT produce var files."""
        builder = AnsibleBuilder()
        ansible_dir = tmp_path / "ansible"
        svc = _mock_svc(build_path=tmp_path)
        platform_model = MagicMock()
        vars_dict = _minimal_vars_dict()  # all sections empty

        with patch.object(builder, "_build_ansible_vars", return_value=vars_dict):
            with patch.object(builder, "_resolve_ansible_paths", return_value=[ansible_dir]):
                with patch.object(builder, "_copy_provisioner_source", return_value=True):
                    result = builder.build(svc, tmp_path, tmp_path, dry_run=False, platform_model=platform_model)

        assert result is True
        assert (ansible_dir / "strata_workspace.yml").exists()
        # None of the empty-section files should be written
        for name in [
            "strata_providers.yml",
            "strata_topologies.yml",
            "strata_resources.yml",
            "strata_modules.yml",
            "strata_namespaces.yml",
            "strata_firewalls.yml",
            "strata_dns.yml",
            "strata_networks.yml",
        ]:
            assert not (ansible_dir / name).exists(), f"{name} should not be written for empty section"

    def test_written_yaml_is_valid(self, tmp_path):
        builder = AnsibleBuilder()
        ansible_dir = tmp_path / "ansible"
        svc = _mock_svc(build_path=tmp_path)
        platform_model = MagicMock()
        vars_dict = _minimal_vars_dict()

        with patch.object(builder, "_build_ansible_vars", return_value=vars_dict):
            with patch.object(builder, "_resolve_ansible_paths", return_value=[ansible_dir]):
                builder.build(svc, tmp_path, tmp_path, dry_run=False, platform_model=platform_model)

        content = (ansible_dir / "strata_workspace.yml").read_text(encoding="utf-8")
        parsed = yaml.safe_load(content)
        assert "strata_workspace" in parsed
        assert parsed["strata_workspace"]["name"] == "ws"

    def test_no_ansible_provisioner_skips_generation_entirely(self, tmp_path):
        """Regression test: a workspace with zero ANSIBLE provisioners (e.g.

        Terraform-only) must not get a spurious ``ansible/`` build folder at
        all — previously ``_save_ansible_vars()``'s "no provisioners resolved"
        fallback wrote a default ``ansible/`` directory unconditionally.
        """
        builder = AnsibleBuilder()
        ansible_dir = tmp_path / "ansible"
        svc = _mock_svc(build_path=tmp_path, has_ansible_provisioner=False)
        platform_model = MagicMock()
        vars_dict = _full_vars_dict(resource_types=["objectstorage"])

        with patch.object(builder, "_build_ansible_vars", return_value=vars_dict):
            result = builder.build(svc, tmp_path, tmp_path, dry_run=False, platform_model=platform_model)

        assert result is True
        assert not builder.has_errors()
        assert not ansible_dir.exists()

    def test_no_ansible_provisioner_skips_generation_in_dry_run(self, tmp_path):
        builder = AnsibleBuilder()
        svc = _mock_svc(build_path=tmp_path, has_ansible_provisioner=False)
        platform_model = MagicMock()

        with patch.object(builder, "_build_ansible_vars", return_value=_minimal_vars_dict()) as mocked:
            result = builder.build(svc, tmp_path, tmp_path, dry_run=True, platform_model=platform_model)

        assert result is True
        assert not builder.has_errors()
        mocked.assert_not_called()


class TestAnsibleBuilderAfterBuild:
    def test_dry_run_returns_true(self, tmp_path):
        builder = AnsibleBuilder()
        svc = _mock_svc()
        result = builder.after_build(svc, tmp_path, tmp_path, dry_run=True)
        assert result is True
        assert not builder.has_errors()

    def test_dry_run_verbose_message(self, tmp_path):
        builder = AnsibleBuilder(verbose=True)
        svc = _mock_svc()
        builder.after_build(svc, tmp_path, tmp_path, dry_run=True)
        assert any("DRY-RUN" in m for m in builder.get_messages())

    def test_no_files_returns_false(self, tmp_path):
        builder = AnsibleBuilder()
        ansible_dir = tmp_path / "ansible"
        ansible_dir.mkdir()
        svc = _mock_svc()
        with patch.object(builder, "_resolve_ansible_paths", return_value=[ansible_dir]):
            result = builder.after_build(svc, tmp_path, tmp_path, dry_run=False)
        assert result is False
        assert builder.has_errors()

    def test_with_files_returns_true(self, tmp_path):
        builder = AnsibleBuilder()
        ansible_dir = tmp_path / "ansible"
        ansible_dir.mkdir()
        (ansible_dir / "strata_workspace.yml").write_text("---\nstrata_workspace: {}")
        svc = _mock_svc()
        with patch.object(builder, "_resolve_ansible_paths", return_value=[ansible_dir]):
            result = builder.after_build(svc, tmp_path, tmp_path, dry_run=False)
        assert result is True
        assert not builder.has_errors()


class TestAnsibleBuilderVarAssembly:
    """Test the variable assembly methods with mock platform models."""

    def _make_platform(self, **overrides):
        """Create a minimal mock platform model."""
        platform = MagicMock()
        platform.meta.name = "test_deploy"
        platform.meta.labels = {"version": "2.0.0", "environment": "staging"}
        platform.meta.annotations = {"description": "Test deployment"}
        platform.meta.tags = ["ci"]
        platform.apiVersion = MagicMock(value="strata.huybrechts.xyz/v1")
        platform.spec.workspace.name = "my_workspace"
        platform.spec.workspace.labels = {"version": "1.0.0"}
        platform.spec.workspace.annotations = {"description": "Test workspace"}
        platform.spec.workspace.tags = ["prod"]
        platform.spec.providers = overrides.get("providers", [])
        platform.spec.topologies = overrides.get("topologies", [])
        platform.spec.resources = overrides.get("resources", [])
        platform.spec.modules = overrides.get("modules", [])
        platform.spec.namespaces = overrides.get("namespaces", [])
        platform.spec.firewalls = overrides.get("firewalls", [])
        platform.spec.dns_zones = overrides.get("dns_zones", [])
        platform.spec.networks = overrides.get("networks", [])
        return platform

    def test_workspace_vars(self):
        builder = AnsibleBuilder()
        platform = self._make_platform()
        result = builder._build_workspace_vars(platform, [])
        ws = result["strata_workspace"]
        assert ws["name"] == "my_workspace"
        assert ws["deployment_name"] == "test_deploy"
        assert ws["environment"] == "staging"

    def test_provider_vars(self):
        builder = AnsibleBuilder()
        provider = MagicMock()
        provider.name = "aws_eu"
        provider.properties.type = "aws"
        provider.properties.region = "eu-west-1"
        provider.properties.version = "5.0"
        provider.description = "AWS EU"
        provider.labels = {"team": "infra"}
        provider.tags = ["cloud"]
        platform = self._make_platform(providers=[provider])
        result = builder._build_provider_vars(platform, [])
        assert "aws_eu" in result["strata_providers"]
        assert result["strata_providers"]["aws_eu"]["region"] == "eu-west-1"

    def test_resource_vars(self):
        builder = AnsibleBuilder()
        resource = MagicMock()
        resource.name = "bucket_logs"
        resource.properties.resource_type = "objectstorage"
        resource.properties.provider_type = "aws"
        resource.properties.category = "storage"
        resource.properties.subcategory = "s3"
        resource.properties.unit_cost = 0.023
        resource.annotations = {"description": "Log bucket"}
        resource.labels = {}
        resource.tags = []
        resource.count = 3
        resource.configuration = {"versioning": True}
        resource.storage = None
        resource.firewalls = None
        resource.firewall = None
        resource.role = None
        platform = self._make_platform(resources=[resource])
        result = builder._build_resource_vars(platform, [])
        assert "bucket_logs" in result["strata_resources"]
        assert result["strata_resources"]["bucket_logs"]["count"] == 3
        assert result["strata_resources"]["bucket_logs"]["configuration"]["versioning"] is True

    def test_resources_by_type(self):
        builder = AnsibleBuilder()
        r1 = MagicMock()
        r1.name = "bucket_a"
        r1.properties.resource_type = "objectstorage"
        r1.properties.provider_type = "aws"
        r1.properties.category = "storage"
        r1.properties.subcategory = "s3"
        r1.properties.unit_cost = 0.0
        r1.annotations = {}
        r1.labels = {}
        r1.tags = []
        r1.count = 1
        r1.configuration = None
        r1.storage = None
        r1.firewalls = None
        r1.firewall = None
        r1.role = None

        r2 = MagicMock()
        r2.name = "vm_web"
        r2.properties.resource_type = "virtualmachine"
        r2.properties.provider_type = "aws"
        r2.properties.category = "compute"
        r2.properties.subcategory = "ec2"
        r2.properties.unit_cost = 0.10
        r2.annotations = {}
        r2.labels = {}
        r2.tags = []
        r2.count = 2
        r2.configuration = None
        r2.storage = None
        r2.firewalls = None
        r2.firewall = None
        r2.role = None

        platform = self._make_platform(resources=[r1, r2])
        result = builder._build_resources_by_type(platform, [])
        assert "objectstorage" in result
        assert "virtualmachine" in result
        assert "bucket_a" in result["objectstorage"]
        assert "vm_web" in result["virtualmachine"]

    def test_topology_vars(self):
        builder = AnsibleBuilder()
        comp = MagicMock()
        comp.resource = "vm_manager"
        comp.role = "manager"
        comp.count = 3
        topo = MagicMock()
        topo.name = "swarm"
        topo.type = "dockerswarm"
        topo.provider = "hetzner_eu"
        topo.provisioner = "terraform"
        topo.components = [comp]
        topo.volumes = []
        platform = self._make_platform(topologies=[topo])
        result = builder._build_topology_vars(platform, [])
        assert "swarm" in result["strata_topologies"]
        assert result["strata_topologies"]["swarm"]["components"][0]["count"] == 3


class TestAnsibleBuilderDnsVars:
    """Regression tests: var:/secret: DNS record values must not be silently dropped."""

    def test_collect_environment_variables_populates_variable_refs(self):
        builder = AnsibleBuilder()
        variable = MagicMock()
        variable.key = "public_ip"
        variable.store.value = "literal"
        variable.value = "9.9.9.9"
        env_service = MagicMock()
        env_service.model.spec.variables = [variable]
        env_service.get_name.return_value = "prod"
        deployment_service = MagicMock()
        deployment_service.get_environment_service.return_value = env_service

        builder._collect_environment_variables(deployment_service)

        assert builder.variable_refs["public_ip"]["value"] == "9.9.9.9"

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

    def test_literal_value_record_passes_through(self):
        builder = AnsibleBuilder()
        record = self._make_record(value="1.2.3.4")
        platform = self._make_platform([self._make_dns("dns1", "example.com", [record])])
        result = builder._build_dns_vars(platform, [])
        rec = result["strata_dns_zones"]["dns1"]["zones"]["example.com"]["records"][0]
        assert rec["value"] == "1.2.3.4"
        assert result["strata_dns_secret_records"] == {}

    def test_var_record_resolves_from_environment_variables(self):
        builder = AnsibleBuilder()
        builder.variable_refs = {"public_ip": {"value": "5.6.7.8"}}
        record = self._make_record(var="public_ip")
        platform = self._make_platform([self._make_dns("dns1", "example.com", [record])])
        result = builder._build_dns_vars(platform, [])
        rec = result["strata_dns_zones"]["dns1"]["zones"]["example.com"]["records"][0]
        assert rec["value"] == "5.6.7.8"

    def test_var_record_unresolved_omits_value_with_warning(self):
        builder = AnsibleBuilder()
        record = self._make_record(var="unknown_var")
        platform = self._make_platform([self._make_dns("dns1", "example.com", [record])])
        messages: list = []
        result = builder._build_dns_vars(platform, messages)
        rec = result["strata_dns_zones"]["dns1"]["zones"]["example.com"]["records"][0]
        assert "value" not in rec
        assert any("unknown_var" in m for m in messages)

    def test_secret_record_bucketed_not_dropped(self):
        builder = AnsibleBuilder()
        record = self._make_record(name="@", rtype="TXT", secret="google_verify_token")
        platform = self._make_platform([self._make_dns("dns1", "example.com", [record])])
        result = builder._build_dns_vars(platform, [])
        rec = result["strata_dns_zones"]["dns1"]["zones"]["example.com"]["records"][0]
        assert "value" not in rec
        secret_entry = result["strata_dns_secret_records"]["dns1"]["example.com"]["@_TXT"]
        assert secret_entry["secret_key"] == "google_verify_token"

    def test_output_key_record_bucketed_not_dropped(self):
        builder = AnsibleBuilder()
        record = self._make_record(name="@", rtype="A", output_key="hearth_public_ip")
        platform = self._make_platform([self._make_dns("dns1", "example.com", [record])])
        result = builder._build_dns_vars(platform, [])
        rec = result["strata_dns_zones"]["dns1"]["zones"]["example.com"]["records"][0]
        assert "value" not in rec
        output_entry = result["strata_dns_output_records"]["dns1"]["example.com"]["@_A"]
        assert output_entry["output_key"] == "hearth_public_ip"


class TestAnsibleBuilderPlannedFiles:
    def test_base_files_list_empty_sections(self):
        """With all sections empty, only workspace.yml should be planned."""
        builder = AnsibleBuilder()
        files = builder._get_planned_files(_minimal_vars_dict())
        assert "strata_workspace.yml" in files
        assert "strata_resources.yml" not in files
        assert len(files) == 1

    def test_base_files_list_full_sections(self):
        """With all sections populated, all 9 base files should be planned."""
        builder = AnsibleBuilder()
        files = builder._get_planned_files(_full_vars_dict())
        assert "strata_workspace.yml" in files
        assert "strata_resources.yml" in files
        assert len(files) == 9  # workspace + 8 non-empty sections

    def test_includes_resource_type_files(self):
        builder = AnsibleBuilder()
        files = builder._get_planned_files(_full_vars_dict(resource_types=["objectstorage", "vm"]))
        assert "strata_resx_objectstorage.yml" in files
        assert "strata_resx_vm.yml" in files
        assert len(files) == 11  # 9 base + 2 type files

    def test_dns_secrets_written_to_separate_file(self):
        """strata_dns_secrets.yml is only planned when secret: records exist, and
        strata_dns.yml does not duplicate the secret records bucket."""
        builder = AnsibleBuilder()
        vars_dict = _full_vars_dict()
        vars_dict["dns"] = {
            "strata_dns_zones": {"dns1": {}},
            "strata_dns_secret_records": {"dns1": {"example.com": {"@_TXT": {"secret_key": "tok"}}}},
        }
        pairs = dict(builder._planned_file_pairs(vars_dict))
        assert "strata_dns_secrets.yml" in pairs
        assert pairs["strata_dns_secrets.yml"] == {
            "strata_dns_secret_records": {"dns1": {"example.com": {"@_TXT": {"secret_key": "tok"}}}}
        }
        assert "strata_dns_secret_records" not in pairs["strata_dns.yml"]

    def test_dns_secrets_file_absent_when_no_secret_records(self):
        builder = AnsibleBuilder()
        files = builder._get_planned_files(_full_vars_dict())
        assert "strata_dns_secrets.yml" not in files

    def test_dns_outputs_written_to_separate_file(self):
        """strata_dns_outputs.yml is only planned when output_key: records exist, and
        strata_dns.yml does not duplicate the output records bucket."""
        builder = AnsibleBuilder()
        vars_dict = _full_vars_dict()
        vars_dict["dns"] = {
            "strata_dns_zones": {"dns1": {}},
            "strata_dns_output_records": {"dns1": {"example.com": {"@_A": {"output_key": "hearth_public_ip"}}}},
        }
        pairs = dict(builder._planned_file_pairs(vars_dict))
        assert "strata_dns_outputs.yml" in pairs
        assert pairs["strata_dns_outputs.yml"] == {
            "strata_dns_output_records": {"dns1": {"example.com": {"@_A": {"output_key": "hearth_public_ip"}}}}
        }
        assert "strata_dns_output_records" not in pairs["strata_dns.yml"]

    def test_dns_outputs_file_absent_when_no_output_records(self):
        builder = AnsibleBuilder()
        files = builder._get_planned_files(_full_vars_dict())
        assert "strata_dns_outputs.yml" not in files


def _make_provisioner(
    source_path: str, repository: str | None = None, reference: str | None = None
) -> WorkspaceIacModel:
    """Build a WorkspaceIacModel with an ansible provisioner for testing."""
    source = SourceModel(source_path=source_path, repository=repository, reference=reference)
    return WorkspaceIacModel(name="configure", provisioner=ProvisionerType.ANSIBLE, source=source)


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
    """_copy_provisioner_source resolves to work_path when repository is absent
    (mirrors TerraformBuilder's/BicepBuilder's equivalent tests)."""

    def test_no_repository_resolves_to_work_path(self, tmp_path):
        src_dir = tmp_path / "ansible"
        src_dir.mkdir()
        build_path = tmp_path / "build"
        build_path.mkdir()

        prov = _make_provisioner(source_path="ansible")
        depl_svc = _make_deployment_svc(prov, build_path)

        builder = AnsibleBuilder()
        with patch.object(builder, "_build_template_context", return_value={}):
            result = builder._copy_provisioner_source(depl_svc, build_path, tmp_path, repo_map={}, dry_run=True)

        assert result is True
        assert not builder.has_errors()
        messages = "\n".join(builder.get_messages())
        assert str(tmp_path / "ansible") in messages

    def test_no_repository_missing_src_dir_returns_false(self, tmp_path):
        build_path = tmp_path / "build"
        build_path.mkdir()

        prov = _make_provisioner(source_path="nonexistent")
        depl_svc = _make_deployment_svc(prov, build_path)

        builder = AnsibleBuilder()
        with patch.object(builder, "_build_template_context", return_value={}):
            result = builder._copy_provisioner_source(depl_svc, build_path, tmp_path, repo_map={}, dry_run=True)

        assert result is False
        assert builder.has_errors()
        assert any("nonexistent" in e for e in builder.get_errors())

    def test_with_repository_uses_repo_map(self, tmp_path):
        repo_root = tmp_path / "my-repo"
        src_dir = repo_root / "ansible"
        src_dir.mkdir(parents=True)
        build_path = tmp_path / "build"
        build_path.mkdir()

        prov = _make_provisioner(source_path="ansible", repository="my_repo")
        depl_svc = _make_deployment_svc(prov, build_path)

        builder = AnsibleBuilder()
        with patch.object(builder, "_build_template_context", return_value={}):
            result = builder._copy_provisioner_source(
                depl_svc, build_path, tmp_path, repo_map={"my_repo": str(repo_root)}, dry_run=True
            )

        assert result is True
        messages = "\n".join(builder.get_messages())
        assert str(src_dir) in messages


class TestCopyProvisionerSourceRefPinning:
    """_copy_provisioner_source honors source.reference for ref-pinned extraction.

    ADR-0071: SourceModel.reference is generic ("only valid for git-based sources"),
    not Terraform-specific — this was previously silently ignored by Ansible's copy
    step (a real bug: declaring `reference:` on an ansible provisioner validated
    successfully but had zero effect)."""

    def test_dry_run_with_reference_reports_ref(self, tmp_path):
        build_path = tmp_path / "build"
        build_path.mkdir()

        prov = _make_provisioner(source_path="ansible", repository="my_repo", reference="v1.4.0")
        depl_svc = _make_deployment_svc(prov, build_path)

        repo_root = tmp_path / "my-repo"
        repo_root.mkdir()

        builder = AnsibleBuilder()
        with patch.object(builder, "_build_template_context", return_value={}):
            result = builder._copy_provisioner_source(
                depl_svc, build_path, tmp_path, repo_map={"my_repo": str(repo_root)}, dry_run=True
            )

        assert result is True
        messages = "\n".join(builder.get_messages())
        assert "v1.4.0" in messages
        assert "DRY-RUN" in messages

    def test_no_reference_uses_standard_copy(self, tmp_path):
        src_dir = tmp_path / "ansible"
        src_dir.mkdir()
        (src_dir / "site.yml").write_text("- hosts: all")
        build_path = tmp_path / "build"
        build_path.mkdir()

        prov = _make_provisioner(source_path="ansible", reference=None)
        depl_svc = _make_deployment_svc(prov, build_path)

        builder = AnsibleBuilder()
        with patch.object(builder, "_build_template_context", return_value={}):
            result = builder._copy_provisioner_source(depl_svc, build_path, tmp_path, repo_map={}, dry_run=False)

        assert result is True
        assert (build_path / "ansible" / "site.yml").exists()

    def test_reference_on_non_git_dir_falls_back_to_copy(self, tmp_path):
        repo_root = tmp_path / "my-repo"
        src_dir = repo_root / "ansible"
        src_dir.mkdir(parents=True)
        (src_dir / "site.yml").write_text("- hosts: all")
        build_path = tmp_path / "build"
        build_path.mkdir()

        prov = _make_provisioner(source_path="ansible", repository="my_repo", reference="v1.0.0")
        depl_svc = _make_deployment_svc(prov, build_path)

        builder = AnsibleBuilder()
        with patch.object(builder, "_build_template_context", return_value={}):
            result = builder._copy_provisioner_source(
                depl_svc, build_path, tmp_path, repo_map={"my_repo": str(repo_root)}, dry_run=False
            )

        assert result is True
        assert (build_path / "ansible" / "site.yml").exists()
        messages = "\n".join(builder.get_messages())
        assert "not a git repository" in messages
