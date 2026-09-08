#!/usr/bin/env python3
"""Pydantic model for provider and resource configuration validation."""

import re
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import ConfigDict, Field, field_validator, model_validator

from strata.models.audit_config_model import AuditConfigModel, RepositoryPushModel
from strata.models.common_models import (
    CommonLifecycleModel,
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    check_unique_names,
)
from strata.models.expression_model import ExpressionKind, ExpressionModel
from strata.models.integration_model import IntegrationModel
from strata.models.policy_model import PolicyModel
from strata.models.promotion_model import ConfigurationPromotionsModel
from strata.models.repository_model import RemoteModel
from strata.utils.config import SOLUTION_DEPLOYMENTS_DIR, SOLUTION_DIR, SOLUTION_OUTPUTS_DIR


class ConfigurationSecurityModel(PlatformBaseModel):
    """Model for security policies and constraints."""

    allowed_secret_stores: Optional[List[str]] = Field(
        None,
        description="Allowed secret store types. If None, all stores are allowed (dev mode). Valid values: constant, environment, github, azure-keyvault, bitwarden, vault, infisical. For production, restrict to secure stores only. 'github' and 'environment' are pipeline-trust stores (resolved from environment variables) — add them explicitly to permit use.",
    )
    allowed_variable_stores: Optional[List[str]] = Field(
        None,
        description="Allowed variable store types. If None, all stores are allowed.",
    )
    allowed_feature_stores: Optional[List[str]] = Field(
        None,
        description="Allowed feature store types. If None, all stores are allowed.",
    )


class ConfigurationComponentModel(PlatformBaseModel):
    """Model for a topology component configuration."""

    role: PlatformName = Field(..., description="Unique role for the topology component.")
    description: Optional[str] = Field(None, description="Description of the topology component.")
    uses_module: Optional[bool] = Field(False, description="Indicates if the component requires a module.")
    is_control: Optional[bool] = Field(False, description="Indicates if the component is a control or manager element.")
    required: Optional[bool] = Field(True, description="Indicates if the component is required in the topology.")
    min_count: Optional[int] = Field(0, description="Minimum number of instances for the component.")
    max_count: Optional[int] = Field(0, description="Maximum number of instances for the component (0 = unlimited).")

    @field_validator("min_count")
    @classmethod
    def validate_min_count(cls, v: Optional[int]) -> Optional[int]:
        """Validate min_count is >= 0."""
        if v is not None and v < 0:
            raise ValueError("min_count must be >= 0")
        return v

    @field_validator("max_count")
    @classmethod
    def validate_max_count(cls, v: Optional[int]) -> Optional[int]:
        """Validate max_count is >= 0."""
        if v is not None and v < 0:
            raise ValueError("max_count must be >= 0")
        return v

    @model_validator(mode="after")
    def validate_count_relationship(self) -> "ConfigurationComponentModel":
        """Validate max_count >= min_count."""
        if self.min_count is not None and self.max_count is not None:
            # 0 means unlimited
            if self.max_count != 0 and self.max_count < self.min_count:
                raise ValueError(f"max_count ({self.max_count}) must be >= min_count ({self.min_count})")
        return self


class ConfigurationTopologyModel(PlatformBaseModel):
    """Model for a provider topology configuration."""

    type: PlatformName = Field(..., description="Unique type definition for the topology configuration.")
    description: Optional[str] = Field(None, description="Description of the topology configuration.")
    additional_components: bool = Field(
        False,
        description="Allow components not listed in the configuration for this topology",
    )
    components: Optional[List[ConfigurationComponentModel]] = Field(
        None, description="Topology-specific component configurations."
    )

    @model_validator(mode="after")
    def validate_unique_component_roles(self) -> "ConfigurationTopologyModel":
        """Validate that all component roles are unique within this topology."""
        if self.components:
            check_unique_names([comp.role for comp in self.components], f"component roles in topology '{self.type}'")
        return self


class ConfigurationSchemaField(PlatformBaseModel):
    """Model for a configuration schema field with pattern and required flag.

    All field values are validated as strings against the ``pattern`` regex.
    There is no native boolean type — use ``pattern: "^(true|false)$"`` and
    pass ``"true"`` or ``"false"`` as the value in resource configuration.
    """

    pattern: str = Field(..., description="Regex pattern that field values must match")
    required: bool = Field(True, description="Whether this field is required in resource configuration")
    description: Optional[str] = Field(None, description="Description of what this field represents")


