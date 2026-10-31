"""Generic public DQC rule metadata, independent of competition cases.

The bundled register uses the reference processor's official per-taxonomy CSVs,
pinned to commit 64abefd357c34ef45f9f4336a1e3171984d76601. Exact source URLs,
counts and hashes are in dqc_nonnegative.json. Namespace URIs select the
taxonomy year; prefix-only QNames default to the published 2026 registry.

Exclusions follow the public workbook plus explicit additions in the 2026
constant-us-gaap.xule. This small helper does not implement form applicability,
taxonomy loading, or the complete DQC processor. A True result says the generic
nonnegative constraint applies, not that a particular fact is erroneous.

0117 reference (axis names are matched by local name in the reference rule):
https://github.com/DataQualityCommittee/dqc_us_rules/blob/
64abefd357c34ef45f9f4336a1e3171984d76601/dqc_us_rules/source/us/2023/DQC_0117.xule
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from functools import lru_cache
from pathlib import Path


ROLLUP_AXES = frozenset({
    "StatementClassOfStockAxis",
    "ProductOrServiceAxis",
    "PropertyPlantAndEquipmentByTypeAxis",
    "LongtermDebtTypeAxis",
    "RelatedPartyTransactionsByRelatedPartyAxis",
    "StatementBusinessSegmentsAxis",
    "LimitedPartnersCapitalAccountByClassAxis",
    "PartnerTypeOfPartnersCapitalAccountAxis",
    "FiniteLivedIntangibleAssetsByMajorClassAxis",
    "FinancialInstrumentAxis",
    "InformationByCategoryOfDebtSecurityAxis",
})


def local_name(name: str) -> str:
    """Accept a QName using a prefix or ElementTree's Clark notation."""
    return str(name).rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _canonical_name(name: str, namespace: str | None = None) -> str:
    text = str(name)
    if text.startswith("{") and "}" in text:
        namespace, text = text[1:].split("}", 1)
    if namespace:
        fasb = re.match(r"^https?://fasb\.org/(us-gaap-ebp|us-gaap|srt)/", namespace)
        if fasb:
            prefix = fasb.group(1)
        elif re.match(r"^https?://xbrl\.sec\.gov/dei/", namespace):
            prefix = "dei"
        else:
            prefix = "extension"
        return prefix + ":" + local_name(text)
    if ":" in text:
        return text
    return "us-gaap:" + text


@lru_cache(maxsize=1)
def rule_metadata() -> dict:
    """Load only bundled generic metadata; this function never uses network."""
    return json.loads(Path(__file__).with_name("dqc_nonnegative.json").read_text())


@lru_cache(maxsize=None)
def _registry(prefix: str, taxonomy_year: int | None) -> dict[str, str]:
    metadata = rule_metadata()
    tables = metadata["registries"].get(prefix, {})
    year = str(taxonomy_year or metadata["default_taxonomy_year"])
    if prefix == "dei":
        year = "all"
    elif prefix == "srt" and taxonomy_year is not None and taxonomy_year < 2023:
        year = "legacy"
    if year not in tables:
        return {}
    rules = {k: v for k, v in metadata["concept_rule_ids"].items()
             if k.startswith(prefix + ":")}
    for name in tables[year]["removed"]:
        rules.pop(name, None)
    rules.update(tables[year]["added_or_changed"])
    return rules


def _taxonomy_year(concept: str, namespace: str | None, year: int | None) -> int | None:
    if year is not None:
        return int(year)
    if str(concept).startswith("{"):
        namespace = str(concept)[1:].split("}", 1)[0]
    match = re.search(r"/(20\d{2})(?:-|/|$)", namespace or "")
    return int(match.group(1)) if match else None


@lru_cache(maxsize=None)
def nonnegative_concepts(taxonomy_year: int | None = None) -> frozenset[str]:
    """Registered concepts for a taxonomy year; default is the 2026 release."""
    return frozenset(name for prefix in rule_metadata()["registries"]
                     for name in _registry(prefix, taxonomy_year))


def nonnegative_rule_id(
    concept: str, namespace: str | None = None, *, taxonomy_year: int | None = None,
) -> str | None:
    """Return the suffix identifier from the applicable public CSV registry.

    Unknown/unreleased taxonomy years have no applicable registry. An explicit
    year overrides namespace inference. SRT years before 2023 use the official
    unversioned legacy CSV, whose finer historical applicability is unspecified.
    """
    name = _canonical_name(concept, namespace)
    prefix = name.split(":", 1)[0]
    year = _taxonomy_year(concept, namespace, taxonomy_year)
    return _registry(prefix, year).get(name)


def nonnegative_applicable(
    concept: str,
    context_dimensions: Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
    *,
    namespace: str | None = None,
    taxonomy_year: int | None = None,
) -> bool:
    """Check generic DQC 0015 concept membership and dimensional exemptions.

    Dimensions are explicit axis/member QName pairs. The caller must preserve
    their namespaces. Bare names are interpreted as US GAAP; prefixed extension
    names do not accidentally match standard taxonomy concepts. For a custom
    prefix on the concept, pass its namespace URI. Typed dimensions should be
    handled by the caller rather than converted into arbitrary member strings.
    Year selection affects concept membership; exclusions are those of the
    pinned published rule package, not reconstructed historical rule releases.
    """
    if nonnegative_rule_id(concept, namespace, taxonomy_year=taxonomy_year) is None:
        return False
    if not context_dimensions:
        return True

    items = (
        context_dimensions.items()
        if isinstance(context_dimensions, Mapping)
        else context_dimensions
    )
    exclusions = rule_metadata()["exclusions"]
    for axis, member in items:
        axis_name = _canonical_name(axis)
        member_name = _canonical_name(member)
        member_local = local_name(member).lower()
        if any(part in member_local for part in exclusions["member_substrings"]):
            return False
        if member_name in exclusions["members"]:
            return False
        if axis_name in exclusions["axes"]:
            return False
        if (
            local_name(axis) == "IncomeTaxEffectChangeInLawAxis"
            and not axis_name.startswith(("us-gaap:", "srt:", "dei:"))
        ):
            return False
        if member_name in exclusions["axis_members"].get(axis_name, ()):
            return False
    return True
