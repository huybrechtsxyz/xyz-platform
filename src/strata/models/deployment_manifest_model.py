"""Pydantic models for the deployment manifest — the auditable output of a deploy run.

The deployment manifest captures **what was deployed**: the full platform
artifact, pinned repo commits, container images, provisioner backends, and
per-stage execution results.  It is written automatically by the deploy command
on completion (success or failure) and stored according to the manifest
configuration in the platform config file.

YAML on-disk format::

    apiVersion: strata.huybrechts.xyz/v1
    kind: deployment-manifest
    meta:
      name: prod_deployment
      labels:
        version: "2.3.0"
        environment: production
    spec:
      deployment_name: prod_deployment
      workspace_name: my_workspace
      action: deploy
      status: success
      artifacts:
        platform:
          hash: "sha256:abc123..."
          content: { ... full platform.json ... }
        repositories:
          xyz_infrastructure:
            url: "git@github.com:org/xyz-infra.git"
            ref: "v1.2.0"
            commit: "a1b2c3d4..."
        providers:
          - name: tf_hetzner
            type: terraform
            backend:
              type: azurerm
              configuration: { ... }
        images:
          - name: traefik
            image: "docker.io/traefik:v3.0.1"
      stages:
        - name: infrastructure
          status: success
          ...
"""

from typing import Any, Dict, List, Literal, Optional

from pydantic import Field

from strata.models.change_reference_model import ChangeReferenceModel
from strata.models.common_models import (
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
)
from strata.models.sbom_model import SbomReferenceModel

# ---------------------------------------------------------------------------
# Artifact sub-models
# ---------------------------------------------------------------------------


class ManifestPlatformModel(PlatformBaseModel):
    """Fingerprint and full content of the platform.json artifact deployed."""

    hash: str = Field(description="SHA-256 hash of the platform.json file")
    path: Optional[str] = Field(None, description="Relative path to platform.json in the build output")
    content: Optional[Dict[str, Any]] = Field(
        None, description="Full embedded platform.json content — the complete deployed configuration"
    )


class ManifestRepositoryModel(PlatformBaseModel):
    """Pinned version of a single source repository at deploy time."""

    url: Optional[str] = Field(None, description="Git remote URL")
    ref: Optional[str] = Field(None, description="Requested git ref (tag, branch, or commit)")
    commit: Optional[str] = Field(None, description="Resolved full commit SHA")


class ManifestArtifactImageModel(PlatformBaseModel):
    """A container image used during the deployment."""

    name: str = Field(description="Service or component name (e.g. traefik, app_backend)")
    image: str = Field(description="Full image reference including tag (e.g. docker.io/traefik:v3.0.1)")
    digest: Optional[str] = Field(None, description="Image content digest (sha256:...) — populated when available")


class ManifestArtifactProviderModel(PlatformBaseModel):
    """Provisioner entry used in the deployment — captures tool, state backend, and type-specific details.

    The ``type`` field maps to the IaC tool used (``terraform``, ``ansible``,
    ``compose``, ``helm``, ``script``).  ``backend`` is only present for
    stateful provisioners (Terraform).  ``details`` holds any type-specific
    metadata (e.g. Ansible playbook, Compose service list).
    """

    name: str = Field(description="Provisioner name as defined in the workspace (e.g. tf_hetzner)")
    type: str = Field(description="IaC tool: terraform | ansible | compose | helm | script")
    backend: Optional[Dict[str, Any]] = Field(
        None, description="State backend config: {type: azurerm, configuration: {...}}"
    )
    details: Optional[Dict[str, Any]] = Field(
        None, description="Type-specific metadata (e.g. playbook for ansible, stacks for compose)"
    )