class ConfigurationProviderResourceModel(PlatformBaseModel):
    """Model for a provider resource configuration."""

    name: PlatformName = Field(..., description="Unique name for the configuration resource.")
    category: Optional[str] = Field(None, description="Resource category (e.g., compute, storage, networking)")
    subcategory: Optional[str] = Field(
        None,
        description="Resource subcategory (e.g., virtualmachine, blob, api_gateway)",
    )
    description: Optional[str] = Field(None, description="Description of the resource type")
    additional_configurations: bool = Field(
        False,
        description="Allow configuration fields not listed in the schema for this resource",
    )
    configuration: Optional[Dict[str, Union[str, ConfigurationSchemaField]]] = Field(
        None,
        description="Resource-specific configuration schema (pattern string or structured field)",
    )


class ConfigurationProviderModel(PlatformBaseModel):
    """Model for a provider configuration."""

    name: PlatformName = Field(..., description="Provider name (e.g., kamatera, azure)")
    description: str = Field(..., description="Description of the provider")
    version: Optional[str] = Field(None, description="Provider version constraint (e.g., ~>3.0, >=1.0)")
    additional_regions: bool = Field(
        False,
        description="Allow regions not listed in the configuration for this provider",
    )
    regions: Optional[List[Union[str, Dict[str, Any]]]] = Field(
        None, description="List of supported regions for this provider"
    )
    additional_resources: bool = Field(
        False,
        description="Allow resource types not listed in the configuration for this provider",
    )
    resources: Optional[List[ConfigurationProviderResourceModel]] = Field(
        None, description="List of supported resource types for this provider"
    )

    @model_validator(mode="after")
    def validate_provider_configuration(self) -> "ConfigurationProviderModel":
        """Validate provider configuration requirements and uniqueness."""
        # Validate that lists are provided when additional_* is False
        if not self.additional_regions and (self.regions is None or len(self.regions) == 0):
            raise ValueError("If additional_regions is False, regions must be provided and non-empty")
        if not self.additional_resources and (self.resources is None or len(self.resources) == 0):
            raise ValueError("If additional_resources is False, resources must be provided and non-empty")

        # Validate unique region names
        if self.regions:
            region_names = []
            for region in self.regions:
                if isinstance(region, dict) and "name" in region:
                    region_names.append(region["name"])
                elif isinstance(region, str):
                    region_names.append(region)
            check_unique_names(region_names, f"regions in provider '{self.name}'")

        # Validate unique resource names
        if self.resources:
            check_unique_names([res.name for res in self.resources], f"resources in provider '{self.name}'")

        return self


class ConfigurationLayerModel(PlatformBaseModel):
    """Definition of a single layer/segment in the deployment hierarchy (ADR-0072).

    Used inline by ``PathConventionModel.segments`` (``resolves: layers``
    conventions). There is no ``required`` flag — a segment absent from both the
    deployment's explicit ``spec.layers.segments`` and its own path is simply
    "not applicable" to that deployment, not a validation error (different
    deployments in the same hierarchy family legitimately have different real
    depths).
    """

    name: PlatformName = Field(description="Layer name (must be valid identifier: lowercase, alphanumeric, hyphens)")
    description: Optional[str] = Field(None, description="Human-readable description of this layer's purpose")
    pattern: Optional[str] = Field(
        None,
        description="Regex pattern for validating layer values (e.g., '^[a-z][a-z0-9\\-]*$')",
    )
    default: Optional[str] = Field(
        None,
        description="Default value used when not provided explicitly or derived from the deployment's path",
    )


