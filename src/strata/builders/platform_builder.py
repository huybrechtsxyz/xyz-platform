"""Build the platform model artifact."""

import json
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

import yaml

from strata.builders.base_builder import BaseBuilder

if TYPE_CHECKING:
    from strata.controllers.solution_controller import SolutionController
    from strata.services.configuration_service import ConfigurationService
from strata.models.platform_artifact_model import (
    PlatformArtifactModel,
    PlatformComponentModel,
    PlatformDnsModel,
    PlatformFirewallModel,
    PlatformLifecycleModel,
    PlatformMetaModel,
    PlatformModuleModel,
    PlatformNamespaceModel,
    PlatformNetworkModel,
    PlatformProviderModel,
    PlatformProvisionerModel,
    PlatformResourceModel,
    PlatformSpecModel,
    PlatformTenantModel,
    PlatformTopologyModel,
    PlatformWorkspaceModel,
)
from strata.models.store_models import (
    FeatureStoreModel,
    SecretStoreModel,
    VariableStoreModel,
    VariableStoreType,
)
from strata.services.deployment_service import DeploymentService
from strata.services.platform_artifact_service import PlatformService
from strata.services.tenant_service import TenantService


class PlatformBuilder(BaseBuilder):
    """Builder that assembles a PlatformModel artifact from a fully-loaded
    DeploymentService and persists it as platform.json / platform.yaml."""

    def __init__(self, verbose: bool = False, configuration_service: Optional["ConfigurationService"] = None) -> None:
        super().__init__(verbose=verbose)
        self.configuration_service = configuration_service
        self._last_platform_model: Optional[PlatformArtifactModel] = None

    @property
    def last_platform_model(self) -> Optional[PlatformArtifactModel]:
        """The assembled platform model from the most recent ``build()`` call, if any.

        Populated even when ``dry_run=True`` — used by :class:`CacheController`
        (ADR-0026) to obtain a resolved model in memory without writing files.
        """
        return self._last_platform_model

    # ------------------------------------------------------------------
    # BaseBuilder interface
    # ------------------------------------------------------------------

    def build(
        self,
        deployment_service: DeploymentService,
        work_path: Path,
        build_path: Path,
        dry_run: bool = False,
        solution_controller: Optional["SolutionController"] = None,
    ) -> bool:
        """Assemble and persist the platform model.

        Args:
            deployment_service: Fully-loaded deployment service (related
                services must already be loaded and validated).
            work_path: Working directory path.
            build_path: Root build output directory.
            dry_run: When True, build the model in memory but skip writing
                output files.  The assembled model is stored in
                ``self._last_platform_model`` for downstream use.

        Returns:
            bool: True on success, False on failure.
        """
        self._last_platform_model = None

        try:
            platform, build_messages = self._build_platform(deployment_service, work_path=work_path)
            self._messages.extend(build_messages)

            if platform is None:
                return False

            self._last_platform_model = platform

            if dry_run:
                deployment_build_path = deployment_service.get_build_path(build_path)
                json_path = (
                    solution_controller.get_platform_path(deployment_service, build_path)
                    if solution_controller is not None
                    else deployment_build_path / "platform.json"
                )
                yaml_path = (
                    solution_controller.get_platform_yaml_path(deployment_service, build_path)
                    if solution_controller is not None
                    else deployment_build_path / "platform.yaml"
                )
                self._messages.append(f"[DRY-RUN] Would write platform model to: {json_path}")
                self._messages.append(f"[DRY-RUN] Would write platform model to: {yaml_path}")
                return True

            save_messages = self._save_platform(platform, deployment_service, build_path, solution_controller)
            self._messages.extend(save_messages)

            return True

        except Exception as exc:
            msg = f"Failed to build platform model: {exc}"
            self.logger.exception("Failed to build platform model", error=str(exc))
            self._errors.append(msg)
            return False

    def before_build(
        self,
        deployment_service: DeploymentService,
        work_path: Path,
        build_path: Path,
        solution_controller: Optional["SolutionController"] = None,
    ) -> bool:
        """Pre-build validation hook.

        Verifies that the deployment service and its workspace are ready
        before any build work begins.

        Args:
            deployment_service: Deployment service to validate.
            work_path: Working directory path.
            build_path: Build output directory path.

        Returns:
            bool: True on success, False on failure.
        """
        if not deployment_service.is_validated():
            self._errors.append("Deployment service is not validated")
            return False

        workspace_service = deployment_service.get_workspace_service()
        if not workspace_service:
            self._errors.append("Workspace service is not available")
            return False

        # Clean the deployment build folder so stale files from a previous run
        # (e.g. removed resource types) do not pollute the new output.
        deployment_build_path = deployment_service.get_build_path(build_path)
        if deployment_build_path.exists():
            shutil.rmtree(deployment_build_path)
            self.logger.debug("Cleaned build folder", path=str(deployment_build_path))

        if self.verbose:
            self._messages.append("Pre-build validation passed")

        return True

    def after_build(
        self,
        deployment_service: DeploymentService,
        work_path: Path,
        build_path: Path,
        dry_run: bool = False,
        solution_controller: Optional["SolutionController"] = None,
    ) -> bool:
        """Post-build verification hook.

        Confirms that the expected output files were written successfully.
        When *dry_run* is True file-existence checks are skipped.

        Args:
            deployment_service: Deployment service.
            work_path: Working directory path.
            build_path: Build output directory path.
            dry_run: When True, skip output file existence checks.
            solution_controller: Controller providing canonical path helpers.

        Returns:
            bool: True on success, False on failure.
        """
        if dry_run:
            if self.verbose:
                self._messages.append("[DRY-RUN] Skipping platform model file-existence check")
            return True

        deployment_build_path = deployment_service.get_build_path(build_path)
        json_path = (
            solution_controller.get_platform_path(deployment_service, build_path)
            if solution_controller is not None
            else deployment_build_path / "platform.json"
        )
        yaml_path = (
            solution_controller.get_platform_yaml_path(deployment_service, build_path)
            if solution_controller is not None
            else deployment_build_path / "platform.yaml"
        )

        if json_path.exists() and yaml_path.exists():
            if self.verbose:
                self._messages.append(f"Platform model files created at: {deployment_build_path}")
        else:
            self._errors.append("Platform model files were not created")
            return False

        return True

    # ------------------------------------------------------------------
    # Internal build logic
    # ------------------------------------------------------------------

    def _build_platform(
        self, deployment_service: DeploymentService, work_path: Optional[Path] = None
    ) -> Tuple[Optional[PlatformArtifactModel], List[str]]:
        """Assemble the PlatformModel from the deployment service hierarchy.

        Args:
            deployment_service: Fully-loaded deployment service.

        Returns:
            Tuple[Optional[PlatformModel], List[str]]: (model or None, messages).
        """
        messages: List[str] = []

        try:
            deployment_model = deployment_service.model
            if deployment_model is None:
                raise ValueError("Deployment service model is None")

            # Derive environment for meta label enrichment
            environment_service = deployment_service.get_environment_service()

            # Build meta
            meta = PlatformMetaModel.from_deployment_meta(deployment_model.meta, environment_service)

            # Build spec
            configuration_model = self.configuration_service.model if self.configuration_service else None
            spec = self._build_spec(deployment_service, configuration_model=configuration_model, work_path=work_path)

            platform = PlatformArtifactModel(meta=meta, spec=spec)

            if self.verbose:
                messages.append(f"Built platform model: {platform.meta.name}")

            return platform, messages

        except Exception as exc:
            msg = f"Failed to assemble platform model: {exc}"
            self.logger.exception("Failed to assemble platform model", error=str(exc))
            messages.append(msg)
            return None, messages

    def _build_spec(
        self,
        deployment_service: DeploymentService,
        configuration_model=None,
        work_path: Optional[Path] = None,
    ) -> PlatformSpecModel:
        """Build the PlatformSpecModel from workspace and deployment data.

        Args:
            deployment_service: Fully-loaded deployment service.
            configuration_model: Optional configuration model for
                artifact_path computation.

        Returns:
            PlatformSpecModel.
        """
        deployment_model = deployment_service.model
        if deployment_model is None:
            raise ValueError("Deployment service model is None")

        workspace_service = deployment_service.get_workspace_service()
        if workspace_service is None:
            raise ValueError("Workspace service is not available in deployment service")

        workspace_model = workspace_service.model
        if workspace_model is None:
            raise ValueError("Workspace model is not available in workspace service")

        # ------------------------------------------------------------------
        # Artifact path (optional — requires configuration_model)
        # ------------------------------------------------------------------
        artifact_path: Optional[str] = None
        if configuration_model and hasattr(deployment_service, "get_artifact_path"):
            artifact_path = deployment_service.get_artifact_path(
                configuration_model, work_path=str(work_path) if work_path else None
            )
            if self.verbose:
                self.logger.debug(f"Computed artifact_path: {artifact_path}")

        # ------------------------------------------------------------------
        # Workspace identity
        # ------------------------------------------------------------------
        workspace = PlatformWorkspaceModel.from_workspace_model(workspace_model)

        # ------------------------------------------------------------------
        # Providers
        # ------------------------------------------------------------------
        providers = None
        provider_services = workspace_service.get_provider_services()
        if provider_services:
            # Build a region → zone lookup from configuration (if zones are defined)
            region_to_zone: dict[str, str] = {}
            if configuration_model and configuration_model.spec.zones:
                for zone in configuration_model.spec.zones:
                    for region in zone.regions:
                        region_to_zone[region] = zone.name

            providers = [
                PlatformProviderModel.from_provider_model(
                    svc.model,
                    zone=region_to_zone.get(svc.model.spec.properties.region) if svc.model else None,
                )
                for svc in provider_services.values()
                if svc.model is not None
            ]
            if self.verbose:
                self.logger.debug(f"Built {len(providers)} provider(s)")

        # ------------------------------------------------------------------
        # Topologies — built after resource maps so components can be enriched
        # ------------------------------------------------------------------
        topologies = None
        if workspace_model.spec.topology:
            topologies = [PlatformTopologyModel(**topo.model_dump()) for topo in workspace_model.spec.topology]
            if self.verbose:
                self.logger.debug(f"Built {len(topologies)} topology/topologies")

        # ------------------------------------------------------------------
        # Resources (firewall references resolved later)
        # ------------------------------------------------------------------
        resources = None
        resource_firewall_map: dict = {}  # {resource_name: merged_fw_name}

        # Map resource names → their workspace firewall/role/count declarations.
        # Populated unconditionally from spec.resources (not gated on
        # resource_services) so managed_by: provisioner resources — which have
        # no backing service model — still contribute role/count for topology
        # enrichment below, and so the maps are always defined even when there
        # are no resource services at all.
        resource_to_firewalls: dict = {}
        resource_to_role: dict = {}
        resource_to_count: dict = {}
        if workspace_model.spec.resources:
            for res_ref in workspace_model.spec.resources:
                if res_ref.firewalls:
                    resource_to_firewalls[res_ref.name] = res_ref.firewalls
                if res_ref.role:
                    resource_to_role[str(res_ref.name)] = str(res_ref.role)
                resource_to_count[str(res_ref.name)] = res_ref.count

        resource_services = workspace_service.get_resource_services()
        if resource_services:
            resources = [
                PlatformResourceModel.from_resource_model(
                    svc.model,
                    role=resource_to_role.get(str(svc.model.meta.name)),
                    count=resource_to_count.get(str(svc.model.meta.name), 1),
                )
                for svc in resource_services.values()
                if svc is not None and svc.model is not None
            ]

        # Enrich topology components with role/count now that resource maps exist
        if topologies:
            for topology in topologies:
                topology.components = [
                    PlatformComponentModel(
                        resource=str(comp.resource),
                        role=resource_to_role.get(str(comp.resource)),
                        count=resource_to_count.get(str(comp.resource), 1),
                    )
                    for comp in topology.components
                ]

        # ------------------------------------------------------------------
        # Namespaces
        # ------------------------------------------------------------------
        namespaces = None
        namespace_services = workspace_service.get_namespace_services()
        if namespace_services:
            namespaces = [
                PlatformNamespaceModel.from_namespace_model(svc.model)
                for svc in namespace_services.values()
                if svc.model is not None
            ]

        # ------------------------------------------------------------------
        # Modules (flat dict keyed by name in workspace._related_services)
        # ------------------------------------------------------------------
        modules = None
        module_services = workspace_service.get_module_services()
        if module_services:
            all_modules = [
                PlatformModuleModel.from_module_model(svc.model)
                for svc in module_services.values()
                if svc.model is not None
            ]
            if all_modules:
                modules = all_modules
                self.logger.debug(f"Built {len(modules)} module(s)")
            else:
                self.logger.warning("No modules were built for platform model")

        # ------------------------------------------------------------------
        # Firewalls: one merged firewall per VM resource (originals are not emitted)
        # ------------------------------------------------------------------
        firewalls_list = []

        # Merged firewalls synthesised per resource from its firewall references
        if resource_services:
            for resource_name, resource_service in resource_services.items():
                if resource_service is None:
                    continue
                merged_fw = resource_service.get_merged_firewall()
                if merged_fw:
                    merged_fw_name = f"{resource_name}_merged_fw"
                    self.logger.debug(f"Adding merged firewall '{merged_fw_name}' for resource '{resource_name}'")
                    merged = PlatformFirewallModel.from_firewall_model(merged_fw)
                    # model_copy() is Pydantic v2; fall back to direct assignment
                    merged.name = merged_fw_name  # type: ignore[assignment]
                    firewalls_list.append(merged)
                    resource_firewall_map[resource_name] = merged_fw_name

        # Back-fill merged firewall reference onto each resource
        if resources and resource_firewall_map:
            for resource in resources:
                if resource.name in resource_firewall_map:
                    resource.firewall = resource_firewall_map[resource.name]
                    self.logger.debug(f"Resource '{resource.name}' references merged firewall '{resource.firewall}'")

        firewalls = firewalls_list if firewalls_list else None
        if firewalls:
            self.logger.debug(
                f"Built {len(firewalls)} firewall(s) "
                f"({len(resource_firewall_map)} merged, "
                f"{len(firewalls) - len(resource_firewall_map)} original)"
            )

        # ------------------------------------------------------------------
        # DNS zones
        # ------------------------------------------------------------------
        dns_zones = None
        dns_services = workspace_service.get_dns_services()
        if dns_services:
            dns_zones = [
                PlatformDnsModel.from_dns_model(svc.model) for svc in dns_services.values() if svc.model is not None
            ]
            self.logger.debug(f"Built {len(dns_zones)} DNS zone(s)")

        # ------------------------------------------------------------------
        # Networks
        # ------------------------------------------------------------------
        networks = None
        network_services = workspace_service.get_network_services()
        if network_services:
            networks = [
                PlatformNetworkModel.from_network_model(svc.model)
                for svc in network_services.values()
                if svc.model is not None
            ]
            self.logger.debug(f"Built {len(networks)} network(s)")

        # ------------------------------------------------------------------
        # Variables / Secrets / Features  (optional — service may not exist yet)
        # ------------------------------------------------------------------
        environment_service = deployment_service.get_environment_service()
        all_variables: Optional[List[VariableStoreModel]] = (
            environment_service.get_variables() if environment_service else None
        )
        all_secrets: Optional[List[SecretStoreModel]] = (
            environment_service.get_secrets() if environment_service else None
        )
        all_features: Optional[List[FeatureStoreModel]] = (
            environment_service.get_features() if environment_service else None
        )

        # ------------------------------------------------------------------
        # Provisioners
        # ------------------------------------------------------------------
        provisioners = None
        if workspace_model.spec.provisioners:
            provisioners = [
                PlatformProvisionerModel.model_validate(prov.model_dump()) for prov in workspace_model.spec.provisioners
            ]
            self.logger.debug(f"Added {len(provisioners)} provisioner(s)")

        # ------------------------------------------------------------------
        # Assemble spec
        # ------------------------------------------------------------------
        lifecycle_model = None
        if deployment_model.spec.lifecycle:
            lifecycle_model = PlatformLifecycleModel.model_validate(deployment_model.spec.lifecycle.model_dump())

        # ------------------------------------------------------------------
        # Tenant (load, embed, validate zone alignment)
        # ------------------------------------------------------------------
        platform_tenant: Optional[PlatformTenantModel] = None
        if deployment_model.spec.tenant and work_path:
            from strata.utils.path_convention import resolve_tenant_file_path

            tenant_file = resolve_tenant_file_path(work_path, str(deployment_model.spec.tenant), configuration_model)
            if tenant_file.exists():
                tenant_svc = TenantService(str(tenant_file))
                is_valid, c_errors = tenant_svc.validate()
                if is_valid and tenant_svc.model:
                    platform_tenant = PlatformTenantModel.from_tenant_model(tenant_svc.model)
                    # Zone alignment: each provider's resolved zone must be in tenant.zones
                    allowed_zones = set(platform_tenant.zones)
                    if providers and allowed_zones:
                        for prov in providers:
                            if prov.zone and prov.zone not in allowed_zones:
                                self._errors.append(
                                    f"Provider '{prov.name}' is in zone '{prov.zone}' which is not "
                                    f"allowed for tenant '{platform_tenant.code}'. "
                                    f"tenant zones: {sorted(allowed_zones)}"
                                )
                            elif prov.zone is None and allowed_zones:
                                self.logger.warning(
                                    "Provider has no resolved zone — cannot verify tenant zone alignment",
                                    provider=str(prov.name),
                                    tenant=str(platform_tenant.code),
                                )
                else:
                    self._errors.extend(c_errors)
            else:
                self._errors.append(f"tenant file not found for '{deployment_model.spec.tenant}': {tenant_file}")

        # ------------------------------------------------------------------
        # Convenience fields (populated here so Jinja2 templates can use
        # top-level names without navigating nested paths)
        # ------------------------------------------------------------------
        convenience_name: Optional[str] = str(deployment_model.meta.name)
        convenience_labels: Optional[Dict] = deployment_model.meta.labels or None
        convenience_annotations: Optional[Dict] = deployment_model.meta.annotations or None
        # Resolved layer/segment values (ADR-0072) — explicit -> derived -> default,
        # not the raw LayersModel object; see DeploymentService.get_resolved_layers().
        resolved_layers: Optional[Dict[str, str]] = (
            deployment_service.get_resolved_layers(configuration_model, str(work_path) if work_path else None)
            if configuration_model
            else None
        ) or None
        convenience_layers: Optional[Dict] = resolved_layers

        # chart_versions: module name → helm chart version (chart-based modules only)
        chart_versions: Optional[Dict[str, str]] = None
        if modules:
            cv = {str(m.name): m.source.chart_version for m in modules if m.source.chart_version is not None}
            chart_versions = cv or None

        # image_versions: module name → first container image found in services
        image_versions: Optional[Dict[str, str]] = None
        if modules:
            iv: Dict[str, str] = {}
            for m in modules:
                if m.services:
                    for svc in m.services:
                        if svc.image:
                            iv[str(m.name)] = svc.image
                            break
            image_versions = iv or None

        # resolved_variables: only CONSTANT-store variables have literal values at build time
        resolved_variables: Optional[Dict[str, str]] = None
        if all_variables:
            rv = {v.key: str(v.value) for v in all_variables if v.store == VariableStoreType.CONSTANT}
            resolved_variables = rv or None

        # revision: git HEAD SHA at build time (best-effort; None if not in a git repo)
        revision: Optional[str] = None
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                cwd=str(work_path) if work_path else None,
                timeout=5,
            )
            if result.returncode == 0:
                revision = result.stdout.strip() or None
        except Exception:
            pass

        return PlatformSpecModel(
            workspace=workspace,
            providers=providers,
            topologies=topologies,
            resources=resources,
            namespaces=namespaces,
            modules=modules,
            firewalls=firewalls,
            dns_zones=dns_zones,
            networks=networks,
            deployment=resolved_layers,
            artifact_path=artifact_path,
            stages=deployment_model.spec.stages,
            gates=deployment_model.spec.gates,
            lifecycle=lifecycle_model,
            properties={
                **(platform_tenant.properties or {} if platform_tenant and platform_tenant.properties else {}),
                **(deployment_model.spec.properties or {}),
            }
            or None,
            custom={
                **(platform_tenant.custom or {} if platform_tenant and platform_tenant.custom else {}),
                **(deployment_model.spec.custom or {}),
            }
            or None,
            features=all_features,
            variables=all_variables,
            secrets=all_secrets,
            provisioners=provisioners,
            stereotypes=None,
            tenant=platform_tenant,
            policies=getattr(configuration_model.spec, "policies", None) or None if configuration_model else None,
            name=convenience_name,
            labels=convenience_labels,
            annotations=convenience_annotations,
            layers=convenience_layers,
            chart_versions=chart_versions,
            image_versions=image_versions,
            resolved_variables=resolved_variables,
            revision=revision,
        )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save_platform(
        self,
        platform: PlatformArtifactModel,
        deployment_service: DeploymentService,
        build_path: Path,
        solution_controller: Optional["SolutionController"] = None,
    ) -> List[str]:
        """Persist the PlatformModel to JSON and YAML."""
        messages: List[str] = []

        try:
            deployment_build_path = deployment_service.get_build_path(build_path)
            deployment_build_path.mkdir(parents=True, exist_ok=True)

            service = PlatformService(path=None, data=None)
            service.verbose = self.verbose

            json_path = (
                solution_controller.get_platform_path(deployment_service, build_path)
                if solution_controller is not None
                else deployment_build_path / "platform.json"
            )
            yaml_path = (
                solution_controller.get_platform_yaml_path(deployment_service, build_path)
                if solution_controller is not None
                else deployment_build_path / "platform.yaml"
            )

            service.save_both_formats(platform, json_path, yaml_path)

            if self.verbose:
                messages.append(f"Saved platform model to: {json_path}")
                messages.append(f"Saved platform model to: {yaml_path}")

            # Write standalone tenant.json / tenant.yaml when a tenant is linked
            if platform.spec.tenant:
                tenant_data = platform.spec.tenant.model_dump(exclude_none=True, mode="json")
                tenant_json_path = deployment_build_path / "tenant.json"
                tenant_json_path.write_text(json.dumps(tenant_data, indent=2), encoding="utf-8")
                tenant_yaml_path = deployment_build_path / "tenant.yaml"
                with open(tenant_yaml_path, "w", encoding="utf-8") as f:
                    yaml.dump(tenant_data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
                messages.append(f"Tenant snapshot written to: {tenant_json_path}")
                messages.append(f"Tenant snapshot written to: {tenant_yaml_path}")

        except Exception as exc:
            msg = f"Failed to save platform model: {exc}"
            self.logger.exception("Failed to save platform model", error=str(exc))
            messages.append(msg)

        return messages
