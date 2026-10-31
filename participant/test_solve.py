from decimal import Decimal as D

from participant.solve import calc_candidates, dimension_candidates, period_matches
import participant.solve as solver


def ctx(dimensions=None, start="2024-01-01", end="2024-12-31"):
    return {"entity": "company", "start": start, "end": end,
            "dimensions": dimensions or {}}


def fact(concept, value, context="main", unit="USD"):
    return {"concept": concept, "value": D(value), "context": context,
            "unit": unit, "decimals": "0"}


def test_calculation_roles_do_not_double_count_repeated_links():
    arcs = [{"role": role, "parent": "g:Total", "child": "g:Part", "weight": D(1)}
            for role in ("statement", "note", "statement")]
    facts = [fact("g:Total", "11"), fact("g:Part", "10")]
    doc = {"target": {"concept": "g:Total"}, "contexts": {"main": ctx()},
           "calculation": arcs}
    candidates = calc_candidates(doc, facts, facts[0])
    assert len(candidates) == 2
    assert {c["value"] for c in candidates} == {D(10)}


def test_calculation_keeps_contexts_and_currencies_separate():
    facts = [fact("g:Total", "10"), fact("g:Part", "10"),
             fact("g:Part", "99", "segment"), fact("g:Part", "7", unit="EUR")]
    doc = {"target": {"concept": "g:Total"},
           "contexts": {"main": ctx(), "segment": ctx({"g:RegionAxis": "g:East"})},
           "calculation": [{"role": "statement", "parent": "g:Total",
                             "child": "g:Part", "weight": D(1)}]}
    candidates = calc_candidates(doc, facts, facts[0])
    selected = max(candidates, key=lambda c: c["rank"])
    assert selected["value"] == D(10)
    assert selected["unit"] == "USD"


def test_dimensional_subtotals_exclude_reported_descendants():
    axis = "g:StatementBusinessSegmentsAxis"
    contexts = {"a": ctx({axis: "g:A"}), "a1": ctx({axis: "g:A1"}),
                "b": ctx({axis: "g:B"})}
    facts = [fact("g:Revenue", "100", "a"), fact("g:Revenue", "40", "a1"),
             fact("g:Revenue", "20", "b")]
    doc = {"target": {"concept": "g:Revenue"}, "contexts": contexts,
           "definition": [{"role": "statement", "arcrole": "domain-member",
                           "parent": "g:A", "child": "g:A1"}]}
    candidates = dimension_candidates(doc, facts, None)
    assert len(candidates) == 1
    assert candidates[0]["value"] == D(120)
    assert candidates[0]["excluded_descendants"] == ["g:A1"]


def test_dimensional_views_do_not_mix_different_axes():
    axes = ["g:ProductOrServiceAxis", "g:StatementBusinessSegmentsAxis"]
    contexts, facts = {}, []
    for i, axis in enumerate(axes):
        for j, value in enumerate(("40", "60")):
            cid = f"{i}-{j}"
            contexts[cid] = ctx({axis: f"g:M{j}"})
            facts.append(fact("g:Revenue", value, cid))
    doc = {"target": {"concept": "g:Revenue"}, "contexts": contexts, "definition": []}
    candidates = dimension_candidates(doc, facts, None)
    assert len(candidates) == 2
    assert {c["value"] for c in candidates} == {D(100)}


def test_period_identity_distinguishes_instant_and_duration():
    assert period_matches({"instant": "2024-12-31"},
                          {"instant": "2024-12-31", "end": "2024-12-31"})
    assert not period_matches({"instant": "2024-12-31"},
                              {"start": "2024-01-01", "end": "2024-12-31"})
    assert not period_matches(ctx(), {"start": "2024-07-01", "end": "2024-12-31"})


def test_negative_dimensional_fact_is_not_discarded_for_positive_default(monkeypatch):
    concept = "us-gaap:Assets"
    doc = {"target": {"concept": concept, "start": "2024-01-01", "end": "2024-12-31"},
           "contexts": {"main": ctx(), "sub": ctx({"g:SubsidiaryAxis": "g:Subsidiary"})},
           "facts": [fact(concept, "100"), fact(concept, "-5", "sub")],
           "calculation": [], "definition": [], "section_errors": []}
    monkeypatch.setattr(solver, "parse_query", lambda q: doc)
    prediction, trace = solver.predict("generic synthetic filing")
    assert prediction == {"extracted_value": "-5", "calculated_value": "5"}
    assert trace["selected"]["context"] == "sub"


