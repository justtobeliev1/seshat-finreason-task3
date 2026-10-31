"""Small synthetic parser cases; no competition answers are used."""
from decimal import Decimal
import unittest

from participant.xbrl import parse_query


def query(instance="", calculation="", schema="", definition=""):
    headers = {
        "Schema document": schema or '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"/>',
        "Instance document": instance or '<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"/>',
        "Calculation linkbase document": calculation or '<link:linkbase xmlns:link="http://www.xbrl.org/2003/linkbase"/>',
        "Definition linkbase document": definition or '<link:linkbase xmlns:link="http://www.xbrl.org/2003/linkbase"/>',
        "Presentation linkbase document": '<link:linkbase xmlns:link="http://www.xbrl.org/2003/linkbase"/>',
        "US GAAP Taxonomy": '[Concept Core]\nID: t:Total\nType: monetary | Balance: debit | PeriodType: duration',
    }
    return "\n".join(f"##{name}\n{body}\n" for name, body in headers.items()) + (
        "\nQuestion1: What's the reported value of t:Total in the Instance document "
        "for the period 2025-01-01 to 2025-12-31?\n")


class ParserTests(unittest.TestCase):
    def test_precise_values_and_distinct_dimensions(self):
        instance = '''<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"
          xmlns:d="http://xbrl.org/2006/xbrldi" xmlns:t="urn:test">
          <xbrli:context id="plain"><xbrli:entity><xbrli:identifier scheme="s">E</xbrli:identifier></xbrli:entity>
            <xbrli:period><xbrli:instant>2025-12-31</xbrli:instant></xbrli:period></xbrli:context>
          <xbrli:context id="one"><xbrli:entity><xbrli:segment><d:typedMember dimension="t:Axis"><t:Domain>A</t:Domain></d:typedMember></xbrli:segment></xbrli:entity></xbrli:context>
          <xbrli:context id="two"><xbrli:entity><xbrli:segment><d:typedMember dimension="t:Axis"><t:Domain>B</t:Domain></d:typedMember><d:explicitMember dimension="t:OtherAxis">t:Member</d:explicitMember></xbrli:segment></xbrli:entity></xbrli:context>
          <t:Total contextRef="plain" unitRef="USD" decimals="2">9007199254740993.01</t:Total>
          <t:Text contextRef="plain">not numeric</t:Text></xbrli:xbrl>'''
        parsed = parse_query(query(instance=instance))
        self.assertEqual(parsed["facts"][0]["value"], Decimal("9007199254740993.01"))
        self.assertEqual(parsed["facts"][0]["raw"], "9007199254740993.01")
        self.assertEqual(parsed["contexts"]["plain"]["instant"], "2025-12-31")
        self.assertNotEqual(parsed["contexts"]["one"]["dimensions"], parsed["contexts"]["two"]["dimensions"])
        self.assertEqual(parsed["contexts"]["two"]["dimensions"]["t:OtherAxis"], "t:Member")
        self.assertEqual(parsed["target"], {"concept": "t:Total", "start": "2025-01-01", "end": "2025-12-31"})
        self.assertEqual(parsed["balance"]["t:Total"], "debit")

    def test_locators_use_href_and_roles_remain_separate(self):
        schema = '''<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:t="urn:test" targetNamespace="urn:test">
          <xs:element id="opaque-id" name="Total"/></xs:schema>'''
        arc = '<link:calculationArc xlink:from="misleading" xlink:to="arbitrary" order="2" weight="-1"/>'
        link = '''<link:calculationLink xlink:role="ROLE"><link:loc xlink:label="misleading" xlink:href="a.xsd#opaque-id"/>
          <link:loc xlink:label="arbitrary" xlink:href="a.xsd#t_Part"/>ARC</link:calculationLink>'''.replace('ARC', arc + arc)
        calculation = '<link:linkbase xmlns:link="http://www.xbrl.org/2003/linkbase" xmlns:xlink="http://www.w3.org/1999/xlink">' + link.replace('ROLE', 'r1') + link.replace('ROLE', 'r2') + '</link:linkbase>'
        parsed = parse_query(query(schema=schema, calculation=calculation))
        self.assertEqual(len(parsed["calculation"]), 2)
        self.assertEqual(parsed["calculation"][0], {"role": "r1", "parent": "t:Total", "child": "t:Part", "order": Decimal(2), "weight": Decimal(-1)})

    def test_definition_arcrole_and_target_role(self):
        definition = '''<link:linkbase xmlns:link="http://www.xbrl.org/2003/linkbase" xmlns:xlink="http://www.w3.org/1999/xlink" xmlns:xbrldt="http://xbrl.org/2005/xbrldt" xmlns:t="urn:test">
          <link:definitionLink xlink:role="r"><link:loc xlink:label="a" xlink:href="#t_Axis"/><link:loc xlink:label="b" xlink:href="#t_Default"/>
          <link:definitionArc xlink:from="a" xlink:to="b" xlink:arcrole="dimension-default" xbrldt:targetRole="next"/></link:definitionLink></link:linkbase>'''
        parsed = parse_query(query(definition=definition))
        self.assertEqual(parsed["definition"][0]["target_role"], "next")
        self.assertEqual(parsed["definition"][0]["arcrole"], "dimension-default")

    def test_malformed_section_is_recorded(self):
        parsed = parse_query(query(instance="<broken>"))
        self.assertTrue(any("Instance document" in error for error in parsed["section_errors"]))
        self.assertEqual(parsed["facts"], [])

    def test_instant_question(self):
        parsed = parse_query(query().replace("2025-01-01 to 2025-12-31", "2025-12-31"))
        self.assertIsNone(parsed["target"]["start"])
        self.assertEqual(parsed["target"]["end"], "2025-12-31")
        self.assertEqual(parsed["target"]["instant"], "2025-12-31")

    def test_truncated_instance_keeps_complete_facts_and_contexts(self):
        instance = '''<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:t="urn:test">
          <xbrli:context id="complete"><xbrli:entity><xbrli:identifier>E</xbrli:identifier></xbrli:entity>
          <xbrli:period><xbrli:instant>2025-12-31</xbrli:instant></xbrli:period></xbrli:context>
          <t:Total contextRef="complete" decimals="INF">123.456</t:Total>
          <t:Other contextRef="complete" decimals="INF">789'''
        parsed = parse_query(query(instance=instance))
        self.assertEqual(list(parsed["contexts"]), ["complete"])
        self.assertEqual([(f["concept"], f["value"]) for f in parsed["facts"]],
                         [("t:Total", Decimal("123.456"))])
        self.assertEqual(parsed["partial_sections"], ["Instance document"])
        self.assertTrue(any("partial XML" in error for error in parsed["section_errors"]))

    def test_truncated_context_is_not_treated_as_complete(self):
        instance = '''<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance">
          <xbrli:context id="partial"><xbrli:period><xbrli:instant>2025-12-31</xbrli:instant>'''
        parsed = parse_query(query(instance=instance))
        self.assertEqual(parsed["contexts"], {})

    def test_truncated_link_keeps_resolvable_complete_arcs(self):
        calculation = '''<link:linkbase xmlns:link="http://www.xbrl.org/2003/linkbase"
          xmlns:xlink="http://www.w3.org/1999/xlink" xmlns:t="urn:test">
          <link:calculationLink xlink:role="r">
          <link:loc xlink:label="a" xlink:href="#t_Total"/>
          <link:loc xlink:label="b" xlink:href="#t_Part"/>
          <link:calculationArc xlink:from="a" xlink:to="b" weight="1"/>
          <link:calculationArc xlink:from="a" xlink:to="'''
        parsed = parse_query(query(calculation=calculation))
        self.assertEqual(len(parsed["calculation"]), 1)
        self.assertEqual(parsed["calculation"][0]["child"], "t:Part")
        self.assertEqual(parsed["partial_sections"], ["Calculation linkbase document"])

    def test_interior_xml_error_is_not_recovered(self):
        instance = '''<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:t="urn:test">
          <t:Total contextRef="c">17</t:Other></xbrli:xbrl>'''
        parsed = parse_query(query(instance=instance))
        self.assertEqual(parsed["facts"], [])
        self.assertEqual(parsed["partial_sections"], [])
        self.assertTrue(any("mismatched tag" in error for error in parsed["section_errors"]))

    def test_absent_schema_placeholder_does_not_discard_instance(self):
        parsed = parse_query(query(schema="None"))
        self.assertEqual(parsed["section_errors"], ["Schema document: not provided"])
        self.assertEqual(parsed["role_definitions"], {})

    def test_schema_role_definitions_preserve_raw_statement_and_disclosure_metadata(self):
        schema = '''<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"
          xmlns:link="http://www.xbrl.org/2003/linkbase"><xs:annotation><xs:appinfo>
          <link:roleType roleURI="urn:MisleadingStatementName">
            <link:definition> 9955587 - Disclosure - Changes &amp; Reclassifications </link:definition>
          </link:roleType>
          <link:roleType roleURI="urn:MisleadingDisclosureName">
            <link:definition>9952151 - Statement - BALANCE SHEETS</link:definition>
          </link:roleType>
          <link:roleType roleURI="urn:StatementWithoutMetadata"/>
          </xs:appinfo></xs:annotation></xs:schema>'''
        parsed = parse_query(query(schema=schema))
        self.assertEqual(parsed["role_definitions"], {
            "urn:MisleadingStatementName": " 9955587 - Disclosure - Changes & Reclassifications ",
            "urn:MisleadingDisclosureName": "9952151 - Statement - BALANCE SHEETS"})
        self.assertIsNone(parsed["role_definitions"].get("urn:StatementWithoutMetadata"))


if __name__ == "__main__":
    unittest.main()
