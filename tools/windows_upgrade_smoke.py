"""CI-only frozen transaction test in a disposable installation and LOCALAPPDATA.

Builds an unpublished test candidate. Network release metadata is not exercised;
PE checks, helper process, replacement, OS launch and readiness receipt are real.
"""
from pathlib import Path
import ctypes
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent

def wait_file(path, timeout=45):
    deadline = time.monotonic()+timeout
    while time.monotonic()<deadline:
        if path.is_file():
            try:return json.loads(path.read_text(encoding='utf-8'))
            except (OSError,ValueError):pass
        time.sleep(.2)
    raise TimeoutError('Expected evidence not produced: '+path.name)

def close_owned_window(pid):
    user=ctypes.windll.user32
    user.GetWindowThreadProcessId.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_ulong)]
    user.PostMessageW.argtypes=[ctypes.c_void_p,ctypes.c_uint,ctypes.c_size_t,ctypes.c_ssize_t]
    callback=ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def visit(hwnd, _):
        owner=ctypes.c_ulong();user.GetWindowThreadProcessId(hwnd,ctypes.byref(owner))
        if owner.value==pid:user.PostMessageW(hwnd,0x0010,0,0)
        return True
    user.EnumWindows(callback(visit),0)

def capture_owned_prompts(root, report_path):
    """Read-only evidence from windows owned by this disposable helper only."""
    result=[]
    query="Get-CimInstance Win32_Process -Filter \"Name='helper.exe'\" | Select-Object ProcessId,ExecutablePath | ConvertTo-Json -Compress"
    raw=subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',query],capture_output=True,text=True,timeout=15)
    values=json.loads(raw.stdout or '[]');values=values if isinstance(values,list) else [values]
    pids={int(x['ProcessId']) for x in values if x.get('ExecutablePath') and Path(x['ExecutablePath']).resolve().is_relative_to(root.resolve())}
    user=ctypes.windll.user32
    callback=ctypes.WINFUNCTYPE(ctypes.c_bool,ctypes.c_void_p,ctypes.c_void_p)
    class Rect(ctypes.Structure):_fields_=[('left',ctypes.c_long),('top',ctypes.c_long),('right',ctypes.c_long),('bottom',ctypes.c_long)]
    user.GetWindowThreadProcessId.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_ulong)]
    user.GetWindowTextW.argtypes=[ctypes.c_void_p,ctypes.c_wchar_p,ctypes.c_int]
    user.GetClassNameW.argtypes=[ctypes.c_void_p,ctypes.c_wchar_p,ctypes.c_int]
    user.GetWindowRect.argtypes=[ctypes.c_void_p,ctypes.POINTER(Rect)]
    user.IsWindowVisible.argtypes=[ctypes.c_void_p]
    def visit(hwnd,_):
        pid=ctypes.c_ulong();user.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
        if pid.value not in pids or not user.IsWindowVisible(hwnd):return True
        title=ctypes.create_unicode_buffer(512);kind=ctypes.create_unicode_buffer(128)
        user.GetWindowTextW(hwnd,title,512);user.GetClassNameW(hwnd,kind,128)
        rect=Rect();user.GetWindowRect(hwnd,ctypes.byref(rect))
        entry={'pid':pid.value,'title':title.value,'class':kind.value}
        width,height=rect.right-rect.left,rect.bottom-rect.top
        if 0<width<=2400 and 0<height<=1600 and len(result)<3:
            image=Path(report_path).parent/('windows-update-prompt-'+str(len(result))+'.png')
            # System.Drawing captures only this known test window, never user content.
            command="Add-Type -AssemblyName System.Drawing; $b=New-Object System.Drawing.Bitmap -ArgumentList ([int]$args[2]),([int]$args[3]); $g=[Drawing.Graphics]::FromImage($b); $g.CopyFromScreen([int]$args[0],[int]$args[1],0,0,$b.Size); $b.Save($args[4],[Drawing.Imaging.ImageFormat]::Png); $g.Dispose(); $b.Dispose()"
            try:
                # Script parameters are passed via a temporary script rather than shell interpolation.
                script=root/'capture-owned-window.ps1';script.write_text(command)
                subprocess.run(['powershell','-NoProfile','-NonInteractive','-File',str(script),str(rect.left),str(rect.top),str(width),str(height),str(image)],capture_output=True,timeout=15,check=True)
                entry['screenshot']=image.name
            except Exception as exc:entry['capture_error']=type(exc).__name__
        result.append(entry);return True
    user.EnumWindows(callback(visit),0)
    return result


