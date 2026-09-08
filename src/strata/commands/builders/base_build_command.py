"""Base class for build commands."""

from pathlib import Path
from typing import Any, Dict, Optional

from strata.commands.base_command import BaseCommand
from strata.controllers.repository_controller import RepositoryController
from strata.integrations.cve_scanner import CveScannerIntegration
from strata.models.integration_model import IntegrationModel
from strata.services.configuration_service import ConfigurationService
from strata.services.deployment_service import DeploymentService


class BaseBuildCommand(BaseCommand):
    """Base class for build command implementations."""

    OPERATION = "build"

    def __init__(
        self,
        file: Optional[str] = None,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
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
        self._resolved_remote_refs: Dict[str, str] = {}
        # Set by subclasses once a PlatformBuilder has produced a model (ADR-0026
        # cache warm reuses this instead of re-resolving); None until then.
        self._platform_model: Optional[Any] = None

    def get_required_integrations(self):
        return {}

    def _warm_model_cache(self) -> None:
        """Best-effort cache warm (ADR-0026). Never raises — logged and skipped on failure.

        Reuses ``self._platform_model`` (already built by a ``PlatformBuilder`` call
        earlier in the command) rather than re-resolving, so this is cheap: only a
        cache-key hash + SQLite write, no re-parsing of YAML.
        """
        from strata.controllers.cache_controller import warm_platform_model_best_effort

        warm_platform_model_best_effort(self._work_path, self._deployment_service, self._platform_model, self.logger)

    def _before_execute(self) -> bool:
        """Load and validate the deployment file + configuration service."""
        if not super()._before_execute():
            return False

        if not self._file_path:
            self._errors.append("No deployment file specified. Use --file.")
            return False

        # Resolve to absolute path, supporting @repo-name/... references
        from strata.utils.system import resolve_path

        repo_map: dict[str, str] = (
            self._solution_controller.get_repo_map() if self._solution_controller is not None else {}
        )

        try:
            candidate = resolve_path(str(self._work_path), self._raw_file, repo_map=repo_map)
        except ValueError as e:
            self._errors.append(f"Deployment file reference error: {e}")
            return False

        if not candidate.exists():
            self._errors.append(f"Deployment file not found: {candidate}")
            return False
        self._file_path = candidate
        self.logger.info("Using deployment file", file=str(self._file_path))

        # config_fetch_before: fires before loading configuration from profile refs.
        # (Config not loaded yet — hooks defined in the config are silently skipped;
        # hooks at workspace or environment level can run if already cached.)
        if not self._run_lifecycle_phase(
            "config_fetch_before",
            context={"work_path": str(self._work_path), "file": str(self._file_path)},
        ):
            return False

        # Load configuration service (always required for build)
        self._configuration_service = self._load_configuration_service()
        if self._configuration_service is None:
            return False

        # config_fetch_after: fires after configuration is loaded and validated.
        # Scripts defined in the loaded configuration lifecycle block can run here.
        if not self._run_lifecycle_phase(
            "config_fetch_after",
            context={"work_path": str(self._work_path), "file": str(self._file_path)},
        ):
            return False

        self._build_path = self._get_build_path()

        # Phase 1: load + Pydantic-validate the deployment file
        # ADR 0039: resolve spec.extends before loading into DeploymentService — a
        # deployment file that passes `strata validate --deep` (which resolves
        # extends) must be buildable too, not just validatable.
        deployment_service = self._load_deployment_service_with_extends(self._file_path, repo_map)
        if deployment_service is None:
            return False

        # Pre-flight: reject partial deployments before any build operation — a
        # partial base file (spec.partial: true) has no complete workspace/stages
        # target to build against. Mirrors BaseDeployCommand's identical check.
        if deployment_service.model and deployment_service.model.spec.partial:
            self._errors.append(
                f"'{self._file_path.name}' is a partial deployment (spec.partial: true) "
                "and cannot be built. A leaf deployment file that extends this base is required."
            )
            return False

        # Phase 2: cross-validate against configuration
        config_model = self._configuration_service.model if self._configuration_service else None
        ok, errors = deployment_service.validate(
            configuration_model=config_model,
            work_path=self._work_path,
            repo_map=repo_map,
        )
        if not ok:
            self._errors.extend(errors)
            return False

        # Load related services (workspace, environment, providers, resources, …)
        if not deployment_service.load_deploy_services(str(self._work_path), repo_map=repo_map):
            self._errors.extend(deployment_service.get_validation_errors())
            return False

        # Cross-validate related services
        ok, errors = deployment_service.validate_related_services()
        if not ok:
            self._errors.extend(errors)
            return False

        # Apply environment overrides
        ok, errors = deployment_service.apply_environment_overrides()
        if not ok:
            critical = [e for e in errors if "skipped" not in e.lower()]
            if critical:
                self._errors.extend(critical)
                return False
            self._messages.extend(errors)  # non-critical warnings

        # Ensure gitops remotes are checked out to their effective references
        if self._configuration_service is not None:
            repo_controller = RepositoryController()
            checkout_ok, resolved_refs = repo_controller.ensure_remote_refs(
                config_service=self._configuration_service,
                work_path=self._work_path,
                repo_map=repo_map,
            )
            if not checkout_ok:
                self._errors.extend(repo_controller.get_errors())
                return False
            self._resolved_remote_refs = resolved_refs

        self._deployment_service = deployment_service

        self.logger.debug(
            "Deployment loaded",
            file=str(self._file_path),
            build_path=str(self._build_path),
        )
        return True

    def _load_configuration_service(self) -> Optional[ConfigurationService]:
        """Load ConfigurationService from the active profile's configfile_paths.

        Returns the loaded service, or None on failure (errors appended to self._errors).
        """
        from strata.utils.system import resolve_path

        if self._solution_controller.solution is None:
            self._errors.append("Build requires an initialized workspace. Run `strata sln init` first.")
            return None

        profile, _ = self._solution_controller.get_active_profile()
        if profile is None:
            self._errors.append("Build requires an active profile. Run `strata profile activate <name>`.")
            return None

        configfile_paths = profile.configfile_paths or []
        if not configfile_paths:
            self._errors.append(
                "Build requires at least one configfile path on the active profile. "
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

    def _execute_audit(self, sbom_path: Path) -> bool:
        """Run CVE audit against a generated SBOM file.

        Returns True if audit passed (or scanner not available), False if
        ``--fail-on`` threshold was breached.

        Subclasses must set ``self._audit_severity`` and ``self._fail_on``
        before calling this method.
        """
        from datetime import date, datetime, timezone

        import click

        from strata.models.sbom_model import CveAllowedEntryModel

        config = IntegrationModel(name="cve_scanner", type="cve_scanner")
        scanner = CveScannerIntegration(config)

        available, reason = scanner.ensure_available()
        if not available:
            msg = f"CVE audit skipped — no scanner found ({reason})"
            self.logger.warning(msg)
            if self._is_console_output():
                click.echo(f"⚠️  {msg}")
            return True  # non-fatal

        try:
            result = scanner.scan_sbom(
                sbom_path,
                severity_threshold=self._audit_severity,
            )
        except RuntimeError as exc:
            self._errors.append(f"CVE audit failed: {exc}")
            return False

        # -- Load CVE allowlist and filter findings ---------------------------
        allowed_entries = self._load_cve_allowed(self._work_path)
        today = date.today()
        allowed_ids: dict[str, CveAllowedEntryModel] = {}
        for entry in allowed_entries:
            if entry.expires:
                try:
                    if date.fromisoformat(entry.expires) < today:
                        self.logger.debug("CVE allowlist entry expired", id=entry.id, expires=entry.expires)
                        continue
                except ValueError:
                    self.logger.warning("Invalid expires date in cve-allowed.yaml", id=entry.id, expires=entry.expires)
            allowed_ids[entry.id] = entry

        original_count = result.total_findings
        if allowed_ids:
            filtered = []
            suppressed = 0
            for f in result.findings:
                entry = allowed_ids.get(f.vulnerability_id)
                if entry and (entry.package is None or entry.package == f.package_name):
                    suppressed += 1
                    self.logger.debug(
                        "CVE suppressed by allowlist",
                        id=f.vulnerability_id,
                        package=f.package_name,
                        reason=entry.reason,
                    )
                else:
                    filtered.append(f)

            if suppressed > 0:
                # Rebuild counts from filtered findings
                severity_counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "UNKNOWN": 0}
                for f in filtered:
                    if f.severity in severity_counts:
                        severity_counts[f.severity] += 1

                result = result.model_copy(
                    update={
                        "findings": filtered,
                        "total_findings": len(filtered),
                        "critical": severity_counts["CRITICAL"],
                        "high": severity_counts["HIGH"],
                        "medium": severity_counts["MEDIUM"],
                        "low": severity_counts["LOW"],
                        "unknown": severity_counts["UNKNOWN"],
                    }
                )

                if self._is_console_output():
                    click.echo(f"ℹ️  {suppressed} finding(s) suppressed by cve-allowed.yaml")

        # -- NDJSON: emit each finding as a data event -----------------------
        if self._is_ndjson_output():
            for f in result.findings:
                self.emit_ndjson(
                    {
                        "event": "data",
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "audit_finding": {
                            "vulnerability_id": f.vulnerability_id,
                            "severity": f.severity,
                            "package_name": f.package_name,
                            "installed_version": f.installed_version,
                            "fixed_version": f.fixed_version,
                            "title": f.title,
                        },
                    }
                )

        # -- Console: severity summary table ----------------------------------
        if self._is_console_output():
            click.echo(
                f"\n🔍  CVE audit ({result.scanner} {result.scanner_version}): {result.total_findings} finding(s)"
            )
            click.echo(
                f"    CRITICAL={result.critical}  HIGH={result.high}  "
                f"MEDIUM={result.medium}  LOW={result.low}  UNKNOWN={result.unknown}"
            )
            if result.findings:
                click.echo("")
                for f in result.findings[:10]:
                    fixed = f" → {f.fixed_version}" if f.fixed_version else ""
                    click.echo(
                        f"    [{f.severity}] {f.vulnerability_id}: {f.package_name}@{f.installed_version}{fixed}"
                    )
                if result.total_findings > 10:
                    click.echo(f"    ... and {result.total_findings - 10} more")

        # -- Structured output ------------------------------------------------
        self._output_data["audit"] = {
            "scanner": result.scanner,
            "scanner_version": result.scanner_version,
            "total_findings": result.total_findings,
            "critical": result.critical,
            "high": result.high,
            "medium": result.medium,
            "low": result.low,
            "unknown": result.unknown,
        }

        # -- Fail-on gate -----------------------------------------------------
        if self._fail_on:
            severity_order = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"]
            threshold_idx = severity_order.index(self._fail_on) if self._fail_on in severity_order else 2
            counts = {
                "CRITICAL": result.critical,
                "HIGH": result.high,
                "MEDIUM": result.medium,
                "LOW": result.low,
                "UNKNOWN": result.unknown,
            }
            breaching = sum(counts[s] for s in severity_order[: threshold_idx + 1] if s in counts)
            if breaching > 0:
                msg = f"CVE audit gate failed: {breaching} finding(s) at or above {self._fail_on}"
                self._errors.append(msg)
                if self._is_console_output():
                    click.echo(f"\n❌  {msg}")
                return False

        if self._is_console_output() and result.total_findings == 0:
            click.echo("✅  No vulnerabilities found")

        # -- Write audit report files (VEX / SARIF) --------------------------
        audit_report_formats = getattr(self, "_audit_report", None)
        if audit_report_formats and sbom_path:
            from strata.services.audit_report import write_sarif, write_vex

            strata_version = self._get_strata_version()
            report_dir = sbom_path.parent
            formats = [f.strip().lower() for f in audit_report_formats.split(",")]

            for fmt in formats:
                if fmt == "vex":
                    vex_path = write_vex(result, report_dir, sbom_path, strata_version)
                    if self._is_console_output():
                        click.echo(f"📄  VEX written: {vex_path}")
                    self._output_data.setdefault("audit_reports", {})[fmt] = str(vex_path)
                elif fmt == "sarif":
                    sarif_path = write_sarif(result, report_dir, sbom_path, strata_version)
                    if self._is_console_output():
                        click.echo(f"📄  SARIF written: {sarif_path}")
                    self._output_data.setdefault("audit_reports", {})[fmt] = str(sarif_path)

        # Store for policy engine consumption
        self._cve_audit_result = result

        # Write cve-audit.json artifact so deploy gate evaluation can read CVE counts
        # (GateContextBuilder reads this file for security_review gate conditions)
        if result and sbom_path:
            try:
                cve_artifact = sbom_path.parent / "cve-audit.json"
                import json as _json

                cve_artifact.write_text(
                    _json.dumps(
                        {
                            "scanner": result.scanner,
                            "critical": result.critical,
                            "high": result.high,
                            "medium": result.medium,
                            "low": result.low,
                            "unknown": result.unknown,
                            "total_findings": result.total_findings,
                            "sbom_path": str(sbom_path),
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                self.logger.debug("cve_audit_artifact_written", path=str(cve_artifact))
            except Exception as exc:
                self.logger.debug("cve_audit_artifact_write_failed", error=str(exc))

        return True

    @staticmethod
    def _get_strata_version() -> str:
        """Return the strata CLI version string."""
        try:
            from strata import __version__

            return __version__
        except Exception:
            return "0.0.0"

    @staticmethod
    def _load_cve_allowed(work_path: Path) -> list:
        """Load .strata/cve-allowed.yaml and return a list of CveAllowedEntryModel."""
        import yaml

        from strata.controllers.solution_controller import SolutionController
        from strata.models.sbom_model import CveAllowedEntryModel

        allowed_path = SolutionController.get_cve_allowed_path(work_path)
        if not allowed_path.exists():
            return []
        try:
            with allowed_path.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
            if not isinstance(data, dict):
                return []
            entries = data.get("allowed") or []
            return [CveAllowedEntryModel(**e) for e in entries if isinstance(e, dict)]
        except Exception:
            return []
