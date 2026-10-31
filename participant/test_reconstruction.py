"""Query-only synthetic contracts for optional independent reconstruction."""
from copy import deepcopy
from decimal import Decimal

import pytest

from participant.reconstruction import reconstruct_negative


def context(**changes):
    result = {"entity_scheme": "scheme", "entity": "entity", "start": "2024-01-01",
              "end": "2024-12-31", "instant": None, "dimensions": {}}
    result.update(changes)
    return result


def fact(concept, value, ctx="main", unit="USD", decimals="0"):
    return {"concept": concept, "value": Decimal(value), "context": ctx,
            "unit": unit, "decimals": decimals}


def arc(parent, child, weight=1, role="r"):
    return {"parent": parent, "child": child, "weight": Decimal(weight), "role": role}


def fixture():
    target = fact("company:Target", "-5")
    facts = [target, fact("company:A", "10"), fact("company:B", "20")]
    doc = {"target": {"concept": target["concept"]}, "contexts": {"main": context()},
           "namespaces": {"company": "http://example.test/company"},
           "role_definitions": {"r": "1000 - Statement - Balance Sheet"},
           "calculation": [arc(target["concept"], "company:A"),
                           arc(target["concept"], "company:B")]}
    return doc, facts, target


def reject(doc, facts, reported):
    candidate, diagnostics = reconstruct_negative(doc, facts, reported)
    assert candidate is None
    assert diagnostics["reason"]
    return diagnostics


def test_complete_forward_preserves_evidence_and_assumptions():
    doc, facts, reported = fixture()
    candidate, diagnostics = reconstruct_negative(doc, facts, reported)
    assert candidate["value"] == Decimal(30)
    assert candidate["context"] == "main"
    assert candidate["unit"] == "USD"
    equation = diagnostics["equations"][0]
    assert equation["direction"] == "forward"
    assert equation["role_definition"] == doc["role_definitions"]["r"]
    assert {operand["concept"] for operand in equation["evidence"]} == {"company:A", "company:B"}
    assert all(operand["decimals"] == "0" for operand in equation["evidence"])
    assert "independent_operands_are_correct" in diagnostics["assumptions"]
    assert "reported_scalars_not_certified_exact_true_amounts" in diagnostics["assumptions"]


def test_complete_inverse_uses_actual_target_weight():
    doc, facts, reported = fixture()
    doc["calculation"] = [arc("company:A", "company:Target", 2),
                          arc("company:A", "company:B", -1)]
    candidate, diagnostics = reconstruct_negative(doc, facts, reported)
    assert candidate["value"] == Decimal(15)
    assert diagnostics["equations"][0]["target_weight"] == Decimal(2)
    assert diagnostics["equations"][0]["direction"] == "inverse"


def test_context_id_alias_with_identical_identity_is_accepted():
    doc, facts, reported = fixture()
    doc["contexts"]["alias"] = deepcopy(doc["contexts"]["main"])
    facts[1]["context"] = "alias"
    assert reconstruct_negative(doc, facts, reported)[0]["value"] == 30


@pytest.mark.parametrize("change", [{"entity_scheme": "other"}, {"entity": "other"},
    {"start": "2023-01-01"}, {"end": "2023-12-31"}, {"instant": "2024-12-31"},
    {"dimensions": {"company:Axis": "company:Member"}}])
def test_crossed_context_identity_is_blocked(change):
    doc, facts, reported = fixture()
    doc["contexts"]["other"] = context(**change)
    facts[1]["context"] = "other"
    reject(doc, facts, reported)


def test_crossed_unit_is_blocked():
    doc, facts, reported = fixture()
    facts[1]["unit"] = "shares"
    reject(doc, facts, reported)


def test_missing_operand_is_not_imputed():
    doc, facts, reported = fixture()
    reject(doc, facts[:-1], reported)


def test_conflicting_duplicate_cannot_be_resolved_by_precision():
    doc, facts, reported = fixture()
    facts.append(fact("company:A", "11", decimals="10"))
    reject(doc, facts, reported)