def test_two_eligible_negative_facts_preserve_extraction_ambiguity(monkeypatch):
    concept = "us-gaap:Assets"
    doc = {"target": {"concept": concept, "start": "2024-01-01", "end": "2024-12-31"},
           "contexts": {"one": ctx({"g:SubsidiaryAxis": "g:One"}),
                        "two": ctx({"g:SubsidiaryAxis": "g:Two"})},
           "facts": [fact(concept, "-5", "one"), fact(concept, "-8", "two")],
           "calculation": [], "definition": [], "section_errors": []}
    monkeypatch.setattr(solver, "parse_query", lambda q: doc)
    prediction, trace = solver.predict("generic synthetic filing")
    assert prediction["extracted_value"] == "-5"
    assert trace["ambiguous_extraction"]
    assert len(trace["negative_fact_candidates"]) == 2


def definition(role, kind, parent, child, target_role=None):
    return {"role": role, "arcrole": kind, "parent": parent,
            "child": child, "target_role": target_role}


def test_dimension_members_follow_domain_role_and_target_role():
    axis = "g:ProductOrServiceAxis"
    contexts = {m: ctx({axis: "g:" + m}) for m in ("A", "Child", "B", "Other")}
    facts = [fact("g:Revenue", value, m) for m, value in
             (("A", "60"), ("Child", "20"), ("B", "40"), ("Other", "900"))]
    edges = [definition("statement", "dimension-domain", axis, "g:Domain", "members"),
             definition("members", "domain-member", "g:Domain", "g:A"),
             definition("members", "domain-member", "g:A", "g:Child"),
             definition("members", "domain-member", "g:Domain", "g:B"),
             definition("unrelated", "domain-member", "g:Domain", "g:Other")]
    doc = {"target": {"concept": "g:Revenue"}, "contexts": contexts, "definition": edges}
    candidate, = dimension_candidates(doc, facts, None)
    assert candidate["value"] == D(100)
    assert candidate["excluded_descendants"] == ["g:Child"]
    assert candidate["excluded_outside_domain"] == ["g:Other"]


def test_cube_bound_dimension_is_not_overridden_by_statement_calculation(monkeypatch):
    concept, axis = "g:Equity", "g:StatementClassOfStockAxis"
    contexts = {"main": ctx(), "a": ctx({axis: "g:A"}), "b": ctx({axis: "g:B"})}
    facts = [fact(concept, "100"), fact(concept, "60", "a"), fact(concept, "50", "b"),
             fact("g:Part1", "70"), fact("g:Part2", "50")]
    edges = [definition("statement", "all", "g:LineItems", "g:Table"),
             definition("statement", "domain-member", "g:LineItems", concept),
             definition("statement", "hypercube-dimension", "g:Table", axis),
             definition("statement", "dimension-domain", axis, "g:Domain"),
             definition("statement", "domain-member", "g:Domain", "g:A"),
             definition("statement", "domain-member", "g:Domain", "g:B")]
    doc = {"target": {"concept": concept, "start": "2024-01-01", "end": "2024-12-31"},
           "contexts": contexts, "facts": facts, "definition": edges,
           "calculation": [{"role": "statement", "parent": concept, "child": c, "weight": D(1)}
                           for c in ("g:Part1", "g:Part2")]}
    monkeypatch.setattr(solver, "parse_query", lambda q: doc)
    prediction, trace = solver.predict("synthetic cube statement")
    assert prediction == {"extracted_value": "100", "calculated_value": "110"}
    assert trace["selected"]["governing_cube_roles"] == ["statement"]


def test_notall_cube_does_not_authorize_dimension_priority():
    doc = {"target": {"concept": "g:Total"}, "definition": [
        definition("statement", "notAll", "g:Total", "g:Table"),
        definition("statement", "hypercube-dimension", "g:Table", "g:ProductOrServiceAxis")]}
    assert solver.governing_cube_roles(doc, "g:ProductOrServiceAxis") == []