def build_candidate(root, exe):
    source=root/'candidate-source';source.mkdir()
    # Explicit source types only; never copy .git, credentials, state or prior evidence.
    for name in ('windows_launcher.py','native_windows_folders.py','native_windows_updater.py'):
        shutil.copy2(ROOT/name,source/name)
    for folder in ('web_dialogue','assets'):
        dest=source/folder;dest.mkdir()
        for item in (ROOT/folder).iterdir():
            if item.is_file() and item.suffix in {'.py','.html','.mp3','.ico','.png'}:shutil.copy2(item,dest/item.name)
    updates=source/'web_dialogue/updates.py'
    from web_dialogue.updates import CURRENT_VERSION,version_tuple
    parts=list(version_tuple(CURRENT_VERSION));parts[-1]+=1
    test_version='.'.join(map(str,parts))
    text=updates.read_text(encoding='utf-8');needle='CURRENT_VERSION = "'+CURRENT_VERSION+'"'
    if text.count(needle)!=1:raise RuntimeError('Cannot create isolated test version')
    updates.write_text(text.replace(needle,'CURRENT_VERSION = "'+test_version+'"'),encoding='utf-8')
    from windows_version_resource import write_resource
    resource=root/'candidate-version.txt';write_resource(resource)
    text=resource.read_text(encoding='utf-8').replace(CURRENT_VERSION,test_version).replace(str((*version_tuple(CURRENT_VERSION),0)),str((*parts,0)))
    resource.write_text(text,encoding='utf-8')
    from build_windows import pyinstaller_command
    command=pyinstaller_command(source,root/'candidate-dist',root/'candidate-build',resource)
    subprocess.run(command,cwd=source,check=True,timeout=240)
    candidate=root/'candidate-dist/DialogueTTS.exe'
    from web_dialogue.safe_updater import validate_payload
    validate_payload(candidate,test_version,'win32')
    return candidate,test_version

