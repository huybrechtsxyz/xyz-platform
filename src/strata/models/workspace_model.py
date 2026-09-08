#!/usr/bin/env python3
"""Pydantic models for workspace configuration validation."""

from typing import Annotated, Any, Dict, List, Literal, Optional

from pydantic import (
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from strata.models.common_models import (
    CommonLifecycleModel,
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    ProvisionerType,
    SourceModel,
    check_unique_names,
    validate_slot_type,
)

# ---------------------------------------------------------------------------
# Build output profile models
# ---------------------------------------------------------------------------

EmitCategory = Literal[
    # Two-tier: constant/env at build-time; all stores at deploy-time via ResolvedValues
    "features",
    "variables",
    # Build-time only — from platform.json structural model
    "properties",
    "custom",
    "workspace",
    "providers",
    "topologies",
    "modules",
    "namespaces",
    "firewalls",
    "dns",
    "networks",
    "resources",
    "tenant",
    # NOTE: "secrets" is intentionally absent — secrets are never written to disk.
]


class OutputFileSourceModel(PlatformBaseModel):
    """One variable entry within a multi-source file definition (sources[])."""

    variable: str = Field(description="Top-level TF variable name for this entry")
    source: Literal["properties", "custom"] = Field(description="Source dict to look in")
    key: str = Field(description="Key within the source dict to extract")
    type: Literal["object", "map", "list"] = Field(default="object")


class OutputFileModel(PlatformBaseModel):
    """Single custom output file produced by the Terraform builder."""

    name: str = Field(description="Output filename (e.g. environment_info.auto.tfvars.json)")

    # Single-source mode
    variable: Optional[str] = Field(None, description="Top-level TF variable name to wrap the data in")
    type: Literal["object", "map", "list", "flat", "script"] = Field(default="object")
    source: Optional[Literal["properties", "custom"]] = Field(None)
    key: Optional[str] = Field(None, description="Key within the source dict to extract")

    # Multi-source mode — mutually exclusive with variable/source/key/script
    sources: Optional[List[OutputFileSourceModel]] = Field(
        None, description="Multiple variable sources merged into one file"
    )

    # Script mode — supports @repo_name/path resolution
    script: Optional[str] = Field(None, description="Path to script (required when type=script)")

    @model_validator(mode="after")
    def validate_file_mode(self) -> "OutputFileModel":
        has_sources = bool(self.sources)
        has_single = bool(self.source or self.variable)
        has_script = bool(self.script) or self.type == "script"
        if sum([has_sources, has_single, has_script]) > 1:
            raise ValueError("sources[], variable/source/key, and script are mutually exclusive")
        if self.type == "script" and not self.script:
            raise ValueError("'script' path is required when type is 'script'")
        if self.source and not self.key:
            raise ValueError("'key' is required when 'source' is set")
        if self.sources:
            for i, entry in enumerate(self.sources):
                if not entry.variable or not entry.source or not entry.key:
                    raise ValueError(f"sources[{i}] requires variable, source, and key")
        return self


class OutputProfileModel(PlatformBaseModel):
    """Build output profile for a Terraform provisioner.

    Controls what tfvars files are emitted by ``strata build run``.
    Defaults to ``format: strata`` (current behaviour) when absent.
    """

    format: Literal["strata", "custom", "script", "none"] = Field(
        default="strata",
        description=(
            "strata=emit all built-in files (default); "
            "custom=emit only emits[]+files[]; "
            "script=one user script owns all output; "
            "none=source files copied, no tfvars"
        ),
    )
    script: Optional[str] = Field(
        None,
        description="Path to build script (required when format=script). Supports @repo_name/path.",
    )
    emits: Optional[List[EmitCategory]] = Field(
        None,
        description=(
            "Explicit list of categories to emit. Omit to use format defaults: strata=all, custom/script/none=none."
        ),
    )
    files: Optional[List[OutputFileModel]] = Field(None, description="Custom file definitions")

    def should_emit(self, category: str) -> bool:
        """Return True when *category* should be emitted according to this profile.

        "secrets" always returns False — secrets are never written to disk.
        """
        if category == "secrets":
            return False
        if self.emits is not None:
            return category in self.emits
        return self.format == "strata"

    @model_validator(mode="after")
    def validate_script_format(self) -> "OutputProfileModel":
        if self.format == "script" and not self.script:
            raise ValueError("'script' path is required when format is 'script'")
        if self.format != "script" and self.script:
            raise ValueError("'script' is only valid when format is 'script'")
        if self.format == "none" and (self.files or self.emits):
            raise ValueError("emits and files[] have no effect when format is 'none'")
        return self


class WorkspaceNamespaceModel(PlatformBaseModel):
    """Model for a workspace namespace."""

    name: PlatformName = Field(description="Unique namespace name")
    file: str = Field(description="File reference for the namespace configuration")


class WorkspaceFirewallModel(PlatformBaseModel):
    """Model for a workspace firewall."""

    name: PlatformName = Field(description="Unique firewall name")
    file: str = Field(description="File reference for the firewall configuration")


class WorkspaceDnsModel(PlatformBaseModel):
    """Model for a workspace DNS zone configuration reference."""

    name: PlatformName = Field(description="Unique DNS zone configuration name")
    file: str = Field(description="File reference for the DNS zone configuration")


class WorkspaceNetworkModel(PlatformBaseModel):
    """Model for a workspace network topology reference."""

    name: PlatformName = Field(description="Unique network configuration name")
    file: str = Field(description="File reference for the network topology configuration")


class WorkspaceVolumeModel(PlatformBaseModel):
    """Model for a workspace volume."""

    name: PlatformName = Field(description="Unique volume name within the topology")
    type: str = Field(
        default="local", description="Volume storage type (e.g., local, replicated, distributed, nfs, iscsi, etc.)"
    )
    size: Optional[str] = Field(None, description="Volume capacity (e.g., '10Gi', '500Mi', '1Ti')")
    mount_path: Optional[str] = Field(None, description="Mount path for the volume (e.g., '/data', '/mnt/shared')")
    access_mode: Optional[str] = Field(
        None,
        description="Volume access mode - concurrency pattern at container level (e.g., 'ReadWriteOnce', 'ReadWriteMany', 'ReadOnlyMany')",
    )
    mode: Optional[str] = Field(
        None, description="Filesystem permissions within the volume (e.g., '0755' octal, 'rw', 'ro')"
    )
    driver: Optional[str] = Field(
        None, description="Storage driver or CSI plugin (e.g., 'nfs.csi.k8s.io', 'local-path')"
    )
    configuration: Optional[Dict[str, Any]] = Field(
        None, description="Driver-specific configuration (e.g., server, share, secretRef)"
    )


class WorkspaceModuleReferenceModel(PlatformBaseModel):
    """Module reference for a resource - links code/app to infrastructure."""

    name: PlatformName = Field(description="Unique module name within this resource")
    file: str = Field(description="File reference to the module configuration (module YAML file)")
    slot_type: Optional[str] = Field(
        "main",
        description="Deployment slot type: 'main' (primary/production), 'staging', 'canary', 'sidecar', 'init'. Defaults to 'main'. Custom values allowed but may generate warnings.",
    )
    enabled: bool = Field(default=True, description="Whether this module is enabled/deployed")
    configuration: Optional[Dict[str, Any]] = Field(
        None, description="Module-specific configuration overrides for this workspace"
    )

    @field_validator("slot_type")
    @classmethod
    def validate_slot_type_value(cls, v: Optional[str]) -> Optional[str]:
        """Validate slot_type using common validator."""
        return validate_slot_type(v)


class WorkspaceComponentModel(PlatformBaseModel):
    """Component model - simple resource name reference."""

    resource: Annotated[
        str,
        StringConstraints(min_length=1, strip_whitespace=True),
        Field(description="Resource name reference (must match a resource defined in spec.resources)"),
    ]


class WorkspaceNamespaceReferenceModel(PlatformBaseModel):
    """Namespace reference within a topology - links a namespace to this topology."""

    namespace: Annotated[
        str,
        StringConstraints(min_length=1, strip_whitespace=True),
        Field(description="Namespace name reference (must match a namespace defined in spec.namespaces)"),
    ]


class WorkspaceTopologyModel(PlatformBaseModel):
    name: PlatformName = Field(..., description="Unique topology name")
    provider: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)] = Field(
        ..., description="Provider name used for this topology"
    )
    provisioner: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)] = Field(
        ..., description="IaC provisioner name reference (must match a provisioner defined in spec.provisioners)"
    )
    type: PlatformName = Field(..., description="Topology type (e.g., dockerswarm, kubernetes, azure-native)")
    components: Annotated[
        List[WorkspaceComponentModel],
        Field(min_length=1, description="Topology components"),
    ]
    namespaces: Optional[List[WorkspaceNamespaceReferenceModel]] = Field(
        None, description="Namespace references deployed on this topology"
    )
    volumes: Optional[List[WorkspaceVolumeModel]] = Field(None, description="Topology volumes")

    @model_validator(mode="after")
    def validate_unique_names_within_topology(self) -> "WorkspaceTopologyModel":
        """Validate unique component, namespace, and volume names within this topology."""
        errors = []

        if self.components:
            try:
                check_unique_names(
                    [comp.resource for comp in self.components], f"resource references in topology '{self.name}'"
                )
            except ValueError as e:
                errors.append(str(e))

        if self.namespaces:
            try:
                check_unique_names(
                    [ns.namespace for ns in self.namespaces], f"namespace references in topology '{self.name}'"
                )
            except ValueError as e:
                errors.append(str(e))

        if self.volumes:
            try:
                check_unique_names([vol.name for vol in self.volumes], f"volume names in topology '{self.name}'")
            except ValueError as e:
                errors.append(str(e))

        if errors:
            raise ValueError("; ".join(errors))

        return self


