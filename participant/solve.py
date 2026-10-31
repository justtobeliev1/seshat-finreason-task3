"""Evidence-preserving, offline Task 3 solver. Reads question materials only.

Keep every competing derivation in the trace; neither ids nor rule labels are
used to select predictions. Public gold belongs exclusively in evaluation code.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from participant.xbrl import parse_query

ZERO = Decimal(0)


def number(value):
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def with_commas(text):
    """'-1234567.5' -> '-1,234,567.5'; the numeric value is unchanged."""
    sign = "-" if text.startswith("-") else ""
    whole, dot, frac = text.lstrip("-").partition(".")
    return sign + f"{int(whole):,}" + dot + frac


def period_matches(context, target):
    if target.get("instant"):
        return context.get("instant") == target["instant"]
    if target.get("start") is None and target.get("end"):
        return context.get("instant") == target["end"]
    if target.get("end"):
        return context.get("start") == target["start"] and context.get("end") == target["end"]
    return context.get("instant") == target.get("start")


def precision(fact):
    val = fact.get("decimals")
    if str(val).upper() == "INF":
        return 1000
    try:
        return int(val)
    except (ValueError, TypeError):
        return -1000


def fact_pick(facts):
    """Prefer disclosed precision; retain conflicting duplicate diagnostics."""
    if not facts:
        return None
    return max(facts, key=precision)


def context_identity(context):
    return (context.get("entity_scheme"), context.get("entity"), context.get("start"),
            context.get("end"), context.get("instant"),
            tuple(sorted((a, str(m)) for a, m in context.get("dimensions", {}).items())))


def expanded_qname(name, namespaces):
    prefix, separator, local = name.partition(":")
    uri = namespaces.get(prefix) if separator else None
    return f"{{{uri}}}{local}" if uri else name


def graph_ancestors(edges):
    parents = defaultdict(set)
    for edge in edges:
        if edge.get("arcrole", "").endswith("domain-member"):
            parents[edge["child"]].add(edge["parent"])
    def ancestors(member):
        seen, todo = set(), list(parents[member])
        while todo:
            p = todo.pop()
            if p in seen:
                continue
            seen.add(p)
            todo.extend(parents[p] - seen)
        return seen
    return ancestors


def definition_children(edges, role, parent, kind):
    return [(e.get("target_role") or role, e["child"]) for e in edges
            if e["role"] == role and e["parent"] == parent
            and e.get("arcrole", "").rsplit("/", 1)[-1] == kind]


def domain_states(edges, role, root):
    """Walk an ELR, following explicit targetRole transitions only."""
    seen, todo = set(), [(role, root)]
    while todo:
        state = todo.pop()
        if state in seen:
            continue
        seen.add(state)
        todo.extend(definition_children(edges, *state, "domain-member"))
    return seen


def domain_members(edges, role, root):
    return {member for _, member in domain_states(edges, role, root)}


def dimension_networks(doc, axis):
    edges = doc["definition"]
    networks = []
    for role in sorted({e["role"] for e in edges if e["parent"] == axis}):
        domains = definition_children(edges, role, axis, "dimension-domain")
        if domains:
            allowed = set().union(*(domain_states(edges, *domain) for domain in domains))
            networks.append((role, allowed))
    return networks or [(None, None)]


def role_category(doc, role):
    """Use the schema's declared category, never words guessed from a role URI."""
    fields = [field.strip().lower() for field in
              doc.get("role_definitions", {}).get(role, "").split(" - ")]
    if "statement" in fields:
        return "statement"
    if "disclosure" in fields:
        return "disclosure"
    return "unknown"


def governing_cube_roles(doc, axis, definition_role=None):
    """Identify positive cubes whose primary tree contains the queried concept.

    DQC 0126 skips statement roles containing cubes; their dimensional checks
    must not be overridden by an unrelated calculation in the same role.
    """
    edges, target = doc["definition"], doc["target"]["concept"]
    roles = set()
    for edge in edges:
        if edge.get("arcrole", "").rsplit("/", 1)[-1] != "all":
            continue
        role = edge["role"]
        if target not in domain_members(edges, role, edge["parent"]):
            continue
        cube_role = edge.get("target_role") or role
        if any(child == axis and (definition_role is None or axis_role == definition_role)
               for axis_role, child in
               definition_children(edges, cube_role, edge["child"], "hypercube-dimension")):
            roles.add(role)
    return sorted(roles)


