"""Native confirmation and activity gating for the Windows updater."""
import json, os, sys, threading
from web_dialogue.safe_updater import UpdateError
from web_dialogue.updater_bridge import Updater
from web_dialogue.updater_cli import state_directory
from native_close import workspace_safety
from native_windows_contracts import LockPort, ServicePort, UpdaterPort, WindowPort

class NativeUpdaterMixin:
    _window: WindowPort | None = None
    _update_server: ServicePort | None = None
    _updater: UpdaterPort | None = None
    _update_gate: LockPort | None = None
    _lock: LockPort
    _folder_lock: LockPort

    def _setup_updater(self, server: ServicePort):
        from web_dialogue.updates import CURRENT_VERSION
        self._update_server=server
        self._updater=Updater(sys.executable,CURRENT_VERSION,state_directory()) if getattr(sys,'frozen',False) else None
        if self._update_gate is None:self._update_gate=threading.Lock()

    def check_for_update(self):
        try:
            if not self._updater:return {'state':'error','message':'仅正式桌面安装包支持应用内更新'}
            return self._updater.check()
        except Exception:return {'state':'error','message':'检查更新失败，请稍后重试'}

    def install_update(self):
        if self._update_server is None or self._window is None or not self._updater or self._update_gate is None or not self._update_gate.acquire(False):return {'state':'error','message':'更新尚未就绪'}
        export_lock=False;save_lock=False;gated=False
        try:
            # Both native locks remain held until shutdown or a cancelled update.
            save_lock=self._lock.acquire(False)
            export_lock=self._folder_lock.acquire(False)
            if not save_lock or not export_lock:raise UpdateError('正在保存文件，请完成后再更新')
            safety=self._window.evaluate_js('window.dialogueUpdateSafety()')
            if isinstance(safety,str):safety=json.loads(safety)
            if not isinstance(safety,dict) or any(type(safety.get(k)) is not bool for k in ('busy','unsaved')) or safety['busy'] or safety['unsaved']:raise UpdateError('请先完成当前任务并保存所有音频结果，再更新')
            server=self._update_server
            with server.update_lock:
                backend=workspace_safety(server)
                if backend is None or any(backend.values()):raise UpdateError('后台工作区仍有任务、保存或未保存音频，请先处理后再更新')
                with server.jobs.transaction(),server.batches.transaction():
                    job_running,batch_running=server.jobs.has_running(),server.batches.has_running()
                    if type(job_running) is not bool or type(batch_running) is not bool:raise UpdateError('无法确认合成任务状态')
                    if job_running or batch_running:raise UpdateError('合成任务尚未结束')
                server.update_in_progress=True;gated=True
            self._window.evaluate_js('window.dialogueUpdateLock(true)')
            version=self._updater.offer.get('version') if self._updater.offer else ''
            if not version:raise UpdateError('请先检查更新')
            if not self._window.create_confirmation_dialog('更新对白',f'安装 {version}？下载并核验后应用将重启。输入、结果文件夹和音色设置会保留。系统可能要求确认。'):
                return {'state':'cancelled'}
            self._updater.prepare_confirmed()
            self._updater.start_helper([os.getpid()])
            # destroy triggers normal event loop exit; no terminate/kill.
            threading.Timer(.5,self._window.destroy).start()
            return {'state':'waiting_for_exit','message':'校验完成，即将重启应用'}
        except Exception as exc:return {'state':'error','message':str(exc)}
        finally:
            if not self._updater or self._updater.state.get('state')!='waiting_for_exit':
                if gated:self._update_server.update_in_progress=False
                try:self._window.evaluate_js('window.dialogueUpdateLock(false)')
                except Exception:pass
            if export_lock:self._folder_lock.release()
            if save_lock:self._lock.release()
            self._update_gate.release()