class PathConventionModel(PlatformBaseModel):
    """A path convention rule for directory structure validation.

    Declared in ``spec.paths`` on the configuration model.  Each entry targets a
    subtree via a ``scope`` glob and defines the expected directory structure via a
    ``pattern`` with ``{segment}`` captures.  Optional ``validate`` rules check each
    captured segment value against a model field (via JMESPath) or a file existence
    constraint — see ``strata.models.expression_model.ExpressionModel`` (ADR-0073):
    an explicit ``kind:`` discriminator, not a shape-sniffed string.

    Example::

        paths:
          - name: zone-deployment-tree
            scope: "zones/**"
            pattern: "zones/{zone}/customers/{tenant}/{env}"
            validate:
              zone:
                kind: yaml
                expression: spec.zones[*].name
              tenant:
                kind: path
                expression: "customers/{tenant}/tenant.yaml"
    """

    name: PlatformName = Field(description="Unique convention name for diagnostics and policy filtering")
    scope: str = Field(
        description=(
            "Glob pattern — only files whose relative path (from work_path) matches "
            "this scope are candidates for this convention."
        )
    )
    pattern: str = Field(
        description=(
            "Path template with {segment} captures, anchored at work_path root. "
            "Each {segment} captures exactly one path part (no '/'). "
            "Literal segments must match verbatim. "
            "Trailing path parts after the pattern are ignored."
        )
    )
    rules: Optional[Dict[str, ExpressionModel]] = Field(
        None,
        alias="validate",
        description=(
            "Per-segment validation rules. Keys must match {segment} names in pattern. "
            "Each value is an ExpressionModel: kind=yaml for a JMESPath query against the "
            "configuration model (e.g. 'spec.zones[*].name') checking the segment value is "
            "a member of the result, or kind=path for a {segment}-templated file-existence "
            "check (e.g. 'customers/{tenant}/tenant.yaml')."
        ),
    )
    resolves: Optional[Literal["tenant", "layers"]] = Field(
        None,
        description=(
            "When set to 'tenant', this convention ALSO drives tenant file resolution "
            "(not just validation) — deployment_service/platform_builder substitute the "
            "deployment's tenant code into this pattern's {code} segment instead of the "
            "built-in tenants/{code}.yaml default. The pattern MUST contain a {code} segment. "
            "At most one convention across spec.paths may declare resolves: tenant. "
            "When set to 'layers', this convention ALSO drives deployment.spec.layers "
            "resolution (ADR-0072) — see the 'segments' field."
        ),
    )
    segments: Optional[List[ConfigurationLayerModel]] = Field(
        None,
        description=(
            "Inline segment definitions for this hierarchy family — only meaningful when "
            "resolves: layers. One convention per family, using its deepest legitimate shape; "
            "shallower deployments within the same family resolve fewer segments, handled on "
            "the deploy side (deployment.spec.layers), not by declaring more conventions."
        ),
    )

    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode="after")
    def validate_segments_match_pattern(self) -> "PathConventionModel":
        """Validate that all keys in 'rules' correspond to {segments} in pattern."""
        if not self.rules:
            return self
        import re as _re

        pattern_segments = set(_re.findall(r"\{(\w+)\}", self.pattern))
        for key in self.rules:
            if key not in pattern_segments:
                raise ValueError(
                    f"Validation key '{key}' does not correspond to a {{segment}} "
                    f"in pattern '{self.pattern}'. Available segments: {sorted(pattern_segments)}"
                )
        return self

    @model_validator(mode="after")
    def validate_rules_kind(self) -> "PathConventionModel":
        """'validate' rules only support kind=yaml (spec membership) or kind=path
        (file existence) — ADR-0073's ``ExpressionModel`` also defines kind=regex/jinja
        for other, not-yet-wired call sites, which don't apply here."""
        if not self.rules:
            return self
        for key, expr in self.rules.items():
            if expr.kind not in (ExpressionKind.YAML, ExpressionKind.PATH):
                raise ValueError(
                    f"Convention '{self.name}': validate.{key} has kind={expr.kind.value}, "
                    "but path-convention rules only support kind=yaml or kind=path."
                )
        return self

    @model_validator(mode="after")
    def validate_segments_only_with_resolves_layers(self) -> "PathConventionModel":
        """'segments' is only meaningful when resolves == 'layers'."""
        if self.segments and self.resolves != "layers":
            raise ValueError(
                f"Convention '{self.name}' declares 'segments' but resolves is "
                f"'{self.resolves}', not 'layers'. 'segments' only applies to "
                "resolves: layers conventions."
            )
        return self

    @model_validator(mode="after")
    def validate_unique_segment_names(self) -> "PathConventionModel":
        """Validate that segment names within this convention are unique."""
        if self.segments:
            check_unique_names([seg.name for seg in self.segments], f"segment names in convention '{self.name}'")
        return self

    @model_validator(mode="after")
    def validate_resolves_tenant_has_code_segment(self) -> "PathConventionModel":
        """A convention marked resolves: tenant must have a {code} segment in its pattern."""
        if self.resolves != "tenant":
            return self
        import re as _re

        pattern_segments = set(_re.findall(r"\{(\w+)\}", self.pattern))
        if "code" not in pattern_segments:
            raise ValueError(
                f"Convention '{self.name}' declares resolves: tenant but its pattern "
                f"'{self.pattern}' has no {{code}} segment. Tenant resolution substitutes "
                "the deployment's spec.tenant value into a {code} segment."
            )
        return self


