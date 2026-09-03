from ragkb.evaluation.uat_retest_source_builder import build_retest_source_status
def test_generated_dynamic_selection_blocks_unavailable_sources():
 rows=[{'type':'candidate_review','audit_verdict':'不通过','candidate_id':'a'},{'type':'candidate_review','audit_verdict':'待修订','candidate_id':'b'}]
 cases={'a':{'fixture_ref':'x','source_integrity':True,'question_sha256':'h1'},'b':{'fixture_ref':'y','source_integrity':True,'question_sha256':'h2'}}
 scan={'x':{'representation_status':'AVAILABLE'},'y':{'representation_status':'UNAVAILABLE_FAIL_CLOSED'}}
 out=build_retest_source_status(rows,scan,cases)
 assert [x['state'] for x in out]==['ELIGIBLE','BLOCKED'] and all(x['provider_call_count']==0 for x in out)