class ManifestArtifactsModel(PlatformBaseModel):
    """Complete artifact bill of materials for the deployment.

    Groups all versioned, traceable artefacts that were present when the
    deployment ran:  the platform artifact, source repositories, container
    images, and provisioner backends.
    """

    platform: ManifestPlatformModel = Field(description="Platform artifact hash, path, and embedded content")
    repositories: Optional[Dict[str, ManifestRepositoryModel]] = Field(
        None, description="Pinned source repository versions keyed by repository name"
    )
    images: Optional[List[ManifestArtifactImageModel]] = Field(
        None, description="Container images used across all deployment stages"
    )
    providers: Optional[List[ManifestArtifactProviderModel]] = Field(
        None, description="Provisioners invoked — with state backend and type-specific details"
    )


# ---------------------------------------------------------------------------
# Outputs artifact reference model
# ---------------------------------------------------------------------------


class ManifestOutputsReferenceModel(PlatformBaseModel):
    """Reference to the durable outputs artifact written for a deployment stage.

    Recorded in :class:`ManifestStageModel` when ``spec.deployment.outputs``
    is enabled in the platform configuration.
    """

    path: str = Field(description="Workspace-relative path to the outputs artifact JSON file")
    stage: str = Field(description="Stage name whose outputs were written")
    version: str = Field(description="Deployment version label at time of write")
    written_at: str = Field(description="ISO-8601 timestamp of the artifact write")


class ManifestLockReferenceModel(PlatformBaseModel):
    """State lock audit trail recorded in the deployment manifest.

    Populated when ``spec.locking.enabled: true`` on the deployment.
    Records which backend held the lock, who held it, and when it was
    released — providing a complete concurrency audit trail per deploy run.
    """

    lock_id: str = Field(description="Unique lock identifier (UUID or backend-native ID)")
    backend: str = Field(description="Lock backend type: azurerm | terraform_cloud | s3 | consul | gcs | local")
    acquired_at: str = Field(description="ISO-8601 timestamp when the lock was acquired")
    released_at: Optional[str] = Field(None, description="ISO-8601 timestamp when the lock was released")
    holder: str = Field(description="Identity of the lock holder (user or CI actor)")
    hostname: str = Field(description="Hostname of the machine that acquired the lock")


# ---------------------------------------------------------------------------
# Stage result model
# ---------------------------------------------------------------------------


class ManifestStageModel(PlatformBaseModel):
    """Result of a single deployment stage execution."""

    name: PlatformName = Field(description="Stage name")
    provisioner: Optional[str] = Field(None, description="Provisioner used (e.g. tf_hetzner)")
    topology: Optional[str] = Field(None, description="Topology name (if topology-based)")
    status: str = Field(description="Outcome: success | failed | skipped")
    started_at: Optional[str] = Field(None, description="ISO-8601 start timestamp")
    completed_at: Optional[str] = Field(None, description="ISO-8601 completion timestamp")
    duration_seconds: Optional[int] = Field(None, description="Wall-clock duration in seconds")
    steps: Optional[List[str]] = Field(None, description="Steps executed (e.g. [setup, check, plan, apply])")
    outputs: Optional[Dict[str, Any]] = Field(None, description="Non-sensitive outputs collected from the stage")
    outputs_artifact: Optional[ManifestOutputsReferenceModel] = Field(
        None, description="Reference to the durable outputs artifact file written for this stage"
    )
    error: Optional[str] = Field(None, description="Error message if the stage failed")


# ---------------------------------------------------------------------------
# Policy result model
# ---------------------------------------------------------------------------


class ManifestPolicyResultModel(PlatformBaseModel):
    """Result of a single policy evaluation recorded in the deployment manifest."""

    policy_name: str = Field(description="Policy name as declared in configuration.spec.policies")
    policy_type: str = Field(description="Policy type (e.g. tenant_zone, required_labels, naming_pattern, script)")
    phase: str = Field(description="Phase when evaluated: validate | build | plan | deploy")
    enforcement: str = Field(description="Enforcement level: deny | warn | audit")
    passed: bool = Field(description="Whether the policy passed")
    violations: List[str] = Field(default_factory=list, description="Violation messages when the policy failed")


# ---------------------------------------------------------------------------
# Meta model
# ---------------------------------------------------------------------------


