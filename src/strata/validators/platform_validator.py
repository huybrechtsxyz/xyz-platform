"""Platform file validator — resolves kind and delegates to the appropriate service."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from strata.controllers.lifecycle_controller import LifecycleController
    from strata.services.configuration_service import ConfigurationService

import yaml

from strata.logger import get_logger
from strata.models.common_models import CommonLifecycleModel, PlatformKind
from strata.models.configuration_model import ConfigurationModel
from strata.models.validation_error import ValidationError
from strata.services.base_service import BaseService
from strata.services.deployment_service import DeploymentService
from strata.services.diagram_service import DiagramService
from strata.services.dns_service import DnsService
from strata.services.environment_service import EnvironmentService
from strata.services.firewall_service import FirewallService
from strata.services.module_service import ModuleService
from strata.services.namespace_service import NamespaceService
from strata.services.network_service import NetworkService
from strata.services.platform_artifact_service import PlatformService
from strata.services.promotion_record_service import PromotionRecordService
from strata.services.provider_service import ProviderService
from strata.services.resource_service import ResourceService
from strata.services.tenant_service import TenantService
from strata.services.version_lock_service import VersionLockService
from strata.services.version_manifest_service import VersionManifestService
from strata.services.workspace_service import WorkspaceService
from strata.utils.system import get_pkg_templates_path
from strata.validators.base_validator import BaseValidator

# ConfigurationService is intentionally excluded: it is a path-less singleton
# whose __init__ accepts no constructor arguments, making it incompatible with
# BaseService.load(path).  CONFIGURATION kind files are validated directly via
# ConfigurationModel.model_validate() in the validate() method below.
_KIND_TO_SERVICE: Dict[PlatformKind, Any] = {
    PlatformKind.TENANT: TenantService,
    PlatformKind.DEPLOYMENT: DeploymentService,
    PlatformKind.DIAGRAM: DiagramService,
    PlatformKind.DNS: DnsService,
    PlatformKind.ENVIRONMENT: EnvironmentService,
    PlatformKind.FIREWALL: FirewallService,
    PlatformKind.MODULE: ModuleService,
    PlatformKind.NAMESPACE: NamespaceService,
    PlatformKind.NETWORK: NetworkService,
    PlatformKind.PLATFORM_MODEL: PlatformService,
    PlatformKind.PROVIDER: ProviderService,
    PlatformKind.RESOURCE: ResourceService,
    PlatformKind.WORKSPACE: WorkspaceService,
    PlatformKind.VERSION_LOCK: VersionLockService,
    PlatformKind.VERSION_MANIFEST: VersionManifestService,
    PlatformKind.PROMOTION_RECORD: PromotionRecordService,
}


class PlatformValidator(BaseValidator):
    """Validates a single platform YAML file by resolving its kind and delegating to the appropriate service.

    ``lifecycle_controller`` is injected by the caller (``commands/`` or
    ``controllers/``) rather than constructed internally — per ADR-0003,
    ``validators/`` must not depend on ``controllers/``. When omitted, the
    ``validate_before``/``validate_after`` lifecycle hooks are skipped (treated the
    same as ``configuration_service=None`` skipping Phase 2 dynamic validation).
    """

    def __init__(
        self,
        file_path: Path,
        configuration_service: Optional[ConfigurationService] = None,
        repo_map: Optional[Dict[str, str]] = None,
        verify_digests: bool = False,
        lifecycle_controller: Optional["LifecycleController"] = None,
    ) -> None:
        super().__init__()
        self._file_path = file_path
        self._configuration_service = configuration_service
        self._repo_map = repo_map
        self._verify_digests = verify_digests
        self._lifecycle_controller = lifecycle_controller
        self._detected_kind: Optional[PlatformKind] = None
        self._service: Optional[BaseService] = None
        self._lifecycle_model: Optional[CommonLifecycleModel] = None
        self.logger = get_logger(__name__)

    @property
    def detected_kind(self) -> Optional[PlatformKind]:
        return self._detected_kind

    @property
    def service(self) -> Optional[BaseService]:
        return self._service

    def _is_template_source(self, work_path: Path) -> bool:
        """Return True when the file lives inside a recognized templates directory.

        Covers the workspace-local ``.strata/templates/`` directory and the
        package-bundled template directory used by ``strata new``. Files here
        are Jinja2 sources (e.g. ``meta.name: {{ customer-code }}``) that are
        never meant to be validated in place — they're rendered into concrete
        documents first.
        """
        try:
            resolved = self._file_path.resolve()
        except OSError:
            resolved = self._file_path

        candidates = [work_path / ".strata" / "templates"]
        try:
            candidates.append(get_pkg_templates_path() / "solution" / "dot.strata" / "templates")
        except Exception:
            pass

        for base in candidates:
            try:
                if resolved.is_relative_to(base.resolve()):
                    return True
            except OSError:
                continue
        return False

    def before_validate(self, work_path: Path) -> bool:
        """Verify file exists, parse YAML, extract and validate ``kind``."""
        if not self._file_path.exists():
            self.add_validation_error(
                "FILE_NOT_FOUND",
                f"File not found: {self._file_path}",
                context={"path": str(self._file_path)},
            )
            self.logger.warning("File not found", path=str(self._file_path))
            return False

        if self._is_template_source(work_path):
            self.add_validation_error(
                "TEMPLATE_SOURCE_SKIPPED",
                f"'{self._file_path}' is a template source file (under .strata/templates/) — "
                "it is not validated as a concrete document. Run 'strata new' to render a "
                "real file from it first.",
                context={"path": str(self._file_path)},
            )
            self.logger.info(
                "Skipping validation for template source file",
                path=str(self._file_path),
            )
            return False

        try:
            raw = self._file_path.read_text(encoding="utf-8")
            doc = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            self.add_validation_error(
                "YAML_PARSE_ERROR",
                f"YAML parse error in '{self._file_path}': {exc}",
                context={"path": str(self._file_path)},
            )
            self.logger.warning("YAML parse error", path=str(self._file_path), error=str(exc))
            return False
        except OSError as exc:
            self.add_validation_error(
                "FILE_READ_ERROR",
                f"Cannot read '{self._file_path}': {exc}",
                context={"path": str(self._file_path)},
            )
            self.logger.warning("File read error", path=str(self._file_path), error=str(exc))
            return False

        if not isinstance(doc, dict):
            self.add_validation_error(
                "INVALID_YAML_STRUCTURE",
                f"Expected a YAML mapping in '{self._file_path}', got {type(doc).__name__}.",
                context={"actual_type": type(doc).__name__},
            )
            return False

        raw_kind = doc.get("kind")
        if raw_kind is None:
            self.add_validation_error(
                "MISSING_KIND_FIELD",
                f"Missing required field 'kind' in '{self._file_path}'.",
                field="kind",
            )
            return False

        try:
            self._detected_kind = PlatformKind(raw_kind)
        except ValueError:
            valid = ", ".join(k.value for k in PlatformKind)
            self.add_validation_error(
                "UNKNOWN_KIND",
                f"Unknown kind '{raw_kind}' in '{self._file_path}'. Valid kinds: {valid}.",
                field="kind",
                value=str(raw_kind),
            )
            self.logger.warning("Unknown kind", kind=raw_kind, path=str(self._file_path))
            return False

        self.logger.debug("Kind detected", kind=self._detected_kind.value, path=str(self._file_path))

        # Extract lifecycle model from spec.lifecycle for hook execution
        raw_spec = doc.get("spec")
        spec: Dict[str, Any] = raw_spec if isinstance(raw_spec, dict) else {}
        lifecycle_raw = spec.get("lifecycle")
        if isinstance(lifecycle_raw, dict):
            try:
                self._lifecycle_model = CommonLifecycleModel.model_validate(lifecycle_raw)
            except Exception as exc:
                self.logger.debug(
                    "Could not parse lifecycle from YAML — hooks will be skipped",
                    path=str(self._file_path),
                    error=str(exc),
                )

        # Execute validate_before lifecycle hook (file-level + configuration-level scripts)
        if self._lifecycle_controller is None:
            return True

        lc = self._lifecycle_controller
        if not lc.execute_phase(
            phase_name="validate_before",
            lifecycle_model=self._lifecycle_model,
            work_path=work_path,
            context={"file": str(self._file_path), "kind": self._detected_kind.value},
            add_config_model=True,
        ):
            for err in lc.get_errors():
                self.add_validation_error(
                    "LIFECYCLE_HOOK_ERROR",
                    f"validate_before lifecycle hook: {err}",
                    context={"hook": "validate_before"},
                )
            return False

        return True

    def validate(self, work_path: Path) -> bool:
        """Map kind → service, run Phase 1 (Pydantic) and optional Phase 2 (dynamic)."""
        if self._detected_kind is None:
            self.add_validation_error(
                "MISSING_KIND",
                "validate() called before before_validate() — no kind detected.",
            )
            return False

        # CONFIGURATION kind: validate directly via ConfigurationModel (no path-based service)
        if self._detected_kind == PlatformKind.CONFIGURATION:
            return self._validate_configuration_model(work_path)

        service_class = _KIND_TO_SERVICE.get(self._detected_kind)
        if service_class is None:
            self.add_validation_error(
                "NO_SERVICE_REGISTERED",
                f"No service registered for kind '{self._detected_kind}' — cannot validate '{self._file_path}'.",
                field="kind",
                value=str(self._detected_kind),
            )
            return False

        # --- ADR 0039: deployment extends/partial resolution ---
        # Read spec.partial and spec.extends from raw YAML before any Pydantic loading.
        # This is done once here so the rest of the validation pipeline sees the
        # merged model, not the raw child-only data.
        injected_data: Optional[dict] = None
        skip_phase2_partial = False

        if self._detected_kind == PlatformKind.DEPLOYMENT:
            raw_spec: dict = {}
            try:
                _raw = yaml.safe_load(self._file_path.read_text(encoding="utf-8")) or {}
                raw_spec = _raw.get("spec") or {}
            except Exception:
                pass  # parse failure is reported by Phase 1 below

            skip_phase2_partial = bool(raw_spec.get("partial", False))
            _extends_ref = raw_spec.get("extends")

            if _extends_ref:
                from strata.services.deployment_extension_resolver import DeploymentExtensionResolver

                resolver = DeploymentExtensionResolver(
                    work_path=work_path,
                    repo_map=self._repo_map,
                )
                try:
                    injected_data = resolver.resolve(self._file_path)
                except (ValueError, FileNotFoundError) as exc:
                    self.add_validation_error(
                        "EXTENDS_RESOLUTION_ERROR",
                        f"Failed to resolve extends chain for '{self._file_path}': {exc}",
                        phase=1,
                    )
                    return False

                # Leaf of the chain must not be partial after resolution
                merged_partial = (injected_data.get("spec") or {}).get("partial", False)
                if merged_partial:
                    self.add_validation_error(
                        "PARTIAL_LEAF_ERROR",
                        f"'{self._file_path}' is a partial deployment. "
                        "The last file in an extends chain must be a complete, deployable deployment.",
                        phase=1,
                    )
                    return False

        # Phase 1: structural (Pydantic) validation via BaseService.load()
        try:
            if injected_data is not None:
                service = service_class(path=str(self._file_path), data=injected_data)
                is_valid, errors = service.validate()
                if not is_valid:
                    service._errors.extend(errors)
            else:
                service = service_class.load(str(self._file_path))
        except Exception as exc:
            self.add_validation_error(
                "SERVICE_LOAD_ERROR",
                f"Failed to load '{self._file_path}' as {service_class.__name__}: {exc}",
                context={"service": service_class.__name__},
            )
            self.logger.error(
                "Service load raised an exception",
                service=service_class.__name__,
                path=str(self._file_path),
                error=str(exc),
            )
            return False

        if not service.is_validated():
            plain = service.get_validation_errors()
            structured = service.get_structured_errors()
            if not plain:
                # Service is invalid but provided no error details — synthesize a fallback
                fallback = f"{service_class.__name__}: Phase 1 validation failed (no error details available)"
                plain = [fallback]
                structured = [ValidationError(code="PHASE1_VALIDATION_ERROR", message=fallback, phase=1)]
            for i, msg in enumerate(plain):
                self._errors.append(msg)
                if i < len(structured):
                    self._structured_errors.append(structured[i])
                else:
                    self._structured_errors.append(
                        ValidationError(code="SERVICE_VALIDATION_ERROR", message=msg, phase=1)
                    )
            self.logger.warning(
                "Phase 1 validation failed",
                service=service_class.__name__,
                error_count=len(plain),
                path=str(self._file_path),
            )
            return False

        self._service = service

        # Phase 2: dynamic validation against configuration (optional).
        # Full Phase 2 is skipped entirely for partial deployments (spec.partial: true)
        # — they are base files not intended to be validated as complete deploy
        # targets, and may legitimately omit required leaf-only fields such as
        # 'workspace'. A lighter-weight check still runs for them below.
        if self._configuration_service is not None and not skip_phase2_partial:
            is_valid, dynamic_errors = service.validate(
                configuration_model=self._configuration_service.model,
                work_path=str(work_path),
                repo_map=self._repo_map,
            )
            if not is_valid:
                for msg in dynamic_errors:
                    self.add_validation_error("DYNAMIC_VALIDATION_ERROR", msg, phase=2)
                self.logger.warning(
                    "Phase 2 dynamic validation failed",
                    service=service_class.__name__,
                    error_count=len(dynamic_errors),
                    path=str(self._file_path),
                )
                return False

            # Collect non-fatal warnings from the service (e.g. shadowed overrides)
            if hasattr(service, "get_validation_warnings"):
                for msg in service.get_validation_warnings():
                    self.add_validation_warning(msg)

            # F-2: Digest policy check (require_digests ring policy + --verify-digests format check)
            if hasattr(service, "check_digest_policy"):
                d_errors, d_warnings = service.check_digest_policy(
                    work_path=str(work_path),
                    config_model=self._configuration_service.model,
                    verify_digests=self._verify_digests,
                )
                for msg in d_errors:
                    self.add_validation_error("DIGEST_POLICY_ERROR", msg, phase=2)
                for msg in d_warnings:
                    self.add_validation_warning(msg)
                if d_errors:
                    return False

        # Lightweight partial-file check: even though full Phase 2 semantic
        # validation is skipped for `spec.partial: true` deployments, any
        # `environments[]` entry that IS already present should still point at
        # a file that exists on disk. This only runs with --deep (i.e. when a
        # configuration service was loaded), matching the scope of Phase 2.
        elif (
            self._configuration_service is not None
            and skip_phase2_partial
            and hasattr(service, "check_environment_refs_exist")
        ):
            env_ref_errors = service.check_environment_refs_exist(
                work_path=str(work_path),
                configuration_model=self._configuration_service.model,
                repo_map=self._repo_map,
            )
            if env_ref_errors:
                for msg in env_ref_errors:
                    self.add_validation_error("PARTIAL_ENVIRONMENT_FILE_NOT_FOUND", msg, phase=2)
                self.logger.warning(
                    "Partial deployment environment file check failed",
                    service=service_class.__name__,
                    error_count=len(env_ref_errors),
                    path=str(self._file_path),
                )
                return False

        return True

    def _validate_configuration_model(self, work_path: Path) -> bool:
        """Validate a CONFIGURATION kind file directly via ConfigurationModel."""
        from pydantic import ValidationError

        try:
            raw = self._file_path.read_text(encoding="utf-8")
            import yaml as _yaml

            doc = _yaml.safe_load(raw)
        except Exception as exc:
            self.add_validation_error(
                "FILE_READ_ERROR",
                f"Failed to read '{self._file_path}': {exc}",
                context={"path": str(self._file_path)},
            )
            return False

        try:
            ConfigurationModel.model_validate(doc)
        except ValidationError as exc:
            for err in exc.errors():
                loc = " -> ".join(str(p) for p in err["loc"])
                self.add_validation_error(
                    "PYDANTIC_FIELD_ERROR",
                    f"{loc}: {err['msg']}",
                    phase=1,
                    field=loc,
                    context={"type": err["type"]},
                )
            self.logger.warning(
                "Configuration model validation failed",
                error_count=exc.error_count(),
                path=str(self._file_path),
            )
            return False
        except Exception as exc:
            self.add_validation_error(
                "UNEXPECTED_VALIDATION_ERROR",
                f"Unexpected error validating '{self._file_path}': {exc}",
            )
            return False

        return True

    def after_validate(self, work_path: Path) -> bool:
        """Execute validate_after lifecycle hook (file-level + configuration-level scripts)."""
        if self._lifecycle_controller is None:
            return True

        lc = self._lifecycle_controller
        if not lc.execute_phase(
            phase_name="validate_after",
            lifecycle_model=self._lifecycle_model,
            work_path=work_path,
            context={
                "file": str(self._file_path),
                "kind": self._detected_kind.value if self._detected_kind else "unknown",
                "validation_passed": not self.has_errors(),
            },
            add_config_model=True,
        ):
            for err in lc.get_errors():
                self.add_validation_error(
                    "LIFECYCLE_HOOK_ERROR",
                    f"validate_after lifecycle hook: {err}",
                    context={"hook": "validate_after"},
                )
            return False

        return True