def test_equal_value_duplicate_preserves_both_decimal_declarations():
    doc, facts, reported = fixture()
    facts.append(fact("company:A", "10", decimals="2"))
    candidate, diagnostics = reconstruct_negative(doc, facts, reported)
    assert candidate["value"] == 30
    a = diagnostics["equations"][0]["evidence"][0]
    assert {item["decimals"] for item in a["facts"]} == {"0", "2"}


@pytest.mark.parametrize("definition", [None, "1000 - Disclosure - Statement Summary", "Statement URI Only"])
def test_only_declared_statement_category_is_accepted(definition):
    doc, facts, reported = fixture()
    doc["role_definitions"] = {} if definition is None else {"r": definition}
    reject(doc, facts, reported)


def test_explicit_role_definitions_argument_can_supply_category():
    doc, facts, reported = fixture()
    definitions = doc.pop("role_definitions")
    assert reconstruct_negative(doc, facts, reported, role_definitions=definitions)[0]["value"] == 30


@pytest.mark.parametrize("weight", [0, "NaN", "Infinity"])
def test_zero_or_nonfinite_relationship_weight_is_blocked(weight):
    doc, facts, reported = fixture()
    doc["calculation"][0]["weight"] = Decimal(weight)
    reject(doc, facts, reported)


def test_conflicting_equivalent_child_weight_is_blocked():
    doc, facts, reported = fixture()
    doc["namespaces"]["alias"] = doc["namespaces"]["company"]
    doc["calculation"].append(arc("company:Target", "alias:A", -1))
    reject(doc, facts, reported)


def test_duplicate_same_weight_is_not_double_counted():
    doc, facts, reported = fixture()
    doc["calculation"].append(deepcopy(doc["calculation"][0]))
    assert reconstruct_negative(doc, facts, reported)[0]["value"] == 30


def test_one_operand_is_insufficient():
    doc, facts, reported = fixture()
    doc["calculation"].pop()
    reject(doc, facts, reported)


def test_negative_sibling_public_nonnegative_violation_is_blocked():
    doc, facts, reported = fixture()
    doc["namespaces"]["alias"] = "http://fasb.org/us-gaap/2026"
    doc["calculation"] = [arc("company:A", "company:Target"), arc("company:A", "alias:Assets", -1)]
    facts[2] = fact("alias:Assets", "-20")
    reject(doc, facts, reported)


def test_dimension_qnames_and_namespace_are_passed_to_public_rule():
    doc, facts, reported = fixture()
    doc["namespaces"]["alias"] = "http://fasb.org/us-gaap/2026"
    dims = {"alias:StatementEquityComponentsAxis": "alias:RetainedEarningsMember"}
    doc["contexts"]["main"]["dimensions"] = dims
    doc["calculation"] = [arc("company:Target", "company:A"), arc("company:Target", "alias:Assets", -1)]
    facts[2] = fact("alias:Assets", "-20")
    assert reconstruct_negative(doc, facts, reported)[0]["value"] == 30


def test_disagreeing_independent_forward_and_inverse_equations_block_change():
    doc, facts, reported = fixture()
    facts.extend([fact("company:Parent", "80"), fact("company:Sibling", "10")])
    doc["calculation"].extend([arc("company:Parent", "company:Target"),
                               arc("company:Parent", "company:Sibling")])
    diagnostics = reject(doc, facts, reported)
    assert diagnostics["reason"] == "disagreeing_equations"


def test_agreeing_independent_equations_are_preserved():
    doc, facts, reported = fixture()
    facts.extend([fact("company:Parent", "40"), fact("company:Sibling", "10")])
    doc["calculation"].extend([arc("company:Parent", "company:Target"),
                               arc("company:Parent", "company:Sibling")])
    candidate, diagnostics = reconstruct_negative(doc, facts, reported)
    assert candidate["value"] == 30
    assert len(diagnostics["equations"]) == 2


