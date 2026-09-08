"""Pydantic models for the deploy-log — Layer 2 audit evidence.

The deploy-log captures **proof that a deployment happened**: execution timing,
git context, per-stage results, and optional PR enrichment data. Written
automatically by the deploy command on completion (success or failure).

Each execution produces:
- ``_execution.json`` — overall execution metadata (always written)
- ``{stage}.json`` — per-stage detail (when ``file_per_stage: true``)

On-disk location is resolved from ``spec.deployment.audit.structure``
using Jinja2 path templates.
"""

from typing import Any, Dict, List, Optional

from pydantic import Field

from strata.models.change_reference_model import ChangeReferenceModel
from strata.models.common_models import PlatformBaseModel, PlatformName


class DeployLogStepModel(PlatformBaseModel):
    """A single provisioner step within a stage (setup, check, plan, apply, destroy)."""

    step: str = Field(description="Step name")
    success: bool = Field(description="Whether the step succeeded")
    duration_seconds: float = Field(description="Step duration in seconds")


class DeployLogStageModel(PlatformBaseModel):
    """Per-stage deployment result."""

    name: PlatformName = Field(description="Stage name")
    provisioner: Optional[str] = Field(default=None, description="Provisioner type used")
    topology: Optional[str] = Field(default=None, description="Topology reference if used")
    success: bool = Field(description="Whether the stage succeeded")
    started_at: str = Field(description="ISO 8601 UTC start timestamp")
    completed_at: str = Field(description="ISO 8601 UTC completion timestamp")
    duration_seconds: float = Field(description="Stage duration in seconds")
    steps: List[DeployLogStepModel] = Field(default_factory=list, description="Step-level results")
    errors: List[str] = Field(default_factory=list, description="Errors encountered")
    messages: List[str] = Field(default_factory=list, description="Informational messages")


class DeployLogPullRequestModel(PlatformBaseModel):
    """PR/MR enrichment data — optional, GitHub-specific for now.

    Populated by AuditController.enrich_with_pr_data() when a GitHub remote
    is detected and the commit maps to a merged PR.
    """

    number: int = Field(description="PR number")
    title: str = Field(description="PR title")
    url: str = Field(description="PR URL")
    author: Optional[str] = Field(default=None, description="PR author login")
    merged_by: Optional[str] = Field(default=None, description="Who merged the PR")
    merged_at: Optional[str] = Field(default=None, description="ISO 8601 merge timestamp")
    approvers: List[str] = Field(default_factory=list, description="Approver logins")
    labels: List[str] = Field(default_factory=list, description="PR labels")
    linked_issues: List[str] = Field(default_factory=list, description="Linked issue references")
    files_changed: List[str] = Field(default_factory=list, description="Files changed in the PR")


class DeployLogModel(PlatformBaseModel):
    """Root deploy-log entry — one per execution.

    Written to ``_execution.json`` under the resolved deploy-log path.
    Contains the full execution record including all stages.
    """

    execution_id: str = Field(description="UUID4 unique execution identifier")
    timestamp: str = Field(description="ISO 8601 UTC execution start timestamp")
    command: str = Field(default="deploy_run", description="CLI command that produced this")
    version: str = Field(description="strata CLI version")
    commit_sha: Optional[str] = Field(default=None, description="Git HEAD commit SHA at deploy time")
    commit_message: Optional[str] = Field(default=None, description="Git HEAD commit message")
    commit_author: Optional[str] = Field(default=None, description="Git HEAD commit author")
    deployment: PlatformName = Field(description="Deployment meta.name")
    workspace: Optional[PlatformName] = Field(default=None, description="Workspace name")
    environment: Optional[str] = Field(default=None, description="Environment layer value")
    file: str = Field(description="Deployment YAML file path (relative)")
    force: bool = Field(default=False, description="Whether --force was used")
    dry_run: bool = Field(default=False, description="Whether --dry-run was used")
    success: bool = Field(description="Overall deployment success")
    duration_seconds: float = Field(description="Total execution duration in seconds")
    stages: List[DeployLogStageModel] = Field(default_factory=list, description="Per-stage results")
    pull_request: Optional[DeployLogPullRequestModel] = Field(
        default=None, description="PR enrichment data (null if unavailable)"
    )
    change_reference: Optional[ChangeReferenceModel] = Field(
        default=None, description="External change/ticket record justifying this deployment, when supplied (ADR-0074)"
    )
    errors: List[str] = Field(default_factory=list, description="Top-level errors")
    messages: List[str] = Field(default_factory=list, description="Top-level messages")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Extensible metadata")


class DeployLogStageFileModel(PlatformBaseModel):
    """Per-stage JSON file — written when file_per_stage is True.

    Contains a subset of DeployLogModel scoped to a single stage.
    """

    execution_id: str = Field(description="UUID4 shared with _execution.json")
    timestamp: str = Field(description="Execution start timestamp (same as parent)")
    version: str = Field(description="strata CLI version")
    deployment: PlatformName = Field(description="Deployment meta.name")
    stage: DeployLogStageModel = Field(description="Stage result detail")
