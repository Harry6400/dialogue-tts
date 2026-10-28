"""Offline smoke check of the real macOS bundled service and production UI."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import selectors
import subprocess
import tempfile
import time
import urllib.error
import urllib.request


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('service',type=Path)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    service=args.service.resolve(strict=True)
    report={'passed':False,'online_synthesis':False,'service_sha256':hashlib.sha256(service.read_bytes()).hexdigest()}
    process=None
    with tempfile.TemporaryDirectory(prefix='dialogue-frozen-workspace-') as raw:
        root=Path(raw);state=root/'state';source=root/'input';output=root/'output'
        for folder in (state,source,output):folder.mkdir(mode=0o700)
        for i in range(2):
            for role,suffix in (('AGENT','1'),('USER','0')):
                (source/f'000{i}{suffix}.xml').write_text(f'<analyze><subject><function><text>虚构原生成品测试。</text><time>0,1000</time></function></subject><callInfo><callReference>frozen-fiction-{i}</callReference><role>{role}</role></callInfo></analyze>')
        secret=secrets.token_urlsafe(32)
        env={**os.environ,'DIALOGUE_NATIVE_TOKEN':secret,'DIALOGUE_VERIFICATION_STATE_ROOT':str(state)}
        log=open(root/'service.log','wb')
        def launch():
            child=subprocess.Popen([str(service)],stdout=subprocess.PIPE,stderr=log,env=env,cwd=service.parent)
            selector=selectors.DefaultSelector();selector.register(child.stdout,selectors.EVENT_READ)
            try:
                deadline=time.monotonic()+90
                while time.monotonic()<deadline:
                    if child.poll() is not None:raise RuntimeError('Frozen service exited before ready')
                    if selector.select(.25):
                        line=child.stdout.readline().decode().strip()
                        if re.fullmatch(r'http://127\.0\.0\.1:[0-9]+',line):return child,line
                raise RuntimeError('Frozen service readiness timeout')
            except BaseException:
                child.terminate();child.wait(timeout=15);raise
            finally:selector.close()
        def request(path,payload=None,native=False,token=None):
            headers={}
            if payload is not None:
                headers={'Content-Type':'application/json', 'X-Native-Token' if native else 'X-App-Token': token if token is not None else secret if native else config['token']}
            req=urllib.request.Request(base+path,data=json.dumps(payload).encode() if payload is not None else None,headers=headers)
            try:response=urllib.request.urlopen(req,timeout=15)
            except urllib.error.HTTPError as error:response=error
            with response:return response.status,response.read(),response.headers
        try:
            process,base=launch()
            status,body,_=request('/api/config');assert status==200
            config=json.loads(body);assert secret.encode() not in body
            status,html,_=request('/');assert status==200
            assets=re.findall(rb'(?:src|href)="(/assets/[A-Za-z0-9_-]+\.(?:js|css))"',html)
            assert len(assets)>=2
            for path in assets:
                status,body,headers=request(path.decode());assert status==200 and len(body)>1000
            report['production_assets']=len(assets)
            assert request('/assets/../private.json')[0]==404
            assert request('/api/native/folders',{'path':str(source),'kind':'input'},token=config['token'])[0]==403
            status,body,_=request('/api/native/folders',{'path':str(source),'kind':'input'},native=True);assert status==200
            source_id=json.loads(body)['folder_id']
            status,body,_=request('/api/native/folders',{'path':str(output),'kind':'output'},native=True);assert status==200
            output_id=json.loads(body)['folder_id']
            status,body,_=request('/api/workspaces/import',{'input_folder_id':source_id});assert status in (200,202)
            ident=json.loads(body)['id']
            deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                status,body,_=request('/api/workspaces/'+ident)
                summary=json.loads(body)
                if summary['status']!='scanning':break
                time.sleep(.05)
            assert summary['ready']==2 and summary['scanned']==4,summary
            request('/api/workspaces/'+ident+'/folders',{'output_folder_id':output_id})
            status,body,_=request('/api/workspaces/'+ident+'/selection',{'mode':'all'});assert status==200
            selected=json.loads(body);assert selected['count']==2
            payload={'selection_id':selected['selection_id'],'output_folder_id':output_id,'request_id':'frozen-no-consent'}
            assert request('/api/workspaces/'+ident+'/start',payload)[0]==400
            report['scan_ready']=2;report['consent_required']=True;report['private_authority_separate']=True
            process.terminate();process.wait(timeout=15);process=None
            process,base=launch()
            config=json.loads(request('/api/config')[1])
            summary=json.loads(request('/api/workspaces/'+ident)[1])
            assert summary['selected']==2 and summary['input_folder']['valid'] is False
            status,body,_=request('/api/native/workspaces/restore',{'workspace_id':ident},native=True)
            assert status==200 and json.loads(body)['input_folder']['valid']
            assert json.loads(request('/api/workspaces/'+ident)[1])['status']=='ready'
            report['restart_selection_and_explicit_directory_restore']=True
            report['passed']=True
        finally:
            if process is not None:
                process.terminate();process.wait(timeout=15)
            log.close()
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
