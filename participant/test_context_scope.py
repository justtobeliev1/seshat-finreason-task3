"""Synthetic context-boundary controls; no competition cases or labels."""
from decimal import Decimal

import pytest

from participant.solve import calc_candidates, dimension_candidates


AXIS = "g:ProductOrServiceAxis"


def context(**changes):
    return {"entity_scheme": "scheme:A", "entity": "issuer",
            "start": "2024-01-01", "end": "2024-12-31", "instant": None,
            "dimensions": {}, **changes}


def fact(concept, value, cid, unit="USD"):
    return {"concept": concept, "value": Decimal(value), "context": cid,
            "unit": unit, "decimals": "0"}


BOUNDARIES = [
    {"entity_scheme": "scheme:B"},
    {"entity": "other-issuer"},
    {"start": "2024-07-01"},
    {"end": "2023-12-31"},
    {"start": None, "end": None, "instant": "2024-12-31"},
]


@pytest.mark.parametrize("other_scope", BOUNDARIES)
def test_dimension_members_do_not_merge_across_context_scope(other_scope):
    facts = [fact("g:Total", "60", "a"), fact("g:Total", "40", "b")]
    doc = {"target": {"concept": "g:Total"}, "definition": [], "contexts": {
        "a": context(dimensions={AXIS: "g:A"}),
        "b": context(**other_scope, dimensions={AXIS: "g:B"}),
    }}
    candidates = dimension_candidates(doc, facts, None)
    assert sorted(c["value"] for c in candidates) == [Decimal(40), Decimal(60)]
    reported = fact("g:Total", "60", "reported")
    doc["contexts"]["reported"] = context()
    matched = dimension_candidates(doc, facts, reported)
    assert len(matched) == 1
    assert matched[0]["value"] == Decimal(60)


@pytest.mark.parametrize("other_scope", BOUNDARIES + [{}])
@pytest.mark.parametrize("has_reported", [False, True])
def test_missing_operand_uses_its_calculation_context(other_scope, has_reported):
    facts = [fact("g:Part1", "10", "main"),
             fact("g:Part2", "40", "a"), fact("g:Part2", "50", "b")]
    doc = {"target": {"concept": "g:Total"}, "definition": [], "contexts": {
        "main": context(),
        "a": context(**other_scope, dimensions={AXIS: "g:A"}),
        "b": context(**other_scope, dimensions={AXIS: "g:B"}),
    }, "calculation": [
        {"role": "statement", "parent": "g:Total", "child": child,
         "weight": Decimal(1)} for child in ("g:Part1", "g:Part2")
    ]}
    reported = fact("g:Total", "100", "main") if has_reported else None
    candidates = calc_candidates(doc, facts, reported)
    assert len(candidates) == 1
    selected = candidates[0]
    assert selected["value"] == Decimal(10 if other_scope else 100)
    assert len(selected["imputed_operands"]) == (0 if other_scope else 1)
    # A valid decomposition is still conditional; it is not a disclosed operand.
    assert selected["missing"] == ["g:Part2"]


def test_same_scope_context_aliases_still_roll_up():
    facts = [fact("g:Total", "60", "a"), fact("g:Total", "40", "b")]
    doc = {"target": {"concept": "g:Total"}, "definition": [], "contexts": {
        "a": context(dimensions={AXIS: "g:A"}),
        "b": context(dimensions={AXIS: "g:B"}),
        "reported-alias": context(),
    }}
    reported = fact("g:Total", "100", "reported-alias")
    candidates = dimension_candidates(doc, facts, reported)
    assert len(candidates) == 1
    assert candidates[0]["value"] == Decimal(100)
