import pathlib,sys,tempfile,subprocess,shutil
sys.path.insert(0,str(pathlib.Path.cwd()))
import meta_harness as mh
root=pathlib.Path.cwd(); scratch=tempfile.mkdtemp(prefix='astra-1110-021612-')
try:
 copy=mh.test_copy(scratch)
 env={k:v for k,v in mh.test_env().items() if k not in mh.TEST_COPY_ENV}
 with (root/'meta/evidence/astra-1110-021612-tests.txt').open('w') as log:
  log.write('command: '+repr(mh.test_command())+'\nworking tree copied with meta_harness.test_copy; live state excluded\n');log.flush()
  p=subprocess.run(mh.test_command(),cwd=copy,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=900)
  log.write('\nexit_code='+str(p.returncode)+'\n')
 print('exit_code='+str(p.returncode))
finally:shutil.rmtree(scratch,ignore_errors=True)
