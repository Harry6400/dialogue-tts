"""Kill isolated offline worker processes at exact persistence boundaries.

No private input, installed application, online synthesis or updater is used.
The child exits with os._exit: Python shutdown/finally handlers cannot run.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from native_workspace import NativeWorkspaceAccess
from web_dialogue.jobs import Jobs
from web_dialogue.normalize import InputError
from web_dialogue.workspaces import Workspaces


class OfflineJobs(Jobs):
    def start(self,*args,**kwargs):raise AssertionError('Online workers are forbidden in offline verification')


def wait_idle(work,ident):
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        summary=work.summary(ident)
        if work.active is None and summary['status']!='scanning':return summary
        time.sleep(.02)
    raise AssertionError('Offline recovery did not settle')


def child(root,phase):
    source,output,state=[root/name for name in ('input','output','state')]
    for folder in (source,output,state):folder.mkdir(mode=0o700)
    for role,suffix in (('AGENT','0'),('USER','1')):
        (source/f'000123{suffix}.xml').write_text(f'<analyze><subject><function><text>虚构崩溃边界验收。</text><time>0,1000</time></function></subject><callInfo><callReference>fictional-crash</callReference><role>{role}</role></callInfo></analyze>')
    access=NativeWorkspaceAccess(state)
    source_cap=access.register(source,'input');target=access.register(output,'output')
    if phase=='worker_source' or phase.startswith('timing_'):
        jobs=Jobs(state_dir=state)
        original_spawn=subprocess.Popen
        def fixture_spawn(command,**kwargs):
            if 'web_dialogue.worker' not in command and '--worker' not in command:
                return original_spawn(command,**kwargs)
            fixture=('import json,pathlib,shutil,sys;folder=pathlib.Path(sys.argv[1]);'
                     'shutil.copyfile(sys.argv[2],folder/"dialogue.mp3");'
                     '(folder/"timeline.json").write_text("[]");'
                     '(folder/"worker_status.json").write_text(json.dumps(dict(status="done",completed=1,total=1)))')
            if phase.startswith('timing_'):
                fixture = """import pathlib,json,sys,hashlib
