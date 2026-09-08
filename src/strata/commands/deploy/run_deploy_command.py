from datetime import datetime as _dt
from datetime import timezone as _tz
from typing import Callable, List, Optional

import click

from strata.commands.deploy.base_deploy_command import BaseDeployCommand
from strata.controllers.value_controller import ResolvedValues, ValueController
from strata.deployers.base_deployer import (
    STEP_APPLY,
    STEP_CHECK,
    STEP_DESTROY,
    STEP_PLAN,
    STEP_SETUP,
)
from strata.integrations.lock.base_lock_backend import (
    BaseLockBackend,
    LockHandle,
)
from strata.models.deployment_manifest_model import (
    ManifestOutputsReferenceModel,
)
from strata.models.deployment_model import DeploymentStageModel
from strata.utils.system import local_relative_part


def _parse_ai_risk(content: str) -> tuple:
    """Extract risk level string and parsed dict from AI response content."""
    import json as _json

    try:
        data = _json.loads(content)
        return str(data.get("risk", "low")).lower(), data
    except (ValueError, TypeError):
        lower = content.lower()
        for level in ("critical", "high", "medium", "low"):
            if level in lower:
                return level, {}
        return "low", {}


class RunDeployCommand(BaseDeployCommand):
    """Run the deploy pipeline for a deployment definition."""

    OPERATION = "deploy_run"

    def __init__(
        self,
        file: Optional[str] = None,
        work_path: Optional[str] = None,
        stage: Optional[str] = None,
        scope: Optional[str] = None,
        namespaces: Optional[List[str]] = None,
        force: bool = False,
        dry_run: bool = False,
        force_lock: bool = False,
        require_lock: bool = False,
        version_file: Optional[str] = None,
        ring_override: Optional[str] = None,
        wave: Optional[int] = None,
        promotion_override: Optional[str] = None,
        timeout: int = 0,
        ai: bool = False,
        strict_ai_review: Optional[str] = None,
        resume_id: Optional[str] = None,
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
        self._namespaces = list(namespaces) if namespaces else None
        self._force = force
        self._dry_run = dry_run
        self._force_lock = force_lock
        self._require_lock = require_lock
        self._version_file = version_file
        self._ring_override = ring_override
        self._wave = wave
        self._promotion_override = promotion_override
        self._timeout = timeout
        self._ai = ai
        self._strict_ai_review: Optional[str] = strict_ai_review.lower() if strict_ai_review else None
        self._resume_id: Optional[str] = resume_id
        self._hand_off_required: bool = False
        self._resolved_values: Optional[ResolvedValues] = None

    def has_hand_off_required(self) -> bool:
        """Return True when a gate work item was created and the deploy is paused."""
        return self._hand_off_required

    # -------------------------------------------------------------------------
    # Finalize override — writes deploy-log before standard finalization
    # -------------------------------------------------------------------------

    def _finalize(self, success: bool = False, show_footer: bool = True) -> bool:
        """Write deploy-log audit evidence, then delegate to parent finalize."""
        if self._deploy_started_at and not self._dry_run:
            self._write_deploy_log(success)
        return super()._finalize(success=success, show_footer=show_footer)

    # -------------------------------------------------------------------------
    # Public entry point
    # -------------------------------------------------------------------------

    def _execute(self) -> bool:
        try:
            self._record_deploy_start()

            # -v / --version-file: mutual exclusion + inject into spec.versions (Layer 3)
            if self._version_file:
                version_err = self._apply_explicit_version_file()
                if version_err:
                    self._errors.append(version_err)
                    if self._is_console_output():
                        click.echo(f"\n❌  {version_err}")
                    self._write_deployment_manifest(action="deploy", status="failed", dry_run=self._dry_run)
                    return False
            elif self._should_auto_resolve_version():
                # Layer 5: auto-resolve version from spec.promotion → lock → version file
                version_err = self._auto_resolve_version_from_promotion()
                if version_err:
                    self._errors.append(version_err)
                    if self._is_console_output():
                        click.echo(f"\n❌  {version_err}")
                    self._write_deployment_manifest(action="deploy", status="failed", dry_run=self._dry_run)
                    return False

            # Strict lock mode check (--require-lock or ring.require_lock: true)
            if self._deployment_service is not None:
                config_model = self._configuration_service.model if self._configuration_service else None
                lock_error = self._deployment_service.check_require_lock_mode(
                    self._work_path, config_model, flag=self._require_lock
                )
                if lock_error:
                    self._errors.append(lock_error)
                    if self._is_console_output():
                        click.echo(f"\n❌  {lock_error}")
                    self._write_deployment_manifest(action="deploy", status="failed", dry_run=self._dry_run)
                    return False

            if not self._resolve_values():
                if self._is_console_output():
                    click.echo("\n❌  Failed to resolve variables/secrets/features")
                self._write_deployment_manifest(action="deploy", status="failed", dry_run=self._dry_run)
                return False

            if self._dry_run and self._is_console_output():
                click.echo("\n[DRY-RUN] Validating and planning deploy — no provisioning will run")

            if not self._run_lifecycle_phase(
                "deploy_run_before",
                context={"file": str(self._file_path), "stage": self._stage, "dry_run": self._dry_run},
            ):
                if self._is_console_output():
                    click.echo("\n❌  Pre-deploy lifecycle hook failed")
                self._write_deployment_manifest(action="deploy", status="failed", dry_run=self._dry_run)
                return False

            if not self._execute_provisioning():
                if self._is_console_output():
                    click.echo("\n❌  Deploy provisioning failed")
                self._write_deployment_manifest(action="deploy", status="failed", dry_run=self._dry_run)
                return False
            if not self._run_lifecycle_phase(
                "deploy_configure",
                context={"file": str(self._file_path), "stage": self._stage, "dry_run": self._dry_run},
            ):
                if self._is_console_output():
                    click.echo("\n\u274c  Configure lifecycle hook failed")
                self._write_deployment_manifest(action="deploy", status="failed", dry_run=self._dry_run)
                return False

            if not self._run_lifecycle_phase(
                "deploy_run_after",
                context={"file": str(self._file_path), "stage": self._stage, "dry_run": self._dry_run},
            ):
                if self._is_console_output():
                    click.echo("\n❌  Post-deploy lifecycle hook failed")
                self._write_deployment_manifest(action="deploy", status="failed", dry_run=self._dry_run)
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
                action="deploy",
                status="success",
                dry_run=self._dry_run,
            )
            if manifest_path and self._is_console_output():
                click.echo(f"\n📋  Deployment manifest: {manifest_path}")

            # Write combined outputs artifact for registry consumption
            if not self._dry_run and self._stage_results:
                combined_results = [
                    {
                        "name": str(sr.name),
                        "status": sr.status,
                        "outputs": sr.outputs,
                        "sensitive_outputs": {},  # sensitive tracked via _write_outputs_artifact per stage
                    }
                    for sr in self._stage_results
                ]
                combined_path = self._write_combined_outputs_artifact(combined_results)
                if combined_path and self._is_console_output():
                    click.echo(f"📦  Combined outputs: {combined_path}")

            if self._ai and not self._dry_run:
                self._run_ai_deployment_summary()

            return True

        except Exception as exc:
            self._errors.append(f"Failed to execute deploy_run: {exc}")
            self.logger.exception("deploy_run failed")
            self._write_deployment_manifest(action="deploy", status="failed", dry_run=self._dry_run)
            return False

    # -------------------------------------------------------------------------
    # Version file helpers (Layer 3 — manual -v, and Layer 5 — auto-resolve)
    # -------------------------------------------------------------------------

    def _inject_version_file(self, version_file_path: str) -> None:
        """Append *version_file_path* to deployment spec.versions so the existing
        _apply_version_pins pipeline will load and apply it automatically."""
        if self._deployment_service is None or self._deployment_service.model is None:
            return
        from strata.models.deployment_model import DeploymentVersionRef

        model = self._deployment_service.model
        current = list(model.spec.versions or [])
        current.append(DeploymentVersionRef(file=version_file_path))
        # Use model_copy to avoid mutating a frozen model
        updated_spec = model.spec.model_copy(update={"versions": current})
        self._deployment_service.model = model.model_copy(update={"spec": updated_spec})

    def _apply_explicit_version_file(self) -> Optional[str]:
        """Validate and inject a user-supplied -v version file (Layer 3).

        Returns an error string if validation fails, None on success.
        """
        import hashlib
        import json as _json
        from pathlib import Path

        import yaml as _yaml

        vf = Path(str(self._version_file))
        if not vf.is_absolute():
            vf = Path(str(self._work_path)) / vf

        if not vf.exists():
            return f"Version file not found: {vf}"

        # Mutual exclusion: -v not allowed when deployment uses spec.promotion
        dep_model = self._deployment_service.model if self._deployment_service else None
        env_svc = (
            self._deployment_service._environment_service
            if self._deployment_service and hasattr(self._deployment_service, "_environment_service")
            else None
        )
        env_model = env_svc.model if env_svc else None
        if env_model and env_model.spec and env_model.spec.promotion:
            return (
                f"Deployment is managed by promotion '{env_model.spec.promotion.strategy}'. "
                "Use 'strata promote' to change its version, or remove spec.promotion to use manual mode. "
                "Pass --force to override."
                if not self._force
                else None
            )

        # Hash check: if spec.hash is present, verify it
        try:
            raw = _yaml.safe_load(vf.read_text(encoding="utf-8"))
        except Exception as exc:
            return f"Could not read version file '{vf}': {exc}"

        if isinstance(raw, dict) and raw.get("kind") == "version":
            spec_hash = (raw.get("spec") or {}).get("hash")
            if spec_hash:
                pins = (raw.get("spec") or {}).get("pins", {})
                canonical = _json.dumps(pins, sort_keys=True, separators=(",", ":"))
                computed = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
                if computed != spec_hash:
                    return (
                        f"Version file '{vf.name}' hash mismatch — file may have been modified. "
                        f"Expected: {spec_hash[:12]}…  Got: {computed[:12]}…  "
                        "Run 'strata versions lock' to recompute, or investigate the change."
                    )
            else:
                # No hash — warn but proceed
                import warnings

                warnings.warn(
                    f"Version file '{vf.name}' has no spec.hash — integrity cannot be verified. "
                    "Run 'strata versions lock' to add a tamper-evident hash.",
                    stacklevel=2,
                )

        self._inject_version_file(str(vf))
        return None

    def _should_auto_resolve_version(self) -> bool:
        """True when the deployment's environment has spec.promotion and no -v was given."""
        if self._deployment_service is None:
            return False
        dep_model = self._deployment_service.model
        if dep_model is None:
            return False
        env_svc = getattr(self._deployment_service, "_environment_service", None)
        if env_svc is None or env_svc.model is None:
            return False
        return env_svc.model.spec.promotion is not None

    def _auto_resolve_version_from_promotion(self) -> Optional[str]:
        """Layer 5: resolve version from spec.promotion → config → lock → version file.

        Honours ``--ring``, ``--wave``, and ``--promotion`` overrides.
        When ``--wave N`` is given, the wave lock is layered on top of the ring lock:
        the wave lock is injected first, then the ring lock, so that the wave pins take
        precedence (last-writer-wins in VersionService.resolve_pins).

        Returns an error string on failure, None on success.
        """
        from pathlib import Path

        import yaml as _yaml

        env_svc = getattr(self._deployment_service, "_environment_service", None)
        if env_svc is None or env_svc.model is None:
            return None  # no environment — skip

        promotion = env_svc.model.spec.promotion
        if not promotion and not (self._ring_override and self._promotion_override):
            return None

        ring_name: str = self._ring_override or (promotion.ring if promotion else "")
        strategy_name: str = self._promotion_override or (promotion.strategy if promotion else "")

        if not ring_name or not strategy_name:
            return None

        # Load config to get versions_path
        config_svc = self._configuration_service
        if config_svc is None or config_svc.model is None:
            return (
                "Environment has spec.promotion but no configuration is loaded. Cannot resolve version automatically."
            )

        promotions = config_svc.model.spec.promotions if config_svc.model.spec else None
        if not promotions or not promotions.strategies:
            return (
                f"No promotions configured in configuration.spec.promotions. "
                f"Cannot auto-resolve version for ring '{ring_name}'."
            )

        strategy = next((s for s in (promotions.strategies or []) if s.name == strategy_name), None)
        if strategy is None:
            return f"Promotion strategy '{strategy_name}' not found in configuration. Cannot auto-resolve version."

        if not strategy.versions_path:
            return f"Promotion '{strategy_name}' has no versions_path configured. Cannot auto-resolve version."

        # Resolve versions_path
        vp_raw = strategy.versions_path
        if vp_raw.startswith("@"):
            vp_raw = local_relative_part(vp_raw)
        vp = Path(str(self._work_path)) / vp_raw

        # Find ring lock
        lock_path = vp / f"{ring_name}.lock.yaml"
        if not lock_path.exists():
            return (
                f"No lock file at '{lock_path.relative_to(self._work_path)}'. "
                f"Run 'strata promote {ring_name} <file> --promotion {strategy_name}' first."
            )

        # Load lock → follow pointer → get version file path
        try:
            lock_raw = _yaml.safe_load(lock_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return f"Could not read lock file '{lock_path}': {exc}"

        source = (lock_raw.get("spec") or {}).get("source")
        if not source:
            # Old-style pins lock — pins are already in the lock file; inject it
            self._inject_version_file(str(lock_path))
            return None

        version_file_path = (lock_path.parent / source).resolve()
        if not version_file_path.exists():
            return (
                f"Lock file points to '{source}' which does not exist "
                f"(resolved: '{version_file_path}'). "
                "Re-run 'strata promote' to fix the lock."
            )

        # ── wave lock layering ──────────────────────────────────────────────
        # When --wave N is given, look for {ring}.wave.N.lock.yaml and layer it
        # on top: inject ring version file first, then wave lock (wave wins).
        if self._wave is not None:
            wave_lock_path = vp / f"{ring_name}.wave.{self._wave}.lock.yaml"
            if not wave_lock_path.exists():
                return (
                    f"Wave lock '{wave_lock_path.name}' not found in '{vp}'. "
                    f"Run 'strata promote {ring_name} <file> --promotion {strategy_name} "
                    f"--wave {self._wave}' first."
                )
            # Load wave lock and follow its pointer to the wave version file
            try:
                wave_raw = _yaml.safe_load(wave_lock_path.read_text(encoding="utf-8"))
            except Exception as exc:
                return f"Could not read wave lock file '{wave_lock_path}': {exc}"

            wave_source = (wave_raw.get("spec") or {}).get("source")
            if wave_source:
                wave_version_file = (wave_lock_path.parent / wave_source).resolve()
                if not wave_version_file.exists():
                    return (
                        f"Wave lock points to '{wave_source}' which does not exist "
                        f"(resolved: '{wave_version_file}'). "
                        "Re-run 'strata promote' to fix the wave lock."
                    )
                # Inject ring version file first (lower priority), then wave (wins)
                self._inject_version_file(str(version_file_path))
                self._inject_version_file(str(wave_version_file))
                return None
            else:
                # Old-style wave lock with inline pins — inject ring file + wave lock
                self._inject_version_file(str(version_file_path))
                self._inject_version_file(str(wave_lock_path))
                return None

        self._inject_version_file(str(version_file_path))
        return None

    # -------------------------------------------------------------------------
    # Internal pipeline steps
    # -------------------------------------------------------------------------

    def _run_cost_diff_for_stage(self, stage: "DeploymentStageModel", plan_json_path) -> None:
        """Run infracost diff after plan in dry-run mode. Non-fatal — cost errors never block deploy.

        Displays cost impact (before/after/delta) in console output.
        Requires a cost estimator (e.g. Infracost) to be declared in
        ``spec.integrations`` — an installed binary alone is not enough (see
        ``CostController.is_auto_diff_enabled``). Skips silently if not
        declared, not installed, or otherwise unavailable.
        """
        if self._deployment_service is None:
            return

        try:
            from strata.controllers.cost_controller import CostController

            controller = CostController(work_path=self._work_path)

            if not controller.is_auto_diff_enabled():
                return  # No cost estimator declared in spec.integrations — skip silently

            if not controller.is_available():
                return  # Declared but not installed/available — skip silently

            success, result = controller.diff(
                deployment_service=self._deployment_service,
                build_path=self._build_path,
                plan_file=str(plan_json_path),
                solution_controller=self._solution_controller,
                provisioner_filter=stage.provisioner,
            )

            if not success:
                # Non-fatal — log at debug level, don't surface to user
                self.logger.debug("cost_diff_skipped", stage=stage.name, reason=result.get("error", "unknown"))
                return

            if self._is_console_output():
                diff = result.get("diff", {})
                past_total = result.get("pastTotalMonthlyCost", diff.get("pastTotalMonthlyCost", "0.00"))
                total = result.get("totalMonthlyCost", diff.get("totalMonthlyCost", "0.00"))
                try:
                    delta = float(total or 0) - float(past_total or 0)
                    delta_sign = "+" if delta >= 0 else ""
                    delta_str = f"{delta_sign}{delta:.2f}"
                except (ValueError, TypeError):
                    delta_str = "n/a"
                click.echo(f"    💰 Cost impact:  {past_total} → {total}/month  (delta: {delta_str})")

        except Exception as exc:
            # Cost diff is always non-fatal
            self.logger.debug("cost_diff_error", stage=stage.name, error=str(exc))

    # -------------------------------------------------------------------------
    # AI advisory helpers
    # -------------------------------------------------------------------------

    _RISK_LEVELS = {"low": 0, "medium": 1, "high": 2, "critical": 3}

    def _check_ai_plan_gate(self, stage: "DeploymentStageModel", plan_msgs: list) -> bool:
        """Run AI plan analysis and optionally block apply.

        Returns True if deployment should proceed, False to block.

        Gating rules:
        - ``--strict-ai-review [THRESHOLD]``: always fail (non-interactive) when
          risk ≥ threshold. Suitable for CI/CD pipelines.
        - ``--ai`` without ``--strict-ai-review``: if risk is high/critical and
          ``--force`` is not set and stdin is a TTY, prompt the operator. Falls
          back to blocking when running non-interactively (CI mode).
        """
        import sys

        import click as _click

        from strata.integrations.ai import find_ai_integration

        integration = find_ai_integration(self._configuration_service)
        if integration is None or not integration.ensure_available()[0]:
            if self._strict_ai_review:
                self._errors.append("--strict-ai-review set but no reachable ai_agent integration configured")
                return False
            return True  # advisory-only: missing provider is non-fatal

        deployment_name = (
            str(self._deployment_service.model.meta.name)  # type: ignore[union-attr]
            if self._deployment_service and self._deployment_service.model
            else "unknown"
        )
        context = {
            "deployment": deployment_name,
            "stage": str(stage.name),
            "work_path": str(self._work_path),
        }
        plan_data = {"stages": [{"stage": str(stage.name), "messages": plan_msgs}]}

        if self._is_console_output():
            _click.echo(f"\n  🤖  AI plan review ({integration.integration_name}) …")

        try:
            response = integration.analyse_plan(plan_data, context)
        except Exception as exc:
            self._messages.append(f"AI plan analysis failed: {exc}")
            if self._strict_ai_review:
                self._errors.append(f"AI plan analysis failed: {exc}")
                return False
            return True  # advisory: don't block on analysis error

        risk_str, parsed = _parse_ai_risk(response.content)
        risk_level = self._RISK_LEVELS.get(risk_str, 0)
        risk_icon = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🟠", "CRITICAL": "🔴"}.get(risk_str.upper(), "⚪")

        if self._is_console_output():
            _click.echo(f"\n  {risk_icon}  AI Risk: {risk_str.upper()}  —  {parsed.get('summary', '')}")
            if parsed.get("concerns"):
                _click.echo("  Concerns:")
                for c in parsed["concerns"]:
                    _click.echo(f"    • {c}")
            if parsed.get("recommendations"):
                _click.echo("  Recommendations:")
                for r in parsed["recommendations"]:
                    _click.echo(f"    → {r}")

        # Determine gate threshold
        threshold_str = self._strict_ai_review or "high"
        threshold = self._RISK_LEVELS.get(threshold_str, 2)

        if risk_level < threshold:
            return True  # below threshold — proceed

        # Risk meets or exceeds threshold
        if self._strict_ai_review:
            self._errors.append(
                f"AI plan review blocked deployment: risk={risk_str.upper()} "
                f"≥ threshold={threshold_str.upper()} (--strict-ai-review)"
            )
            if self._is_console_output():
                _click.echo(
                    f"\n  ❌  Deployment blocked — AI risk {risk_str.upper()} ≥ "
                    f"{threshold_str.upper()} (--strict-ai-review)"
                )
            return False

        # Interactive mode (--ai without --strict): prompt if TTY, block if CI
        if self._force:
            if self._is_console_output():
                _click.echo(f"  ⚠️  AI risk {risk_str.upper()} — proceeding (--force)")
            return True

        is_tty = sys.stdin.isatty()
        if not is_tty:
            self._errors.append(
                f"AI plan review: risk={risk_str.upper()} requires confirmation but stdin is not a TTY. "
                "Pass --force to override in CI/CD, or use --strict-ai-review to enforce explicitly."
            )
            if self._is_console_output():
                _click.echo(f"\n  ❌  AI risk {risk_str.upper()} — blocking (non-interactive, use --force to override)")
            return False

        # TTY prompt
        if self._is_console_output():
            _click.echo("")
        confirmed = _click.confirm(
            f"  AI risk is {risk_str.upper()} for stage '{stage.name}'. Proceed with apply?",
            default=False,
        )
        if not confirmed:
            self._errors.append(f"Deployment cancelled by operator after AI risk review ({risk_str.upper()})")
        return confirmed

    def _run_ai_failure_diagnosis(self, error_output: str, step: str, stage_name: str) -> None:
        """Call AI failure diagnosis after a deployer step fails."""
        import click as _click

        from strata.integrations.ai import find_ai_integration

        integration = find_ai_integration(self._configuration_service)
        if integration is None or not integration.ensure_available()[0]:
            return

        deployment_name = (
            str(self._deployment_service.model.meta.name)  # type: ignore[union-attr]
            if self._deployment_service and self._deployment_service.model
            else "unknown"
        )
        context = {
            "deployment": deployment_name,
            "stage": stage_name,
            "provisioner": "terraform",
            "work_path": str(self._work_path),
        }

        if self._is_console_output():
            _click.echo(f"\n  🤖  AI failure diagnosis ({integration.integration_name}) …")

        try:
            response = integration.diagnose_failure(error_output, step, context)
        except Exception as exc:
            self._messages.append(f"AI failure diagnosis failed: {exc}")
            return

        if "ai_analysis" not in self._output_data:
            self._output_data["ai_analysis"] = {}
        self._output_data["ai_analysis"]["failure_diagnosis"] = {
            "provider": response.provider,
            "model": response.model,
            "content": response.content,
        }

        if self._is_console_output():
            self._print_ai_diagnosis(response.content)

    def _print_ai_diagnosis(self, content: str) -> None:
        import json as _json

        import click as _click

        try:
            parsed = _json.loads(content)
            category = parsed.get("category", "unknown").upper()
            _click.echo(f"\n  🔍  Root cause [{category}]: {parsed.get('root_cause', '')}")
            if parsed.get("remediation"):
                _click.echo("  Remediation:")
                for i, step in enumerate(parsed["remediation"], 1):
                    _click.echo(f"    {i}. {step}")
        except (_json.JSONDecodeError, TypeError):
            _click.echo(content)
        _click.echo("")

    def _run_ai_deployment_summary(self) -> None:
        """Generate an AI deployment summary after successful provisioning."""
        import click as _click

        from strata.integrations.ai import find_ai_integration

        integration = find_ai_integration(self._configuration_service)
        if integration is None or not integration.ensure_available()[0]:
            return

        if self._is_console_output():
            _click.echo(f"\n  🤖  AI deployment summary ({integration.integration_name}) …")

        manifest = dict(self._output_data)
        try:
            response = integration.summarise_deployment(manifest, history=[])
        except Exception as exc:
            self._messages.append(f"AI deployment summary failed: {exc}")
            return

        if "ai_analysis" not in self._output_data:
            self._output_data["ai_analysis"] = {}
        self._output_data["ai_analysis"]["deployment_summary"] = {
            "provider": response.provider,
            "model": response.model,
            "content": response.content,
        }

        if self._is_console_output():
            self._print_ai_summary(response.content)

    def _print_ai_summary(self, content: str) -> None:
        import json as _json

        import click as _click

        try:
            parsed = _json.loads(content)
            outcome = parsed.get("outcome", "?").upper()
            outcome_icon = {"SUCCESS": "✅", "PARTIAL": "⚠️", "FAILURE": "❌"}.get(outcome, "📋")
            _click.echo(f"\n  {outcome_icon}  {parsed.get('headline', '')}")
            if parsed.get("highlights"):
                _click.echo("  Highlights:")
                for h in parsed["highlights"]:
                    _click.echo(f"    • {h}")
            if parsed.get("next_steps"):
                _click.echo("  Next steps:")
                for s in parsed["next_steps"]:
                    _click.echo(f"    → {s}")
        except (_json.JSONDecodeError, TypeError):
            _click.echo(content)
        _click.echo("")

    def _write_deploy_log(self, success: bool) -> None:
        """Assemble and write deploy-log via ``AuditController``, forwarding ``deployment.completed``.

        Thin wrapper around the shared ``BaseDeployCommand._write_deploy_log_and_forward()``
        (ADR-0066 gap B) — kept as its own named method (rather than inlining the call at
        the one call site in ``_finalize``) so existing tests that patch/call
        ``_write_deploy_log`` directly keep working unchanged.
        """
        self._write_deploy_log_and_forward("deployment.completed", success)

    def _evaluate_deployment_gates(self, stages_to_run: List[DeploymentStageModel]):
        """Evaluate spec.gates from the deployment model — pre-provisioning.

        Handles gates with `when: always` (approval) and `type: scheduled`.
        Condition-based gates (cost_review, security_review) run post-plan via
        _evaluate_condition_gates_post_plan(). Verify gates run post-apply.
        `mode: declare` gates never create a work item — they're logged via the
        gate evaluator itself (see gate_controller.evaluate_and_create).
        Returns a WorkItem if an enforcing gate is triggered, else None.
        """
        try:
            deployment_model = self._deployment_service.model if self._deployment_service else None  # type: ignore[union-attr]
            gates = (deployment_model.spec.gates or []) if (deployment_model and deployment_model.spec) else []
            if not gates:
                return None
            # Only pre-provisioning gate types: approval, scheduled, cab, incident
            pre_gates = [g for g in gates if g.type in ("approval", "scheduled", "cab", "incident")]
            if not pre_gates:
                return None
        except Exception:
            return None

        from strata.controllers.gate_controller import _SCHEDULED_BLOCK_SENTINEL, GateContext, WorkItemGateController
        from strata.controllers.workitem_controller import WorkItemController

        context = GateContext()
        controller = WorkItemGateController(WorkItemController.from_config(self._work_path))
        deployment_path = str(self._file_path) if self._file_path else ""
        commit = self._get_current_commit()
        scope_stages = [s.name for s in stages_to_run]

        result = controller.evaluate_and_create(pre_gates, deployment_path, commit, context, scope_stages=scope_stages)
        # Scheduled block sentinel means "blocked by window, no work item needed"
        if result is _SCHEDULED_BLOCK_SENTINEL:
            from strata.models.gate_model import GateWhenConditionsModel

            window = next(
                (
                    g.when.time_utc
                    for g in gates
                    if g.type == "scheduled" and isinstance(g.when, GateWhenConditionsModel) and g.when.time_utc
                ),
                "configured window",
            )
            self._errors.append(
                f"Deployment blocked by scheduled gate: outside maintenance window ({window}). "
                "Retry when the window opens."
            )
            if self._is_console_output():
                click.echo(f"\n⏰  Scheduled gate: outside window ({window}). Retry when the window opens.")
            # Use exit code 5 so CI distinguishes "retry later" from a system error (exit 1)
            self._hand_off_required = True
            return result
        # A real work item was created — forward to SIEM
        if result is not None:
            self._forward_workitem_event("workitem.created", result)
        return result

    def _evaluate_condition_gates_post_plan(
        self,
        stage,
        deployer,
        stage_started: str,
        steps_to_run: list,
    ) -> bool:
        """Evaluate condition-based gates (cost_review, security_review) after plan.

        Called after STEP_PLAN, before STEP_APPLY. Populates GateContext with
        real cost delta and CVE counts so gate conditions are evaluated accurately.
        Returns True if provisioning should continue, False if a gate triggered.
        """
        try:
            deployment_model = self._deployment_service.model if self._deployment_service else None  # type: ignore[union-attr]
            gates = (deployment_model.spec.gates or []) if (deployment_model and deployment_model.spec) else []
            condition_gates = [g for g in gates if g.type in ("cost_review", "security_review")]
            if not condition_gates:
                return True
        except Exception:
            return True

        from strata.controllers.gate_context_builder import GateContextBuilder
        from strata.controllers.gate_controller import WorkItemGateController
        from strata.controllers.workitem_controller import WorkItemController

        builder = GateContextBuilder(
            build_path=self._build_path,
            deployment_service=self._deployment_service,
        )
        ai_analysis = self._output_data.get("ai_analysis") if hasattr(self, "_output_data") else None
        context = builder.build(stage=stage, deployer=deployer, ai_analysis=ai_analysis)

        controller = WorkItemGateController(WorkItemController.from_config(self._work_path))
        deployment_path = str(self._file_path) if self._file_path else ""
        commit = self._get_current_commit()

        work_item = controller.evaluate_and_create(
            condition_gates, deployment_path, commit, context, scope_stages=[str(stage.name)]
        )
        if work_item is None:
            return True

        self._forward_workitem_event("workitem.created", work_item)
        self._hand_off_required = True
        if self._is_console_output():
            click.echo(f"\n⏸️  Deployment paused — {work_item.type} gate triggered:")
            click.echo(f"   ID:   {work_item.id}")
            if work_item.context.get("cost_delta_monthly") is not None:
                delta = work_item.context["cost_delta_monthly"]
                click.echo(f"   Cost delta: ${delta:+.2f}/month")
            if work_item.context.get("cve_critical_count"):
                click.echo(f"   Critical CVEs: {work_item.context['cve_critical_count']}")
            if work_item.expires_at:
                click.echo(f"   Expires: {work_item.expires_at[:19].replace('T', ' ')} UTC")
            click.echo(f"\n   Resolve:  strata workitem approve {work_item.id!r}")
            click.echo(f"   Resume:   strata deploy run -f {self._file_path} --resume {work_item.id!r}")
        self._record_stage_result(
            stage_name=str(stage.name),
            provisioner=stage.provisioner,
            topology=stage.topology,
            status="failed",
            started_at=stage_started,
            completed_at=_dt.now(_tz.utc).isoformat(),
            steps=steps_to_run,
            error=f"Gate {work_item.type!r} triggered — awaiting resolution",
        )
        return False

    def _evaluate_verify_gate_post_apply(
        self,
        stage,
        stage_started: str,
        steps_to_run: list,
    ) -> bool:
        """Evaluate verify gates after apply completes.

        Pauses the pipeline pending human verification that the deployment
        is functioning correctly. Returns True to continue, False to pause.
        """
        try:
            deployment_model = self._deployment_service.model if self._deployment_service else None  # type: ignore[union-attr]
            gates = (deployment_model.spec.gates or []) if (deployment_model and deployment_model.spec) else []
            verify_gates = [g for g in gates if g.type == "verify"]
            if not verify_gates:
                return True
        except Exception:
            return True

        from strata.controllers.gate_controller import GateContext, WorkItemGateController
        from strata.controllers.workitem_controller import WorkItemController

        context = GateContext()
        # Enrich with deploy summary
        context.extra["stage"] = str(stage.name)
        context.extra["action"] = "verify post-deploy"

        controller = WorkItemGateController(WorkItemController.from_config(self._work_path))
        deployment_path = str(self._file_path) if self._file_path else ""
        commit = self._get_current_commit()

        work_item = controller.evaluate_and_create(
            verify_gates, deployment_path, commit, context, gate_type_filter="verify", scope_stages=[str(stage.name)]
        )
        if work_item is None:
            return True

        self._forward_workitem_event("workitem.created", work_item)
        self._hand_off_required = True
        if self._is_console_output():
            click.echo("\n⏸️  Verify gate: deployment applied — awaiting manual verification:")
            click.echo(f"   ID:   {work_item.id}")
            click.echo(f"   Stage: {stage.name}")
            if work_item.context.get("description"):
                click.echo(f"   Instructions: {work_item.context['description']}")
            if work_item.expires_at:
                click.echo(f"   Expires: {work_item.expires_at[:19].replace('T', ' ')} UTC")
            click.echo(f"\n   Complete:  strata workitem complete {work_item.id!r}")
            click.echo(f"   Resume:    strata deploy run -f {self._file_path} --resume {work_item.id!r}")
        self._record_stage_result(
            stage_name=str(stage.name),
            provisioner=stage.provisioner,
            topology=stage.topology,
            status="pending_verification",
            started_at=stage_started,
            completed_at=_dt.now(_tz.utc).isoformat(),
            steps=steps_to_run,
            error="Verify gate triggered — awaiting manual verification",
        )
        return False

    def _verify_gate_resume(self) -> bool:
        """Verify the --resume work item is approved for this commit.

        Returns True if the deployment can proceed, False otherwise.
        Populates self._errors on failure.
        """
        from strata.controllers.gate_controller import WorkItemGateController
        from strata.controllers.workitem_controller import WorkItemController

        controller = WorkItemGateController(WorkItemController.from_config(self._work_path))
        commit = self._get_current_commit()

        try:
            item = controller.verify_resume(self._resume_id or "", commit)
            self._forward_workitem_event("workitem.resumed", item)
            if self._is_console_output():
                click.echo(f"\n✅  Gate cleared: {item.id}  ({item.status} by {item.resolved_by or 'system'})")
            return True
        except Exception as exc:
            self._errors.append(str(exc))
            if self._is_console_output():
                click.echo(f"\n❌  Gate resume failed: {exc}")
            return False

    def _forward_workitem_event(self, event_name: str, item) -> None:
        """Forward a work-item lifecycle event to configured audit sinks.

        Best-effort — never raises. Routes through ``AuditController.forward()``
        (ADR-0066) — the same single sink-resolution path ``deploy_audit`` and
        ``resend`` use, rather than a separate duplicated resolver.
        """
        try:
            audit_cfg = None
            if self._configuration_service:
                audit_cfg = getattr(getattr(self._configuration_service.model, "spec", None), "audit", None)
            if not audit_cfg or not audit_cfg.sinks:
                return
            from strata.controllers.audit_controller import AuditController

            data = {**item.to_dict(), "event": event_name}
            AuditController(work_path=self._work_path).forward(event_name, data, audit_config=audit_cfg)
        except Exception as exc:
            self.logger.debug("workitem_siem_forward_error", event_name=event_name, error=str(exc))

    # No _load_related_services() override here — `deploy run` needs the full
    # workspace + environment load (topology, providers, resources, secrets),
    # so it inherits BaseDeployCommand._load_related_services() unchanged. A
    # stale no-op override (`return True`, loading nothing) used to live here
    # and silently left `DeploymentService._environment_service`/`_workspace_service`
    # unset, causing `ServiceNotValidatedError: Service 'EnvironmentService' must be
    # validated before use` the moment `_resolve_values()` ran. Regression window:
    # introduced when `_load_related_services()` became an overridable hook (v1.7.0,
    # commit 0720edb3) — before that, `base_deploy_command.py` called
    # `deployment_service.load_deploy_services()` directly and unconditionally, so
    # this class's pre-existing no-op override was never actually invoked.

    def _resolve_values(self, strict: bool = False) -> bool:
        """Resolve variables, secrets, and feature flags from the environment.

        Populates ``self._resolved_values`` which is later passed to the
        deployer so it can inject TF_VAR_* env vars around each terraform step.

        Non-strict mode: resolution warnings (a declared value genuinely absent,
        with no default) are logged but do not abort the deploy. A store being
        unreachable/unauthenticated is a DIFFERENT, always-fatal category — see
        ``ValueController.resolve_values()`` — since continuing past it risks
        silently generating/overwriting secrets or deploying with blank values.
        """
        controller = ValueController()
        ok, resolved, errors = controller.resolve_values(
            self._deployment_service,  # type: ignore[arg-type]
            strict=False,  # type: ignore[arg-type]
        )

        self._resolved_values = resolved

        if resolved.store_unavailable_errors:
            for err in resolved.store_unavailable_errors:
                self.logger.error("Value resolution aborted — store unavailable: %s", err)
            if self._is_console_output():
                click.echo(
                    f"\n\u274c  {len(resolved.store_unavailable_errors)} store(s) unreachable/unauthenticated — "
                    "aborting deploy rather than risk generating or overwriting secrets:"
                )
                for err in resolved.store_unavailable_errors:
                    click.echo(f"     • {err}")
            self._errors.extend(resolved.store_unavailable_errors)
            return False

        if errors:
            for err in errors:
                self.logger.warning("Value resolution warning: %s", err)
            if self._is_console_output():
                click.echo(f"  ⚠️  {len(errors)} value(s) could not be resolved (see logs for details).")

        if self._is_console_output() and not resolved.is_empty():
            click.echo(
                f"  ✓  Resolved {len(resolved.variables)} variable(s), "
                f"{len(resolved.secrets)} secret(s), "
                f"{len(resolved.features)} feature(s)."
            )
            seeded = [
                f"{k}={v[len('default: ') :]}"
                for k, v in {**resolved.variable_notes, **resolved.feature_notes}.items()
                if v.startswith("default: ")
            ]
            generated = [k for k, v in resolved.secret_notes.items() if v == "generated"]
            advisories = [
                f"{k} ({v.split(':', 1)[1]})"
                for k, v in resolved.secret_notes.items()
                if v.startswith("rotation_advisory:")
            ]
            rotated = [
                f"{k} ({v.split(':', 1)[1]})" for k, v in resolved.secret_notes.items() if v.startswith("rotated:")
            ]
            failed = [
                f"{k} ({v.split(':', 1)[1]})"
                for k, v in resolved.secret_notes.items()
                if v.startswith("rotation_failed:")
            ]
            if seeded:
                click.echo(f"  \u21b3  Seeded on first run: {', '.join(seeded)}")
            if generated:
                click.echo(f"  \u21b3  Generated on first run: {', '.join(generated)}")
            if advisories:
                click.echo(f"  \u21b3  Rotation advisory: {', '.join(advisories)}")
            if rotated:
                click.echo(f"  \u21b3  Rotated: {', '.join(rotated)}")
            if failed:
                click.echo(f"  ⚠️  Rotation failed: {', '.join(failed)}")

        # Always log STRATA_CONTEXT/STRATA_SENSITIVE at DEBUG; show under --verbose
        self.logger.debug("strata_context_resolved", **resolved.debug_summary())
        if self._is_verbose() and self._is_console_output() and not resolved.is_empty():
            summary = resolved.debug_summary()
            ctx = summary["strata_context"]
            sens = summary["strata_sensitive"]
            click.echo("  STRATA_CONTEXT:")
            for section, values in ctx.items():
                if values:
                    for k, v in values.items():
                        click.echo(f"    [{section}] {k} = {v}")
            click.echo("  STRATA_SENSITIVE (keys only):")
            for section, masked in sens.items():
                if masked:
                    for k in masked:
                        click.echo(f"    [{section}] {k} = ***")

        # ok is always True in non-strict mode — keep going even with warnings
        return True

    def _execute_provisioning(self) -> bool:
        """Iterate deployment stages and invoke the appropriate provisioner per stage."""
        if self._deployment_service is None:
            self._errors.append("Deployment service not loaded")
            return False
        spec = self._deployment_service.model.spec  # type: ignore[union-attr]
        all_stages: List[DeploymentStageModel] = spec.stages or []

        if not all_stages:
            if self._is_console_output():
                click.echo("⚠️  No deployment stages defined — nothing to deploy.")
            return True

        # Filter to a single stage when --stage is supplied
        stages_to_run = [s for s in all_stages if s.name == self._stage] if self._stage else all_stages

        if self._stage and not stages_to_run:
            self._errors.append(
                f"Stage '{self._stage}' not found in deployment definition. Available: {[s.name for s in all_stages]}"
            )
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

        # Validate --namespace values (if supplied) against the workspace's declared
        # namespaces before running anything. Only the helm provisioner honors this
        # filter (see BaseDeployer.namespace_filter), but the CLI-level names must
        # still exist in workspace.spec.namespaces regardless of which stages run.
        if self._namespaces:
            known_namespaces = set((self._deployment_service.get_namespace_services() or {}).keys())
            unknown = [ns for ns in self._namespaces if ns not in known_namespaces]
            if unknown:
                self._errors.append(
                    f"Namespace(s) not found in workspace: {unknown}. "
                    f"Available namespaces: {sorted(known_namespaces) or ['(none defined)']}"
                )
                return False

        # ADR-0074 Phase 2 — 'deploy_before' policies (e.g. change_reference_required)
        # evaluate once per run, before any stage executes — fails fast, same as the
        # pre-flight provisioner check below, instead of only being discovered after
        # an earlier stage has already made real infrastructure changes. Not evaluated
        # for --dry-run: nothing is changed, so nothing to require a reference for.
        if not self._dry_run and not self._evaluate_preflight_policies("deploy_before"):
            return False

        if self._is_console_output():
            click.echo(f"\n🚀  Deploying {len(stages_to_run)} stage(s)…")

        # Pre-flight: validate every stage's provisioner environment (tool binary
        # + auth) BEFORE acquiring the deployment lock or running any stage. This
        # fails fast so a later stage's missing tool can't be discovered only
        # after an earlier stage has already made real infrastructure changes.
        # Stages with on_failure=continue are still checked (for visibility) but
        # a failure there is downgraded to a warning, consistent with how it's
        # already tolerated once execution actually reaches that stage.
        preflight_errors = self._preflight_check_provisioners(stages_to_run)
        if preflight_errors:
            self._errors.extend(preflight_errors)
            if self._is_console_output():
                click.echo(
                    f"\n❌  {len(preflight_errors)} stage(s) failed pre-flight validation "
                    "— aborting before any provisioning:"
                )
                for err in preflight_errors:
                    click.echo(f"     • {err}")
            return False

        # Check approval gates before any provisioning
        if not self._dry_run:
            # Evaluate deployment spec.gates (ADR-0057/ADR-0059)
            if self._resume_id:
                if not self._verify_gate_resume():
                    return False
            else:
                from strata.controllers.gate_controller import _SCHEDULED_BLOCK_SENTINEL

                work_item = self._evaluate_deployment_gates(stages_to_run)
                if work_item is not None:
                    if work_item is _SCHEDULED_BLOCK_SENTINEL:
                        return False  # error already recorded in _evaluate_deployment_gates
                    self._hand_off_required = True
                    if self._is_console_output():
                        click.echo("\n⏸️  Deployment paused — gate work item created:")
                        click.echo(f"   ID:   {work_item.id}")
                        click.echo(f"   Type: {work_item.type}")
                        if work_item.expires_at:
                            click.echo(f"   Expires: {work_item.expires_at[:19].replace('T', ' ')} UTC")
                        click.echo(f"\n   Resolve:  strata workitem approve {work_item.id!r}")
                        click.echo(f"   Resume:   strata deploy run -f {self._file_path} --resume {work_item.id!r}")
                    return False

        import concurrent.futures

        from strata.utils.shutdown_coordinator import ShutdownCoordinator

        deploy_name = (
            str(self._deployment_service.model.meta.name)  # type: ignore[union-attr]
            if self._deployment_service
            else "deployment"
        )

        # Activate coordinator on the main thread (signal handlers require this)
        coordinator = ShutdownCoordinator.activate(
            lock_backend=None,  # lock acquired inside _run_stages; updated via update_lock()
            lock_handle=None,
            deployment_name=deploy_name,
        )

        def _run_stages() -> bool:
            """Lock acquisition + stage loop — may run in a worker thread."""
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

                    ok = self._execute_stage_provisioning(stage)
                    if not ok:
                        if stage.on_failure == "continue":
                            if self._is_console_output():
                                click.echo(f"  ⚠️  Stage '{stage.name}' failed — on_failure=continue, proceeding.")
                            continue
                        self._errors.append(f"Stage '{stage.name}' failed (on_failure=stop).")
                        return False

                if self._is_console_output() and not self._dry_run:
                    click.echo("\n✅  All stages completed.")
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
                            f"Deploy timed out after {self._timeout}s. Lock released and subprocesses terminated."
                        )
                        coordinator.shutdown(f"timeout after {self._timeout}s")
                        return False  # unreachable — shutdown exits
            else:
                return _run_stages()
        finally:
            coordinator.deactivate()

    def _preflight_check_provisioners(self, stages_to_run: List[DeploymentStageModel]) -> List[str]:
        """Validate every stage's provisioner environment before any stage runs.

        For each stage, creates the deployer and runs the same
        ``validate_workspace()`` / ``validate_environment()`` checks that
        ``_execute_stage_provisioning`` runs later — but up front, before the
        deployment lock is acquired and before stage 1 has made any real
        infrastructure changes.

        A failure on a stage configured with ``on_failure: continue`` is
        downgraded to a warning message (that stage would be skipped, not
        fatal, once execution actually reached it) rather than aborting the
        whole deploy. Any other stage (``stop`` — the default — or
        ``rollback``) is fatal here, matching how ``_run_stages`` already
        treats it once discovered mid-loop.

        Returns:
            List of fatal error messages (empty when every ``stop``/``rollback``
            stage's provisioner environment is confirmed ready).
        """
        fatal_errors: List[str] = []

        for stage in stages_to_run:
            errors_before = len(self._errors)
            deployer = self._create_deployer(stage)
            # _create_deployer() appends failures straight to self._errors —
            # reclaim them here so on_failure semantics can be applied instead
            # of letting them leak through regardless of that setting.
            own_errors = self._errors[errors_before:]
            del self._errors[errors_before:]

            if deployer is None:
                messages = own_errors or [f"Stage '{stage.name}': failed to create deployer for pre-flight check."]
                for msg in messages:
                    self._record_preflight_issue(stage, msg, fatal_errors)
                continue

            for _label, validate_fn in (
                ("workspace", deployer.validate_workspace),
                ("environment", deployer.validate_environment),
            ):
                ok, msgs = validate_fn()
                if not ok:
                    for msg in msgs or [f"Stage '{stage.name}': pre-flight validation failed."]:
                        self._record_preflight_issue(stage, msg, fatal_errors)
                    break  # no point running the next check for an already-failed stage

        return fatal_errors

    def _record_preflight_issue(self, stage: "DeploymentStageModel", message: str, fatal_errors: List[str]) -> None:
        """Route a single pre-flight finding to either a warning or a fatal error.

        ``on_failure: continue`` stages are recorded as warnings (visible, but
        non-blocking) — anything else is added to ``fatal_errors``.
        """
        full_message = f"Stage '{stage.name}': {message}"
        if stage.on_failure == "continue":
            self._messages.append(f"⚠️  Pre-flight: {full_message} (on_failure=continue — skipped when reached)")
        else:
            fatal_errors.append(full_message)

    def _execute_stage_provisioning(self, stage: DeploymentStageModel) -> bool:
        """Instantiate the deployer for *stage*, validate, then run the step sequence.

        Step sequences:
          dry-run  : setup → check → plan
          destroy  : setup → destroy  (requires --force for -auto-approve)
          normal   : setup → check → plan → apply
        """
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

        # --- pre-flight validation ---
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
                return False

        # --- determine step sequence ---
        if self._dry_run:
            steps_to_run = [STEP_SETUP, STEP_CHECK, STEP_PLAN]
        else:
            steps_to_run = [STEP_SETUP, STEP_CHECK, STEP_PLAN, STEP_APPLY]

        # --- dry-run: surface deployer-specific plan context before steps run ---
        if self._dry_run and self._is_console_output():
            for line in deployer.describe_plan():
                click.echo(f"    [DRY-RUN] {line}")

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

        # --- stage-level before hook ---
        if not self._run_hierarchy_lifecycle_phase(
            "deploy_stage_before",
            context={"stage": str(stage.name), "dry_run": self._dry_run},
        ):
            self._errors.append(f"Stage '{stage.name}': deploy_stage_before lifecycle hook failed.")
            self._record_stage_result(
                stage_name=str(stage.name),
                provisioner=stage.provisioner,
                topology=stage.topology,
                status="failed",
                started_at=stage_started,
                completed_at=_dt.now(_tz.utc).isoformat(),
                error="deploy_stage_before hook failed",
            )
            return False

        # --- execute each step ---
        for step_name in steps_to_run:
            if step_name not in supported:
                self._errors.append(
                    f"Stage '{stage.name}': step '{step_name}' is not supported "
                    f"by deployer '{deployer.get_deployer_name()}'."
                )
                return False

            # Build the appropriate line callback for this step.
            line_cb: Optional[Callable[[str, str], None]] = None
            if self._is_ndjson_output():
                # Tier 2: stream each subprocess output line as an NDJSON event.
                self.emit_ndjson(
                    {
                        "event": "step_start",
                        "step": step_name,
                        "stage": stage.name,
                        "ts": _dt.now(_tz.utc).isoformat(),
                    }
                )
                line_cb = self.make_ndjson_line_callback(step=step_name, stage=stage.name)
            elif self._is_verbose():
                # Tier 1: print subprocess lines live to the console as they arrive.
                # Use the deployer name (e.g. "terraform") so the output is clearly
                # attributed, with a │ gutter to separate it from strata messages.
                def _make_verbose_cb(tool: str) -> Callable[[str, str], None]:
                    def _cb(stream: str, text: str) -> None:
                        if stream == "stderr":
                            click.secho(f"      {tool} │ {text}", fg="yellow", err=True)
                        else:
                            click.secho(f"      {tool} │ {text}", fg="cyan")

                    return _cb

                line_cb = _make_verbose_cb(deployer.get_deployer_name())

            if self._is_console_output():
                prefix = "[DRY-RUN] " if self._dry_run else ""
                click.echo(f"    {prefix}{step_name}")

            step_fn = getattr(deployer, step_name)
            # Steps that support line_callback accept it as a keyword arg;
            # output/show_plan return (bool, dict, list) and don't stream.

            # --- deploy_apply_before hook ---
            if step_name == STEP_APPLY:
                if not self._run_hierarchy_lifecycle_phase(
                    "deploy_apply_before",
                    context={"stage": str(stage.name), "dry_run": self._dry_run},
                ):
                    self._errors.append(f"Stage '{stage.name}': deploy_apply_before lifecycle hook blocked apply.")
                    self._record_stage_result(
                        stage_name=str(stage.name),
                        provisioner=stage.provisioner,
                        topology=stage.topology,
                        status="failed",
                        started_at=stage_started,
                        completed_at=_dt.now(_tz.utc).isoformat(),
                        steps=steps_to_run,
                        error="deploy_apply_before hook blocked apply",
                    )
                    return False

            if step_name in (STEP_SETUP, STEP_CHECK, STEP_PLAN, STEP_APPLY, STEP_DESTROY):
                ok, msgs = step_fn(line_callback=line_cb)
            else:
                ok, msgs = step_fn()
            self._messages.extend(msgs)
            if self._is_console_output():
                for msg in msgs:
                    click.echo(f"      {msg}")
                if self._dry_run and not msgs:
                    click.echo(f"      (no extra information available for '{step_name}' in dry run)")
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
                if self._ai:
                    self._run_ai_failure_diagnosis(
                        error_output="\n".join(msgs),
                        step=step_name,
                        stage_name=str(stage.name),
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

            # --- deploy_apply_after hook ---
            if step_name == STEP_APPLY:
                if not self._run_hierarchy_lifecycle_phase(
                    "deploy_apply_after",
                    context={"stage": str(stage.name), "dry_run": self._dry_run},
                ):
                    self._errors.append(f"Stage '{stage.name}': deploy_apply_after lifecycle hook failed.")
                    self._record_stage_result(
                        stage_name=str(stage.name),
                        provisioner=stage.provisioner,
                        topology=stage.topology,
                        status="failed",
                        started_at=stage_started,
                        completed_at=_dt.now(_tz.utc).isoformat(),
                        steps=steps_to_run,
                        error="deploy_apply_after hook failed",
                    )
                    return False

            # --- plan gate: enforce deploy_plan_after hook before apply ---
            if step_name == STEP_PLAN and STEP_APPLY in steps_to_run:
                if not self._run_lifecycle_phase(
                    "deploy_plan_after",
                    context={"stage": str(stage.name), "dry_run": self._dry_run},
                ):
                    self._errors.append(f"Stage '{stage.name}': deploy_plan_after lifecycle hook blocked apply.")
                    self._record_stage_result(
                        stage_name=str(stage.name),
                        provisioner=stage.provisioner,
                        topology=stage.topology,
                        status="failed",
                        started_at=stage_started,
                        completed_at=_dt.now(_tz.utc).isoformat(),
                        steps=steps_to_run,
                        error="deploy_plan_after hook blocked apply",
                    )
                    return False

                # --- policy evaluation: plan phase ---
                if not self._evaluate_phase_policies("plan", stage, deployer):
                    self._record_stage_result(
                        stage_name=str(stage.name),
                        provisioner=stage.provisioner,
                        topology=stage.topology,
                        status="failed",
                        started_at=stage_started,
                        completed_at=_dt.now(_tz.utc).isoformat(),
                        steps=steps_to_run,
                        error="Plan policy denied deployment",
                    )
                    return False

                # --- policy evaluation: deploy phase ---
                if not self._evaluate_phase_policies("deploy", stage, deployer):
                    self._record_stage_result(
                        stage_name=str(stage.name),
                        provisioner=stage.provisioner,
                        topology=stage.topology,
                        status="failed",
                        started_at=stage_started,
                        completed_at=_dt.now(_tz.utc).isoformat(),
                        steps=steps_to_run,
                        error="Deploy policy denied deployment",
                    )
                    return False

                # --- post-plan condition gate evaluation (ADR-0057 Phase 4) ---
                # Evaluates cost_review and security_review gates with real plan data.
                # Runs AFTER plan (so cost delta and CVE counts are available) and
                # BEFORE apply. approval and scheduled gates already ran pre-provisioning.
                if not self._evaluate_condition_gates_post_plan(stage, deployer, stage_started, steps_to_run):
                    return False

                # --- AI plan gate (--ai / --strict-ai-review) ---
                if self._ai or self._strict_ai_review:
                    ai_ok = self._check_ai_plan_gate(stage, msgs)
                    if not ai_ok:
                        self._record_stage_result(
                            stage_name=str(stage.name),
                            provisioner=stage.provisioner,
                            topology=stage.topology,
                            status="failed",
                            started_at=stage_started,
                            completed_at=_dt.now(_tz.utc).isoformat(),
                            steps=steps_to_run,
                            error="AI plan review blocked deployment",
                        )
                        return False

        # --- save plan JSON for artifact upload / downstream use ---
        if STEP_PLAN in steps_to_run:
            ok_save, plan_json_path, save_msgs = deployer.save_plan_json()
            self._messages.extend(save_msgs)
            if self._is_console_output():
                for msg in save_msgs:
                    click.echo(f"      {msg}")
                if ok_save and plan_json_path:
                    click.echo(f"    plan JSON → {plan_json_path}")

            # --- cost diff after plan (dry-run only, non-fatal) ---
            if self._dry_run and ok_save and plan_json_path:
                self._run_cost_diff_for_stage(stage, plan_json_path)

        # --- collect outputs for downstream stages ---
        out_path = None
        if STEP_APPLY in steps_to_run:
            _ok_out, _outputs, _sensitive, _out_msgs = deployer.collect_outputs()
            if _ok_out and self._resolved_values is not None:
                if _outputs:
                    self._resolved_values.stage_outputs.update(_outputs)
                if _sensitive:
                    self._resolved_values.stage_outputs_sensitive.update(_sensitive)
                if self._is_console_output() and (_outputs or _sensitive):
                    _sens_note = f", {len(_sensitive)} sensitive (not injected)" if _sensitive else ""
                    click.echo(f"    \u2713  Collected {len(_outputs)} output(s){_sens_note} for downstream stages.")
                if _outputs or _sensitive:
                    self.logger.debug(
                        "stage_outputs_collected",
                        stage=stage.name,
                        **self._resolved_values.debug_summary(),
                    )
                    if self._is_verbose() and self._is_console_output():
                        if _outputs:
                            for k, v in _outputs.items():
                                click.echo(f"      [stage_output] {k} = {v}")
                        if _sensitive:
                            for k in _sensitive:
                                click.echo(f"      [stage_output_sensitive] {k} = ***")
            if _ok_out:
                out_path = self._write_outputs_artifact(str(stage.name), _outputs, _sensitive)
                if out_path and self._is_console_output():
                    click.echo(f"    outputs \u2192 {out_path}")
        elif self._dry_run and self._is_console_output():
            click.echo("    [DRY-RUN] Stage outputs not captured \u2014 apply did not run.")

        if self._is_ndjson_output():
            self.emit_ndjson(
                {
                    "event": "stage_end",
                    "stage": stage.name,
                    "success": True,
                    "ts": _dt.now(_tz.utc).isoformat(),
                }
            )

        # Record stage result for deployment manifest
        stage_outputs = None
        if STEP_APPLY in steps_to_run and self._resolved_values is not None:
            stage_outputs = dict(self._resolved_values.stage_outputs) if self._resolved_values.stage_outputs else None

        outputs_artifact_ref: Optional[ManifestOutputsReferenceModel] = None
        if out_path is not None and self._deployment_service is not None:
            deploy_meta = self._deployment_service.model.meta  # type: ignore[union-attr]
            labels = deploy_meta.labels or {}
            version = str(labels.get("version", "unknown"))
            try:
                rel = str(out_path.relative_to(self._work_path))
            except ValueError:
                rel = str(out_path)
            outputs_artifact_ref = ManifestOutputsReferenceModel(
                path=rel,
                stage=str(stage.name),
                version=version,
                written_at=_dt.now(_tz.utc).isoformat(),
            )

        self._record_stage_result(
            stage_name=str(stage.name),
            provisioner=stage.provisioner,
            topology=stage.topology,
            status="success",
            started_at=stage_started,
            completed_at=_dt.now(_tz.utc).isoformat(),
            steps=steps_to_run,
            outputs=stage_outputs,
            outputs_artifact=outputs_artifact_ref,
        )

        # --- stage-level after hook ---
        if not self._run_hierarchy_lifecycle_phase(
            "deploy_stage_after",
            context={"stage": str(stage.name), "dry_run": self._dry_run},
        ):
            self._errors.append(f"Stage '{stage.name}': deploy_stage_after lifecycle hook failed.")
            return False

        # --- post-apply verify gate (ADR-0057 Phase 4) ---
        # Evaluates verify gates AFTER apply completes — requires human confirmation.
        if STEP_APPLY in steps_to_run and not self._dry_run:
            if not self._evaluate_verify_gate_post_apply(stage, stage_started, steps_to_run):
                return False

        return True

    def _evaluate_phase_policies(self, phase: str, stage: DeploymentStageModel, deployer) -> bool:
        """Evaluate policies for *phase* ('plan' or 'deploy'). Returns False if any deny-enforcement policy fails."""
        from strata.models.deployment_manifest_model import ManifestPolicyResultModel
        from strata.validators.policies.base_policy import PolicyContext
        from strata.validators.policies.policy_engine import PolicyEngine

        if self._configuration_service is None:
            return True

        spec = self._configuration_service.model.spec if self._configuration_service.model else None
        policy_models = getattr(spec, "policies", None) or []
        phase_policies = [p for p in policy_models if p.phase == phase and p.enabled]
        if not phase_policies:
            return True

        # Load plan JSON from the deployer (available for both plan and deploy phases)
        plan_data = None
        if hasattr(deployer, "show_plan"):
            _, plan_data, _ = deployer.show_plan()

        # Load cost.json from build artifacts if present
        cost_data = None
        if self._deployment_service is not None and self._build_path is not None:
            try:
                import json as _json

                cost_path = self._deployment_service.get_build_path(self._build_path) / "cost.json"
                if cost_path.exists():
                    cost_data = _json.loads(cost_path.read_text(encoding="utf-8"))
            except Exception:
                pass  # cost_data stays None — policy will skip gracefully

        context = PolicyContext(
            phase=phase,
            work_path=self._work_path,
            deployment_service=self._deployment_service,
            configuration_service=self._configuration_service,
            plan_data=plan_data,
            build_path=self._build_path,
            cost_data=cost_data,
        )

        engine = PolicyEngine(phase_policies)
        results = engine.evaluate(phase, context)

        denied = False
        for policy_model, result in zip(phase_policies, results, strict=False):
            self._policy_results.append(
                ManifestPolicyResultModel(
                    policy_name=result.policy_name,
                    policy_type=policy_model.type,
                    phase=phase,
                    enforcement=result.enforcement,
                    passed=result.passed,
                    violations=result.violations or [],
                )
            )
            if result.passed:
                if self._is_verbose() and self._is_console_output():
                    click.echo(f"    \u2713  Policy '{result.policy_name}' passed")
            else:
                for v in result.violations:
                    if result.enforcement == "deny":
                        click.echo(f"    \u2717  Policy '{result.policy_name}' DENIED: {v}")
                        self._errors.append(f"Policy '{result.policy_name}': {v}")
                        denied = True
                    elif result.enforcement == "warn":
                        click.echo(f"    \u26a0  Policy '{result.policy_name}' warning: {v}")
                    elif result.enforcement == "audit" and self._is_verbose():
                        click.echo(f"    \u00b7  Policy '{result.policy_name}' audit: {v}")
                self._forward_policy_violation_audit_event(result)
        return not denied
