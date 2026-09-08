#!/usr/bin/env python3
"""Unit tests for RemoteModel's type-specific field requirements."""

import pytest
from pydantic import ValidationError

from strata.models.repository_model import RemoteModel, RemoteType


class TestGitopsRequiresDeployPath:
    """A gitops remote with no deploy_path silently dropped out of
    ConfigurationService.get_remote_map() and fell back to a local path
    (RepositoryController._resolve_target_path()) that frequently didn't match
    where the repo was actually cloned — fail loud at config-validation time instead."""

    def test_gitops_without_deploy_path_is_rejected(self):
        with pytest.raises(ValidationError, match="deploy_path is required for gitops"):
            RemoteModel(
                name="iac_int",
                type=RemoteType.GITOPS,
                repository="https://example.com/acme/iac-int.git",
                reference="main",
                source_path=".",
            )

    def test_gitops_with_deploy_path_is_accepted(self):
        remote = RemoteModel(
            name="iac_int",
            type=RemoteType.GITOPS,
            repository="https://example.com/acme/iac-int.git",
            reference="main",
            source_path=".",
            deploy_path="repos/iac-int",
        )
        assert remote.deploy_path == "repos/iac-int"

    def test_bundled_without_deploy_path_is_still_accepted(self):
        """deploy_path remains optional for bundled — only gitops needs a stable
        local checkout path resolved by RepositoryController."""
        remote = RemoteModel(
            name="platform_iac",
            type=RemoteType.BUNDLED,
            repository="/",
            reference="main",
            source_path="deploy",
        )
        assert remote.deploy_path is None

    def test_container_without_deploy_path_is_still_accepted(self):
        remote = RemoteModel(
            name="docker_registry",
            type=RemoteType.CONTAINER,
            repository="registry.example.com/platform",
            reference="latest",
        )
        assert remote.deploy_path is None
