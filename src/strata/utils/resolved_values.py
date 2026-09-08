"""Resolved runtime values (variables, secrets, feature flags) and environment injection helpers.

This module lives in ``utils/`` so that deployers and other orchestration-tier components
can import ``ResolvedValues`` and ``inject_*`` without crossing into the controller layer.
``ValueController`` (which *produces* these values) imports from here as well.
"""

import json
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, Generator, List, Optional, Set, Tuple


@dataclass
class ResolvedValues:
    """Concrete runtime values resolved from variables, secrets, and feature flags.

    Attributes:
        variables              (Dict[str, Any]):          Resolved configuration variables.
        secrets                (Dict[str, Any]):          Resolved secrets  (treat as sensitive).
        features               (Dict[str, Optional[bool]]): Resolved feature-flag booleans.
        stage_outputs          (Dict[str, Any]):          Non-sensitive outputs collected from
                                                           preceding deployment stages.
                                                           Injected as TF_VAR_* / verbatim env
                                                           vars into every subsequent stage.
        stage_outputs_sensitive (Dict[str, Any]):         Sensitive outputs from preceding stages
                                                           (e.g. Terraform ``sensitive = true``).
                                                           Available internally but never injected
                                                           into subprocess environments.
        errors                 (List[str]):               Resolution errors / warnings.
        store_unavailable_errors (List[str]):              Subset of resolution failures caused
                                                           by a store being unreachable/unauthenticated
                                                           (as opposed to a key genuinely not
                                                           existing). Always treated as fatal by
                                                           ``ValueController.resolve_values()``,
                                                           regardless of ``strict``.
    """

    variables: Dict[str, Any] = field(default_factory=dict)
    secrets: Dict[str, Any] = field(default_factory=dict)
    features: Dict[str, Optional[bool]] = field(default_factory=dict)
    stage_outputs: Dict[str, Any] = field(default_factory=dict)
    stage_outputs_sensitive: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    # Store/connectivity failures (as opposed to "key genuinely not found") —
    # ALWAYS fatal regardless of strict mode; see ValueController.resolve_values().
    store_unavailable_errors: List[str] = field(default_factory=list)
    variable_notes: Dict[str, str] = field(default_factory=dict)
    secret_notes: Dict[str, str] = field(default_factory=dict)
    feature_notes: Dict[str, str] = field(default_factory=dict)
    # Provenance: which environment file each declared value came from
    variable_sources: Dict[str, str] = field(default_factory=dict)
    secret_sources: Dict[str, str] = field(default_factory=dict)
    feature_sources: Dict[str, str] = field(default_factory=dict)
    # Merge order recorded during merge_envfiles()
    merge_order: List[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        """Return True when no values were resolved."""
        return not (self.variables or self.secrets or self.features or self.stage_outputs)

    def for_stage(self, allowed_secrets: Optional[List[str]] = None) -> "ResolvedValues":
        """Return a copy scoped to the given stage's secret allowlist.

        STRATA_CONTEXT (variables, features, stage_outputs) passes through unfiltered.
        STRATA_SENSITIVE (secrets, stage_outputs_sensitive) is filtered to only
        the keys in ``allowed_secrets``.

        Args:
            allowed_secrets: List of secret key names this stage may access.
                - ``None`` or ``[]`` → no secrets, no sensitive outputs.
                - ``['*']``          → all secrets + all sensitive outputs (escape hatch).
                - ``['a', 'b']``     → only those keys from secrets + stage_outputs_sensitive.
        """
        if not allowed_secrets:
            return ResolvedValues(
                variables=dict(self.variables),
                secrets={},
                features=dict(self.features),
                stage_outputs=dict(self.stage_outputs),
                stage_outputs_sensitive={},
                errors=list(self.errors),
                variable_notes=dict(self.variable_notes),
                secret_notes={},
                feature_notes=dict(self.feature_notes),
                variable_sources=dict(self.variable_sources),
                secret_sources={},
                feature_sources=dict(self.feature_sources),
                merge_order=list(self.merge_order),
            )

        if allowed_secrets == ["*"]:
            return ResolvedValues(
                variables=dict(self.variables),
                secrets=dict(self.secrets),
                features=dict(self.features),
                stage_outputs=dict(self.stage_outputs),
                stage_outputs_sensitive=dict(self.stage_outputs_sensitive),
                errors=list(self.errors),
                variable_notes=dict(self.variable_notes),
                secret_notes=dict(self.secret_notes),
                feature_notes=dict(self.feature_notes),
                variable_sources=dict(self.variable_sources),
                secret_sources=dict(self.secret_sources),
                feature_sources=dict(self.feature_sources),
                merge_order=list(self.merge_order),
            )

        allowed = set(allowed_secrets)
        return ResolvedValues(
            variables=dict(self.variables),
            secrets={k: v for k, v in self.secrets.items() if k in allowed},
            features=dict(self.features),
            stage_outputs=dict(self.stage_outputs),
            stage_outputs_sensitive={k: v for k, v in self.stage_outputs_sensitive.items() if k in allowed},
            errors=list(self.errors),
            variable_notes=dict(self.variable_notes),
            secret_notes={k: v for k, v in self.secret_notes.items() if k in allowed},
            feature_notes=dict(self.feature_notes),
            variable_sources=dict(self.variable_sources),
            secret_sources={k: v for k, v in self.secret_sources.items() if k in allowed},
            feature_sources=dict(self.feature_sources),
            merge_order=list(self.merge_order),
        )

    def debug_summary(self) -> Dict[str, Any]:
        """Return a debug-safe representation of STRATA_CONTEXT and STRATA_SENSITIVE.

        STRATA_CONTEXT (variables, features, stage_outputs) — values shown as-is.
        STRATA_SENSITIVE (secrets, stage_outputs_sensitive) — keys listed, values masked as '***'.
        Safe to pass to structured logging; never write to stdout without --verbose.
        """
        return {
            "strata_context": {
                "variables": dict(self.variables),
                "features": {k: v for k, v in self.features.items() if v is not None},
                "stage_outputs": dict(self.stage_outputs),
            },
            "strata_sensitive": {
                "secrets": {k: "***" for k in self.secrets},
                "stage_outputs_sensitive": {k: "***" for k in self.stage_outputs_sensitive},
            },
        }

    def as_compose_env(self) -> Dict[str, str]:
        """Return all resolved values as a flat env-var dict for Docker Compose ``${KEY}`` substitution.

        Key names are used verbatim (no prefix) — Docker reads them directly from
        the process environment.

        Merge order (later entry wins on key collision):
          features → variables → secrets → stage_outputs

        Feature flags with a ``None`` value are skipped.
        Secrets are included; callers should avoid logging this dict.
        """
        result: Dict[str, str] = {}
        for key, val in self.features.items():
            if val is not None:
                result[key] = str(val).lower()  # "true" / "false"
        for key, val in self.variables.items():
            result[key] = str(val)
        for key, val in self.secrets.items():
            result[key] = str(val)
        for key, val in self.stage_outputs.items():
            if val is not None:
                result[key] = json.dumps(val) if isinstance(val, (dict, list)) else str(val)
        return result

    def as_tf_vars(self) -> Dict[str, str]:
        """Return all resolved values as a ``TF_VAR_*`` environment-variable dict.

        Intended for injection into subprocess calls (e.g. terraform init/plan/apply).
        Variables and features are considered non-sensitive; secrets are included
        but callers should avoid logging this dict.

        Naming:
          - variables     → TF_VAR_<key>
          - secrets       → TF_VAR_<key>
          - features      → TF_VAR_<key>  (bool serialised as "true" / "false")
          - stage_outputs → TF_VAR_<key>  (complex values JSON-encoded)
        """
        result: Dict[str, str] = {}
        for key, val in self.variables.items():
            result[f"TF_VAR_{key}"] = str(val)
        for key, val in self.features.items():
            if val is not None:
                result[f"TF_VAR_{key}"] = str(val).lower()  # "true" / "false"
        for key, val in self.secrets.items():
            result[f"TF_VAR_{key}"] = str(val)
        for key, val in self.stage_outputs.items():
            if val is not None:
                result[f"TF_VAR_{key}"] = json.dumps(val) if isinstance(val, (dict, list)) else str(val)
        return result


# ---------------------------------------------------------------------------
# ${var:KEY} / ${secret:KEY} / ${feature:KEY} expression substitution (ADR-0075)
#
# Shared by TerraformDeployer (spec.provisioners[].backend.configuration) and
# HelmDeployer (rendered values.yaml) — one implementation, not a per-provisioner
# copy. Both provisioners' "plumbing" config (state-backend settings, Helm chart
# values) needs the same operation: substitute a typed reference into a string
# using already-resolved ResolvedValues, and fail loud (never silently pass
# through) when a reference doesn't resolve.
# ---------------------------------------------------------------------------

EXPR_PATTERN = re.compile(r"\$\{(var|secret|feature):([^}]+)\}")


def resolve_expr_string(value: str, resolved: ResolvedValues) -> Tuple[str, List[str]]:
    """Partial ``re.sub`` over *value*, substituting every ``${var:}``/``${secret:}``/
    ``${feature:}`` reference found (a token may appear anywhere in the string,
    possibly alongside literal text or other references).

    Every match must resolve — an unmatched key is always an error, never a
    silent pass-through (ADR-0075's "fail loud" decision driver).

    Returns:
        ``(resolved_value, errors)``. ``resolved_value`` has every *resolvable*
        match substituted even when some matches fail — callers should treat a
        non-empty ``errors`` list as fatal rather than use the partially-resolved
        string.
    """
    errors: List[str] = []

    def _replace(match: "re.Match[str]") -> str:
        kind, key = match.group(1), match.group(2)
        if kind == "var":
            if key in resolved.variables:
                return str(resolved.variables[key])
            errors.append(f"no variable named '{key}'")
            return match.group(0)
        if kind == "secret":
            if key in resolved.secrets:
                return str(resolved.secrets[key])
            errors.append(f"no secret named '{key}'")
            return match.group(0)
        if kind == "feature":
            if key in resolved.features and resolved.features[key] is not None:
                return str(resolved.features[key]).lower()
            errors.append(f"no feature named '{key}'")
            return match.group(0)
        return match.group(0)  # pragma: no cover — unreachable, EXPR_PATTERN only matches the three kinds above

    result = EXPR_PATTERN.sub(_replace, value)
    return result, errors


def collect_expr_refs(node: Any) -> Set[Tuple[str, str]]:
    """Recursively walk any structure (dict/list/str; other leaf types are ignored)
    and return every ``(kind, key)`` pair referenced anywhere in it.

    No "must be nested under a key named env" restriction — safe to run as an
    unrestricted full-tree walk precisely because the ``kind:`` prefix makes a
    false-positive match implausible (nothing but a real reference produces the
    literal substring ``${var:``, ``${secret:``, or ``${feature:``).
    """
    refs: Set[Tuple[str, str]] = set()
    if isinstance(node, dict):
        for child in node.values():
            refs |= collect_expr_refs(child)
    elif isinstance(node, list):
        for item in node:
            refs |= collect_expr_refs(item)
    elif isinstance(node, str):
        for match in EXPR_PATTERN.finditer(node):
            refs.add((match.group(1), match.group(2)))
    return refs


@contextmanager
def inject_compose_env(resolved: Optional[ResolvedValues]) -> Generator[None, None, None]:
    """Context manager that injects compose env vars into ``os.environ`` and removes them on exit.

    Unlike ``inject_tf_vars``, keys are used verbatim (no ``TF_VAR_`` prefix).
    Accepts ``None`` for convenience — behaves as a no-op when ``resolved`` is ``None``
    or when ``as_compose_env()`` returns an empty dict.
    """
    env_vars = resolved.as_compose_env() if resolved else {}
    if not env_vars:
        yield
        return

    prev: Dict[str, Optional[str]] = {}
    for key, val in env_vars.items():
        prev[key] = os.environ.get(key)
        os.environ[key] = val

    try:
        yield
    finally:
        for key, original in prev.items():
            if original is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original


@contextmanager
def inject_tf_vars(resolved: ResolvedValues) -> Generator[None, None, None]:
    """Context manager that injects TF_VAR_* env vars and removes them on exit.

    Since ``run_command`` snapshots ``os.environ`` at call time, variables must
    be present before each subprocess call.  Use this context around any step
    that runs a terraform sub-process:

        with inject_tf_vars(self._resolved_values):
            result = self._tf.apply(...)
    """
    tf_vars = resolved.as_tf_vars() if resolved else {}
    if not tf_vars:
        yield
        return

    prev: Dict[str, Optional[str]] = {}
    for key, val in tf_vars.items():
        prev[key] = os.environ.get(key)
        os.environ[key] = val

    try:
        yield
    finally:
        for key, original in prev.items():
            if original is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original


def apply_input_mapping(
    upstream_outputs: Dict[str, Any],
    mapping: Optional[Dict[str, str]] = None,
    prefix: Optional[str] = None,
    select: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Apply mapping/prefix/select to upstream outputs for downstream injection.

    Args:
        upstream_outputs: Raw outputs from the upstream provisioner.
        mapping: Optional key rename map (upstream_name → downstream_name).
        prefix: Optional prefix to add to all keys. Mutually exclusive with mapping.
        select: Optional allowlist of output names to forward.

    Returns:
        Transformed output dict ready for injection.

    Raises:
        ValueError: When selected keys are missing from upstream outputs.
    """
    if select:
        filtered = {k: v for k, v in upstream_outputs.items() if k in select}
        missing = set(select) - set(upstream_outputs.keys())
        if missing:
            raise ValueError(f"Expected outputs not found in upstream: {sorted(missing)}")
    else:
        filtered = dict(upstream_outputs)

    if mapping:
        return {downstream: filtered[upstream] for upstream, downstream in mapping.items() if upstream in filtered}
    elif prefix:
        return {f"{prefix}{k}": v for k, v in filtered.items()}
    else:
        return filtered


def collect_inputs_from_keys(inputs_from_list) -> set:
    """Collect all downstream variable key names that inputs_from will provide.

    Used by Gap 3 input validation to treat these keys as "supplied" even though
    their values are not known at build time.

    Args:
        inputs_from_list: List of ProvisionerInputMappingModel instances.

    Returns:
        Set of downstream variable key names.
    """
    keys: set = set()
    if not inputs_from_list:
        return keys

    for inp in inputs_from_list:
        if inp.mapping:
            keys.update(inp.mapping.values())
        elif inp.select:
            if inp.prefix:
                keys.update(f"{inp.prefix}{k}" for k in inp.select)
            else:
                keys.update(inp.select)
    return keys
