"""Base class for deploy commands."""

import json
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import click

from strata.commands.base_command import BaseCommand
from strata.controllers.actor_controller import resolve_actor
from strata.integrations.lock.base_lock_backend import (
    BaseLockBackend,
    LockBackendError,
    LockConflictError,
    LockHandle,
)
from strata.models.change_reference_model import ChangeReferenceModel
from strata.models.common_models import ProvisionerType
from strata.models.deployment_manifest_model import (
    DeploymentManifestMetaModel,
    DeploymentManifestModel,
    DeploymentManifestSpecModel,
    ManifestArtifactImageModel,
    ManifestArtifactProviderModel,
    ManifestArtifactsModel,
    ManifestLockReferenceModel,
    ManifestOutputsReferenceModel,
    ManifestPlatformModel,
    ManifestPolicyResultModel,
    ManifestRepositoryModel,
    ManifestStageModel,
)
from strata.models.deployment_model import DeploymentStageModel
from strata.services.configuration_service import ConfigurationService
from strata.services.deployment_manifest_service import DeploymentManifestService
from strata.services.deployment_service import DeploymentService
from strata.services.manifest_artifact_collector import (
    collect_platform_artifact,
    collect_provider_info,
    collect_repository_info,
)
from strata.utils.duration import parse_duration


