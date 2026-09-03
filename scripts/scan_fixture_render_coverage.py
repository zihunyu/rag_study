"""Content-free dry-run fixture source/render glyph coverage scanner."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
from ragkb.evaluation.fixture_glyph_coverage import glyph_coverage
def scan(pairs):
    records=[]
    for pair in pairs:
        source=Path(pair['source']); rendered=Path(pair['rendered'])
        text=source.read_text(encoding='utf-8')
        rendered_text=rendered.read_text(encoding='utf-8')
        coverage=glyph_coverage(text,pair['supported_codepoints'])
        records.append({'source_ref':str(source),'render_ref':str(rendered),'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'render_sha256':hashlib.sha256(rendered.read_bytes()).hexdigest(),'flagged':coverage['covered'] is False or text!=rendered_text})
    return {'revision':'fixture-render-coverage:v1','dry_run':True,'fixture_count':len(records),'flagged_count':sum(r['flagged'] for r in records),'records':records,'provider_call_count':0}
def main():
    p=argparse.ArgumentParser();p.add_argument('--dry-run',action='store_true');p.add_argument('pairs_json',type=Path,nargs='?');p.add_argument('--manifest',type=Path);p.add_argument('--output',type=Path);a=p.parse_args()
    if not a.dry_run: raise SystemExit('DRY_RUN_REQUIRED')
    if a.manifest:
        from ragkb.evaluation.fixture_manifest_scan import scan_fixture_manifest
        report=scan_fixture_manifest(ROOT,a.manifest)
    elif a.pairs_json:
        report=scan(json.loads(a.pairs_json.read_text(encoding='utf-8')))
    else: raise SystemExit('MANIFEST_OR_PAIRS_REQUIRED')
    if a.output: a.output.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    print(json.dumps(report,sort_keys=True))
if __name__=='__main__': main()
