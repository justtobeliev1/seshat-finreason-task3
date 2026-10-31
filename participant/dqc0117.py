"""Query-only approximation of DQC_US_0117 rules 9574 and 10093.

Reference: dqc_us_rules commit 64abefd, source/us/2023/DQC_0117.xule.
Rule 9574 compares a default (consolidated) fact with the sum of single-dimension
members on a roll-up axis of a statement cube. Rule 10093 compares a
single-dimension fact (axis = SubMember) with the sum of facts that add a second
dimension. Both are evaluated per concept, period and unit of the question.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
import re

from participant.dqc_rules import ROLLUP_AXES

ZERO = Decimal(0)


def _kind(arcrole):
    return (arcrole or "").rsplit("/", 1)[-1]


def _children(edges, role, parent, kind):
    return [(e.get("target_role") or role, e["child"]) for e in edges
            if e["role"] == role and e["parent"] == parent and _kind(e.get("arcrole")) == kind]


def _descendants(edges, role, root):
    """Domain-member descendants (role-aware); returns {member: set(parent members)}."""
    parents, seen, todo = defaultdict(set), set(), [(role, root)]
    while todo:
        state = todo.pop()
        if state in seen:
            continue
        seen.add(state)
        for child_state in _children(edges, *state, "domain-member"):
            parents[child_state[1]].add(state[1])
            todo.append(child_state)
    return parents


def _category(doc, role):
    text = doc.get("role_definitions", {}).get(role, "")
    fields = [f.strip().lower() for f in text.split(" - ")]
    if "parenthetical" in text.lower():
        return "parenthetical"
    if "statement" in fields:
        return "statement"
    if "disclosure" in fields or "notes" in text.lower():
        return "notes"
    return "unknown"


def cubes(doc):
    """List DRS cubes: role, primary items, and per-axis domains/member parents."""
    edges = doc["definition"]
    out = []
    for e in edges:
        if _kind(e.get("arcrole")) != "all":
            continue
        role = e["role"]
        primary = {e["parent"]} | set(_descendants(edges, role, e["parent"]))
        cube_role = e.get("target_role") or role
        dims = {}
        for dim_role, axis in _children(edges, cube_role, e["child"], "hypercube-dimension"):
            domains = _children(edges, dim_role, axis, "dimension-domain")
            members = {}
            for dom_role, dom in domains:
                for m, ps in _descendants(edges, dom_role, dom).items():
                    members.setdefault(m, set()).update(ps)
            dims[axis] = {"domain": {d for _, d in domains}, "parents": members}
        out.append({"role": role, "primary": primary, "dims": dims,
                    "category": _category(doc, role)})
    return out


def _in_cube(cube, concept, dims):
    if concept not in cube["primary"]:
        return False
    for axis, member in dims.items():
        spec = cube["dims"].get(axis)
        if spec is None:
            return False
        if member not in spec["parents"] and member not in spec["domain"]:
            return False
    return True


def document_period_days(query, doc):
    m = re.search(r"<dei:DocumentType\b[^>]*contextRef=\"([^\"]+)\"", query)
    ctx = doc["contexts"].get(m.group(1)) if m else None
    if not ctx or not ctx.get("start") or not ctx.get("end"):
        return None
    return (date.fromisoformat(ctx["end"][:10]) - date.fromisoformat(ctx["start"][:10])).days


def _days(ctx):
    if ctx.get("instant"):
        return 0
    try:
        return (date.fromisoformat(ctx["end"][:10]) - date.fromisoformat(ctx["start"][:10])).days
    except (TypeError, ValueError):
        return None


def _monetary(doc, unit):
    xml = doc.get("units", {}).get(unit, "")
    return "iso4217" in xml and "divide" not in xml


def _ancestors(parents, member, domain):
    seen, todo = set(), list(parents.get(member, ()))
    while todo:
        p = todo.pop()
        if p in seen:
            continue
        seen.add(p)
        todo.extend(parents.get(p, ()))
    return seen - domain


def evaluate(query, doc, facts, *, statement_only=True, relaxed=False):
    """Return candidate findings for the question concept within question-period facts.

    ``facts`` must already be restricted to the question period. ``relaxed``
    ignores cube category, notes/parenthetical gating and the subtotal guard.
    """
    concept = doc["target"]["concept"]
    contexts = doc["contexts"]
    doc_days = document_period_days(query, doc)
    all_cubes = cubes(doc)
    findings = []
    by_unit = defaultdict(list)
    seen = set()
    for f in facts:
        if f["concept"] == concept and _monetary(doc, f.get("unit")):
            ctx = contexts.get(f["context"], {})
            # Duplicate facts (same aspects and value) are one fact, as in the
            # reference processor's default duplicate removal.
            key = (ctx.get("start"), ctx.get("end"), ctx.get("instant"),
                   tuple(sorted(ctx.get("dimensions", {}).items())), f.get("unit"), f["value"])
            if key in seen:
                continue
            seen.add(key)
            by_unit[f.get("unit")].append(f)
    for unit, cfacts in by_unit.items():
        def dims_of(f):
            return contexts.get(f["context"], {}).get("dimensions", {})

        def period_ok(f):
            d = _days(contexts.get(f["context"], {}))
            return d == 0 or doc_days is None or d == doc_days

        for cube in all_cubes:
            if statement_only and not relaxed and cube["category"] != "statement":
                continue
            valid = [f for f in cfacts if _in_cube(cube, concept, dims_of(f))]
            for axis, spec in cube["dims"].items():
                if axis.split(":")[-1] not in ROLLUP_AXES:
                    continue
                domain, parents = spec["domain"], spec["parents"]
                member_desc = set(parents) - domain
                with_axis = [f for f in valid if axis in dims_of(f)]
                single = defaultdict(list)
                for f in with_axis:
                    if len(dims_of(f)) == 1:
                        single[dims_of(f)[axis]].append(f)
                notes = [f for f in cfacts if axis in dims_of(f) and any(
                    c["category"] == "notes" and _in_cube(c, concept, dims_of(f)) for c in all_cubes)]
                paren = [f for f in cfacts if axis in dims_of(f) and any(
                    c["category"] == "parenthetical" and _in_cube(c, concept, dims_of(f)) for c in all_cubes)]
                if relaxed:
                    notes, paren = [], []
                # ---- rule 9574
                multi = [f for f in with_axis if len(dims_of(f)) > 1]
                subtotal = relaxed or all(single.get(dims_of(f)[axis]) for f in multi)
                totals = [f for f in valid if period_ok(f) and (
                    not dims_of(f) or (set(dims_of(f)) == {axis} and dims_of(f)[axis] in domain))]
                total = max(totals, key=lambda f: int(f.get("decimals") or -99)
                            if str(f.get("decimals")).lstrip("-").isdigit() else 99) if totals else None
                greater = bool(notes) and total is not None and max(f["value"] for f in notes) > total["value"]
                if subtotal and (not notes or greater) and not paren:
                    members = []
                    for m in sorted(member_desc):
                        anc = _ancestors(parents, m, domain)
                        if any(single.get(a) for a in anc):
                            continue
                        if single.get(m):
                            members.append(single[m][0])
                    if members:
                        s = sum((f["value"] for f in members), ZERO)
                        tv = total["value"] if total else None
                        findings.append({"rule": "9574", "role": cube["role"], "axis": axis, "unit": unit,
                                         "total": tv, "total_context": total["context"] if total else None,
                                         "sum": s, "members": [(dims_of(f)[axis], f["value"]) for f in members],
                                         "fires": tv is None or s != tv})
                # ---- rule 10093
                two = [f for f in with_axis if len(dims_of(f)) == 2]
                for sub in sorted({dims_of(f)[axis] for f in two}):
                    d2_facts = [f for f in two if dims_of(f)[axis] == sub]
                    d2 = sorted({a for f in d2_facts for a in dims_of(f) if a != axis})
                    if not d2:
                        continue
                    dim2 = d2[0]
                    if dim2.split(":")[-1] == "LegalEntityAxis":
                        continue
                    more = [f for f in with_axis if dims_of(f).get(axis) == sub and dim2 in dims_of(f)
                            and len(dims_of(f)) > 2]
                    sub_ok = relaxed or all(any(dims_of(g).get(axis) == sub and dims_of(g).get(dim2) == dims_of(f)[dim2]
                                     and len(dims_of(g)) == 2 for g in with_axis) for f in more)
                    tot = [f for f in single.get(sub, []) if period_ok(f)]
                    notes2 = [f for f in notes if dims_of(f).get(axis) == sub and dim2 in dims_of(f)]
                    if not tot or not sub_ok:
                        continue
                    if notes2 and not max(f["value"] for f in notes2) > tot[0]["value"]:
                        continue
                    mem = [f for f in with_axis if dims_of(f).get(axis) == sub and dim2 in dims_of(f)]
                    s = sum((f["value"] for f in mem), ZERO)
                    findings.append({"rule": "10093", "role": cube["role"], "axis": axis, "sub_member": sub,
                                     "dim2": dim2, "unit": unit, "total": tot[0]["value"],
                                     "total_context": tot[0]["context"], "sum": s,
                                     "members": [(dims_of(f).get(dim2), f["value"]) for f in mem],
                                     "fires": s != tot[0]["value"]})
    return findings
