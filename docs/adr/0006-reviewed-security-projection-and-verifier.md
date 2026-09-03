# ADR-0006: Reviewed security projection and independent claim verification

Status: Accepted

Unreviewed chunks are indexed only as `RESTRICTED`, classification 3, empty ACL, `STAGED` and
`current_version=false`. Approval freezes an immutable security projection; missing security facts block
publication. Request clearance comes from the verified OIDC principal.

Evidence is serialized as JSON. The generator emits atomic claims with evidence IDs. Deterministic
number/date/unit, URL-domain and credential-request checks run before an independently configured verifier
model. Any unsupported claim prevents `verified=true` and prevents cache insertion.
