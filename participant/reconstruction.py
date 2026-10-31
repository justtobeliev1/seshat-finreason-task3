"""Optional complete-equation reconstruction; no prediction policy or defaults.

The arithmetic treats disclosed Decimal scalars as rationals. It does not certify
that rounded reported scalars are the issuer's exact underlying amounts.
"""
from collections import defaultdict
from decimal import Decimal, InvalidOperation, localcontext
from fractions import Fraction

from participant.dqc_rules import nonnegative_applicable
from participant.solve import context_identity, expanded_qname


ASSUMPTIONS = ["independent_operands_are_correct",
               "reported_scalars_not_certified_exact_true_amounts"]


def _decimal(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return result if result.is_finite() else None


def _exact_decimal(value):
    """Avoid silently rounding a nonterminating inverse-weight quotient."""
    denominator, twos, fives = value.denominator, 0, 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    if denominator != 1:
        return None
    with localcontext() as arithmetic:
        arithmetic.prec = max(28, len(str(abs(value.numerator))) + max(twos, fives) + 2)
        return Decimal(value.numerator) / Decimal(value.denominator)


def reconstruct_negative(doc, facts, reported, *, role_definitions=None):
    """Return a unique clean reconstruction plus auditable acceptance/rejections.

    Independent equations are direct complete sums in one declared Statement
    role. Inverse sums require a reported parent plus every other child. Facts
    are joined by the solver's complete semantic context identity and unit.
    """
    diagnostics = {"reason": None, "equations": [], "rejected": [],
                   "assumptions": list(ASSUMPTIONS)}

    def stop(reason):
        diagnostics["reason"] = reason
        return None, diagnostics

    namespaces = doc.get("namespaces", {})
    canonical = lambda name: expanded_qname(name, namespaces)
    target = canonical(doc["target"]["concept"])
    reported_value = _decimal(reported.get("value"))
    if (reported_value is None or reported_value >= 0
            or canonical(reported["concept"]) != target):
        return stop("ineligible_reported_fact")
    contexts = doc.get("contexts", {})
    if reported.get("context") not in contexts:
        return stop("missing_reported_context")
    scope = context_identity(contexts[reported["context"]])
    unit = reported.get("unit")
    if not unit:
        return stop("missing_reported_unit")
    by_concept = defaultdict(list)
    for item in facts:
        context = contexts.get(item.get("context"))
        if (context is not None and context_identity(context) == scope
                and item.get("unit") == unit):
            by_concept[canonical(item["concept"])].append(item)
    target_values = [_decimal(item.get("value")) for item in by_concept.get(target, [])]
    if any(value is None or value != reported_value for value in target_values):
        return stop("conflicting_duplicate_target")

    # Preserve all equivalent arcs, including conflicting declarations. Replacing
    # them with a dict's last weight would erase the reason to reject an equation.
    networks = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for edge in doc.get("calculation", []):
        networks[edge["role"]][canonical(edge["parent"])][canonical(edge["child"])].append(edge)
    definitions = doc.get("role_definitions", {}) if role_definitions is None else role_definitions

    def operand(concept, coefficient):
        equivalent = by_concept.get(concept, [])
        if not equivalent:
            return None, "missing_operand"
        values = [_decimal(item.get("value")) for item in equivalent]
        if any(value is None for value in values):
            return None, "nonfinite_operand"
        if len(set(values)) != 1:
            return None, "conflicting_duplicate_operand"
        evidence = []
        for item, value in zip(equivalent, values):
            context = contexts[item["context"]]
            dimensions = {expanded_qname(axis, namespaces): expanded_qname(member, namespaces)
                          for axis, member in context.get("dimensions", {}).items()}
            prefix = item["concept"].partition(":")[0]
            if value < 0 and nonnegative_applicable(
                    item["concept"], dimensions, namespace=namespaces.get(prefix)):
                return None, "negative_operand_violates_nonnegative_rule"
            evidence.append({"concept": item["concept"], "context": item["context"],
                             "context_identity": context_identity(context),
                             "dimensions_expanded": dimensions, "unit": item.get("unit"),
                             "value": value, "decimals": item.get("decimals")})
        return {"concept": equivalent[0]["concept"], "expanded_concept": concept,
                "coefficient": coefficient, "value": values[0],
                "context": equivalent[0]["context"], "unit": unit,
                "decimals": equivalent[0].get("decimals"), "facts": evidence}, None

    for role, parents in sorted(networks.items()):
        definition = definitions.get(role, "")
        categories = {part.strip().lower() for part in definition.split(" - ")}
        for parent, children in sorted(parents.items()):
            direction = "forward" if parent == target else "inverse" if target in children else None
            if direction is None:
                continue
            equation = {"direction": direction, "role": role, "role_definition": definition,
                        "parent": parent, "context": reported["context"],
                        "context_identity": scope, "unit": unit, "relationships": [], "evidence": []}

            def reject(reason):
                diagnostics["rejected"].append({**equation, "reason": reason})

            if "statement" not in categories or "disclosure" in categories:
                reject("role_not_declared_statement")
                continue
            weights = {}
            invalid = False
            for child, declarations in sorted(children.items()):
                declared = [_decimal(edge.get("weight")) for edge in declarations]
                equation["relationships"].extend(dict(edge) for edge in declarations)
                if any(weight is None or weight == 0 for weight in declared):
                    reject("invalid_or_zero_weight")
                    invalid = True
                    break
                if len(set(declared)) != 1:
                    reject("conflicting_equivalent_weights")
                    invalid = True
                    break
                weights[child] = declared[0]
            if invalid:
                continue

            def reaches(start, goal):
                seen, todo = set(), [start]
                while todo:
                    current = todo.pop()
                    if current == goal:
                        return True
                    if current in seen:
                        continue
                    seen.add(current)
                    todo.extend(parents.get(current, {}))
                return False

            if direction == "forward":
                coefficients = dict(weights)
                circular = any(reaches(child, target) for child in children)
            else:
                # Parent is necessarily linked to target by this equation;
                # target-to-parent or a target-dependent sibling is circular.
                coefficients = {parent: Decimal(1), **{child: weight.copy_negate() for child, weight in weights.items()
                                                       if child != target}}
                equation["target_weight"] = weights[target]
                circular = (parent == target or reaches(target, parent)
                            or any(reaches(child, target) for child in children if child != target))
            if target in coefficients or circular:
                reject("target_reuse_or_circular_equation")
                continue
            if len(coefficients) < 2:
                reject("fewer_than_two_independent_operand_concepts")
                continue
            value = Fraction(0)
            for concept, coefficient in coefficients.items():
                evidence, reason = operand(concept, coefficient)
                if reason:
                    reject(reason)
                    invalid = True
                    break
                equation["evidence"].append(evidence)
                value += Fraction(evidence["value"]) * Fraction(coefficient)
            if invalid:
                continue
            if direction == "inverse":
                value /= Fraction(weights[target])
            result = _exact_decimal(value)
            if result is None:
                reject("nonterminating_scalar_result")
                continue
            equation["value"] = result
            diagnostics["equations"].append(equation)

    equations = diagnostics["equations"]
    if not equations:
        return stop("no_clean_complete_statement_equation")
    values = {equation["value"] for equation in equations}
    if len(values) != 1:
        return stop("disagreeing_equations")
    value = next(iter(values))
    if not value.is_finite() or value < 0:
        return stop("nonfinite_or_negative_result")
    if value == reported_value.copy_abs():
        return stop("same_as_absolute_reported")
    diagnostics["reason"] = "unique_independent_reconstruction"
    return {"kind": "independent_reconstruction", "value": value,
            "context": reported["context"], "unit": unit,
            "roles": sorted({equation["role"] for equation in equations}),
            "evidence": equations, "assumptions": list(ASSUMPTIONS)}, diagnostics
