"""Real synthetic-directory capacity check; synthesis is replaced, never online.

Creates two separate runs of role files so pairs cross enumeration boundaries.
All input/output/state live in a temporary private directory and are removed.
"""
import argparse
import json
from pathlib import Path
try:
    import resource
except ImportError:  # Windows has no resource module; functional checks still run.
    resource = None
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from native_workspace import NativeWorkspaceAccess
from web_dialogue.workspaces import Workspaces


class OfflineJobs:
    """The injected synthesizer handles all audio; no worker can be started."""
    batch_active = None
    def start(self, *args, **kwargs):
        raise AssertionError('A live worker must never start in this check')


def wait_for(manager, ident, states, timeout=3600):
    deadline, last = time.monotonic() + timeout, 0
    while time.monotonic() < deadline:
        result = manager.summary(ident)
        if result['status'] in states:
            return result
        if time.monotonic() - last > 15:
            print(json.dumps({k: result.get(k) for k in ('status','phase','discovered','total','saved','error')}), flush=True)
            last = time.monotonic()
        time.sleep(.25)
    raise AssertionError('Timed out waiting for ' + repr(states))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--calls', type=int, default=60000)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.calls < 100:
        parser.error('Use at least 100 calls to exercise pagination and recovery')
    report = {'calls': args.calls, 'online_synthesis': False, 'passed': False}
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='dialogue-large-capacity-') as raw:
        base = Path(raw)
        source, output, state = [base / name for name in ('input','output','state')]
        for folder in (source, output, state):
            folder.mkdir(mode=0o700)
        for role_index, role in enumerate(('AGENT','USER')):
            for index in range(args.calls):
                content = ('<analyze><subject><function><text>这是虚构容量测试。</text>'
                           f'<time>{role_index*2000},{role_index*2000+1000}</time></function></subject>'
                           f'<callInfo><callReference>synthetic-{index}</callReference><role>{role}</role></callInfo></analyze>')
                (source / f'{index:09d}{role_index}.xml').write_text(content, encoding='utf-8')
        (source / 'ignored.txt').write_text('Fictional non-XML file')
        (source / 'broken.xml').write_text('<analyze>')
        (source / '9999999990.xml').write_text('<analyze><callInfo><callReference>missing</callReference><role>AGENT</role></callInfo></analyze>')
        (source / 'oversized.xml').write_bytes(b' ' * (2 * 1024 * 1024 + 1))
        # Appended after both role runs: a late repeated track must invalidate
        # the whole identity even when an earlier pair looked complete.
        for name, role in [('9999999980.xml','AGENT'), ('9999999981.xml','USER'), ('late-duplicate.xml','AGENT')]:
            (source / name).write_text('<analyze><subject><function><text>虚构重复声道。</text><time>0,1000</time></function></subject>'
                                      f'<callInfo><callReference>late-duplicate</callReference><role>{role}</role></callInfo></analyze>', encoding='utf-8')
        report['fixture_seconds'] = round(time.monotonic()-started, 3)
        print(json.dumps({'fixtures_created': args.calls*2+7, 'seconds': report['fixture_seconds']}), flush=True)
        access = NativeWorkspaceAccess(state)
        input_grant, output_grant = access.register(source,'input'), access.register(output,'output')
        counter = {'calls': 0}
        def fake_synthesis(preview):
            counter['calls'] += 1
            return ROOT / 'web_dialogue' / 'silence.mp3'
        jobs = OfflineJobs()
        manager = Workspaces(state,jobs,access,synthesizer=fake_synthesis)
        ident = manager.import_folder(input_grant['folder_id'])['id']
        scan_started = time.monotonic()
        scan = wait_for(manager,ident,{'ready','error','cancelled'})
        assert scan['status']=='ready', scan
        assert scan['scanned']==args.calls*2+6, scan
        assert scan['ready']==args.calls and scan['error']==4, scan
        with manager.db() as db:
            assert db.execute("SELECT count(*) FROM items WHERE wid=? AND error_code='duplicate_track'", (ident,)).fetchone()[0]==1
        report['late_duplicate_rejected_globally'] = True
        report['scan_seconds'] = round(time.monotonic()-scan_started,3)
        report['scan'] = {k:scan[k] for k in ('discovered','scanned','total','ready','error')}
        page = manager.page(ident,limit=100)
        assert len(page['items'])==100
        report['page_response_bytes'] = len(json.dumps(page,ensure_ascii=False).encode())
        assert report['page_response_bytes']<256*1024
        selection = manager.select(ident,{'mode':'all','filter':'ready'})
        assert selection['count']==args.calls
        # Deselection is an incremental request, and a new selection snapshot
        # survives a complete manager/access reconstruction.
        first = manager.page(ident,filter='ready',limit=1)['items'][0]
        selection = manager.select(ident,{'mode':'toggle','item_id':first['id'],'selected':False})
        assert selection['count']==args.calls-1
        selection = manager.select(ident,{'mode':'toggle','item_id':first['id'],'selected':True})
        assert selection['count']==args.calls
        manager.close();access.close()
        access = NativeWorkspaceAccess(state)
        manager = Workspaces(state,jobs,access,synthesizer=fake_synthesis)
        assert manager.summary(ident)['selected']==args.calls
        manager.restore(ident)
        access.restore(output_grant['folder_id'])
        selection_id = manager.summary(ident)['selection_id']
        execution_started = time.monotonic()
        manager.start(ident,{'selection_id':selection_id,'output_folder_id':output_grant['folder_id'],
                             'consent':True,'request_id':'synthetic-capacity-work'})
        finished = wait_for(manager,ident,{'done','paused','interrupted','error'})
        assert finished['status']=='done' and finished['saved']==args.calls, finished
        assert counter['calls']==args.calls, counter
        report['execution_seconds'] = round(time.monotonic()-execution_started,3)
        report['synthesis_replacements'] = counter['calls']
        assert sum(1 for _ in output.glob('*.mp3'))==args.calls
        # Verify both early and late receipts after all saves and reopening.
        with manager.db() as db:
            endpoints = db.execute("SELECT filename,fingerprint FROM items WHERE wid=? AND status='saved' ORDER BY filename LIMIT 1",(ident,)).fetchall()
            endpoints += db.execute("SELECT filename,fingerprint FROM items WHERE wid=? AND status='saved' ORDER BY filename DESC LIMIT 1",(ident,)).fetchall()
        manager.close();access.close()
        access = NativeWorkspaceAccess(state)
        access.restore(output_grant['folder_id'])
        for row in endpoints:
            assert access.verify_output(output_grant['folder_id'],row['filename'],row['fingerprint'])['state']=='saved'
        report['early_late_receipts_verified_after_reopen'] = True
        report['saved_files'] = args.calls
        access.close()
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss if resource else None
    report['peak_rss_mib'] = round(peak/(1024*1024 if sys.platform=='darwin' else 1024),2) if peak else None
    report['total_seconds'] = round(time.monotonic()-started,3)
    report['passed'] = True
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