def calc_candidates(doc, facts, reported, *, impute=True, dqc_decimals=False):
    """Compute each ELR separately, with context/unit isolation and arc dedup."""
    concept = doc["target"]["concept"]
    by_key = defaultdict(list)
    by_context = defaultdict(list)
    for f in facts:
        c = doc["contexts"].get(f["context"], {})
        scope = context_identity(c)
        by_key[(f["concept"], scope, f.get("unit"))].append(f)
        if not c.get("dimensions"):
            by_context[(scope, f.get("unit"))].append(f)
    arcs = defaultdict(dict)
    for a in doc["calculation"]:
        arcs[(a["role"], a["parent"])][a["child"]] = a["weight"]
    out = []
    for (role, parent), children in arcs.items():
        if parent != concept:
            continue
        pick = fact_pick
        if dqc_decimals:
            # DQC 0126: prefer the statement's most frequent decimals, then
            # finer, then coarser duplicates.
            network = {a["parent"] for a in doc["calculation"] if a["role"] == role} | {
                a["child"] for a in doc["calculation"] if a["role"] == role}
            counts = Counter(str(f.get("decimals")) for f in facts if f["concept"] in network)
            fs_decimals = counts.most_common(1)[0][0] if counts else None

            def pick(candidates, fs=fs_decimals):
                if not candidates:
                    return None
                exact = [f for f in candidates if str(f.get("decimals")) == fs]
                if exact:
                    return exact[0]
                return fact_pick(candidates)
        for (scope, unit), context_facts in by_context.items():
            if reported and unit != reported.get("unit"):
                continue
            context = context_facts[0]["context"]
            evidence, missing, imputed = [], [], []
            total = ZERO
            for child, weight in children.items():
                f = pick(by_key[(child, scope, unit)])
                if f is None:
                    missing.append(child)
                    if not impute:
                        # DQC 0126 does not add dimension components for statements.
                        continue
                    # A missing consolidated operand may have an explicit
                    # class/segment decomposition. Preserve the assumption:
                    # two members alone do not prove disclosure completeness.
                    child_doc = {**doc, "target": {**doc["target"], "concept": child}}
                    # Anchor to this calculation even when the target total is
                    # absent or belongs to a different candidate context.
                    rollups = [c for c in dimension_candidates(child_doc, facts, context_facts[0])
                               if c.get("unit") == unit and len(c["evidence"]) >= 2]
                    values = {c["value"] for c in rollups}
                    if len(values) == 1:
                        rollup = max(rollups, key=lambda c: c["rank"])
                        total += rollup["value"] * weight
                        imputed.append({"concept": child, "weight": number(weight),
                                        "value": number(rollup["value"]), "rollup": rollup})
                else:
                    total += f["value"] * weight
                    evidence.append({"concept": child, "value": number(f["value"]),
                                     "weight": number(weight), "context": f["context"]})
            if not evidence:
                continue
            # Missing summation terms may be zero by non-disclosure; record them.
            rank = (int(not missing), Decimal(len(evidence)) / len(children), len(evidence))
            if reported is not None:
                reported_scope = context_identity(doc["contexts"][reported["context"]])
                rank = (int(scope == reported_scope), *rank)
            out.append({"kind": "calculation", "value": total, "role": role,
                        "context": context, "unit": unit, "evidence": evidence,
                        "missing": missing, "imputed_operands": imputed, "rank": rank})
    return out