class ConfigurationLoggingModel(PlatformBaseModel):
    """Model for logging configuration."""

    file: Optional[str] = Field(
        None,
        description="Path to logging configuration YAML file (relative to workspace root or absolute)",
    )


class ConfigurationManifestModel(PlatformBaseModel):
    """Configuration for deployment manifest storage.

    Controls where and how deployment manifests are persisted after each
    deploy run.  When omitted from the configuration, manifests are not
    written and a log message is emitted.

    Path structure (auto-appended by the service)::

        {path}/{deployment_name}/{version}/{timestamp}.json

    Example (local only):
        manifest:
          path: ".strata/deployments"

    Example (local + durable git-push, ADR-0065 Phase 1):
        manifest:
          path: "deployments"
          push_manifest: true
          repository:
            push: true
            name: "state-repo"
            path: "history/manifest"
    """

    path: str = Field(
        default=f"{SOLUTION_DIR}/{SOLUTION_DEPLOYMENTS_DIR}",
        description="Base path for manifests. Service appends /{deployment_name}/{version}/{timestamp}.json",
    )
    push_manifest: bool = Field(
        default=False,
        description="Commit and push the written manifest file to the git remote after writing.",
    )
    repository: Optional[RepositoryPushModel] = Field(
        default=None,
        description=(
            "Durable git-push destination for the manifest (ADR-0065 Phase 1). "
            "When omitted but push_manifest is true, pushes to this workspace's own repo — "
            "unchanged from previous behaviour. Set repository.name to push to a named "
            "solution repo instead."
        ),
    )


class SensitiveOutputHandling(str, Enum):
    """How to handle Terraform outputs marked ``sensitive = true``."""

    REDACT = "redact"
    """Replace the value with the string ``(sensitive)`` — key remains visible."""

    OMIT = "omit"
    """Drop the key entirely from the stored artifact."""


class ConfigurationOutputsModel(PlatformBaseModel):
    """Configuration for Terraform output artifact storage.

    Controls whether and how Terraform output values are persisted after a
    successful deploy.  Stored outputs never include sensitive values as
    plain text; the ``sensitive`` field controls whether sensitive keys are
    redacted (value replaced with ``"(sensitive)"``) or omitted entirely.

    Path structure (auto-appended by the deployer)::

        {path}/{deployment_name}/{version}/{stage}.json

    Example:
        outputs:
          enabled: true
          path: ".strata/outputs"
          sensitive: redact
    """

    enabled: bool = Field(
        default=True,
        description="Write output artifacts after a successful deploy. Set to false to disable.",
    )
    path: str = Field(
        default=f"{SOLUTION_DIR}/{SOLUTION_OUTPUTS_DIR}",
        description=("Base path for output artifacts. The deployer appends /{deployment_name}/{version}/{stage}.json"),
    )
    sensitive: SensitiveOutputHandling = Field(
        default=SensitiveOutputHandling.REDACT,
        description=(
            "How to handle outputs marked sensitive=true in Terraform: "
            "'redact' (store key with value '(sensitive)') or 'omit' (drop the key entirely)."
        ),
    )


