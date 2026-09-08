"""Tests for VersionController.lock_manifest and add_manifest (ADR-0011)."""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

import yaml

from strata.controllers.version_controller import VersionController

_VERSION_YAML = """\
apiVersion: strata.huybrechts.xyz/v1
kind: version
meta:
  name: v2-1-0
spec:
  ring: prd
  pins:
    images:
      app: v2.1.0
      worker: v2.0.0
    charts:
      traefik: "28.2.0"
"""


def _expected_hash(pins: dict) -> str:
    canonical = json.dumps(pins, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class TestVersionControllerLockManifest:
    """lock_manifest computes and writes spec.hash."""

    def setup_method(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = Path(self._td.name)
        self.vf = self.td / "v2.1.0.yaml"
        self.vf.write_text(_VERSION_YAML)

    def teardown_method(self):
        self._td.cleanup()

    def test_lock_returns_file_and_hash(self):
        ctrl = VersionController()
        result = ctrl.lock_manifest(self.vf)
        assert not ctrl.has_errors()
        assert result["file"] == str(self.vf)
        assert len(result["hash"]) == 64  # SHA-256 hex

    def test_lock_writes_hash_to_file(self):
        ctrl = VersionController()
        ctrl.lock_manifest(self.vf)
        raw = yaml.safe_load(self.vf.read_text())
        assert "hash" in raw["spec"]

    def test_lock_hash_is_deterministic(self):
        ctrl = VersionController()
        r1 = ctrl.lock_manifest(self.vf)
        r2 = ctrl.lock_manifest(self.vf)  # second lock over same content
        assert r1["hash"] == r2["hash"]

    def test_lock_hash_matches_expected(self):
        raw = yaml.safe_load(self.vf.read_text())
        pins = raw["spec"]["pins"]
        expected = _expected_hash(pins)

        ctrl = VersionController()
        result = ctrl.lock_manifest(self.vf)
        assert result["hash"] == expected

    def test_lock_nonexistent_file_returns_error(self):
        ctrl = VersionController()
        result = ctrl.lock_manifest(self.td / "missing.yaml")
        assert result == {}
        assert ctrl.has_errors()

    def test_lock_wrong_kind_returns_error(self):
        wrong = self.td / "wrong.yaml"
        wrong.write_text("apiVersion: strata.huybrechts.xyz/v1\nkind: workspace\nmeta:\n  name: test\nspec: {}\n")
        ctrl = VersionController()
        result = ctrl.lock_manifest(wrong)
        assert result == {}
        assert ctrl.has_errors()

    def test_lock_preserves_comments(self):
        """Regression test: lock_manifest() used to rewrite the file via plain
        yaml.safe_load()/yaml.dump(), silently deleting any hand-authored comments."""
        commented = self.td / "commented.yaml"
        commented.write_text(
            "apiVersion: strata.huybrechts.xyz/v1\n"
            "kind: version\n"
            "meta:\n"
            "  name: v2-1-0\n"
            "spec:\n"
            "  ring: prd\n"
            "  pins:\n"
            "    images:\n"
            "      app: v2.1.0  # pinned for the Q3 release\n"
            "      # worker intentionally lags behind app\n"
            "      worker: v2.0.0\n"
        )

        ctrl = VersionController()
        ctrl.lock_manifest(commented)

        content = commented.read_text()
        assert "# pinned for the Q3 release" in content
        assert "# worker intentionally lags behind app" in content
        assert "hash:" in content


class TestVersionControllerAddManifest:
    """add_manifest creates a new version snapshot."""

    def setup_method(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = Path(self._td.name)
        self.src = self.td / "v2.1.0.yaml"
        self.src.write_text(_VERSION_YAML)

    def teardown_method(self):
        self._td.cleanup()

    def test_add_creates_file(self):
        dest = self.td / "v3.0.0.yaml"
        ctrl = VersionController()
        result = ctrl.add_manifest(dest, ring="prd")
        assert not ctrl.has_errors()
        assert dest.exists()
        assert result["file"] == str(dest)
        assert result["ring"] == "prd"
        assert result["from"] is None

    def test_add_from_copies_pins(self):
        dest = self.td / "v3.0.0.yaml"
        ctrl = VersionController()
        ctrl.add_manifest(dest, ring="prd", from_file=self.src)
        raw = yaml.safe_load(dest.read_text())
        assert raw["spec"]["pins"]["images"]["app"] == "v2.1.0"
        assert raw["spec"]["pins"]["charts"]["traefik"] == "28.2.0"

    def test_add_from_sets_from_key_in_result(self):
        dest = self.td / "v3.0.0.yaml"
        ctrl = VersionController()
        result = ctrl.add_manifest(dest, ring="prd", from_file=self.src)
        assert result["from"] == str(self.src)

    def test_add_existing_without_force_returns_error(self):
        ctrl = VersionController()
        ctrl.add_manifest(self.src, ring="prd")  # file already exists
        assert ctrl.has_errors()
        assert ctrl.get_errors()[0].startswith("File already exists")

    def test_add_existing_with_force_overwrites(self):
        dest = self.td / "v3.0.0.yaml"
        ctrl = VersionController()
        ctrl.add_manifest(dest, ring="prd")  # create first
        ctrl2 = VersionController()
        result = ctrl2.add_manifest(dest, ring="prd", force=True)
        assert not ctrl2.has_errors()
        assert result["file"] == str(dest)

    def test_add_creates_parent_dirs(self):
        dest = self.td / "nested" / "dir" / "v1.0.0.yaml"
        ctrl = VersionController()
        ctrl.add_manifest(dest, ring="dev")
        assert dest.exists()

    def test_add_from_missing_source_returns_error(self):
        ctrl = VersionController()
        result = ctrl.add_manifest(self.td / "new.yaml", ring="dev", from_file=self.td / "ghost.yaml")
        assert result == {}
        assert ctrl.has_errors()


class TestVersionControllerRefreshManifest:
    """refresh_manifest syncs spec.pins against targets discovered under scan_dir."""

    def setup_method(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = Path(self._td.name)
        self.vf = self.td / "v2.1.0.yaml"
        self.vf.write_text(_VERSION_YAML)
        self.scan_dir = self.td / "scan"
        self.scan_dir.mkdir()

    def teardown_method(self):
        self._td.cleanup()

    def test_refresh_with_no_scan_targets_reports_stale(self):
        ctrl = VersionController()
        result = ctrl.refresh_manifest(self.vf, self.scan_dir)
        assert not ctrl.has_errors()
        assert set(result["stale"]["images"]) == {"app", "worker"}
        assert set(result["stale"]["charts"]) == {"traefik"}
        assert result["added"] == {"images": [], "charts": [], "remotes": []}

    def test_refresh_dry_run_does_not_write(self):
        before = self.vf.read_text()
        ctrl = VersionController()
        result = ctrl.refresh_manifest(self.vf, self.scan_dir, dry_run=True)
        assert result["dry_run"] is True
        assert self.vf.read_text() == before

    def test_refresh_adds_newly_discovered_target(self):
        (self.scan_dir / "module.yaml").write_text(
            "apiVersion: strata.huybrechts.xyz/v1\n"
            "kind: module\n"
            "meta:\n"
            "  name: api\n"
            "spec:\n"
            "  type: compose\n"
            "  services:\n"
            "    - name: api\n"
            "      image: api-image:latest\n"
        )
        ctrl = VersionController()
        result = ctrl.refresh_manifest(self.vf, self.scan_dir)
        assert "api" in result["added"]["images"]
        raw = yaml.safe_load(self.vf.read_text())
        assert raw["spec"]["pins"]["images"]["api"] == "api-image:latest"

    def test_refresh_remove_stale_deletes_pin(self):
        ctrl = VersionController()
        ctrl.refresh_manifest(self.vf, self.scan_dir, remove_stale=True)
        raw = yaml.safe_load(self.vf.read_text())
        assert raw["spec"]["pins"]["images"] == {}
        assert raw["spec"]["pins"]["charts"] == {}

    def test_refresh_missing_file_returns_error(self):
        ctrl = VersionController()
        result = ctrl.refresh_manifest(self.td / "missing.yaml", self.scan_dir)
        assert result == {}
        assert ctrl.has_errors()

    def test_refresh_wrong_kind_returns_error(self):
        wrong = self.td / "wrong.yaml"
        wrong.write_text("apiVersion: strata.huybrechts.xyz/v1\nkind: workspace\nmeta:\n  name: test\nspec: {}\n")
        ctrl = VersionController()
        result = ctrl.refresh_manifest(wrong, self.scan_dir)
        assert result == {}
        assert ctrl.has_errors()

    def test_refresh_preserves_comments(self):
        """Regression test: refresh_manifest() used to rewrite the file via plain
        yaml.safe_load()/yaml.dump(), silently deleting any hand-authored comments."""
        commented = self.td / "commented.yaml"
        commented.write_text(
            "apiVersion: strata.huybrechts.xyz/v1\n"
            "kind: version\n"
            "meta:\n"
            "  name: v2-1-0\n"
            "spec:\n"
            "  ring: prd\n"
            "  pins:\n"
            "    images:\n"
            "      app: v2.1.0  # pinned for the Q3 release\n"
        )

        ctrl = VersionController()
        ctrl.refresh_manifest(commented, self.scan_dir)

        assert "# pinned for the Q3 release" in commented.read_text()