class WorkspaceResourceModel(PlatformBaseModel):
    """Model for workspace resource definition (gluing layer)."""

    name: PlatformName = Field(description="Unique resource name")
    file: Optional[str] = Field(
        None,
        description="Path to the resource configuration file. Required unless managed_by is set.",
    )
    managed_by: Optional[Literal["provisioner"]] = Field(
        default=None,
        description="Indicates the resource is fully managed externally and no resource file is needed. "
        "Currently supported: 'provisioner' (resource details defined in Terraform/Ansible).",
    )
    description: Optional[str] = Field(
        None,
        description="Optional description of the resource for documentation purposes",
    )

    # Conditional inclusion
    enabled: bool = Field(
        default=True,
        description="Whether this resource is enabled/deployed in this workspace",
    )
    condition: Optional[str] = Field(
        None,
        description="Conditional expression for resource inclusion (e.g., '{{ environment }} == production')",
    )

    # Resource metadata
    role: Optional[PlatformName] = Field(None, description="Role of the resource (e.g., networking, database, api)")
    count: Annotated[
        int,
        Field(
            ge=1,
            le=100,
            description="Number of resource instances (must be greater than 0)",
        ),
    ] = 1

    # Dependencies and references
    depends_on: Optional[List[str]] = Field(
        None,
        description="List of resource names this resource depends on (workspace-specific gluing)",
    )

    @field_validator("depends_on", mode="before")
    @classmethod
    def coerce_depends_on(cls, v):
        if isinstance(v, str):
            return [v]
        return v

    references: Optional[Dict[str, str]] = Field(
        None,
        description="Cross-resource value references (e.g., {'storage_connection': 'contoso_storage.connection_string'})",
    )
    firewalls: Optional[List[str]] = Field(
        None,
        description="References to firewall/NSG resource names for network security",
    )
    subnet: Optional[str] = Field(
        None,
        description="Subnet reference in qualified format 'network_name/subnet_name'",
    )

    # Configuration overrides
    configuration: Optional[Dict[str, Any]] = Field(
        None,
        description="Workspace-specific configuration overrides (merged with resource file configuration)",
    )
    custom: Optional[Dict[str, Any]] = Field(
        None,
        description="Optional additional properties for the resource (key-value pairs)",
    )

    # Module references (code/apps that run on this resource)
    modules: Optional[List[WorkspaceModuleReferenceModel]] = Field(
        None,
        description="Optional module references (code/apps) - links apps to infrastructure (e.g., web app code on Azure Web App, function code on Function App, containers on AKS)",
    )

    # Metadata
    labels: Optional[Dict[str, Any]] = Field(
        None,
        description="Optional labels (key-value pairs for classification/filtering)",
    )
    tags: Optional[List[Any]] = Field(None, description="Optional tags (list of values for categorization)")

    @model_validator(mode="after")
    def validate_file_or_managed_by(self) -> "WorkspaceResourceModel":
        """Ensure resource has either a file reference or a managed_by declaration."""
        if not self.file and not self.managed_by:
            raise ValueError(
                f"Resource '{self.name}' must either specify a 'file' path or set 'managed_by: provisioner'. "
                "If the provisioner (Terraform/Ansible) fully manages this resource, set managed_by: provisioner."
            )
        if self.file and self.managed_by:
            raise ValueError(
                f"Resource '{self.name}' cannot both specify a 'file' and 'managed_by'. "
                "Remove the file reference or remove managed_by."
            )
        return self


