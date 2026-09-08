#!/usr/bin/env python3
"""Pydantic model for remote source configuration validation."""

import re
from enum import Enum
from pathlib import Path
from typing import Annotated, Optional

from pydantic import (
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from strata.models.common_models import PlatformBaseModel, PlatformName


class RemoteType(str, Enum):
    """
    Enumeration of supported remote source types.

    BUNDLED: Platform-bundled modules (available within the platform)
    GITOPS: GitOps repository to clone from
    CONTAINER: Container image source
    """

    BUNDLED = "bundled"
    GITOPS = "gitops"
    CONTAINER = "container"


class RemoteConventionsModel(PlatformBaseModel):
    """Naming conventions for references (tags) pinned to this remote.

    This is the single source of truth for what a valid release or
    quality-gate tag looks like for this remote. Declared once, here,
    alongside the remote itself — never duplicated in policy configuration.
    """

    release_pattern: Optional[str] = Field(
        None,
        description="Full-match regex for release tags, e.g. '^v\\d+\\.\\d+\\.\\d+$'",
    )
    quality_pattern: Optional[str] = Field(
        None,
        description="Full-match regex for quality-gate tags, e.g. '^tested(-\\d+)?$'",
    )


# Model for remote configuration.
class RemoteModel(PlatformBaseModel):
    """
    Model for remote source configuration.

    Supports three remote types:

    BUNDLED:
        - Platform-bundled modules available within the platform itself
        - repository: Platform-relative path ('.' or relative path)
        - reference: Version, tag, or branch identifier
        - source_path: Path to the module from the platform root
        - deploy_path: Optional path to deployment artifacts

    GITOPS:
        - Module deployed via GitOps workflow from Git repository
        - repository: Git repository URL (e.g., GitHub, GitLab)
        - reference: Branch, tag, or commit hash
        - source_path: Path to the module within the repository
        - deploy_path: Required — local path (relative to work_path) the remote is
          checked out to. RepositoryController._resolve_target_path() falls back to
          `name`/`repository` when unset, which frequently does not match where
          `strata repo add` actually cloned the repo (solution.json's own
          `repos/<name>` convention) — a silent misconfiguration that surfaced as
          "has not been fetched yet" even though the repo IS fetched, just at a
          different path. Required so this fails loud at config-validation time.

    CONTAINER:
        - Module deployed via container image
        - repository: Container image repository (e.g., Docker Hub, ECR)
        - reference: Image tag or digest
        - source_path: Path to the module within the container image
        - deploy_path: Optional path to deployment artifacts
    """

    name: Optional[PlatformName] = Field(None, description="Optional name for the remote")
    description: Optional[str] = Field(None, description="Human-readable description of this remote")
    type: RemoteType = Field(
        description="Remote type: bundled (platform-bundled), gitops (Git repository), or container"
    )
    repository: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)] = Field(
        description="Source repository or image for the deployment"
    )
    reference: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)] = Field(
        description="Version/tag/branch or image tag/digest for the deployment"
    )
    source_path: Optional[Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]] = Field(
        None,
        description="Path to the module configuration or script (not applicable for container type)",
    )
    deploy_path: Optional[str] = Field(None, description="Path to deployment artifacts (if applicable)")
    conventions: Optional[RemoteConventionsModel] = Field(
        None,
        description="Optional reference naming conventions for this remote (used by the ref_convention policy "
        "and by 'strata repo status' tag classification when a local repo's remote URL matches this one)",
    )

    @staticmethod
    def _validate_relative_path(v: str, field_name: str) -> str:
        """
        Validate that a path is relative and secure.

        Args:
            v: Path value to validate
            field_name: Name of the field being validated (for error messages)

        Returns:
            Validated path string

        Raises:
            ValueError: If path is invalid, absolute, or contains unsafe patterns
        """
        if not v or v.strip() == "":
            raise ValueError(f"{field_name} must not be empty")

        path_obj = Path(v)

        # Reject absolute paths
        if path_obj.is_absolute():
            raise ValueError(
                f"{field_name} must be relative, not absolute: {v}. "
                f"Absolute paths are not allowed for security reasons."
            )

        # Reject Windows drive letters (C:\, X:\, etc.)
        if re.match(r"^[A-Za-z]:[/\\]", v):
            raise ValueError(f"{field_name} must be relative, found Windows drive letter: {v}")

        # Reject UNC paths (\\\\server\\share)
        if v.startswith("\\\\"):
            raise ValueError(f"{field_name} must be relative, found UNC path: {v}")

        # Reject Unix absolute paths starting with /
        if v.startswith("/"):
            raise ValueError(f"{field_name} must be relative, not absolute: {v}")

        # Ensure it's a valid path format (allow spaces, @, ~, but not colons for security)
        if not re.match(r"^[a-zA-Z0-9.\-_/\\@ ~]+$", v):
            raise ValueError(f"{field_name} contains invalid characters: {v}")

        # Document that parent directory traversal (..) is allowed but risky
        if ".." in v:
            # SECURITY WARNING: Parent directory traversal (..) is allowed
            # This may allow access outside the intended workspace.
            # Ensure proper sandboxing and path resolution is in place.
            pass

        return v

    @field_validator("repository")
    @classmethod
    def validate_repository(cls, v: str, info) -> str:
        """
        Validate repository based on deployment type (format only, not existence).
        """
        deployment_type = info.data.get("type")

        if deployment_type == RemoteType.BUNDLED:
            # Bundled: validate format (paths can be created later)
            if v not in [".", "/"] and not re.match(r"^[a-zA-Z0-9.\-_/\\: ]+$", v):
                raise ValueError(f"Bundled repository path format is invalid: {v}")
        elif deployment_type == RemoteType.GITOPS:
            # Git URL: must look like a valid git URL
            if not re.match(r"^(https?://|git@|ssh://)", v):
                raise ValueError(f"GitOps repository must be a valid Git URL: {v}")
        elif deployment_type == RemoteType.CONTAINER:
            # Container: validate registry/image format
            # Examples: docker.io/library/nginx, ghcr.io/org/image, myregistry.azurecr.io/namespace/image
            if not re.match(r"^[a-zA-Z0-9.\-_:/]+$", v):
                raise ValueError(f"Container repository must be a valid image path: {v}")

        return v

    @field_validator("reference")
    @classmethod
    def validate_reference(cls, v: str, info) -> str:
        """
        Validate reference based on source type.
        """
        deployment_type = info.data.get("type")

        if deployment_type == RemoteType.BUNDLED:
            # Bundled: can be '.' or '/' or a version string
            pass  # Allow any value for bundled references
        elif deployment_type == RemoteType.GITOPS:
            # Git: must be a valid branch/tag/commit
            if not re.match(r"^[a-zA-Z0-9.\-_/]+$", v):
                raise ValueError(f"Git reference must be a valid branch, tag, or commit hash: {v}")
        elif deployment_type == RemoteType.CONTAINER:
            # Container: can be tag (v1.0.0, latest) or digest (sha256:abc123...)
            # Allow alphanumeric, dots, dashes, underscores, and sha256: prefix
            if not re.match(r"^(sha256:[a-f0-9]{64}|[a-zA-Z0-9.\-_]+)$", v):
                raise ValueError(f"Container reference must be a valid tag or digest: {v}")

        return v

    @field_validator("source_path")
    @classmethod
    def validate_config_path(cls, v: Optional[str], info) -> Optional[str]:
        """
        Validate source_path is relative and properly formatted.
        Absolute paths are not allowed for security reasons.
        Not required for CONTAINER type.
        """
        if v is None:
            return v
        return cls._validate_relative_path(v, "source_path")

    @field_validator("deploy_path")
    @classmethod
    def validate_deploy_path(cls, v: Optional[str], info) -> Optional[str]:
        """
        Validate deploy_path is relative if provided.
        Absolute paths are not allowed for security reasons.
        """
        if v is None:
            return v

        return cls._validate_relative_path(v, "deploy_path")

    @model_validator(mode="after")
    def validate_type_specific_requirements(self) -> "RemoteModel":
        """
        Cross-field validation for type-specific field requirements.
        """
        # BUNDLED and GITOPS require source_path
        if self.type in [RemoteType.BUNDLED, RemoteType.GITOPS]:
            if not self.source_path:
                raise ValueError(f"source_path is required for {self.type.value} source type")

        # GITOPS also requires deploy_path — the remote's local checkout path must be
        # explicit (see class docstring): a missing value silently drops the remote
        # from ConfigurationService.get_remote_map() and falls back to a path that
        # usually does not match where the repo was actually cloned.
        if self.type == RemoteType.GITOPS and not self.deploy_path:
            raise ValueError(
                "deploy_path is required for gitops source type — it must name the local "
                "path (relative to work_path) this remote is checked out to."
            )

        # CONTAINER type should not use source_path
        if self.type == RemoteType.CONTAINER:
            if self.source_path is not None:
                raise ValueError(
                    "source_path is not applicable for container source type. Container images are self-contained."
                )

        return self

    def get_label(self) -> str:  # Not Optional
        """Get the label/name of the remote configuration."""
        if self.name and self.type:
            return f"{self.name} ({self.type.value})"
        elif self.type:
            return str(self.type.value)
        return "Unnamed Remote"


# Backwards-compatible aliases (deprecated — will be removed before v1)
RepositoryModel = RemoteModel
RepositoryType = RemoteType
