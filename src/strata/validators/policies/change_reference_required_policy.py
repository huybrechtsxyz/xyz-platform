#!/usr/bin/env python3
"""Built-in policy: require an external change/ticket reference before deploying (ADR-0074 Phase 2).

Evaluates at the ``deploy_before`` phase — once per ``deploy run`` invocation,
before any stage executes, rather than per-stage like ``plan``/``deploy``
phase policies. This policy needs no plan/deployer/stage context, only
whether ``BaseDeployCommand._resolve_change_reference()`` populated
``PolicyContext.change_reference`` from ``--change-id``/``--change-system``/
``--reason`` (or the corresponding ``STRATA_CHANGE_*`` env vars).

Configuration
-------------
None. This policy takes no ``configuration`` options — it is a plain
existence check. Anything more specific (e.g. requiring a particular
``system``, or a particular ``classification``) is an ADR-0074 open item, not
implemented here.

Graceful degradation
--------------------
- No configuration service in context → pass (skip); matches every other
  policy's behavior when there's nothing to evaluate against.
- ``--dry-run`` deployments never reach this policy at all — the caller skips
  ``deploy_before`` evaluation entirely for dry-runs (ADR-0074: "dry_run
  invocations do not require a change reference").

Example configuration YAML::

    policies:
      - name: production-change-record
        type: change_reference_required
        phase: deploy_before
        enforcement: deny
"""

from strata.validators.policies.base_policy import BasePolicy, PolicyContext, PolicyResult


class ChangeReferenceRequiredPolicy(BasePolicy):
    """Fails when no change/ticket reference was supplied for this deploy."""

    def evaluate(self, context: PolicyContext) -> PolicyResult:
        if context.change_reference is not None:
            return PolicyResult(passed=True, policy_name=self.name, enforcement=self.enforcement)

        return PolicyResult(
            passed=False,
            policy_name=self.name,
            enforcement=self.enforcement,
            violations=[
                "No change/ticket reference supplied for this deployment. Provide --change-id "
                "(with --change-system and --reason), or the corresponding STRATA_CHANGE_ID/"
                "STRATA_CHANGE_SYSTEM/STRATA_CHANGE_REASON environment variables."
            ],
        )
