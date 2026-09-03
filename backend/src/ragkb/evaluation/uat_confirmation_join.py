"""Content-free join between human confirmation rows and prepared input cases."""
from __future__ import annotations
from collections.abc import Mapping,Sequence
def join_confirmation(rows:Sequence[Mapping[str,object]], cases:Sequence[Mapping[str,object]])->list[dict[str,object]]:
 by_key={(c['review_index'],c['source_bundle_sha256']):c for c in cases};out=[]
 for row in rows:
  key=(row['review_index'],row['source_bundle_sha256_from_case']);case=by_key[key]
  out.append({'test_case_id':case['test_case_id'],'source_bundle_sha256':case['source_bundle_sha256'],'fixture_ref':case['fixture_ref'],'category':case['category'],'locator':case['locator'],'source_sha256':case['source_sha256']})
 return out