class ConfigurationDeploymentModel(PlatformBaseModel):
    """Model for deployment configuration and schema definition.

    Defines required and optional properties that deployments must provide,
    similar to how ConfigurationProviderResourceModel defines resource configuration schemas.

    Example:
        deployment:
          properties:
            additional_properties: false
            properties:
              environment:
                pattern: "^(dev|test|staging|prod)$"
                required: true
                description: "Deployment environment"
              tenant:
                pattern: "^[a-z][a-z0-9-]*$"
                required: true
                description: "tenant identifier"
              region:
                pattern: "^[a-z]{2}-[a-z]+(-[0-9]+)?$"
                required: false
                description: "Deployment region"
          manifest:
            path: ".strata/deployments"
          outputs:
            enabled: true
            path: ".strata/outputs"
            sensitive: redact
    """

    additional_properties: bool = Field(
        False,
        description="Allow properties not listed in the schema for deployments",
    )
    properties: Optional[Dict[str, Union[str, ConfigurationSchemaField]]] = Field(
        None,
        description="Deployment properties schema (pattern string or structured field with validation)",
    )
    manifest: Optional[ConfigurationManifestModel] = Field(
        None,
        description="Manifest storage configuration. When absent, manifests are not written.",
    )
    outputs: Optional[ConfigurationOutputsModel] = Field(
        None,
        description=(
            "Terraform output artifact storage. "
            "When absent, outputs are not written to a durable store "
            "(they are still available within the current deploy run for downstream stages)."
        ),
    )


class ConfigurationZoneModel(PlatformBaseModel):
    """A logical zone grouping one or more provider regions.

    Zones are used to enforce data residency constraints — a tenant
    restricted to zone 'eu' may only be deployed to providers whose
    region is in that zone's regions list.
    """

    name: PlatformName = Field(..., description="Unique zone name (e.g., 'eu', 'us', 'apac')")
    description: Optional[str] = Field(None, description="Human-readable description of this zone")
    regions: List[str] = Field(
        ...,
        min_length=1,
        description="Provider regions belonging to this zone (e.g., 'westeurope', 'northeurope')",
    )


class CostAlertConfigModel(PlatformBaseModel):
    """Threshold configuration for ``cost.threshold_exceeded`` alerts (ADR-0066 follow-up).

    Either condition fires the alert; both are optional and independent.
    """

    max_monthly: Optional[float] = Field(
        default=None,
        description=(
            "Fire an alert if the recorded total_monthly exceeds this value. "
            "Same name and meaning as CostThresholdPolicy's max_monthly — deliberately "
            "reusing that vocabulary rather than inventing a second one."
        ),
    )
    delta_percent: Optional[float] = Field(
        default=None,
        description=(
            "Fire an alert if total_monthly increased by at least this percent since the "
            "previous snapshot. A decrease never fires this condition."
        ),
    )


class CostHistoryConfigModel(PlatformBaseModel):
    """Configuration for cost-history durable storage (ADR-0065 Phase 1).

    The local history file itself (``.strata/cost/{deployment}.cost-history.json``)
    is fixed and not configurable — only the optional durable git-push destination is.
    """

    repository: Optional[RepositoryPushModel] = Field(
        default=None,
        description="Durable git-push destination for cost history. Omit to skip.",
    )
    alert: Optional[CostAlertConfigModel] = Field(
        default=None,
        description="Threshold configuration for cost.threshold_exceeded alerts. Omit to skip.",
    )


class CostConfigModel(PlatformBaseModel):
    """Top-level cost configuration under spec.cost in configuration YAML."""

    history: Optional[CostHistoryConfigModel] = Field(
        default=None,
        description="Cost-history durable storage configuration.",
    )


class DriftHistoryConfigModel(PlatformBaseModel):
    """Configuration for drift-history durable storage (ADR-0065 Phase 1).

    The local history file itself (``.strata/drift/{deployment}.drift.json``)
    is fixed and not configurable — only the optional durable git-push destination is.
    """

    repository: Optional[RepositoryPushModel] = Field(
        default=None,
        description="Durable git-push destination for drift history. Omit to skip.",
    )


class DriftConfigModel(PlatformBaseModel):
    """Top-level drift configuration under spec.drift in configuration YAML."""

    history: Optional[DriftHistoryConfigModel] = Field(
        default=None,
        description="Drift-history durable storage configuration.",
    )


