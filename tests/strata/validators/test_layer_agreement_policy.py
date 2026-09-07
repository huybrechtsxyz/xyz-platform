"""Tests for LayerAgreementPolicy (ADR-0072).

Covers:
- skip: no file_path/work_path, file outside work_path
- skip: no deployment_service, no spec.layers.segments declared
- skip: no configuration_service, no resolves: layers convention applies
- pass: explicit segment values agree with path-derived values
- fail: explicit segment value disagrees with path-derived value (deny/warn)
- pass: explicit segment not reachable from path (shallower deployment) — nothing to compare
"""

from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock

from strata.models.configuration_model import (
    ConfigurationLayerModel,
    ConfigurationMetaModel,
    ConfigurationModel,
    ConfigurationSpecModel,
    PathConventionModel,
)
from strata.models.deployment_model import LayersModel
from strata.models.policy_model import PolicyModel
from strata.validators.policies.base_policy import PolicyContext
from strata.validators.policies.layer_agreement_policy import LayerAgreementPolicy

WORK_PATH = Path("/work")


def _make_policy(enforcement: str = "deny") -> PolicyModel:
    return PolicyModel(
        name="test_layer_agreement",
        type="layer_agreement",
        phase="validate",
        enforcement=enforcement,
    )


def _make_convention(
    name: str = "hub-scheme",
    scope: str = "deploy/hubs/*",
    pattern: str = "deploy/hubs/{hub}/{ring}",
) -> PathConventionModel:
    return PathConventionModel(
        name=name,
        scope=scope,
        pattern=pattern,
        resolves="layers",
        segments=[ConfigurationLayerModel(name="hub"), ConfigurationLayerModel(name="ring")],
    )


def _make_config_model(paths=None) -> ConfigurationModel:
    return ConfigurationModel(
        meta=ConfigurationMetaModel(name="test-config"),
        spec=ConfigurationSpecModel(paths=paths),
    )


def _make_context(
    file_path: Optional[Path],
    layers: Optional[LayersModel],
    config_model: Optional[ConfigurationModel],
    work_path: Optional[Path] = WORK_PATH,
) -> PolicyContext:
    dep_svc = MagicMock()
    dep_svc.model.spec.layers = layers

    cfg_svc = None
    if config_model is not None:
        cfg_svc = MagicMock()
        cfg_svc.model = config_model

    return PolicyContext(
        phase="validate",
        work_path=work_path,
        file_path=file_path,
        deployment_service=dep_svc,
        configuration_service=cfg_svc,
    )


class TestLayerAgreementPolicySkip:
    def test_skip_no_file_path(self):
        policy = LayerAgreementPolicy(_make_policy())
        result = policy.evaluate(PolicyContext(phase="validate", work_path=WORK_PATH, file_path=None))
        assert result.passed is True
        assert result.details == {"skipped": "no file_path in context"}

    def test_skip_no_work_path(self):
        policy = LayerAgreementPolicy(_make_policy())
        result = policy.evaluate(PolicyContext(phase="validate", work_path=None, file_path=Path("/work/x.yaml")))
        assert result.passed is True
        assert result.details == {"skipped": "no file_path in context"}

    def test_skip_file_outside_work_path(self):
        policy = LayerAgreementPolicy(_make_policy())
        context = PolicyContext(phase="validate", work_path=WORK_PATH, file_path=Path("/elsewhere/x.yaml"))
        result = policy.evaluate(context)
        assert result.passed is True
        assert "not under work_path" in result.details["skipped"]

    def test_skip_no_deployment_service(self):
        policy = LayerAgreementPolicy(_make_policy())
        context = PolicyContext(
            phase="validate", work_path=WORK_PATH, file_path=WORK_PATH / "deploy/hubs/hub1/prd/deploy.yaml"
        )
        result = policy.evaluate(context)
        assert result.passed is True
        assert result.details == {"skipped": "no deployment loaded"}

    def test_skip_no_explicit_segments(self):
        policy = LayerAgreementPolicy(_make_policy())
        layers = LayersModel(follows="hub-scheme")  # no segments declared
        context = _make_context(
            file_path=WORK_PATH / "deploy/hubs/hub1/prd/deploy.yaml",
            layers=layers,
            config_model=_make_config_model(paths=[_make_convention()]),
        )
        result = policy.evaluate(context)
        assert result.passed is True
        assert result.details == {"skipped": "no explicit spec.layers.segments values declared"}

    def test_skip_no_configuration_service(self):
        policy = LayerAgreementPolicy(_make_policy())
        layers = LayersModel(follows="hub-scheme", segments={"hub": "hub1"})
        context = _make_context(
            file_path=WORK_PATH / "deploy/hubs/hub1/prd/deploy.yaml", layers=layers, config_model=None
        )
        result = policy.evaluate(context)
        assert result.passed is True
        assert result.details == {"skipped": "no configuration service available"}

    def test_skip_no_convention_applies(self):
        policy = LayerAgreementPolicy(_make_policy())
        layers = LayersModel(segments={"hub": "hub1"})
        context = _make_context(
            file_path=WORK_PATH / "unrelated/path/deploy.yaml",
            layers=layers,
            config_model=_make_config_model(paths=[_make_convention()]),
        )
        result = policy.evaluate(context)
        assert result.passed is True
        assert result.details == {"skipped": "no resolves: layers convention applies to this file"}


