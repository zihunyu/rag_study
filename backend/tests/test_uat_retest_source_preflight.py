from pathlib import Path
import json,subprocess,sys
def test_generated_preflight_cli(tmp_path:Path):
 review=tmp_path/'r';review.write_text(json.dumps({'type':'candidate_review','audit_verdict':'不通过','candidate_id':'a'})+'\n')
 scan=tmp_path/'s';scan.write_text(json.dumps({'records':[{'fixture_ref':'x','representation_status':'AVAILABLE'}]}))
 cases=tmp_path/'c';cases.write_text(json.dumps({'a':{'fixture_ref':'x','source_integrity':True,'question_sha256':'h'}}))
 out=tmp_path/'o';script=Path(__file__).resolve().parents[2]/'scripts/prepare_uat_retest_sources_v5.py';subprocess.run([sys.executable,str(script),str(review),str(scan),str(cases),'--output',str(out)],check=True);assert json.loads(out.read_text())['eligible_count']==1
