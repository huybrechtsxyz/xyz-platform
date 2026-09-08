#!/usr/bin/env python3
"""Unit tests for repo status tag discovery.

Tag classification only happens when a solution repo's actual git remote URL
matches a configured ``spec.remotes[]`` entry that declares ``conventions``.
There is no name-based guessing and no hardcoded tag-name heuristic.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from strata.commands.repo.status_repo_solution_command import StatusRepoSolutionCommand
from strata.integrations.git import TagInfo
from strata.models.repository_model import RemoteConventionsModel, RemoteModel, RemoteType


def _make_remote(release_pattern=None, quality_pattern=None, repository="https://example.com/acme/repo.git"):
    conventions = None
    if release_pattern or quality_pattern:
        conventions = RemoteConventionsModel(release_pattern=release_pattern, quality_pattern=quality_pattern)
    return RemoteModel(
        name="my-service",
        type=RemoteType.GITOPS,
        repository=repository,
        reference="main",
        source_path=".",
        deploy_path="repos/my-service",
        conventions=conventions,
    )


def _make_tag(name: str, created: datetime) -> TagInfo:
    return TagInfo(
        name=name,
        commit=f"{name}-full-sha",
        short_commit=name[:7],
        tagger=None,
        created=created,
        message=None,
        is_annotated=False,
    )


class TestRepoStatusTagDiscoveryUnlinked:
    """No matched remote => no guessing, no classification."""

    def test_discover_tags_none_when_no_matched_remote(self):
        cmd = StatusRepoSolutionCommand()
        mock_git = MagicMock()
        mock_git.list_tags.return_value = [_make_tag("v1.2.0", datetime(2026, 6, 20, tzinfo=timezone.utc))]

        result = cmd._discover_tags(mock_git, "/tmp/repo", matched_remote=None)

        assert result is None
        # No git call should even be made when unlinked
        mock_git.list_tags.assert_not_called()

    def test_discover_tags_none_when_matched_remote_has_no_conventions(self):
        cmd = StatusRepoSolutionCommand()
        mock_git = MagicMock()
        remote = _make_remote()  # no conventions

        result = cmd._discover_tags(mock_git, "/tmp/repo", matched_remote=remote)

        assert result is None


class TestRepoStatusTagDiscoveryLinked:
    """Matched remote with conventions => classify using its declared patterns."""

    def test_discover_tags_no_tags(self):
        cmd = StatusRepoSolutionCommand()
        mock_git = MagicMock()
        mock_git.list_tags.return_value = []
        remote = _make_remote(release_pattern=r"^v\d+\.\d+\.\d+$")

        result = cmd._discover_tags(mock_git, "/tmp/repo", matched_remote=remote)

        assert result is None

    def test_discover_tags_release_tag_only(self):
        cmd = StatusRepoSolutionCommand()
        mock_git = MagicMock()
        mock_git.list_tags.return_value = [_make_tag("v1.2.0", datetime(2026, 6, 20, 10, 30, tzinfo=timezone.utc))]
        remote = _make_remote(release_pattern=r"^v\d+\.\d+\.\d+$")

        result = cmd._discover_tags(mock_git, "/tmp/repo", matched_remote=remote)

        assert result is not None
        assert "latest_release" in result
        assert result["latest_release"]["name"] == "v1.2.0"
        assert "latest_quality" not in result

    def test_discover_tags_quality_tag_only(self):
        cmd = StatusRepoSolutionCommand()
        mock_git = MagicMock()
        mock_git.list_tags.return_value = [_make_tag("tested", datetime(2026, 6, 21, 8, 0, tzinfo=timezone.utc))]
        remote = _make_remote(quality_pattern=r"^tested(-\d+)?$")

        result = cmd._discover_tags(mock_git, "/tmp/repo", matched_remote=remote)

        assert result is not None
        assert result["latest_quality"]["name"] == "tested"
        assert "latest_release" not in result

    def test_discover_tags_both_release_and_quality(self):
        cmd = StatusRepoSolutionCommand()
        mock_git = MagicMock()
        mock_git.list_tags.return_value = [
            _make_tag("tested", datetime(2026, 6, 21, 8, 0, tzinfo=timezone.utc)),
            _make_tag("v1.2.0", datetime(2026, 6, 20, 10, 30, tzinfo=timezone.utc)),
        ]
        remote = _make_remote(release_pattern=r"^v\d+\.\d+\.\d+$", quality_pattern=r"^tested(-\d+)?$")

        result = cmd._discover_tags(mock_git, "/tmp/repo", matched_remote=remote)

        assert result is not None
        assert result["latest_release"]["name"] == "v1.2.0"
        assert result["latest_quality"]["name"] == "tested"

    def test_discover_tags_prefers_first_matching_newest_first(self):
        cmd = StatusRepoSolutionCommand()
        mock_git = MagicMock()
        mock_git.list_tags.return_value = [
            _make_tag("v1.3.0", datetime(2026, 6, 21, 14, 0, tzinfo=timezone.utc)),
            _make_tag("v1.2.0", datetime(2026, 6, 20, 10, 30, tzinfo=timezone.utc)),
        ]
        remote = _make_remote(release_pattern=r"^v\d+\.\d+\.\d+$")

        result = cmd._discover_tags(mock_git, "/tmp/repo", matched_remote=remote)

        assert result["latest_release"]["name"] == "v1.3.0"

    def test_discover_tags_reference_not_matching_pattern_is_ignored(self):
        cmd = StatusRepoSolutionCommand()
        mock_git = MagicMock()
        # calver-style tag, but remote only declares a semver pattern
        mock_git.list_tags.return_value = [_make_tag("release-2026-07-27", datetime(2026, 7, 27, tzinfo=timezone.utc))]
        remote = _make_remote(release_pattern=r"^v\d+\.\d+\.\d+$")

        result = cmd._discover_tags(mock_git, "/tmp/repo", matched_remote=remote)

        assert result is None

    def test_discover_tags_handles_exception(self):
        cmd = StatusRepoSolutionCommand()
        mock_git = MagicMock()
        mock_git.list_tags.side_effect = Exception("Git command failed")
        remote = _make_remote(release_pattern=r"^v\d+\.\d+\.\d+$")

        result = cmd._discover_tags(mock_git, "/tmp/repo", matched_remote=remote)

        assert result is None

    def test_discover_tags_iso_format_created_date(self):
        cmd = StatusRepoSolutionCommand()
        mock_git = MagicMock()
        created = datetime(2026, 6, 20, 10, 30, 45, tzinfo=timezone.utc)
        mock_git.list_tags.return_value = [_make_tag("v1.2.0", created)]
        remote = _make_remote(release_pattern=r"^v\d+\.\d+\.\d+$")

        result = cmd._discover_tags(mock_git, "/tmp/repo", matched_remote=remote)

        assert result["latest_release"]["created"] == created.isoformat()

    def test_discover_tags_age_calculation(self):
        cmd = StatusRepoSolutionCommand()
        mock_git = MagicMock()
        now = datetime.now(timezone.utc)
        created = now - timedelta(days=5)
        mock_git.list_tags.return_value = [_make_tag("v1.2.0", created)]
        remote = _make_remote(release_pattern=r"^v\d+\.\d+\.\d+$")

        result = cmd._discover_tags(mock_git, "/tmp/repo", matched_remote=remote)

        assert result["latest_release"]["age_days"] == 5
        assert "days ago" in result["latest_release"]["age_str"]


class TestNormalizeRepoUrl:
    """URL normalization used to link a local repo's real remote URL to spec.remotes[].repository."""

    def test_https_and_ssh_forms_are_equal(self):
        https = StatusRepoSolutionCommand._normalize_repo_url("https://github.com/acme/billing-service")
        ssh = StatusRepoSolutionCommand._normalize_repo_url("git@github.com:acme/billing-service.git")

        assert https == ssh

    def test_scheme_based_ssh_form_equals_scp_form(self):
        """Regression: ssh://user@host/org/repo (scheme form) must normalize the same as
        user@host:org/repo (SCP-like form) — both are valid, common syntaxes for the same
        remote. The SCP-form rewrite regex only matched at the start of the string, so a
        leading scheme (ssh://) prevented it from firing, leaving 'git@' in the result and
        silently breaking the match against the SCP-form/https equivalents."""
        scp_form = StatusRepoSolutionCommand._normalize_repo_url("git@github.com:acme/billing-service.git")
        scheme_form = StatusRepoSolutionCommand._normalize_repo_url("ssh://git@github.com/acme/billing-service.git")

        assert scheme_form == scp_form

    def test_trailing_slash_and_dotgit_suffix_ignored(self):
        a = StatusRepoSolutionCommand._normalize_repo_url("https://github.com/acme/billing-service.git")
        b = StatusRepoSolutionCommand._normalize_repo_url("https://github.com/acme/billing-service/")

        assert a == b

    def test_case_insensitive(self):
        a = StatusRepoSolutionCommand._normalize_repo_url("https://GitHub.com/Acme/Billing-Service")
        b = StatusRepoSolutionCommand._normalize_repo_url("https://github.com/acme/billing-service")

        assert a == b

    def test_different_repos_do_not_match(self):
        a = StatusRepoSolutionCommand._normalize_repo_url("https://github.com/acme/billing-service")
        b = StatusRepoSolutionCommand._normalize_repo_url("https://github.com/acme/other-service")

        assert a != b
