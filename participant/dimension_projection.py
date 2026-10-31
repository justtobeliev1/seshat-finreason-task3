"""Experimental projection of a constant dimension with an explicit value bridge.

This produces assumptions, not proofs of dimensional completeness. It never
projects to an unqualified/default fact. Callers must opt in separately from
reported-fact selection and ordinary calculation evaluation.
"""
from collections import defaultdict
from copy import deepcopy
import hashlib
import json

from participant.dqc_rules import ROLLUP_AXES


def _children(edges, role, parent, kind):
    return [(edge.get("target_role") or role, edge["child"])
            for edge in edges if edge["role"] == role and edge["parent"] == parent
            and edge.get("arcrole", "").rsplit("/", 1)[-1] == kind]


def _members(edges, role, root):
    seen, pending = set(), [(role, root)]
    while pending:
        state = pending.pop()
        if state in seen:
            continue
        seen.add(state)
        pending.extend(_children(edges, *state, "domain-member"))
    return {member for _, member in seen}


def _cubes(doc):
    """Keep actual cube nodes separate even when their ELR is identical."""
    edges, target = doc.get("definition", []), doc["target"]["concept"]
    cubes = []
    for edge in edges:
        if edge.get("arcrole", "").rsplit("/", 1)[-1] != "all":
            continue
        role = edge["role"]
        if target not in _members(edges, role, edge["parent"]):
            continue
        cube_role, cube = edge.get("target_role") or role, edge["child"]
        axes = defaultdict(set)
        for axis_role, axis in _children(edges, cube_role, cube, "hypercube-dimension"):
            for domain_role, domain in _children(edges, axis_role, axis, "dimension-domain"):
                axes[axis].update(_members(edges, domain_role, domain))
        cubes.append({"role": role, "cube_role": cube_role, "cube": cube, "axes": axes})
    return cubes


def _scope(context, unit):
    return (context.get("entity_scheme"), context.get("entity"),
            context.get("start"), context.get("end"), context.get("instant"), unit)


def project_dimensions(doc, facts):
    """Return ``(augmented_doc, augmented_facts, assumptions)`` without mutation.

    For the queried concept only, remove one extra dimension from two-axis
    facts when it has one observed member in that entity/period/unit scope.
    Both axes and their members must belong to the same target-governing cube.
    An equal-value pair with and without the extra axis must explicitly bridge
    that projection, and any conflicting projected value rejects the plan.
    The retained axis must be an existing permitted rollup axis. Original facts
    remain intact; projected contexts and their provenance are additional.
    """
    result = dict(doc)
    result["contexts"] = deepcopy(doc.get("contexts", {}))
    augmented, assumptions = list(facts), []
    target = doc.get("target", {}).get("concept")
    groups = defaultdict(list)
    for fact in facts:
        context = doc.get("contexts", {}).get(fact.get("context"))
        if fact.get("concept") == target and fact.get("value") is not None and context is not None:
            groups[_scope(context, fact.get("unit"))].append(fact)
    cubes = _cubes(doc) if target else []
    emitted = set()
    for scope_facts in groups.values():
        observed = defaultdict(set)
        for fact in scope_facts:
            for axis, member in doc["contexts"][fact["context"]].get("dimensions", {}).items():
                observed[axis].add(member)
        for cube in cubes:
            for retained_axis, retained_members in cube["axes"].items():
                if retained_axis.split(":")[-1] not in ROLLUP_AXES:
                    continue
                for extra_axis, extra_members in cube["axes"].items():
                    if extra_axis == retained_axis or len(observed.get(extra_axis, ())) != 1:
                        continue
                    extra_member = next(iter(observed[extra_axis]))
                    if extra_member not in extra_members or extra_member.startswith("typed:"):
                        continue
                    sources, bases = defaultdict(list), defaultdict(list)
                    for fact in scope_facts:
                        dims = doc["contexts"][fact["context"]].get("dimensions", {})
                        member = dims.get(retained_axis)
                        if member not in retained_members:
                            continue
                        if set(dims) == {retained_axis, extra_axis} and dims[extra_axis] == extra_member:
                            sources[member].append(fact)
                        elif set(dims) == {retained_axis}:
                            bases[member].append(fact)
                    if not sources:
                        continue
                    bridges, conflict = [], False
                    for member, source_facts in sources.items():
                        source_values = {fact["value"] for fact in source_facts}
                        base_values = {fact["value"] for fact in bases[member]}
                        if len(source_values) != 1 or (base_values and source_values != base_values):
                            conflict = True
                            break
                        if base_values:
                            bridges.append({"source_context": source_facts[0]["context"],
                                            "reduced_context": bases[member][0]["context"],
                                            "retained_member": member,
                                            "value": str(source_facts[0]["value"])})
                    if conflict or not bridges:
                        continue
                    for member, source_facts in sources.items():
                        if bases[member]:
                            continue
                        for fact in source_facts:
                            signature = (fact["context"], fact.get("unit"), fact["value"],
                                         fact.get("decimals"), extra_axis)
                            if signature in emitted:
                                continue
                            source_context = doc["contexts"][fact["context"]]
                            context = deepcopy(source_context)
                            context["dimensions"] = {retained_axis: member}
                            assumption = {"kind": "assumed_dimension_projection",
                                          "source_context": fact["context"],
                                          "removed_axis": extra_axis, "removed_member": extra_member,
                                          "retained_axis": retained_axis, "retained_member": member,
                                          "governing_role": cube["role"], "cube_role": cube["cube_role"],
                                          "cube": cube["cube"], "bridges": deepcopy(bridges)}
                            digest = hashlib.sha256(json.dumps(assumption, sort_keys=True).encode()).hexdigest()[:20]
                            context_id = "dimension_projection_" + digest
                            suffix = 0
                            while context_id in result["contexts"]:
                                suffix += 1
                                context_id = "dimension_projection_" + digest + "_" + str(suffix)
                            assumption["projected_context"] = context_id
                            context["projection_provenance"] = deepcopy(assumption)
                            projected = {**fact, "context": context_id,
                                         "projection_provenance": deepcopy(assumption)}
                            result["contexts"][context_id] = context
                            augmented.append(projected)
                            assumptions.append(assumption)
                            emitted.add(signature)
    return result, augmented, assumptions