def test_same_axis_in_unrelated_note_does_not_inherit_statement_cube():
    concept, axis = "g:Revenue", "g:ProductOrServiceAxis"
    edges = [definition("statement", "all", concept, "g:Table", "cube"),
             definition("cube", "hypercube-dimension", "g:Table", axis, "members"),
             definition("members", "dimension-domain", axis, "g:StatementDomain"),
             definition("members", "domain-member", "g:StatementDomain", "g:A"),
             definition("note", "dimension-domain", axis, "g:NoteDomain"),
             definition("note", "domain-member", "g:NoteDomain", "g:B")]
    doc = {"target": {"concept": concept}, "definition": edges,
           "contexts": {"a": ctx({axis: "g:A"}), "b": ctx({axis: "g:B"})}}
    candidates = dimension_candidates(doc, [fact(concept, "10", "a"), fact(concept, "900", "b")], None)
    by_role = {c["definition_role"]: c for c in candidates}
    assert by_role["members"]["governing_cube_roles"] == ["statement"]
    assert by_role["note"]["governing_cube_roles"] == []


def test_nonnegative_constraint_uses_fact_namespace_not_lexical_prefix(monkeypatch):
    concept = "alias:Assets"
    doc = {"target": {"concept": concept, "start": "2024-01-01", "end": "2024-12-31"},
           "namespaces": {"alias": "http://fasb.org/us-gaap/2021-01-31"},
           "contexts": {"main": ctx()}, "facts": [fact(concept, "-5")],
           "calculation": [], "definition": []}
    monkeypatch.setattr(solver, "parse_query", lambda q: doc)
    prediction, _ = solver.predict("synthetic alias namespace")
    assert prediction == {"extracted_value": "-5", "calculated_value": "5"}

    doc["contexts"]["main"]["dimensions"] = {"alias:StatementEquityComponentsAxis": "alias:RetainedEarningsMember"}
    _, trace = solver.predict("synthetic alias namespace with exempt dimension")
    assert trace["method"] != "nonnegative"


def test_agreeing_cube_protection_is_explicit_and_disabled_by_default(monkeypatch):
    concept, axis = "g:Revenue", "g:StatementBusinessSegmentsAxis"
    contexts = {"main": ctx(), "a": ctx({axis: "g:A"}), "b": ctx({axis: "g:B"})}
    doc = {"target": {"concept": concept, "start": "2024-01-01", "end": "2024-12-31"},
           "contexts": contexts,
           "facts": [fact(concept, "100"), fact(concept, "60", "a"), fact(concept, "40", "b"),
                     fact("g:Part1", "-20"), fact("g:Part2", "10")],
           "definition": [definition("statement", "all", concept, "g:Table"),
                          definition("statement", "hypercube-dimension", "g:Table", axis),
                          definition("statement", "dimension-domain", axis, "g:Domain"),
                          definition("statement", "domain-member", "g:Domain", "g:A"),
                          definition("statement", "domain-member", "g:Domain", "g:B")],
           "calculation": [{"role": "statement", "parent": concept, "child": child, "weight": D(1)}
                           for child in ("g:Part1", "g:Part2", "g:Missing")]}
    monkeypatch.setattr(solver, "parse_query", lambda q: doc)
    baseline, baseline_trace = solver.predict("synthetic agreeing cube")
    assert baseline == {"extracted_value": "100", "calculated_value": "-10"}
    assert baseline_trace["method"] == "calculation"
    prediction, trace = solver.predict("synthetic agreeing cube", cube_agreement_guard=True)
    assert prediction == {"extracted_value": "100", "calculated_value": "100"}
    assert trace["method"] == "dimension"