class WorkspaceIacBackendModel(PlatformBaseModel):
    """Model for IaC backend configuration (state storage)."""

    type: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)] = Field(
        description="Backend type (e.g., 'terraform_cloud', 's3', 'azurerm', 'gcs', 'local', 'remote')"
    )
    configuration: Dict[str, Any] = Field(
        description="Backend-specific configuration (supports either a constant value or references like ${var:tf_org}, ${secret:tf_token}, ${feature:enable_encryption})"
    )


class WorkspaceIacAnsiblePropertiesModel(PlatformBaseModel):
    """Typed properties for an Ansible provisioner entry."""

    playbook: Optional[str] = Field(
        None,
        description="Playbook file to run, relative to the playbook directory (default: site.yml)",
    )
    inventory: Optional[str] = Field(
        None,
        description="Static inventory file path, relative to the playbook directory",
    )
    ssh_private_key_secret: Optional[str] = Field(
        None,
        description="Name of the secret holding the SSH private key (default: ssh_private_key)",
    )
    extra_vars: Optional[Dict[str, str]] = Field(
        None,
        description="Extra variables passed to ansible-playbook via --extra-vars",
    )


class ProvisionerInputMappingModel(PlatformBaseModel):
    """Maps outputs from an upstream provisioner to inputs of this provisioner."""

    provisioner: PlatformName = Field(description="Name of the upstream provisioner whose outputs to consume")
    mapping: Optional[Dict[str, str]] = Field(
        None,
        description=(
            "Optional output-to-input name mapping. Keys are upstream output names, "
            "values are downstream variable names. When omitted, outputs are passed "
            "through with their original names."
        ),
    )
    prefix: Optional[str] = Field(
        None,
        description=(
            "Optional prefix to add to all output names when injecting as inputs. "
            "Mutually exclusive with 'mapping'. E.g., prefix='baseline_' turns "
            "'vnet_id' into 'baseline_vnet_id'."
        ),
    )
    select: Optional[List[str]] = Field(
        None,
        description=(
            "Optional allowlist of output names to pass. When set, only these "
            "outputs are forwarded. When omitted, all non-sensitive outputs pass."
        ),
    )

    @model_validator(mode="after")
    def validate_mapping_prefix_exclusive(self) -> "ProvisionerInputMappingModel":
        """Ensure mapping and prefix are not both set."""
        if self.mapping and self.prefix:
            raise ValueError("'mapping' and 'prefix' are mutually exclusive on inputs_from")
        return self


