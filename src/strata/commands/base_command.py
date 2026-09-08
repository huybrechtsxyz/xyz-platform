"""Base command class for Strata CLI commands."""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, ClassVar, Dict, List, Optional

import click

from strata.controllers.integration_controller import IntegrationController
from strata.controllers.solution_controller import SolutionController
from strata.logger import (
    configure_audit_log,
    get_audit_log_source,
    get_configured_audit_log_path,
    get_logger,
    is_audit_configured,
    shutdown_audit,
)
from strata.logger.context import set_context
from strata.logger.logger import reconfigure_logging
from strata.utils.config import (
    DEFAULT_BUILD_PATH,
    DOCS_URL,
    SUPPORT_URL,
    get_audit_log_path,
    get_logs_dir,
)
from strata.utils.system import generate_uuid, redact_argv, resolve_path, resolve_work_path
from strata.utils.version import get_version


class BaseCommand:
    """Base command class for Strata CLI commands."""

    OPERATION = "base_command"
    SHOW_CHROME: ClassVar[bool] = True  # Set False on commands that suppress header/footer (e.g. tools commands)

    # ADR-0066 problem 3: read-only commands have no observable side effect, so
    # auditing them is pure volume with no signal — this was ~95% of the measured
    # 18,853-entry audit log (VS Code's `workitem_list` polling, editor `schema_get`/
    # `schema_list`, `tools_status`). Matches the criterion `logger/audit.py` already
    # documents ("user actions with observable side-effects").
    _AUDIT_READ_ONLY_OPERATION_PREFIXES: ClassVar[tuple] = ("schema_",)
    _AUDIT_READ_ONLY_OPERATION_SUFFIXES: ClassVar[tuple] = ("_list", "_show", "_status")

    @classmethod
    def _is_audit_mutating_operation(cls) -> bool:
        """Return False for read-only operations excluded from the `command.executed` audit entry."""
        operation = cls.OPERATION
        if operation.startswith(cls._AUDIT_READ_ONLY_OPERATION_PREFIXES):
            return False
        if operation.endswith(cls._AUDIT_READ_ONLY_OPERATION_SUFFIXES):
            return False
        return True

    def __init__(
        self,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ) -> None:
        """Initialize the base command."""
        # Timer attributes — always UTC-aware
        self._start_time: datetime = datetime.now(timezone.utc)
        self._end_time: datetime = datetime.now(timezone.utc)

        # Paths
        self._work_path: Path = self._get_current_workpath(work_path)

        # Correlation IDs — set during _initialize()
        self._solution_controller: SolutionController = SolutionController(self._work_path)
        self._execution_id: str = generate_uuid()

        # Structured result data
        self._output_data: Any = {}
        self._output_format = output or "console"
        self._output_verbose = verbose or False
        self._output_quiet = quiet or False

        # Logging context (must be after _output_verbose/_output_quiet are set)
        self._configure_session_logging()

        # Integration controller (lazy-loaded)
        self._integration_controller: Optional[IntegrationController] = None

        # Merged configuration from active profile configfile_paths (set during _initialize)
        self._merged_config: Optional[Dict[str, Any]] = None

        # Message and error accumulation
        self._messages: List[str] = []
        self._errors: List[str] = []

    def execute(self) -> bool:
        """Execute the command using the standard lifecycle.

        Phase execution contract (short-circuit on early failure):

        - Phase 1 (``_initialize``): always runs.
        - Phase 2 (``_before_execute``): skipped if phase 1 failed.
        - Phase 3 (``_execute``): skipped if phase 1 or 2 failed.
        - Phase 4 (``_after_execute``): always runs regardless of earlier failures.
        - Phase 5 (``_finalize``): always runs — writes audit entry and structured output.

        Each phase is wrapped in try/except; errors accumulate in ``self._errors``.
        Skipping phases 2–3 on early failure prevents cascading errors when the
        workspace is not initialised (e.g. no ``solution.json``).

        Override ``_execute()`` to provide command-specific logic.
        Override individual phases (``_initialize``, ``_before_execute``, etc.)
        for customisation. Do not override ``execute()`` itself.
        """
        success = True

        # Phase 1: workspace, timing, logging, config setup
        try:
            if not self._initialize(show_header=self.SHOW_CHROME):
                success = False
        except click.UsageError:
            raise
        except Exception as e:
            error_msg = f"Initialization failed in {self.__class__.__name__}: {e}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            success = False

        # Phase 2: pre-execution validation and requirement checks
        try:
            if success and not self._before_execute():
                if self._is_console_output():
                    click.echo("\n\u274c  Pre-execution validation failed")
                success = False
        except click.UsageError:
            raise
        except Exception as e:
            error_msg = f"Pre-execution failed in {self.__class__.__name__}: {e}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            success = False

        # Phase 3: core business logic — the subclass override point
        try:
            if success and not self._execute():
                success = False
        except click.UsageError:
            raise
        except Exception as e:
            error_msg = f"Execution failed in {self.__class__.__name__}: {e}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            success = False

        # Phase 4: post-execution cleanup
        try:
            if not self._after_execute():
                success = False
        except click.UsageError:
            raise
        except Exception as e:
            error_msg = f"Post-execution failed in {self.__class__.__name__}: {e}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            success = False

        # Phase 5: audit, structured output, footer — always runs
        self._finalize(success=success, show_footer=self.SHOW_CHROME)
        return success

    def _execute(self) -> bool:
        """Phase 3 — core business logic.

        Override in subclasses. Return True on success, False on failure
        (add details to self._errors). Raise exceptions for unexpected errors —
        execute() will catch and record them.
        """
        raise NotImplementedError(f"{self.__class__.__name__} must implement _execute().")

    def _get_build_path(self) -> Path:
        """Return the resolved build output path.

        Uses ``_configuration_service.get_default_build_path()`` when the
        configuration service is already loaded (i.e. after ``_before_execute``
        has run), otherwise falls back to ``work_path / DEFAULT_BUILD_PATH``.
        """
        if getattr(self, "_configuration_service", None) is not None:
            return self._configuration_service.get_default_build_path(  # type: ignore[attr-defined]
                self._work_path, create_path=False
            )
        return self._work_path / DEFAULT_BUILD_PATH

    def get_required_integrations(self) -> Dict[str, str]:
        """
        Declare required integrations for this command.

        Subclasses override this to declare what external tools they need.
        Returns a dict mapping integration names to operation descriptions.

        Example:
            return {"git": "repository clone operations", "docker": "container build"}

        Returns:
            Dict[str, str]: Empty dict (no requirements by default)
        """
        return {}

    # Console output methods

    @classmethod
    def show_console_header(cls, work_path: Optional[str] = None) -> None:
        click.echo("─" * 80)
        click.echo(f"🚀 Strata — CLI (v{get_version()})")
        click.echo("─" * 80)
        click.echo("Automates workspace preparation, configuration, and deployment.")
        click.echo(f"⏱️   Timestamp       : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
        click.echo(f"📜  Entry point     : {' '.join(redact_argv(sys.argv))}")
        click.echo(f"📂  Current dir     : {os.getcwd()}")
        if work_path:
            click.echo(f"📁  Work path       : {work_path}")

    @classmethod
    def show_console_footer(cls) -> None:
        click.echo("─" * 80)
        click.echo("✨ Thank you for using Strata CLI!")
        click.echo(f"📘 Documentation: {DOCS_URL}")
        click.echo(f"💬 Support: {SUPPORT_URL}")
        click.echo("─" * 80)

    # Message and error handling methods

    def has_errors(self) -> bool:
        """Check if any errors were accumulated during execution."""
        return len(self._errors) > 0

    def get_errors(self) -> List[str]:
        """
        Get accumulated errors from command execution.

        Returns:
            List[str]: Copy of error messages list
        """
        return self._errors.copy()

    def clear_errors(self) -> None:
        """Clear accumulated errors."""
        self._errors.clear()

    def has_messages(self) -> bool:
        """Check if any messages were accumulated during execution."""
        return len(self._messages) > 0

    def get_messages(self) -> List[str]:
        """
        Get accumulated messages from command execution.

        Returns:
            List[str]: Copy of messages list
        """
        return self._messages.copy()

    def clear_messages(self) -> None:
        """Clear accumulated messages."""
        self._messages.clear()

    # Public helper methods

    def get_integration_controller(self) -> IntegrationController:
        """
        Get or create the IntegrationController instance (lazy-loaded).

        Returns:
            IntegrationController: The controller instance
        """
        if self._integration_controller is None:
            self._integration_controller = IntegrationController()
        return self._integration_controller

    def _load_deployment_service_with_extends(
        self,
        file_path: Path,
        repo_map: Optional[Dict[str, str]] = None,
    ) -> Optional[Any]:
        """Load and Pydantic-validate a deployment file, resolving ``spec.extends``
        (ADR-0039) first when present.

        Shared by build and deploy commands so both see the exact same merged
        model that ``validate --deep`` already checks — a deployment file that
        passes ``strata validate --deep`` (which resolves ``extends``) must be
        buildable/deployable too, not just validatable.

        Returns:
            The loaded, validated ``DeploymentService`` on success. On failure,
            error(s) are appended to ``self._errors`` and ``None`` is returned.
        """
        from strata.services.deployment_extension_resolver import DeploymentExtensionResolver
        from strata.services.deployment_service import DeploymentService

        resolver = DeploymentExtensionResolver(work_path=Path(self._work_path), repo_map=repo_map or {})
        if resolver.needs_resolution(file_path):
            try:
                merged_data = resolver.resolve(file_path)
            except (ValueError, FileNotFoundError) as exc:
                self._errors.append(f"Deployment extends resolution failed: {exc}")
                return None
            deployment_service = DeploymentService(path=str(file_path), data=merged_data)
            is_valid, errors = deployment_service.validate()
            if not is_valid:
                # Unlike DeploymentService.load(), constructing directly with `data=`
                # bypasses the classmethod that extends self._errors from validate()'s
                # return value — do it here so get_validation_errors() below actually
                # reports the failure instead of silently returning an empty list.
                deployment_service._errors.extend(errors)
        else:
            deployment_service = DeploymentService.load(str(file_path), validate=True)

        if not deployment_service.is_validated():
            self._errors.extend(deployment_service.get_validation_errors())
            return None

        return deployment_service

    # Lifecycle methods

    def _initialize(self, show_header: bool = True) -> bool:
        """
        Initialize the command before execution.
        Sets up paths, timing, and logging context.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        try:
            self._start_time = datetime.now(timezone.utc)
            self._configure_session_logging()

            self.logger.debug(
                "Initializing command",
                extra={"command_class": self.__class__.__name__},
            )

            if not self._work_path.exists():
                error_msg = f"Work path does not exist: {self._work_path}"
                self.logger.error(error_msg)
                self._errors.append(error_msg)
                return False

            # Try to load — but don't fail hard if solution.json doesn't exist yet
            # (e.g. during `strata sln init` itself)
            solution_id: str = "unknown"
            solution_path = SolutionController.get_solution_json_path(self._work_path)
            if solution_path.exists():
                self._solution_controller.load()
                solution_id = self._solution_controller.get_solution_id()
            else:
                error_msg = f"Solution configuration not found at {solution_path}. This command requires an initialized solution. Please run 'strata sln init' first."
                self.logger.error(error_msg)
                self._errors.append(error_msg)
                return False

            set_context({"solution_id": solution_id, "execution_id": self._execution_id})

            # Configure audit log (separate from application logs).
            # Skip if already configured by the audit: section in logging.yaml.
            if not is_audit_configured():
                audit_path = get_audit_log_path(self._work_path)
                configure_audit_log(log_path=str(audit_path))

            # Start session operation if specified
            self._start_session_operation()

            # Load env-file sources from active profile and inject into os.environ
            self._load_env_sources()

            # Load and merge configfile_paths from active profile
            self._load_config_sources()

            # Phase 1: reconfigure the audit journal from spec.audit.journal now that
            # configuration is loaded (ADR-0066). No-op if logging.yaml already claimed it.
            self._apply_audit_journal_config()

            if show_header and self._is_console_output():
                self.show_console_header()

            self.logger.debug(
                "Command initialized successfully",
                extra={
                    "command_class": self.__class__.__name__,
                    "solution_id": solution_id,
                    "execution_id": self._execution_id,
                },
            )

            return True
        except Exception as e:
            self.logger.error(
                "Failed to initialize command",
                extra={
                    "error": str(e),
                    "exc_info": True,
                    "execution_id": self._execution_id,
                    "command_class": self.__class__.__name__,
                },
            )
            self._errors.append(f"Failed to initialize command: {e}")
            return False

    def _initialize_session(self, show_header: bool = True) -> bool:
        """Workspace-optional variant of :meth:`_initialize`.

        Performs session setup (timing, logging, context, env/config loading)
        and loads the solution when ``solution.json`` exists, but does *not*
        require it to be present.  Returns ``False`` only when the work path
        itself is missing or an unexpected exception is raised.

        Commands that function without an initialized workspace must override
        ``_initialize()`` and delegate here instead of calling
        ``super()._initialize()``, so that missing-solution-json errors are
        neither logged to stdout nor accumulated in ``self._errors``.
        """
        try:
            self._start_time = datetime.now(timezone.utc)
            self._configure_session_logging()

            self.logger.debug(
                "Initializing command",
                extra={"command_class": self.__class__.__name__},
            )

            if not self._work_path.exists():
                error_msg = f"Work path does not exist: {self._work_path}"
                self.logger.error(error_msg)
                self._errors.append(error_msg)
                return False

            solution_id: str = "unknown"
            solution_path = SolutionController.get_solution_json_path(self._work_path)
            if solution_path.exists():
                self._solution_controller.load()
                solution_id = self._solution_controller.get_solution_id()

            set_context({"solution_id": solution_id, "execution_id": self._execution_id})

            if not is_audit_configured():
                audit_path = get_audit_log_path(self._work_path)
                configure_audit_log(log_path=str(audit_path))

            self._start_session_operation()
            self._load_env_sources()
            self._load_config_sources()
            self._apply_audit_journal_config()

            if show_header and self._is_console_output():
                self.show_console_header()

            self.logger.debug(
                "Command initialized successfully",
                extra={
                    "command_class": self.__class__.__name__,
                    "solution_id": solution_id,
                    "execution_id": self._execution_id,
                },
            )

            return True
        except Exception as e:
            self.logger.error(
                "Failed to initialize command",
                extra={
                    "error": str(e),
                    "exc_info": True,
                    "execution_id": self._execution_id,
                    "command_class": self.__class__.__name__,
                },
            )
            self._errors.append(f"Failed to initialize command: {e}")
            return False

    def _before_execute(self) -> bool:
        """Optional steps before main execution."""
        self.logger.debug(
            "Executing pre-command logic",
            extra={"command_class": self.__class__.__name__},
        )

        # Validate integration requirements
        if not self._validate_requirements():
            return False

        # Placeholder for additional pre-execution logic (hooks, validation, etc.)
        self.logger.debug(
            "Pre-command logic executed successfully",
            extra={"command_class": self.__class__.__name__},
        )
        return True

    def _after_execute(self) -> bool:
        """
        Execute post-command logic (cleanup, notifications, etc.).

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        self.logger.debug(
            "Executing post-command logic",
            extra={"command_class": self.__class__.__name__},
        )

        self.logger.debug(
            "Post-command logic executed successfully",
            extra={"command_class": self.__class__.__name__},
        )
        return True

    def _run_lifecycle_phase(self, phase_name: str, context: Optional[dict] = None) -> bool:
        """Execute a named lifecycle phase via the active ConfigurationService.

        Returns True when no hooks are defined for the phase or all scripts succeed.
        Returns False and appends errors when any script fails.
        """
        from strata.controllers.lifecycle_controller import LifecycleController

        lc = LifecycleController()
        if not lc.execute_configuration_phase(
            phase_name=phase_name,
            work_path=self._work_path,
            context=context,
        ):
            for err in lc.get_errors():
                self._errors.append(f"Lifecycle hook '{phase_name}' failed: {err}")
            return False
        return True

    def _finalize(
        self,
        success: bool = False,
        show_footer: bool = True,
    ) -> bool:
        """
        Finalize the command execution (logging, metrics, cleanup).

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        self.logger.debug(
            "Finalizing command execution",
            extra={"command_class": self.__class__.__name__, "success": success},
        )

        if self._is_ndjson_output():
            # ── NDJSON mode: emit final "complete" event ──────────────────────

            self.emit_ndjson(
                {
                    "event": "complete",
                    "success": bool(success),
                    "command": self.OPERATION,
                    "execution_id": self._execution_id,
                    "timestamp": self._start_time.isoformat(),
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "data": self._output_data,
                    "messages": self._messages,
                    "errors": self._errors,
                }
            )

        elif self._is_structured_output():
            # ── Structured output (--output json / text) ─────────────────────
            envelope: Dict[str, Any] = {
                "success": bool(success),
                "command": self.OPERATION,
                "execution_id": self._execution_id,
                "timestamp": self._start_time.isoformat(),
                "data": self._output_data,
                "messages": self._messages,
                "errors": self._errors,
            }
            if self._output_format == "json":
                click.echo(json.dumps(envelope, indent=2, default=str))
            else:  # text
                click.echo(f"success: {envelope['success']}")
                click.echo(f"command: {envelope['command']}")
                click.echo(f"execution_id: {envelope['execution_id']}")
                click.echo(f"timestamp: {envelope['timestamp']}")
                for k, v in envelope["data"].items():
                    if isinstance(v, list):
                        click.echo(f"{k}:")
                        for item in v:
                            if isinstance(item, dict):
                                first = True
                                for ik, iv in item.items():
                                    prefix = "  - " if first else "    "
                                    click.echo(f"{prefix}{ik}: {iv}")
                                    first = False
                            else:
                                click.echo(f"  - {item}")
                    elif isinstance(v, dict):
                        click.echo(f"{k}:")
                        for dk, dv in v.items():
                            click.echo(f"  {dk}: {dv}")
                    else:
                        click.echo(f"{k}: {v}")
                if envelope["messages"]:
                    click.echo("messages:")
                    for m in envelope["messages"]:
                        click.echo(f"  - {m}")
                if envelope["errors"]:
                    click.echo("errors:")
                    for err in envelope["errors"]:
                        click.echo(f"  - {err}")

        elif show_footer and self._is_console_output():
            # ── Human-readable output (default) ──────────────────────────────
            # Show messages
            if self._messages and len(self._messages) > 0:
                click.echo("💬  Messages:")
                for msg in self._messages:
                    click.secho(f"    - {msg}")
                click.echo("")

            # Show errors
            if self._errors and len(self._errors) > 0:
                click.echo("❌  Errors:", err=True)
                for err in self._errors:
                    click.secho(f"    - {err}", fg="red", err=True)
                click.echo("", err=True)

            # Show verbose logging
            if self._is_verbose():
                self._print_verbose_logs()

            # Show footer
            self.show_console_footer()

        self._end_time = datetime.now(timezone.utc)
        duration = self._end_time - self._start_time

        # Emit audit entry only for mutating operations (ADR-0066 problem 3) — read-only
        # commands (*_list, *_show, *_status, schema_*) have no observable side effect.
        #
        # Routed through AuditController.forward() (ADR-0066 gap 3) rather than the raw
        # logger.audit.audit() call this used to make directly — command.executed now
        # gets the same policy gate and CloudEvents/ECS envelope as every other event
        # type, instead of unconditionally reaching only the local journal. This is a
        # deliberate behaviour change: command.executed defaults to disabled, so unless
        # spec.audit.policy.events.command.executed is explicitly enabled, CLI invocations
        # no longer write a local journal entry at all (previously unconditional) — see
        # the ADR's Risk section. Wrapped in try/except because forward() itself is not
        # (only its per-sink loop is) and _finalize() runs outside execute()'s own
        # per-phase try/except — an unhandled exception here must never crash the command.
        if self._is_audit_mutating_operation():
            try:
                self._forward_command_executed_audit_event(success=success, duration_seconds=duration.total_seconds())
            except Exception as e:
                self.logger.debug(f"Failed to forward command.executed audit event (non-fatal): {e}")
        shutdown_audit()

        self.logger.debug(
            "Command execution completed",
            extra={
                "command_class": self.__class__.__name__,
                "duration_seconds": duration.total_seconds(),
            },
        )

        return True

    def _forward_command_executed_audit_event(self, success: bool, duration_seconds: float) -> None:
        """Route the CLI-invocation record through ``AuditController.forward()`` (ADR-0066 gap 3).

        ``self.OPERATION`` travels inside the payload (not as the event type) since the
        closed event-type enum has a single ``command.executed`` type — every CLI
        invocation shares it, matching the ADR's type-name table (``cli_action`` ->
        ``command.executed``). ``AuditConfigModel`` is read from the already-populated
        ``ConfigurationService`` singleton (populated by ``_load_config_sources()`` /
        ``_apply_audit_journal_config()`` earlier in this same command's lifecycle);
        resolution failures fall back to ``forward()``'s own default (an all-defaults
        ``AuditConfigModel()``, under which ``command.executed`` stays disabled).
        """
        from strata.controllers.audit_controller import AuditController
        from strata.models.audit_config_model import AuditConfigModel

        audit_cfg: Optional[AuditConfigModel] = None
        try:
            from strata.services.configuration_service import ConfigurationService

            config_model = ConfigurationService.get_instance().model
            audit_cfg = getattr(getattr(config_model, "spec", None), "audit", None)
        except Exception as e:
            self.logger.debug(f"Failed to resolve spec.audit for command.executed (non-fatal): {e}")

        payload = {
            "execution_id": self._execution_id,
            "operation": self.OPERATION,
            "target": " ".join(redact_argv(sys.argv[1:])) if len(sys.argv) > 1 else self.OPERATION,
            "success": success,
            "duration_seconds": duration_seconds,
        }
        AuditController(work_path=self._work_path).forward("command.executed", payload, audit_config=audit_cfg)

    def _forward_policy_violation_audit_event(self, result: Any) -> None:
        """Forward a ``policy.violated`` event for one failed ``PolicyResult`` (ADR-0066 follow-up).

        Shared by every command that evaluates policies (``validate``, ``build``,
        ``deploy``, ``check_policy``) — each calls this right where it already loops
        over ``PolicyEngine.evaluate()`` results. The actual event construction lives
        in ``AuditController.forward_policy_violation()``, not duplicated here or at
        each call site — ``PolicyEngine`` itself (``validators/``) cannot call
        ``AuditController.forward()`` directly, since ``validators`` sits below
        ``controllers`` in ADR-0003's layering chain, so the trigger has to live at
        the command layer instead.

        Never raises: a forwarding failure must never fail a validate/build/deploy/
        policy-check run.
        """
        try:
            from strata.controllers.audit_controller import AuditController

            deployment = None
            deployment_service = getattr(self, "_deployment_service", None)
            if deployment_service is not None and getattr(deployment_service, "model", None) is not None:
                deployment = str(deployment_service.model.meta.name)

            AuditController(work_path=self._work_path).forward_policy_violation(
                result, execution_id=self._execution_id, deployment=deployment
            )
        except Exception as e:
            self.logger.debug(f"Failed to forward policy.violated audit event (non-fatal): {e}")

    # Output configuration

    def _is_quiet(self) -> bool:
        """Return True when --quiet is active: suppress all output."""
        return self._output_quiet

    def _is_verbose(self) -> bool:
        """Return True when --verbose is active: include debug log replay."""
        return self._output_verbose

    def _is_structured_output(self) -> bool:
        """Return True when --output <format> is active: emit machine-readable data (json, text, etc.)."""
        return bool(self._output_format) and self._output_format not in ("console", "ndjson")

    def _is_ndjson_output(self) -> bool:
        """Return True when --output ndjson is active: emit events as newline-delimited JSON."""
        return self._output_format == "ndjson"

    def _is_console_output(self) -> bool:
        """Return True for default human-readable console output (no --quiet, no explicit --output format)."""
        return not self._output_quiet and (not bool(self._output_format) or self._output_format == "console")

    def emit_ndjson(self, event: Dict[str, Any]) -> None:
        """Emit one NDJSON event line immediately to stdout.

        Each call writes exactly one ``\\n``-terminated JSON object.  Callers
        should add a ``"ts"`` field with the current ISO-8601 timestamp when the
        event carries timing information.
        """
        click.echo(json.dumps(event, default=str), nl=True)

    def make_ndjson_line_callback(self, step: str, stage: Optional[str] = None) -> "Callable[[str, str], None]":
        """Return a ``line_callback`` that emits NDJSON ``line`` events for every subprocess output line.

        The returned callable signature matches the one expected by
        ``run_command(line_callback=...)`` and ``TerraformDeployer`` step methods:
        ``(stream: str, text: str) -> None``.
        """

        def _cb(stream: str, text: str) -> None:
            event: Dict[str, Any] = {
                "event": "line",
                "step": step,
                "stream": stream,
                "text": text,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
            if stage is not None:
                event["stage"] = stage
            self.emit_ndjson(event)

        return _cb

    # Internal helper methods

    # Configure session logging based on .strata/logging.yaml if it exists, and refresh logger
    def _configure_session_logging(self) -> None:
        """
        Auto-configure logging from session-specific YAML if available.

        Looks for ``.strata/logging.yaml`` under ``self._work_path``.
        Reconfigures logging only when the discovered config path changes.
        Falls back to enabling a default session log file for crash diagnostics.
        """
        # Logger must exist — raise immediately if it cannot be created.
        self.logger = get_logger(self.__class__.__module__)

        try:
            logging_config = SolutionController.get_logging_config_path(self._work_path)
            if logging_config is None:
                self._configure_default_session_log()
                return

            if not logging_config.exists():
                self._configure_default_session_log()
                return

            config_path = resolve_path(str(logging_config))
            reconfigure_logging(config_path=config_path)

            # Refresh logger after reconfiguration
            self.logger = get_logger(self.__class__.__module__)
            self.logger.debug(
                "Session logging configuration loaded",
                extra={
                    "command_class": self.__class__.__name__,
                    "logging_config_path": config_path,
                },
            )

        except Exception as e:
            # Logging configuration should not block command execution
            try:
                self.logger.warning(
                    f"Failed to auto-configure session logging: {str(e)}",
                    extra={"command_class": self.__class__.__name__},
                )
            except Exception:
                # Best-effort only; never raise from logging setup
                pass

    def _configure_default_session_log(self) -> None:
        """Enable a JSON session log file for crash diagnostics when no YAML config exists."""
        from datetime import date

        log_dir = get_logs_dir(self._work_path)
        if not log_dir.parent.exists():
            return  # work_path/.strata/ doesn't exist yet (pre-init)

        session_log = log_dir / f"session-{date.today().isoformat()}.json"
        level = "DEBUG" if self._output_verbose else "WARNING"
        reconfigure_logging(
            level=level,
            enable_console=not self._output_quiet,
            enable_json_file=True,
            log_file_path=str(session_log),
        )
        self.logger = get_logger(self.__class__.__module__)

    # Get the work path based on input or default to current directory
    def _get_current_workpath(self, work_path: Optional[str]) -> Path:
        """Resolve the workspace root — delegates to ``utils.system.resolve_work_path``."""
        return resolve_work_path(work_path or None)

    # Print log lines for the current execution when --verbose is active
    def _print_verbose_logs(self) -> None:
        """Print log lines for the current execution when --verbose is active."""
        try:
            ok, entries, _ = self._solution_controller.get_logs(
                work_path=self._work_path,
                execution_id=self._execution_id,
            )
            if not ok or not entries:
                return
            click.echo()
            click.echo("─" * 80)
            click.echo(f"📋  Execution log  [{self._execution_id}]")
            click.echo("─" * 80)
            for entry in entries:
                ts = entry.get("timestamp", "")
                lvl = entry.get("level", "").upper()
                msg = entry.get("event", entry.get("message", ""))
                click.echo(f"  {ts}  {lvl:<8}  {msg}")
        except Exception as e:
            self.logger.debug(f"Could not display verbose logs: {e}")

    # Start a session operation for tracking in SessionController (e.g., for last_execution_id)
    def _start_session_operation(self) -> None:
        """
        Mark the start of a named operation in the session state.

        Records the current execution_id against the session and persists it.
        The current SessionController tracks last-execution rather than named
        operation trees, so this calls update_last_execution + save_session.

        Args:
            operation: Name of the operation (e.g., 'tools_status', 'session_init')
        """
        try:
            self._solution_controller.update_last_execution(self._execution_id)
            self.logger.debug(
                f"Started session operation: {self.OPERATION}",
                extra={"operation": self.OPERATION, "execution_id": self._execution_id},
            )
        except Exception as e:
            # Session tracking must never block command execution
            self.logger.warning(
                f"Failed to start session operation '{self.OPERATION}': {e}",
                extra={"operation": self.OPERATION},
            )

    # Load env-file sources from cli.yaml and inject into os.environ
    def _load_env_sources(self) -> None:
        """Inject the active profile's ``envfile`` paths into ``os.environ``.

        Reads ``envfile_paths`` from the currently active profile in
        ``solution.json``, resolves ``@repo_name/…`` references, parses each
        ``.env`` file in declaration order, and injects the variables into the
        current process environment.

        Runs after solution load so ``@repo_name/…`` paths can be resolved.
        Non-fatal — missing files produce debug warnings, never block execution.
        """
        try:
            from strata.utils.system import resolve_path

            profile, _ = self._solution_controller.get_active_profile()
            if profile is None:
                return

            envfile_paths = profile.envfile_paths or []
            if not envfile_paths:
                return

            repo_map: Dict[str, str] = self._solution_controller.get_repo_map()

            from strata.controllers.env_controller import EnvController

            for entry in envfile_paths:
                raw_path = str(entry.path)
                name = str(entry.name)
                try:
                    resolved = resolve_path(str(self._work_path), raw_path, repo_map=repo_map)
                except ValueError as e:
                    self.logger.debug(f"Env source '{name}': {e}")
                    continue

                if not resolved.exists():
                    self.logger.debug(f"Env source '{name}': file not found at {resolved}")
                    continue

                file_vars = EnvController._parse_env_file(resolved)
                for key, value in file_vars.items():
                    os.environ[key] = value
                self.logger.debug(
                    "Loaded env source from active profile",
                    name=name,
                    path=str(resolved),
                    vars_count=len(file_vars),
                )

        except Exception as e:
            # Env loading must never block command execution
            self.logger.debug(f"Failed to load env sources: {e}")

    def _load_config_sources(self) -> None:
        """Load and deep-merge the active profile's ``configfile_paths`` into ``self._merged_config``.

        Files are merged in declaration order (later entries override earlier ones).
        The merged result is also written to ``.strata/configuration.yaml`` as a
        debug artifact — write failures are non-fatal.

        Runs after ``_load_env_sources()`` so env vars are available for any
        variable substitution performed by downstream consumers.
        """
        try:
            from strata.utils.config import get_configuration_path
            from strata.utils.configuration_loader import ConfigurationLoader
            from strata.utils.system import resolve_path

            profile, _ = self._solution_controller.get_active_profile()
            if profile is None:
                return

            configfile_paths = profile.configfile_paths or []
            if not configfile_paths:
                return

            repo_map: Dict[str, str] = self._solution_controller.get_repo_map()

            resolved_paths: List[Path] = []
            for entry in configfile_paths:
                name = str(entry.name)
                try:
                    resolved = resolve_path(str(self._work_path), str(entry.path), repo_map=repo_map)
                except ValueError as e:
                    self.logger.debug(f"Config source '{name}': {e}")
                    continue
                if not resolved.exists():
                    self.logger.debug(f"Config source '{name}': file not found at {resolved}")
                    continue
                resolved_paths.append(resolved)

            if not resolved_paths:
                return

            loader = ConfigurationLoader()
            merged = loader.load_and_merge_yaml_files(resolved_paths)
            self._merged_config = merged
            self.logger.debug(
                "Loaded and merged config sources from active profile",
                files=len(resolved_paths),
                keys=len(merged),
            )

            # Populate the ConfigurationService singleton so any downstream code
            # that calls ConfigurationService.get_instance() gets the merged model
            # for free — no re-read required.
            try:
                from strata.services.configuration_service import ConfigurationService

                if not ConfigurationService.get_instance().data:
                    ConfigurationService.get_instance().add_configurations([Path(p) for p in resolved_paths])
            except Exception as e:
                self.logger.debug(f"ConfigurationService pre-load failed (non-fatal): {e}")

            # Write debug artifact — non-fatal
            try:
                import yaml

                debug_path = get_configuration_path(self._work_path)
                with open(debug_path, "w", encoding="utf-8") as fh:
                    yaml.safe_dump(merged, fh, sort_keys=False)
            except Exception as e:
                self.logger.warning(f"Failed to write merged config debug artifact: {e}")

        except Exception as e:
            # Config loading must never block command execution
            self.logger.debug(f"Failed to load config sources: {e}")

    def _apply_audit_journal_config(self) -> None:
        """Reconfigure the audit journal from ``spec.audit.journal``, if present (ADR-0066).

        This is the second of the two bootstrap phases: the first (in ``_initialize()``,
        before configuration is loaded) opens the journal with built-in defaults so early
        failures still produce a record. This phase runs once ``_load_config_sources()``
        has populated ``ConfigurationService``, and re-opens the journal per
        ``spec.audit.journal`` if one is declared.

        A no-op when ``.strata/logging.yaml``'s ``audit:`` section already configured the
        journal — that is a machine-local override and outranks the committed
        ``spec.audit.journal`` (precedence: ``spec.audit.journal`` < ``logging.yaml`` <
        built-in default). Never raises: a broken or absent audit config here should not
        block the command, it should just leave the bootstrap defaults in place.
        """
        if get_audit_log_source() == "logging_yaml":
            return
        try:
            from strata.services.configuration_service import ConfigurationService

            config_model = ConfigurationService.get_instance().model
            audit_cfg = getattr(getattr(config_model, "spec", None), "audit", None)
            journal = audit_cfg.journal if audit_cfg else None
        except Exception as e:
            self.logger.debug(f"Failed to resolve spec.audit.journal (non-fatal): {e}")
            return
        if journal is None:
            return

        kwargs: Dict[str, Any] = {"source": "spec_audit"}
        if journal.path is not None:
            kwargs["log_path"] = str(resolve_path(str(self._work_path), journal.path))
        else:
            # Preserve the path already in effect from bootstrap phase — otherwise
            # setting only e.g. `rotation` here would silently reset the path to
            # configure_audit_log()'s own default (relative to CWD, not work_path).
            kwargs["log_path"] = get_configured_audit_log_path() or str(get_audit_log_path(self._work_path))
        if journal.rotation is not None:
            kwargs["rotation"] = journal.rotation
        if journal.max_bytes is not None:
            kwargs["max_bytes"] = journal.max_bytes
        if journal.backup_count is not None:
            kwargs["backup_count"] = journal.backup_count
        if journal.date_suffix is not None:
            kwargs["date_suffix"] = journal.date_suffix

        try:
            configure_audit_log(**kwargs)
        except Exception as e:
            self.logger.debug(f"Failed to apply spec.audit.journal (non-fatal): {e}")

    # Validate declared integration requirements (e.g., check if 'git' is available for 'repository clone operations')
    def _validate_requirements(self) -> bool:
        """
        Validate that all required integrations are available.

        Called automatically by _before_execute() before command execution.
        Checks each integration declared in get_required_integrations().

        Returns:
            bool: True if all requirements met, False otherwise (errors stored in self._errors)
        """
        required = self.get_required_integrations()

        if not required:
            # No requirements - validation passes
            return True

        self.logger.debug(
            f"Validating {len(required)} required integration(s)",
            extra={
                "command_class": self.__class__.__name__,
                "integrations": list(required.keys()),
            },
        )

        integration_controller = self.get_integration_controller()

        for integration_name, operation_desc in required.items():
            is_available, error_msg = integration_controller.ensure_integration_available(
                integration_name, operation_desc
            )

            if not is_available:
                self.logger.error(
                    f"Integration requirement not met: {integration_name}",
                    extra={
                        "command_class": self.__class__.__name__,
                        "integration": integration_name,
                        "operation": operation_desc,
                    },
                )
                self._errors.append(error_msg)
                return False

            self.logger.debug(
                f"Integration '{integration_name}' validated successfully",
                extra={
                    "command_class": self.__class__.__name__,
                    "integration": integration_name,
                },
            )

        self.logger.debug(
            "All integration requirements validated",
            extra={"command_class": self.__class__.__name__},
        )
        return True
