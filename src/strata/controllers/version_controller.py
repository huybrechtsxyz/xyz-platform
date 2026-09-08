"""Business logic for version-manifest and version-lock file operations."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml
from ruamel.yaml import YAML

from strata.controllers.base_controller import BaseController
from strata.models.common_models import PlatformKind

_API_VERSION = "strata.huybrechts.xyz/v1"

# Flat pin representation: type_key (str) → {name: version}
TargetMap = dict[str, dict[str, str]]

# Round-trip YAML loader/dumper — preserves comments, key order, and blank lines
# on any read-modify-write cycle over an *existing* file (as opposed to
# `yaml.dump()`, used elsewhere in this module only to write brand-new files
# that have no prior content to preserve). `lock_manifest()`/`refresh_manifest()`
# rewrite a hand-authored version-manifest in place — without this, every
# `strata versions lock`/`refresh` silently deleted any comments the file had.
_ROUND_TRIP_YAML = YAML()
_ROUND_TRIP_YAML.preserve_quotes = True
_ROUND_TRIP_YAML.width = 4096  # avoid re-wrapping long scalar values (e.g. image digests)


class VersionController(BaseController):
    """Operations on version-manifest and version-lock YAML files.

    All public methods accumulate errors via ``_add_error()`` and return an
    empty dict on failure so callers can check ``has_errors()``.
    """

    # ── init ─────────────────────────────────────────────────────────────────

    def init_manifest(self, dest: Path, ring: str, force: bool = False) -> dict:
        """Scaffold a starter version-manifest file at *dest*.

        Returns ``{"file": str, "ring": str}`` on success, ``{}`` on failure.
        """
        if dest.exists() and not force:
            self._add_error(f"File already exists: {dest}. Use --force to overwrite.")
            return {}

        doc = {
            "apiVersion": _API_VERSION,
            "kind": PlatformKind.VERSION_MANIFEST.value,
            "meta": {
                "name": ring,
                "annotations": {"description": f"Version manifest for the {ring} ring"},
            },
            "spec": {
                "ring": ring,
                "pins": {
                    "images": {},
                    "charts": {},
                    "remotes": {},
                },
            },
        }

        dest.parent.mkdir(parents=True, exist_ok=True)
        self._write_yaml(dest, doc)
        return {"file": str(dest), "ring": ring}

    # ── export ────────────────────────────────────────────────────────────────

    def export_pins(self, file_path: Path) -> dict:
        """Load a version file and return the resolved flat pin dict.

        Returns ``{"pins": {type: {name: version}}}`` on success, ``{}`` on failure.
        """
        if not file_path.exists():
            self._add_error(f"File not found: {file_path}")
            return {}

        from strata.services.version_service import VersionService

        try:
            model = VersionService.load(str(file_path))
        except Exception as exc:
            self._add_error(str(exc))
            return {}

        pins = VersionService.resolve_pins([model])
        flat: dict[str, dict[str, str]] = {pt.value: entries for pt, entries in pins.items() if entries}
        return {"pins": flat}

    # ── apply ─────────────────────────────────────────────────────────────────

    def apply_manifest(
        self,
        file_path: Path,
        lock_path: Path,
        force: bool = False,
    ) -> dict:
        """Convert a version-manifest (kind: version) into a version-lock file.

        Returns ``{"lock_file": str, "ring": str, "pins_count": int}`` on success,
        ``{}`` on failure.
        """
        if not file_path.exists():
            self._add_error(f"File not found: {file_path}")
            return {}

        if lock_path.exists() and not force:
            self._add_error(f"Lock file already exists: {lock_path}. Use --force to overwrite.")
            return {}

        from strata.models.version_lock_model import VersionPinTargetType
        from strata.models.version_manifest_model import VersionManifestModel
        from strata.services.version_service import VersionService

        try:
            model = VersionService.load(str(file_path))
        except Exception as exc:
            self._add_error(str(exc))
            return {}

        if not isinstance(model, VersionManifestModel):
            self._add_error(
                f"Expected kind: {PlatformKind.VERSION_MANIFEST.value}, "
                f"got '{model.kind}'. Provide a version-manifest file."
            )
            return {}

        pins_raw = model.spec.pins
        lock_pins: list[dict] = []

        if pins_raw.images:
            for name, version in pins_raw.images.items():
                lock_pins.append(
                    {"target": {"type": VersionPinTargetType.IMAGE.value, "name": name}, "version": version}
                )
        if pins_raw.charts:
            for name, version in pins_raw.charts.items():
                lock_pins.append(
                    {"target": {"type": VersionPinTargetType.HELM_CHART.value, "name": name}, "version": version}
                )
        if pins_raw.remotes:
            for name, version in pins_raw.remotes.items():
                lock_pins.append(
                    {"target": {"type": VersionPinTargetType.REMOTE.value, "name": name}, "version": version}
                )

        now_ts = datetime.now(timezone.utc).isoformat()
        lock_doc = {
            "apiVersion": _API_VERSION,
            "kind": PlatformKind.VERSION_LOCK.value,
            "meta": {
                "name": model.meta.name,
                "annotations": {
                    "generated_at": now_ts,
                    "generated_by": "strata versions apply",
                },
            },
            "spec": {
                "ring": model.spec.ring,
                "pins": lock_pins,
            },
        }

        lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_yaml(lock_path, lock_doc)
        return {"lock_file": str(lock_path), "ring": model.spec.ring, "pins_count": len(lock_pins)}

    # ── refresh ───────────────────────────────────────────────────────────────

    def refresh_manifest(
        self,
        file_path: Path,
        scan_dir: Path,
        remove_stale: bool = False,
        dry_run: bool = False,
    ) -> dict:
        """Sync *file_path* against targets discovered under *scan_dir*.

        Returns a dict with keys ``added``, ``stale``, ``stale_removed``,
        ``dry_run``, ``file``.  Returns ``{}`` on failure.
        """
        if not file_path.exists():
            self._add_error(f"File not found: {file_path}")
            return {}

        raw_doc = self._read_yaml_preserving_comments(file_path)

        if not isinstance(raw_doc, dict) or raw_doc.get("kind") != PlatformKind.VERSION_MANIFEST.value:
            self._add_error(
                f"Expected kind: {PlatformKind.VERSION_MANIFEST.value} — "
                f"got '{raw_doc.get('kind') if isinstance(raw_doc, dict) else '<invalid>'}'."
            )
            return {}

        discovered = self.scan_for_targets(scan_dir)

        spec = raw_doc.setdefault("spec", {})
        current_pins: dict = spec.setdefault("pins", {})

        added: dict[str, list[str]] = {"images": [], "charts": [], "remotes": []}
        stale: dict[str, list[str]] = {"images": [], "charts": [], "remotes": []}

        for type_key in ("images", "charts", "remotes"):
            current = current_pins.setdefault(type_key, {}) or {}
            found = discovered.get(type_key, {})

            for name, seed in found.items():
                if name not in current:
                    current[name] = seed
                    added[type_key].append(name)

            for name in list(current.keys()):
                if name not in found:
                    stale[type_key].append(name)
                    if remove_stale:
                        del current[name]

            current_pins[type_key] = current

        result = {
            "added": added,
            "stale": stale,
            "stale_removed": remove_stale,
            "dry_run": dry_run,
            "file": str(file_path),
        }

        if not dry_run:
            self._write_yaml_preserving_comments(file_path, raw_doc)

        return result

    # ── scanner ───────────────────────────────────────────────────────────────

    def scan_for_targets(self, scan_dir: Path) -> TargetMap:
        """Walk *scan_dir* and extract versionable targets from YAML files.

        Returns a dict with keys ``images``, ``charts``, ``remotes``, each
        mapping target name → seed version string.

        Sources scanned:
        - ``kind: module``        — ``spec.source.chart_name``/``chart_version``
                                    and ``spec.services[].image``
        - ``kind: workspace``     — ``spec.provisioners[].source.repository``
                                    and ``spec.provisioners[].source.chart_name``/``chart_version``
        - ``kind: configuration`` — ``spec.remotes[].name``  (seed: ``reference``)
        - ``kind: environment``   — ``spec.overrides.remotes[].remote``/``reference``,
                                    ``spec.overrides.modules[].chart_version``,
                                    ``spec.overrides.modules[].services[].image``

        Unreadable or non-YAML files are silently skipped.
        """
        discovered: TargetMap = {"images": {}, "charts": {}, "remotes": {}}

        kind_extractors = {
            PlatformKind.MODULE.value: self._extract_module_targets,
            PlatformKind.WORKSPACE.value: self._extract_workspace_targets,
            PlatformKind.CONFIGURATION.value: self._extract_configuration_targets,
            PlatformKind.ENVIRONMENT.value: self._extract_environment_targets,
        }

        for yaml_path in sorted(scan_dir.rglob("*.yaml")):
            try:
                with yaml_path.open("r", encoding="utf-8") as fh:
                    raw = yaml.safe_load(fh)
                if not isinstance(raw, dict):
                    continue
                extractor = kind_extractors.get(raw.get("kind") or "")  # type: ignore[arg-type]
                if extractor:
                    extractor(raw, discovered)
            except Exception:
                continue

        return discovered

    # ── private extractors ────────────────────────────────────────────────────

    def _extract_module_targets(self, raw: dict, discovered: TargetMap) -> None:
        spec = raw.get("spec") or {}
        meta = raw.get("meta") or {}
        module_name: str = meta.get("name") or ""
        source = spec.get("source") or {}

        chart_name: Optional[str] = source.get("chart_name")
        if chart_name:
            key = module_name or chart_name
            discovered["charts"].setdefault(key, source.get("chart_version") or "")

        for svc in spec.get("services") or []:
            image: Optional[str] = svc.get("image")
            svc_name: str = svc.get("name") or ""
            if image and svc_name:
                discovered["images"].setdefault(svc_name, image)

    def _extract_workspace_targets(self, raw: dict, discovered: TargetMap) -> None:
        spec = raw.get("spec") or {}
        for prov in spec.get("provisioners") or []:
            source = prov.get("source") or {}
            repo: Optional[str] = source.get("repository")
            if repo:
                discovered["remotes"].setdefault(repo, "")
            chart_name: Optional[str] = source.get("chart_name")
            if chart_name:
                key = prov.get("name") or chart_name
                discovered["charts"].setdefault(key, source.get("chart_version") or "")

    def _extract_configuration_targets(self, raw: dict, discovered: TargetMap) -> None:
        spec = raw.get("spec") or {}
        for remote in spec.get("remotes") or []:
            name: Optional[str] = remote.get("name")
            if name:
                discovered["remotes"].setdefault(name, remote.get("reference") or "")

    def _extract_environment_targets(self, raw: dict, discovered: TargetMap) -> None:
        overrides = (raw.get("spec") or {}).get("overrides") or {}

        for remote_ov in overrides.get("remotes") or []:
            name: Optional[str] = remote_ov.get("remote")
            if name:
                discovered["remotes"].setdefault(name, remote_ov.get("reference") or "")

        for mod_ov in overrides.get("modules") or []:
            mod_name: Optional[str] = mod_ov.get("module")
            chart_version: Optional[str] = mod_ov.get("chart_version")
            if mod_name and chart_version:
                discovered["charts"].setdefault(mod_name, chart_version)
            for svc_ov in mod_ov.get("services") or []:
                svc_name: Optional[str] = svc_ov.get("name")
                image: Optional[str] = svc_ov.get("image")
                if svc_name and image:
                    discovered["images"].setdefault(svc_name, image)

    # ── lock ──────────────────────────────────────────────────────────────────

    def lock_manifest(self, file_path: Path) -> dict:
        """Compute spec.hash for a version-manifest file and write it back in place.

        The hash is a SHA-256 over the canonical JSON serialisation of spec.pins
        (sorted keys, no extra whitespace).  Any existing spec.hash value is replaced.

        Returns ``{"file": str, "hash": str}`` on success, ``{}`` on failure.
        """
        if not file_path.exists():
            self._add_error(f"File not found: {file_path}")
            return {}

        raw_doc = self._read_yaml_preserving_comments(file_path)

        if not isinstance(raw_doc, dict) or raw_doc.get("kind") != PlatformKind.VERSION_MANIFEST.value:
            self._add_error(
                f"Expected kind: {PlatformKind.VERSION_MANIFEST.value} — "
                f"got '{raw_doc.get('kind') if isinstance(raw_doc, dict) else '<invalid>'}'."
            )
            return {}

        pins = raw_doc.get("spec", {}).get("pins", {})
        canonical = json.dumps(pins, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

        raw_doc.setdefault("spec", {})["hash"] = digest
        self._write_yaml_preserving_comments(file_path, raw_doc)

        return {"file": str(file_path), "hash": digest}

    # ── add ───────────────────────────────────────────────────────────────────

    def add_manifest(
        self,
        dest: Path,
        ring: str,
        from_file: Optional[Path] = None,
        force: bool = False,
    ) -> dict:
        """Create a new version-manifest snapshot file.

        When *from_file* is provided the pins are copied from that file.
        Otherwise an empty scaffold is written (like ``init_manifest``).

        Returns ``{"file": str, "ring": str, "from": str | None}`` on success, ``{}`` on failure.
        """
        if dest.exists() and not force:
            self._add_error(f"File already exists: {dest}. Use --force to overwrite.")
            return {}

        if from_file is not None:
            if not from_file.exists():
                self._add_error(f"Source file not found: {from_file}")
                return {}

            with from_file.open("r", encoding="utf-8") as fh:
                src = yaml.safe_load(fh)

            if not isinstance(src, dict) or src.get("kind") != PlatformKind.VERSION_MANIFEST.value:
                self._add_error(
                    f"--from must be a kind: {PlatformKind.VERSION_MANIFEST.value} file, "
                    f"got '{src.get('kind') if isinstance(src, dict) else '<invalid>'}'."
                )
                return {}

            pins = src.get("spec", {}).get("pins", {})
        else:
            pins = {"images": {}, "charts": {}, "remotes": {}}

        doc = {
            "apiVersion": _API_VERSION,
            "kind": PlatformKind.VERSION_MANIFEST.value,
            "meta": {
                "name": dest.stem,
                "annotations": {"description": f"Version snapshot for the {ring} ring"},
            },
            "spec": {
                "ring": ring,
                "pins": pins,
            },
        }

        dest.parent.mkdir(parents=True, exist_ok=True)
        self._write_yaml(dest, doc)
        return {"file": str(dest), "ring": ring, "from": str(from_file) if from_file else None}

    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _write_yaml(path: Path, doc: dict) -> None:
        with path.open("w", encoding="utf-8") as fh:
            yaml.dump(doc, fh, default_flow_style=False, sort_keys=False, allow_unicode=True)

    @staticmethod
    def _read_yaml_preserving_comments(path: Path) -> Any:
        """Load *path* via the round-trip loader — the returned mapping carries
        comment/order metadata that survives a subsequent
        ``_write_yaml_preserving_comments()`` call. Use for any read-modify-write
        cycle over a file a user may have hand-authored; use ``yaml.safe_load()``
        (via plain ``open()``) for read-only inspection instead, since the
        round-trip loader is unnecessary overhead when nothing gets written back.
        """
        with path.open("r", encoding="utf-8") as fh:
            return _ROUND_TRIP_YAML.load(fh)

    @staticmethod
    def _write_yaml_preserving_comments(path: Path, doc: Any) -> None:
        """Write back a mapping previously loaded by ``_read_yaml_preserving_comments()``.

        Only meaningful when *doc* is still the same (mutated) round-trip object —
        passing a plain ``dict`` here writes it without comments, same as
        ``_write_yaml()``.
        """
        with path.open("w", encoding="utf-8") as fh:
            _ROUND_TRIP_YAML.dump(doc, fh)
