"""Command reporting the effective, resolved audit configuration (ADR-0066)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import click

from strata.commands.schemas.schema_base_command import SchemaBaseCommand


class StatusAuditCommand(SchemaBaseCommand):
    """Report the effective audit journal, policy gate, and sink configuration.

    Exists because ``spec.audit`` configuration can be set in three places
    (``spec.audit.journal``, ``.strata/logging.yaml``'s ``audit:`` section, or left
    on built-in defaults) with a fixed precedence between them (ADR-0066 problem 11).
    This command resolves and reports the picture actually in effect, rather than
    requiring an operator to reconstruct it by reading multiple files.
    """

    OPERATION = "audit_status"

    def __init__(
        self,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._journal: Dict[str, Any] = {}
        self._gate: Dict[str, bool] = {}
        self._sinks: List[Dict[str, Any]] = []

    def get_required_integrations(self) -> Dict[str, str]:
        return {}

    @classmethod
    def show_console_header(cls, work_path: Optional[str] = None) -> None:
        """Suppress the standard base-command chrome."""

    @classmethod
    def show_console_footer(cls) -> None:
        """Suppress the standard base-command chrome."""

    def _execute(self) -> bool:
        import yaml

        from strata.controllers.solution_controller import SolutionController
        from strata.models.audit_config_model import AUDIT_EVENT_DEFAULTS, AuditConfigModel
        from strata.services.configuration_service import ConfigurationService
        from strata.utils.config import get_audit_log_path, get_configuration_path
        from strata.utils.system import resolve_path

        audit_config: Optional[AuditConfigModel] = None
        integration_types_by_name: Dict[str, str] = {}
        try:
            config_service = ConfigurationService.load(str(get_configuration_path(self._work_path)), validate=False)
            if config_service.model and config_service.model.spec:
                spec = config_service.model.spec
                if spec.audit:
                    audit_config = spec.audit
                if spec.integrations:
                    integration_types_by_name = {str(i.name): str(i.type) for i in spec.integrations}
        except Exception as e:
            self.logger.debug(f"Failed to load configuration for audit status (non-fatal): {e}")

        audit_config = audit_config or AuditConfigModel()

        # --- Journal: resolve effective path/rotation and which layer supplied it. ---
        # Precedence (ADR-0066): spec.audit.journal < .strata/logging.yaml's audit: < built-in default.
        logging_yaml_overrides = False
        logging_config_path = SolutionController.get_logging_config_path(self._work_path)
        if logging_config_path and logging_config_path.exists():
            try:
                with open(logging_config_path, "r", encoding="utf-8") as fh:
                    raw_logging_config = yaml.safe_load(fh) or {}
                logging_yaml_overrides = "audit" in raw_logging_config
            except Exception as e:
                self.logger.debug(f"Failed to read {logging_config_path} (non-fatal): {e}")

        if logging_yaml_overrides:
            self._journal = {
                "source": "logging_yaml",
                "path": None,
                "rotation": None,
                "note": f"overridden by {logging_config_path}'s 'audit:' section",
            }
        elif audit_config.journal is not None:
            journal = audit_config.journal
            path = (
                str(resolve_path(str(self._work_path), journal.path))
                if journal.path
                else str(get_audit_log_path(self._work_path))
            )
            self._journal = {
                "source": "spec_audit",
                "path": path,
                "rotation": journal.rotation or "size",
            }
        else:
            self._journal = {
                "source": "bootstrap",
                "path": str(get_audit_log_path(self._work_path)),
                "rotation": "size",
            }

        # --- Policy gate: every closed-set event type and whether it is admitted. ---
        self._gate = {event_type: audit_config.policy.is_enabled(event_type) for event_type in AUDIT_EVENT_DEFAULTS}

        # --- Sinks: declared routing plus whether the referenced integration exists
        # AND resolves to a real, registered integration type. A sink whose
        # `integration:` name matches a `spec.integrations[]` entry but whose `type`
        # is misspelled/nonexistent (IntegrationModel.type is a free-form string, not
        # an enum, so this passes Pydantic validation) would otherwise be silently
        # reported as fully declared even though it can never actually forward an
        # event (IntegrationFactory.create() would raise "Unknown integration type").
        from strata.integrations.factory import IntegrationFactory

        self._sinks = []
        for sink in audit_config.sinks:
            sink_integration = str(sink.integration)
            integration_type = integration_types_by_name.get(sink_integration)
            name_declared = sink_integration in integration_types_by_name
            type_known = integration_type is not None and IntegrationFactory.is_known_type(integration_type)
            self._sinks.append(
                {
                    "name": str(sink.name),
                    "integration": sink_integration,
                    "integration_type": integration_type,
                    "enabled": sink.enabled,
                    "events": sink.events,
                    "integration_declared": name_declared and type_known,
                }
            )

        self._output_data = {
            "journal": self._journal,
            "policy": self._gate,
            "sinks": self._sinks,
        }
        return True

    def _after_execute(self) -> bool:
        if self._is_console_output():
            self._render_console()
        return super()._after_execute()

    def _render_console(self) -> None:
        click.echo("Journal:")
        click.echo(f"  path:     {self._journal.get('path') or '(see note)'}")
        click.echo(f"  rotation: {self._journal.get('rotation') or '(see note)'}")
        click.echo(f"  source:   {self._journal.get('source')}")
        if self._journal.get("note"):
            click.echo(f"  note:     {self._journal['note']}")

        click.echo("\nPolicy gate:")
        for event_type in sorted(self._gate):
            state = "on " if self._gate[event_type] else "off"
            click.echo(f"  [{state}] {event_type}")

        click.echo("\nSinks:")
        if not self._sinks:
            click.echo("  (none configured)")
        else:
            for sink in self._sinks:
                flags = []
                if not sink["enabled"]:
                    flags.append("disabled")
                if not sink["integration_declared"]:
                    if sink["integration_type"] is None:
                        flags.append("integration not found")
                    else:
                        flags.append(f"integration type '{sink['integration_type']}' not registered")
                suffix = f"  [{', '.join(flags)}]" if flags else ""
                events = ", ".join(sink["events"]) if sink["events"] else "all enabled events"
                click.echo(f"  {sink['name']} -> {sink['integration']} ({events}){suffix}")
