"""Standalone frozen helper. No network. Never terminates an application process."""
import json, os, secrets, subprocess, sys, time, tempfile, threading
from pathlib import Path
from .safe_updater import UpdateError, validate_payload, replace_transaction, sha256

class UpdateCancelled(UpdateError): pass

class RecoveryRequired(UpdateError):
    """New process state unknown: leave both copies intact rather than kill it."""

def alive(pid):
    if sys.platform=='win32':
        import ctypes
        k=ctypes.WinDLL('kernel32',use_last_error=True)
        k.OpenProcess.restype=ctypes.c_void_p
        h=k.OpenProcess(0x1000,False,pid)
        if not h: return ctypes.get_last_error()==5
        code=ctypes.c_ulong();ok=k.GetExitCodeProcess(ctypes.c_void_p(h),ctypes.byref(code));k.CloseHandle(ctypes.c_void_p(h))
        return not ok or code.value==259
    try: os.kill(pid,0);return True
    except ProcessLookupError:return False
    except PermissionError:return True

def acknowledge_receipt(path, nonce):
    """Called by native shell ONLY after backend and real window are ready.

    Receipt must already exist under a private updater stage. It cannot select
    arbitrary write paths: validate its own nonce, app target and bounded JSON.
    """
    from .updater_cli import state_directory
    raw=Path(path)
    if not raw.is_absolute() or raw.name!='receipt.json' or raw.is_symlink():return False
    try:
        # Both operands must be canonical. On Windows a helper can expand an
        # 8.3 user/temp path while LOCALAPPDATA still contains its short alias.
        # macOS /var and /tmp aliases need the same treatment.
        p=raw.resolve(strict=True)
        base=(state_directory()/'updates').resolve(strict=True)
        if not p.is_relative_to(base) or len(p.relative_to(base).parts)!=2:return False
        if not p.is_file() or p.stat().st_size>4096:return False
    except (OSError,ValueError,RuntimeError):return False
    try: data=json.loads(p.read_text())
    except (ValueError,OSError):return False
    if data.get('nonce')!=nonce or not isinstance(nonce,str) or len(nonce)!=64: return False
    # Native launch arguments are trusted OS process args, never web messages.
    data.update(state='ready',pid=os.getpid())
    fd,name=tempfile.mkstemp(prefix='.receipt-',dir=p.parent)
    with os.fdopen(fd,'w') as out:
        json.dump(data,out);out.flush();os.fsync(out.fileno())
    os.replace(name,p);return True

def _windows_shell_open(target, args):
    """Initialize COM on this owned shell thread, retaining normal OS policy.

    Microsoft recommends STA + DISABLE_OLE1DDE for ShellExecute extensions:
    https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-shellexecutew
    """
    import ctypes
    ole=ctypes.OleDLL('ole32')
    ole.CoInitializeEx.argtypes=[ctypes.c_void_p,ctypes.c_uint32]
    ole.CoInitializeEx.restype=ctypes.c_long
    initialized=False
    try:
        hr=ole.CoInitializeEx(None,0x2|0x4)
        if hr not in (0,1):raise OSError('Cannot initialize the Windows shell apartment')
        initialized=True
        os.startfile(str(target),'open',arguments=subprocess.list2cmdline(args),cwd=str(target.parent))
    finally:
        if initialized:ole.CoUninitialize()

def _receipt_ready(receipt,nonce):
    try:
        data=json.loads(receipt.read_text())
        return data.get('nonce')==nonce and data.get('state')=='ready'
    except (ValueError,OSError):return False

def launch_normal(target, receipt, nonce, *, timeout=90):
    args=['--update-receipt',str(receipt),nonce]
    if sys.platform=='darwin':
        from .verification_profile import ENV, verification_root
        profile = verification_root()
        # Only explicit verification launches carry a private state root through LaunchServices.
        command = ['/usr/bin/open', '-n', str(target)]
        if profile is not None: command += ['--env', ENV + '=' + str(profile)]
        try: subprocess.run([*command, '--args', *args],check=True,timeout=30)
        except subprocess.TimeoutExpired as exc: raise RecoveryRequired('System open is still pending; both versions retained') from exc
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if _receipt_ready(receipt,nonce):return
            time.sleep(.2)
    else:
        # ShellExecute may synchronously wait for security/UI confirmation.
        # The deadline includes that call, not just the later ready handshake.
        # Never force-stop a process, suppress a prompt, or remove MOTW.
        outcome={};done=threading.Event();deadline=time.monotonic()+timeout
        evidence=receipt.parent/'launch.json'
        evidence.write_text(json.dumps({'state':'shell_pending','started_at':time.time()}))
        def invoke_shell():
            try:
                _windows_shell_open(target,args)
                outcome['state']='shell_returned'
            except Exception as exc:
                outcome.update(state='shell_error',error=exc)
            finally:
                try:evidence.write_text(json.dumps({'state':outcome['state'],'finished_at':time.time()}))
                except OSError:pass
                done.set()
        threading.Thread(target=invoke_shell,name='DialogueTTS-OS-open',daemon=True).start()
        while time.monotonic()<deadline:
            if _receipt_ready(receipt,nonce):return
            if done.is_set() and 'error' in outcome:raise outcome['error']
            remaining=min(.2,max(0,deadline-time.monotonic()))
            if done.is_set():time.sleep(remaining)
            else:done.wait(remaining)
        evidence.write_text(json.dumps({'state':'needs_attention','shell_returned':done.is_set(),'message':'OS launch/readiness not confirmed within deadline'}))
    # A blocked OS call / pending prompt is NOT proof of failed startup.
    # Keep both copies. The daemon OS-call thread cannot hold helper exit forever;
    # no attempt is made to terminate a launched app or interact with system UI.
    raise RecoveryRequired('系统启动或安全确认尚未完成；已保留新旧版本，没有强制关闭应用。请检查系统提示或从原位置重新打开对白。')

