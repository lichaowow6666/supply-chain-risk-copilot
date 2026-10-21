"""Explicit registries; registration never silently overwrites an identity."""
from dataclasses import asdict
import json
from .schema import Company, SourceDocument


class Registry:
    def __init__(self):
        self._companies = {}
        self._documents = {}

    def register_company(self, company):
        if not isinstance(company, Company):
            raise ValueError('expected Company')
        self._register(self._companies, company.company_id, company)
        return company.company_id

    def register_document(self, document):
        if not isinstance(document, SourceDocument):
            raise ValueError('expected SourceDocument')
        if document.company_id not in self._companies:
            raise ValueError('register company before document')
        self._register(self._documents, document.document_id, document)
        return document.document_id

    @staticmethod
    def _register(records, key, value):
        if key in records and records[key] != value:
            raise ValueError('conflicting registration; existing record preserved')
        records[key] = value

    def get_company(self, company_id):
        return self._companies[company_id]

    def get_document(self, document_id):
        return self._documents[document_id]

    def validate_snapshot(self, snapshot):
        self.get_company(snapshot.company_id)
        for document_id in snapshot.document_ids:
            if self.get_document(document_id).company_id != snapshot.company_id:
                raise ValueError('cross-company document')

    def to_json(self):
        return json.dumps({'schema_version': 1,
                           'companies': [asdict(self._companies[k]) for k in sorted(self._companies)],
                           'documents': [asdict(self._documents[k]) for k in sorted(self._documents)]}, sort_keys=True, indent=2)

    @classmethod
    def from_json(cls, payload):
        data = json.loads(payload)
        if data['schema_version'] != 1:
            raise ValueError('unsupported registry schema')
        result = cls()
        for company in data['companies']:
            result.register_company(Company(**company))
        for document in data['documents']:
            result.register_document(SourceDocument(**document))
        return result
