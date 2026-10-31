"""Synthetic checks of the public registry contract; no filing/gold fixtures."""

import unittest

from participant.dqc_rules import (
    ROLLUP_AXES,
    nonnegative_applicable,
    nonnegative_concepts,
    nonnegative_rule_id,
    rule_metadata,
)


class DqcRegistryTests(unittest.TestCase):
    concept = "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment"

    def test_official_current_registry_count_and_provenance(self):
        metadata = rule_metadata()
        self.assertEqual(len(nonnegative_concepts()), 6668)
        self.assertEqual(metadata["default_concept_count"], 6668)
        for source in metadata["sources"]:
            self.assertIn(metadata["source_commit"], source["url"])
            if "concept_count" in source:
                self.assertEqual(len(source["sha256"]), 64)

    def test_reference_csv_is_authoritative_over_workbook_active_flag(self):
        # This public CSV concept is absent from the workbook's Active subset.
        self.assertEqual(nonnegative_rule_id("us-gaap:IncomeTaxReconciliationTaxCredits"), "1307")

    def test_already_qualified_csv_tokens_keep_one_prefix(self):
        metadata = rule_metadata()
        keys = set(metadata["concept_rule_ids"])
        for tables in metadata["registries"].values():
            for delta in tables.values():
                keys.update(delta["removed"])
                keys.update(delta["added_or_changed"])
        self.assertTrue(all(name.count(":") == 1 for name in keys))
        self.assertEqual(metadata["registry_integrity"]["csv_tables_verified"], 21)

    def test_qualified_srt_csv_concept_works_with_prefix_and_uri(self):
        local = "MortgageLoansOnRealEstateCollectionsOfPrincipal"
        self.assertEqual(nonnegative_rule_id("srt:" + local, taxonomy_year=2024), "7633")
        self.assertEqual(nonnegative_rule_id("{http://fasb.org/srt/2024}" + local), "7633")
        self.assertTrue(nonnegative_applicable("srt:" + local))

    def test_qualified_ebp_csv_concept_works_with_prefix_and_uri(self):
        local = "EmployeeBenefitPlanAccruedExpense"
        self.assertEqual(nonnegative_rule_id("us-gaap-ebp:" + local), "10244")
        self.assertEqual(nonnegative_rule_id("{http://fasb.org/us-gaap-ebp/2026}" + local),
                         "10244")

    def test_qualified_dei_csv_concept_works_with_prefix_and_uri(self):
        local = "EntityCommonStockSharesOutstanding"
        self.assertEqual(nonnegative_rule_id("dei:" + local), "699")
        self.assertEqual(nonnegative_rule_id("{http://xbrl.sec.gov/dei/2025}" + local), "699")

    def test_qualified_csv_names_do_not_create_local_name_aliases(self):
        for local in ("MortgageLoansOnRealEstateCollectionsOfPrincipal",
                      "EmployeeBenefitPlanAccruedExpense", "EntityCommonStockSharesOutstanding"):
            self.assertIsNone(nonnegative_rule_id("company:" + local))
            self.assertIsNone(nonnegative_rule_id("{https://example.org/taxonomy/2026}" + local))

    def test_released_taxonomy_membership_changes_are_respected(self):
        concept = "us-gaap:OperatingLeaseRightOfUseAsset"
        self.assertTrue(nonnegative_applicable(concept, taxonomy_year=2026))
        self.assertFalse(nonnegative_applicable(concept, taxonomy_year=2015))

    def test_namespace_uri_selects_its_taxonomy_year(self):
        local = "OperatingLeaseRightOfUseAsset"
        self.assertTrue(nonnegative_applicable("{http://fasb.org/us-gaap/2026}" + local))
        self.assertFalse(nonnegative_applicable("{http://fasb.org/us-gaap/2015-01-31}" + local))
        self.assertFalse(nonnegative_applicable("custom:" + local,
                                              namespace="http://fasb.org/us-gaap/2015-01-31"))

    def test_unreleased_taxonomy_is_not_silently_mapped_to_current(self):
        self.assertFalse(nonnegative_applicable(self.concept, taxonomy_year=2050))

    def test_standard_local_name_in_an_extension_is_not_standard_concept(self):
        local = self.concept.split(":")[1]
        self.assertFalse(nonnegative_applicable("company:" + local))
        self.assertFalse(nonnegative_applicable(local,
                         namespace="http://example.org/fasb.org/us-gaap/2026"))

    def test_balance_direction_does_not_make_every_concept_nonnegative(self):
        self.assertFalse(nonnegative_applicable("us-gaap:NetIncomeLoss"))

    def test_case_insensitive_extension_member_substrings_are_exempt(self):
        self.assertFalse(nonnegative_applicable(self.concept, {
            "company:Axis": "company:ELIMINATIONMember"}))

    def test_named_standard_member_exemption_is_namespace_sensitive(self):
        self.assertFalse(nonnegative_applicable(self.concept, {
            "company:Axis": "us-gaap:AboveMarketLeasesMember"}))
        self.assertTrue(nonnegative_applicable(self.concept, {
            "company:Axis": "company:AboveMarketLeasesMember"}))

    def test_exempt_axis_and_axis_member_combinations(self):
        self.assertFalse(nonnegative_applicable(self.concept, {
            "us-gaap:StatementEquityComponentsAxis": "us-gaap:CommonStockMember"}))
        self.assertFalse(nonnegative_applicable(self.concept, {
            "srt:ConsolidatedEntitiesAxis": "srt:ParentCompanyMember"}))
        self.assertTrue(nonnegative_applicable(self.concept, {
            "srt:ConsolidatedEntitiesAxis": "company:OtherEntityMember"}))

    def test_nonexempt_breakdowns_and_iterable_pairs_are_supported(self):
        self.assertTrue(nonnegative_applicable(self.concept, [
            ("srt:ProductOrServiceAxis", "company:WidgetMember")]))

    def test_extension_law_change_axis_is_exempt(self):
        self.assertFalse(nonnegative_applicable(self.concept, {
            "company:IncomeTaxEffectChangeInLawAxis": "company:LawMember"}))

    def test_rollup_axis_contract_is_independent_of_namespace_prefix(self):
        self.assertIn("ProductOrServiceAxis", ROLLUP_AXES)
        self.assertEqual(len(ROLLUP_AXES), 11)


if __name__ == "__main__":
    unittest.main()
