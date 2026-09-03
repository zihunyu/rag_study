from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
from plan_fixture_regeneration import plan
def test_generated_scan_plans_only_dynamic_nonavailable_records():
 report=plan({'records':[{'fixture_ref':'a','source_sha256':'x','representation_status':'AVAILABLE','glyph_render_defect':True},{'fixture_ref':'b','source_sha256':'y','representation_status':'UNAVAILABLE_FAIL_CLOSED'},{'fixture_ref':'c','source_sha256':'z','representation_status':'DEFERRED_BY_USER'}]})
 assert report['rebuild_count']==1 and report['blocked_count']==1 and report['records'][0]['fixture_ref']=='a' and report['blocked_records'][0]['fixture_ref']=='b' and report['provider_call_count']==0