class ChangeTrackingConfigModel(PlatformBaseModel):
    """Top-level change-tracking configuration under spec.change_tracking (ADR-0074).

    Declares the workspace's default external change/ticket tracker once, so an
    operator only needs to supply the volatile part (the id and reason) on the
    command line. Phase 1 only — this configures capture, not enforcement.
    """

    system: Optional[str] = Field(
        default=None,
        description="Default tracker identifier used when --change-system is not supplied, e.g. 'jira', 'azure_devops', 'servicenow'.",
    )
    url_template: Optional[str] = Field(
        default=None,
        description="Template for resolving a change record's URL from its id. The literal '{id}' is substituted; no other placeholders are supported.",
    )
    id_pattern: Optional[str] = Field(
        default=None,
        description="Standard regular expression the supplied change id must match, e.g. '^[A-Z][A-Z0-9]+-[0-9]+$'.",
    )
    classifications: Optional[List[str]] = Field(
        default=None,
        description=(
            "Allowed values for --change-classification, e.g. ['emergency', 'normal', 'standard']. "
            "An open list, not a fixed strata enum — each organization defines its own scheme. "
            "When unset, any classification value is accepted."
        ),
    )

    @field_validator("id_pattern")
    @classmethod
    def validate_id_pattern_is_valid_regex(cls, value: Optional[str]) -> Optional[str]:
        """Reject an unparsable regex at load time instead of failing on the first deploy."""
        if value is None:
            return value
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"change_tracking.id_pattern is not a valid regular expression: {exc}") from exc
        return value