class WorkspaceIacModel(PlatformBaseModel):
    name: PlatformName
    description: Optional[str] = Field(
        None,
        description="Optional description of the provisioner for documentation purposes",
    )
    provisioner: str = Field(
        ...,
        description=(
            "IaC tool used for provisioning. Built-in types: terraform, ansible, "
            "compose, helm, script, argocd, flux. Custom provisioner plugins are also accepted."
        ),
    )
    source: Optional[SourceModel] = Field(
        None,
        description=(
            "IaC deployment configuration (file path, variables, secrets). "
            "Required for IaC provisioner types (terraform, ansible, helm, compose, script). "
            "Optional for sync provisioner types (argocd, flux) which render from the platform artifact."
        ),
    )
    backend: Optional[WorkspaceIacBackendModel] = Field(
        None,
        description="Backend configuration for state storage (e.g., Terraform Cloud, S3, Azure Storage)",
    )
    properties: Optional[WorkspaceIacAnsiblePropertiesModel] = Field(
        None,
        description="Provisioner-specific typed properties (currently supported: ansible)",
    )
    configuration: Optional[Dict[str, Any]] = Field(
        None,
        description="Tool-specific configuration (e.g. playbook, inventory, ssh_private_key_secret for Ansible; backend overrides for Terraform).",
    )
    output: Optional[OutputProfileModel] = Field(
        None,
        description=(
            "Build output profile for Terraform provisioners. "
            "Controls what tfvars files are emitted by 'strata build run'. "
            "Defaults to format=strata (current behaviour) when absent."
        ),
    )
    version: Optional[str] = Field(
        None,
        description=(
            "Pinned tool version for this provisioner. "
            "Set by 'strata versions' when a type:tool pin targets this provisioner's name. "
            "Used by build/deploy to select the exact tool version (e.g. Terraform, Ansible)."
        ),
    )
    inputs_from: Optional[List[ProvisionerInputMappingModel]] = Field(
        None,
        description=(
            "Declare dependencies on other provisioners' outputs. Outputs from the "
            "named provisioners are injected as variables into this provisioner at deploy time."
        ),
    )

    @model_validator(mode="after")
    def validate_provisioner_fields(self) -> "WorkspaceIacModel":
        """Validate provisioner-type-specific field constraints."""
        from strata.models.common_models import _SYNC_PROVISIONER_TYPES

        # NOTE: str(t) here would NOT produce "argocd"/"flux" — ProvisionerType(str, Enum)'s
        # default __str__ returns "ProvisionerType.ARGOCD" (the enum repr), not t.value. That
        # previously made this set contain values no real provisioner string could ever match,
        # so is_sync was always False and source was wrongly required even for argocd/flux.
        is_sync = self.provisioner in {t.value for t in _SYNC_PROVISIONER_TYPES}

        # source is required for all non-sync provisioner types
        if not is_sync and self.source is None:
            raise ValueError(
                f"Provisioner '{self.name}': 'source' is required for provisioner type '{self.provisioner}'"
            )

        # properties is only supported for ansible
        if self.properties is not None and self.provisioner != ProvisionerType.ANSIBLE:
            raise ValueError(
                f"Provisioner '{self.name}': 'properties' is only supported for ansible provisioners "
                f"(got provisioner='{self.provisioner}')"
            )

        # backend and output are only supported for terraform (state-backend locking and
        # tfvars emission profile respectively — both read exclusively by terraform-scoped
        # code paths). Declaring either on another provisioner type previously validated
        # successfully and was then silently ignored everywhere; fail loudly instead,
        # mirroring the properties/ansible restriction above (ADR-0071).
        if self.backend is not None and self.provisioner != ProvisionerType.TERRAFORM:
            raise ValueError(
                f"Provisioner '{self.name}': 'backend' is only supported for terraform provisioners "
                f"(got provisioner='{self.provisioner}')"
            )
        if self.output is not None and self.provisioner != ProvisionerType.TERRAFORM:
            raise ValueError(
                f"Provisioner '{self.name}': 'output' is only supported for terraform provisioners "
                f"(got provisioner='{self.provisioner}')"
            )
        return self


