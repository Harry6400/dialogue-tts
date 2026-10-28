import {useCallback, useRef, useState} from 'react';
import {messageOf, type WorkspaceModel} from './useWorkspace';

type UpdateState = 'idle' | 'checking' | 'latest' | 'available' | 'preparing' | 'waiting_for_exit' | 'cancelled' | 'error';
export interface UpdateFeedback {state: UpdateState; message: string}

export function useApplicationUpdate(model: WorkspaceModel, busy: boolean, running: boolean) {
  const [feedback, setFeedback] = useState<UpdateFeedback>({state: 'idle', message: ''});
  const [availableVersion, setAvailableVersion] = useState<string | null>(null);
  const inFlight = useRef(false);

  const request = useCallback(async (install: boolean) => {
    if (inFlight.current) return;
    if (busy || running) {
      setFeedback({state: 'error', message: running ? '请先暂停当前任务，再检查更新。' : '请等待当前操作完成后再检查更新。'});
      return;
    }
    if (!model.native.current) {
      setFeedback({state: 'error', message: '请在对白桌面应用中检查更新。'});
      return;
    }
    // Only a successful discovery exposes installation. Native code still owns
    // its confirmation, offered identity and every global installation gate.
    if (install && !availableVersion) return;
    inFlight.current = true;
    if (!install) setAvailableVersion(null);
    setFeedback({state: install ? 'preparing' : 'checking', message: install ? '正在准备更新…' : '正在检查正式版本…'});
    try {
      let started = false;
      await model.perform({kind: install ? 'update_install' : 'update_check', label: install ? '准备更新' : '检查更新'}, async () => {
        started = true;
        try {
          const outcome = await model.native.current!.call(install ? 'install_update' : 'check_for_update', {});
          if (outcome.kind === 'unsupported') {setFeedback({state: 'error', message: '请在对白桌面应用中检查更新。'}); return;}
          const result = model.nativeValue(outcome);
          if (!result) {setFeedback({state: 'cancelled', message: '已取消更新，当前版本保持不变。'}); return;}
          if (result.state === 'available') {
            setAvailableVersion(result.latest_version);
            setFeedback({state: 'available', message: '发现新版本 ' + result.latest_version});
          } else if (result.state === 'latest') {
            setAvailableVersion(null);
            setFeedback({state: 'latest', message: '当前已是最新版本'});
          } else if (result.state === 'cancelled') {
            setFeedback({state: 'cancelled', message: result.message || '已取消更新，当前版本保持不变。'});
          } else if (result.state === 'waiting_for_exit') {
            setFeedback({state: 'waiting_for_exit', message: result.message || '校验完成，正在退出并重启…'});
          } else {
            setFeedback({state: 'error', message: ('message' in result && result.message) || '检查更新失败，请重试。'});
          }
        } catch (issue) {setFeedback({state: 'error', message: messageOf(issue)});}
      });
      if (!started) setFeedback({state: 'error', message: '当前还有操作未完成，请稍后重试。'});
    } catch (issue) {setFeedback({state: 'error', message: messageOf(issue)});}
    finally {inFlight.current = false;}
  }, [availableVersion, busy, model, running]);

  return {feedback, availableVersion, pending: feedback.state === 'checking' || feedback.state === 'preparing', check: useCallback(() => request(false), [request]), install: useCallback(() => request(true), [request])};
}

export type ApplicationUpdate = ReturnType<typeof useApplicationUpdate>;
