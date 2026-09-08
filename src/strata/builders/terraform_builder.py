"""Build Terraform tfvars artifacts from the generated platform model.

Security: this builder NEVER writes resolved variable/feature/secret values.
It only documents required keys.
"""

import json
import os
import shutil
from glob import glob
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Tuple

from strata.builders.base_builder import BaseBuilder
from strata.models.common_models import ProvisionerType
from strata.models.environment_model import IncludeMergeStrategy
from strata.models.platform_artifact_model import PlatformArtifactModel
from strata.models.workspace_model import OutputFileModel, OutputProfileModel
from strata.services.deployment_service import DeploymentService
from strata.services.platform_artifact_service import PlatformService
from strata.utils.dict_merge import deep_merge
from strata.utils.terraform_loader import TerraformLoader

if TYPE_CHECKING:
    from strata.controllers.solution_controller import SolutionController


class TerraformBuilder(BaseBuilder):
    """Builder for Terraform tfvars generation."""

    def __init__(self, verbose: bool = False) -> None:
        super().__init__(verbose=verbose)

        # Requirement tracking
        self.variable_refs: Dict[str, Dict[str, Any]] = {}
        self.feature_refs: Dict[str, Dict[str, Any]] = {}
        self.secret_refs: Dict[str, Dict[str, Any]] = {}

        # Tracks which files were written during the last build() call.
        # Used by after_build() to verify only the files that were actually written.
        self._written_file_names: List[str] = []

    def build(
        self,
        deployment_service: DeploymentService,
        work_path: Path,
        build_path: Path,
        dry_run: bool = False,
        platform_model: Optional["PlatformArtifactModel"] = None,
        repo_map: Optional[Dict[str, str]] = None,
        solution_controller: Optional["SolutionController"] = None,
    ) -> bool:
        """Build Terraform tfvars files from ``platform.json``.

        Args:
            deployment_service: Loaded deployment service
            work_path: Working directory path
            build_path: Build output directory path
            dry_run: When True, build all vars in memory but skip writing
                output files.  A summary of planned outputs is emitted.
            platform_model: Pre-assembled PlatformModel (used in dry-run so
                that the on-disk ``platform.json`` is not required).
            repo_map: Repository name → absolute path mapping for @repo/ resolution.

        Returns:
            bool: True on success, False on failure.
        """
        try:
            self.variable_refs = {}
            self.feature_refs = {}
            self.secret_refs = {}
            self._written_file_names = []

            deployment_build_path = deployment_service.get_build_path(build_path)

            if platform_model is not None:
                # Caller supplied the model directly (e.g. during dry-run)
                if self.verbose:
                    model_name = (
                        platform_model.meta.name
                        if platform_model and getattr(platform_model, "meta", None)
                        else "<unknown>"
                    )
                    self._messages.append(f"Using pre-assembled platform model: {model_name}")
            else:
                platform_path = (
                    solution_controller.get_platform_path(deployment_service, build_path)
                    if solution_controller is not None
                    else deployment_service.get_build_path(build_path) / "platform.json"
                )

                if not platform_path.exists():
                    self._errors.append("Platform model not found. Run platform build first.")
                    return False

                platform_service = PlatformService.load(str(platform_path), validate=True)
                if not platform_service.is_validated() or not platform_service.model:
                    self._errors.append("Platform model validation failed")
                    return False

                platform_model = platform_service.model
                if self.verbose and platform_model and getattr(platform_model, "meta", None):
                    self._messages.append(f"Loaded platform model: {platform_model.meta.name}")

            if platform_model is None:
                error_msg = "Platform model is None after loading"
                self.logger.error("Platform model is None after loading")
                self._errors.append(error_msg)
                return False

            terraform_vars = self._build_terraform_vars(platform_model, deployment_service, [])

            if dry_run:
                terraform_paths = self._resolve_terraform_paths(deployment_service, build_path, solution_controller)
                terraform_path = (
                    terraform_paths[0]
                    if terraform_paths
                    else deployment_service.get_build_path(build_path) / "terraform"
                )
                # Use the first terraform provisioner's profile for dry-run display
                ws_service = deployment_service.get_workspace_service()
                first_profile: Optional[OutputProfileModel] = None
                if ws_service and ws_service.model:
                    tf_provs = [
                        p for p in ws_service.model.spec.provisioners if p.provisioner == ProvisionerType.TERRAFORM
                    ]
                    if tf_provs:
                        first_profile = tf_provs[0].output
                planned = [name for name, _ in self._planned_files(terraform_vars, profile=first_profile)]
                for filename in planned:
                    self._messages.append(f"[DRY-RUN] Would write: {terraform_path / filename}")
                self._messages.append(f"[DRY-RUN] Planned {len(planned)} Terraform artifact file(s)")
                variables_count = len(terraform_vars.get("required_variables", {}).get("variables", []))
                features_count = len(terraform_vars.get("required_features", {}).get("features", []))
                secrets_count = len(terraform_vars.get("required_secrets", {}).get("secrets", []))
                if variables_count or features_count or secrets_count:
                    self._messages.append(
                        f"[DRY-RUN] Requires: {variables_count} variable(s), "
                        f"{features_count} feature(s), {secrets_count} secret(s)"
                    )

                # Validate source copy in dry-run mode (no writes)
                copy_ok = self._copy_provisioner_source(
                    deployment_service=deployment_service,
                    build_path=build_path,
                    work_path=work_path,
                    repo_map=repo_map or {},
                    dry_run=True,
                    solution_controller=solution_controller,
                )
                if not copy_ok:
                    return False

                # Validate includes in dry-run mode (no writes)
                include_ok = self._process_includes(
                    deployment_service=deployment_service,
                    build_path=build_path,
                    work_path=work_path,
                    repo_map=repo_map or {},
                    dry_run=True,
                    solution_controller=solution_controller,
                )
                if not include_ok:
                    return False

                return True

            self._messages.extend(
                self._save_terraform_vars(
                    terraform_vars,
                    deployment_service,
                    build_path,
                    solution_controller,
                    work_path=work_path,
                    repo_map=repo_map or {},
                )
            )

            # Copy terraform source files from the provisioner source_path into the build
            copy_ok = self._copy_provisioner_source(
                deployment_service=deployment_service,
                build_path=build_path,
                work_path=work_path,
                repo_map=repo_map or {},
                dry_run=False,
                solution_controller=solution_controller,
            )
            if not copy_ok:
                return False

            # Process environment includes (merge/override .tf/.tfvars files on top)
            include_ok = self._process_includes(
                deployment_service=deployment_service,
                build_path=build_path,
                work_path=work_path,
                repo_map=repo_map or {},
                dry_run=False,
                solution_controller=solution_controller,
            )
            if not include_ok:
                return False

            # Validate declared inputs against module variables.tf
            inputs_ok = self._validate_inputs(
                deployment_service=deployment_service,
                build_path=build_path,
                solution_controller=solution_controller,
            )
            if not inputs_ok:
                return False

            return True

        except Exception as exc:
            error_msg = f"Failed to build Terraform artifacts: {exc}"
            self.logger.exception("Failed to build Terraform artifacts", error=str(exc))
            self._errors.append(error_msg)
            return False

    def before_build(
        self,
        deployment_service: DeploymentService,
        work_path: Path,
        build_path: Path,
        dry_run: bool = False,
        solution_controller: Optional["SolutionController"] = None,
    ) -> bool:
        """Hook executed before build starts."""
        if not deployment_service.is_validated():
            self._errors.append("Deployment service is not validated")
            return False

        if not dry_run:
            platform_path = (
                solution_controller.get_platform_path(deployment_service, build_path)
                if solution_controller is not None
                else deployment_service.get_build_path(build_path) / "platform.json"
            )

            if not platform_path.exists():
                self._errors.append(f"Platform model not found at: {platform_path}. Run platform build first.")
                return False

        if self.verbose:
            self._messages.append("Pre-build validation passed for Terraform artifacts")

        return True

    def after_build(
        self,
        deployment_service: DeploymentService,
        work_path: Path,
        build_path: Path,
        dry_run: bool = False,
        solution_controller: Optional["SolutionController"] = None,
    ) -> bool:
        """Hook executed after build completes."""
        if dry_run:
            if self.verbose:
                self._messages.append("[DRY-RUN] Skipping Terraform artifact file-existence check")
            return True

        terraform_paths = self._resolve_terraform_paths(deployment_service, build_path, solution_controller)
        if not terraform_paths:
            terraform_paths = [deployment_service.get_build_path(build_path) / "terraform"]
        terraform_path = terraform_paths[0]

        # Verify only the files that were actually written during build()
        missing = [f for f in self._written_file_names if not (terraform_path / f).exists()]
        if missing:
            self._errors.append(f"Terraform artifact files missing: {', '.join(missing)}")
            return False

        type_files = list(terraform_path.glob("resx_*.auto.tfvars.json"))
        if self.verbose:
            written_count = len(self._written_file_names)
            self._messages.append(f"Terraform artifacts created at: {terraform_path}")
            self._messages.append(f"Generated {written_count} file(s) ({len(type_files)} resource type file(s))")

        return True

    def _build_terraform_vars(
        self,
        platform: PlatformArtifactModel,
        deployment_service: DeploymentService,
        messages: List[str],
    ) -> Dict[str, Any]:
        """Build all Terraform tfvars payloads."""
        # Collect environment-declared variables and secrets
        self._collect_environment_variables(deployment_service)
        self._collect_environment_secrets(deployment_service)
        return {
            "workspace": self._build_workspace_vars(platform, messages),
            "providers": self._build_provider_vars(platform, messages),
            "topologies": self._build_topology_vars(platform, messages),
            "resources_by_category": self._build_resources_by_category(platform, deployment_service, messages),
            "modules": self._build_module_vars(platform, messages),
            "namespaces": self._build_namespace_vars(platform, messages),
            "firewalls": self._build_firewall_vars(platform, messages),
            "dns": self._build_dns_vars(platform, messages),
            "networks": self._build_network_vars(platform, messages),
            "Tenant": self._build_tenant_vars(platform, messages),
            "required_variables": self._document_required_variables(),
            "required_features": self._document_required_features(),
            "required_secrets": self._document_required_secrets(),
        }

    def _build_workspace_vars(self, platform: PlatformArtifactModel, messages: List[str]) -> Dict[str, Any]:
        """Build workspace-level tfvars payload."""
        workspace = platform.spec.workspace
        workspace_labels = workspace.labels or {}
        deployment_labels = platform.meta.labels or {}

        workspace_version = workspace_labels.get("version", "1.0.0")
        deployment_version = deployment_labels.get("version", workspace_version)
        environment = deployment_labels.get("environment", "production")

        payload = {
            "workspace_name": workspace.name,
            "workspace_version": workspace_version,
            "deployment_name": platform.meta.name,
            "environment": environment,
            "platform_version": getattr(platform.apiVersion, "value", platform.apiVersion),
            "labels": workspace_labels,
            "metadata": {
                "deployment_version": deployment_version,
                "workspace_description": (
                    workspace.annotations.get("description", "") if workspace.annotations else ""
                ),
                "deployment_description": (
                    platform.meta.annotations.get("description", "") if platform.meta.annotations else ""
                ),
                "workspace_tags": workspace.tags or [],
                "deployment_tags": platform.meta.tags or [],
            },
        }

        if self.verbose:
            messages.append(f"Built workspace vars: {workspace.name}")

        return payload

    def _build_provider_vars(self, platform: PlatformArtifactModel, messages: List[str]) -> Dict[str, Any]:
        """Build provider tfvars payload."""
        providers_dict: Dict[str, Dict[str, Any]] = {}

        if platform.spec.providers:
            for provider in platform.spec.providers:
                providers_dict[provider.name] = {
                    "type": provider.properties.type,
                    "region": provider.properties.region,
                    "version": provider.properties.version,
                    "description": provider.description,
                    "labels": provider.labels or {},
                    "tags": provider.tags or [],
                }

                if provider.references:
                    for key in provider.references.variables or []:
                        self._track_variable(
                            key,
                            f"Variable referenced by provider {provider.name}",
                            [provider.name],
                        )
                    for key in provider.references.features or []:
                        self._track_feature(
                            key,
                            f"Feature referenced by provider {provider.name}",
                            [provider.name],
                        )
                    for key in provider.references.secrets or []:
                        self._track_secret(
                            key,
                            f"Secret referenced by provider {provider.name}",
                            [provider.name],
                        )

        if self.verbose:
            messages.append(f"Built provider vars: {len(providers_dict)} providers")

        return {"platform_providers": providers_dict}

    def _build_resources_by_category(
        self,
        platform: PlatformArtifactModel,
        deployment_service: DeploymentService,
        messages: List[str],
    ) -> Dict[str, Dict[str, Any]]:
        """Build resource tfvars grouped by resource type."""
        resources_by_category: Dict[str, Dict[str, Dict[str, Any]]] = {}

        if platform.spec.resources:
            for resource in platform.spec.resources:
                resource_type = (
                    resource.properties.resource_type.lower() if resource.properties.resource_type else "uncategorized"
                )

                if resource_type not in resources_by_category:
                    resources_by_category[resource_type] = {}

                resource_data: Dict[str, Any] = {
                    "type": resource.properties.resource_type,
                    "provider": resource.properties.provider_type,
                    "category": resource.properties.category,
                    "subcategory": resource.properties.subcategory,
                    "unit_cost": resource.properties.unit_cost,
                    "description": (resource.annotations.get("description", "") if resource.annotations else ""),
                    "labels": resource.labels or {},
                    "tags": resource.tags or [],
                }

                if resource.configuration:
                    resource_data["configuration"] = resource.configuration
                if resource.storage:
                    resource_data["storage"] = resource.storage.model_dump(exclude_none=True)
                if resource.firewalls:
                    resource_data["firewalls"] = resource.firewalls
                if resource.firewall:
                    resource_data["firewall"] = resource.firewall

                self._track_resource_requirements(resource)
                resources_by_category[resource_type][resource.name] = resource_data

        result: Dict[str, Dict[str, Any]] = {}
        for resource_type, resources_dict in resources_by_category.items():
            result[resource_type] = {"resources": resources_dict}
            if self.verbose:
                messages.append(f"Built {resource_type} resources: {len(resources_dict)} resources")

        return result

    def _build_module_vars(self, platform: PlatformArtifactModel, messages: List[str]) -> Dict[str, Any]:
        """Build module tfvars payload."""
        modules_dict: Dict[str, Dict[str, Any]] = {}

        if platform.spec.modules:
            for module in platform.spec.modules:
                modules_dict[module.name] = {
                    "repository": module.source.repository,
                    "source_path": module.source.source_path,
                    "target_path": module.source.target_path,
                    "description": (module.annotations.get("description", "") if module.annotations else ""),
                    "labels": module.labels or {},
                    "tags": module.tags or [],
                    "properties": (module.properties.model_dump(exclude_none=True) if module.properties else {}),
                }

                if module.references:
                    for key in module.references.variables or []:
                        self._track_variable(
                            key,
                            f"Variable referenced by module {module.name}",
                            [module.name],
                        )
                    for key in module.references.features or []:
                        self._track_feature(
                            key,
                            f"Feature referenced by module {module.name}",
                            [module.name],
                        )
                    for key in module.references.secrets or []:
                        self._track_secret(
                            key,
                            f"Secret referenced by module {module.name}",
                            [module.name],
                        )

        return {"modules": modules_dict}

    def _build_topology_vars(self, platform: PlatformArtifactModel, messages: List[str]) -> Dict[str, Any]:
        """Build topology tfvars payload."""
        topologies_dict: Dict[str, Dict[str, Any]] = {}

        if platform.spec.topologies:
            for topology in platform.spec.topologies:
                components = []
                for component in topology.components:
                    entry: Dict[str, Any] = {"resource": component.resource}
                    if getattr(component, "role", None):
                        entry["role"] = component.role
                    count = getattr(component, "count", 1)
                    entry["count"] = count
                    components.append(entry)

                volumes = []
                if topology.volumes:
                    for volume in topology.volumes:
                        volumes.append({"name": volume.name, "type": volume.type})

                topologies_dict[topology.name] = {
                    "type": topology.type,
                    "provider": topology.provider,
                    "provisioner": str(topology.provisioner),
                    "components": components,
                    "volumes": volumes,
                }

        return {"topologies": topologies_dict}

    def _build_namespace_vars(self, platform: PlatformArtifactModel, messages: List[str]) -> Dict[str, Any]:
        """Build namespace tfvars payload."""
        namespaces_dict: Dict[str, Any] = {}

        if platform.spec.namespaces:
            for namespace in platform.spec.namespaces:
                namespaces_dict[namespace.name] = {
                    "description": (namespace.annotations.get("description", "") if namespace.annotations else ""),
                    "labels": namespace.labels or {},
                    "tags": namespace.tags or [],
                    "modules": [str(m.module) for m in namespace.modules] if namespace.modules else [],
                }

        if self.verbose:
            messages.append(f"Built namespace vars: {len(namespaces_dict)} namespaces")

        return {"namespaces": namespaces_dict}

    def _build_firewall_vars(self, platform: PlatformArtifactModel, messages: List[str]) -> Dict[str, Any]:
        """Build firewall tfvars payload."""
        firewalls_dict: Dict[str, Any] = {}

        if platform.spec.firewalls:
            for firewall in platform.spec.firewalls:
                rules: Dict[str, Any] = {
                    "reset": firewall.reset or False,
                    "defaults": [r.model_dump(exclude_none=True, by_alias=True) for r in firewall.defaults]
                    if firewall.defaults
                    else [],
                    "deny": [r.model_dump(exclude_none=True, by_alias=True) for r in firewall.deny]
                    if firewall.deny
                    else [],
                    "allow": [r.model_dump(exclude_none=True, by_alias=True) for r in firewall.allow]
                    if firewall.allow
                    else [],
                }
                firewalls_dict[firewall.name] = {
                    "description": (firewall.annotations.get("description", "") if firewall.annotations else ""),
                    "labels": firewall.labels or {},
                    "tags": firewall.tags or [],
                    "rules": rules,
                }

        if self.verbose:
            messages.append(f"Built firewall vars: {len(firewalls_dict)} firewalls")

        return {"firewalls": firewalls_dict}

    def _build_dns_vars(self, platform: PlatformArtifactModel, messages: List[str]) -> Dict[str, Any]:
        """Build DNS zone tfvars payload.

        Records sourced via ``output_key:`` never have their value written to
        ``dns.auto.tfvars.json`` (the output only exists once a preceding stage has
        applied, which build time cannot know about) — instead their coordinates
        are bucketed into ``dns_output_records`` (mirrors ``dns_secret_records``),
        keyed by ``{name}_{type}``, carrying the ``output_key`` name. The value is
        already available generically as ``TF_VAR_<output_key>`` in the DNS stage's
        apply environment (every resolved stage output is auto-injected this way),
        so the consuming Terraform module reads it directly via ``var.<output_key>``.
        """
        dns_dict: Dict[str, Any] = {}
        secret_records_dict: Dict[str, Any] = {}
        output_records_dict: Dict[str, Any] = {}

        if platform.spec.dns_zones:
            for dns in platform.spec.dns_zones:
                zones_dict: Dict[str, Any] = {}
                dns_secret_zones: Dict[str, Any] = {}
                dns_output_zones: Dict[str, Any] = {}
                for zone in dns.zones:
                    records = []
                    secret_records = {}
                    output_records = {}
                    if zone.records:
                        for record in zone.records:
                            if record.value is not None:
                                resolved_value: Optional[str] = record.value
                            elif record.var is not None:
                                var_entry = self.variable_refs.get(record.var, {})
                                resolved_value = var_entry.get("value")
                                if resolved_value is None:
                                    messages.append(
                                        f"DNS record '{record.name}' in zone '{zone.name}' uses "
                                        f"var '{record.var}' which has no resolved value — "
                                        "emitting null. Pass resolved_values to resolve at build time."
                                    )
                            elif record.secret is not None:
                                resolved_value = None
                                coord_key = f"{record.name}_{record.type.value}"
                                secret_records[coord_key] = {
                                    "name": record.name,
                                    "type": record.type.value,
                                    "secret_key": record.secret,
                                    "ttl": record.ttl,
                                    "priority": record.priority,
                                }
                            else:
                                # record.output_key is not None
                                resolved_value = None
                                coord_key = f"{record.name}_{record.type.value}"
                                output_records[coord_key] = {
                                    "name": record.name,
                                    "type": record.type.value,
                                    "output_key": record.output_key,
                                    "ttl": record.ttl,
                                    "priority": record.priority,
                                }
                            records.append(
                                {
                                    "name": record.name,
                                    "type": record.type.value,
                                    "value": resolved_value,
                                    "ttl": record.ttl,
                                    "priority": record.priority,
                                }
                            )
                    zones_dict[zone.name] = {
                        "ttl": zone.ttl,
                        "records": records,
                    }
                    if secret_records:
                        dns_secret_zones[zone.name] = secret_records
                    if output_records:
                        dns_output_zones[zone.name] = output_records
                dns_dict[dns.name] = {
                    "description": (dns.annotations.get("description", "") if dns.annotations else ""),
                    "labels": dns.labels or {},
                    "tags": dns.tags or [],
                    "provider": dns.provider,
                    "zones": zones_dict,
                }
                if dns_secret_zones:
                    secret_records_dict[dns.name] = dns_secret_zones
                if dns_output_zones:
                    output_records_dict[dns.name] = dns_output_zones

        if self.verbose:
            messages.append(f"Built DNS vars: {len(dns_dict)} DNS zone configurations")

        return {
            "dns_zones": dns_dict,
            "dns_secret_records": secret_records_dict,
            "dns_output_records": output_records_dict,
        }

    def _build_network_vars(self, platform: PlatformArtifactModel, messages: List[str]) -> Dict[str, Any]:
        """Build network topology tfvars payload."""
        networks_dict: Dict[str, Any] = {}

        if platform.spec.networks:
            for net_attachment in platform.spec.networks:
                networks_inner: Dict[str, Any] = {}
                for network in net_attachment.networks:
                    # Resolve address_space CIDRs
                    address_space: List[Optional[str]] = []
                    for addr in network.address_space:
                        if addr.value is not None:
                            address_space.append(addr.value)
                        elif addr.var is not None:
                            var_entry = self.variable_refs.get(addr.var, {})
                            resolved = var_entry.get("value")
                            if resolved is None:
                                messages.append(
                                    f"Network '{network.name}' address_space uses "
                                    f"var '{addr.var}' which has no resolved value — "
                                    "emitting null."
                                )
                            address_space.append(resolved)
                        else:
                            # addr.secret
                            address_space.append(None)

                    # Resolve subnet CIDRs
                    subnets_dict: Dict[str, Any] = {}
                    for subnet in network.subnets:
                        if subnet.cidr.value is not None:
                            resolved_cidr: Optional[str] = subnet.cidr.value
                        elif subnet.cidr.var is not None:
                            var_entry = self.variable_refs.get(subnet.cidr.var, {})
                            resolved_cidr = var_entry.get("value")
                            if resolved_cidr is None:
                                messages.append(
                                    f"Subnet '{subnet.name}' in network '{network.name}' uses "
                                    f"var '{subnet.cidr.var}' which has no resolved value — "
                                    "emitting null."
                                )
                        else:
                            # subnet.cidr.secret
                            resolved_cidr = None
                        subnets_dict[subnet.name] = {
                            "cidr": resolved_cidr,
                            "description": subnet.description,
                        }

                    # Peerings
                    peerings_dict: Dict[str, Any] = {}
                    if network.peerings:
                        for peering in network.peerings:
                            peerings_dict[peering.name] = {
                                "target": peering.target,
                            }

                    networks_inner[network.name] = {
                        "address_space": address_space,
                        "subnets": subnets_dict,
                        "peerings": peerings_dict,
                    }

                networks_dict[net_attachment.name] = {
                    "description": (
                        net_attachment.annotations.get("description", "") if net_attachment.annotations else ""
                    ),
                    "labels": net_attachment.labels or {},
                    "tags": net_attachment.tags or [],
                    "networks": networks_inner,
                }

        if self.verbose:
            messages.append(f"Built network vars: {len(networks_dict)} network configurations")

        return {"networks": networks_dict}

    def _document_required_variables(self) -> Dict[str, Any]:
        return {"variables": list(self.variable_refs.values())}

    def _document_required_features(self) -> Dict[str, Any]:
        return {"features": list(self.feature_refs.values())}

    def _document_required_secrets(self) -> Dict[str, Any]:
        return {"secrets": list(self.secret_refs.values())}

    def _build_tenant_vars(self, platform: PlatformArtifactModel, messages: List[str]) -> Dict[str, Any]:
        """Build tenant tfvars payload. Returns empty dict when no tenant is linked."""
        tenant = platform.spec.tenant
        if not tenant:
            return {}

        tenant_dict: Dict[str, Any] = {
            "code": str(tenant.code),
            "name": tenant.name,
            "zones": list(tenant.zones),
        }
        if tenant.onboarded is not None:
            tenant_dict["onboarded"] = str(tenant.onboarded)
        if tenant.configuration:
            tenant_dict["configuration"] = dict(tenant.configuration)

        if self.verbose:
            messages.append(f"Built tenant vars: {tenant.code}")

        return {"strata_tenant": tenant_dict}

    def _resolve_terraform_paths(
        self,
        deployment_service: DeploymentService,
        build_path: Path,
        solution_controller: Optional["SolutionController"],
    ) -> List[Path]:
        """Return canonical paths for all terraform provisioners in the workspace.

        Used by build, after_build and dry-run display to locate per-provisioner
        artifact directories without doing inline path arithmetic.
        """
        if solution_controller is None:
            return []
        workspace_service = deployment_service.get_workspace_service()
        if workspace_service is None or workspace_service.model is None:
            return []
        provisioners = workspace_service.model.spec.provisioners or []
        return [
            solution_controller.get_provisioner_path(deployment_service, build_path, prov)
            for prov in provisioners
            if prov.provisioner == ProvisionerType.TERRAFORM
        ]

    def _planned_files(
        self,
        terraform_vars: Dict[str, Any],
        profile: Optional[OutputProfileModel] = None,
    ) -> List[Tuple[str, Dict[str, Any]]]:
        """Return ``(filename, payload)`` pairs for every non-empty tfvars file.

        When *profile* is provided its ``should_emit()`` gate is applied.
        ``workspace.auto.tfvars.json`` is always included when the strata format
        is active.  All other files are omitted when their data section is an empty
        dict or list so that Terraform modules that do not declare those variables
        never receive an undeclared-variable warning.

        For ``format: custom`` or ``format: script`` profiles the built-in files are
        controlled by ``emits[]``.  ``format: none`` produces no files at all.
        """
        # When format=none, produce nothing
        if profile is not None and profile.format == "none":
            return []

        files: List[tuple] = []

        def _emit(category: str) -> bool:
            return profile is None or profile.should_emit(category)

        # workspace — always include when the strata format is active
        if _emit("workspace"):
            files.append(("workspace.auto.tfvars.json", terraform_vars["workspace"]))

        if _emit("providers") and terraform_vars.get("providers", {}).get("platform_providers"):
            files.append(("providers.auto.tfvars.json", terraform_vars["providers"]))

        if _emit("topologies") and terraform_vars.get("topologies", {}).get("topologies"):
            files.append(("topologies.auto.tfvars.json", terraform_vars["topologies"]))

        if _emit("modules") and terraform_vars.get("modules", {}).get("modules"):
            files.append(("modules.auto.tfvars.json", terraform_vars["modules"]))

        if _emit("namespaces") and terraform_vars.get("namespaces", {}).get("namespaces"):
            files.append(("namespaces.auto.tfvars.json", terraform_vars["namespaces"]))

        if _emit("firewalls") and terraform_vars.get("firewalls", {}).get("firewalls"):
            files.append(("firewalls.auto.tfvars.json", terraform_vars["firewalls"]))

        if _emit("dns"):
            dns = terraform_vars.get("dns", {})
            if dns.get("dns_zones"):
                files.append(("dns.auto.tfvars.json", {"dns_zones": dns["dns_zones"]}))
            if dns.get("dns_secret_records"):
                files.append(
                    (
                        "dns_secret_records.auto.tfvars.json",
                        {"dns_secret_records": dns["dns_secret_records"]},
                    )
                )
            if dns.get("dns_output_records"):
                files.append(
                    (
                        "dns_output_records.auto.tfvars.json",
                        {"dns_output_records": dns["dns_output_records"]},
                    )
                )

        if _emit("networks") and terraform_vars.get("networks", {}).get("networks"):
            files.append(("networks.auto.tfvars.json", terraform_vars["networks"]))

        if _emit("resources"):
            for resource_type, payload in terraform_vars.get("resources_by_category", {}).items():
                files.append((f"resx_{resource_type}.auto.tfvars.json", payload))

        if _emit("tenant") and terraform_vars.get("Tenant"):
            files.append(("tenant.auto.tfvars.json", terraform_vars["Tenant"]))

        # Documentation files — always produced regardless of profile
        if terraform_vars.get("required_variables", {}).get("variables"):
            files.append(("tf_required_variables.json", terraform_vars["required_variables"]))

        if terraform_vars.get("required_features", {}).get("features"):
            files.append(("tf_required_features.json", terraform_vars["required_features"]))

        if terraform_vars.get("required_secrets", {}).get("secrets"):
            files.append(("tf_required_secrets.json", terraform_vars["required_secrets"]))

        return files

    # ------------------------------------------------------------------
    # Phase 1 helpers — emit categories and custom files
    # ------------------------------------------------------------------

    def _build_feature_flags_vars(self, deployment_service: DeploymentService) -> Dict[str, bool]:
        """Resolve constant/environment-store feature flags for build-time emission.

        Returns a flat ``{key: bool}`` dict suitable for ``flags.auto.tfvars.json``.
        Integration-backed stores (Flagsmith, etc.) are skipped here — the deployer
        writes those from ``ResolvedValues`` before ``terraform init``.
        """
        from strata.models.store_models import FeatureStoreType

        env_service = deployment_service.get_environment_service()
        if env_service is None or env_service.model is None:
            return {}
        result: Dict[str, bool] = {}
        for feat in env_service.get_features():
            if feat.store == FeatureStoreType.CONSTANT:
                raw = feat.value
                if isinstance(raw, bool):
                    result[feat.key] = raw
                elif isinstance(raw, str):
                    result[feat.key] = raw.lower() not in ("false", "0", "no", "")
                else:
                    result[feat.key] = bool(raw)
            elif feat.store == FeatureStoreType.ENVIRONMENT:
                env_val = os.environ.get(str(feat.value))
                if env_val is not None:
                    result[feat.key] = env_val.lower() not in ("false", "0", "no", "")
        return result

    def _build_flat_variables(self, deployment_service: DeploymentService) -> Dict[str, Any]:
        """Resolve constant/environment-store variables for build-time emission.

        Returns a flat ``{key: value}`` dict suitable for ``variables.auto.tfvars.json``.

        When a variable declares a ``type`` field (VariableValueType), the value is
        emitted as its native Python type (dict, list, int, float, bool) which serializes
        to the corresponding JSON type. Without ``type``, values are emitted as-is for
        backward compatibility (strings remain strings, but dicts/lists also pass through).
        """
        from strata.models.store_models import VariableStoreType

        env_service = deployment_service.get_environment_service()
        if env_service is None or env_service.model is None:
            return {}
        result: Dict[str, Any] = {}
        for var in env_service.get_variables():
            if var.store == VariableStoreType.CONSTANT:
                result[var.key] = self._emit_variable_value(var)
            elif var.store == VariableStoreType.ENVIRONMENT:
                env_val = os.environ.get(str(var.value))
                if env_val is not None:
                    result[var.key] = env_val
        return result

    @staticmethod
    def _emit_variable_value(var) -> Any:
        """Convert a variable's value to the JSON-serializable form for tfvars emission.

        When ``var.type`` is set, the value passes through as its native Python type
        (Pydantic validation already ensured type-value consistency).
        When ``var.type`` is None (default), the value is emitted as-is for backward
        compatibility — string values remain strings, complex values (dict/list) also
        pass through since JSON supports them natively.
        """
        from strata.models.store_models import VariableValueType

        if var.type is None:
            # Backward compatible: emit value as-is
            return var.value

        # Typed emission: ensure correct Python type mapping
        if var.type == VariableValueType.STRING:
            return str(var.value) if var.value is not None else None
        # For number, bool, object, list, map — value is already the correct Python type
        # after Pydantic validation
        return var.value

    def _resolve_merged_properties(
        self,
        deployment_service: DeploymentService,
        source: str = "properties",
    ) -> Dict[str, Any]:
        """Return deep-merged {source} dict: workspace → environment → deployment."""
        result: Dict[str, Any] = {}

        ws_service = deployment_service.get_workspace_service()
        if ws_service and ws_service.model and ws_service.model.spec:
            ws_val = getattr(ws_service.model.spec, source, None) or {}
            result = deep_merge(result, ws_val)

        env_service = deployment_service.get_environment_service()
        if env_service and env_service.model and env_service.model.spec:
            env_val = getattr(env_service.model.spec, source, None) or {}
            result = deep_merge(result, env_val)
            # Also pick up environment.spec.overrides.properties when source=="properties"
            if source == "properties":
                overrides = getattr(env_service.model.spec, "overrides", None)
                if overrides and overrides.properties:
                    result = deep_merge(result, overrides.properties)

        return result

    def _build_custom_output_files(
        self,
        profile: OutputProfileModel,
        deployment_service: DeploymentService,
        terraform_path: Path,
        platform_path: Path,
        build_path: Path,
        work_path: Path,
        repo_map: Dict[str, str],
        dry_run: bool = False,
    ) -> bool:
        """Execute custom file definitions from ``output.files[]``.

        Handles source mode (properties/custom pass-through) and script mode.
        Returns True on success.
        """
        effective_files = list(profile.files or [])

        # Phase 1C: append environment-level overrides (additive only)
        env_service = deployment_service.get_environment_service()
        if env_service and env_service.model and env_service.model.spec:
            overrides = getattr(env_service.model.spec, "overrides", None)
            if overrides and overrides.output_files:
                # Warn on name collision
                ws_names = {f.name for f in (profile.files or [])}
                for env_file in overrides.output_files:
                    if env_file.name in ws_names:
                        self._messages.append(
                            f"⚠ Environment output_file '{env_file.name}' collides with "
                            "a workspace-level file definition — environment entry appended "
                            "(both will be written; last write wins)."
                        )
                effective_files.extend(overrides.output_files)

        for file_def in effective_files:
            if file_def.type == "script" or file_def.script:
                ok = self._execute_file_script(
                    file_def=file_def,
                    terraform_path=terraform_path,
                    platform_path=platform_path,
                    build_path=build_path,
                    work_path=work_path,
                    repo_map=repo_map,
                    dry_run=dry_run,
                )
                if not ok:
                    return False
            elif file_def.sources:
                ok = self._write_sources_file(
                    file_def=file_def,
                    terraform_path=terraform_path,
                    deployment_service=deployment_service,
                    dry_run=dry_run,
                )
                if not ok:
                    return False
            elif file_def.source:
                ok = self._write_source_file(
                    file_def=file_def,
                    terraform_path=terraform_path,
                    deployment_service=deployment_service,
                    dry_run=dry_run,
                )
                if not ok:
                    return False
        return True

    def _write_source_file(
        self,
        file_def: OutputFileModel,
        terraform_path: Path,
        deployment_service: DeploymentService,
        dry_run: bool = False,
    ) -> bool:
        """Write a single source-mode file (properties/custom pass-through)."""
        assert file_def.source is not None
        assert file_def.key is not None

        merged = self._resolve_merged_properties(deployment_service, file_def.source)
        value = merged.get(file_def.key)
        if value is None:
            self._messages.append(
                f"⚠ output.files['{file_def.name}']: key '{file_def.key}' not found in "
                f"merged {file_def.source} — skipping file."
            )
            return True

        payload = {file_def.variable: value} if file_def.variable else value
        if dry_run:
            self._messages.append(f"[DRY-RUN] Would write source file: {terraform_path / file_def.name}")
            return True

        self._write_json(terraform_path / file_def.name, payload)
        self._written_file_names.append(file_def.name)
        return True

    def _write_sources_file(
        self,
        file_def: OutputFileModel,
        terraform_path: Path,
        deployment_service: DeploymentService,
        dry_run: bool = False,
    ) -> bool:
        """Write a multi-source file (sources[] form — multiple TF variables in one file)."""
        assert file_def.sources is not None

        payload: Dict[str, Any] = {}
        for entry in file_def.sources:
            merged = self._resolve_merged_properties(deployment_service, entry.source)
            value = merged.get(entry.key)
            if value is None:
                self._messages.append(
                    f"⚠ output.files['{file_def.name}'].sources['{entry.variable}']: "
                    f"key '{entry.key}' not found in merged {entry.source} — emitting null."
                )
            payload[entry.variable] = value

        if dry_run:
            self._messages.append(f"[DRY-RUN] Would write sources file: {terraform_path / file_def.name}")
            return True

        self._write_json(terraform_path / file_def.name, payload)
        self._written_file_names.append(file_def.name)
        return True

    def _execute_file_script(
        self,
        file_def: OutputFileModel,
        terraform_path: Path,
        platform_path: Path,
        build_path: Path,
        work_path: Path,
        repo_map: Dict[str, str],
        dry_run: bool = False,
    ) -> bool:
        """Execute a per-file user script that produces one output file."""
        from strata.utils.system import resolve_path

        assert file_def.script is not None

        try:
            script_path = resolve_path(str(work_path), file_def.script, repo_map=repo_map)
        except ValueError as exc:
            self._errors.append(f"output.files['{file_def.name}']: script path resolution failed: {exc}")
            return False

        if not script_path.exists():
            self._errors.append(f"output.files['{file_def.name}']: script not found: {script_path}")
            return False

        if dry_run:
            self._messages.append(f"[DRY-RUN] Would execute file script: {script_path} → {file_def.name}")
            return True

        env = {
            **os.environ,
            "STRATA_PLATFORM_PATH": str(platform_path),
            "STRATA_BUILD_PATH": str(build_path),
            "STRATA_OUTPUT_PATH": str(terraform_path),
            "STRATA_OUTPUT_FILE": file_def.name,
            "STRATA_WORKSPACE_PATH": str(work_path),
            "STRATA_DRY_RUN": "false",
        }
        from strata.utils.system import run_command

        result = run_command(
            ["python", str(script_path)],
            env=env,
            timeout=300,
        )
        if not result.is_successful:
            if result.timed_out:
                self._errors.append(f"output.files['{file_def.name}']: script timed out after 300s")
            else:
                self._errors.append(
                    f"output.files['{file_def.name}']: script failed (exit {result.returncode}):\n{result.stderr}"
                )
            return False

        expected = terraform_path / file_def.name
        if not expected.exists():
            self._messages.append(
                f"⚠ output.files['{file_def.name}']: script exited 0 but did not write the expected file."
            )
        else:
            self._written_file_names.append(file_def.name)
        return True

    def _execute_format_script(
        self,
        profile: OutputProfileModel,
        terraform_path: Path,
        platform_path: Path,
        build_path: Path,
        work_path: Path,
        repo_map: Dict[str, str],
        provisioner_name: str,
        dry_run: bool = False,
    ) -> bool:
        """Execute a format-level user script that owns all output files."""
        from strata.utils.system import resolve_path

        assert profile.script is not None

        try:
            script_path = resolve_path(str(work_path), profile.script, repo_map=repo_map)
        except ValueError as exc:
            self._errors.append(f"format=script: script path resolution failed: {exc}")
            return False

        if not script_path.exists():
            self._errors.append(f"format=script: script not found: {script_path}")
            return False

        if dry_run:
            self._messages.append(f"[DRY-RUN] Would execute format script: {script_path}")
            return True

        env = {
            **os.environ,
            "STRATA_PLATFORM_PATH": str(platform_path),
            "STRATA_BUILD_PATH": str(build_path),
            "STRATA_OUTPUT_PATH": str(terraform_path),
            "STRATA_WORKSPACE_PATH": str(work_path),
            "STRATA_PROVISIONER": provisioner_name,
            "STRATA_DRY_RUN": "false",
        }
        from strata.utils.system import run_command

        result = run_command(
            ["python", str(script_path)],
            env=env,
            timeout=300,
        )
        if not result.is_successful:
            if result.timed_out:
                self._errors.append("format=script: script timed out after 300s")
            else:
                self._errors.append(f"format=script: script failed (exit {result.returncode}):\n{result.stderr}")
            return False

        tfvars_files = list(terraform_path.glob("*.auto.tfvars.json"))
        if not tfvars_files:
            self._messages.append(
                f"⚠ format=script: script exited 0 but no *.auto.tfvars.json files found in {terraform_path}."
            )
        return True

    def _save_terraform_vars(
        self,
        terraform_vars: Dict[str, Any],
        deployment_service: DeploymentService,
        build_path: Path,
        solution_controller: Optional["SolutionController"] = None,
        work_path: Optional[Path] = None,
        repo_map: Optional[Dict[str, str]] = None,
    ) -> List[str]:
        """Write Terraform payload files, applying the provisioner output profile.

        Iterates once per Terraform provisioner so each provisioner's ``output``
        profile drives its own set of emitted files.  This fixes the bug where
        the old implementation wrote identical files to every provisioner path.
        """
        messages: List[str] = []

        try:
            ws_service = deployment_service.get_workspace_service()
            provisioners = (
                [p for p in ws_service.model.spec.provisioners if p.provisioner == ProvisionerType.TERRAFORM]
                if ws_service and ws_service.model
                else []
            )

            if not provisioners:
                # Fallback for workspaces with no provisioner list loaded
                terraform_path = deployment_service.get_build_path(build_path) / "terraform"
                terraform_path.mkdir(parents=True, exist_ok=True)
                planned = self._planned_files(terraform_vars, profile=None)
                self._written_file_names = [name for name, _ in planned]
                for filename, payload in planned:
                    self._write_json(terraform_path / filename, payload)
                messages.append(f"✓ Terraform artifacts saved to: {terraform_path}")
                return messages

            for prov in provisioners:
                terraform_path = (
                    solution_controller.get_provisioner_path(deployment_service, build_path, prov)
                    if solution_controller is not None
                    else deployment_service.get_build_path(build_path) / "terraform"
                )
                terraform_path.mkdir(parents=True, exist_ok=True)

                profile: Optional[OutputProfileModel] = prov.output
                # Default: strata format (backward compat)

                # --- format: none — no output files ---
                if profile is not None and profile.format == "none":
                    messages.append(f"✓ Provisioner '{prov.name}': format=none, skipping tfvars output.")
                    continue

                # --- format: script — user script owns all output ---
                if profile is not None and profile.format == "script":
                    platform_path = (
                        solution_controller.get_platform_path(deployment_service, build_path)
                        if solution_controller
                        else deployment_service.get_build_path(build_path) / "platform.json"
                    )
                    ok = self._execute_format_script(
                        profile=profile,
                        terraform_path=terraform_path,
                        platform_path=platform_path,
                        build_path=deployment_service.get_build_path(build_path),
                        work_path=work_path or Path("."),
                        repo_map=repo_map or {},
                        provisioner_name=str(prov.name),
                        dry_run=False,
                    )
                    if not ok:
                        return messages
                    messages.append(f"✓ Provisioner '{prov.name}': format=script output written.")
                    continue

                # --- format: strata / custom — built-in files + optional custom files ---
                planned = self._planned_files(terraform_vars, profile=profile)
                written = [name for name, _ in planned]
                for filename, payload in planned:
                    self._write_json(terraform_path / filename, payload)

                # Emit features (build-time: constant/env stores only)
                if profile is not None and profile.should_emit("features"):
                    flags = self._build_feature_flags_vars(deployment_service)
                    if flags:
                        self._write_json(terraform_path / "flags.auto.tfvars.json", flags)
                        written.append("flags.auto.tfvars.json")

                # Emit variables (build-time: constant/env stores only)
                if profile is not None and profile.should_emit("variables"):
                    flat_vars = self._build_flat_variables(deployment_service)
                    if flat_vars:
                        self._write_json(terraform_path / "variables.auto.tfvars.json", flat_vars)
                        written.append("variables.auto.tfvars.json")

                # Emit properties flat dump
                if profile is not None and profile.should_emit("properties"):
                    merged_props = self._resolve_merged_properties(deployment_service, "properties")
                    if merged_props:
                        self._write_json(terraform_path / "properties.auto.tfvars.json", merged_props)
                        written.append("properties.auto.tfvars.json")

                # Emit custom flat dump
                if profile is not None and profile.should_emit("custom"):
                    merged_custom = self._resolve_merged_properties(deployment_service, "custom")
                    if merged_custom:
                        self._write_json(terraform_path / "custom.auto.tfvars.json", merged_custom)
                        written.append("custom.auto.tfvars.json")

                # Custom file definitions (source mode + per-file scripts)
                if profile is not None and profile.files:
                    platform_path = (
                        solution_controller.get_platform_path(deployment_service, build_path)
                        if solution_controller
                        else deployment_service.get_build_path(build_path) / "platform.json"
                    )
                    ok = self._build_custom_output_files(
                        profile=profile,
                        deployment_service=deployment_service,
                        terraform_path=terraform_path,
                        platform_path=platform_path,
                        build_path=deployment_service.get_build_path(build_path),
                        work_path=work_path or Path("."),
                        repo_map=repo_map or {},
                        dry_run=False,
                    )
                    if not ok:
                        return messages

                self._written_file_names.extend(written)
                messages.append(f"✓ Terraform artifacts saved to: {terraform_path}")

        except Exception as exc:
            error_msg = f"Failed to save Terraform artifacts: {exc}"
            self.logger.exception("Failed to save Terraform artifacts", error=str(exc))
            messages.append(error_msg)

        return messages

    def _write_json(self, path: Path, payload: Dict[str, Any]) -> None:
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # ------------------------------------------------------------------
    # Input validation against module variables.tf
    # ------------------------------------------------------------------

    def _validate_inputs(
        self,
        deployment_service: DeploymentService,
        build_path: Path,
        solution_controller: Optional["SolutionController"] = None,
    ) -> bool:
        """Validate declared variable/feature keys against the module's variables.tf.

        Runs after source copy, so the provisioner's .tf files are available in the
        build directory. Collects all declared input keys (variables + features from
        environment YAML) and cross-checks against parsed variable declarations.

        Undeclared inputs are errors (blocks build). Unsupplied required variables
        are warnings (informational, does not block).

        Returns:
            True if no undeclared-input errors found, False otherwise.
        """
        from strata.validators.terraform_input_validator import (
            STRATA_INJECTED_KEYS,
            check_inputs,
            collect_backend_expr_keys,
            parse_variables_tf,
        )

        workspace_service = deployment_service.get_workspace_service()
        if workspace_service is None or workspace_service.model is None:
            return True

        provisioners = workspace_service.model.spec.provisioners or []
        all_stages = (
            deployment_service.model.spec.stages or []
            if deployment_service.model and deployment_service.model.spec
            else []
        )
        has_errors = False

        for prov in provisioners:
            if prov.provisioner != ProvisionerType.TERRAFORM:
                continue

            # Determine the build directory where .tf files were copied
            prov_build_dir = (
                solution_controller.get_provisioner_path(deployment_service, build_path, prov)
                if solution_controller is not None
                else deployment_service.get_build_path(build_path)
                / (prov.source.target_path or prov.source.source_path if prov.source else "terraform")
            )

            if not prov_build_dir.exists():
                continue

            # Parse module variable declarations
            module_vars = parse_variables_tf(prov_build_dir)
            if not module_vars:
                # No variables.tf found or no variables declared — skip
                continue

            # Collect all declared input keys for this provisioner. Secrets are scoped to
            # the stage(s) that actually resolve to *this* provisioner (mirroring
            # ResolvedValues.for_stage(), which applies the same stage.secrets allowlist
            # at deploy time) — a secret meant only for a different provisioner sharing
            # this environment file (e.g. an Ansible/Helm-only app secret) is never
            # injected here and must not be flagged as "not declared in variables.tf".
            matching_stages = self._stages_for_provisioner(workspace_service.model, all_stages, prov)
            declared_keys = self._collect_declared_input_keys(deployment_service, matching_stages)

            # Include keys that will be injected at deploy-time via inputs_from
            if prov.inputs_from:
                from strata.utils.resolved_values import collect_inputs_from_keys

                declared_keys.update(collect_inputs_from_keys(prov.inputs_from))

            # Also include keys from the platform structural output (resource categories, etc.)
            # These are emitted by the builder itself and should be excluded from checks.
            excluded = set(STRATA_INJECTED_KEYS)
            # Add resource-category keys emitted by _build_resources_by_category
            excluded.update(self._collect_platform_emitted_keys(deployment_service))

            # Backend configuration expressions (${var:KEY} / ${secret:KEY}) are resolved
            # directly from ResolvedValues at deploy time — never passed as Terraform root-
            # module inputs — so keys referenced only there are backend plumbing, not
            # undeclared module inputs.
            if prov.backend is not None:
                excluded.update(collect_backend_expr_keys(prov.backend.configuration))

            # Run the cross-check
            result = check_inputs(declared_keys, module_vars, excluded_keys=excluded)

            # Report results
            for error in result.errors:
                self._errors.append(f"[{prov.name}] {error}")
            for warning in result.warnings:
                self._messages.append(f"⚠ [{prov.name}] {warning}")

            if result.has_errors:
                has_errors = True

        return not has_errors

    def _stages_for_provisioner(self, workspace_model: Any, stages: List[Any], prov: Any) -> List[Any]:
        """Return the deployment stages that resolve to *prov*.

        Mirrors ``BaseDeployer._resolve_iac_model()``'s resolution order so build-time
        validation scopes secrets exactly the way deploy-time injection does:

        1. ``stage.provisioner`` — explicit provisioner name match.
        2. ``stage.topology``    — topology name → ``topology.provisioner`` name match.
        3. Sole workspace provisioner — every stage implicitly uses it when it's the
           only one declared and neither of the above is set.
        """
        provisioners = workspace_model.spec.provisioners or []
        topologies = workspace_model.spec.topology or []
        sole_provisioner_name = provisioners[0].name if len(provisioners) == 1 else None

        matched: List[Any] = []
        for stage in stages:
            resolved_name: Optional[str] = None
            if stage.provisioner:
                resolved_name = stage.provisioner
            elif stage.topology:
                topo = next((t for t in topologies if str(t.name) == stage.topology), None)
                if topo:
                    resolved_name = str(topo.provisioner)
            elif sole_provisioner_name:
                resolved_name = sole_provisioner_name

            if resolved_name == prov.name:
                matched.append(stage)

        return matched

    @staticmethod
    def _allowed_secret_keys_for_stages(stages: List[Any], all_secret_keys: Set[str]) -> Set[str]:
        """Scope declared secret keys to what ``ResolvedValues.for_stage()`` would inject.

        - No matching stages at all (no ``stages:`` defined, or this provisioner isn't
          referenced by any stage) — no per-stage scoping signal available, so fall back
          to the legacy unscoped behavior rather than silently skipping validation.
        - Any matching stage with ``secrets: ['*']`` — all secrets (escape hatch, same as
          ``ResolvedValues.for_stage()``).
        - Otherwise — the union of every matching stage's ``secrets:`` allowlist.
        """
        if not stages:
            return set(all_secret_keys)

        allowed: Set[str] = set()
        for stage in stages:
            stage_secrets = getattr(stage, "secrets", None)
            if not stage_secrets:
                continue
            if stage_secrets == ["*"]:
                return set(all_secret_keys)
            allowed.update(stage_secrets)
        return allowed

    def _collect_declared_input_keys(
        self, deployment_service: DeploymentService, matching_stages: Optional[List[Any]] = None
    ) -> Set[str]:
        """Collect all variable and feature keys declared in the environment YAML.

        Variables and features are never stage-scoped at deploy time (they're injected
        into every stage unfiltered — see ``ResolvedValues.for_stage()``), so they are
        collected unconditionally here too. Secrets ARE stage-scoped at deploy time via
        each stage's ``secrets:`` allowlist, so when *matching_stages* is given, only
        secrets reachable by those stages are included — see
        ``_allowed_secret_keys_for_stages()``.
        """
        keys: Set[str] = set()

        env_service = deployment_service.get_environment_service()
        if env_service is None or env_service.model is None:
            return keys

        # Variables
        for var in env_service.get_variables():
            keys.add(var.key)

        # Features
        for feat in env_service.get_features():
            keys.add(feat.key)

        # Secrets (injected as TF_VAR_* at deploy-time) — scoped to what the matching
        # stage(s)' secrets: allowlist would actually pass to this provisioner.
        if env_service.model.spec and env_service.model.spec.secrets:
            all_secret_keys = {secret.key for secret in env_service.model.spec.secrets}
            keys.update(self._allowed_secret_keys_for_stages(matching_stages or [], all_secret_keys))

        return keys

    def _collect_platform_emitted_keys(self, deployment_service: DeploymentService) -> Set[str]:
        """Collect keys emitted by the platform builder that should be excluded from checks.

        These are structural keys like resource category variable names that the builder
        emits regardless of what the environment declares.
        """
        keys: Set[str] = set()

        # Resource category keys (e.g. "databases", "caches") from workspace resources
        workspace_service = deployment_service.get_workspace_service()
        if workspace_service and workspace_service.model and workspace_service.model.spec.resources:
            # The builder groups resources by their role/category into tfvars
            for resource in workspace_service.model.spec.resources:
                if resource.role:
                    keys.add(str(resource.role))

        return keys

    # ------------------------------------------------------------------
    # Terraform provisioner source copy
    # ------------------------------------------------------------------

    def _copy_provisioner_source(
        self,
        deployment_service: DeploymentService,
        build_path: Path,
        work_path: Path,
        repo_map: Dict[str, str],
        dry_run: bool = False,
        solution_controller: Optional["SolutionController"] = None,
    ) -> bool:
        """Copy terraform source files from each provisioner's source_path into the build.

        For each terraform provisioner declared in the workspace:
          source  = repo_root / source_path   (repo_root = work_path when repo not in repo_map)
          dest    = solution_controller.get_provisioner_path(deployment_service, build_path, prov)

        In dry_run mode only logs the planned copy; no files are written.
        """
        workspace_service = deployment_service.get_workspace_service()
        if workspace_service is None or workspace_service.model is None:
            return True  # Nothing to copy — workspace not loaded

        provisioners = workspace_service.model.spec.provisioners or []
        deployment_build_path = deployment_service.get_build_path(build_path)
        template_context = self._build_template_context(deployment_service)

        for prov in provisioners:
            if prov.provisioner != ProvisionerType.TERRAFORM:
                continue

            source = prov.source
            if source.source_path is None:
                self._errors.append(
                    f"Provisioner '{prov.name}' has no source_path — "
                    "provisioner sources must use a git-based source (repository + source_path)."
                )
                return False

            repo_name = str(source.repository) if source.repository else ""

            # Resolve repository root: use repo_map when available, fall back to work_path
            if repo_map and repo_name and repo_name in repo_map:
                repo_root = Path(repo_map[repo_name])
            else:
                repo_root = work_path

            src_dir = repo_root / source.source_path
            dest_dir = (
                solution_controller.get_provisioner_path(deployment_service, build_path, prov)
                if solution_controller is not None
                else deployment_build_path / (source.target_path or source.source_path)
            )

            # When source.reference is set, extract from the pinned ref using git archive
            # instead of copying from the (potentially different) working tree checkout.
            if source.reference:
                if dry_run:
                    self._messages.append(
                        f"[DRY-RUN] Would extract terraform source at ref '{source.reference}': "
                        f"{repo_root}/{source.source_path} → {dest_dir}"
                    )
                    continue

                ok, msg = self._extract_source_at_ref(
                    repo_root=repo_root,
                    source_path=source.source_path,
                    ref=source.reference,
                    dest_dir=dest_dir,
                    provisioner_name=prov.name,
                )
                if not ok:
                    self._errors.append(msg)
                    return False
                self._apply_templates_to_dir(dest_dir, template_context)
                self._messages.append(
                    f"Extracted terraform source at ref '{source.reference}': "
                    f"{repo_root}/{source.source_path} → {dest_dir}"
                )
                continue

            if dry_run:
                self._messages.append(f"[DRY-RUN] Would copy terraform source: {src_dir} → {dest_dir}")
                if not src_dir.exists():
                    self._errors.append(f"Terraform source directory not found: {src_dir} (provisioner: {prov.name})")
                    return False
                continue

            if not src_dir.exists():
                self._errors.append(f"Terraform source directory not found: {src_dir} (provisioner: {prov.name})")
                return False

            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src_dir, dest_dir, dirs_exist_ok=True)
            self._apply_templates_to_dir(dest_dir, template_context)
            self._messages.append(f"Copied terraform source: {src_dir} → {dest_dir}")

        return True

    # ------------------------------------------------------------------
    # Terraform file includes (merge external .tf / .tfvars into build)
    # ------------------------------------------------------------------

    def _process_includes(
        self,
        deployment_service: DeploymentService,
        build_path: Path,
        work_path: Path,
        repo_map: Dict[str, str],
        dry_run: bool = False,
        solution_controller: Optional["SolutionController"] = None,
    ) -> bool:
        """Validate and execute terraform file includes from environment model.

        Collects includes from:
        - Environment-wide: spec.overrides.includes
        - Per-resource: spec.overrides.resources[].includes

        Phase 1: Validate all includes (resolve paths, check files exist).
        Phase 2: Execute merges/concatenations (skipped in dry_run mode).

        Returns:
            True on success, False if validation fails.
        """
        from strata.utils.system import resolve_path

        env_service = deployment_service.get_environment_service()
        if env_service is None or env_service.model is None:
            return True  # No environment — nothing to include

        overrides = getattr(env_service.model.spec, "overrides", None)
        if overrides is None:
            return True

        # Collect all include directives with their context
        include_tasks: List[Dict[str, Any]] = []

        # Environment-wide includes
        if overrides.includes:
            for inc in overrides.includes:
                include_tasks.append({"include": inc, "context": "environment-wide"})

        # Per-resource includes
        if overrides.resources:
            for res_override in overrides.resources:
                if res_override.includes:
                    for inc in res_override.includes:
                        include_tasks.append(
                            {
                                "include": inc,
                                "context": f"resource:{res_override.resource}",
                            }
                        )

        if not include_tasks:
            return True

        # Resolve output directory
        terraform_paths = self._resolve_terraform_paths(deployment_service, build_path, solution_controller)
        terraform_path = (
            terraform_paths[0] if terraform_paths else deployment_service.get_build_path(build_path) / "terraform"
        )

        # Phase 1: Validate all includes
        resolved_tasks: List[Dict[str, Any]] = []
        for task in include_tasks:
            inc = task["include"]
            context = task["context"]

            # Resolve source path(s)
            source_files: List[Path] = []
            try:
                resolved_source = resolve_path(str(work_path), inc.source, repo_map=repo_map)
                source_str = str(resolved_source)

                # Support glob patterns
                if "*" in source_str or "?" in source_str:
                    matched = sorted(glob(source_str))
                    if not matched and not inc.optional:
                        self._errors.append(f"Include source glob matched no files: {inc.source} ({context})")
                        return False
                    source_files = [Path(m) for m in matched]
                else:
                    if not resolved_source.exists():
                        if not inc.optional:
                            self._errors.append(
                                f"Include source not found: {inc.source} → {resolved_source} ({context})"
                            )
                            return False
                    else:
                        source_files = [resolved_source]

            except ValueError as exc:
                self._errors.append(f"Include source resolution error: {exc} ({context})")
                return False

            if not source_files:
                # Optional include with no matching files — skip
                self._messages.append(f"⊘ Include skipped (optional, not found): {inc.source} ({context})")
                continue

            # Validate target filename (no path traversal)
            target_path = terraform_path / inc.target
            try:
                target_path.resolve().relative_to(terraform_path.resolve())
            except ValueError:
                self._errors.append(f"Include target escapes terraform directory: {inc.target} ({context})")
                return False

            # Sort by order field if specified
            if inc.order is not None:
                sort_key = inc.order
            else:
                sort_key = len(resolved_tasks)

            resolved_tasks.append(
                {
                    "source_files": source_files,
                    "target": target_path,
                    "strategy": inc.strategy,
                    "context": context,
                    "order": sort_key,
                    "source_ref": inc.source,
                }
            )

        # Sort resolved tasks by order
        resolved_tasks.sort(key=lambda t: t["order"])

        # Dry-run reporting
        if dry_run:
            for task in resolved_tasks:
                strategy_label = task["strategy"].value
                src_count = len(task["source_files"])
                self._messages.append(
                    f"[DRY-RUN] Would {strategy_label} {src_count} file(s) → {task['target'].name} ({task['context']})"
                )
            if resolved_tasks:
                self._messages.append(f"[DRY-RUN] Planned {len(resolved_tasks)} include operation(s)")
            return True

        # Phase 2: Execute
        loader = TerraformLoader()
        for task in resolved_tasks:
            src_files: List[Path] = task["source_files"]
            target: Path = task["target"]
            strategy: IncludeMergeStrategy = task["strategy"]
            ctx: str = task["context"]
            source_ref: str = task["source_ref"]

            try:
                if strategy == IncludeMergeStrategy.MERGE:
                    result = loader.load_and_merge(src_files)
                    loader.write(result, target)
                elif strategy == IncludeMergeStrategy.CONCATENATE:
                    content = loader.concatenate(src_files)
                    loader.write_raw(content, target)

                self._messages.append(f"✓ Include {strategy.value}: {source_ref} → {target.name} ({ctx})")

            except Exception as exc:
                self._errors.append(f"Include {strategy.value} failed: {source_ref} → {target.name}: {exc} ({ctx})")
                return False

        return True

    def _track_resource_requirements(self, resource: Any) -> None:
        if not resource.references:
            return

        for key in resource.references.variables or []:
            self._track_variable(
                key,
                f"Variable referenced by resource {resource.name}",
                [resource.name],
            )

        for key in resource.references.features or []:
            self._track_feature(
                key,
                f"Feature referenced by resource {resource.name}",
                [resource.name],
            )

        for key in resource.references.secrets or []:
            self._track_secret(
                key,
                f"Secret referenced by resource {resource.name}",
                [resource.name],
            )

    def _track_variable(
        self,
        key: str,
        description: str,
        used_by: List[str],
        store: Optional[str] = None,
        value: Any = None,
    ) -> None:
        if key not in self.variable_refs:
            entry: Dict[str, Any] = {
                "key": key,
                "description": description,
                "required": True,
                "suggested_env_var": f"TF_VAR_{key}",
                "used_by": list(used_by),
            }
            if store is not None:
                entry["store"] = store
            if value is not None:
                entry["value"] = value
            self.variable_refs[key] = entry
        else:
            existing = self.variable_refs[key]
            merged = set(existing.get("used_by", [])) | set(used_by)
            existing["used_by"] = sorted(merged)

    def _track_feature(self, key: str, description: str, used_by: List[str]) -> None:
        if key not in self.feature_refs:
            self.feature_refs[key] = {
                "key": key,
                "description": description,
                "required": True,
                "suggested_env_var": f"TF_VAR_{key}",
                "used_by": list(used_by),
            }
        else:
            existing = self.feature_refs[key]
            merged = set(existing.get("used_by", [])) | set(used_by)
            existing["used_by"] = sorted(merged)

    def _collect_environment_variables(self, deployment_service: DeploymentService) -> None:
        """Track variables declared in the deployment's environment file."""
        try:
            env_service = deployment_service.get_environment_service()
        except Exception:
            return
        if env_service is None or env_service.model is None:
            return
        variables = env_service.model.spec.variables if env_service.model.spec else None
        if not variables:
            return
        env_name = env_service.get_name() or "environment"
        for variable in variables:
            self._track_variable(
                key=variable.key,
                description=variable.description or f"Variable from {env_name} ({variable.store.value})",
                used_by=[env_name],
                store=variable.store.value,
                value=variable.value,
            )

    def _collect_environment_secrets(self, deployment_service: DeploymentService) -> None:
        """Track secrets declared in the deployment's environment file."""
        try:
            env_service = deployment_service.get_environment_service()
        except Exception:
            return
        if env_service is None or env_service.model is None:
            return
        secrets = env_service.model.spec.secrets if env_service.model.spec else None
        if not secrets:
            return
        env_name = env_service.get_name() or "environment"
        for secret in secrets:
            self._track_secret(
                key=secret.key,
                description=secret.description or f"Secret from {env_name} ({secret.store.value})",
                used_by=[env_name],
            )

    def _track_secret(self, key: str, description: str, used_by: List[str]) -> None:
        if key not in self.secret_refs:
            self.secret_refs[key] = {
                "key": key,
                "description": description,
                "required": True,
                "suggested_env_var": f"TF_VAR_{key}",
                "suggested_key_vault_name": key.replace("_", "-"),
                "used_by": list(used_by),
            }
        else:
            existing = self.secret_refs[key]
            merged = set(existing.get("used_by", [])) | set(used_by)
            existing["used_by"] = sorted(merged)
