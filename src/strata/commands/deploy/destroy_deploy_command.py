from datetime import datetime as _dt
from datetime import timezone as _tz
from typing import List, Optional

import click

from strata.commands.deploy.base_deploy_command import BaseDeployCommand
from strata.controllers.value_controller import ResolvedValues, ValueController
from strata.deployers.base_deployer import (
    STEP_DESTROY,
    STEP_PLAN_DESTROY,
    STEP_SETUP,
)
from strata.integrations.lock.base_lock_backend import (
    BaseLockBackend,
    LockHandle,
)
from strata.models.deployment_model import DeploymentStageModel


class DestroyDeployCommand(BaseDeployCommand):
    """Tear down provisioned infrastructure for a deployment definition.

    Step sequences:
        --dry-run        : setup → plan_destroy  (shows what would be removed)
        --force          : setup → destroy       (auto-approve, non-interactive)
    """

    OPERATION = "deploy_destroy"

    def __init__(
        self,
        file: Optional[str] = None,
        work_path: Optional[str] = None,
        stage: Optional[str] = None,
        scope: Optional[str] = None,
        force: bool = False,
        dry_run: bool = False,
        force_lock: bool = False,
        timeout: int = 0,
        change_id: Optional[str] = None,
        change_system: Optional[str] = None,
        change_title: Optional[str] = None,
        change_url: Optional[str] = None,
        change_classification: Optional[str] = None,
        change_reason: Optional[str] = None,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ):
        super().__init__(
            file=file,
            work_path=work_path,
            output=output,
            verbose=verbose,
            quiet=quiet,
            change_id=change_id,
            change_system=change_system,
            change_title=change_title,
            change_url=change_url,
            change_classification=change_classification,
            change_reason=change_reason,
        )
        self._stage = stage
        self._scope = scope
        self._force = force
        self._dry_run = dry_run
        self._force_lock = force_lock
        self._timeout = timeout
        self._resolved_values: Optional[ResolvedValues] = None

    # -------------------------------------------------------------------------
    # Finalize override — writes deploy-log before standard finalization
    # -------------------------------------------------------------------------

    def _finalize(self, success: bool = False, show_footer: bool = True) -> bool:
        """Write deploy-log audit evidence for the destroy, then delegate to parent finalize.

        ADR-0066 gap B: destroy previously produced only a deployment manifest
        (BOM/artifact tracking) and no deploy-log / SIEM-forwarded event at all —
        a destructive, irreversible action was less observable than a routine
        deploy. Reuses the same shared helper ``RunDeployCommand`` uses, with
        ``deployment.destroyed`` instead of ``deployment.completed``.
        """
        if self._deploy_started_at and not self._dry_run:
            self._write_deploy_log_and_forward("deployment.destroyed", success)
        return super()._finalize(success=success, show_footer=show_footer)

    # -------------------------------------------------------------------------
    # Public entry point
    # -------------------------------------------------------------------------

    def _execute(self) -> bool:
        try:
            self._record_deploy_start()

            if not self._resolve_values():
                if self._is_console_output():
                    click.echo("\n❌  Failed to resolve variables/secrets/features")
                self._write_deployment_manifest(action="destroy", status="failed", dry_run=self._dry_run)
                return False

            if self._dry_run and self._is_console_output():
                click.echo("\n[DRY-RUN] Planning destroy \u2014 no infrastructure will be removed")
            elif self._is_console_output():
                click.echo("\n⚠️  --destroy: removing provisioned infrastructure per stage")

            if not self._run_lifecycle_phase(
                "deploy_destroy_before",
                context={"file": str(self._file_path), "stage": self._stage, "dry_run": self._dry_run},
            ):
                if self._is_console_output():
                    click.echo("\n❌  Pre-destroy lifecycle hook failed")
                self._write_deployment_manifest(action="destroy", status="failed", dry_run=self._dry_run)
                return False

            if not self._execute_provisioning():
                if self._is_console_output():
                    click.echo("\n❌  Destroy failed")
                self._write_deployment_manifest(action="destroy", status="failed", dry_run=self._dry_run)
                return False

            if not self._run_lifecycle_phase(
                "deploy_destroy_after",
                context={"file": str(self._file_path), "stage": self._stage, "dry_run": self._dry_run},
            ):
                if self._is_console_output():
                    click.echo("\n❌  Post-destroy lifecycle hook failed")
                self._write_deployment_manifest(action="destroy", status="failed", dry_run=self._dry_run)
                return False

            self._output_data.update(
                {
                    "file": str(self._file_path),
                    "build_path": str(self._build_path),
                    "stage": self._stage,
                    "force": self._force,
                    "dry_run": self._dry_run,
                }
            )

            manifest_path = self._write_deployment_manifest(
                action="destroy",
                status="success",
                dry_run=self._dry_run,
            )
            if manifest_path and self._is_console_output():
                click.echo(f"\n📋  Deployment manifest: {manifest_path}")

            return True

        except Exception as exc:
            self._errors.append(f"Failed to execute deploy_destroy: {exc}")
            self.logger.exception("deploy_destroy failed")
            self._write_deployment_manifest(action="destroy", status="failed", dry_run=self._dry_run)
            return False

    # -------------------------------------------------------------------------
    # Internal pipeline steps
    # -------------------------------------------------------------------------

    def _resolve_values(self, strict: bool = False) -> bool:
        controller = ValueController()
        ok, resolved, errors = controller.resolve_values(
            self._deployment_service,  # type: ignore[arg-type]
            strict=False,  # type: ignore[arg-type]
        )
        self._resolved_values = resolved
        if errors:
            for err in errors:
                self.logger.warning("Value resolution warning: %s", err)
        return True

    def _execute_provisioning(self) -> bool:
        if self._deployment_service is None:
            self._errors.append("Deployment service not loaded")
            return False
        spec = self._deployment_service.model.spec  # type: ignore[union-attr]
        all_stages: List[DeploymentStageModel] = spec.stages or []

        if not all_stages:
            if self._is_console_output():
                click.echo("⚠️  No deployment stages defined — nothing to destroy.")
            return True

        stages_to_run = [s for s in all_stages if s.name == self._stage] if self._stage else all_stages

        if self._stage and not stages_to_run:
            self._errors.append(f"Stage '{self._stage}' not found. Available: {[s.name for s in all_stages]}")
            return False

        # Filter by --scope label when supplied
        if self._scope:
            stages_to_run = [s for s in stages_to_run if s.scope == self._scope]
            if not stages_to_run:
                self._errors.append(
                    f"No stages match scope '{self._scope}'. "
                    f"Available scopes: {[s.scope for s in all_stages if s.scope]}"
                )
                return False

        # ADR-0074 Phase 2 — 'destroy_before' policies (e.g. change_reference_required)
        # evaluate once per run, before any stage executes — same fail-fast spot and
        # shared implementation RunDeployCommand uses for 'deploy_before'. A distinct
        # phase (not a reuse of 'deploy_before') so a workspace can require a change
        # reference under different strictness for destroy vs. deploy. Not evaluated
        # for --dry-run: nothing is changed, so nothing to require a reference for.
        if not self._dry_run and not self._evaluate_preflight_policies("destroy_before"):
            return False

        if self._is_console_output():
            action = "Planning destroy for" if self._dry_run else "Destroying"
            click.echo(f"\n💣  {action} {len(stages_to_run)} stage(s)…")

        import concurrent.futures

        from strata.utils.shutdown_coordinator import ShutdownCoordinator

        deploy_name = (
            str(self._deployment_service.model.meta.name)  # type: ignore[union-attr]
            if self._deployment_service
            else "deployment"
        )
        coordinator = ShutdownCoordinator.activate(
            lock_backend=None,
            lock_handle=None,
            deployment_name=deploy_name,
        )

        def _run_stages() -> bool:
            lock_handle: Optional[LockHandle] = None
            lock_backend: Optional[BaseLockBackend] = None
            if self._should_lock():
                lock_backend = self._resolve_lock_backend(stages_to_run)
                lock_handle = self._acquire_lock(lock_backend)
                if lock_handle is None:
                    return False
                coordinator.update_lock(lock_backend, lock_handle)

            try:
                for stage in stages_to_run:
                    if self._is_console_output():
                        label = f"[{stage.name}]"
                        if stage.provisioner:
                            label += f" via {stage.provisioner}"
                        elif stage.topology:
                            label += f" topology:{stage.topology}"
                        prefix = "[DRY-RUN] " if self._dry_run else ""
                        click.echo(f"\n  ▶  {prefix}Stage: {stage.name}  {label}")

                    ok = self._execute_stage_destroy(stage)
                    if not ok:
                        if stage.on_failure == "continue":
                            if self._is_console_output():
                                click.echo(f"  ⚠️  Stage '{stage.name}' failed — on_failure=continue, proceeding.")
                            continue
                        self._errors.append(f"Stage '{stage.name}' failed (on_failure=stop).")
                        return False

                if self._is_console_output() and not self._dry_run:
                    click.echo("\n✅  All stages destroyed.")
                return True
            finally:
                coordinator.clear_lock()
                if lock_handle is not None and lock_backend is not None:
                    self._release_lock(lock_backend, lock_handle)

        try:
            if self._timeout > 0:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(_run_stages)
                    try:
                        return future.result(timeout=self._timeout)
                    except concurrent.futures.TimeoutError:
                        self._errors.append(
                            f"Destroy timed out after {self._timeout}s. Lock released and subprocesses terminated."
                        )
                        coordinator.shutdown(f"timeout after {self._timeout}s")
                        return False
            else:
                return _run_stages()
        finally:
            coordinator.deactivate()

    def _execute_stage_destroy(self, stage: DeploymentStageModel) -> bool:
        stage_started = _dt.now(_tz.utc).isoformat()
        deployer = self._create_deployer(stage)
        if deployer is None:
            self._record_stage_result(
                stage_name=str(stage.name),
                provisioner=stage.provisioner,
                topology=stage.topology,
                status="failed",
                started_at=stage_started,
                completed_at=_dt.now(_tz.utc).isoformat(),
                error="Failed to create deployer",
            )
            return False

        # Pre-flight validation
        for _label, validate_fn in (
            ("workspace", deployer.validate_workspace),
            ("environment", deployer.validate_environment),
        ):
            ok, msgs = validate_fn()
            self._messages.extend(msgs)
            if self._is_console_output():
                for msg in msgs:
                    click.echo(f"    {msg}")
            if not ok:
                self._errors.extend(msgs)
                self._record_stage_result(
                    stage_name=str(stage.name),
                    provisioner=stage.provisioner,
                    topology=stage.topology,
                    status="failed",
                    started_at=stage_started,
                    completed_at=_dt.now(_tz.utc).isoformat(),
                    error=f"Validation '{_label}' failed",
                )
                return False

        # Step sequence
        if self._dry_run:
            steps_to_run = [STEP_SETUP, STEP_PLAN_DESTROY]
        else:
            if not self._force:
                self._errors.append(
                    f"Stage '{stage.name}': --force is required to run destroy "
                    "(non-interactive execution needs -auto-approve). "
                    "Use --dry-run to preview what would be removed."
                )
                self._record_stage_result(
                    stage_name=str(stage.name),
                    provisioner=stage.provisioner,
                    topology=stage.topology,
                    status="failed",
                    started_at=stage_started,
                    completed_at=_dt.now(_tz.utc).isoformat(),
                    error="--force flag required",
                )
                return False
            steps_to_run = [STEP_SETUP, STEP_DESTROY]

        supported = deployer.get_supported_steps()

        # --- emit stage-start event (NDJSON) ---
        if self._is_ndjson_output():
            self.emit_ndjson(
                {
                    "event": "stage_start",
                    "stage": stage.name,
                    "ts": _dt.now(_tz.utc).isoformat(),
                }
            )

        for step_name in steps_to_run:
            if step_name not in supported:
                self._errors.append(
                    f"Stage '{stage.name}': step '{step_name}' is not supported "
                    f"by deployer '{deployer.get_deployer_name()}'."
                )
                self._record_stage_result(
                    stage_name=str(stage.name),
                    provisioner=stage.provisioner,
                    topology=stage.topology,
                    status="failed",
                    started_at=stage_started,
                    completed_at=_dt.now(_tz.utc).isoformat(),
                    steps=steps_to_run,
                    error=f"Step '{step_name}' not supported",
                )
                return False

            if self._is_console_output():
                prefix = "[DRY-RUN] " if self._dry_run else ""
                click.echo(f"    {prefix}{step_name}")

            # Build line callback for live NDJSON streaming.
            line_cb = None
            if self._is_ndjson_output():
                self.emit_ndjson(
                    {
                        "event": "step_start",
                        "step": step_name,
                        "stage": stage.name,
                        "ts": _dt.now(_tz.utc).isoformat(),
                    }
                )
                line_cb = self.make_ndjson_line_callback(step=step_name, stage=stage.name)

            step_fn = getattr(deployer, step_name)
            ok, msgs = step_fn(line_callback=line_cb)
            self._messages.extend(msgs)
            if self._is_console_output():
                for msg in msgs:
                    click.echo(f"      {msg}")
            if not ok:
                self._errors.extend(msgs)
                if self._is_ndjson_output():
                    self.emit_ndjson(
                        {
                            "event": "step_end",
                            "step": step_name,
                            "stage": stage.name,
                            "success": False,
                            "ts": _dt.now(_tz.utc).isoformat(),
                        }
                    )
                self._record_stage_result(
                    stage_name=str(stage.name),
                    provisioner=stage.provisioner,
                    topology=stage.topology,
                    status="failed",
                    started_at=stage_started,
                    completed_at=_dt.now(_tz.utc).isoformat(),
                    steps=steps_to_run,
                    error=f"Step '{step_name}' failed",
                )
                return False

            if self._is_ndjson_output():
                self.emit_ndjson(
                    {
                        "event": "step_end",
                        "step": step_name,
                        "stage": stage.name,
                        "success": True,
                        "ts": _dt.now(_tz.utc).isoformat(),
                    }
                )

        if self._is_ndjson_output():
            self.emit_ndjson(
                {
                    "event": "stage_end",
                    "stage": stage.name,
                    "success": True,
                    "ts": _dt.now(_tz.utc).isoformat(),
                }
            )

        self._record_stage_result(
            stage_name=str(stage.name),
            provisioner=stage.provisioner,
            topology=stage.topology,
            status="success",
            started_at=stage_started,
            completed_at=_dt.now(_tz.utc).isoformat(),
            steps=steps_to_run,
        )

        return True
