# ADR-0007: Low-cost real-provider acceptance

Status: Accepted

Real acceptance uses exactly ten business-reviewed and HMAC-signed cases covering the eight required query
types. Performance observations use 1, 5 and 20 chunk generations and are explicitly low-confidence,
non-SLO baselines.

All Embedding, Reranker, Generator and Verifier requests share an atomic budget ledger capped at 60 calls,
200,000 input tokens and 20,000 output tokens. Acceptance transports use zero automatic retries. The three
indirect prompt-injection cases combine attacks into one PDF, one DOCX and one OCR image to control cost.

Production retains local original/artifact files and is therefore explicitly restricted to a single
instance. MySQL owns control state and Redis owns queue, lease and cache state.
