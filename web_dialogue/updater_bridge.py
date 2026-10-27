"""Native-owned update state. UI never supplies a URL, path, version or command."""
import json, os, shutil, subprocess, sys, threading, uuid, time, secrets
from pathlib import Path
from .safe_updater import prepare, validate_payload, sha256, UpdateError
from .update_sources import discover

class Updater:
    def __init__(self, target, current_version, state_directory):
        self.target=Path(target).resolve();self.version=current_version
        self.root=Path(state_directory)/'updates';self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.offer=None;self.stage=None;self.payload=None;self.lock=threading.Lock();self.state={'state':'idle'}

    def check(self):
        with self.lock:
            self.offer=discover(self.version,sys.platform)
            self.state={'state':self.offer['state'],'latest_version':self.offer.get('version',self.version),'current_version':self.version,'source':self.offer.get('source')}
            return dict(self.state)

    def prepare_confirmed(self):
        """Native caller must obtain confirmation and hold an activity gate.
        Runs in native background thread. No replacement or shutdown yet.
        """
        if not self.lock.acquire(False):raise UpdateError('Update already in progress')
        try:
            if not self.offer or self.offer['state']!='available':raise UpdateError('Check for updates first')
            validate_payload(self.target,self.version,sys.platform)
            self.stage=self.root/uuid.uuid4().hex
            self.state={'state':'downloading','progress':0}
            self.payload=prepare(self.offer,self.stage,lambda n,total:self.state.update(progress=round(100*n/total)))
            self.state={'state':'prepared','latest_version':self.offer['version']}
            return dict(self.state)
        except Exception:
            self.state={'state':'error','message':'更新下载或校验失败，原应用和设置未改动'};raise
        finally:self.lock.release()

    def start_helper(self, old_pids):
        """Call only while native activity gate is still held; then quit normally.
        old_pids must include GUI and service; neither may be forcibly terminated.
        """
        if not self.payload or self.state['state']!='prepared':raise UpdateError('No verified update')
        if not old_pids or any(type(p)!=int or p<=0 for p in old_pids):raise UpdateError('Invalid process ownership')
        if sys.platform=='darwin':
            copy=self.stage/'helper.app';shutil.copytree(self.target,copy,symlinks=True)
            helper=copy/'Contents/MacOS/DialogueTTSService'
            entry=self.target/'Contents/MacOS/DialogueTTS'
        elif sys.platform=='win32':
            helper=self.stage/'DialogueTTS-update-helper.exe';shutil.copy2(self.target,helper);entry=self.target
        else:raise UpdateError('Unsupported platform')
        plan={'target':str(self.target),'payload':str(self.payload),'platform':sys.platform,'old_version':self.version,'version':self.offer['version'],'old_pids':old_pids,'old_entry_sha256':sha256(entry),'helper_nonce':secrets.token_hex(32)}
        path=self.stage/'plan.json';path.write_text(json.dumps(plan));path.chmod(0o600)
        process=subprocess.Popen([str(helper),'--apply-update',str(path)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=True)
        deadline=time.monotonic()+30
        while True:
            try:
                receipt=json.loads((self.stage/'helper-ready.json').read_text())
                if receipt.get('nonce')==plan['helper_nonce'] and receipt.get('state')=='ready_to_wait' and process.poll() is None:break
            except (ValueError,OSError):pass
            if process.poll() is not None or time.monotonic()>deadline:
                from .update_cancel import cancel_transaction
                cancel_transaction(self.stage.name,plan['helper_nonce'])
                raise UpdateError('更新助手未就绪，原应用继续运行，请稍后重试')
            time.sleep(.1)
        self.state={'state':'waiting_for_exit','transaction_id':self.stage.name,'cancel_token':plan['helper_nonce']}
        return dict(self.state)