class TestLayerAgreementPolicyEvaluate:
    def test_agrees_passes(self):
        policy = LayerAgreementPolicy(_make_policy())
        layers = LayersModel(follows="hub-scheme", segments={"hub": "hub1", "ring": "prd"})
        context = _make_context(
            file_path=WORK_PATH / "deploy/hubs/hub1/prd/deploy.yaml",
            layers=layers,
            config_model=_make_config_model(paths=[_make_convention()]),
        )
        result = policy.evaluate(context)
        assert result.passed is True
        assert result.violations == []

    def test_disagrees_fails(self):
        policy = LayerAgreementPolicy(_make_policy(enforcement="deny"))
        # explicit hub2 disagrees with the path's hub1
        layers = LayersModel(follows="hub-scheme", segments={"hub": "hub2"})
        context = _make_context(
            file_path=WORK_PATH / "deploy/hubs/hub1/prd/deploy.yaml",
            layers=layers,
            config_model=_make_config_model(paths=[_make_convention()]),
        )
        result = policy.evaluate(context)
        assert result.passed is False
        assert result.enforcement == "deny"
        assert len(result.violations) == 1
        assert "hub" in result.violations[0]
        assert "hub2" in result.violations[0]
        assert "hub1" in result.violations[0]

    def test_disagrees_warn_enforcement_preserved(self):
        policy = LayerAgreementPolicy(_make_policy(enforcement="warn"))
        layers = LayersModel(follows="hub-scheme", segments={"hub": "hub2"})
        context = _make_context(
            file_path=WORK_PATH / "deploy/hubs/hub1/prd/deploy.yaml",
            layers=layers,
            config_model=_make_config_model(paths=[_make_convention()]),
        )
        result = policy.evaluate(context)
        assert result.passed is False
        assert result.enforcement == "warn"

    def test_shallow_deployment_segment_not_reachable_passes(self):
        """A hub-only deployment's explicit 'hub' value has nothing to compare
        against for 'ring' since the path never reaches that position — not a
        violation."""
        policy = LayerAgreementPolicy(_make_policy())
        layers = LayersModel(follows="hub-scheme", segments={"hub": "hub1"})
        context = _make_context(
            file_path=WORK_PATH / "deploy/hubs/hub1/deploy.yaml",
            layers=layers,
            config_model=_make_config_model(paths=[_make_convention()]),
        )
        result = policy.evaluate(context)
        assert result.passed is True
        assert result.violations == []

    def test_shortfall_one_explicit_deepest_segment_not_falsely_flagged(self):
        """Regression: at shortfall == 1 (the file sits directly in the 'hub'
        directory, with no 'ring' subdirectory — the deployment's real directory
        depth is exactly segment-count - 1), an explicit 'ring' value must not be
        compared against the deployment's own filename. Previously match_pattern()
        incorrectly captured the filename as 'ring's derived value (since path
        length happened to equal pattern length), producing a false disagreement
        violation whenever the explicit value didn't coincidentally equal the
        deployment's own filename."""
        policy = LayerAgreementPolicy(_make_policy())
        layers = LayersModel(follows="hub-scheme", segments={"hub": "hub1", "ring": "prd"})
        context = _make_context(
            file_path=WORK_PATH / "deploy/hubs/hub1/deploy.yaml",
            layers=layers,
            config_model=_make_config_model(paths=[_make_convention()]),
        )
        result = policy.evaluate(context)
        assert result.passed is True
        assert result.violations == []

    def test_auto_detected_convention_agrees(self):
        """follows omitted — convention auto-detected from the path; still checked."""
        policy = LayerAgreementPolicy(_make_policy())
        layers = LayersModel(segments={"hub": "hub1", "ring": "prd"})
        context = _make_context(
            file_path=WORK_PATH / "deploy/hubs/hub1/prd/deploy.yaml",
            layers=layers,
            config_model=_make_config_model(paths=[_make_convention()]),
        )
        result = policy.evaluate(context)
        assert result.passed is True
