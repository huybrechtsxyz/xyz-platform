"""Tests for the ${var:}/${secret:}/${feature:} expression primitives (ADR-0075).

Shared by TerraformDeployer (backend.configuration) and HelmDeployer (values.yaml).
"""

from strata.utils.resolved_values import (
    EXPR_PATTERN,
    ResolvedValues,
    collect_expr_refs,
    resolve_expr_string,
)


def _resolved(**kwargs) -> ResolvedValues:
    return ResolvedValues(**kwargs)


class TestExprPattern:
    def test_matches_all_three_kinds(self):
        assert EXPR_PATTERN.findall("${var:a} ${secret:b} ${feature:c}") == [
            ("var", "a"),
            ("secret", "b"),
            ("feature", "c"),
        ]

    def test_does_not_match_bare_braces(self):
        assert EXPR_PATTERN.findall("${KEY}") == []

    def test_does_not_match_unknown_kind(self):
        assert EXPR_PATTERN.findall("${env:KEY}") == []


class TestResolveExprString:
    def test_resolves_var(self):
        resolved = _resolved(variables={"cluster_name": "prd-aks"})
        result, errors = resolve_expr_string("${var:cluster_name}", resolved)
        assert result == "prd-aks"
        assert errors == []

    def test_resolves_secret(self):
        resolved = _resolved(secrets={"tf_token": "s3cr3t"})
        result, errors = resolve_expr_string("${secret:tf_token}", resolved)
        assert result == "s3cr3t"
        assert errors == []

    def test_resolves_feature_true(self):
        resolved = _resolved(features={"enable_encryption": True})
        result, errors = resolve_expr_string("${feature:enable_encryption}", resolved)
        assert result == "true"
        assert errors == []

    def test_resolves_feature_false(self):
        resolved = _resolved(features={"enable_encryption": False})
        result, errors = resolve_expr_string("${feature:enable_encryption}", resolved)
        assert result == "false"
        assert errors == []

    def test_partial_match_within_larger_string(self):
        resolved = _resolved(variables={"region": "westeurope"})
        result, errors = resolve_expr_string("prd-${var:region}-storage", resolved)
        assert result == "prd-westeurope-storage"
        assert errors == []

    def test_multiple_refs_in_one_string(self):
        resolved = _resolved(variables={"a": "1", "b": "2"})
        result, errors = resolve_expr_string("${var:a}-${var:b}", resolved)
        assert result == "1-2"
        assert errors == []

    def test_mixed_kinds_in_one_string(self):
        resolved = _resolved(variables={"a": "1"}, secrets={"b": "2"})
        result, errors = resolve_expr_string("${var:a}-${secret:b}", resolved)
        assert result == "1-2"
        assert errors == []

    def test_unresolved_var_is_an_error_not_a_pass_through(self):
        resolved = _resolved()
        result, errors = resolve_expr_string("${var:missing}", resolved)
        assert len(errors) == 1
        assert "missing" in errors[0]

    def test_unresolved_secret_is_an_error(self):
        resolved = _resolved()
        _result, errors = resolve_expr_string("${secret:missing}", resolved)
        assert len(errors) == 1
        assert "missing" in errors[0]

    def test_unresolved_feature_is_an_error(self):
        resolved = _resolved()
        _result, errors = resolve_expr_string("${feature:missing}", resolved)
        assert len(errors) == 1
        assert "missing" in errors[0]

    def test_feature_with_none_value_is_an_error(self):
        """A feature declared with no value resolves to None — treated the same as unset."""
        resolved = _resolved(features={"maybe": None})
        _result, errors = resolve_expr_string("${feature:maybe}", resolved)
        assert len(errors) == 1

    def test_no_expression_returns_value_unchanged(self):
        resolved = _resolved()
        result, errors = resolve_expr_string("plain-string", resolved)
        assert result == "plain-string"
        assert errors == []

    def test_one_bad_ref_does_not_prevent_others_from_resolving(self):
        resolved = _resolved(variables={"a": "1"})
        result, errors = resolve_expr_string("${var:a}-${var:missing}", resolved)
        assert "1" in result
        assert len(errors) == 1


class TestCollectExprRefs:
    def test_collects_from_flat_dict(self):
        node = {"a": "${var:x}", "b": "${secret:y}"}
        assert collect_expr_refs(node) == {("var", "x"), ("secret", "y")}

    def test_collects_from_nested_dict_and_list(self):
        node = {"a": {"b": ["${feature:z}", "plain"]}}
        assert collect_expr_refs(node) == {("feature", "z")}

    def test_collects_multiple_refs_from_one_string(self):
        node = {"a": "${var:x}-${secret:y}"}
        assert collect_expr_refs(node) == {("var", "x"), ("secret", "y")}

    def test_no_refs_returns_empty_set(self):
        assert collect_expr_refs({"a": "plain", "b": 1, "c": None}) == set()

    def test_none_returns_empty_set(self):
        assert collect_expr_refs(None) == set()