def main(exe, report_path):
    from web_dialogue.safe_updater import sha256, validate_payload
    from web_dialogue.updates import CURRENT_VERSION
    report={'passed':False,'platform':sys.platform,'network_metadata':'not exercised; local candidate staged by test harness','folder_dialog_and_reveal':'mocked in separate folder smoke','cases':{}}
    child=None;old=None;old_gui_pid=None;app_pid=None;root=None;stage=None
    try:
        # Keep failed CI sandboxes until runner teardown so evidence is not masked by EXE locks.
        from contextlib import nullcontext
        with nullcontext(tempfile.mkdtemp(prefix='dialogue-upgrade-ci-')) as name:
            root=Path(name);local=root/'localappdata';state=local/'DialogueTTS';state.mkdir(parents=True)
            env={**os.environ,'LOCALAPPDATA':str(local)}
            # Independent sentinel bytes represent persisted settings, receipts and cache.
            sentinels={'preferences.json':b'{"version":1,"voices":{"AGENT":"zh-CN-XiaoyiNeural"},"rate":1.2,"pause_ms":400,"paths":{},"receipts":{},"exports":{}}','receipts-test.json':b'{"kept":true}','cache-test.bin':b'fictional-cache-kept'}
            for filename,data in sentinels.items():(state/filename).write_bytes(data)
            candidate,test_version=build_candidate(root,exe)
            install=root/'installation';install.mkdir();target=install/'DialogueTTS.exe';shutil.copy2(exe,target)
            initial=sha256(target);validate_payload(target,CURRENT_VERSION,'win32')
            def start_old_window():
                startup=state/'updates'/secrets.token_hex(16);startup.mkdir(parents=True)
                receipt=startup/'receipt.json';nonce=secrets.token_hex(32)
                receipt.write_text(json.dumps({'nonce':nonce,'state':'waiting'}))
                process=subprocess.Popen([str(target),'--update-receipt',str(receipt),nonce],env=env)
                deadline=time.monotonic()+45
                while time.monotonic()<deadline:
                    data=json.loads(receipt.read_text())
                    if data.get('state')=='ready':return process,data['pid']
                    if process.poll() is not None:raise RuntimeError('Old frozen app exited before readiness')
                    time.sleep(.2)
                raise TimeoutError('Old frozen GUI did not acknowledge readiness')
            def stop_old_window(process, gui_pid):
                close_owned_window(gui_pid);process.wait(timeout=25)

            def make_plan(case, old_pids):
                stage=state/'updates'/secrets.token_hex(16);extracted=stage/'extracted';extracted.mkdir(parents=True)
                payload=extracted/'DialogueTTS.exe';shutil.copy2(candidate,payload)
                helper=stage/'helper.exe';shutil.copy2(exe,helper)
                plan={'target':str(target),'payload':str(payload),'platform':'win32','old_version':CURRENT_VERSION,'version':test_version,'old_pids':old_pids,'old_entry_sha256':initial,'helper_nonce':secrets.token_hex(32)}
                path=stage/'plan.json';path.write_text(json.dumps(plan),encoding='utf-8')
                return stage,path,helper,plan
            # Actual frozen helper refuses a changed installed hash before replacement.
            stage,path,helper,plan=make_plan('bad-hash',[]);plan['old_entry_sha256']='0'*64;path.write_text(json.dumps(plan))
            subprocess.run([str(helper),'--apply-update',str(path)],env=env,check=True,timeout=40)
            result=wait_file(stage/'status.json')
            report['cases']['bad_hash_preserves_old']=result['state']=='failed_before_replace' and sha256(target)==initial
            if not report['cases']['bad_hash_preserves_old']:raise RuntimeError('Bad hash was not rejected')
            # Real old-process wait and cancellation; closing it later must not install.
            old,old_gui_pid=start_old_window()
            stage,path,helper,plan=make_plan('cancelled-case',[old.pid,old_gui_pid])
            child=subprocess.Popen([str(helper),'--apply-update',str(path)],env=env)
            wait_file(stage/'helper-ready.json');time.sleep(.5)
            report['cases']['waits_for_old_pid']=child.poll() is None and sha256(target)==initial and not (stage/'receipt.json').exists()
            subprocess.run([str(helper),'--cancel-update',stage.name,plan['helper_nonce']],env=env,check=True,timeout=30)
            result=wait_file(stage/'status.json');stop_old_window(old,old_gui_pid);old=None;old_gui_pid=None;child.wait(timeout=20);child=None
            time.sleep(.5);report['cases']['cancel_no_late_install']=result['state']=='cancelled' and sha256(target)==initial
            if not all(report['cases'].values()):raise RuntimeError('Wait/cancel transaction failed')
            # Actual replacement launches through the OS and acknowledges only real window ready.
            old,old_gui_pid=start_old_window()
            stage,path,helper,plan=make_plan('successful-case',[old.pid,old_gui_pid])
            child=subprocess.Popen([str(helper),'--apply-update',str(path)],env=env)
            wait_file(stage/'helper-ready.json');stop_old_window(old,old_gui_pid);old=None;old_gui_pid=None
            deadline=time.monotonic()+160; capture_at=time.monotonic()+15
            while not (stage/'status.json').is_file() and time.monotonic()<deadline:
                if time.monotonic()>=capture_at and not report.get('owned_prompt_windows'):
                    try:report['owned_prompt_windows']=capture_owned_prompts(root,report_path)
                    except Exception as exc:report['prompt_capture_error']=str(exc)
                    capture_at=time.monotonic()+25
                time.sleep(.2)
            result=wait_file(stage/'status.json',1)
            if result.get('state')!='complete' and not report.get('owned_prompt_windows'):
                try:report['owned_prompt_windows']=capture_owned_prompts(root,report_path)
                except Exception as exc:report['prompt_capture_error']=str(exc)
            child.wait(timeout=20);child=None
            receipt=wait_file(stage/'receipt.json');app_pid=receipt.get('pid')
            report['transaction_state']=result['state'];report['receipt_state']=receipt.get('state')
            report['cases']['replacement_and_real_ready']=result['state']=='complete' and receipt.get('state')=='ready' and sha256(target)==sha256(candidate)
            if app_pid:close_owned_window(app_pid)
            validate_payload(target,test_version,'win32')
            report['test_candidate_version']=test_version
            report['cases']['preferences_receipts_cache_preserved']=all((state/k).read_bytes()==v for k,v in sentinels.items())
            report['cases']['old_backup_retained']=Path(result.get('backup','missing')).is_file() and sha256(Path(result['backup']))==initial if result.get('backup') else False
            if not all(report['cases'].values()):raise RuntimeError('Isolated update not fully verified; check transaction state')
            # Wait only for our owned app to close normally, never terminate unrelated processes.
            from web_dialogue.update_helper import alive
            if app_pid:
                deadline=time.monotonic()+20
                while alive(app_pid) and time.monotonic()<deadline:time.sleep(.2)
                if alive(app_pid):raise RuntimeError('Owned test app did not close normally')
                app_pid=None
            report['passed']=True
    except Exception as exc:
        report['error']=str(exc)
        report['helper_returncode']=child.poll() if child else None
        if root and 'owned_prompt_windows' not in report:
            try:report['owned_prompt_windows']=capture_owned_prompts(root,report_path)
            except Exception as capture_error:report['prompt_capture_error']=str(capture_error)
        if stage:
            report['transaction_evidence']={}
            for name in ('plan.json','helper-ready.json','receipt.json','status.json','launch.json'):
                file=stage/name
                if file.is_file():
                    try:
                        evidence=json.loads(file.read_text())
                        report['transaction_evidence'][name]={k:v for k,v in evidence.items() if k not in {'nonce','helper_nonce'}}
                    except (OSError,ValueError):report['transaction_evidence'][name]='unreadable'
            if (stage/'plan.json').is_file():
                from web_dialogue.update_helper import alive
                plan=json.loads((stage/'plan.json').read_text())
                report['old_pid_alive']={str(pid):alive(pid) for pid in plan['old_pids']}
                report['replacement_started']=(stage/'replacing').exists()
            try:
                listing=subprocess.run(['tasklist','/FI','IMAGENAME eq DialogueTTS.exe','/FO','CSV'],capture_output=True,text=True,timeout=10)
                report['owned_app_process_diagnostic']=listing.stdout
            except Exception:pass
    finally:
        if old and old.poll() is None and old_gui_pid:
            close_owned_window(old_gui_pid)
            try:old.wait(timeout=25)
            except subprocess.TimeoutExpired:pass
        if app_pid:close_owned_window(app_pid)
        if report['passed'] and root:
            try:shutil.rmtree(root)
            except OSError:pass
        Path(report_path).write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    if not report['passed']:raise RuntimeError('Windows isolated upgrade smoke failed; see report')

if __name__=='__main__':
    sys.path.insert(0,str(ROOT))
    main(Path(sys.argv[1]),Path(sys.argv[2]))
