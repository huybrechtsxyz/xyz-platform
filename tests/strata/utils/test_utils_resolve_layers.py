"""Tests for resolve_layers() — ADR-0072 layer/segment resolution.

Focus: the three LayerResolution states (resolved / pass-through / failed) and the
detection that stops a misconfigured convention from silently producing an empty
artifact path.
"""

from strata.models.configuration_model import ConfigurationLayerModel, PathConventionModel
from strata.models.deployment_model import LayersModel
from strata.utils.path_convention import resolve_layers

REL = "zones/europe/customers/acme/deploy.yaml"


def _conv(name="zone-tenant", pattern="zones/{zone}/customers/{customer}", resolves="layers", segments=None):
    if segments is None:
        segments = [ConfigurationLayerModel(name="zone"), ConfigurationLayerModel(name="customer")]
    return PathConventionModel(
        name=name,
        scope="zones/**",
        pattern=pattern,
        resolves=resolves,
        segments=segments if resolves == "layers" else None,
    )


DECLARED = LayersModel(segments={"zone": "europe", "customer": "acme"})


class TestLevel1ConventionSelection:
    def test_auto_detects_matching_convention(self):
        r = resolve_layers(REL, LayersModel(), [_conv()])
        assert r.convention is not None
        assert r.convention.name == "zone-tenant"
        assert r.error is None

    def test_explicit_follows_wins(self):
        r = resolve_layers(REL, LayersModel(follows="zone-tenant"), [_conv()])
        assert r.convention.name == "zone-tenant"
        assert r.error is None

    def test_unknown_follows_name_is_an_error(self):
        r = resolve_layers(REL, LayersModel(follows="nope"), [_conv()])
        assert r.convention is None
        assert r.error is not None and "nope" in r.error

    def test_ambiguous_match_is_an_error_naming_both(self):
        a = _conv(name="family-a")
        b = _conv(name="family-b")
        r = resolve_layers(REL, LayersModel(), [a, b])
        assert r.convention is None
        assert r.error is not None
        assert "family-a" in r.error and "family-b" in r.error


class TestLevel2SegmentValues:
    def test_derives_values_from_path(self):
        r = resolve_layers(REL, LayersModel(), [_conv()])
        assert r.values == {"zone": "europe", "customer": "acme"}

    def test_explicit_value_beats_derived(self):
        r = resolve_layers(REL, LayersModel(segments={"zone": "override"}), [_conv()])
        assert r.values["zone"] == "override"
        assert r.values["customer"] == "acme"  # still derived

    def test_default_used_when_neither_explicit_nor_derivable(self):
        conv = _conv(
            pattern="zones/{zone}",
            segments=[
                ConfigurationLayerModel(name="zone"),
                ConfigurationLayerModel(name="ring", default="dev"),
            ],
        )
        r = resolve_layers("zones/europe/deploy.yaml", LayersModel(), [conv])
        assert r.values == {"zone": "europe", "ring": "dev"}

    def test_unresolvable_segment_is_omitted_not_an_error(self):
        conv = _conv(
            pattern="zones/{zone}",
            segments=[ConfigurationLayerModel(name="zone"), ConfigurationLayerModel(name="ring")],
        )
        r = resolve_layers("zones/europe/deploy.yaml", LayersModel(), [conv])
        assert r.values == {"zone": "europe"}
        assert r.error is None  # "not applicable", not a failure


class TestSilentNoOpDetection:
    """A convention whose pattern stopped matching must not fail silently.

    Regression guard: the declared values still pass through, so nothing *looks*
    wrong — but with no convention there is no segment order, so the artifact path
    silently becomes empty and the build lands in the wrong place.
    """

    def test_declared_layers_claimed_by_no_convention_is_an_error(self):
        broken = _conv(pattern="zoneZZ/{zone}/customers/{customer}")  # typo
        r = resolve_layers(REL, DECLARED, [broken])
        assert r.convention is None
        assert r.error is not None
        assert "no resolves: layers convention claims it" in r.error
        assert "zone-tenant" in r.error  # names what was tried
        # values still pass through so callers can degrade gracefully
        assert r.values == {"zone": "europe", "customer": "acme"}

    def test_deployment_outside_every_family_scope_is_an_error(self):
        r = resolve_layers("other/x.yaml", DECLARED, [_conv()])
        assert r.error is not None

    def test_no_layers_declared_stays_silent(self):
        """Nothing claims to be in a hierarchy — nothing to contradict."""
        broken = _conv(pattern="zoneZZ/{zone}")
        assert resolve_layers(REL, LayersModel(), [broken]).error is None
        assert resolve_layers(REL, None, [broken]).error is None

    def test_no_layers_convention_declared_stays_silent(self):
        """Workspace simply isn't using layering — must not start erroring."""
        layout_only = _conv(resolves=None)
        r = resolve_layers(REL, DECLARED, [layout_only])
        assert r.error is None
        assert resolve_layers(REL, DECLARED, []).error is None

    def test_pass_through_preserves_values_for_templates(self):
        """Sync templates read `layers.environment` — blanking it would silently
        retarget GitOps resources to the fallback namespace."""
        r = resolve_layers(REL, LayersModel(segments={"environment": "prd"}), [])
        assert r.convention is None and r.error is None
        assert r.values == {"environment": "prd"}


