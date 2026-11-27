from control import ssh
code="""import json,subprocess
from pathlib import Path
r=Path('/home/comalswjd/ai-agent-engineering-101')
rel='submissions/25512085/week-08/'
# Only tracked public command configuration; never runtime environment or launcher auth.
a=json.loads(subprocess.check_output(['git','show','HEAD:'+rel+'agent.json'],cwd=r))
result={k:{'restock_py':'restock.py' in a.get(k,{}).get('cmd',''),'review_py':'review.py' in a.get(k,{}).get('cmd',''),'shell_loop':any(x in a.get(k,{}).get('cmd','') for x in ('while ','for '))} for k in ('restock','review')}
assert result['restock']['restock_py'] and not result['restock']['shell_loop']
print(json.dumps(result))
"""
print(ssh(code,'load-flow').decode())
