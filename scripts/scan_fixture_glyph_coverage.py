"""Dry-run all fixture metadata through the generic glyph coverage gate."""
from __future__ import annotations
import hashlib, json, sys
from pathlib import Path
import yaml
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend/src'))
from ragkb.evaluation.fixture_rebuild import fixture_rebuild_plan
manifest=yaml.safe_load((ROOT/'backend/tests/fixtures/manifests/format-samples.yaml').read_text(encoding='utf-8'))
records=[]
for item in manifest['collection_plan']:
    meta=yaml.safe_load((ROOT/item['metadata_path']).read_text(encoding='utf-8'))
    for sample in meta['samples']:
        path=ROOT/item['sample_directory']/sample['file']
        records.append({'fixture_id':hashlib.sha256(f"{item['format']}:{sample['id']}".encode()).hexdigest()[:16],'text':'','payload':path.read_bytes(),'supported_codepoints':[],'fixture_ref':str(path.relative_to(ROOT))})
plan=fixture_rebuild_plan(records)
print(json.dumps({'dry_run':True,'fixture_count':len(records),'flagged_count':sum(r['rebuild_required'] for r in plan['records']),'provider_call_count':0},sort_keys=True))