from web_dialogue.mp3_frames import join_segments
folder=pathlib.Path(sys.argv[1]);script=json.loads((folder/'normalized.json').read_text());lines=script['lines']
clip=pathlib.Path(sys.argv[2]).read_bytes()
audio,spans,duration=join_segments([clip]*len(lines),[line['pause_after_ms'] for line in lines],clip)
(folder/'dialogue.mp3').write_bytes(audio)
(folder/'timeline.json').write_text(json.dumps({'audio_sha256':hashlib.sha256(audio).hexdigest(),'duration_ms':duration,'segments':[{'id':line['id'],'start_ms':span[0],'end_ms':span[1]} for line,span in zip(lines,spans)]}))
(folder/'worker_status.json').write_text(json.dumps({'status':'done','completed':len(lines),'total':len(lines)}))
"""
            return original_spawn([sys.executable,'-c',fixture,command[-1],str(ROOT/'web_dialogue/silence.mp3')],**kwargs)
        subprocess.Popen=fixture_spawn
        open_result=jobs.open_result
        def crash_verified_source(*args,**kwargs):
            lease=open_result(*args,**kwargs)
            assert lease.size>0 and lease.sha256
            os._exit(75)
        if phase=='worker_source':jobs.open_result=crash_verified_source
        work=Workspaces(state,jobs,access)
    else:
        jobs=OfflineJobs()
        work=Workspaces(state,jobs,access,synthesizer=lambda preview:ROOT/'web_dialogue/silence.mp3')
    ident=work.import_folder(source_cap['folder_id'])['id']
    assert wait_idle(work,ident)['ready']==1
    selected=work.select(ident,{'mode':'all'})
    item=work.page(ident)['items'][0]
    (root/'checkpoint.json').write_text(json.dumps({'workspace':ident,'item':item['id'],'input_id':source_cap['folder_id'],'output_id':target['folder_id']}))
    if phase=='timing_publish':
        work._after_cache_publish=lambda *_:os._exit(76)
    elif phase=='timing_commit':
        store=work._store_result_metadata
        def crash_timing(db,*args):
            store(db,*args);db.commit();os._exit(77)
        work._store_result_metadata=crash_timing
    elif phase=='timing_receipt':
        access._after_publish=lambda:os._exit(78)
    elif phase=='cache_publish':
        replace=os.replace
        def crash_replace(source,destination,*args,**kwargs):
            result=replace(source,destination,*args,**kwargs)
            if Path(destination).parent==work.cache and Path(destination).suffix=='.mp3':os._exit(71)
            return result
        os.replace=crash_replace
    elif phase=='partial_cache':
        import builtins
        original_open=builtins.open
        class PartialWriter:
            def __init__(self,handle):self.handle=handle
            def __enter__(self):return self
            def __exit__(self,*args):return self.handle.__exit__(*args)
            def __getattr__(self,name):return getattr(self.handle,name)
            def write(self,data):
                self.handle.write(data[:17]);self.handle.flush();os.fsync(self.handle.fileno());os._exit(72)
        def crash_open(file,*args,**kwargs):
            handle=original_open(file,*args,**kwargs)
            if Path(file).parent==work.cache and Path(file).suffix=='.tmp' and args and args[0]=='wb':return PartialWriter(handle)
            return handle
        builtins.open=crash_open
    elif phase=='output_publish':
        access._after_publish=lambda:os._exit(73)
    elif phase=='receipt_commit':
        unlink=Path.unlink
        def crash_unlink(path,*args,**kwargs):
            if path.parent==work.cache and path.suffix=='.mp3':os._exit(74)
            return unlink(path,*args,**kwargs)
        Path.unlink=crash_unlink
    work.start(ident,{'selection_id':selected['selection_id'],'output_folder_id':target['folder_id'],
                      'consent':True,'request_id':'offline-crash-'+phase})
    wait_idle(work,ident)
    raise AssertionError('Crash boundary was not reached')


def verify(root,phase):
    completed=subprocess.run([sys.executable,'-B',str(Path(__file__).resolve()),'--child',str(root),'--phase',phase],
                             cwd=ROOT,capture_output=True,timeout=30)
    expected={'cache_publish':71,'partial_cache':72,'output_publish':73,'receipt_commit':74,'worker_source':75,'timing_publish':76,'timing_commit':77,'timing_receipt':78}[phase]
    assert completed.returncode==expected,(phase,completed.returncode,completed.stderr.decode())
    checkpoint=json.loads((root/'checkpoint.json').read_text())
    access=NativeWorkspaceAccess(root/'state');calls=[]
    def synthesize(preview):
        calls.append(preview['filename'])
        return ROOT/'web_dialogue/silence.mp3'
    jobs=OfflineJobs(state_dir=root/'state') if phase=='worker_source' or phase.startswith('timing_') else OfflineJobs()
    work=Workspaces(root/'state',jobs,access,synthesizer=synthesize)
    ident=checkpoint['workspace'];cache=work._audio_path(ident,checkpoint['item'])
    try:
        before=work.summary(ident);safety=work.safety()
        assert calls==[],'Restart must never synthesize automatically'
        complete=phase!='partial_cache'
        assert cache.is_file()==complete
        if complete:
            assert safety['unsaved'] is True,(phase,safety)
            try:work.action(ident,'finish',{})
            except InputError:pass
            else:raise AssertionError('Complete audio was not protected from finish')
            assert cache.is_file()
        work.restore(ident)
        wait_idle(work,ident)
        if complete:work.action(ident,'retry',{'kind':'save','consent':True})
        else:work.action(ident,'resume',{'consent':True})
        after=wait_idle(work,ident)
        assert after['saved']==1,after
        assert len(calls)==(0 if complete else 1),calls
        target=root/'output/000123.mp3'
        digest=hashlib.sha256(target.read_bytes()).hexdigest()
        reference=(ROOT/'web_dialogue/silence.mp3').read_bytes()
        if phase.startswith('timing_'):
            from web_dialogue.mp3_frames import join_segments
            reference=join_segments([reference]*2,[250]*2,reference)[0]
            assert work.timeline(ident,checkpoint['item'])['available']
        assert digest==hashlib.sha256(reference).hexdigest()
        assert len(list((root/'output').glob('*.mp3')))==1
        assert access.verify_output(checkpoint['output_id'],'000123.mp3',work.detail(ident,checkpoint['item'])['fingerprint'])['state']=='saved'
        return {'phase':phase,'child_exit_code':expected,'complete_cache_protected':complete,
                'restart_automatic_synthesis':False,'recovery_synthesis_calls':len(calls),
                'saved':1,'output_sha256':digest,'playback_timing_recovered':phase.startswith('timing_'),'passed':True}
    finally:work.close();jobs.close();access.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path)
    parser.add_argument('--child',type=Path)
    parser.add_argument('--phase',choices=('partial_cache','cache_publish','output_publish','receipt_commit','worker_source','timing_publish','timing_commit','timing_receipt'))
    args=parser.parse_args()
    if args.child:
        child(args.child,args.phase)
        return
    if args.report is None:parser.error('--report is required')
    checks=[]
    for phase in ('partial_cache','cache_publish','output_publish','receipt_commit','worker_source','timing_publish','timing_commit','timing_receipt'):
        with tempfile.TemporaryDirectory(prefix='dialogue-crash-'+phase+'-') as raw:
            check=verify(Path(raw),phase);checks.append(check);print(json.dumps(check),flush=True)
    report={'passed':True,'online_synthesis':False,'real_process_abrupt_exit':True,'checks':checks}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':main()