def dimension_candidates(doc, facts, reported, *, note_domain_fallback=False):
    from participant.dqc_rules import ROLLUP_AXES
    concept = doc["target"]["concept"]
    target_facts = [f for f in facts if f["concept"] == concept]
    groups = defaultdict(lambda: defaultdict(list))
    for f in target_facts:
        c = doc["contexts"].get(f["context"], {})
        dims = c.get("dimensions", {})
        for axis, member in dims.items():
            if axis.split(":")[-1] not in ROLLUP_AXES or not isinstance(member, str):
                continue
            other = tuple(sorted((a, str(m)) for a, m in dims.items() if a != axis))
            key = (axis, other, context_identity(c)[:-1], f.get("unit"))
            groups[key][member].append(f)
    out = []
    for (axis, other, scope, unit), members in groups.items():
        # The question has no dimensional qualifier: use consolidated rollups.
        if other:
            continue
        if reported and (unit != reported.get("unit") or
                         scope != context_identity(doc["contexts"][reported["context"]])[:-1]):
            continue
        networks = dimension_networks(doc, axis)
        observed_fallback = False
        if (note_domain_fallback and reported is None
                and all(role is not None and role_category(doc, role) == "disclosure"
                        for role, _ in networks)):
            declared = {member for _, states in networks for _, member in states}
            if set(members) - declared:
                # A note-specific domain does not establish the global scope
                # of otherwise unqualified member facts. Keep both views;
                # treating all observed members as complete is still assumed.
                networks.append((None, None))
                observed_fallback = True
        for role, allowed in networks:
            allowed_members = None if allowed is None else {m for _, m in allowed}
            chosen = {m: fact_pick(fs) for m, fs in members.items()
                      if allowed_members is None or m in allowed_members}
            if role is None:
                ancestors = graph_ancestors(doc["definition"])
                top = {m: f for m, f in chosen.items() if not ancestors(m).intersection(chosen)}
            else:
                descendants = {m: set().union(*(
                    domain_members(doc["definition"], member_role, m)
                    for member_role, member in allowed if member == m)) - {m}
                    for m in chosen}
                top = {m: f for m, f in chosen.items()
                       if not any(m in ds for ds in descendants.values())}
            if not top:
                continue
            evidence = [{"member": m, "value": number(f["value"]), "context": f["context"]}
                        for m, f in sorted(top.items())]
            governing = governing_cube_roles(doc, axis, role) if role is not None else []
            if observed_fallback:
                governing = []
            out.append({"kind": "dimension", "value": sum((f["value"] for f in top.values()), ZERO),
                        "axis": axis, "unit": unit, "entity": scope[1], "evidence": evidence,
                        "definition_role": role, "governing_cube_roles": governing,
                        "role_category": role_category(doc, role),
                        "observed_note_domain_fallback": observed_fallback and role is None,
                        "excluded_outside_domain": sorted(set(members) - set(chosen)),
                        "excluded_descendants": sorted(set(chosen) - set(top)),
                        "rank": (int(bool(governing)), len(top))})
    return out


NEGATIVE_POLICIES = ("least_dims", "first", "last", "maxabs", "minabs", "modal")


def pick_negative(negative_facts, contexts, policy="least_dims"):
    """Choose one DQC 0015 violation when several are reported for the question.

    Every listed fact violates the rule; the question names only concept and
    period. ``least_dims`` is the measured N1 default. The alternatives order
    by instance-document position or by magnitude.
    """
    if policy == "least_dims":
        return min(negative_facts, key=lambda f: (
            len(contexts.get(f["context"], {}).get("dimensions", {})), -precision(f)))
    if policy == "first":
        return negative_facts[0]
    if policy == "last":
        return negative_facts[-1]
    if policy == "maxabs":
        return min(negative_facts, key=lambda f: f["value"])
    if policy == "minabs":
        return max(negative_facts, key=lambda f: f["value"])
    if policy == "modal":
        # If each violating fact is equally likely to be the asked one, the
        # most frequent value is the best guess; ties keep document order.
        counts = Counter(f["value"] for f in negative_facts)
        top = max(counts.values())
        return next(f for f in negative_facts if counts[f["value"]] == top)
    raise ValueError(f"unknown negative policy {policy!r}")


def rule_10093_override(query, doc, facts, *, over_9574=False):
    """A single DQC 0117 two-dimension finding when the one-dimension rule is silent.

    With ``over_9574`` a lone two-dimension finding also replaces a firing
    one-dimension finding (all cube roles are considered).
    """
    from participant.dqc0117 import evaluate
    findings = [f for f in evaluate(query, doc, facts, statement_only=not over_9574) if f["fires"]]
    if any(f["rule"] == "9574" for f in findings) and not over_9574:
        return None
    two_dim = [f for f in findings if f["rule"] == "10093"]
    if len({(f["total"], f["sum"]) for f in two_dim}) != 1:
        return None
    f = two_dim[0]
    return {"kind": "dimension_10093", "value": f["sum"], "extracted": f["total"],
            "context": f["total_context"], "axis": f["axis"], "sub_member": f["sub_member"],
            "dim2": f["dim2"], "role": f["role"],
            "evidence": [{"member": m, "value": number(v)} for m, v in f["members"]]}


