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

def capture_owned_prompts(root, report_path, binding_context=None, *, process_name="helper.exe", target_path=None):
    """Read-only evidence bound to the disposable helper or exact target exe."""
    if process_name not in {"helper.exe","DialogueTTS.exe"}:raise ValueError("Unsupported test process")
    if process_name != "helper.exe":binding_context=None
    result=[]
    query="Get-CimInstance Win32_Process -Filter \"Name='"+process_name+"'\" | Select-Object ProcessId,ExecutablePath | ConvertTo-Json -Compress"
    raw=subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',query],capture_output=True,text=True,timeout=15)
    values=json.loads(raw.stdout or '[]');values=values if isinstance(values,list) else [values]
    processes={int(x['ProcessId']):Path(x['ExecutablePath']).resolve() for x in values if x.get('ExecutablePath') and Path(x['ExecutablePath']).resolve().is_relative_to(root.resolve())}
    if target_path is not None:processes={pid:path for pid,path in processes.items() if path==Path(target_path).resolve()}
    pids=set(processes)
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
        controls=[]
        def child_text(child,_):
            label=ctypes.create_unicode_buffer(1024);control_class=ctypes.create_unicode_buffer(128)
            user.GetWindowTextW(child,label,1024);user.GetClassNameW(child,control_class,128)
            if label.value and len(controls)<20:controls.append({'class':control_class.value,'text':label.value})
            return True
        user.EnumChildWindows(ctypes.c_void_p(hwnd),callback(child_text),0)
        entry['controls']=controls
        width,height=rect.right-rect.left,rect.bottom-rect.top
        if 0<width<=2400 and 0<height<=1600 and len(result)<3:
            image=Path(report_path).parent/(('windows-update-target-' if process_name=='DialogueTTS.exe' else 'windows-update-prompt-')+str(len(result))+'.png')
            # System.Drawing captures only this known test window, never user content.
            command="Add-Type -AssemblyName System.Drawing; $b=New-Object System.Drawing.Bitmap -ArgumentList ([int]$args[2]),([int]$args[3]); $g=[Drawing.Graphics]::FromImage($b); $g.CopyFromScreen([int]$args[0],[int]$args[1],0,0,$b.Size); $b.Save($args[4],[Drawing.Imaging.ImageFormat]::Png); $g.Dispose(); $b.Dispose()"
            try:
                # Script parameters are passed via a temporary script rather than shell interpolation.
                script=root/'capture-owned-window.ps1';script.write_text(command)
                subprocess.run(['powershell','-NoProfile','-NonInteractive','-File',str(script),str(rect.left),str(rect.top),str(width),str(height),str(image)],capture_output=True,timeout=15,check=True)
                entry['screenshot']=image.name
            except Exception as exc:entry['capture_error']=type(exc).__name__
        if binding_context:
            from web_dialogue.safe_updater import sha256
            expected=binding_context['target'].resolve()
            displayed={os.path.normcase(os.path.normpath(c['text'])) for c in controls}
            target_matches=bool(displayed & {os.path.normcase(str(expected)),os.path.normcase(str(binding_context['target']))})
            binding='full-control-text' if target_matches else None
            if not target_matches:
                # Read-only accessibility metadata may retain the untruncated filename.
                try:
                    script=root/'read-owned-accessibility.ps1'
                    script.write_text("param([long]$WindowHandle)\nAdd-Type -AssemblyName UIAutomationClient; Add-Type -AssemblyName UIAutomationTypes; $e=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$WindowHandle); $e.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition) | ForEach-Object { @{name=$_.Current.Name;help=$_.Current.HelpText} } | ConvertTo-Json -Compress")
                    accessible=subprocess.run(['powershell','-NoProfile','-NonInteractive','-File',str(script),str(hwnd)],capture_output=True,text=True,timeout=10,check=True)
                    values=json.loads(accessible.stdout or '[]');values=values if isinstance(values,list) else [values]
                    entry['accessibility']=values[:20]
                    texts={os.path.normcase(os.path.normpath(str(v))) for item in values for v in item.values() if isinstance(v,str)}
                    target_matches=bool(texts & {os.path.normcase(str(expected)),os.path.normcase(str(binding_context['target']))})
                    if target_matches:binding='full-accessibility-path'
                except Exception as exc:entry['accessibility_error']=type(exc).__name__
            if not target_matches:
                # The OS really shortens SysLink text. Bind it to the same trusted
                # helper's exact private plan, immutable candidate hash and unique
                # disposable directory suffix; never accept a basename alone.
                plan_file=binding_context['plan']
                if plan_file.is_file() and not plan_file.is_symlink() and plan_file.stat().st_size<=16384:
                    plan=json.loads(plan_file.read_text())
                    payload=Path(plan['payload']).resolve()
                    unique_tail=os.path.normcase(root.name[-8:]+'\\installation\\DialogueTTS.exe')
                    suffix_matches=any(c['class']=='SysLink' and c['text'].startswith('...') and unique_tail in os.path.normcase(c['text']) and os.path.normcase(str(binding_context['target'])).endswith(os.path.normcase(c['text'][3:])) for c in controls)
                    plan_matches=Path(plan['target']).resolve()==expected and payload.is_relative_to((plan_file.parent/'extracted').resolve()) and sha256(payload)==binding_context['sha256'] and sha256(binding_context['helper'])==binding_context['helper_sha256']
                    target_matches=bool(suffix_matches and plan_matches)
                    if target_matches:binding='exact-plan-helper-hashes-and-unique-OS-suffix'
            entry['verified_target_binding']=binding
            entry['exact_helper_owner']=processes.get(pid.value)==binding_context['helper'].resolve()
            buttons={c['text'].replace('&','').strip() for c in controls if c['class']=='Button'}
            entry['run_and_cancel_visible']={'Run','Cancel'} <= buttons
        result.append(entry);return True
    user.EnumWindows(callback(visit),0)
    return result