def _apply(plan_path):
    plan_file=Path(plan_path).resolve(strict=True)
    from .updater_cli import state_directory
    allowed=(state_directory()/'updates').resolve()
    if not plan_file.is_relative_to(allowed) or len(plan_file.relative_to(allowed).parts)!=2:raise UpdateError('Plan outside private updater directory')
    if plan_file.name!='plan.json' or plan_file.stat().st_size>16384:raise UpdateError('Invalid update plan')
    plan=json.loads(plan_file.read_text()); stage=plan_file.parent
    target=Path(plan['target']); payload=Path(plan['payload'])
    expected_name='对白.app' if sys.platform=='darwin' else 'DialogueTTS.exe'
    if target.name!=expected_name or not target.is_absolute() or target.is_symlink() or not payload.resolve().is_relative_to(stage/'extracted'):raise UpdateError('Unexpected installation target')
    if plan['platform']!=sys.platform:raise UpdateError('Wrong platform')
    validate_payload(target,plan['old_version'],sys.platform)
    validate_payload(payload,plan['version'],sys.platform)
    entry=target/'Contents/MacOS/DialogueTTS' if sys.platform=='darwin' else target
    if sha256(entry)!=plan['old_entry_sha256']:raise UpdateError('Installed app changed while update was staged')
    def check_cancelled():
        if (stage/'cancelled').exists():raise UpdateCancelled('Update was disarmed by the original app')
    check_cancelled()
    (stage/'helper-ready.json').write_text(json.dumps({'nonce':plan['helper_nonce'],'state':'ready_to_wait'}))
    deadline=time.monotonic()+120
    while any(alive(pid) for pid in plan['old_pids']):
        check_cancelled()
        if time.monotonic()>deadline:raise UpdateError('Old application did not exit normally; update cancelled')
        time.sleep(.25)
    from .install_lock import InstallationLease
    lease=InstallationLease(target).acquire()
    try:
        check_cancelled()
        validate_payload(target,plan['old_version'],sys.platform)
        if sha256(entry)!=plan['old_entry_sha256']:raise UpdateError('Installed app changed before replacement')
        receipt=stage/'receipt.json';nonce=secrets.token_hex(32)
        receipt.write_text(json.dumps({'nonce':nonce,'state':'waiting'}));receipt.chmod(0o600)
        def open_verified(app):
            lease.close()  # replacement complete; new backend must acquire its own lease
            try:
                launch_normal(app,receipt,nonce)
            except RecoveryRequired:
                raise
            except Exception:
                try: lease.acquire()
                except UpdateError as exc: raise RecoveryRequired('另一实例可能已启动；保留新旧版本，不自动回滚') from exc
                raise
        check_cancelled()
        from .update_cancel import begin_replacement
        if not begin_replacement(stage):raise UpdateCancelled('Update cancelled before replacement')
        backup=replace_transaction(target,payload,open_verified)
        status={'state':'complete','version':plan['version'],'backup':str(backup)}
    except UpdateCancelled:
        lease.close()
        raise
    except RecoveryRequired as exc:
        status={'state':'needs_attention','message':str(exc)}
    except Exception as exc:
        status={'state':'rolled_back','message':str(exc)}
        # Only ordinary launch failure is rolled back, through the normal OS route.
        lease.close()
        try:
            if sys.platform=='darwin':subprocess.run(['/usr/bin/open',str(target)],check=True,timeout=30)
            else:os.startfile(str(target))
        except Exception:pass
    lease.close()
    (stage/'status.json').write_text(json.dumps(status))
    return status


def main(plan_path):
    """Persist pre-replacement failures too; leave/reopen verified old app safely."""
    from .updater_cli import state_directory
    p=Path(plan_path).resolve(strict=True)
    allowed=(state_directory()/'updates').resolve()
    if p.name!='plan.json' or not p.is_relative_to(allowed) or len(p.relative_to(allowed).parts)!=2 or p.stat().st_size>16384:raise UpdateError('Invalid private plan path')
    plan=json.loads(p.read_text());stage=p.parent
    try:return _apply(p)
    except Exception as exc:
        status={'state':'cancelled' if (stage/'cancelled').exists() else 'failed_before_replace','message':str(exc)}
        (stage/'status.json').write_text(json.dumps(status))
        # If the main app has not exited, leave it alone. Cancellation must never
        # reopen an application the user intentionally closed later.
        if (stage/'helper-ready.json').exists() and status['state']!='cancelled' and not any(alive(pid) for pid in plan.get('old_pids',[])):
            try:
                target=Path(plan['target']);validate_payload(target,plan['old_version'],sys.platform)
                from .install_lock import InstallationLease
                lease=InstallationLease(target).acquire();lease.close()
                if sys.platform=='darwin':subprocess.run(['/usr/bin/open',str(target)],check=True,timeout=30)
                else:os.startfile(str(target))
            except Exception:pass
        return status
