"""Create an isolated source/test tree without copying credentials or live state."""
from pathlib import Path
import shutil
from r3.state import ROOT

if __name__=='__main__':
    base=ROOT/'.market/system-upgrade-20261110'
    target=base/'repo/submissions/26510121/week-08'
    target.mkdir(parents=True,exist_ok=False)
    for folder in ('r3','tests'):
        for source in (ROOT/folder).rglob('*.py'):
            destination=target/source.relative_to(ROOT)
            destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,destination)
    for source in ROOT.glob('*.py'):shutil.copy2(source,target/source.name)
    for name in ('policy.json','agent.json'):shutil.copy2(ROOT/name,target/name)
    scripts=base/'repo/scripts';scripts.mkdir(parents=True)
    shutil.copy2(ROOT.parents[2]/'scripts/market_kit.py',scripts/'market_kit.py')
    print(target)
