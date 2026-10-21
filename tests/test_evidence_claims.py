import unittest

from pe_copilot.evidence import EvidenceReference, EvidenceStatus, RelationshipClaim


class RelationshipClaimTests(unittest.TestCase):
    def test_confirmed_claim_requires_evidence(self):
        with self.assertRaisesRegex(ValueError, "confirmed claims require"):
            RelationshipClaim(
                subject="Materion Corporation (MTRN)",
                relation="exposed_to_material",
                object="Copper",
                evidence_status=EvidenceStatus.CONFIRMED,
            )

    def test_confirmed_claim_serializes_provenance(self):
        claim = RelationshipClaim(
            subject="Materion Corporation (MTRN)",
            relation="exposed to material",
            object="Copper",
            evidence_status="confirmed",
            evidence=EvidenceReference(
                source_document="MTRN 10-K",
                evidence_excerpt="The cost of copper can be quite volatile.",
                effective_date="2025-12-31",
            ),
        )
        self.assertEqual(claim.relation, "exposed_to_material")
        self.assertEqual(
            claim.to_dict(),
            {
                "subject": "Materion Corporation (MTRN)",
                "relation": "exposed_to_material",
                "object": "Copper",
                "evidence_status": "confirmed",
                "evidence": {
                    "source_document": "MTRN 10-K",
                    "evidence_excerpt": "The cost of copper can be quite volatile.",
                    "source_url": None,
                    "filing_date": None,
                    "evidence_section": None,
                    "effective_date": "2025-12-31",
                },
                "rationale": None,
            },
        )

    def test_non_confirmed_claim_without_evidence_requires_rationale(self):
        with self.assertRaisesRegex(ValueError, "rationale"):
            RelationshipClaim(
                subject="Copper",
                relation="flows_to",
                object="Scenario supplier pool",
                evidence_status="synthetic",
            )

    def test_evidence_dates_must_be_iso_formatted(self):
        with self.assertRaisesRegex(ValueError, "ISO"):
            EvidenceReference(
                source_document="MTRN 10-K",
                evidence_excerpt="Evidence",
                filing_date="December 31, 2025",
            )


if __name__ == "__main__":
    unittest.main()
