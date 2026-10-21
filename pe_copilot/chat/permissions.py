"""Synthetic resource policy for the interview; not a production identity provider."""
from dataclasses import dataclass


@dataclass(frozen=True)
class DemoPermissionPolicy:
    principal: str = 'demo-public-reader'

    def authorize(self, resource_id):
        # Both identities and classifications are application-owned, never supplied by an LLM.
        classification={'synthetic:public-filing':'public','synthetic:treasury-memo':'restricted'}.get(resource_id)
        allowed=self.principal=='demo-public-reader' and classification=='public'
        return {'principal':self.principal,'resource_id':resource_id,'classification':classification or 'unknown',
                'decision':'allow' if allowed else 'deny','synthetic':True,
                'reason':'Public evidence is permitted.' if allowed else 'No grant for this resource; stop before retrieval.'}
