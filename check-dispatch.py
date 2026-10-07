import os, sys, json
from pathlib import Path
from cryptography.fernet import Fernet
os.environ.setdefault('ENCRYPTION_KEY',Fernet.generate_key().decode())
sys.path.insert(0,str(Path.cwd()/'backend'))
from app.tools.helper import resolve_tool_call
from examples import EXAMPLES, example_content
for name, example in EXAMPLES.items():
 for action in ['validate','render']:
  generator=resolve_tool_call(db=None, tool_name='create_visualization', tool_arguments={'action':action,'title':example['title'],'content':example_content(name),'summary':example['summary']}, user_id='proof',group_id=None,project_id=None,_skip_rate_limit=True)
  events=[]
  while True:
   try: events.append(json.loads(next(generator)))
   except StopIteration as completed:
    result=completed.value
    break
  assert (len(events)>0)==(action=='render')
  if events: assert events[0]['widget_type']=='visualization'
  print(name, action, 'PASS', len(example_content(name).encode()),'bytes')
