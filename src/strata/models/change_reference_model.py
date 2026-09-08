#!/usr/bin/env python3
"""Change reference — links a deployment to an external change/ticket record (ADR-0074).

Phase 1 (this module): capture and record only. ``ChangeReferenceModel`` is an
assertion made by an identified actor at a known time — it is not proof that the
referenced record exists, was approved, or covers this deployment. Verification
against a live tracker, and enforcement (requiring a reference before a deploy can
proceed), are later, separate phases — see ADR-0074.

The same model instance is reused, unmodified, by the deployment manifest, the
deploy log, and the forwarded audit event (one implementation, not copies).
"""

from typing import Optional

from pydantic import Field

from strata.models.common_models import PlatformBaseModel


class ChangeReferenceModel(PlatformBaseModel):
    """Reference to an external change/ticket record justifying a deployment.

    ``system`` is intentionally an open string, not an enum — teams with internal
    or unsupported trackers must be able to use this without a strata release.
    """

    system: str = Field(
        description="Tracker identifier, e.g. 'jira', 'azure_devops', 'servicenow', 'github', or any internal name"
    )
    id: str = Field(description="Change/ticket identifier in the tracker, e.g. 'OPS-1234', 'CHG0041234'")
    reason: str = Field(description="Operator-supplied justification for this deployment")
    classification: Optional[str] = Field(
        default=None,
        description=(
            "Change classification, e.g. 'emergency'/'normal'/'standard' — an open string, validated "
            "against configuration's change_tracking.classifications when that allowlist is set"
        ),
    )
    title: Optional[str] = Field(
        default=None,
        description="Snapshot of the record's title at invocation time — evidence for offline review, not proof of current state",
    )
    url: Optional[str] = Field(
        default=None, description="Link to the change record, supplied or resolved from configuration's url_template"
    )
    supplied_by: str = Field(
        description="Actor who supplied this reference (same resolution as deployment's deployed_by)"
    )
    supplied_at: str = Field(description="ISO-8601 timestamp when this reference was supplied")