class ConfigurationSpecModel(PlatformBaseModel):
    """Specification for the configuration model."""

    logging: Optional[ConfigurationLoggingModel] = Field(None, description="Logging configuration for the platform")
    custom: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "Generic freeform escape hatch for structures that don't warrant a dedicated "
            "typed model (ADR-0072), same pattern as spec.configuration/spec.properties. "
            "kind=yaml validate: rules (see PathConventionModel) run JMESPath against the "
            "model's full model_dump(), so they work against this field's contents too."
        ),
    )
    paths: Optional[List[PathConventionModel]] = Field(
        None,
        description=(
            "Declared directory structure conventions for path validation policy. "
            "Each entry targets a subtree via a scope glob and defines the expected "
            "path structure with per-segment validation rules."
        ),
    )
    integrations: List[IntegrationModel] = Field(
        default_factory=list,
        description="External integrations that extend platform capabilities",
    )
    remotes: Optional[List[RemoteModel]] = Field(
        None, description="Named remote endpoints (artifact sources and deployment targets)"
    )
    lifecycle: Optional[CommonLifecycleModel] = Field(None, description="Configuration lifecycle phases")
    configuration: Optional[Dict[str, Any]] = Field(None, description="List of configuration defaults")
    properties: Optional[Dict[str, Any]] = Field(None, description="List of configuration properties")
    deployment: Optional[ConfigurationDeploymentModel] = Field(
        None,
        description="Deployment configuration and schema for deployment.spec.properties validation",
    )
    providers: Optional[List[ConfigurationProviderModel]] = Field(None, description="List of supported providers")
    additional_topologies: bool = Field(
        False,
        description="Allow topology types not listed in the configuration",
    )
    topologies: Optional[List[ConfigurationTopologyModel]] = Field(None, description="List of supported topologies")
    security: Optional[ConfigurationSecurityModel] = Field(
        None,
        description="Security policies for store types and other security constraints",
    )
    zones: Optional[List[ConfigurationZoneModel]] = Field(
        None,
        description="Logical zones grouping provider regions for data residency enforcement",
    )
    policies: Optional[List[PolicyModel]] = Field(
        default_factory=list,
        description="Policy rules evaluated at validate, build, plan, and deploy phases",
    )
    audit: Optional[AuditConfigModel] = Field(
        None,
        description="Audit and deploy-log configuration (structure, sinks, retention)",
    )
    cost: Optional[CostConfigModel] = Field(
        None,
        description="Cost configuration (currently only history durable storage, ADR-0065 Phase 1)",
    )
    drift: Optional[DriftConfigModel] = Field(
        None,
        description="Drift configuration (currently only history durable storage, ADR-0065 Phase 1)",
    )
    change_tracking: Optional[ChangeTrackingConfigModel] = Field(
        None,
        description="Default external change/ticket tracker for deploy run/destroy --change-* flags (ADR-0074 Phase 1)",
    )
    promotions: Optional[ConfigurationPromotionsModel] = Field(
        None,
        description="Promotion strategy configuration: progressions (ring sequences) and strategies (how artifacts move through rings)",
    )

    @model_validator(mode="after")
    def validate_unique_zones(self) -> "ConfigurationSpecModel":
        """Validate zone names are unique and each region appears in at most one zone."""
        if not self.zones:
            return self

        # Unique zone names
        zone_names = [z.name for z in self.zones]
        check_unique_names(zone_names, "zone names in configuration")

        # Each region must appear in at most one zone
        seen: dict[str, str] = {}
        for zone in self.zones:
            for region in zone.regions:
                if region in seen:
                    raise ValueError(
                        f"Region '{region}' is listed in both zone '{seen[region]}' and zone '{zone.name}'. "
                        "Each region must belong to exactly one zone."
                    )
                seen[region] = zone.name

        return self

    @model_validator(mode="after")
    def validate_unique_providers(self) -> "ConfigurationSpecModel":
        """Validate that all provider names are unique."""
        if self.providers:
            check_unique_names([provider.name for provider in self.providers], "provider names in configuration")
        return self

    @model_validator(mode="after")
    def validate_unique_integrations(self) -> "ConfigurationSpecModel":
        """Validate that all integration names are unique (ADR-0066).

        Integration identity (both singleton keying in ``BaseIntegration`` and
        ``sinks[].integration`` references) is the declared ``name`` — a duplicate
        previously resolved last-wins silently; it is now a validation error.
        """
        if self.integrations:
            check_unique_names([i.name for i in self.integrations], "integration names in configuration")
        return self

    @model_validator(mode="after")
    def validate_unique_topologies(self) -> "ConfigurationSpecModel":
        """Validate that all topology types are unique."""
        if self.topologies:
            check_unique_names([topo.type for topo in self.topologies], "topology types in configuration")
        return self

    @model_validator(mode="after")
    def validate_single_tenant_path_resolver(self) -> "ConfigurationSpecModel":
        """At most one spec.paths convention may declare resolves: tenant — ambiguous otherwise."""
        if not self.paths:
            return self
        resolvers = [p.name for p in self.paths if p.resolves == "tenant"]
        if len(resolvers) > 1:
            raise ValueError(
                f"Multiple spec.paths conventions declare resolves: tenant: {resolvers}. "
                "Only one convention may resolve tenant file locations."
            )
        return self

    @model_validator(mode="after")
    def validate_unique_path_convention_names(self) -> "ConfigurationSpecModel":
        """Validate that path convention names are unique."""
        if self.paths:
            check_unique_names([p.name for p in self.paths], "path convention names in configuration")
        return self


class ConfigurationMetaModel(PlatformBaseModel):
    """Metadata for the configuration model."""

    name: PlatformName = Field(..., description="Unique name for the configuration resource.")
    annotations: Optional[Dict[str, Any]] = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: Optional[Dict[str, Any]] = Field(None, description="Labels for categorization and filtering.")
    tags: Optional[List[Any]] = Field(None, description="Optional list of tags.")


class ConfigurationModel(PlatformBaseModel):
    """
    Top-level model for a configuration file.
    """

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v1,
        frozen=True,
        description="API version of the configuration model.",
    )
    kind: PlatformKind = Field(
        default=PlatformKind.CONFIGURATION,
        frozen=True,
        description="Platform kind: always 'configuration'.",
    )
    meta: ConfigurationMetaModel = Field(..., description="Metadata for the configuration model.")
    spec: ConfigurationSpecModel = Field(..., description="Specification for the configuration.")

    def get_remote_map(self) -> Dict[str, str]:
        """Return a ``{remote_name: deploy_path}`` mapping for resolving ``@remote_name/...`` references."""
        remotes = self.spec.remotes if self.spec and self.spec.remotes else {}
        if not remotes or len(remotes) == 0:
            return {}
        return {remote.name: remote.deploy_path for remote in remotes if remote.name and remote.deploy_path}
