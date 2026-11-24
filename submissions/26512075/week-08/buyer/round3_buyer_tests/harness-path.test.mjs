import {test} from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {resolve} from 'node:path';
import {tmpdir} from 'node:os';

test('real TypeScript bridge locates Python harness from an unrelated working directory', () => {
  const root=fileURLToPath(new URL('../../',import.meta.url));
  const result=spawnSync(process.execPath,['--import',resolve(root,'node_modules/tsx/dist/loader.mjs'),
    resolve(root,'buyer/src/bridge-smoke.ts')],{cwd:tmpdir(),encoding:'utf8',
    env:{...process.env,DECISION_MODULE:'harness.example_decisions',PYTHONDONTWRITEBYTECODE:'1'}});
  assert.ifError(result.error);
  assert.equal(result.status,0,result.stderr);
  assert.deepEqual(JSON.parse(result.stdout),{action:'offer',price:100});
});
