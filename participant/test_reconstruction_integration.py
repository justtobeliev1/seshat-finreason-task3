from decimal import Decimal

from participant import solve


def document():
    def fact(concept, value):
        return {"concept": concept, "value": Decimal(value), "context": "main",
                "unit": "USD", "decimals": "0"}
    return {"target": {"concept": "us-gaap:Assets", "start": "2024-01-01", "end": "2024-12-31"},
            "contexts": {"main": {"entity": "issuer", "entity_scheme": "scheme",
                "start": "2024-01-01", "end": "2024-12-31", "dimensions": {}}},
            "facts": [fact("us-gaap:Assets", "-5"), fact("company:A", "10"), fact("company:B", "20")],
            "calculation": [{"role": "r", "parent": "us-gaap:Assets", "child": child,
                             "weight": Decimal(1)} for child in ("company:A", "company:B")],
            "definition": [], "role_definitions": {"r": "1000 - Statement - Balance Sheet"}}


def test_optional_reconstruction_preserves_default_and_records_changed_derivation(monkeypatch):
    monkeypatch.setattr(solve, "parse_query", lambda query: document())
    before, before_trace = solve.predict("synthetic query")
    after, after_trace = solve.predict("synthetic query", independent_reconstruction=True)
    assert before == {"extracted_value": "-5", "calculated_value": "5"}
    assert after == {"extracted_value": "-5", "calculated_value": "30"}
    assert "reconstruction_diagnostics" not in before_trace
    assert after_trace["method"] == "independent_reconstruction"
    assert after_trace["selected"] in after_trace["candidates"]
    assert after_trace["reconstruction_diagnostics"]["reason"] == "unique_independent_reconstruction"


def test_failed_reconstruction_preserves_original_choice(monkeypatch):
    doc = document()
    doc["facts"].pop()
    monkeypatch.setattr(solve, "parse_query", lambda query: doc)
    before, _ = solve.predict("synthetic query")
    after, trace = solve.predict("synthetic query", independent_reconstruction=True)
    assert after == before
    assert trace["method"] == "nonnegative"
    assert trace["reconstruction_diagnostics"]["reason"] == "no_clean_complete_statement_equation"
