"""Create an isolated QA workspace, never insert demo data into a real installation."""
import argparse
import io
import subprocess
import sys
from pathlib import Path
from damask_chief_of_staff.config import Config
from damask_chief_of_staff.store import Store
from damask_chief_of_staff.engine import Engine
from damask_chief_of_staff.adapters import connect, session_id
from damask_chief_of_staff.hook import handle
from damask_chief_of_staff.checks import register, run

parser=argparse.ArgumentParser()
parser.add_argument('--directory',required=True)
args=parser.parse_args()
base=Path(args.directory).resolve()
if base.exists():
    raise SystemExit('Choose a new empty fixture directory')
root=base/'fixture-workspace'
root.mkdir(parents=True)
subprocess.run(['git','init','-q',str(root)],check=True)
(root/'sort.py').write_text('def order(values):\n    return values\n')
config=Config(base/'state');config.data['fixture_mode']=True;config.save()
store=Store(config);engine=Engine(store)
p=connect(store,str(root),capture_text=True,install=False)
def event(native,kind,**fields):
    handle(config,p['id'],{'cwd':str(root),'session_id':native,'hook_event_name':kind,**fields},io.StringIO())
    engine.drain()
event('fixture-sort','SessionStart',source='startup')
event('fixture-sort','UserPromptSubmit',prompt='Fix dashboard sorting so rows stay in ascending order. Do not edit authentication.')
event('fixture-sort','PostToolUse',tool_name='Read',tool_input={'file_path':'sort.py'})
event('fixture-sort','PostToolUse',tool_name='Edit',tool_input={'file_path':'sort.py'})
event('fixture-sort','Stop',last_assistant_message='Sorting implementation updated; ready for review.')
sid=session_id(p['id'],'fixture-sort')
check=register(store,p['id'],'Ascending-order regression',[sys.executable,'-c','from sort import order; assert order([3,1,2]) == [1,2,3]'])
run(store,sid,check['id'])
engine.summarize(sid)
event('fixture-docs','SessionStart',source='startup')
event('fixture-docs','UserPromptSubmit',prompt='Improve the quickstart examples and document the new CLI flags.')
event('fixture-docs','PostToolUse',tool_name='Read',tool_input={'file_path':'README.md'})
event('fixture-docs','PreCompact')
engine.summarize(session_id(p['id'],'fixture-docs'))
print('Created isolated fixture:',base)