class WorkspaceProviderModel(PlatformBaseModel):
    name: PlatformName = Field(description="Unique provider name")
    file: str = Field(description="Path to the provider configuration file")
    description: Optional[str] = Field(
        None,
        description="Optional description of the provider for documentation purposes",
    )


class WorkspaceSpecModel(PlatformBaseModel):
    """Workspace specification model."""

    lifecycle: Optional[CommonLifecycleModel] = Field(
        None,
        description="Workspace workflow lifecycle phases",
    )
    properties: Optional[Dict[str, Any]] = Field(None, description="Workspace properties")
    custom: Optional[Dict[str, Any]] = Field(None, description="Optional additional properties (key-value pairs)")
    providers: Annotated[
        List[WorkspaceProviderModel],
        Field(min_length=1, description="Provider configurations"),
    ]
    provisioners: Annotated[
        List[WorkspaceIacModel],
        Field(min_length=1, description="IaC provisioner configurations"),
    ]
    topology: Annotated[
        List[WorkspaceTopologyModel],
        Field(min_length=1, description="Workspace topology configurations"),
    ]
    resources: Optional[List[WorkspaceResourceModel]] = Field(
        None,
        description="Workspace resources with dependencies (gluing layer for topology)",
    )
    namespaces: Optional[List[WorkspaceNamespaceModel]] = Field(None, description="Workspace namespaces")
    firewalls: Optional[List[WorkspaceFirewallModel]] = Field(None, description="Workspace firewalls")
    dns_zones: Optional[List[WorkspaceDnsModel]] = Field(None, description="DNS zone file references")
    networks: Optional[List[WorkspaceNetworkModel]] = Field(None, description="Network topology file references")

    # Validate unique provider names
    @model_validator(mode="after")
    def validate_unique_providers(self) -> "WorkspaceSpecModel":
        """Validate that all provider names are unique."""
        if self.providers:
            check_unique_names([provider.name for provider in self.providers], "provider names")
        return self

    # Validate unique provisioner names
    @model_validator(mode="after")
    def validate_unique_provisioners(self) -> "WorkspaceSpecModel":
        """Validate that all provisioner names are unique."""
        if self.provisioners:
            check_unique_names([prov.name for prov in self.provisioners], "provisioner names")
        return self

    # Validate inputs_from references, self-references, and cycles
    @model_validator(mode="after")
    def validate_inputs_from(self) -> "WorkspaceSpecModel":
        """Validate inputs_from declarations across all provisioners."""
        if not self.provisioners:
            return self

        provisioner_names = {p.name for p in self.provisioners}
        errors: list = []

        # Build dependency graph for cycle detection
        graph: dict = {str(p.name): set() for p in self.provisioners}

        for prov in self.provisioners:
            if not prov.inputs_from:
                continue
            for inp in prov.inputs_from:
                # Reference to unknown provisioner
                if inp.provisioner not in provisioner_names:
                    errors.append(
                        f"Provisioner '{prov.name}': inputs_from references unknown provisioner '{inp.provisioner}'"
                    )
                # Self-reference
                elif inp.provisioner == prov.name:
                    errors.append(f"Provisioner '{prov.name}' cannot reference itself in inputs_from")
                else:
                    graph[str(prov.name)].add(str(inp.provisioner))

        # Cycle detection via topological sort (Kahn's algorithm)
        if not errors:
            # reverse_in tracks, per node, how many other nodes it is depended on by
            # (i.e. how many nodes must be processed before this one can be considered "free").
            reverse_in: dict = {node: 0 for node in graph}
            for node, deps in graph.items():
                reverse_in[node] = len(deps)

            queue = [n for n, d in reverse_in.items() if d == 0]
            visited = 0
            while queue:
                current = queue.pop(0)
                visited += 1
                # Find nodes that depend on current and reduce their count
                for node, deps in graph.items():
                    if current in deps:
                        reverse_in[node] -= 1
                        if reverse_in[node] == 0:
                            queue.append(node)

            if visited < len(graph):
                cycle_nodes = [n for n, d in reverse_in.items() if d > 0]
                errors.append(f"Circular dependency in inputs_from: {' → '.join(sorted(cycle_nodes))}")

        if errors:
            raise ValueError("; ".join(errors))

        return self

    # Validate unique topology names
    @model_validator(mode="after")
    def validate_unique_topologies(self) -> "WorkspaceSpecModel":
        """Validate that all topology names are unique."""
        if self.topology:
            check_unique_names([topo.name for topo in self.topology], "topology names")
        return self

    # Validate unique namespace names
    @model_validator(mode="after")
    def validate_unique_namespaces(self) -> "WorkspaceSpecModel":
        """Validate that all namespace names are unique."""
        if self.namespaces:
            check_unique_names([ns.name for ns in self.namespaces], "namespace names")
        return self

    # Validate unique firewall names
    @model_validator(mode="after")
    def validate_unique_firewalls(self) -> "WorkspaceSpecModel":
        """Validate that all firewall names are unique."""
        if self.firewalls:
            check_unique_names([fw.name for fw in self.firewalls], "firewall names")
        return self

    # Validate unique DNS zone names
    @model_validator(mode="after")
    def validate_unique_dns_zones(self) -> "WorkspaceSpecModel":
        """Validate that all DNS zone configuration names are unique."""
        if self.dns_zones:
            check_unique_names([dz.name for dz in self.dns_zones], "DNS zone names")
        return self

    # Validate unique network names
    @model_validator(mode="after")
    def validate_unique_networks(self) -> "WorkspaceSpecModel":
        """Validate that all network configuration names are unique."""
        if self.networks:
            check_unique_names([n.name for n in self.networks], "network names")
        return self

    # Validate unique resource names
    @model_validator(mode="after")
    def validate_unique_resources(self) -> "WorkspaceSpecModel":
        """Validate that all resource names are unique."""
        if self.resources:
            check_unique_names([res.name for res in self.resources], "resource names")

            # Validate unique module names within each resource
            errors = []
            for resource in self.resources:
                if resource.modules:
                    try:
                        check_unique_names(
                            [mod.name for mod in resource.modules],
                            f"module names in resource '{resource.name}'",
                        )
                    except ValueError as e:
                        errors.append(str(e))

                    # Validate slot types: at least one 'main' if multiple modules
                    if len(resource.modules) > 1:
                        enabled_modules = [mod for mod in resource.modules if mod.enabled]
                        if enabled_modules:
                            main_slots = [mod for mod in enabled_modules if mod.slot_type == "main"]
                            if not main_slots:
                                errors.append(
                                    f"Resource '{resource.name}' has multiple enabled modules but no 'main' slot defined"
                                )
                            elif len(main_slots) > 1:
                                errors.append(
                                    f"Resource '{resource.name}' has multiple modules marked as 'main' slot: {[m.name for m in main_slots]}"
                                )

            if errors:
                raise ValueError("; ".join(errors))

        return self

    # Validate firewall references
    @model_validator(mode="after")
    def validate_firewall_references(self) -> "WorkspaceSpecModel":
        """Validate that all firewall references in resources exist in firewalls section."""
        if not self.resources:
            return self

        # Get all defined firewall names
        firewall_names = set()
        if self.firewalls:
            firewall_names = {fw.name for fw in self.firewalls}

        # Check all resource firewall references
        invalid_refs = []
        for resource in self.resources:
            if resource.firewalls:
                for firewall in resource.firewalls:
                    if firewall not in firewall_names:
                        invalid_refs.append(f"Resource '{resource.name}' references undefined firewall '{firewall}'")

        if invalid_refs:
            raise ValueError(f"Invalid firewall references found: {'; '.join(invalid_refs)}")

        return self

    # Validate topology component resource references
    @model_validator(mode="after")
    def validate_topology_resource_references(self) -> "WorkspaceSpecModel":
        """Validate that all resource references in topology exist in resources section."""
        if not self.topology:
            return self

        # Get all defined resource names
        resource_names = set()
        if self.resources:
            resource_names = {res.name for res in self.resources}

        # Check all topology component resource references
        invalid_refs = []
        for topology in self.topology:
            if not topology.components:
                continue

            for component in topology.components:
                # Check resource reference exists
                if component.resource not in resource_names:
                    invalid_refs.append(
                        f"Topology '{topology.name}' component references undefined resource '{component.resource}'"
                    )

        if invalid_refs:
            raise ValueError(f"Invalid resource references in topology: {'; '.join(invalid_refs)}")

        return self

    # Validate topology namespace references
    @model_validator(mode="after")
    def validate_topology_namespace_references(self) -> "WorkspaceSpecModel":
        """Validate that all namespace references in topology exist in namespaces section."""
        if not self.topology:
            return self

        # Get all defined namespace names
        namespace_names = set()
        if self.namespaces:
            namespace_names = {ns.name for ns in self.namespaces}

        # Check all topology namespace references
        invalid_refs = []
        for topology in self.topology:
            if not topology.namespaces:
                continue

            for ns_ref in topology.namespaces:
                if ns_ref.namespace not in namespace_names:
                    invalid_refs.append(
                        f"Topology '{topology.name}' references undefined namespace '{ns_ref.namespace}'"
                    )

        if invalid_refs:
            raise ValueError(f"Invalid namespace references in topology: {'; '.join(invalid_refs)}")

        return self

    # Validate topology provisioner name references
    @model_validator(mode="after")
    def validate_topology_provisioner_references(self) -> "WorkspaceSpecModel":
        """Validate that all provisioner references in topology exist in spec.provisioners by name."""
        if not self.topology:
            return self

        provisioner_names = {prov.name for prov in self.provisioners}

        invalid_refs = []
        for topology in self.topology:
            if topology.provisioner not in provisioner_names:
                invalid_refs.append(
                    f"Topology '{topology.name}' references undefined provisioner '{topology.provisioner}'"
                )

        if invalid_refs:
            raise ValueError(f"Invalid provisioner references in topology: {'; '.join(invalid_refs)}")

        return self


class WorkspaceMetaModel(PlatformBaseModel):
    """Model for workspace metadata (name, annotations, labels, tags)."""

    name: PlatformName = Field(description="Unique workspace name")
    annotations: Optional[Dict[str, Any]] = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: Optional[Dict[str, Any]] = Field(
        None,
        description="Optional labels (key-value pairs for classification/filtering)",
    )
    tags: Optional[List[Any]] = Field(None, description="Optional tags (list of values for categorization)")


class WorkspaceModel(PlatformBaseModel):
    """Root model for a workspace configuration file."""

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v1,
        frozen=True,
        description="API version for workspace configuration",
    )
    kind: PlatformKind = Field(
        default=PlatformKind.WORKSPACE,
        frozen=True,
        description="Platform kind (always 'Workspace')",
    )
    meta: WorkspaceMetaModel = Field(description="Workspace metadata (name, annotations, labels, tags)")
    spec: WorkspaceSpecModel = Field(
        description="Workspace specification (properties, topology, providers, IaC, lifecycle)"
    )
