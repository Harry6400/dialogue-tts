"""Standalone frozen helper. No network. Never terminates an application process."""
import json, os, secrets, subprocess, sys, time, tempfile
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
    p=Path(path)
    base=(Path.home()/'Library/Application Support/DialogueTTS' if sys.platform=='darwin' else Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'AppData/Local')))/'DialogueTTS')/'updates'
    if not p.is_absolute() or not p.resolve().is_relative_to(base.resolve()) or len(p.relative_to(base).parts)!=2: return False
    if p.name!='receipt.json' or p.is_symlink() or not p.is_file() or p.stat().st_size>4096: return False
    try: data=json.loads(p.read_text())
    except (ValueError,OSError):return False
    if data.get('nonce')!=nonce or not isinstance(nonce,str) or len(nonce)!=64: return False
    # Native launch arguments are trusted OS process args, never web messages.
    data.update(state='ready',pid=os.getpid())
    fd,name=tempfile.mkstemp(prefix='.receipt-',dir=p.parent)
    with os.fdopen(fd,'w') as out:
        json.dump(data,out);out.flush();os.fsync(out.fileno())
    os.replace(name,p);return True

def launch_normal(target, receipt, nonce):
    args=['--update-receipt',str(receipt),nonce]
    if sys.platform=='darwin':
        try: subprocess.run(['/usr/bin/open','-n',str(target),'--args',*args],check=True,timeout=30)
        except subprocess.TimeoutExpired as exc: raise RecoveryRequired('System open is still pending; both versions retained') from exc
    else:
        # Python uses ShellExecuteEx through the normal Windows shell path.
        os.startfile(str(target), 'open', arguments=subprocess.list2cmdline(args), cwd=str(target.parent))
    deadline=time.monotonic()+90
    while time.monotonic()<deadline:
        try:
            data=json.loads(receipt.read_text())
            if data.get('nonce')==nonce and data.get('state')=='ready':return
        except (ValueError,OSError):pass
        time.sleep(.5)
    # Do not delete a possibly running app or dismiss Gatekeeper prompts.
    raise RecoveryRequired('New application has not confirmed readiness; old backup retained. No process was killed.')

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
