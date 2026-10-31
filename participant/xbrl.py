"""Parse the filing materials embedded in a Task 3 query, without network I/O.

Facts retain their reported text and Decimal value. Locator labels are opaque:
relationships are resolved exclusively through their href and schema element IDs.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import io
import json
import re
from urllib.parse import unquote, urldefrag
import xml.etree.ElementTree as ET

XBRLI = "http://www.xbrl.org/2003/instance"
XLINK = "http://www.w3.org/1999/xlink"
LINK = "http://www.xbrl.org/2003/linkbase"
XBRLDI = "http://xbrl.org/2006/xbrldi"
XBRLDT = "http://xbrl.org/2005/xbrldt"
XS = "http://www.w3.org/2001/XMLSchema"
XSI = "http://www.w3.org/2001/XMLSchema-instance"


def _xml(text):
    """Return a document or its complete prefix when the supplied XML ends early.

    Practice materials can be cut off mid-token. Iterparse still exposes the
    preceding complete nodes. Keep only those nodes, retaining an open root or
    extended link as a container. Never repair an unfinished fact or context,
    and never recover XML with an interior structural/syntax error.
    """
    namespaces = {}
    parser = ET.iterparse(io.StringIO(text.strip()), events=("start-ns", "start", "end"))
    root, stack = None, []
    try:
        for event, node in parser:
            if event == "start-ns":
                prefix, uri = node
                namespaces[prefix] = uri
            elif event == "start":
                if root is None:
                    root = node
                stack.append(node)
            else:
                stack.pop()
    except ET.ParseError as error:
        # Expat: no element found, unclosed token, partial character. Other
        # failures (e.g. mismatched tags) are not evidence of simple truncation.
        if root is None or error.code not in (3, 5, 6):
            raise
        open_links = {f"{{{LINK}}}{kind}Link" for kind in
                      ("calculation", "definition", "presentation", "label")}
        for index in range(len(stack) - 1, 0, -1):
            node = stack[index]
            if node.tag not in open_links:
                stack[index - 1].remove(node)
        return root, namespaces, str(error)
    return parser.root, namespaces, None


def _decimal(text, default=None):
    if text is None:
        return default
    try:
        value = Decimal(text.strip())
        return value if value.is_finite() else default
    except InvalidOperation:
        return default


def parse_query(query: str) -> dict:
    """Parse only the supplied query. Missing/malformed sections are recorded.

    ``target.start`` is None for instant questions; ``target.end`` is the
    instant date. Units in facts are instance unit IDs, not currency guesses.
    Typed dimension members use a canonical JSON string retaining their full
    typed XML content, including element names, attributes and child order.
    """
    result = {"target": {"concept": None, "start": None, "end": None},
              "contexts": {}, "facts": [], "calculation": [],
              "definition": [], "presentation": [], "balance": {}, "role_definitions": {},
              "units": {}, "section_errors": [], "partial_sections": []}
    sections = {}
    for match in re.finditer(r"^##\s*([^\r\n]+)\r?\n(.*?)(?=^##|\Z)", query,
                             re.MULTILINE | re.DOTALL):
        sections[match.group(1).strip()] = match.group(2).strip()

    documents = {}
    namespace_by_uri = {}
    # Prefer the lexical prefixes used by the instance for a stable output.
    names = ["Instance document", "Schema document", "Calculation linkbase document",
             "Definition linkbase document", "Presentation linkbase document"]
    for name in names:
        text = sections.get(name)
        if not text:
            result["section_errors"].append(f"{name}: missing section")
            continue
        if text in ("None", "null"):
            result["section_errors"].append(f"{name}: not provided")
            continue
        try:
            root, namespaces, partial_error = _xml(text)
        except ET.ParseError as error:
            result["section_errors"].append(f"{name}: {error}")
            continue
        if partial_error:
            result["partial_sections"].append(name)
            result["section_errors"].append(
                f"{name}: partial XML; retained complete elements ({partial_error})")
        documents[name] = (root, namespaces)
        for prefix, uri in namespaces.items():
            if prefix:
                namespace_by_uri.setdefault(uri, prefix)

    result["namespaces"] = {}
    for uri, prefix in namespace_by_uri.items():
        result["namespaces"].setdefault(prefix, uri)

    def qname(value, namespaces=None):
        if value.startswith("{"):
            uri, local = value[1:].split("}", 1)
            prefix = namespace_by_uri.get(uri)
            return f"{prefix}:{local}" if prefix else value
        if namespaces and ":" in value:
            prefix, local = value.split(":", 1)
            uri = namespaces.get(prefix)
            if uri in namespace_by_uri:
                return f"{namespace_by_uri[uri]}:{local}"
        return value

    question = re.search(
        r"^Question1:\s*What's the reported value of\s+(\S+)\s+in the Instance document "
        r"for the period\s+(\d{4}-\d{2}-\d{2})(?:\s+to\s+(\d{4}-\d{2}-\d{2}))?",
        query, re.MULTILINE)
    if question:
        concept, first, last = question.groups()
        result["target"] = {"concept": qname(concept),
                            "start": first if last else None, "end": last or first}
        if not last:
            result["target"]["instant"] = first
    else:
        result["section_errors"].append("Question1: target not found")

    schema_ids = {}
    schema = documents.get("Schema document")
    if schema:
        root, namespaces = schema
        target_namespace = root.get("targetNamespace", "")
        # Preserve supplied metadata for downstream classification. A URI can
        # contain "Statement" even when its definition explicitly says
        # Disclosure; neither the parser nor missing metadata infers a type.
        for role in root.iter(f"{{{LINK}}}roleType"):
            uri = role.get("roleURI")
            definition = role.find(f"{{{LINK}}}definition")
            if uri and definition is not None:
                result["role_definitions"][uri] = "".join(definition.itertext())
        for element in root.iter(f"{{{XS}}}element"):
            local = element.get("name")
            if not local:
                continue
            concept = qname(f"{{{target_namespace}}}{local}")
            if element.get("id"):
                schema_ids[element.get("id")] = concept
            balance = element.get(f"{{{XBRLI}}}balance")
            if balance:
                result["balance"][concept] = balance

    # The supplied taxonomy is text, not an XML document. Its balance is an
    # independent metadata source; do not interpret its generic arcs as the
    # filing's own calculation network.
    taxonomy = sections.get("US GAAP Taxonomy", "")
    for block in taxonomy.split("[Concept Core]")[1:]:
        concept = re.search(r"^ID:\s*(\S+)", block, re.MULTILINE)
        balance = re.search(r"\bBalance:\s*(debit|credit)\b", block)
        if concept and balance:
            result["balance"][qname(concept.group(1))] = balance.group(1)

    def href_concept(href):
        fragment = unquote(urldefrag(href)[1])
        if fragment in schema_ids:
            return schema_ids[fragment]
        if ":" in fragment:
            return qname(fragment)
        # XBRL standard schema IDs are QName prefixes joined with '_'. Use
        # namespace declarations, never the unrelated locator label.
        for prefix in sorted(set(namespace_by_uri.values()), key=len, reverse=True):
            if fragment.startswith(prefix + "_"):
                return prefix + ":" + fragment[len(prefix) + 1:]
        # Preserve the fragment if its namespace cannot be established.
        return fragment

    instance = documents.get("Instance document")
    if instance:
        root, namespaces = instance

        def typed_node(node):
            return [qname(node.tag), sorted((qname(k), v) for k, v in node.attrib.items()),
                    (node.text or "").strip(),
                    [[typed_node(child), (child.tail or "").strip()] for child in node]]

        for node in root.iter():
            if node.tag == f"{{{XBRLI}}}context":
                dimensions = {}
                for member in node.iter():
                    if member.tag == f"{{{XBRLDI}}}explicitMember":
                        dimensions[qname(member.get("dimension", ""), namespaces)] = qname(
                            (member.text or "").strip(), namespaces)
                    elif member.tag == f"{{{XBRLDI}}}typedMember":
                        dimensions[qname(member.get("dimension", ""), namespaces)] = "typed:" + json.dumps(
                            [typed_node(child) for child in member], separators=(",", ":"), ensure_ascii=False)
                def content(local):
                    return node.findtext(f".//{{{XBRLI}}}{local}")
                identifier = node.find(f".//{{{XBRLI}}}identifier")
                result["contexts"][node.get("id")] = {
                    "entity": identifier.text if identifier is not None else None,
                    "entity_scheme": identifier.get("scheme") if identifier is not None else None,
                    "start": content("startDate"), "end": content("endDate"),
                    "instant": content("instant"), "dimensions": dimensions}
            elif node.tag == f"{{{XBRLI}}}unit":
                result["units"][node.get("id")] = ET.tostring(node, encoding="unicode")
            elif node.get("contextRef") is not None:
                if node.get(f"{{{XSI}}}nil") in ("true", "1"):
                    continue
                raw = "".join(node.itertext()).strip()
                value = _decimal(raw)
                if value is not None:
                    result["facts"].append({"concept": qname(node.tag),
                        "context": node.get("contextRef"), "unit": node.get("unitRef"),
                        "value": value, "decimals": node.get("decimals"), "raw": raw})

    for kind in ("calculation", "definition", "presentation"):
        document = documents.get(f"{kind.title()} linkbase document")
        if not document:
            continue
        seen = set()
        for link in document[0].iter(f"{{{LINK}}}{kind}Link"):
            role = link.get(f"{{{XLINK}}}role", "")
            locators = {node.get(f"{{{XLINK}}}label"): href_concept(node.get(f"{{{XLINK}}}href", ""))
                        for node in link.findall(f"{{{LINK}}}loc")}
            for arc in link.findall(f"{{{LINK}}}{kind}Arc"):
                if arc.get("use") == "prohibited":
                    continue
                parent = locators.get(arc.get(f"{{{XLINK}}}from"))
                child = locators.get(arc.get(f"{{{XLINK}}}to"))
                if not parent or not child:
                    result["section_errors"].append(f"{kind}: unresolved arc locator in {role}")
                    continue
                row = {"role": role, "parent": parent, "child": child,
                       "order": _decimal(arc.get("order"), Decimal(1))}
                if kind == "calculation":
                    weight = _decimal(arc.get("weight"))
                    if weight is None:
                        result["section_errors"].append(f"calculation: invalid weight for {parent} -> {child}")
                        continue
                    row["weight"] = weight
                if kind == "definition":
                    row["arcrole"] = arc.get(f"{{{XLINK}}}arcrole", "")
                    row["target_role"] = arc.get(f"{{{XBRLDT}}}targetRole")
                if kind == "presentation":
                    row["preferred_label"] = arc.get("preferredLabel")
                key = tuple(row.items())
                if key not in seen:
                    result[kind].append(row)
                    seen.add(key)
    return result
