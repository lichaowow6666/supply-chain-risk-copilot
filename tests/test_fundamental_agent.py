import unittest

from pe_copilot.agents.fundamental_agent import FundamentalAgent


class FakeCollection:
    def __init__(self):
        self.query_kwargs = None

    def get(self, include):
        return {
            "metadatas": [
                {"ticker": "MTRN", "document_type": "10-K"},
                {"ticker": "IGNORED", "document_type": "10-K"},
            ]
        }

    def query(self, **kwargs):
        self.query_kwargs = kwargs
        return {
            "documents": [["Copper prices can be volatile."]],
            "distances": [[0.2]],
            "metadatas": [[{"ticker": "MTRN", "document_type": "10-Q", "accession": "selected-quarter"}]],
        }


class FundamentalAgentTests(unittest.TestCase):
    def setUp(self):
        self.agent = FundamentalAgent.__new__(FundamentalAgent)
        self.agent.collection = FakeCollection()

    def test_available_tickers_returns_all_database_companies(self):
        self.assertEqual(self.agent.get_available_tickers(), ["IGNORED", "MTRN"])

    def test_query_is_filtered_to_selected_ticker(self):
        result = self.agent.query_10k("commodity risk", company_ticker="mtrn", accession="selected-quarter")
        self.assertEqual(result[0]["ticker"], "MTRN")
        self.assertEqual(result[0]["document_type"], "10-Q")
        self.assertEqual(result[0]["accession"], "selected-quarter")
        self.assertEqual(
            self.agent.collection.query_kwargs["where"],
            {"$and": [{"ticker": "MTRN"}, {"accession": "selected-quarter"}]},
        )

    def test_accession_is_required_even_on_legacy_entry_point(self):
        with self.assertRaisesRegex(ValueError, 'accession'):
            self.agent.query_10k('copper', 'MTRN')
        self.assertIsNone(self.agent.collection.query_kwargs)

    def test_legacy_results_with_wrong_or_missing_scope_are_discarded(self):
        from unittest.mock import Mock
        for metadata in ({'ticker': 'MTRN'},
                         {'ticker': 'MTRN', 'accession': 'old-annual'},
                         {'ticker': 'OTHER', 'accession': 'selected-quarter'}, {}):
            with self.subTest(metadata=metadata):
                self.agent.collection.query = Mock(return_value={
                    'documents': [['Copper is expensive.']], 'distances': [[0.1]],
                    'metadatas': [[metadata]]})
                self.assertEqual(self.agent.query_report('copper', 'MTRN', accession='selected-quarter'), [])

    def test_material_validation_normalizes_plural_and_rejects_group(self):
        chunks = [
            {
                "chunk_id": 1,
                "text": "The cost of copper can be quite volatile.",
                "distance": 0.2,
            },
            {
                "chunk_id": 2,
                "text": "Precious metals are used in production.",
                "distance": 0.3,
            },
        ]
        items = [
            {
                "material": "Coppers",
                "inherent_exposure_level": "High",
                "hedge_level": "Not disclosed in context",
                "explanation": "Copper costs are volatile.",
                "supporting_chunk_ids": [1],
            },
            {
                "material": "Precious Metals",
                "inherent_exposure_level": "High",
                "hedge_level": "Not disclosed in context",
                "explanation": "Group-only result.",
                "supporting_chunk_ids": [2],
            },
        ]

        result = self.agent._validate_material_results("MTRN", items, chunks)

        self.assertEqual([item["material"] for item in result], ["Copper"])
        claim = result[0]["relationship_claims"][0]
        self.assertEqual(claim["object"], "Copper")
        self.assertEqual(claim["evidence_status"], "confirmed")
        self.assertEqual(
            claim["evidence"]["evidence_excerpt"],
            "The cost of copper can be quite volatile.",
        )

    def test_claim_is_rejected_when_cited_excerpt_does_not_name_metal(self):
        chunks = [
            {
                "chunk_id": 1,
                "text": "Precious metals are used in production.",
                "distance": 0.2,
            }
        ]
        items = [{"material": "Gold", "supporting_chunk_ids": [1]}]
        self.assertEqual(
            self.agent._validate_material_results("MTRN", items, chunks),
            [],
        )

    def test_material_fallback_fails_closed(self):
        self.assertEqual(self.agent._fallback_material_synthesis([]), [])


if __name__ == "__main__":
    unittest.main()