class TestFilenameNeverCapturedAsSegmentValue:
    """Regression: a deployment file's own filename must never be captured as a
    layer segment's derived value.

    Bug: when a deployment's real directory depth was exactly
    (segment-count - 1) — i.e. every segment except the deepest one has a real
    directory — the deepest, genuinely-not-applicable segment resolved to the
    deployment file's own filename instead of being omitted. Root cause:
    match_pattern()'s "trailing path parts are ignored" contract only absorbs the
    filename as a trailing part when the path is *deeper* than the pattern; at
    exactly that depth, path length == pattern length, so the filename lines up
    with the last placeholder instead of being trailing.
    """

    def _hub_conv(self, name="hub-path"):
        return PathConventionModel(
            name=name,
            scope="deploy/hubs/**",
            pattern="deploy/hubs/{hub}/{spoke}/{customer}/{ring}/{environment}",
            resolves="layers",
            segments=[
                ConfigurationLayerModel(name="hub"),
                ConfigurationLayerModel(name="spoke"),
                ConfigurationLayerModel(name="customer"),
                ConfigurationLayerModel(name="ring"),
                ConfigurationLayerModel(name="environment"),
            ],
        )

    def test_shortfall_one_omits_deepest_segment_instead_of_filename(self):
        """4 real directories for a 5-segment convention (shortfall == 1) —
        'environment' must be omitted entirely, never resolve to 'deployment.yaml'."""
        rel_path = "deploy/hubs/z01/s01/c0062/dev/deployment.yaml"
        layers = LayersModel(
            follows="hub-path", segments={"hub": "z01", "spoke": "s01", "customer": "c0062", "ring": "dev"}
        )
        r = resolve_layers(rel_path, layers, [self._hub_conv()])
        assert r.error is None
        assert r.values == {"hub": "z01", "spoke": "s01", "customer": "c0062", "ring": "dev"}
        assert "environment" not in r.values

    def test_shortfall_zero_still_derives_deepest_segment_from_real_directory(self):
        """5 real directories (full depth) — environment IS derivable, from the
        real 'prd' directory, not from the trailing filename."""
        rel_path = "deploy/hubs/z01/s01/c0062/dev/prd/deployment.yaml"
        layers = LayersModel(
            follows="hub-path", segments={"hub": "z01", "spoke": "s01", "customer": "c0062", "ring": "dev"}
        )
        r = resolve_layers(rel_path, layers, [self._hub_conv()])
        assert r.error is None
        assert r.values["environment"] == "prd"

    def test_shortfall_two_and_three_still_omit_deeper_segments(self):
        """Shallower depths (shortfall 2 and 3) already worked before the fix —
        guard against a regression at those depths too."""
        conv = self._hub_conv()

        r2 = resolve_layers(
            "deploy/hubs/z01/s01/c0062/deployment.yaml",
            LayersModel(follows="hub-path", segments={"hub": "z01", "spoke": "s01", "customer": "c0062"}),
            [conv],
        )
        assert r2.error is None
        assert "ring" not in r2.values and "environment" not in r2.values

        r3 = resolve_layers(
            "deploy/hubs/z01/s01/deployment.yaml",
            LayersModel(follows="hub-path", segments={"hub": "z01", "spoke": "s01"}),
            [conv],
        )
        assert r3.error is None
        assert "customer" not in r3.values and "ring" not in r3.values and "environment" not in r3.values

    def test_auto_detect_at_full_depth_unaffected(self):
        """Level 1 auto-detect (no explicit follows) still works at full depth —
        stripping the filename only removes the false trailing-part match, it
        doesn't break the legitimate one."""
        rel_path = "deploy/hubs/z01/s01/c0062/dev/prd/deployment.yaml"
        r = resolve_layers(rel_path, LayersModel(), [self._hub_conv()])
        assert r.convention is not None and r.convention.name == "hub-path"
        assert r.values["environment"] == "prd"

    def test_auto_detect_at_shortfall_one_still_finds_convention_but_not_filename(self):
        """Level 1 (which convention applies) is unaffected by the filename fix —
        it still recognizes this file belongs to the hub-path family even at
        shortfall == 1 (matching existing behavior relied on elsewhere, e.g. a
        single-segment convention matching a deployment file sitting at the
        workspace root). Level 2 (segment *values*) still must never leak the
        filename — 'environment' stays omitted."""
        rel_path = "deploy/hubs/z01/s01/c0062/dev/deployment.yaml"
        layers = LayersModel(follows=None, segments={"hub": "z01", "spoke": "s01", "customer": "c0062", "ring": "dev"})
        r = resolve_layers(rel_path, layers, [self._hub_conv()])
        assert r.error is None
        assert r.convention is not None and r.convention.name == "hub-path"
        assert "environment" not in r.values
