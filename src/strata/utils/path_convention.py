"""Shared utilities for path convention validation.

Provides:
- ``match_pattern()``  — match a file path against a convention pattern and return
  captured segment values.
- ``resolve_layers()`` — resolve a deployment's layer/segment values against
  ``configuration.spec.paths`` conventions with ``resolves: layers`` (ADR-0072).
  The ONLY place Level 1 (which convention) + Level 2 (each segment's value)
  precedence is implemented — every caller that needs a deployment's resolved
  layer values must call this, not reimplement any part of it.
- ``evaluate_conventions()`` — top-level evaluator; for each segment's
  ``validate`` rule, dispatches on ``ExpressionModel.kind`` (ADR-0073) instead
  of shape-sniffing a raw string: ``kind=yaml`` runs a JMESPath query against
  the loaded ``ConfigurationModel``'s ``model_dump()``; ``kind=path`` calls
  ``ExpressionModel.check_path()``, which reuses ``evaluate_file_rule()`` below.
- ``evaluate_file_rule()`` — expand placeholder references in a file path template
  and check existence on disk.
- ``build_path_from_pattern()`` — inverse of ``match_pattern()``: substitute named
  segment values into a pattern to build a concrete path.
- ``find_tenant_path_pattern()`` / ``resolve_tenant_file_path()`` — resolve a
  tenant's on-disk file path, honoring a ``spec.paths`` convention marked
  ``resolves: tenant`` if declared, else falling back to the built-in
  ``tenants/{code}.yaml`` convention (ADR-0012).
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:
    from strata.models.configuration_model import PathConventionModel
    from strata.models.deployment_model import LayersModel


def _dir_only(rel_path: str) -> str:
    """Return *rel_path* with its final path component (the file itself) stripped.

    Used only for deriving segment *values* (Level 2 of :func:`resolve_layers`,
    and the equivalent comparison in ``LayerAgreementPolicy``) — never for Level 1
    "which convention applies" matching, which intentionally keeps using the full
    path (a file's own name is allowed to satisfy a convention's overall shape,
    e.g. a single-segment convention matching a deployment file at the workspace
    root; that's a structural "does it belong" check, not a segment value).

    Segment *values* must only ever come from real *directories* a deployment file
    lives under — its own filename is never a meaningful segment value.
    :func:`match_pattern`'s "trailing path parts are ignored" contract normally
    absorbs the filename as an extra trailing part beyond the deepest matched
    segment (the common case: the file sits one level deeper than the
    convention's full segment depth). But when a deployment happens to sit
    exactly one directory shallower than that (its real directory depth is
    exactly segment-count − 1), the path has no extra trailing part — the
    filename itself lines up with the last placeholder position instead of being
    trailing, and gets captured as if it were that segment's real directory
    value. Stripping the filename before matching removes the ambiguity
    entirely: only real directories are ever considered as segment values.
    """
    parent = Path(rel_path).parent
    parent_str = parent.as_posix()
    return "" if parent_str == "." else parent_str


def match_pattern(rel_path: str, pattern: str) -> Optional[Dict[str, str]]:
    """Match a file path against a convention pattern, returning captured segment values.

    The pattern uses ``{segment}`` placeholders that capture exactly one path part
    (never ``/``). Literal parts must match verbatim. Trailing path parts after the
    pattern are ignored — files deeper than the pattern still match.

    Returns a dict of ``{segment_name: value}`` on match, or ``None`` if the path
    does not match the pattern (no violation; the file is skipped).

    Examples::

        match_pattern(
            "zones/europe/customers/contoso/prd/deploy.yaml",
            "zones/{zone}/customers/{tenant}/{env}"
        )
        # → {"zone": "europe", "tenant": "contoso", "env": "prd"}

        match_pattern("zones/europe/shared.yaml", "zones/{zone}/customers/{tenant}/{env}")
        # → None  (path is shallower than pattern — skip, not a violation)

        match_pattern("customers/contoso/tenant.yaml", "customers/{tenant}")
        # → {"tenant": "contoso"}
    """
    path_parts = Path(rel_path).parts
    pattern_parts = [p for p in pattern.split("/") if p]

    if len(path_parts) < len(pattern_parts):
        return None  # file is shallower than pattern — skip

    captures: Dict[str, str] = {}
    for i, pat_part in enumerate(pattern_parts):
        actual_part = path_parts[i]
        if pat_part.startswith("{") and pat_part.endswith("}"):
            segment_name = pat_part[1:-1]
            captures[segment_name] = actual_part
        else:
            if actual_part != pat_part:
                return None  # literal segment mismatch

    return captures


# ---------------------------------------------------------------------------
# Layer/segment resolution (ADR-0072) — the single shared entry point
# ---------------------------------------------------------------------------


@dataclass
class LayerResolution:
    """Result of resolving a deployment's layer values (ADR-0072).

    Three distinct states — check ``convention`` and ``error``, never ``values``
    alone, since a non-empty ``values`` does **not** imply resolution succeeded:

    - **Resolved** (``convention`` set, ``error`` None) — ``values`` are the Level 1
      + Level 2 outcome, ordered by ``convention.segments``. Only in this state is
      an artifact path defined, because segment *order* comes from the convention.
    - **Pass-through** (both None) — no ``resolves: layers`` convention exists at
      all, so layering simply isn't in play. ``values`` carries whatever the
      deployment declared, unvalidated, so consumers that only need a lookup (e.g.
      GitOps templates reading ``layers.environment``) keep working. There is no
      artifact path: with no convention there is no defined segment order, and
      guessing one from YAML key order would be exactly the kind of implicit
      behavior ADR-0072 exists to remove.
    - **Failed** (``error`` set) — unknown ``follows`` name, ambiguous match, or a
      deployment declaring layers that no existing convention claims. ``values``
      still carries the declared segments so callers can degrade gracefully, but
      the error must be surfaced, not swallowed.
    """

    convention: Optional["PathConventionModel"] = None
    values: Dict[str, str] = field(default_factory=dict)
    error: Optional[str] = None


def resolve_layers(
    rel_path: str,
    layers: Optional["LayersModel"],
    conventions: "List[PathConventionModel]",
) -> LayerResolution:
    """Resolve a deployment's layer/segment values (ADR-0072).

    The ONLY function implementing this precedence — every caller that needs a
    deployment's resolved layer values (validation, artifact-path construction,
    ``rules:`` checking, overlap/promote filtering) must call this, not
    reimplement any part of it.

    Level 1 — which convention applies:
        1. Explicit — ``layers.follows`` names a convention → use it. Error if the
           name doesn't exist among *conventions*' ``resolves: layers`` entries.
        2. Auto-detected — no ``follows`` → match *rel_path* against every
           ``resolves: layers`` convention's ``scope`` + ``pattern``. Error if more
           than one matches (ambiguous).
        3. None — neither explicit nor auto-detected → no convention applies;
           ``layers.segments`` (if any) is returned as unvalidated free-form data.

    Level 2 — each segment's value, once a convention is selected:
        1. Explicit — ``layers.segments[name]`` is set → use it. Always wins.
        2. Derived — *rel_path* matches the convention's ``pattern`` far enough to
           capture ``name`` → use the captured value.
        3. Default — the segment declares ``default`` → use it.
        4. Not applicable — none of the above → ``name`` is omitted from
           ``.values`` entirely (not an error).

    Args:
        rel_path: Deployment file path relative to work_path, forward-slash separated.
        layers: The deployment's ``spec.layers`` (a ``LayersModel`` instance), or
            ``None``.
        conventions: ``configuration.spec.paths`` entries — only those with
            ``resolves == "layers"`` are considered; others are ignored even if
            present in this list.

    Returns:
        A `LayerResolution`.
    """
    from fnmatch import fnmatch

    layer_conventions = [c for c in conventions if getattr(c, "resolves", None) == "layers"]

    follows = getattr(layers, "follows", None) if layers is not None else None
    explicit_segments: Dict[str, str] = dict(getattr(layers, "segments", None) or {}) if layers is not None else {}
    declares_layers = bool(follows or explicit_segments)

    convention: Optional["PathConventionModel"] = None

    if follows:
        convention = next((c for c in layer_conventions if c.name == follows), None)
        if convention is None:
            return LayerResolution(
                values=explicit_segments,
                error=(
                    f"spec.layers.follows '{follows}' does not name a configuration.spec.paths "
                    "convention declaring resolves: layers"
                ),
            )
    else:
        matches = [
            c
            for c in layer_conventions
            if fnmatch(rel_path, c.scope) and match_pattern(rel_path, c.pattern) is not None
        ]
        if len(matches) > 1:
            return LayerResolution(
                values=explicit_segments,
                error=(
                    f"Ambiguous resolves: layers match for '{rel_path}': "
                    f"{sorted(m.name for m in matches)} all match — give each convention a "
                    "distinguishing literal prefix segment in scope/pattern, or set spec.layers.follows explicitly"
                ),
            )
        convention = matches[0] if matches else None

    if convention is None:
        # A deployment that declares layer values while the configuration *does*
        # define resolves: layers conventions, yet none of them claims this file, is
        # a misconfiguration — almost always a pattern that no longer matches the
        # real tree. Without this check the failure is invisible: the declared values
        # still pass through, so nothing looks wrong, but no convention means no
        # segment order, so the artifact path silently becomes empty and the build
        # lands at the wrong place. Report it rather than let it pass.
        if declares_layers and layer_conventions:
            return LayerResolution(
                values=explicit_segments,
                error=(
                    f"'{rel_path}' declares spec.layers but no resolves: layers convention claims it "
                    f"(tried: {sorted(c.name for c in layer_conventions)}). Its artifact path would be "
                    "empty. Fix the convention's scope/pattern to cover this file, set "
                    "spec.layers.follows explicitly, or remove spec.layers from this deployment."
                ),
            )
        # Otherwise genuinely no layering in play — segments are unvalidated
        # free-form pass-through data, exactly as today's graceful "no scope match".
        return LayerResolution(values=explicit_segments)

    captures = match_pattern(_dir_only(rel_path), convention.pattern) or {}

    values: Dict[str, str] = {}
    for segment in convention.segments or []:
        name = segment.name
        explicit_value = explicit_segments.get(name)
        if explicit_value is not None:
            values[name] = explicit_value
        elif name in captures:
            values[name] = captures[name]
        elif segment.default is not None:
            values[name] = segment.default
        # else: not applicable — omitted entirely from values, not an error

    return LayerResolution(convention=convention, values=values)


# ---------------------------------------------------------------------------
# Validation rule: file existence check
# ---------------------------------------------------------------------------

_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def evaluate_file_rule(rule: str, captures: Dict[str, str], work_path: Path) -> Optional[str]:
    """Check that a file path rule resolves to an existing file on disk.

    Expands ``{placeholder}`` references in *rule* from *captures*, then checks
    ``work_path / expanded_path`` exists.

    Returns ``None`` on success (file exists or self-reference warning), or a
    violation message string if the file does not exist.

    Note: Self-references (file checking its own existence during creation) produce
    a warning string prefixed with "WARN:" — callers may treat these differently.
    """
    try:
        expanded = _PLACEHOLDER_RE.sub(lambda m: captures.get(m.group(1), m.group(0)), rule)
        target = work_path / expanded
        if not target.exists():
            return f"{expanded} does not exist"
        return None
    except Exception:
        return None  # never fail on unexpected errors


# ---------------------------------------------------------------------------
# Top-level evaluator
# ---------------------------------------------------------------------------


def evaluate_conventions(
    rel_path: str,
    conventions: "List[PathConventionModel]",
    work_path: Path,
    configuration_model=None,
    deployment_layers: Optional["LayersModel"] = None,
) -> List[str]:
    """Evaluate all matching conventions for a file and return violations.

    Args:
        rel_path: File path relative to ``work_path``, forward-slash separated.
        conventions: List of ``PathConventionModel`` entries from ``spec.paths``.
        work_path: Workspace root for file existence checks.
        configuration_model: Optional ``ConfigurationModel`` for ``spec.*`` rules.
        deployment_layers: Optional deployment ``spec.layers`` (a ``LayersModel``)
            for *rel_path* — required so ``rules:`` validates the *resolved* value
            (explicit -> derived -> default; see ``resolve_layers()``) for
            ``resolves: layers`` conventions, per ADR-0072. Every other convention
            keeps validating the raw ``match_pattern()`` capture, unchanged.

    Returns:
        List of violation strings.  Empty list means the file is compliant.
    """
    from fnmatch import fnmatch

    violations: List[str] = []
    layer_resolution: Optional[LayerResolution] = None

    for conv in conventions:
        # Step a: scope check
        if not fnmatch(rel_path, conv.scope):
            continue

        # Step b: pattern match
        captures = match_pattern(rel_path, conv.pattern)
        if captures is None:
            continue  # in scope but at different depth — not a violation

        # Step c: validate each segment
        if not conv.rules:
            continue

        # rules: validates the resolved value (Level 1 + Level 2 outcome) for
        # resolves: layers conventions, not the raw path capture — see ADR-0072.
        # Computed once per file (conventions list is identical across iterations).
        if conv.resolves == "layers":
            if layer_resolution is None:
                layer_resolution = resolve_layers(rel_path, deployment_layers, conventions)
                if layer_resolution.error:
                    # Report it here too rather than only validating the fallback
                    # values. This function is reachable from the path_convention
                    # policy on files that never went through
                    # DeploymentService._validate_deployment_layers() (which needs a
                    # configuration model), so this may be the only place a bad
                    # `follows` name or an ambiguous convention match is ever seen.
                    violations.append(f"layer resolution failed — {layer_resolution.error}")
            segment_values = layer_resolution.values
        else:
            segment_values = captures

        for segment_name, rule in conv.rules.items():
            value = segment_values.get(segment_name)
            if value is None:
                continue

            if rule.kind == "yaml":
                if configuration_model is None:
                    continue  # No configuration service available — skip gracefully
                try:
                    allowed_raw = rule.query(configuration_model.model_dump())
                except Exception:
                    continue  # unresolvable (missing field, wrong shape) — skip gracefully
                if not isinstance(allowed_raw, list):
                    continue
                allowed = {str(v) for v in allowed_raw if v is not None}
                if value not in allowed:
                    sorted_allowed = sorted(allowed)
                    violations.append(
                        f"convention '{conv.name}' \u2014 segment '{segment_name}' = '{value}' "
                        f"not in {rule.expression}: {sorted_allowed}"
                    )
            else:
                # kind == ExpressionKind.PATH — file existence rule. Model
                # validation (PathConventionModel.validate_rules_kind) already
                # guarantees rule.kind is yaml or path, nothing else, here.
                violation = rule.check_path(segment_values, work_path)
                if violation:
                    violations.append(
                        f"convention '{conv.name}' \u2014 segment '{segment_name}' = '{value}': {violation}"
                    )

    return violations


# ---------------------------------------------------------------------------
# Tenant file resolution — inverse direction (build a path FROM a pattern)
# ---------------------------------------------------------------------------

_BUILTIN_TENANT_PATTERN = "tenants/{code}.yaml"


def build_path_from_pattern(pattern: str, **values: str) -> str:
    """Build a concrete relative path from a ``{segment}`` pattern.

    Inverse of :func:`match_pattern`: substitutes each named value into its
    ``{segment}`` placeholder. Since every ``{segment}`` captures exactly one
    literal path part (see :func:`match_pattern`), this is a direct
    ``str.format()`` substitution — no additional parsing needed.

    Example::

        build_path_from_pattern("customers/{code}/customer.yaml", code="acme")
        # -> "customers/acme/customer.yaml"

    Raises:
        ValueError: *pattern* references a segment not present in *values*.
    """
    try:
        return pattern.format(**values)
    except KeyError as exc:
        raise ValueError(f"Pattern '{pattern}' requires segment {exc}, which was not provided") from exc


def find_tenant_path_pattern(configuration_model) -> Optional[str]:
    """Return the pattern of the ``spec.paths`` convention marked ``resolves: tenant``.

    Returns ``None`` if *configuration_model* is ``None``, has no ``spec.paths``,
    or none of its conventions declare ``resolves: tenant`` — callers should then
    fall back to the built-in ``tenants/{code}.yaml`` convention. Model validation
    already guarantees at most one such convention exists
    (``ConfigurationSpecModel.validate_single_tenant_path_resolver``).
    """
    if configuration_model is None:
        return None
    paths = getattr(getattr(configuration_model, "spec", None), "paths", None)
    if not paths:
        return None
    for conv in paths:
        if getattr(conv, "resolves", None) == "tenant":
            return conv.pattern
    return None


def resolve_tenant_relative_path(tenant_code: str, configuration_model=None) -> str:
    """Return a tenant's file path, relative to the workspace root (not yet joined).

    Uses the ``spec.paths`` convention marked ``resolves: tenant`` if declared
    on *configuration_model*, else the built-in ``tenants/{code}.yaml``
    convention (ADR-0012).
    """
    pattern = find_tenant_path_pattern(configuration_model) or _BUILTIN_TENANT_PATTERN
    return build_path_from_pattern(pattern, code=tenant_code)


def resolve_tenant_file_path(work_path: Path, tenant_code: str, configuration_model=None) -> Path:
    """Resolve the on-disk (absolute) path to a tenant's YAML file.

    Uses the ``spec.paths`` convention marked ``resolves: tenant`` if one is
    declared on *configuration_model* (substituting *tenant_code* into its
    ``{code}`` segment); otherwise falls back to the built-in
    ``tenants/{code}.yaml`` convention (ADR-0012) — identical to strata's
    behavior before this function existed, so workspaces that don't declare a
    custom convention see no change at all.

    Args:
        work_path: Workspace root.
        tenant_code: The deployment's ``spec.tenant`` value.
        configuration_model: Optional loaded ``ConfigurationModel``.

    Returns:
        Absolute path to the tenant's YAML file (existence not checked here).
    """
    return Path(work_path) / resolve_tenant_relative_path(tenant_code, configuration_model)
