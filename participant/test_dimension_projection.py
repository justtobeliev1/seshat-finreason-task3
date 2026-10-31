"""Synthetic tests for the opt-in dimension bridge experiment."""
from copy import deepcopy
from decimal import Decimal
import unittest
from unittest.mock import patch

from participant.dimension_projection import project_dimensions
from participant.solve import predict

STOCK = "g:StatementClassOfStockAxis"
COMPONENT = "g:StatementEquityComponentsAxis"
TARGET = "g:Dividends"


def edge(role, kind, parent, child, target_role=None):
    return {"role": role, "arcrole": kind, "parent": parent,
            "child": child, "target_role": target_role}


def fixture():
    contexts = {}
    facts = []
    for cid, dims, value in [
        ("a_extra", {STOCK: "g:A", COMPONENT: "g:Retained"}, "26"),
        ("b_extra", {STOCK: "g:B", COMPONENT: "g:Retained"}, "13.9"),
        ("b", {STOCK: "g:B"}, "13.9"),
        ("main", {}, "26"),
    ]:
        contexts[cid] = {"entity_scheme": "SEC", "entity": "E",
                         "start": "2025-01-01", "end": "2025-12-31", "dimensions": dims}
        facts.append({"concept": TARGET, "context": cid, "unit": "USD",
                      "value": Decimal(value), "raw": value, "decimals": "INF"})
    definitions = [edge("primary", "all", "g:LineItems", "g:Table", "cube"),
                   edge("primary", "domain-member", "g:LineItems", TARGET),
                   edge("cube", "hypercube-dimension", "g:Table", STOCK, "stock"),
                   edge("cube", "hypercube-dimension", "g:Table", COMPONENT, "component"),
                   edge("stock", "dimension-domain", STOCK, "g:StockDomain"),
                   edge("stock", "domain-member", "g:StockDomain", "g:A"),
                   edge("stock", "domain-member", "g:StockDomain", "g:B"),
                   edge("component", "dimension-domain", COMPONENT, "g:ComponentDomain"),
                   edge("component", "domain-member", "g:ComponentDomain", "g:Retained")]
    return {"target": {"concept": TARGET}, "contexts": contexts, "definition": definitions}, facts


class ProjectionTests(unittest.TestCase):
    def test_bridge_projects_missing_class_with_provenance_and_no_mutation(self):
        doc, facts = fixture()
        before_doc, before_facts = deepcopy(doc), deepcopy(facts)
        result, augmented, assumptions = project_dimensions(doc, facts)
        self.assertEqual(doc, before_doc)
        self.assertEqual(facts, before_facts)
        self.assertEqual(augmented[:len(facts)], facts)
        self.assertEqual(len(augmented), len(facts) + 1)
        projected = augmented[-1]
        self.assertEqual(projected["value"], Decimal(26))
        self.assertEqual(result["contexts"][projected["context"]]["dimensions"], {STOCK: "g:A"})
        self.assertEqual(assumptions[0]["source_context"], "a_extra")
        self.assertEqual(assumptions[0]["bridges"][0]["reduced_context"], "b")
        self.assertEqual(assumptions[0]["cube"], "g:Table")
        self.assertTrue(all(result["contexts"][f["context"]]["dimensions"] for f in augmented[len(facts):]))

    def test_missing_bridge_rejects_projection(self):
        doc, facts = fixture()
        facts = [fact for fact in facts if fact["context"] != "b"]
        self.assertEqual(project_dimensions(doc, facts)[2], [])

    def test_conflicting_bridge_or_collision_rejects_whole_plan(self):
        doc, facts = fixture()
        facts[2]["value"] = Decimal(999)
        self.assertEqual(project_dimensions(doc, facts)[2], [])
        doc, facts = fixture()
        doc["contexts"]["a"] = {**doc["contexts"]["b"], "dimensions": {STOCK: "g:A"}}
        facts.append({**facts[0], "context": "a", "value": Decimal(25)})
        self.assertEqual(project_dimensions(doc, facts)[2], [])

    def test_axes_in_different_cubes_same_role_are_not_joined(self):
        doc, facts = fixture()
        doc["definition"] = [e for e in doc["definition"]
                             if not (e["parent"] == "g:Table" and e["child"] == COMPONENT)]
        doc["definition"] += [edge("primary", "all", "g:LineItems", "g:OtherTable", "cube"),
                              edge("cube", "hypercube-dimension", "g:OtherTable", COMPONENT, "component")]
        self.assertEqual(project_dimensions(doc, facts)[2], [])

    def test_wrong_role_or_primary_tree_rejects_projection(self):
        doc, facts = fixture()
        for e in doc["definition"]:
            if e["child"] == COMPONENT:
                e["role"] = "unrelated"
        self.assertEqual(project_dimensions(doc, facts)[2], [])
        doc, facts = fixture()
        doc["target"]["concept"] = "g:Other"
        facts = [{**f, "concept": "g:Other"} for f in facts]
        self.assertEqual(project_dimensions(doc, facts)[2], [])

    def test_multiple_observed_extra_members_reject_projection(self):
        doc, facts = fixture()
        doc["contexts"]["different"] = {**doc["contexts"]["a_extra"],
            "dimensions": {STOCK: "g:A", COMPONENT: "g:Other"}}
        facts.append({**facts[0], "context": "different"})
        self.assertEqual(project_dimensions(doc, facts)[2], [])

    def test_bridge_different_unit_period_or_entity_is_not_reused(self):
        for field, value in (("unit", "EUR"), ("start", "2024-01-01"),
                             ("entity", "OTHER"), ("entity_scheme", "OTHER")):
            with self.subTest(field=field):
                doc, facts = fixture()
                if field == "unit":
                    facts[2][field] = value
                else:
                    doc["contexts"]["b"][field] = value
                self.assertEqual(project_dimensions(doc, facts)[2], [])

    def test_other_concepts_cannot_supply_bridge(self):
        doc, facts = fixture()
        facts[2]["concept"] = "g:Other"
        self.assertEqual(project_dimensions(doc, facts)[2], [])

    def test_solver_requires_opt_in_and_keeps_reported_fact_unchanged(self):
        doc, facts = fixture()
        doc["target"].update(start="2025-01-01", end="2025-12-31")
        doc.update(facts=facts, calculation=[])
        with patch("participant.solve.parse_query", return_value=doc):
            original, original_trace = predict("synthetic dimension bridge")
            projected, projected_trace = predict("synthetic dimension bridge", dimension_projection=True)
        self.assertEqual(original["extracted_value"], projected["extracted_value"])
        self.assertEqual(original["calculated_value"], "13.9")
        self.assertEqual(projected["calculated_value"], "39.9")
        self.assertFalse(original_trace["selected_dimension_projection"])
        self.assertTrue(projected_trace["selected_dimension_projection"])
        self.assertEqual(len(projected_trace["dimension_projection_assumptions"]), 1)


if __name__ == "__main__":
    unittest.main()