class DeploymentManifestMetaModel(PlatformBaseModel):
    """Metadata for a deployment manifest."""

    name: PlatformName = Field(description="Manifest name (derived from deployment name)")
    annotations: Optional[Dict[str, Any]] = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: Optional[Dict[str, Any]] = Field(
        None, description="Optional labels (key-value pairs for classification/filtering)"
    )
    tags: Optional[List[Any]] = Field(None, description="Optional tags (list of values for categorization)")


# ---------------------------------------------------------------------------
# Spec model
# ---------------------------------------------------------------------------


class DeploymentManifestSpecModel(PlatformBaseModel):
    """Full specification of a deployment manifest."""

    # Identity
    deployment_name: PlatformName = Field(description="Name of the deployment definition used")
    workspace_name: PlatformName = Field(description="Name of the workspace that was deployed")
    environment: Optional[str] = Field(None, description="Environment label (e.g. production, staging)")

    # Action & timing
    action: Literal["build", "deploy", "destroy"] = Field(description="Action performed: build | deploy | destroy")
    started_at: str = Field(description="ISO-8601 timestamp when the deploy started")
    completed_at: Optional[str] = Field(None, description="ISO-8601 timestamp when the deploy completed")
    duration_seconds: Optional[int] = Field(None, description="Total wall-clock duration in seconds")
    status: str = Field(description="Overall outcome: success | partial | failed")
    dry_run: bool = Field(default=False, description="Whether this was a dry-run (no changes applied)")

    # Actor
    deployed_by: Optional[str] = Field(
        None, description="Identity of who performed the deploy ($USER, $GITHUB_ACTOR, etc.)"
    )

    # Artifact bill of materials
    artifacts: ManifestArtifactsModel = Field(
        description="Complete artifact BOM: platform artifact, repositories, images, provisioner backends"
    )

    # Stage results
    stages: Optional[List[ManifestStageModel]] = Field(None, description="Results of each deployment stage")

    # Extension points
    sbom: Optional[SbomReferenceModel] = Field(None, description="Reference to the generated CycloneDX SBOM file")
    signatures: Optional[Dict[str, Any]] = Field(
        None,
        description="Signing/attestation data — when GPG signing is configured, "
        "contains {method, key_id, signature, signed_at}",
    )
    policy_results: Optional[List[ManifestPolicyResultModel]] = Field(
        None, description="Policy evaluation results from all phases run during this deployment"
    )
    lock: Optional[ManifestLockReferenceModel] = Field(
        None, description="State lock audit trail — populated when spec.locking.enabled: true"
    )
    audit_log: Optional[str] = Field(
        None, description="Relative path to the deploy-log _execution.json for this execution"
    )
    change_reference: Optional[ChangeReferenceModel] = Field(
        None, description="External change/ticket record justifying this deployment, when supplied (ADR-0074)"
    )


# ---------------------------------------------------------------------------
# Root model
# ---------------------------------------------------------------------------


class DeploymentManifestModel(PlatformBaseModel):
    """Root model for a deployment manifest file.

    Written automatically by both the build and deploy commands:

    * **Build:** ``strata build run`` writes ``manifest.json`` alongside the
      build artifacts (platform.json, terraform files, SBOM) capturing the
      full bill of materials at build time.
    * **Deploy:** ``strata deploy run/destroy`` writes a manifest on completion
      (success or failure) capturing per-stage execution results, state lock
      audit trail, and deployed-by identity.

    The manifest captures: platform artifact (with embedded platform.json),
    pinned repository commits, container images, provisioner backends, SBOM
    references, policy evaluation results, and optional signing attestations.
    """

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v1,
        frozen=True,
        description="API version for platform configuration",
    )
    kind: PlatformKind = Field(
        default=PlatformKind.DEPLOYMENT_MANIFEST,
        frozen=True,
        description="Platform kind (always 'deployment-manifest')",
    )
    meta: DeploymentManifestMetaModel = Field(description="Manifest metadata (name, labels, tags, annotations)")
    spec: DeploymentManifestSpecModel = Field(description="Full deployment manifest specification")
