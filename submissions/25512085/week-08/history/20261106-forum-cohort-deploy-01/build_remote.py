from control import OUT
OLD=OUT.parent/'20261106-gap35-deploy-01'
def put(n,s):
    with (OUT/n).open('x',encoding='utf-8',newline='\n') as f:f.write(s)
put('local_validate.py',(OLD/'local_validate.py').read_text())
s=(OLD/'remote.py').read_text().replace('20261106-gap35-runtime-01','20261106-forum-cohort-runtime-01')
s=s.replace("M['paths'][1]).exists()", "M['paths'][3]).exists()")
s=s.replace("for mode,count in [('focused',137),('all',416)]:", "for mode,count in [('focused',193),('all',426)]:")
start=s.index("    original=REPO/M['paths'][0]")
end=s.index("    subprocess.run(['git','add'",start)
s=s[:start]+'''    tree=OUT/'evidence/sandbox'
    staged={}
    for p in M['paths']:
        original=REPO/p
        assert sha(tree/p)==M['after'][p]
        compile((tree/p).read_bytes(),p,'exec')
        if M['before'][p] is not None:
            with (backup/Path(p).name).open('xb') as f: f.write(original.read_bytes()); f.flush(); os.fsync(f.fileno())
            assert sha(backup/Path(p).name)==M['before'][p]
        tmp=original.with_name('.forum-cohort-'+original.name+'.tmp')
        with tmp.open('xb') as f:
            f.write((tree/p).read_bytes()); f.flush(); os.fsync(f.fileno())
        if original.exists(): assert (tmp.stat().st_mode & 0o777)==(original.stat().st_mode & 0o777)
        staged[p]=tmp
    guard()
    save('pre-atomic.json',dict(utc=utc(),head=git('rev-parse','HEAD'),dirty=[],source_sha256=hashes(M['source_sha256']),processes=snapshot()))
    applied=[]
    # Observer first: strict writer must never precede proof collection.
    for p in M['paths']:
        assert git('rev-parse','HEAD')==M['base']
        if M['before'][p] is not None: assert sha(REPO/p)==M['before'][p]
        else: assert not (REPO/p).exists()
        os.replace(staged[p],REPO/p)
        fd=os.open(str((REPO/p).parent),os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
        applied.append(dict(path=p,utc=utc(),sha256=sha(REPO/p)))
    save('atomic-applied.json',dict(utc=utc(),files=applied,signals_sent=0))
    assert hashes(M['paths'])==M['after']
    for p in M['paths']: compile((REPO/p).read_bytes(),p,'exec')
    unchanged={p:h for p,h in M['source_sha256'].items() if p not in M['paths']}
    assert hashes(unchanged)==unchanged
    assert sorted(git('diff','HEAD','--name-only').splitlines())==sorted(p for p in M['paths'] if M['before'][p] is not None)
'''+s[end:]
a=s.index("    msg='fix(week08):")
b=s.index("    with (OUT/'commit.log')",a)
s=s[:a]+"    msg='fix(week08): backport mature forum cohort proof only\\n\\nBackport-of: '+M['source']+'\\nPreserve monolithic cash pricing, procurement guard, buyer gap and ranking.'\n"+s[b:]
s=s.replace("'backup/market_buyer.py','backup/test_buyer_retry_offline.py'", "'backup/seller_cash_policy.py','backup/forum_writer.py','backup/test_forum_intel_offline.py'")
put('remote.py',s)
