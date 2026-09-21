"""Run from this folder: python capture_run.py baseline (or three-tools).

Capture UTF-8 bytes directly, avoiding PowerShell's native-output decoding.
No credentials are logged. Existing logs and settlement files are preserved.
"""
import datetime
import importlib.metadata
import os
from pathlib import Path
import subprocess
import sys

GOAL = ('Read notes.txt. Calculate the total expense and the equal per-person '
        'share using the listed expenses and participant count. Record the '
        'expense breakdown, calculation, and results in settlement.txt, then '
        'report whether the file was actually saved.')
NO_SAVE_GOAL = ('Read notes.txt. Calculate the total expense and the equal '
                'per-person share using the listed expenses and participant '
                'count. Only report the answer; do not save a file.')


def main():
    os.chdir(Path(__file__).resolve().parent)
    if Path('settlement.txt').exists():
        raise SystemExit('Existing settlement.txt found. Preserve it separately before a fresh comparison.')
    if not os.environ.get('OPENAI_API_KEY', '').strip():
        raise SystemExit('OPENAI_API_KEY is missing in this terminal.')
    label = sys.argv[1] if len(sys.argv) > 1 else 'run'
    if label == 'no-save-ab':
        codes = []
        for variant in ('a', 'b'):
            codes.append(subprocess.run(
                [sys.executable, __file__, 'no-save-' + variant]).returncode)
        return 1 if any(codes) else 0
    if label not in ('baseline', 'three-tools', 'description-b', 'run',
                     'no-save-a', 'no-save-b'):
        raise SystemExit('Unknown run label.')
    no_save = label in ('no-save-a', 'no-save-b')
    goal = NO_SAVE_GOAL if no_save else GOAL
    Path('logs').mkdir(exist_ok=True)
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    target = Path('logs') / f'{label}-{stamp}.txt'
    env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1')
    variant = 'A' if label in ('three-tools', 'no-save-a') else 'B'
    env['WRITE_NOTE_DESCRIPTION'] = variant
    with target.open('xb') as log:
        header = (f'Python: {sys.version}\nopenai: {importlib.metadata.version("openai")}\n'
                  f'Model: {env.get("AGENT_MODEL", "gpt-4o-mini")}\n'
                  f'Description variant: {variant}\n'
                  f'Capture: direct UTF-8 bytes\nGoal: {goal}\n\n')
        log.write(header.encode('utf-8'))
        log.flush()
        result = subprocess.run([sys.executable, 'first_agent.py', goal],
                                env=env, stdout=log, stderr=subprocess.STDOUT)
        log.write(f'\nExit code: {result.returncode}\nsettlement.txt exists: {Path("settlement.txt").exists()}\n'.encode('utf-8'))
        if Path('settlement.txt').exists():
            log.write(b'\n--- actual settlement.txt (UTF-8) ---\n')
            log.write(Path('settlement.txt').read_bytes())
    # Preserve unexpected writes so the next variant starts without a file.
    if no_save and Path('settlement.txt').exists():
        Path('outputs').mkdir(exist_ok=True)
        archived = Path('outputs') / f'{label}-{stamp}-settlement.txt'
        if archived.exists():
            raise SystemExit('Archive collision; settlement.txt left untouched.')
        Path('settlement.txt').rename(archived)
        print(f'Unexpected output preserved: {archived.resolve()}')
    print(f'Log saved: {target.resolve()}')
    print('Open the log as UTF-8 to inspect the complete response and saved file.')
    return result.returncode


if __name__ == '__main__':
    sys.exit(main())
