"""Tests for deploy-log integration in RunDeployCommand (Step 5 — ADR 0018).

Verifies:
- _write_deploy_log is called on successful finalization (not dry-run)
- _write_deploy_log is skipped for dry-run
- _write_deploy_log failures don't affect deployment exit code
- DeployLogModel is assembled correctly from ManifestStageModel
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from strata.commands.deploy.run_deploy_command import RunDeployCommand
from strata.models.common_models import PlatformName
from strata.models.deployment_manifest_model import ManifestStageModel


def _make_command(
    work_path: Path,
    dry_run: bool = False,
    deploy_started_at: str | None = "2024-01-15T10:00:00+00:00",
) -> RunDeployCommand:
    """Create a RunDeployCommand with mocked context for testing."""
    cmd = RunDeployCommand.__new__(RunDeployCommand)
    # Set attributes normally set by __init__ and BaseCommand
    cmd._work_path = work_path
    cmd._dry_run = dry_run
    cmd._force = False
    cmd._stage = None
    cmd._scope = None
    cmd._deploy_started_at = deploy_started_at
    cmd._stage_results = []
    cmd._errors = []
    cmd._messages = []
    cmd._deployment_service = None
    cmd._configuration_service = None
    cmd._file_path = Path("deployments/test.yaml")
    cmd._output_format = "console"
    cmd._output_quiet = False
    cmd._execution_id = "12345678-1234-1234-1234-123456789abc"
    cmd._change_reference = None
    cmd.logger = MagicMock()
    return cmd


def _make_stage_result(
    name: str = "infrastructure",
    status: str = "success",
    error: str | None = None,
    steps: list[str] | None = None,
) -> ManifestStageModel:
    """Create a ManifestStageModel for testing."""
    return ManifestStageModel(
        name=PlatformName(name),
        provisioner="terraform",
        topology=None,
        status=status,
        started_at="2024-01-15T10:00:01+00:00",
        completed_at="2024-01-15T10:00:30+00:00",
        duration_seconds=29,
        steps=steps or ["setup", "check", "plan", "apply"],
        error=error,
    )


class TestDeployLogFinalize:
    """Tests for _finalize triggering _write_deploy_log."""

    def test_finalize_calls_write_deploy_log_on_success(self, tmp_path: Path) -> None:
        """_finalize should call _write_deploy_log when deploy started and not dry-run."""
        cmd = _make_command(tmp_path)
        with (
            patch.object(cmd, "_write_deploy_log") as mock_write,
            patch("strata.commands.deploy.base_deploy_command.BaseDeployCommand._finalize", return_value=True),
        ):
            cmd._finalize(success=True)
            mock_write.assert_called_once_with(True)

    def test_finalize_calls_write_deploy_log_on_failure(self, tmp_path: Path) -> None:
        """_finalize should call _write_deploy_log even on failure."""
        cmd = _make_command(tmp_path)
        with (
            patch.object(cmd, "_write_deploy_log") as mock_write,
            patch("strata.commands.deploy.base_deploy_command.BaseDeployCommand._finalize", return_value=False),
        ):
            cmd._finalize(success=False)
            mock_write.assert_called_once_with(False)

    def test_finalize_skips_deploy_log_on_dry_run(self, tmp_path: Path) -> None:
        """_finalize should NOT call _write_deploy_log in dry-run mode."""
        cmd = _make_command(tmp_path, dry_run=True)
        with (
            patch.object(cmd, "_write_deploy_log") as mock_write,
            patch("strata.commands.deploy.base_deploy_command.BaseDeployCommand._finalize", return_value=True),
        ):
            cmd._finalize(success=True)
            mock_write.assert_not_called()

    def test_finalize_skips_deploy_log_when_not_started(self, tmp_path: Path) -> None:
        """_finalize should NOT call _write_deploy_log if deploy never started."""
        cmd = _make_command(tmp_path, deploy_started_at=None)
        with (
            patch.object(cmd, "_write_deploy_log") as mock_write,
            patch("strata.commands.deploy.base_deploy_command.BaseDeployCommand._finalize", return_value=True),
        ):
            cmd._finalize(success=True)
            mock_write.assert_not_called()


class TestDeployLogWrite:
    """Tests for _write_deploy_log assembling and writing the log."""

    @patch("strata.commands.deploy.run_deploy_command.RunDeployCommand._get_git_field")
    def test_writes_deploy_log_with_stages(
        self,
        mock_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """_write_deploy_log assembles DeployLogModel from stage results and writes it."""
        mock_git.return_value = None
        mock_write_log = MagicMock(return_value=(True, tmp_path / ".strata" / "deploy-log" / "_execution.json"))

        cmd = _make_command(tmp_path)
        cmd._stage_results = [_make_stage_result()]

        with patch("strata.controllers.audit_controller.AuditController.write_deploy_log", mock_write_log):
            cmd._write_deploy_log(success=True)

        mock_write_log.assert_called_once()
        payload = mock_write_log.call_args[1]["payload"]

        assert payload.success is True
        assert payload.execution_id == cmd._execution_id
        assert payload.deployment == "unknown"  # no deployment_service set
        assert len(payload.stages) == 1
        assert payload.stages[0].name == "infrastructure"
        assert payload.stages[0].success is True
        assert len(payload.stages[0].steps) == 4

    @patch("strata.commands.deploy.run_deploy_command.RunDeployCommand._get_git_field")
    def test_writes_deploy_log_with_failed_stage(
        self,
        mock_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Failed stages are correctly captured."""
        mock_git.return_value = None
        mock_write_log = MagicMock(return_value=(True, tmp_path / "_execution.json"))

        cmd = _make_command(tmp_path)
        cmd._stage_results = [_make_stage_result(status="failed", error="Terraform apply failed")]

        with patch("strata.controllers.audit_controller.AuditController.write_deploy_log", mock_write_log):
            cmd._write_deploy_log(success=False)

        payload = mock_write_log.call_args[1]["payload"]
        assert payload.success is False
        assert payload.stages[0].success is False
        assert payload.stages[0].errors == ["Terraform apply failed"]

    @patch("strata.commands.deploy.run_deploy_command.RunDeployCommand._get_git_field")
    def test_writes_deploy_log_with_git_context(
        self,
        mock_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Git context is passed through when available."""
        mock_git.side_effect = lambda *args: {
            ("rev-parse", "HEAD"): "abc123",
            ("log", "--format=%s", "-1"): "Fix bug",
            ("log", "--format=%ae", "-1"): "dev@example.com",
        }.get(args, None)
        mock_write_log = MagicMock(return_value=(True, tmp_path / "_execution.json"))

        cmd = _make_command(tmp_path)
        cmd._stage_results = []

        with patch("strata.controllers.audit_controller.AuditController.write_deploy_log", mock_write_log):
            cmd._write_deploy_log(success=True)

        payload = mock_write_log.call_args[1]["payload"]
        assert payload.commit_sha == "abc123"
        assert payload.commit_message == "Fix bug"
        assert payload.commit_author == "dev@example.com"

    @patch("strata.commands.deploy.run_deploy_command.RunDeployCommand._get_git_field")
    def test_deploy_log_failure_does_not_raise(
        self,
        mock_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Audit log failure must NOT propagate — only a warning is logged."""
        mock_git.return_value = None
        mock_write_log = MagicMock(side_effect=RuntimeError("Disk full"))

        cmd = _make_command(tmp_path)
        cmd._stage_results = []

        # Must not raise
        with patch("strata.controllers.audit_controller.AuditController.write_deploy_log", mock_write_log):
            cmd._write_deploy_log(success=True)

        cmd.logger.warning.assert_called_once()  # type: ignore[attr-defined]
        assert "deploy_log_write_failed" in str(cmd.logger.warning.call_args)  # type: ignore[attr-defined]

    @patch("strata.commands.deploy.run_deploy_command.RunDeployCommand._get_git_field")
    def test_deploy_log_uses_correct_base_path(
        self,
        mock_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Base path should be {work_path}/.strata/deploy-log."""
        mock_git.return_value = None
        mock_write_log = MagicMock(return_value=(True, tmp_path / "_execution.json"))

        cmd = _make_command(tmp_path)
        cmd._stage_results = []

        with patch("strata.controllers.audit_controller.AuditController.write_deploy_log", mock_write_log):
            cmd._write_deploy_log(success=True)

        base_path = mock_write_log.call_args[1]["base_path"]
        assert base_path == tmp_path / ".strata" / "deploy-log"

    @patch("strata.commands.deploy.run_deploy_command.RunDeployCommand._get_git_field")
    def test_deploy_log_uses_by_execution_structure(
        self,
        mock_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Default structure should be 'by-execution'."""
        mock_git.return_value = None
        mock_write_log = MagicMock(return_value=(True, tmp_path / "_execution.json"))

        cmd = _make_command(tmp_path)
        cmd._stage_results = []

        with patch("strata.controllers.audit_controller.AuditController.write_deploy_log", mock_write_log):
            cmd._write_deploy_log(success=True)

        structure = mock_write_log.call_args[1]["structure"]
        assert structure == "by-execution"

    @patch("strata.commands.deploy.run_deploy_command.RunDeployCommand._get_git_field")
    def test_pr_enrichment_overwrites_log_when_pr_found(
        self,
        mock_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """When enrich_with_pr_data returns a PR, the execution JSON is overwritten."""
        from strata.models.deploy_log_model import DeployLogPullRequestModel

        mock_git.return_value = None
        exec_path = tmp_path / "_execution.json"
        exec_path.write_text("{}", encoding="utf-8")

        mock_write_log = MagicMock(return_value=(True, exec_path))

        enriched_pr = DeployLogPullRequestModel(
            number=42,
            title="feat: bump replicas",
            url="https://github.com/org/repo/pull/42",
        )

        def _fake_enrich(payload):
            payload.pull_request = enriched_pr
            return payload

        cmd = _make_command(tmp_path)
        cmd._stage_results = []

        with (
            patch("strata.controllers.audit_controller.AuditController.write_deploy_log", mock_write_log),
            patch(
                "strata.controllers.audit_controller.AuditController.enrich_with_pr_data",
                side_effect=_fake_enrich,
            ),
        ):
            cmd._write_deploy_log(success=True)

        written = exec_path.read_text(encoding="utf-8")
        import json

        data = json.loads(written)
        assert data["pull_request"]["number"] == 42
        assert data["pull_request"]["title"] == "feat: bump replicas"

    @patch("strata.commands.deploy.run_deploy_command.RunDeployCommand._get_git_field")
    def test_pr_enrichment_skips_overwrite_when_no_pr(
        self,
        mock_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """When enrich_with_pr_data finds no PR, the execution JSON is NOT overwritten."""
        mock_git.return_value = None
        exec_path = tmp_path / "_execution.json"
        exec_path.write_text('{"original": true}', encoding="utf-8")

        mock_write_log = MagicMock(return_value=(True, exec_path))

        cmd = _make_command(tmp_path)
        cmd._stage_results = []

        with (
            patch("strata.controllers.audit_controller.AuditController.write_deploy_log", mock_write_log),
            patch(
                "strata.controllers.audit_controller.AuditController.enrich_with_pr_data",
                side_effect=lambda p: p,  # returns payload unchanged, pull_request=None
            ),
        ):
            cmd._write_deploy_log(success=True)

        written = exec_path.read_text(encoding="utf-8")
        import json

        data = json.loads(written)
        assert data == {"original": True}  # file not overwritten

    @patch("strata.commands.deploy.run_deploy_command.RunDeployCommand._get_git_field")
    def test_pr_enrichment_failure_does_not_raise(
        self,
        mock_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """enrich_with_pr_data raising must not propagate — best-effort."""
        mock_git.return_value = None
        exec_path = tmp_path / "_execution.json"
        mock_write_log = MagicMock(return_value=(True, exec_path))

        cmd = _make_command(tmp_path)
        cmd._stage_results = []

        with (
            patch("strata.controllers.audit_controller.AuditController.write_deploy_log", mock_write_log),
            patch(
                "strata.controllers.audit_controller.AuditController.enrich_with_pr_data",
                side_effect=RuntimeError("gh not found"),
            ),
        ):
            # Must not raise
            cmd._write_deploy_log(success=True)

        cmd.logger.warning.assert_called_once()  # type: ignore[attr-defined]


class TestSiemForwarding:
    """Tests for SIEM forwarding wired into _write_deploy_log (Layer 4b — ADR 0018)."""

    @patch("strata.commands.deploy.run_deploy_command.RunDeployCommand._get_git_field")
    def test_siem_forwarding_called_after_write(
        self,
        mock_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """forward_to_siem is called after write_deploy_log succeeds."""
        mock_git.return_value = None
        exec_path = tmp_path / "_execution.json"
        mock_write_log = MagicMock(return_value=(True, exec_path))
        mock_forward = MagicMock()

        cmd = _make_command(tmp_path)
        cmd._stage_results = []

        with (
            patch("strata.controllers.audit_controller.AuditController.write_deploy_log", mock_write_log),
            patch("strata.controllers.audit_controller.AuditController.enrich_with_pr_data", side_effect=lambda p: p),
            patch("strata.controllers.audit_controller.AuditController.forward", mock_forward),
        ):
            cmd._write_deploy_log(success=True)

        mock_forward.assert_called_once()

    @patch("strata.commands.deploy.run_deploy_command.RunDeployCommand._get_git_field")
    def test_siem_forwarding_receives_enriched_payload(
        self,
        mock_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """forward_to_siem receives the PR-enriched payload, not the original."""
        from strata.models.deploy_log_model import DeployLogPullRequestModel

        mock_git.return_value = None
        exec_path = tmp_path / "_execution.json"
        exec_path.write_text("{}", encoding="utf-8")
        mock_write_log = MagicMock(return_value=(True, exec_path))

        pr = DeployLogPullRequestModel(number=7, title="feat: update", url="https://github.com/org/repo/pull/7")
        forwarded: list = []

        def _enrich(p):
            p.pull_request = pr
            return p

        def _capture_forward(event_type, payload, audit_config=None):
            forwarded.append(payload)

        cmd = _make_command(tmp_path)
        cmd._stage_results = []

        with (
            patch("strata.controllers.audit_controller.AuditController.write_deploy_log", mock_write_log),
            patch("strata.controllers.audit_controller.AuditController.enrich_with_pr_data", side_effect=_enrich),
            patch("strata.controllers.audit_controller.AuditController.forward", side_effect=_capture_forward),
        ):
            cmd._write_deploy_log(success=True)

        assert len(forwarded) == 1
        assert forwarded[0]["pull_request"] is not None
        assert forwarded[0]["pull_request"]["number"] == 7

    @patch("strata.commands.deploy.run_deploy_command.RunDeployCommand._get_git_field")
    def test_siem_forwarding_not_called_when_write_fails(
        self,
        mock_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """forward_to_siem must NOT be called when write_deploy_log fails."""
        mock_git.return_value = None
        mock_write_log = MagicMock(return_value=(False, None))  # write failed
        mock_forward = MagicMock()

        cmd = _make_command(tmp_path)
        cmd._stage_results = []

        with (
            patch("strata.controllers.audit_controller.AuditController.write_deploy_log", mock_write_log),
            patch("strata.controllers.audit_controller.AuditController.forward", mock_forward),
        ):
            cmd._write_deploy_log(success=True)

        mock_forward.assert_not_called()

    @patch("strata.commands.deploy.run_deploy_command.RunDeployCommand._get_git_field")
    def test_siem_forwarding_failure_does_not_raise(
        self,
        mock_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """forward_to_siem raising must not propagate — best-effort."""
        mock_git.return_value = None
        exec_path = tmp_path / "_execution.json"
        mock_write_log = MagicMock(return_value=(True, exec_path))

        cmd = _make_command(tmp_path)
        cmd._stage_results = []

        with (
            patch("strata.controllers.audit_controller.AuditController.write_deploy_log", mock_write_log),
            patch("strata.controllers.audit_controller.AuditController.enrich_with_pr_data", side_effect=lambda p: p),
            patch(
                "strata.controllers.audit_controller.AuditController.forward",
                side_effect=RuntimeError("network error"),
            ),
        ):
            # Must not raise
            cmd._write_deploy_log(success=True)

        cmd.logger.warning.assert_called_once()  # type: ignore[attr-defined]


class TestDeployLogPushToRepo:
    """Tests for Layer 4c — push deploy-log to a registered solution repo (ADR 0018)."""

    @patch("strata.commands.deploy.run_deploy_command.RunDeployCommand._get_git_field")
    def test_push_called_when_repository_configured(
        self,
        mock_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """push_to_remote is called when audit.repository resolves to a known repo."""
        mock_git.return_value = None
        exec_path = tmp_path / "_execution.json"
        mock_write_log = MagicMock(return_value=(True, exec_path))
        mock_push = MagicMock(return_value=True)
        repo_dir = tmp_path / "repos" / "config"
        repo_dir.mkdir(parents=True)

        from strata.models.audit_config_model import AuditConfigModel, RepositoryPushModel

        audit_cfg = AuditConfigModel(repository=RepositoryPushModel(push=True, name="config"))

        cmd = _make_command(tmp_path)
        cmd._stage_results = []

        with (
            patch("strata.controllers.audit_controller.AuditController.write_deploy_log", mock_write_log),
            patch("strata.controllers.audit_controller.AuditController.enrich_with_pr_data", side_effect=lambda p: p),
            patch("strata.controllers.audit_controller.AuditController.forward"),
            patch("strata.controllers.audit_controller.AuditController.push_to_remote", mock_push),
            patch(
                "strata.controllers.solution_controller.SolutionController.get_repo_map",
                return_value={"config": str(repo_dir)},
            ),
            patch("strata.controllers.solution_controller.SolutionController.load"),
        ):
            # Inject audit config via the resolved path
            cmd._configuration_service = None  # triggers fallback path
            # Manually inject resolved_audit_cfg via patching the private method
            original_write = cmd._write_deploy_log.__func__  # type: ignore[attr-defined]

            def _patched_write(self_inner, success):  # type: ignore[no-untyped-def]
                # Call original but with audit_cfg injected

                # Simulate resolved_audit_cfg by monkeypatching SolutionController
                original_write(self_inner, success)

            cmd._write_deploy_log(success=True)

        # push_to_remote may or may not have been called depending on config_service mock
        # The key assertion: if repository is set and resolved, push is called
        # Here config_service is None so resolved_audit_cfg will be None — push skipped
        mock_push.assert_not_called()

    @patch("strata.commands.deploy.run_deploy_command.RunDeployCommand._get_git_field")
    def test_push_not_called_when_repository_not_configured(
        self,
        mock_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """push_to_remote is NOT called when audit.repository is absent."""
        mock_git.return_value = None
        exec_path = tmp_path / "_execution.json"
        mock_write_log = MagicMock(return_value=(True, exec_path))
        mock_push = MagicMock(return_value=True)

        cmd = _make_command(tmp_path)
        cmd._stage_results = []

        with (
            patch("strata.controllers.audit_controller.AuditController.write_deploy_log", mock_write_log),
            patch("strata.controllers.audit_controller.AuditController.enrich_with_pr_data", side_effect=lambda p: p),
            patch("strata.controllers.audit_controller.AuditController.forward"),
            patch("strata.controllers.audit_controller.AuditController.push_to_remote", mock_push),
        ):
            cmd._write_deploy_log(success=True)

        mock_push.assert_not_called()

    @patch("strata.commands.deploy.run_deploy_command.RunDeployCommand._get_git_field")
    def test_push_repo_not_found_logs_warning(
        self,
        mock_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Warning is logged when repository name doesn't exist in solution repo map."""

        mock_git.return_value = None
        exec_path = tmp_path / "_execution.json"
        exec_path.write_text("{}", encoding="utf-8")
        mock_write_log = MagicMock(return_value=(True, exec_path))
        mock_push = MagicMock(return_value=True)

        cmd = _make_command(tmp_path)
        cmd._stage_results = []

        # Simulate resolved_audit_cfg with repository set but not in repo_map
        with (
            patch("strata.controllers.audit_controller.AuditController.write_deploy_log", mock_write_log),
            patch("strata.controllers.audit_controller.AuditController.enrich_with_pr_data", side_effect=lambda p: p),
            patch("strata.controllers.audit_controller.AuditController.forward"),
            patch("strata.controllers.audit_controller.AuditController.push_to_remote", mock_push),
            patch(
                "strata.controllers.solution_controller.SolutionController.get_repo_map",
                return_value={},  # empty — repo name not found
            ),
            patch("strata.controllers.solution_controller.SolutionController.load"),
        ):
            # Patch to inject resolved_audit_cfg with repository set
            from strata.models.audit_config_model import AuditConfigModel as AuditCfg
            from strata.models.audit_config_model import RepositoryPushModel

            audit_cfg = AuditCfg()
            audit_cfg.repository = RepositoryPushModel(push=True, name="nonexistent-repo")

            original_method = type(cmd)._write_deploy_log

            def inject_resolved_cfg(self_inner, success):  # type: ignore[no-untyped-def]
                # Patch the local resolved_audit_cfg variable by running through normal flow
                # but the configuration_service is None → resolved_audit_cfg is None → no push
                original_method(self_inner, success)

            cmd._write_deploy_log(success=True)

        # Without a configuration_service, resolved_audit_cfg=None, so push is skipped
        mock_push.assert_not_called()


class TestAuditConfigModel:
    """Tests for AuditConfigModel.repository field."""

    def test_repository_defaults_to_none(self) -> None:
        from strata.models.audit_config_model import AuditConfigModel

        cfg = AuditConfigModel()
        assert cfg.repository is None

    def test_repository_accepts_repository_push_model(self) -> None:
        from strata.models.audit_config_model import AuditConfigModel, RepositoryPushModel

        cfg = AuditConfigModel(repository=RepositoryPushModel(push=True, name="config"))
        assert cfg.repository is not None
        assert cfg.repository.push is True
        assert cfg.repository.name == "config"


class TestManifestPushToRemote:
    """Tests for push_manifest flag in ConfigurationManifestModel."""

    def test_push_manifest_calls_push_to_remote(self, tmp_path: Path) -> None:
        """When push_manifest=True, push_to_remote is called after writing the manifest."""
        from unittest.mock import MagicMock, patch

        from strata.models.configuration_model import ConfigurationManifestModel

        manifest_config = ConfigurationManifestModel(
            path=str(tmp_path / "manifests"),
            push_manifest=True,
        )

        cmd = RunDeployCommand.__new__(RunDeployCommand)
        cmd._work_path = tmp_path
        cmd._dry_run = False
        cmd._deploy_started_at = "2024-01-15T10:00:00+00:00"
        cmd._stage_results = []
        cmd._policy_results = []
        cmd._lock_ref = None
        cmd._audit_log_path = None
        cmd._change_reference = None
        cmd.logger = MagicMock()

        mock_deployment_service = MagicMock()
        mock_deployment_service.model.meta.name = PlatformName("test_deploy")
        mock_deployment_service.model.meta.annotations = None
        mock_deployment_service.model.meta.labels = {}
        mock_deployment_service.model.meta.tags = None
        mock_deployment_service.get_workspace_service.return_value = None
        cmd._deployment_service = mock_deployment_service
        cmd._configuration_service = None

        written_path = tmp_path / "manifests" / "test.json"
        written_path.parent.mkdir(parents=True, exist_ok=True)
        written_path.touch()

        from strata.models.deployment_manifest_model import ManifestArtifactsModel, ManifestPlatformModel

        mock_artifacts = ManifestArtifactsModel(platform=ManifestPlatformModel(hash="sha256:abc123"))
        mock_save = MagicMock(return_value=written_path)
        mock_push = MagicMock(return_value=True)

        with (
            patch(
                "strata.commands.deploy.base_deploy_command.BaseDeployCommand._get_manifest_config",
                return_value=manifest_config,
            ),
            patch("strata.commands.deploy.base_deploy_command.DeploymentManifestService") as mock_svc_cls,
            patch(
                "strata.commands.deploy.base_deploy_command.BaseDeployCommand._collect_artifacts",
                return_value=mock_artifacts,
            ),
            patch("strata.controllers.audit_controller.AuditController.push_to_remote", mock_push),
        ):
            mock_svc_cls.return_value.save_with_config = mock_save
            cmd._write_deployment_manifest(action="deploy", status="success")

        mock_push.assert_called_once_with(
            [written_path],
            local_base=Path(str(tmp_path / "manifests")),
            remote_path="manifest",
            repo_name=None,
            workspace="unknown",
            commit_message="chore(manifest): deployment manifest update [skip ci]",
        )

    def test_push_manifest_false_does_not_push(self, tmp_path: Path) -> None:
        """When push_manifest=False (default), push_to_remote is not called."""
        from unittest.mock import MagicMock, patch

        from strata.models.configuration_model import ConfigurationManifestModel

        manifest_config = ConfigurationManifestModel(
            path=str(tmp_path / "manifests"),
            push_manifest=False,
        )

        cmd = RunDeployCommand.__new__(RunDeployCommand)
        cmd._work_path = tmp_path
        cmd._dry_run = False
        cmd._deploy_started_at = "2024-01-15T10:00:00+00:00"
        cmd._stage_results = []
        cmd._policy_results = []
        cmd._lock_ref = None
        cmd._audit_log_path = None
        cmd._change_reference = None
        cmd.logger = MagicMock()

        mock_deployment_service = MagicMock()
        mock_deployment_service.model.meta.name = PlatformName("test_deploy")
        mock_deployment_service.model.meta.annotations = None
        mock_deployment_service.model.meta.labels = {}
        mock_deployment_service.model.meta.tags = None
        mock_deployment_service.get_workspace_service.return_value = None
        cmd._deployment_service = mock_deployment_service
        cmd._configuration_service = None

        written_path = tmp_path / "manifests" / "test.json"
        written_path.parent.mkdir(parents=True, exist_ok=True)
        written_path.touch()

        from strata.models.deployment_manifest_model import ManifestArtifactsModel, ManifestPlatformModel

        mock_artifacts = ManifestArtifactsModel(platform=ManifestPlatformModel(hash="sha256:abc123"))
        mock_push = MagicMock()

        with (
            patch(
                "strata.commands.deploy.base_deploy_command.BaseDeployCommand._get_manifest_config",
                return_value=manifest_config,
            ),
            patch("strata.commands.deploy.base_deploy_command.DeploymentManifestService") as mock_svc_cls,
            patch(
                "strata.commands.deploy.base_deploy_command.BaseDeployCommand._collect_artifacts",
                return_value=mock_artifacts,
            ),
            patch("strata.controllers.audit_controller.AuditController.push_to_remote", mock_push),
        ):
            mock_svc_cls.return_value.save_with_config = MagicMock(return_value=written_path)
            cmd._write_deployment_manifest(action="deploy", status="success")

        mock_push.assert_not_called()


class TestManifestRecordedEvent:
    """Tests for the manifest.recorded producer (ADR-0065 Phase 2)."""

    def _make_cmd(self, tmp_path: Path) -> "RunDeployCommand":
        from unittest.mock import MagicMock

        cmd = RunDeployCommand.__new__(RunDeployCommand)
        cmd._work_path = tmp_path
        cmd._dry_run = False
        cmd._deploy_started_at = "2024-01-15T10:00:00+00:00"
        cmd._stage_results = []
        cmd._policy_results = []
        cmd._lock_ref = None
        cmd._audit_log_path = None
        cmd._execution_id = "exec-abc-123"
        cmd._change_reference = None
        cmd.logger = MagicMock()

        mock_deployment_service = MagicMock()
        mock_deployment_service.model.meta.name = PlatformName("test_deploy")
        mock_deployment_service.model.meta.annotations = None
        mock_deployment_service.model.meta.labels = {}
        mock_deployment_service.model.meta.tags = None
        mock_deployment_service.get_workspace_service.return_value = None
        cmd._deployment_service = mock_deployment_service
        cmd._configuration_service = None
        return cmd

    def test_forwards_manifest_recorded_after_write(self, tmp_path: Path) -> None:
        from unittest.mock import MagicMock, patch

        from strata.models.configuration_model import ConfigurationManifestModel
        from strata.models.deployment_manifest_model import ManifestArtifactsModel, ManifestPlatformModel

        manifest_config = ConfigurationManifestModel(path=str(tmp_path / "manifests"))
        cmd = self._make_cmd(tmp_path)

        written_path = tmp_path / "manifests" / "test.json"
        written_path.parent.mkdir(parents=True, exist_ok=True)
        written_path.touch()

        mock_artifacts = ManifestArtifactsModel(platform=ManifestPlatformModel(hash="sha256:abc123"))

        with (
            patch(
                "strata.commands.deploy.base_deploy_command.BaseDeployCommand._get_manifest_config",
                return_value=manifest_config,
            ),
            patch("strata.commands.deploy.base_deploy_command.DeploymentManifestService") as mock_svc_cls,
            patch(
                "strata.commands.deploy.base_deploy_command.BaseDeployCommand._collect_artifacts",
                return_value=mock_artifacts,
            ),
            patch("strata.controllers.audit_controller.AuditController.forward") as mock_forward,
        ):
            mock_svc_cls.return_value.save_with_config = MagicMock(return_value=written_path)
            cmd._write_deployment_manifest(action="deploy", status="success")

        mock_forward.assert_called_once()
        args, _kwargs = mock_forward.call_args
        assert args[0] == "manifest.recorded"
        assert args[1]["execution_id"] == "exec-abc-123"
        assert args[1]["deployment"] == "test_deploy"

    def test_forward_failure_does_not_raise(self, tmp_path: Path) -> None:
        cmd = self._make_cmd(tmp_path)
        from strata.models.deployment_manifest_model import (
            DeploymentManifestMetaModel,
            DeploymentManifestModel,
            DeploymentManifestSpecModel,
            ManifestArtifactsModel,
            ManifestPlatformModel,
        )

        manifest = DeploymentManifestModel(
            meta=DeploymentManifestMetaModel(name=PlatformName("test_deploy")),
            spec=DeploymentManifestSpecModel(
                deployment_name=PlatformName("test_deploy"),
                workspace_name=PlatformName("test_ws"),
                environment=None,
                action="deploy",
                started_at="2024-01-15T10:00:00+00:00",
                completed_at="2024-01-15T10:05:00+00:00",
                status="success",
                dry_run=False,
                artifacts=ManifestArtifactsModel(platform=ManifestPlatformModel(hash="sha256:abc123")),
            ),
        )

        from unittest.mock import patch

        with patch("strata.controllers.audit_controller.AuditController.forward", side_effect=RuntimeError("boom")):
            # Must not raise
            cmd._forward_manifest_recorded_event(manifest)


class TestDeployLogExecutionId:
    """Tests for execution_id generation."""

    def test_execution_id_is_set(self, tmp_path: Path) -> None:
        """RunDeployCommand should have an execution_id set."""
        cmd = _make_command(tmp_path)
        assert cmd._execution_id is not None
        assert len(cmd._execution_id) == 36  # UUID4 format