class BaseDeployCommand(BaseCommand):
    """Base class for deploy command implementations."""

    OPERATION = "deploy"

    def __init__(
        self,
        file: Optional[str] = None,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
        no_cache: bool = False,
        refresh_cache: bool = False,
        change_id: Optional[str] = None,
        change_system: Optional[str] = None,
        change_title: Optional[str] = None,
        change_url: Optional[str] = None,
        change_classification: Optional[str] = None,
        change_reason: Optional[str] = None,
    ):
        super().__init__(
            work_path=work_path,
            output=output,
            verbose=verbose,
            quiet=quiet,
        )
        self._raw_file: Optional[str] = file
        self._file_path: Optional[Path] = Path(file) if file else None
        self._deployment_service: Optional[DeploymentService] = None
        self._configuration_service: Optional[ConfigurationService] = None
        self._build_path: Path = self._work_path / "build"
        self._deploy_started_at: Optional[str] = None
        self._stage_results: List[ManifestStageModel] = []
        self._policy_results: List[ManifestPolicyResultModel] = []
        self._lock_ref: Optional[ManifestLockReferenceModel] = None
        self._audit_log_path: Optional[str] = None
        self._lock_conflict: bool = False
        # True when the deployment file loaded but failed schema/cross-reference
        # validation (exit 3) — as opposed to a usage error (missing/unresolvable
        # file, exit 2, raised as click.UsageError) or a system/execution error
        # (exit 1, the default for any other failure). See ADR-0004.
        self._validation_failed: bool = False
        # Subclasses override these before calling _execute_provisioning.
        self._dry_run: bool = False
        self._force_lock: bool = False
        self._force: bool = False
        # ADR-0026 resolved-environment cache (only consumed by commands that
        # override _load_related_services to use _load_environment_related_services,
        # e.g. ShowDeployCommand, values list/get/resolve — unused elsewhere).
        self._no_cache: bool = no_cache
        self._refresh_cache: bool = refresh_cache
        self._environment_snapshot: Optional[Dict[str, Any]] = None
        self._cache_indicator: str = "no-cache"
        # ADR-0074 Phase 1 — external change/ticket reference, capture only.
        # Raw CLI/env input; resolved into self._change_reference by
        # _resolve_change_reference() once configuration is loaded.
        self._change_id: Optional[str] = change_id
        self._change_system: Optional[str] = change_system
        self._change_title: Optional[str] = change_title
        self._change_url: Optional[str] = change_url
        self._change_classification: Optional[str] = change_classification
        self._change_reason: Optional[str] = change_reason
        self._change_reference: Optional[ChangeReferenceModel] = None

    def get_required_integrations(self):
        return {}

    def has_validation_errors(self) -> bool:
        """True when the deployment file failed schema/cross-reference validation.

        Checked by ``handle_command_exit`` to emit exit code 3 instead of 1.
        Only set by the validation steps in ``_before_execute`` — execution-phase
        failures (provisioning, lifecycle hooks, etc.) never set this flag and
        correctly fall through to the generic exit code 1.
        """
        return self._validation_failed

    def has_lock_conflict(self) -> bool:
        """True when the last execute() failed due to a deployment lock conflict.

        Checked by ``handle_command_exit`` to emit exit code 4 instead of 1.
        Only reachable for ``deploy run`` and ``deploy destroy`` — ``LockConflictError``
        is only raised by the lock-acquisition path in these commands.
        """
        return self._lock_conflict

    # -------------------------------------------------------------------------
    # Lock helpers — shared by RunDeployCommand and DestroyDeployCommand
    # -------------------------------------------------------------------------

    def _should_lock(self) -> bool:
        """Return True if locking is enabled and this is not a dry-run.

        Returns False immediately for dry-runs and for the ``delegate``
        strategy (which trusts the backend's native locking, e.g. TFC run queue).
        """
        if self._dry_run:
            return False
        if self._deployment_service is None:
            return False
        spec = self._deployment_service.model.spec  # type: ignore[union-attr]
        locking = getattr(spec, "locking", None)
        if locking is None or not locking.enabled:
            return False
        if locking.strategy == "delegate":
            return False
        return True

    def _resolve_lock_backend(self, stages: List[DeploymentStageModel]) -> BaseLockBackend:
        """Return the lock backend from the first Terraform provisioner with a backend.

        Falls back to ``LocalLockBackend`` when no matching provisioner is found.
        """
        from strata.integrations.lock.lock_factory import LockFactory

        if self._deployment_service is not None:
            workspace_service = self._deployment_service.get_workspace_service()
            if workspace_service is not None:
                spec = workspace_service.model.spec  # type: ignore[union-attr]
                provisioners = spec.provisioners or []
                for stage in stages:
                    if stage.provisioner:
                        iac = next(
                            (p for p in provisioners if p.name == stage.provisioner),
                            None,
                        )
                        if iac and iac.provisioner == ProvisionerType.TERRAFORM and iac.backend:
                            return LockFactory.create(iac.backend, self._work_path)

        return LockFactory.create(None, self._work_path)

    def _acquire_lock(self, backend: BaseLockBackend) -> Optional[LockHandle]:
        """Acquire the deployment lock. Returns the handle or ``None`` on failure.

        When ``self._force_lock`` is True and a lock is already held, the held
        lock is force-released before acquiring.  A warning is printed so the
        audit trail is clear about the override.
        """
        if self._deployment_service is None:
            return None
        deploy_name = str(self._deployment_service.model.meta.name)  # type: ignore[union-attr]
        spec = self._deployment_service.model.spec  # type: ignore[union-attr]
        locking = getattr(spec, "locking", None)
        wait_timeout: str = getattr(locking, "wait_timeout", "30m") if locking else "30m"
        timeout_seconds = parse_duration(wait_timeout)
        holder = resolve_actor()

        # Force-release a held lock when --force-lock is set.
        if self._force_lock:
            try:
                existing = backend.status(deploy_name)
                if existing is not None:
                    if self._is_console_output():
                        click.echo(
                            f"\n⚠️   --force-lock: releasing lock held by '{existing.holder}' "
                            f"on {existing.hostname} (lock_id: {existing.lock_id})"
                        )
                    backend.force_release(deploy_name)
                    self.logger.warning(
                        "deploy_lock_force_released_before_acquire",
                        deployment=deploy_name,
                        previous_holder=existing.holder,
                    )
            except LockBackendError as exc:
                self._errors.append(f"Force-lock release failed: {exc}")
                return None

        reason = f"strata {self.OPERATION.replace('_', ' ')}"
        try:
            handle = backend.acquire(
                deployment_name=deploy_name,
                holder=holder,
                reason=reason,
                timeout_seconds=timeout_seconds,
            )
            self._lock_ref = ManifestLockReferenceModel(
                lock_id=handle.lock_id,
                backend=handle.backend_type,
                acquired_at=handle.acquired_at,
                holder=holder,
                hostname=socket.gethostname(),
            )
            if self._is_console_output():
                click.echo(f"\n\U0001f512  Lock acquired ({handle.backend_type}) for '{deploy_name}'")
            self.logger.info(
                "deploy_lock_acquired",
                lock_id=handle.lock_id,
                backend=handle.backend_type,
            )
            self._forward_lock_audit_event("lock.acquired", handle, deploy_name)
            return handle
        except LockConflictError as exc:
            self._lock_conflict = True
            self._errors.append(str(exc))
            if self._is_console_output():
                holder = getattr(exc, "holder", "unknown")
                click.echo(
                    f"\n\U0001f512  Could not acquire lock \u2014 held by {holder!r}. "
                    "Run `strata deploy lock status` for details, or use --force-lock to override."
                )
            self.logger.error(
                "deploy_lock_conflict",
                deployment=deploy_name,
                error=str(exc),
            )
            return None
        except LockBackendError as exc:
            self._errors.append(f"Lock backend error: {exc}")
            self.logger.error("deploy_lock_backend_error", error=str(exc))
            return None

    def _release_lock(self, backend: BaseLockBackend, handle: LockHandle) -> None:
        """Release the deployment lock. Safe to call in a ``finally`` block."""
        released_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            backend.release(handle)
            if self._lock_ref is not None:
                self._lock_ref = self._lock_ref.model_copy(update={"released_at": released_at})
            if self._is_console_output():
                click.echo(f"\n\U0001f513  Lock released ({handle.backend_type})")
            self.logger.info("deploy_lock_released", lock_id=handle.lock_id)
            deploy_name = (
                str(self._deployment_service.model.meta.name)  # type: ignore[union-attr]
                if self._deployment_service is not None
                else None
            )
            self._forward_lock_audit_event("lock.released", handle, deploy_name)
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(
                "deploy_lock_release_failed",
                lock_id=handle.lock_id,
                error=str(exc),
            )

    def _forward_lock_audit_event(self, event_type: str, handle: LockHandle, deploy_name: Optional[str]) -> None:
        """Forward a lock.acquired / lock.released event (ADR-0066 follow-up).

        Best-effort: wrapped so a forwarding failure never affects the deploy/destroy
        exit code, matching ``forward()``'s own "audit must never fail a deploy"
        guarantee. Both event types default to disabled (not every lock/unlock is
        interesting), so this is a no-op unless explicitly enabled via
        ``spec.audit.policy.events``.
        """
        try:
            from strata.controllers.audit_controller import AuditController
            from strata.services.configuration_service import ConfigurationService

            audit_cfg = None
            try:
                config_model = ConfigurationService.get_instance().model
                audit_cfg = getattr(getattr(config_model, "spec", None), "audit", None)
            except Exception as e:
                self.logger.debug(f"Failed to resolve spec.audit for {event_type} (non-fatal): {e}")

            payload = {
                "execution_id": self._execution_id,
                "deployment": deploy_name,
                "lock_id": handle.lock_id,
                "backend": handle.backend_type,
            }
            AuditController(work_path=self._work_path).forward(event_type, payload, audit_config=audit_cfg)
        except Exception as e:
            self.logger.debug(f"Failed to forward {event_type} audit event (non-fatal): {e}")

    # -------------------------------------------------------------------------
    # Hierarchical lifecycle helper
    # -------------------------------------------------------------------------

    def _run_hierarchy_lifecycle_phase(self, phase_name: str, context: Optional[dict] = None) -> bool:
        """Execute a lifecycle phase across the full service hierarchy.

        Traversal order:
          configuration → workspace → namespaces → providers → resources → modules

        Each level runs its own scripts for *phase_name*.  A missing phase at any
        level is silently skipped.  A non-zero script exit at any level aborts the
        remaining levels and returns False.

        Additional ``STRATA_*`` context variables are injected per level:
          - namespace level  → ``STRATA_NAMESPACE``
          - provider level   → ``STRATA_PROVIDER``
          - resource level   → ``STRATA_RESOURCE``
          - module level     → ``STRATA_MODULE``
        """
        from strata.controllers.lifecycle_controller import LifecycleController

        lc = LifecycleController()

        # 1 — configuration level
        if not lc.execute_configuration_phase(phase_name=phase_name, work_path=self._work_path, context=context):
            for err in lc.get_errors():
                self._errors.append(f"Lifecycle hook '{phase_name}' (config) failed: {err}")
            return False

        if self._deployment_service is None:
            return True

        # 2 — workspace level
        workspace = self._deployment_service.get_workspace_service()
        if workspace is not None:
            lc.clear_errors()
            lc.clear_messages()
            if not lc.execute_workspace_phase(
                base_service=workspace, phase_name=phase_name, work_path=self._work_path, context=context
            ):
                for err in lc.get_errors():
                    self._errors.append(f"Lifecycle hook '{phase_name}' (workspace) failed: {err}")
                return False

        # 3 — namespace level
        for ns_name, ns_service in (self._deployment_service.get_namespace_services() or {}).items():
            lc.clear_errors()
            lc.clear_messages()
            ns_ctx = {**(context or {}), "namespace": str(ns_name)}
            if not lc.execute_workspace_phase(
                base_service=ns_service, phase_name=phase_name, work_path=self._work_path, context=ns_ctx
            ):
                for err in lc.get_errors():
                    self._errors.append(f"Lifecycle hook '{phase_name}' (namespace:{ns_name}) failed: {err}")
                return False

        # 4 — provider level
        for prov_name, prov_service in (self._deployment_service.get_provider_services() or {}).items():
            lc.clear_errors()
            lc.clear_messages()
            prov_ctx = {**(context or {}), "provider": str(prov_name)}
            if not lc.execute_workspace_phase(
                base_service=prov_service, phase_name=phase_name, work_path=self._work_path, context=prov_ctx
            ):
                for err in lc.get_errors():
                    self._errors.append(f"Lifecycle hook '{phase_name}' (provider:{prov_name}) failed: {err}")
                return False

        # 5 — resource level
        for res_name, res_service in (self._deployment_service.get_resource_services() or {}).items():
            lc.clear_errors()
            lc.clear_messages()
            res_ctx = {**(context or {}), "resource": str(res_name)}
            if not lc.execute_workspace_phase(
                base_service=res_service, phase_name=phase_name, work_path=self._work_path, context=res_ctx
            ):
                for err in lc.get_errors():
                    self._errors.append(f"Lifecycle hook '{phase_name}' (resource:{res_name}) failed: {err}")
                return False

        # 6 — module level
        for mod_key, mod_service in (self._deployment_service.get_module_services() or {}).items():
            lc.clear_errors()
            lc.clear_messages()
            mod_ctx = {**(context or {}), "module": str(mod_key)}
            if not lc.execute_workspace_phase(
                base_service=mod_service, phase_name=phase_name, work_path=self._work_path, context=mod_ctx
            ):
                for err in lc.get_errors():
                    self._errors.append(f"Lifecycle hook '{phase_name}' (module:{mod_key}) failed: {err}")
                return False

        return True

    # -------------------------------------------------------------------------
    # Deployment lifecycle
    # -------------------------------------------------------------------------

    def _before_execute(self) -> bool:
        """Load and validate the deployment file + configuration service."""
        if not super()._before_execute():
            return False

        # Load user provisioner plugins from .strata/provisioners/
        from strata.deployers.factory import DeployerFactory

        DeployerFactory.load_plugins(self._work_path)

        if not self._file_path:
            raise click.UsageError("No deployment file specified. Use --file.")

        # Resolve to absolute path, supporting @repo-name/... references
        from strata.utils.system import resolve_path

        repo_map: dict[str, str] = (
            self._solution_controller.get_repo_map() if self._solution_controller is not None else {}
        )

        try:
            candidate = resolve_path(str(self._work_path), self._raw_file, repo_map=repo_map)
        except ValueError as e:
            raise click.UsageError(f"Deployment file reference error: {e}") from e

        if not candidate.exists():
            raise click.UsageError(f"Deployment file not found: {candidate}")
        self._file_path = candidate
        self.logger.info("Using deployment file", file=str(self._file_path))

        # Load configuration service (required for cross-validation)
        self._configuration_service = self._load_configuration_service()
        if self._configuration_service is None:
            return False
        self._build_path = self._get_build_path()

        # ADR-0074 Phase 1 — resolve --change-id/--change-system/--change-title/
        # --change-url/--reason (plus configuration.spec.change_tracking defaults)
        # into self._change_reference. A usage-level problem (missing --reason,
        # missing --change-id, id not matching id_pattern, ...) is reported as
        # exit code 2, same as any other bad-argument case — it doesn't depend
        # on the deployment file at all.
        change_reference_error = self._resolve_change_reference()
        if change_reference_error:
            raise click.UsageError(change_reference_error)

        # Phase 1: load + Pydantic-validate the deployment file
        # ADR 0039: resolve spec.extends before loading into DeploymentService.
        from strata.services.deployment_extension_resolver import DeploymentExtensionResolver

        resolver = DeploymentExtensionResolver(work_path=Path(self._work_path), repo_map=repo_map)
        if resolver.needs_resolution(self._file_path):
            try:
                merged_data = resolver.resolve(self._file_path)
            except (ValueError, FileNotFoundError) as exc:
                self._errors.append(f"Deployment extends resolution failed: {exc}")
                self._validation_failed = True
                return False
            deployment_service = DeploymentService(path=str(self._file_path), data=merged_data)
            deployment_service.validate()
        else:
            deployment_service = DeploymentService.load(str(self._file_path), validate=True)

        if not deployment_service.is_validated():
            self._errors.extend(deployment_service.get_validation_errors())
            self._validation_failed = True
            return False

        # Pre-flight: reject partial deployments before any infrastructure operation.
        if deployment_service.model and deployment_service.model.spec.partial:
            self._errors.append(
                f"'{self._file_path.name}' is a partial deployment (spec.partial: true) "
                "and cannot be deployed. A leaf deployment file that extends this base is required."
            )
            self._validation_failed = True
            return False

        # Phase 2: cross-validate against configuration
        config_model = self._configuration_service.model if self._configuration_service else None
        ok, errors = deployment_service.validate(
            configuration_model=config_model,
            work_path=str(self._work_path),
            repo_map=repo_map,
        )
        if not ok:
            self._errors.extend(errors)
            self._validation_failed = True
            return False

        # Load related services (workspace, environment, providers, resources, ...)
        # Overridable hook: environment-only commands (deploy show, values
        # list/get/resolve) skip the workspace load entirely — see
        # _load_environment_related_services below.
        if not self._load_related_services(deployment_service, repo_map):
            return False

        self._deployment_service = deployment_service

        self.logger.debug(
            "Deployment loaded",
            file=str(self._file_path),
            build_path=str(self._build_path),
        )
        return True

    def _load_related_services(self, deployment_service: DeploymentService, repo_map: Dict[str, str]) -> bool:
        """Load workspace + environment services and apply overrides.

        Default hook used by commands that need full infrastructure resolution
        (``deploy run``/``destroy``/``plan``/etc.). Override for lighter-weight,
        environment-only commands that never touch workspace resources, providers,
        or modules — see :meth:`_load_environment_related_services`.
        """
        if not deployment_service.load_deploy_services(str(self._work_path), repo_map=repo_map):
            self._errors.extend(deployment_service.get_validation_errors())
            self._validation_failed = True
            return False

        ok, errors = deployment_service.validate_related_services()
        if not ok:
            self._errors.extend(errors)
            self._validation_failed = True
            return False

        ok, errors = deployment_service.apply_environment_overrides()
        if not ok:
            critical = [e for e in errors if "skipped" not in e.lower()]
            if critical:
                self._errors.extend(critical)
                self._validation_failed = True
                return False
            self._messages.extend(errors)  # non-critical warnings

        return True

    def _load_environment_related_services(
        self,
        deployment_service: DeploymentService,
        repo_map: Dict[str, str],
        apply_remote_overrides: bool = False,
    ) -> bool:
        """Load only the merged environment (ADR-0026 ``resolved_environment`` cache) —
        never the workspace.

        Used by commands that only need declared variables/secrets/features and merge
        provenance (``deploy show``, ``values list/get/resolve``). Skips workspace,
        provider, resource, and module resolution entirely, which is the expensive
        part of the default :meth:`_load_related_services` for a fleet of deployments
        that share the same workspace file.

        With *apply_remote_overrides*, also applies the environment's declared remote
        reference overrides to the active ``ConfigurationService`` (needed by ``deploy
        show`` to display effective remote references) — this only depends on the
        environment service populated here, not on the workspace.

        Populates ``self._environment_snapshot`` (the cache payload, for callers that
        want to display cache provenance) and ``self._cache_indicator`` (``"cached"``
        / ``"refreshed"`` / ``"no-cache"``).
        """
        from strata.controllers.cache_controller import CacheController

        controller = CacheController(self._work_path)
        ok, snapshot, indicator = controller.get_or_resolve_environment(
            deployment_service,
            repo_map,
            no_cache=self._no_cache,
            refresh_cache=self._refresh_cache,
        )
        if not ok or snapshot is None:
            self._errors.extend(controller.get_errors())
            self._validation_failed = True
            return False

        self._environment_snapshot = snapshot
        self._cache_indicator = indicator
        CacheController.apply_environment_snapshot(deployment_service, snapshot)

        if apply_remote_overrides:
            _, errors = deployment_service.apply_remote_overrides()
            if errors:
                self._messages.extend(errors)

        return True

    def _resolve_values(self, strict: bool = False):
        """Resolve variables/secrets/features for ``self._deployment_service``.

        Uses the ADR-0026 OQ-4 ``resolved_values`` cache for variables and
        features (never secrets — always resolved live) when this command was
        loaded via :meth:`_load_environment_related_services` (signalled by
        ``self._environment_snapshot`` being set). Commands that load the full
        workspace (``deploy run``/``destroy``/etc., where
        ``_environment_snapshot`` is never populated) get a plain live
        ``ValueController.resolve_values()`` call, completely unaffected by the
        value cache.

        Same ``--no-cache``/``--refresh-cache`` flags as every other ADR-0026
        cache consumer — one cache, one set of semantics, for the user.

        Returns ``(success, resolved, errors)`` — same shape as
        ``ValueController.resolve_values()``. Also updates
        ``self._cache_indicator`` when the cache-aware path is used.
        """
        from strata.controllers.cache_controller import CacheController
        from strata.controllers.value_controller import ValueController
        from strata.utils.resolved_values import ResolvedValues

        if self._deployment_service is None:
            return True, ResolvedValues(), []

        controller = ValueController()

        if self._environment_snapshot is None:
            return controller.resolve_values(self._deployment_service, strict=strict)

        cache_controller = CacheController(self._work_path)
        name = self._deployment_service.get_name() or str(self._file_path)
        input_paths = cache_controller._collect_environment_input_paths(self._deployment_service)
        ok, resolved, errors, indicator = controller.resolve_values_via_cache(
            self._deployment_service,
            cache_controller.cache,
            name,
            input_paths,
            no_cache=self._no_cache,
            refresh_cache=self._refresh_cache,
            strict=strict,
        )
        self._cache_indicator = indicator
        return ok, resolved, errors

    def _create_deployer(self, stage: DeploymentStageModel):
        """Instantiate the deployer for *stage*, or None on failure.

        Resolution order (mutually exclusive):
        - stage.provisioner → named provisioner entry in the workspace YAML
        - stage.topology    → topology name → inferred provisioner type

        Errors are appended to ``self._errors`` and None is returned on failure.
        Subclass attributes ``_force`` and ``_resolved_values`` are consumed when
        present; commands that do not declare them receive safe defaults.
        """
        from strata.deployers.factory import DeployerFactory

        if self._deployment_service is None:
            self._errors.append(f"Stage '{stage.name}': deployment service not loaded.")
            return None

        resolved_type, errors = DeployerFactory.resolve_type(stage, self._deployment_service)
        if errors:
            self._errors.extend(errors)
        if resolved_type is None:
            return None

        _resolved_values = getattr(self, "_resolved_values", None)
        _stage_values = _resolved_values.for_stage(stage.secrets) if _resolved_values else None

        try:
            deployer = DeployerFactory.create(
                resolved_type,
                stage=stage,
                deployment_service=self._deployment_service,  # type: ignore[arg-type]
                configuration_service=self._configuration_service,  # type: ignore[arg-type]
                build_path=self._build_path,
                work_path=self._work_path,
                verbose=self._is_verbose(),
                force=getattr(self, "_force", False),
                resolved_values=_stage_values,
                solution_controller=self._solution_controller,
            )
        except ValueError as exc:
            self._errors.append(str(exc))
            return None

        # Effective namespace allowlist: CLI --namespace (only RunDeployCommand
        # sets self._namespaces) takes precedence over the stage's declarative
        # helm_namespaces allowlist. None on both means no filtering. Only
        # HelmDeployer consumes this attribute (see BaseDeployer.namespace_filter).
        effective_namespaces = getattr(self, "_namespaces", None) or stage.helm_namespaces
        if effective_namespaces:
            deployer.namespace_filter = list(effective_namespaces)

        return deployer

    def _load_configuration_service(self) -> Optional[ConfigurationService]:
        """Load ConfigurationService from the active profile's configfile_paths."""
        from strata.utils.system import resolve_path

        if self._solution_controller.solution is None:
            self._errors.append("Deploy requires an initialized workspace. Run `strata sln init` first.")
            return None

        profile, _ = self._solution_controller.get_active_profile()
        if profile is None:
            self._errors.append("Deploy requires an active profile. Run `strata profile activate <name>`.")
            return None

        configfile_paths = profile.configfile_paths or []
        if not configfile_paths:
            self._errors.append(
                "Deploy requires at least one configfile path on the active profile. "
                "Add one with `strata ref configfile add`."
            )
            return None

        repo_map = self._solution_controller.get_repo_map()

        resolved_paths = []
        for entry in configfile_paths:
            try:
                resolved = resolve_path(str(self._work_path), str(entry.path), repo_map=repo_map)
            except ValueError as exc:
                self.logger.debug("Config source skipped", name=str(entry.name), reason=str(exc))
                continue
            if not resolved.exists():
                self.logger.debug("Config source not found", name=str(entry.name), path=str(resolved))
                continue
            resolved_paths.append(str(resolved))

        if not resolved_paths:
            self._errors.append("No configfile_paths resolved to existing files. Check your profile refs.")
            return None

        try:
            ConfigurationService.reset()
            config_svc = ConfigurationService.get_instance()
            success, load_errors = config_svc.load_from_paths(resolved_paths)
            if not success:
                self._errors.append(f"Failed to load configuration: {'; '.join(load_errors)}")
                return None

            self.logger.debug(
                "ConfigurationService loaded",
                profile=str(profile.name),
                files=len(resolved_paths),
            )
            return config_svc
        except Exception as exc:
            self._errors.append(f"Unexpected error loading configuration: {exc}")
            return None

    def _after_execute(self) -> bool:
        return super()._after_execute()

    def _finalize(self, success: bool = False, show_footer: bool = True) -> bool:
        return super()._finalize(success=success, show_footer=show_footer)

    # ------------------------------------------------------------------
    # Change reference (ADR-0074 Phase 1 — capture and record only)
    # ------------------------------------------------------------------

    def _resolve_change_reference(self) -> Optional[str]:
        """Build ``self._change_reference`` from ``--change-*``/``--reason`` input.

        Combines CLI/env-supplied values with ``configuration.spec.change_tracking``
        defaults (``system``, ``url_template``, ``id_pattern``). Populates
        ``self._change_reference`` on success.

        This is a usage-level check, not deployment-file validation — it runs
        before the deployment file is loaded and never sets
        ``self._validation_failed``. Nothing is required by default: when no
        ``--change-*``/``--reason`` flag is supplied, this is a no-op. Phase 1 does
        not enforce that a reference is supplied at all — see ADR-0074.

        Returns:
            An error message if the supplied input is invalid, else ``None``.
        """
        if not any(
            (
                self._change_id,
                self._change_system,
                self._change_title,
                self._change_url,
                self._change_classification,
                self._change_reason,
            )
        ):
            return None

        if not self._change_id:
            return (
                "--change-id is required when --change-system/--change-title/--change-url/"
                "--change-classification/--reason is supplied."
            )

        if not self._change_reason:
            return "--reason is required when --change-id is supplied."

        change_tracking = None
        if self._configuration_service is not None and self._configuration_service.model is not None:
            change_tracking = getattr(self._configuration_service.model.spec, "change_tracking", None)

        system = self._change_system or (change_tracking.system if change_tracking else None)
        if not system:
            return (
                "--change-system is required when --change-id is supplied and no default is configured "
                "under configuration spec.change_tracking.system."
            )

        if change_tracking and change_tracking.id_pattern:
            import re

            if not re.match(change_tracking.id_pattern, self._change_id):
                return (
                    f"--change-id '{self._change_id}' does not match the configured "
                    f"change_tracking.id_pattern '{change_tracking.id_pattern}'."
                )

        if self._change_classification and change_tracking and change_tracking.classifications:
            if self._change_classification not in change_tracking.classifications:
                allowed = ", ".join(change_tracking.classifications)
                return (
                    f"--change-classification '{self._change_classification}' is not one of the configured "
                    f"change_tracking.classifications: {allowed}."
                )

        url = self._change_url
        if not url and change_tracking and change_tracking.url_template:
            url = change_tracking.url_template.replace("{id}", self._change_id)

        self._change_reference = ChangeReferenceModel(
            system=system,
            id=self._change_id,
            reason=self._change_reason,
            classification=self._change_classification,
            title=self._change_title,
            url=url,
            supplied_by=resolve_actor(),
            supplied_at=datetime.now(timezone.utc).isoformat(),
        )
        return None

    def _evaluate_preflight_policies(self, phase: str) -> bool:
        """Evaluate one-shot, deployment-level policies for *phase* (ADR-0074 Phase 2).

        Shared by ``RunDeployCommand`` (``phase="deploy_before"``) and
        ``DestroyDeployCommand`` (``phase="destroy_before"``) — one implementation,
        not a copy per command, per the "Introducing a new convention" rule. Each
        command calls this once, before any stage executes, guarded by
        ``if not self._dry_run`` at the call site (nothing is changed on a dry-run,
        so nothing to require a reference/authorization for).

        Unlike ``RunDeployCommand._evaluate_phase_policies()`` (used for the
        stage-scoped ``plan``/``deploy`` phases), no stage/deployer/plan context is
        available or needed here — e.g. ``change_reference_required`` only reads
        ``PolicyContext.change_reference``. Evaluating a deployment-level check
        per-stage instead of once would duplicate the same violation once per stage
        on a multi-stage deployment, which is exactly what this method avoids.

        Returns:
            ``False`` if any deny-enforcement policy fails, else ``True`` (including
            when no configuration service is loaded, or no policies target *phase*).
        """
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

        context = PolicyContext(
            phase=phase,
            work_path=self._work_path,
            deployment_service=self._deployment_service,
            configuration_service=self._configuration_service,
            change_reference=self._change_reference,
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

    # ------------------------------------------------------------------
    # Deployment manifest helpers
    # ------------------------------------------------------------------

    def _record_deploy_start(self) -> None:
        """Record the start time of the deploy operation."""
        self._deploy_started_at = datetime.now(timezone.utc).isoformat()

    def _record_stage_result(
        self,
        stage_name: str,
        provisioner: Optional[str],
        topology: Optional[str],
        status: str,
        started_at: Optional[str],
        completed_at: Optional[str],
        steps: Optional[List[str]] = None,
        outputs: Optional[Dict[str, Any]] = None,
        outputs_artifact: Optional[ManifestOutputsReferenceModel] = None,
        error: Optional[str] = None,
    ) -> None:
        """Append a stage result for the deployment manifest."""
        duration: Optional[int] = None
        if started_at and completed_at:
            try:
                t0 = datetime.fromisoformat(started_at)
                t1 = datetime.fromisoformat(completed_at)
                duration = int((t1 - t0).total_seconds())
            except (ValueError, TypeError):
                pass

        self._stage_results.append(
            ManifestStageModel(
                name=stage_name,
                provisioner=provisioner,
                topology=topology,
                status=status,
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=duration,
                steps=steps,
                outputs=outputs,
                outputs_artifact=outputs_artifact,
                error=error,
            )
        )

    def _write_deployment_manifest(
        self,
        action: str,
        status: str,
        dry_run: bool = False,
    ) -> Optional[Path]:
        """Assemble and persist a deployment manifest.

        Uses the manifest configuration from ``spec.deployment.manifest`` in
        the platform configuration.  When no manifest config is defined, logs
        an info message and skips writing.

        Args:
            action: ``"deploy"`` or ``"destroy"``.
            status: ``"success"``, ``"partial"``, or ``"failed"``.
            dry_run: Whether this was a dry-run (skips writing).

        Returns:
            Path to the written manifest, or None on skip/error.
        """
        from strata.controllers.audit_controller import AuditController

        if dry_run:
            self.logger.debug("Dry-run — skipping deployment manifest write")
            return None

        if self._deployment_service is None:
            self.logger.warning("Cannot write manifest — deployment service not loaded")
            return None

        # Check manifest configuration
        manifest_config = self._get_manifest_config()
        if manifest_config is None:
            self.logger.info(
                "Deployment manifest not configured — skipping. "
                "Define spec.deployment.manifest in the configuration to enable manifest storage."
            )
            return None

        try:
            completed_at = datetime.now(timezone.utc).isoformat()
            started_at = self._deploy_started_at or completed_at

            duration: Optional[int] = None
            try:
                t0 = datetime.fromisoformat(started_at)
                t1 = datetime.fromisoformat(completed_at)
                duration = int((t1 - t0).total_seconds())
            except (ValueError, TypeError):
                pass

            # Deployment identity
            deploy_meta = self._deployment_service.model.meta  # type: ignore[union-attr]
            workspace_service = self._deployment_service.get_workspace_service()
            workspace_name = (
                str(workspace_service.model.meta.name) if workspace_service and workspace_service.model else "unknown"
            )

            # Environment and version from deployment labels
            labels = deploy_meta.labels or {}
            environment = labels.get("environment")

            # Version from deployment labels
            version = labels.get("version")

            # Actor
            deployed_by = resolve_actor()

            # Full artifact BOM
            artifacts = self._collect_artifacts()

            manifest = DeploymentManifestModel(
                meta=DeploymentManifestMetaModel(
                    name=deploy_meta.name,
                    annotations=deploy_meta.annotations,
                    labels=deploy_meta.labels,
                    tags=deploy_meta.tags,
                ),
                spec=DeploymentManifestSpecModel(
                    deployment_name=deploy_meta.name,
                    workspace_name=workspace_name,
                    environment=environment,
                    action=action,
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_seconds=duration,
                    status=status,
                    dry_run=dry_run,
                    deployed_by=deployed_by,
                    artifacts=artifacts,
                    stages=self._stage_results if self._stage_results else None,
                    policy_results=self._policy_results if self._policy_results else None,
                    lock=self._lock_ref,
                    audit_log=self._audit_log_path,
                    change_reference=self._change_reference,
                ),
            )

            svc = DeploymentManifestService()
            path = svc.save_with_config(
                manifest=manifest,
                manifest_config=manifest_config,
                work_path=self._work_path,
                version=version,
            )
            self.logger.info("Deployment manifest written", path=str(path))

            repo_cfg = manifest_config.repository
            should_push = manifest_config.push_manifest or (repo_cfg is not None and repo_cfg.push)
            if should_push and path:
                manifest_base = Path(manifest_config.path)
                if not manifest_base.is_absolute():
                    manifest_base = self._work_path / manifest_base

                audit_ctrl = AuditController(work_path=self._work_path)
                pushed = audit_ctrl.push_to_remote(
                    [path],
                    local_base=manifest_base,
                    remote_path=(repo_cfg.path if repo_cfg else None) or "manifest",
                    repo_name=repo_cfg.name if repo_cfg else None,
                    workspace=workspace_name,
                    commit_message="chore(manifest): deployment manifest update [skip ci]",
                )
                if pushed:
                    self.logger.info("Deployment manifest pushed to remote", path=str(path))
                else:
                    self.logger.warning("Deployment manifest push failed — continuing", path=str(path))

            self._forward_manifest_recorded_event(manifest)

            return path

        except Exception as exc:
            self.logger.warning("Failed to write deployment manifest", error=str(exc))
            return None

    def _forward_manifest_recorded_event(self, manifest: DeploymentManifestModel) -> None:
        """Forward a manifest.recorded event for this deploy/destroy (ADR-0065 Phase 2 producer).

        Unconditional — fires once per manifest write, independent of whether git-push
        (``manifest_config.push_manifest``/``repository.push``) is configured; the two
        delivery mechanisms are orthogonal, same as cost/drift's own recorded events.
        Uses this command's own ``self._execution_id`` (not a derived hash): a manifest
        is written exactly once per command run, so it's the same correlation key
        ``deployment.completed``/``deployment.destroyed`` already use for events from
        the same run — a resend of the persisted record keeps that same id.

        Best-effort — never raises, never affects the deployment/destroy exit code.
        """
        try:
            from strata.controllers.audit_controller import AuditController
            from strata.services.configuration_service import ConfigurationService

            audit_cfg = None
            try:
                config_model = ConfigurationService.get_instance().model
                audit_cfg = getattr(getattr(config_model, "spec", None), "audit", None)
            except Exception as e:
                self.logger.debug(f"Failed to resolve spec.audit for manifest.recorded (non-fatal): {e}")

            payload = manifest.model_dump(mode="json")
            payload["execution_id"] = self._execution_id
            payload["deployment"] = manifest.spec.deployment_name
            payload["workspace"] = manifest.spec.workspace_name
            payload["environment"] = manifest.spec.environment

            AuditController(work_path=self._work_path).forward("manifest.recorded", payload, audit_config=audit_cfg)
        except Exception as e:
            self.logger.debug(f"Failed to forward manifest.recorded audit event (non-fatal): {e}")

    def _get_manifest_config(self):
        """Retrieve manifest configuration from the configuration service.

        Returns:
            ConfigurationManifestModel or None if not configured.
        """
        if self._configuration_service is None:
            return None
        model = self._configuration_service.model
        if model is None:
            return None
        if model.spec.deployment is None:
            return None
        return model.spec.deployment.manifest

    def _get_outputs_config(self):
        """Retrieve outputs configuration from the configuration service.

        Returns:
            ConfigurationOutputsModel or None if not configured.
        """
        if self._configuration_service is None:
            return None
        model = self._configuration_service.model
        if model is None:
            return None
        if model.spec.deployment is None:
            return None
        return model.spec.deployment.outputs

    def _write_outputs_artifact(
        self,
        stage_name: str,
        non_sensitive: Dict[str, Any],
        sensitive: Dict[str, Any],
    ) -> Optional[Path]:
        """Write per-stage Terraform outputs to the configured durable store.

        Uses ``spec.deployment.outputs`` from the platform configuration.
        When not configured or ``enabled=False`` the call is a no-op.
        Sensitive output handling follows ``outputs.sensitive``:

        * ``redact`` — include the key with value ``"(sensitive)"``
        * ``omit``   — drop the key entirely

        The artifact is written to::

            {work_path}/{outputs.path}/{deployment_name}/{version}/{stage_name}.json

        Non-fatal: write failures are logged as warnings and do not affect
        the deploy outcome.

        Args:
            stage_name: The stage whose outputs are being persisted.
            non_sensitive: Outputs with ``sensitive=false`` from Terraform.
            sensitive: Outputs with ``sensitive=true`` from Terraform.

        Returns:
            Path written, or None when skipped/failed.
        """
        from strata.models.configuration_model import SensitiveOutputHandling

        outputs_config = self._get_outputs_config()
        if outputs_config is None:
            self.logger.debug("Outputs artifact not configured — skipping", stage=stage_name)
            return None
        if not outputs_config.enabled:
            self.logger.debug("Outputs artifact disabled — skipping", stage=stage_name)
            return None

        if self._deployment_service is None:
            return None

        try:
            deploy_meta = self._deployment_service.model.meta  # type: ignore[union-attr]
            deployment_name = str(deploy_meta.name)
            labels = deploy_meta.labels or {}
            version = str(labels.get("version", "unknown"))

            # Apply sensitive handling
            if outputs_config.sensitive == SensitiveOutputHandling.OMIT:
                stored: Dict[str, Any] = dict(non_sensitive)
            else:  # REDACT (default)
                stored = dict(non_sensitive)
                for key in sensitive:
                    stored[key] = "(sensitive)"

            artifact_path = self._work_path / outputs_config.path / deployment_name / version / f"{stage_name}.json"
            artifact_path.parent.mkdir(parents=True, exist_ok=True)

            data = {
                "deployment": deployment_name,
                "version": version,
                "stage": stage_name,
                "written_at": datetime.now(timezone.utc).isoformat(),
                "outputs": stored,
            }
            with open(artifact_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, default=str)

            self.logger.info(
                "Outputs artifact written",
                stage=stage_name,
                path=str(artifact_path),
                output_count=len(non_sensitive),
                sensitive_count=len(sensitive),
            )
            return artifact_path

        except Exception as exc:
            self.logger.warning("Failed to write outputs artifact", stage=stage_name, error=str(exc))
            return None

    def _write_combined_outputs_artifact(
        self,
        stage_results: list,
    ) -> Optional[Path]:
        """Write a combined deployment-outputs.json merging all stages' outputs.

        Produces a single registry-consumable artifact with outputs keyed by
        stage name.  Written to the build directory alongside the manifest.
        Non-fatal: failures are logged as warnings.

        Args:
            stage_results: List of dicts with keys: name, status, outputs, sensitive_outputs.

        Returns:
            Path written, or None when skipped/failed.
        """
        from strata.models.deployment_outputs_model import (
            DeploymentOutputsMetaModel,
            DeploymentOutputsModel,
        )

        if self._deployment_service is None:
            return None

        try:
            deploy_meta = self._deployment_service.model.meta  # type: ignore[union-attr]
            deployment_name = str(deploy_meta.name)
            labels = deploy_meta.labels or {}
            version = str(labels.get("version", "unknown"))

            ws_service = self._deployment_service.get_workspace_service()
            workspace_name = str(ws_service.model.meta.name) if ws_service and ws_service.model else deployment_name

            env_service = self._deployment_service.get_environment_service()
            environment_name = None
            tenant_code = None
            if env_service and env_service.model and env_service.model.meta:
                env_labels = env_service.model.meta.labels or {}
                environment_name = env_labels.get("environment")
                tenant_code = env_labels.get("tenant")

            outputs: Dict[str, Dict[str, Any]] = {}
            sensitive_keys: List[str] = []
            completed_stages: List[str] = []

            for result in stage_results:
                if result.get("status") != "success":
                    continue
                stage_name = result.get("name", "unknown")
                completed_stages.append(stage_name)

                stage_outputs = result.get("outputs") or {}
                outputs[stage_name] = dict(stage_outputs)

                for key in (result.get("sensitive_outputs") or {}).keys():
                    sensitive_keys.append(f"{stage_name}.{key}")

            if not outputs:
                return None  # No successful stages with outputs

            artifact = DeploymentOutputsModel(
                meta=DeploymentOutputsMetaModel(
                    name=deployment_name,
                    deployment=deployment_name,
                    version=version,
                    deployed_at=datetime.now(timezone.utc).isoformat(),
                    workspace=workspace_name,
                    environment=environment_name,
                    tenant=tenant_code,
                ),
                outputs=outputs,
                sensitive_keys=sensitive_keys,
                provenance={
                    "stages_completed": completed_stages,
                },
            )

            output_path = self._deployment_service.get_build_path(self._build_path) / "deployment-outputs.json"
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path, "w", encoding="utf-8") as fh:
                json.dump(artifact.model_dump(exclude_none=True), fh, indent=2, default=str)

            self.logger.info(
                "Combined outputs artifact written",
                path=str(output_path),
                stage_count=len(completed_stages),
            )
            return output_path

        except Exception as exc:
            self.logger.warning("Failed to write combined outputs artifact", error=str(exc))
            return None

    def _collect_artifacts(self) -> ManifestArtifactsModel:
        """Assemble the full artifact BOM from available runtime data."""
        return ManifestArtifactsModel(
            platform=self._collect_platform_artifact(),
            repositories=self._collect_repository_info(),
            providers=self._collect_provider_info(),
            images=self._collect_image_info(),
        )

    def _collect_platform_artifact(self) -> ManifestPlatformModel:
        """Compute SHA-256 of platform.json and embed its full content."""
        return collect_platform_artifact(self._deployment_service, self._build_path, self._work_path)

    def _collect_repository_info(self) -> Optional[Dict[str, ManifestRepositoryModel]]:
        """Walk solution repositories and collect URL/ref/commit info."""
        if self._solution_controller is None:
            return None
        return collect_repository_info(self._solution_controller.solution, self._solution_controller.get_repo_map())

    def _collect_provider_info(self) -> Optional[List[ManifestArtifactProviderModel]]:
        """Collect provisioner metadata from the workspace model.

        Walks ``workspace.spec.provisioners`` and captures each provisioner's
        name, tool type, and state backend configuration.
        """
        return collect_provider_info(self._deployment_service)

    def _collect_image_info(self) -> Optional[List[ManifestArtifactImageModel]]:
        """Collect container image references from stage outputs.

        Compose stages emit service image references in their outputs under
        the ``services`` key.  This method walks all recorded stage results
        and extracts any image data found there.
        """
        images: List[ManifestArtifactImageModel] = []
        for stage in self._stage_results:
            if not stage.outputs:
                continue
            services = stage.outputs.get("services")
            if not isinstance(services, list):
                continue
            for svc in services:
                if not isinstance(svc, dict):
                    continue
                name = svc.get("name") or svc.get("service")
                image = svc.get("image")
                if name and image:
                    images.append(
                        ManifestArtifactImageModel(
                            name=str(name),
                            image=str(image),
                            digest=svc.get("digest"),
                        )
                    )
        return images if images else None

    # ------------------------------------------------------------------
    # Deploy-log + SIEM forwarding (ADR-0066) — shared by run and destroy
    # ------------------------------------------------------------------

    def _get_git_field(self, *args: str) -> Optional[str]:
        """Run a git command and return stdout, or None on failure."""
        try:
            from strata.utils.system import run_command

            result = run_command(["git"] + list(args), cwd=str(self._work_path), timeout=10)
            if result.returncode == 0 and result.stdout:
                return result.stdout.strip()
        except Exception:
            pass
        return None

    def _get_current_commit(self) -> str:
        """Return the current HEAD commit SHA, or 'unknown' on failure."""
        return self._get_git_field("rev-parse", "HEAD") or "unknown"

    def _write_deploy_log_and_forward(self, event_type: str, success: bool) -> None:
        """Assemble and write deploy-log via ``AuditController``, then forward *event_type*.

        Shared by ``RunDeployCommand`` (``event_type="deployment.completed"``) and
        ``DestroyDeployCommand`` (``event_type="deployment.destroyed"``, ADR-0066 gap B)
        so the deploy-log-build + PR-enrich + forward + push-to-remote pipeline exists
        in exactly one place rather than being duplicated per command.

        This is best-effort — failures are logged as WARNING and never affect the
        deployment exit code (ADR 0018, decision #2).
        """
        try:
            from strata.controllers.audit_controller import AuditController
            from strata.models.deploy_log_model import (
                DeployLogModel,
                DeployLogStageModel,
                DeployLogStepModel,
            )
            from strata.utils.config import get_deploy_log_dir

            # Assemble per-stage data from manifest stage results
            stages: List[DeployLogStageModel] = []
            for sr in self._stage_results:
                stage_steps: List[DeployLogStepModel] = []
                if sr.steps:
                    for step_name in sr.steps:
                        stage_steps.append(DeployLogStepModel(step=step_name, success=True, duration_seconds=0.0))

                stages.append(
                    DeployLogStageModel(
                        name=sr.name,
                        provisioner=sr.provisioner,
                        topology=sr.topology,
                        success=(sr.status == "success"),
                        started_at=sr.started_at or self._deploy_started_at or "",
                        completed_at=sr.completed_at or datetime.now(timezone.utc).isoformat(),
                        duration_seconds=float(sr.duration_seconds or 0),
                        steps=stage_steps,
                        errors=[sr.error] if sr.error else [],
                    )
                )

            # Calculate total duration
            completed_at = datetime.now(timezone.utc).isoformat()
            try:
                duration = (
                    datetime.fromisoformat(completed_at)
                    - datetime.fromisoformat(self._deploy_started_at or completed_at)
                ).total_seconds()
            except (ValueError, TypeError):
                duration = 0.0

            # Get git context (best-effort)
            commit_sha = self._get_git_field("rev-parse", "HEAD")
            commit_message = self._get_git_field("log", "--format=%s", "-1")
            commit_author = self._get_git_field("log", "--format=%ae", "-1")

            # Get version
            from strata import __version__

            # Resolve deployment metadata
            deployment_name = ""
            workspace_name = None
            environment = None
            if self._deployment_service and self._deployment_service.model:
                deployment_name = self._deployment_service.model.meta.name
                spec = self._deployment_service.model.spec
                if spec:
                    workspace_name = spec.workspace.name if spec.workspace else None
                    layers = spec.layers
                    environment = layers.segments.get("environment") if layers and layers.segments else None

            payload = DeployLogModel(
                execution_id=self._execution_id,
                timestamp=self._deploy_started_at or completed_at,
                command=self.OPERATION,
                version=__version__,
                commit_sha=commit_sha,
                commit_message=commit_message,
                commit_author=commit_author,
                deployment=deployment_name or "unknown",
                workspace=workspace_name,
                environment=environment,
                file=str(self._file_path or ""),
                force=self._force,
                dry_run=False,
                success=success,
                duration_seconds=duration,
                stages=stages,
                errors=list(self._errors),
                messages=list(self._messages),
                change_reference=self._change_reference,
            )

            # Resolve audit config (structure + base path)
            structure = "by-execution"
            base_path = get_deploy_log_dir(self._work_path)
            resolved_audit_cfg = None
            if self._configuration_service:
                resolved_audit_cfg = getattr(getattr(self._configuration_service.model, "spec", None), "audit", None)
                if resolved_audit_cfg:
                    structure = resolved_audit_cfg.structure or structure
                base_path = self._configuration_service.get_deploy_log_path(self._work_path, create_path=True)

            # Write via AuditController
            controller = AuditController(work_path=self._work_path)
            ok, path = controller.write_deploy_log(
                payload=payload,
                base_path=base_path,
                structure=structure,
            )

            # Layer 4a: PR enrichment — best-effort, never blocks (gh CLI required)
            if ok and path:
                enriched = controller.enrich_with_pr_data(payload)
                if enriched.pull_request is not None:
                    import json

                    path.write_text(
                        json.dumps(enriched.model_dump(exclude_none=True), indent=2, default=str),
                        encoding="utf-8",
                    )

                # Layer 4b: SIEM forwarding — best-effort, fire-and-forget
                # Uses the enriched payload so SIEM gets PR data when available.
                controller.forward(event_type, enriched.model_dump(exclude_none=True), audit_config=resolved_audit_cfg)

                # Layer 4c: Push to remote repo — best-effort, opt-in via audit.repository.push (ADR-0065 Phase 1)
                repo_cfg = resolved_audit_cfg.repository if resolved_audit_cfg else None
                if repo_cfg and repo_cfg.push:
                    controller.push_to_remote(
                        [path],
                        local_base=base_path,
                        remote_path=repo_cfg.path or "deploy-log",
                        repo_name=repo_cfg.name,
                        workspace=str(workspace_name or "default"),
                        commit_message="chore(audit): deploy-log update [skip ci]",
                    )

            if ok and path and self._is_console_output():
                self._audit_log_path = str(path.relative_to(self._work_path))
                click.echo(f"  📝  Deploy-log: {self._audit_log_path}")
            elif ok and path:
                self._audit_log_path = str(path.relative_to(self._work_path))

        except Exception as exc:
            self.logger.warning("deploy_log_write_failed", error=str(exc))