def observed_member_rollup(doc, facts, reported):
    """Sum single-dimension roll-up members when no declared domain admits them.

    Used only when every other derivation is unresolved; the member set is
    whatever the instance reports, so completeness is an assumption.
    """
    from participant.dqc_rules import ROLLUP_AXES
    concept, contexts = doc["target"]["concept"], doc["contexts"]
    unit = reported.get("unit") if reported else None
    by_axis = defaultdict(dict)
    for f in facts:
        dims = contexts.get(f["context"], {}).get("dimensions", {})
        if f["concept"] != concept or len(dims) != 1 or (unit and f.get("unit") != unit):
            continue
        (axis, member), = dims.items()
        if axis.split(":")[-1] in ROLLUP_AXES:
            by_axis[axis].setdefault(member, f)
    if not by_axis:
        return None
    axis, members = max(by_axis.items(), key=lambda item: (len(item[1]), item[0]))
    return {"kind": "observed_members", "axis": axis, "missing": [],
            "value": sum((f["value"] for f in members.values()), ZERO),
            "evidence": [{"member": m, "value": number(f["value"]), "context": f["context"]}
                         for m, f in sorted(members.items())]}


def predict(query, *, dimension_projection=False, note_domain_fallback=False,
            independent_reconstruction=False, cube_agreement_guard=False,
            negative_policy="least_dims", rule_10093=False, ignore_exemptions=False,
            observed_member_fallback=False, prefer_10093=False, standard_member_retry=False,
            no_imputation=False, dqc_decimals=False):
    from participant.dqc_rules import nonnegative_applicable
    doc = parse_query(query)
    trace = {"query_sha256": hashlib.sha256(query.encode()).hexdigest(),
             "target": doc["target"], "parse_errors": doc.get("section_errors", []),
             "partial_sections": doc.get("partial_sections", [])}
    if not doc.get("target", {}).get("concept"):
        return {"extracted_value": "0", "calculated_value": "0"}, {**trace, "method": "unparsed"}
    contexts = doc["contexts"]
    facts = [f for f in doc["facts"] if f.get("value") is not None
             and period_matches(contexts.get(f["context"], {}), doc["target"])]
    trace["numeric_fact_count"] = sum(f.get("value") is not None for f in doc["facts"])
    trace["matching_period_fact_count"] = len(facts)
    target_facts = [f for f in facts if f["concept"] == doc["target"]["concept"]]
    reported_facts = [f for f in target_facts
                      if not contexts.get(f["context"], {}).get("dimensions")]
    reported = fact_pick(reported_facts)
    if reported is None and len(target_facts) == 1:
        reported = target_facts[0]
    namespaces = doc.get("namespaces", {})

    def violates(f, dimensional_exemptions=True):
        dims = ({expanded_qname(axis, namespaces): expanded_qname(member, namespaces)
                 for axis, member in contexts.get(f["context"], {}).get("dimensions", {}).items()}
                if dimensional_exemptions else {})
        return f["value"] < ZERO and nonnegative_applicable(
            f["concept"], dims, namespace=namespaces.get(f["concept"].split(":", 1)[0]))

    negative_facts = [f for f in target_facts if violates(f)]
    if negative_facts and ignore_exemptions:
        # Keep the rule's applicability, but let exempted dimensional facts
        # compete for the position-based choice below.
        negative_facts = [f for f in target_facts if violates(f, dimensional_exemptions=False)]
    # A rule violation can be dimensional even when a positive consolidated
    # value exists. Prefer the least-qualified violation; equal candidates
    # retain document order and are explicitly marked as ambiguous below.
    if negative_facts:
        reported = pick_negative(negative_facts, contexts, negative_policy)
    extracted = reported["value"] if reported else ZERO
    trace["reported_candidates"] = [{"context": f["context"], "unit": f.get("unit"),
                                      "value": number(f["value"]),
                                      "dimensions": contexts.get(f["context"], {}).get("dimensions", {})}
                                     for f in target_facts]
    calcs = calc_candidates(doc, facts, reported, impute=not no_imputation, dqc_decimals=dqc_decimals)
    dimension_doc, dimension_facts, projection_assumptions = doc, facts, []
    if dimension_projection:
        from participant.dimension_projection import project_dimensions
        dimension_doc, dimension_facts, projection_assumptions = project_dimensions(doc, facts)
    dims = dimension_candidates(dimension_doc, dimension_facts, reported,
                                note_domain_fallback=note_domain_fallback)
    trace["dimension_projection_assumptions"] = projection_assumptions
    # Use a named DQC nonnegative constraint, rather than a taxonomy balance guess.
    dim_discrepancies = [c for c in dims if c["value"] != extracted]
    calc_discrepancies = [c for c in calcs if c["value"] != extracted]
    direct_calcs = [c for c in calc_discrepancies if len(c["evidence"]) >= 2]
    cube_dims = [c for c in dim_discrepancies if c.get("governing_cube_roles")]
    if negative_facts:
        selected = {"kind": "nonnegative", "value": -extracted,
                    "context": reported["context"],
                    "evidence": [{"concept": reported["concept"],
                                  "value": number(extracted), "context": reported["context"]}]}
    elif cube_dims:
        selected = max(cube_dims, key=lambda c: c["rank"])
    elif direct_calcs:
        # The question explicitly requests calculation relationships. A
        # dimensional view can be a note breakdown of only part of the total.
        # Missing children remain a documented, unverified zero assumption.
        selected = max(direct_calcs, key=lambda c: c["rank"])
        agreeing_cubes = [c for c in dims if c["value"] == extracted
                          and selected["role"] in c.get("governing_cube_roles", [])
                          and len(c["evidence"]) >= 2]
        # A missing-operand calculation is not evidence against a matching
        # dimensional reconciliation in its own cube. Agreement on one axis
        # does not, however, erase a discrepancy on another independent axis.
        if (cube_agreement_guard and selected.get("missing")
                and agreeing_cubes and not dim_discrepancies):
            selected = max(agreeing_cubes, key=lambda c: c["rank"])
    elif dim_discrepancies:
        selected = max(dim_discrepancies, key=lambda c: c["rank"])
    else:
        if calc_discrepancies:
            selected = max(calc_discrepancies, key=lambda c: c["rank"])
        elif calcs:
            selected = max(calcs, key=lambda c: c["rank"])
        elif dims:
            selected = max(dims, key=lambda c: c["rank"])
        else:
            selected = {"kind": "unresolved", "value": ZERO, "evidence": []}
    reconstruction = None
    if independent_reconstruction and negative_facts:
        from participant.reconstruction import reconstruct_negative
        reconstruction, diagnostics = reconstruct_negative(doc, facts, reported)
        trace["reconstruction_diagnostics"] = diagnostics
        if reconstruction is not None:
            selected = reconstruction
    # DQC 0117 rule 10093 reports a one-dimension total against its
    # two-dimension breakdown. Use it only where rule 9574 is silent and the
    # current answer is a dimensional view or a calculation with missing operands.
    if (rule_10093 and not negative_facts and (selected["kind"] in ("dimension", "unresolved")
                                              or (selected["kind"] == "calculation" and selected.get("missing")))):
        override = rule_10093_override(query, doc, facts)
        if override is None and prefer_10093 and selected["kind"] == "dimension":
            override = rule_10093_override(query, doc, facts, over_9574=True)
        if override is not None:
            trace["replaced_selection"] = selected
            selected = override
            extracted = override["extracted"]
    # Every question is a flagged inconsistency. When an undeclared observed
    # member set happens to reconcile, the flagged statement cube most likely
    # contained only standard-taxonomy members; extension members come from notes.
    if (standard_member_retry and selected["kind"] == "dimension" and selected["value"] == extracted
            and extracted != ZERO and selected.get("definition_role") is None):
        standard = [e for e in selected["evidence"]
                    if e["member"].split(":", 1)[0] in ("us-gaap", "srt")]
        if standard and len(standard) < len(selected["evidence"]):
            trace["replaced_selection"] = selected
            selected = {**selected, "kind": "dimension_standard_members", "evidence": standard,
                        "value": sum((Decimal(e["value"]) for e in standard), ZERO)}
    if observed_member_fallback and selected["kind"] == "unresolved":
        fallback = observed_member_rollup(doc, facts, reported)
        if fallback is not None:
            selected = fallback
    candidates = dims + calcs + ([reconstruction] if reconstruction is not None else [])
    trace["method"] = selected["kind"]
    trace["selected"] = selected
    projected_contexts = {a["projected_context"] for a in projection_assumptions}
    trace["selected_dimension_projection"] = any(
        e.get("context") in projected_contexts for e in selected.get("evidence", []))
    trace["selected_note_domain_fallback"] = selected.get("observed_note_domain_fallback", False)
    trace["candidates"] = candidates
    trace["negative_fact_candidates"] = [{"context": f["context"], "value": number(f["value"])}
                                         for f in negative_facts]
    trace["ambiguous_extraction"] = len({f["value"] for f in negative_facts}) > 1
    trace["missing_operand_assumption"] = bool(selected.get("missing"))
    trace["score_status"] = "unscored"
    trace["ambiguous"] = trace["ambiguous_extraction"] or len({
        c["value"] for c in candidates if c["value"] != extracted}) > 1
    return {"extracted_value": number(extracted), "calculated_value": number(selected["value"])}, trace


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--trace", required=True)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--dimension-projection", action="store_true",
                    help="Experimental equal-value dimension bridge; assumptions are traced")
    ap.add_argument("--note-domain-fallback", action="store_true",
                    help="Experimental observed-member view for note-only domains without a reported total")
    ap.add_argument("--independent-reconstruction", action="store_true",
                    help="Experimental complete forward/inverse calculation reconstruction")
    ap.add_argument("--cube-agreement-guard", action="store_true",
                    help="Experimental agreeing-cube override; rejected N1_D1 policy, off by default")
    ap.add_argument("--negative-policy", choices=NEGATIVE_POLICIES, default="least_dims",
                    help="Choice among several DQC 0015 violations; least_dims is the N1 default")
    ap.add_argument("--ignore-exemptions", action="store_true",
                    help="Treat dimensional DQC 0015 member/axis exemptions as violations too")
    ap.add_argument("--observed-member-fallback", action="store_true",
                    help="When unresolved, sum observed single-dimension roll-up members")
    ap.add_argument("--dqc-decimals", action="store_true",
                    help="Choose duplicate calculation operands by the statement's most frequent decimals")
    ap.add_argument("--no-imputation", action="store_true",
                    help="Do not fill missing calculation operands from dimensional rollups (DQC 0126 behaviour)")
    ap.add_argument("--standard-member-retry", action="store_true",
                    help="If an undeclared member rollup reconciles exactly, keep standard-taxonomy members only")
    ap.add_argument("--prefer-10093", action="store_true",
                    help="With --rule-10093, a lone 10093 finding also replaces a 9574 dimensional answer")
    ap.add_argument("--rule-10093", action="store_true",
                    help="Use DQC 0117 two-dimension findings where rule 9574 is silent")
    ap.add_argument("--number-format", choices=("plain", "comma"), default="plain",
                    help="Output style; comma adds thousands separators (numeric value unchanged)")
    args = ap.parse_args()
    for p in [args.output, args.trace]:
        Path(p).parent.mkdir(parents=True, exist_ok=True)
    tally = Counter()
    with open(args.input) as src, open(args.output, "w") as dst, open(args.trace, "w") as log:
        for i, line in enumerate(src):
            if args.limit is not None and i >= args.limit:
                break
            row = json.loads(line)
            # Deliberately forward only query: no answer, id, source or rule label.
            pred, trace = predict(row["query"], dimension_projection=args.dimension_projection,
                                  note_domain_fallback=args.note_domain_fallback,
                                  independent_reconstruction=args.independent_reconstruction,
                                  cube_agreement_guard=args.cube_agreement_guard,
                                  negative_policy=args.negative_policy,
                                  rule_10093=args.rule_10093,
                                  ignore_exemptions=args.ignore_exemptions,
                                  observed_member_fallback=args.observed_member_fallback,
                                  prefer_10093=args.prefer_10093,
                                  standard_member_retry=args.standard_member_retry,
                                  no_imputation=args.no_imputation,
                                  dqc_decimals=args.dqc_decimals)
            if args.number_format == "comma":
                pred = {k: with_commas(v) for k, v in pred.items()}
            dst.write(json.dumps({"id": row["id"], **pred}) + "\n")
            log.write(json.dumps({"id": row["id"], **trace}, default=str) + "\n")
            tally[trace["method"]] += 1
            tally["ambiguous"] += bool(trace.get("ambiguous"))
    print(json.dumps(tally, indent=2))


if __name__ == "__main__":
    main()
