"""Native-only opaque transaction cancellation with an atomic replace boundary."""
import contextlib,json,os,re,sys
from pathlib import Path
from .safe_updater import UpdateError

@contextlib.contextmanager
def transaction_guard(stage):
    path=Path(stage)/'transition.lock'
    with open(path,'a+b') as f:
        if f.seek(0,2)==0:f.write(b'0');f.flush()
        f.seek(0)
        if sys.platform=='win32':
            import msvcrt
            msvcrt.locking(f.fileno(),msvcrt.LK_LOCK,1)
        else:
            import fcntl
            fcntl.flock(f.fileno(),fcntl.LOCK_EX)
        try:yield
        finally:
            if sys.platform=='win32':
                f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(f.fileno(),fcntl.LOCK_UN)

def cancel_transaction(transaction_id,token):
    from .updater_cli import state_directory
    if not isinstance(transaction_id,str) or not re.fullmatch('[0-9a-f]{32}',transaction_id) or not isinstance(token,str) or not re.fullmatch('[0-9a-f]{64}',token):raise UpdateError('Invalid cancellation capability')
    root=(state_directory()/'updates').resolve();stage=root/transaction_id
    if stage.is_symlink() or not stage.is_dir() or stage.resolve().parent!=root:raise UpdateError('Invalid private transaction')
    plan_path=stage/'plan.json'
    if plan_path.is_symlink() or plan_path.stat().st_size>16384:raise UpdateError('Invalid plan')
    plan=json.loads(plan_path.read_text())
    import hmac
    if not hmac.compare_digest(str(plan.get('helper_nonce','')),token):raise UpdateError('Cancellation capability mismatch')
    with transaction_guard(stage):
        # Presence is irreversible boundary. Never claim cancellation after it.
        if (stage/'replacing').exists():return {'state':'too_late','cancelled':False,'message':'替换已开始，不能取消；请等待更新结果'}
        marker=stage/'cancelled'
        if marker.is_symlink():raise UpdateError('Invalid cancellation marker')
        with open(marker,'w') as out:out.write(token);out.flush();os.fsync(out.fileno())
    return {'state':'cancelled','cancelled':True,'transaction_id':transaction_id,'message':'已撤销更新；之后正常退出也不会安装本事务'}

def begin_replacement(stage):
    """Serialize disarm with the final pre-replacement check."""
    with transaction_guard(stage):
        if (Path(stage)/'cancelled').exists():return False
        with open(Path(stage)/'replacing','x') as out:out.write('replacing');out.flush();os.fsync(out.fileno())
    return True
