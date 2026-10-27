"""Native Mac service command modes; arguments come from AppKit, never webpage."""
import json, sys
from pathlib import Path
from .safe_updater import fetch_release, release_offer, UpdateError
from .updater_bridge import Updater

def state_directory():
    import os
    return (Path.home()/'Library/Application Support/DialogueTTS' if sys.platform=='darwin' else Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'AppData/Local')))/'DialogueTTS')

def dispatch(args):
    if not args or args[0] not in ('--apply-update','--ack-update','--update-check','--update-install','--cancel-update'):return False
    try:
        if args[0]=='--apply-update' and len(args)==2:
            from .update_helper import main
            result=main(args[1])
        elif args[0]=='--ack-update' and len(args)==3:
            from .update_helper import acknowledge_receipt
            result={'ok':acknowledge_receipt(args[1],args[2])}
        elif args[0]=='--cancel-update' and len(args)==3:
            from .update_cancel import cancel_transaction
            result=cancel_transaction(args[1],args[2])
        elif args[0]=='--update-check' and len(args)==2:
            offer=release_offer(fetch_release(),args[1],sys.platform)
            result={'state':offer['state'],'current_version':args[1],'latest_version':offer.get('version',args[1])}
        elif args[0]=='--update-install' and len(args)==6:
            # target, installed version, displayed version, GUI PID, service PID
            updater=Updater(args[1],args[2],state_directory());result=updater.check()
            if result.get('latest_version')!=args[3]:raise UpdateError('版本已变化，请重新检查更新')
            updater.prepare_confirmed();result=updater.start_helper([int(args[4]),int(args[5])])
        else:raise UpdateError('Invalid updater mode')
        print(json.dumps(result,ensure_ascii=False),flush=True)
    except Exception as exc:
        print(json.dumps({'state':'error','message':str(exc)},ensure_ascii=False),flush=True)
    return True
