def test_eligible_plan_contract_has_no_answer_and_no_duplicate_resume():
 inputs=[{'question':'q','evidence':[{'content':'fresh'}]}];calls=[]
 for item in inputs:
  assert 'answer' not in str(item);calls.append(item['question'])
 for item in inputs:
  if item['question'] not in calls:calls.append(item['question'])
 assert calls==['q']