def test_agreement_on_one_axis_keeps_independent_dimension_discrepancy(monkeypatch):
    concept, axis = "g:Revenue", "g:ProductOrServiceAxis"
    other_axis = "g:RelatedPartyTransactionsByRelatedPartyAxis"
    doc = {"target": {"concept": concept, "start": "2024-01-01", "end": "2024-12-31"},
           "contexts": {"main": ctx(), "a": ctx({axis: "g:A"}), "b": ctx({axis: "g:B"}),
                        "related": ctx({other_axis: "g:Related"})},
           "facts": [fact(concept, "100"), fact(concept, "60", "a"), fact(concept, "40", "b"),
                     fact(concept, "15", "related")],
           "definition": [definition("statement", "all", concept, "g:Table"),
                          definition("statement", "hypercube-dimension", "g:Table", axis),
                          definition("statement", "dimension-domain", axis, "g:Domain"),
                          definition("statement", "domain-member", "g:Domain", "g:A"),
                          definition("statement", "domain-member", "g:Domain", "g:B")],
           "calculation": []}
    monkeypatch.setattr(solver, "parse_query", lambda q: doc)
    prediction, trace = solver.predict("synthetic independent dimensional checks", cube_agreement_guard=True)
    assert prediction == {"extracted_value": "100", "calculated_value": "15"}
    assert trace["selected"]["axis"] == other_axis


def test_agreeing_cube_vetoes_override_with_independent_note_and_incomplete_calc(monkeypatch):
    concept, axis = "g:Revenue", "g:ProductOrServiceAxis"
    other_axis = "g:RelatedPartyTransactionsByRelatedPartyAxis"
    doc = {"target": {"concept": concept, "start": "2024-01-01", "end": "2024-12-31"},
           "contexts": {"main": ctx(), "a": ctx({axis: "g:A"}), "b": ctx({axis: "g:B"}),
                        "related": ctx({other_axis: "g:Related"})},
           "facts": [fact(concept, "100"), fact(concept, "60", "a"), fact(concept, "40", "b"),
                     fact(concept, "15", "related"), fact("g:Part1", "20"), fact("g:Part2", "10")],
           "definition": [definition("statement", "all", concept, "g:Table"),
                          definition("statement", "hypercube-dimension", "g:Table", axis),
                          definition("statement", "dimension-domain", axis, "g:Domain"),
                          definition("statement", "domain-member", "g:Domain", "g:A"),
                          definition("statement", "domain-member", "g:Domain", "g:B")],
           "calculation": [{"role": "statement", "parent": concept, "child": c, "weight": D(1)}
                           for c in ("g:Part1", "g:Part2", "g:Missing")]}
    monkeypatch.setattr(solver, "parse_query", lambda _: doc)
    prediction, trace = solver.predict("synthetic independent note and incomplete arithmetic", cube_agreement_guard=True)
    assert prediction == {"extracted_value": "100", "calculated_value": "30"}
    assert trace["method"] == "calculation"
    assert any(c.get("axis") == other_axis and c["value"] == D(15) for c in trace["candidates"])


def test_note_local_domain_fallback_is_explicit_and_does_not_create_reported_total(monkeypatch):
    concept, axis = "g:Expense", "g:ProductOrServiceAxis"
    doc = {"target": {"concept": concept, "start": "2024-01-01", "end": "2024-12-31"},
           "contexts": {"a": ctx({axis: "g:Product"}), "b": ctx({axis: "g:Service"})},
           "facts": [fact(concept, "40", "a"), fact(concept, "60", "b")],
           "role_definitions": {"note": "123 - Disclosure - Reclassification (Details)"},
           "definition": [definition("note", "all", concept, "g:NoteTable"),
                          definition("note", "hypercube-dimension", "g:NoteTable", axis),
                          definition("note", "dimension-domain", axis, "g:Domain"),
                          definition("note", "domain-member", "g:Domain", "g:Service")],
           "calculation": []}
    monkeypatch.setattr(solver, "parse_query", lambda q: doc)
    core, _ = solver.predict("synthetic note domain")
    experimental, trace = solver.predict("synthetic note domain", note_domain_fallback=True)
    assert core == {"extracted_value": "0", "calculated_value": "60"}
    assert experimental == {"extracted_value": "0", "calculated_value": "100"}
    assert trace["selected_note_domain_fallback"]
    for category in ("123 - Statement - Operations", "Unclassified description"):
        doc["role_definitions"]["note"] = category
        unchanged, _ = solver.predict("synthetic statement domain", note_domain_fallback=True)
        assert unchanged == core


def test_role_category_uses_declared_field_instead_of_uri_words():
    doc = {"role_definitions": {"statement-looking-url": "99 - Disclosure - Statement details"}}
    assert solver.role_category(doc, "statement-looking-url") == "disclosure"
    assert solver.role_category(doc, "statement-looking-unknown-url") == "unknown"