def test_self_reference_and_target_cycle_are_blocked():
    doc, facts, reported = fixture()
    doc["calculation"].append(arc("company:A", "company:Target"))
    reject(doc, facts, reported)
    doc["calculation"] = [arc("company:Target", "company:Target"), arc("company:Target", "company:A")]
    reject(doc, facts, reported)


@pytest.mark.parametrize("values", [("-50", "20"), ("NaN", "20"), ("Infinity", "20")])
def test_nonpositive_or_nonfinite_reconstruction_is_blocked(values):
    doc, facts, reported = fixture()
    facts[1]["value"], facts[2]["value"] = map(Decimal, values)
    reject(doc, facts, reported)


def test_same_as_absolute_report_is_no_change():
    doc, facts, reported = fixture()
    reported["value"] = Decimal(-30)
    assert reject(doc, facts, reported)["reason"] == "same_as_absolute_reported"


def test_nonnegative_report_is_ineligible():
    doc, facts, reported = fixture()
    reported["value"] = Decimal(5)
    reject(doc, facts, reported)


@pytest.mark.parametrize("target_weight,duplicate", [(0, None), (1, -1)])
def test_inverse_target_weight_zero_or_conflict_is_blocked(target_weight, duplicate):
    doc, facts, reported = fixture()
    doc["calculation"] = [arc("company:A", "company:Target", target_weight),
                          arc("company:A", "company:B", -1)]
    if duplicate is not None:
        doc["calculation"].append(arc("company:A", "company:Target", duplicate))
    reject(doc, facts, reported)


def test_forward_actual_weights_and_zero_result_are_supported():
    doc, facts, reported = fixture()
    doc["calculation"][0]["weight"] = Decimal(2)
    doc["calculation"][1]["weight"] = Decimal(-1)
    assert reconstruct_negative(doc, facts, reported)[0]["value"] == 0


def test_qname_equivalent_fact_is_accepted_and_duplicate_conflict_rejected():
    doc, facts, reported = fixture()
    doc["namespaces"]["alias"] = doc["namespaces"]["company"]
    facts[1]["concept"] = "alias:A"
    assert reconstruct_negative(doc, facts, reported)[0]["value"] == 30
    facts.append(fact("company:A", "11"))
    reject(doc, facts, reported)


def test_distinct_statement_roles_must_agree():
    doc, facts, reported = fixture()
    doc["role_definitions"]["other"] = "2000 - Statement - Other"
    doc["calculation"].extend([arc("company:Target", "company:A", 2, role="other"),
                               arc("company:Target", "company:B", role="other")])
    assert reject(doc, facts, reported)["reason"] == "disagreeing_equations"


def test_scalar_arithmetic_does_not_round_to_default_decimal_precision():
    doc, facts, reported = fixture()
    facts[1]["value"] = Decimal("1234567890123456789012345678901234567890")
    assert reconstruct_negative(doc, facts, reported)[0]["value"] == Decimal(
        "1234567890123456789012345678901234567910")


def test_nonterminating_inverse_quotient_is_not_silently_rounded():
    doc, facts, reported = fixture()
    doc["calculation"] = [arc("company:A", "company:Target", 7),
                          arc("company:A", "company:B", -1)]
    diagnostics = reject(doc, facts, reported)
    assert diagnostics["rejected"][0]["reason"] == "nonterminating_scalar_result"


def test_conflicting_target_duplicate_in_alias_context_blocks_reconstruction():
    doc, facts, reported = fixture()
    doc["contexts"]["alias"] = deepcopy(doc["contexts"]["main"])
    facts.append(fact("company:Target", "-6", ctx="alias", decimals="10"))
    assert reject(doc, facts, reported)["reason"] == "conflicting_duplicate_target"


@pytest.mark.parametrize("unit", [None, ""])
def test_missing_unit_does_not_bind_unqualified_numeric_facts(unit):
    doc, facts, reported = fixture()
    for item in facts:
        item["unit"] = unit
    assert reject(doc, facts, reported)["reason"] == "missing_reported_unit"