def confirm_owned_prompt(windows):
    """Confirm only a hash/path-bound disposable test executable's OS dialog."""
    eligible=[w for w in windows if w.get('title')=='Open File - Security Warning' and w.get('class')=='#32770' and w.get('exact_helper_owner') is True and w.get('verified_target_binding') in {'full-control-text','full-accessibility-path','exact-plan-helper-hashes-and-unique-OS-suffix'} and w.get('run_and_cancel_visible') is True]
    if len(eligible)!=1:return False
    user=ctypes.windll.user32
    user.SendMessageW.argtypes=[ctypes.c_void_p,ctypes.c_uint,ctypes.c_size_t,ctypes.c_ssize_t]
    callback=ctypes.WINFUNCTYPE(ctypes.c_bool,ctypes.c_void_p,ctypes.c_void_p)
    clicked=[]
    def visit(hwnd,_):
        pid=ctypes.c_ulong();user.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
        title=ctypes.create_unicode_buffer(512);user.GetWindowTextW(hwnd,title,512)
        if pid.value!=eligible[0]['pid'] or title.value!='Open File - Security Warning':return True
        def button(child,_):
            text=ctypes.create_unicode_buffer(256);kind=ctypes.create_unicode_buffer(128)
            user.GetWindowTextW(child,text,256);user.GetClassNameW(child,kind,128)
            if kind.value=='Button' and text.value.replace('&','').strip()=='Run':
                user.PostMessageW(child,0x00F5,0,0);clicked.append(True)
            return True
        user.EnumChildWindows(ctypes.c_void_p(hwnd),callback(button),0)
        return True
    user.EnumWindows(callback(visit),0)
    return len(clicked)==1


def expected_security_gate(report,result,receipt,launch,*,correct_target,old_processes_exited,helper_exited):
    """A narrow CI build gate, explicitly NOT a successful end-to-end update."""
    required=('bad_hash_preserves_old','waits_for_old_pid','cancel_no_late_install',
              'preferences_receipts_cache_preserved','old_backup_retained')
    if not all(report.get('cases',{}).get(name) is True for name in required):return False
    if not (correct_target and old_processes_exited and helper_exited):return False
    if result.get('state')!='needs_attention' or receipt.get('state')!='waiting' or receipt.get('pid') is not None:return False
    if launch.get('state')!='needs_attention' or launch.get('shell_returned') is not False:return False
    return any(window.get('title')=='Open File - Security Warning' and window.get('class')=='#32770'
               and window.get('verified_target_binding') in {'full-control-text','full-accessibility-path','exact-plan-helper-hashes-and-unique-OS-suffix'}
               and window.get('exact_helper_owner') is True and window.get('run_and_cancel_visible') is True
               for window in report.get('owned_prompt_windows',[]))


