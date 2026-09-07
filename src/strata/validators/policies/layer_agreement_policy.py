#!/usr/bin/env python3
"""Built-in policy: layer agreement enforcement (ADR-0072).

Checks that an explicitly-declared ``deployment.spec.layers.segments`` value
agrees with the value the deployment file's own path would derive for that
same segment, against the ``configuration.spec.paths`` convention
(``resolves: layers``) it resolves to.

Declaring a ``resolves: layers`` convention is what turns *derivation* on at
all (ADR-0072's "no policy declared -> no oversight" principle, applied at the
schema level). Whether an explicit override is *allowed to disagree* with what
the path implies is a separate, opt-in dial — this policy — reusing the
standard ``enforcement: deny | warn | audit`` convention every other policy
already uses, rather than inventing a new toggle.

Example::

    policies:
      - name: layer-agreement
        type: layer_agreement
        phase: validate
        enforcement: warn

Graceful degradation (never fails on missing data):

- No ``file_path``/``work_path`` in context -> pass, skipped
- No deployment loaded, or ``spec.layers.segments`` not declared -> pass,
  skipped (nothing explicit to check agreement against)
- No configuration service, or no ``resolves: layers`` convention applies ->
  pass, skipped (``resolve_layers()``/``path_convention`` is what reports a
  genuine misconfiguration here, not this policy)
- A segment present in ``spec.layers.segments`` but not reachable from the
  path (deployment shallower than the pattern at that position) -> nothing to
  compare, not a violation
"""

from typing import Dict, List

from strata.models.policy_model import PolicyModel
from strata.validators.policies.base_policy import BasePolicy, PolicyContext, PolicyResult


class LayerAgreementPolicy(BasePolicy):
    """Validate that explicit spec.layers.segments values agree with path-derived ones."""

    def __init__(self, policy_model: PolicyModel) -> None:
        super().__init__(policy_model)

    def evaluate(self, context: PolicyContext) -> PolicyResult:
        if context.file_path is None or context.work_path is None:
            return self._skip("no file_path in context")

        try:
            rel_path = context.file_path.relative_to(context.work_path).as_posix()
        except ValueError:
            return self._skip(f"file_path not under work_path: {context.file_path}")

        if context.deployment_service is None:
            return self._skip("no deployment loaded")
        deployment_model = getattr(context.deployment_service, "model", None)
        deployment_spec = getattr(deployment_model, "spec", None)
        layers = getattr(deployment_spec, "layers", None)
        explicit_segments: Dict[str, str] = dict(getattr(layers, "segments", None) or {}) if layers is not None else {}
        if not explicit_segments:
            return self._skip("no explicit spec.layers.segments values declared")

        if context.configuration_service is None:
            return self._skip("no configuration service available")
        config_model = getattr(context.configuration_service, "model", None)
        config_spec = getattr(config_model, "spec", None)
        conventions = getattr(config_spec, "paths", None) or []

        from strata.utils.path_convention import _dir_only, match_pattern, resolve_layers

        resolution = resolve_layers(rel_path, layers, conventions)
        if resolution.convention is None:
            # No resolves: layers convention applies here (or the match itself
            # is ambiguous/misconfigured) — resolve_layers() and the
            # path_convention policy are what report that; this policy has
            # nothing to compare an explicit value against.
            return self._skip("no resolves: layers convention applies to this file")

        derived = match_pattern(_dir_only(rel_path), resolution.convention.pattern) or {}

        violations: List[str] = []
        for name, explicit_value in sorted(explicit_segments.items()):
            derived_value = derived.get(name)
            if derived_value is not None and derived_value != explicit_value:
                violations.append(
                    f"segment '{name}' = '{explicit_value}' (explicit) disagrees with "
                    f"'{derived_value}' (derived from path '{rel_path}' via convention "
                    f"'{resolution.convention.name}')"
                )

        return PolicyResult(
            passed=len(violations) == 0,
            policy_name=self.name,
            enforcement=self.enforcement,
            violations=violations,
        )

    def _skip(self, reason: str) -> PolicyResult:
        return PolicyResult(
            passed=True,
            policy_name=self.name,
            enforcement=self.enforcement,
            details={"skipped": reason},
        )