def copy_candidate_sources(source):
    """Use the release source allowlist, plus the exact already-built frontend.

    This includes native_workspace/native_close and dependency notices without
    copying .git, installed dependencies, private state or previous evidence.
    """
    from package_sources import source_files
    from frontend_build import collect_frontend_licenses
    source=Path(source);source.mkdir(parents=True,exist_ok=True)
    for item in source_files(ROOT):
        destination=source/item.relative_to(ROOT)
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(item,destination)
    frontend=ROOT/'frontend'/'dist'
    if not (frontend/'index.html').is_file() or not any((frontend/'assets').glob('*.js')):
        raise RuntimeError('Build the production frontend before the isolated Windows update candidate.')
    for item in frontend.rglob('*'):
        if item.is_symlink():raise RuntimeError('The production frontend must not contain symbolic links.')
        if item.is_file():
            destination=source/'frontend'/'dist'/item.relative_to(frontend)
            destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(item,destination)
    collect_frontend_licenses(source/'third_party'/'bundled-runtime')


def build_candidate(root, exe):
    source=root/'candidate-source'
    copy_candidate_sources(source)
    updates=source/'web_dialogue/updates.py'
    from web_dialogue.updates import CURRENT_VERSION,version_tuple
    parts=list(version_tuple(CURRENT_VERSION));parts[-1]+=1
    test_version='.'.join(map(str,parts))
    text=updates.read_text(encoding='utf-8');needle='CURRENT_VERSION = "'+CURRENT_VERSION+'"'
    if text.count(needle)!=1:raise RuntimeError('Cannot create isolated test version')
    updates.write_text(text.replace(needle,'CURRENT_VERSION = "'+test_version+'"'),encoding='utf-8')
    # Instrument only the unpublished candidate source, not distributed source.
    # No nonce, token, transcript, or broad environment capture.
    launcher=source/'windows_launcher.py';text=launcher.read_text(encoding='utf-8')
    logfile=str(root/'candidate-startup.jsonl')
    instrumentation="\ndef _ci_diag(event, **details):\n    try:\n        with open("+repr(logfile)+", 'a', encoding='utf-8') as out: out.write(json.dumps({'event':event,'pid':os.getpid(),**details},ensure_ascii=True)+'\\n')\n    except OSError: pass\n\n"
    text=text.replace('class NativeDownloads(',instrumentation+'class NativeDownloads(',1)
    text=text.replace('def main():\n',"def main():\n    _ci_diag('main_entered', argc=len(sys.argv), mode=sys.argv[1] if len(sys.argv)>1 else '', executable=sys.executable, localappdata=os.environ.get('LOCALAPPDATA'), pyi_archive=os.environ.get('_PYI_ARCHIVE_FILE'), pyi_level=os.environ.get('_PYI_PARENT_PROCESS_LEVEL'))\n",1)
    lease_line="    installation_lease=InstallationLease(sys.executable).acquire() if getattr(sys,'frozen',False) else None\n"
    text=text.replace(lease_line,lease_line+"    _ci_diag('lease_acquired')\n",1)
    text=text.replace('    downloads._setup_updater(server)\n',"    downloads._setup_updater(server)\n    _ci_diag('backend_window_created')\n",1)
    text=text.replace('    def acknowledge_update():\n',"    def acknowledge_update():\n        _ci_diag('window_loaded', argc=len(sys.argv), receipt_path=sys.argv[2] if len(sys.argv)>2 else None)\n",1)
    text=text.replace('            acknowledge_receipt(sys.argv[2],sys.argv[3])',"            try:\n                acknowledged=acknowledge_receipt(sys.argv[2],sys.argv[3])\n                _ci_diag('ack_result',ok=acknowledged)\n            except Exception as exc:\n                _ci_diag('ack_error',kind=type(exc).__name__,message=str(exc))\n                raise",1)
    text=text.replace("        webview.start(gui='edgechromium', debug=False, private_mode=True)","        _ci_diag('webview_starting')\n        webview.start(gui='edgechromium', debug=False, private_mode=True)",1)
    compile(text,str(launcher),'exec');launcher.write_text(text,encoding='utf-8')
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
    report={'passed':False,'end_to_end_passed':False,'ci_gate_passed':False,'outcome':'failed','platform':sys.platform,'network_metadata':'not exercised; local candidate staged by test harness','folder_dialog_and_reveal':'mocked in separate folder smoke','cases':{}}
    confirm=os.environ.get('DIALOGUE_CONFIRM_TEST_UPDATE')=='1'
    old_version=os.environ.get('DIALOGUE_RELEASE_OLD_VERSION',CURRENT_VERSION)
    child=None;old=None;old_gui_pid=None;app_pid=None;root=None;stage=None;binding_context=None
    try:
        # Keep failed CI sandboxes until runner teardown so evidence is not masked by EXE locks.
        from contextlib import nullcontext
        with nullcontext(tempfile.mkdtemp(prefix='dialogue-upgrade-ci-')) as name:
            root=Path(name);local=root/'localappdata';state=local/'DialogueTTS';state.mkdir(parents=True)
            env={**os.environ,'LOCALAPPDATA':str(local)}
            # Independent sentinel bytes represent persisted settings, receipts and cache.
            sentinels={'preferences.json':b'{"version":1,"voices":{"AGENT":"zh-CN-XiaoyiNeural"},"rate":1.2,"pause_ms":400,"paths":{},"receipts":{},"exports":{}}','receipts-test.json':b'{"kept":true}','cache-test.bin':b'fictional-cache-kept'}
            for filename,data in sentinels.items():(state/filename).write_bytes(data)
            if os.environ.get('DIALOGUE_RELEASE_NEW_EXE'):
                candidate=Path(os.environ['DIALOGUE_RELEASE_NEW_EXE']).resolve();test_version=CURRENT_VERSION
                validate_payload(candidate,test_version,'win32')
                report['official_release_pair']={'old':old_version,'new':test_version}
            else:candidate,test_version=build_candidate(root,exe)
            report['candidate_sha256']=sha256(candidate)
            report['candidate_version']=test_version
            install=root/'installation';install.mkdir();target=install/'DialogueTTS.exe';shutil.copy2(exe,target)
            initial=sha256(target);validate_payload(target,old_version,'win32')
            def start_old_window():
                startup=state/'updates'/secrets.token_hex(16);startup.mkdir(parents=True)
                receipt=startup/'receipt.json';nonce=secrets.token_hex(32)
                receipt.write_text(json.dumps({'nonce':nonce,'state':'waiting'}))
                process=subprocess.Popen([str(target),'--update-receipt',str(receipt),nonce],env=env)
                deadline=time.monotonic()+45
                while time.monotonic()<deadline:
                    # Atomic receipt replacement can briefly share-lock the file
                    # on Windows. Retry within the existing readiness deadline.
                    data=wait_file(receipt,max(.1,deadline-time.monotonic()))
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
                plan={'target':str(target),'payload':str(payload),'platform':'win32','old_version':old_version,'version':test_version,'old_pids':old_pids,'old_entry_sha256':initial,'helper_nonce':secrets.token_hex(32)}
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
            # Explicit opt-in confirms only the bound disposable test dialog; OS policy stays intact.
            binding_context={'target':target,'helper':helper,'plan':path,'helper_sha256':sha256(exe),'sha256':sha256(candidate)}
            wait_file(stage/'helper-ready.json');stop_old_window(old,old_gui_pid);old=None;old_gui_pid=None
            deadline=time.monotonic()+160; capture_at=time.monotonic()+15
            while not (stage/'status.json').is_file() and time.monotonic()<deadline:
                if time.monotonic()>=capture_at and not report.get('owned_prompt_windows'):
                    try:report['owned_prompt_windows']=capture_owned_prompts(root,report_path,binding_context)
                    except Exception as exc:report['prompt_capture_error']=str(exc)
                    if confirm and report.get('owned_prompt_windows') and not report.get('system_prompt_confirmed'):
                        report['system_prompt_confirmed']=confirm_owned_prompt(report['owned_prompt_windows'])
                    capture_at=time.monotonic()+25
                time.sleep(.2)
            result=wait_file(stage/'status.json',1)
            if result.get('state')!='complete' and not report.get('owned_prompt_windows'):
                try:report['owned_prompt_windows']=capture_owned_prompts(root,report_path,binding_context)
                except Exception as exc:report['prompt_capture_error']=str(exc)
            child.wait(timeout=20);child=None
            receipt=wait_file(stage/'receipt.json');app_pid=receipt.get('pid')
            report['transaction_state']=result['state'];report['receipt_state']=receipt.get('state')
            report['cases']['replacement_and_real_ready']=result['state']=='complete' and receipt.get('state')=='ready' and sha256(target)==sha256(candidate)
            if app_pid:
                report['ready_target_windows']=capture_owned_prompts(root,report_path,None,process_name='DialogueTTS.exe',target_path=target)
                close_owned_window(app_pid)
            validate_payload(target,test_version,'win32')
            report['test_candidate_version']=test_version
            report['cases']['preferences_receipts_cache_preserved']=all((state/k).read_bytes()==v for k,v in sentinels.items())
            report['cases']['old_backup_retained']=any(p.is_file() and sha256(p)==initial for p in install.glob('DialogueTTS.exe.update-backup-*'))
            from web_dialogue.update_helper import alive
            launch=wait_file(stage/'launch.json')
            report['launch_state']=launch
            complete=all(report['cases'].values())
            security_gate=expected_security_gate(report,result,receipt,launch,
                correct_target=sha256(target)==sha256(candidate),
                old_processes_exited=not any(alive(pid) for pid in plan['old_pids']),helper_exited=True)
            if confirm and not complete:raise RuntimeError('Confirmed update did not reach real native readiness')
            if not complete and not security_gate:raise RuntimeError('Isolated update failed or has an unknown readiness blocker')
            if security_gate:
                report.update(ci_gate_passed=True,outcome='requires_system_confirmation',
                    security_confirmation_note='Unattended test stopped at the verified OS Run/Cancel dialog. No click was performed; end-to-end readiness was NOT verified in this run.')

            # Wait only for our owned app to close normally, never terminate unrelated processes.
            from web_dialogue.update_helper import alive
            if app_pid:
                deadline=time.monotonic()+20
                while alive(app_pid) and time.monotonic()<deadline:time.sleep(.2)
                if alive(app_pid):raise RuntimeError('Owned test app did not close normally')
                app_pid=None
            if complete:report.update(passed=True,end_to_end_passed=True,ci_gate_passed=True,outcome='verified')
    except Exception as exc:
        report['error']=str(exc)
        report['helper_returncode']=child.poll() if child else None
        if root and 'owned_prompt_windows' not in report:
            try:report['owned_prompt_windows']=capture_owned_prompts(root,report_path,binding_context)
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
        if root:
            log=root/'candidate-startup.jsonl'
            if log.is_file():
                report['candidate_startup_events']=[]
                for line in log.read_text(encoding='utf-8').splitlines()[-40:]:
                    try:report['candidate_startup_events'].append(json.loads(line))
                    except ValueError:pass
            # Only the exact app in this test installation, never unrelated windows.
            try:report['owned_target_windows']=capture_owned_prompts(root,report_path,None,process_name='DialogueTTS.exe',target_path=root/'installation/DialogueTTS.exe')
            except Exception as exc:report['target_capture_error']=str(exc)
        if old and old.poll() is None and old_gui_pid:
            close_owned_window(old_gui_pid)
            try:old.wait(timeout=25)
            except subprocess.TimeoutExpired:pass
        if app_pid:close_owned_window(app_pid)
        if report['ci_gate_passed'] and root:
            try:shutil.rmtree(root)
            except OSError:pass
        Path(report_path).write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    if not report['ci_gate_passed']:raise RuntimeError('Windows isolated upgrade smoke failed; see report')

if __name__=='__main__':
    sys.path.insert(0,str(ROOT))
    main(Path(sys.argv[1]),Path(sys.argv[2]))
