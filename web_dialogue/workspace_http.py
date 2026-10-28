"""Bounded HTTP adapters for native workspaces; no browser filesystem authority."""
from urllib.parse import parse_qs, urlsplit
import re

from .normalize import InputError


def _safety(work, reports=None):
    from .runtime_safety import combined_safety
    return combined_safety(work, reports)


def _audio(handler,handle,filename,fingerprint,size):
    with handle:
        start,end,status=0,size-1,200
        raw=handler.headers.get('Range')
        if raw:
            match=re.fullmatch(r'bytes=(\d+)-(\d*)',raw)
            if not match:return handler.json({'error':'不支持的字节范围。'},416)
            start=int(match[1]);end=min(int(match[2]) if match[2] else size-1,size-1)
            if start>end or start>=size:return handler.json({'error':'字节范围超出文件。'},416)
            status=206
        handler.headers_for(status,'audio/mpeg',end-start+1)
        handler.send_header('Accept-Ranges','bytes')
        handler.send_header('Content-Disposition',f'inline; filename="{filename}"')
        handler.send_header('X-Dialogue-Fingerprint',fingerprint)
        if status==206:handler.send_header('Content-Range',f'bytes {start}-{end}/{size}')
        handler.end_headers()
        handle.seek(start);remaining=end-start+1
        while remaining:
            data=handle.read(min(65536,remaining))
            if not data:break
            handler.wfile.write(data);remaining-=len(data)


def get_workspace(handler,path):
    if not path.startswith('/api/workspaces'):return False
    work=handler.server.workspaces
    if work is None:
        handler.json({'error':'请使用启用本机工作区的桌面版本。'},404);return True
    parts=path.strip('/').split('/')[2:]
    parameters=parse_qs(urlsplit(handler.path).query,keep_blank_values=True)
    query={k:v[-1] for k,v in parameters.items()}
    try:
        allowed={'q','filter','issue','page','limit','sort'} if len(parts)==1 and parts!=['safety'] else {'q','issue','after_id','limit'} if len(parts)==2 and parts[1]=='next-issue' else {'revision'} if len(parts)==2 and parts[1]=='snapshot' else set()
        if set(query)-allowed or any(len(values)!=1 for values in parameters.values()):raise InputError('工作区查询参数无效。')
        if parts==['safety']:result=_safety(work,getattr(handler.server,'reports',None))
        elif not parts:result=work.list()
        elif len(parts)==1:result=work.page(parts[0],q=query.get('q',''),filter=query.get('filter','all'),page=query.get('page',1),limit=query.get('limit',100),sort=query.get('sort','filename'),issue=query.get('issue',''))
        elif len(parts)==2 and parts[1]=='next-issue':result=work.next_issue(parts[0],q=query.get('q',''),issue=query.get('issue',''),after_id=query.get('after_id'),limit=query.get('limit',100))
        elif len(parts)==2 and parts[1]=='snapshot':result=work.snapshot(parts[0],query.get('revision',0))
        elif len(parts)==3 and parts[1]=='items':result=work.detail(parts[0],parts[2])
        elif len(parts)==4 and parts[1]=='items' and parts[3]=='audio':
            _audio(handler,*work.audio_lease(parts[0],parts[2]));return True
        else:raise KeyError('工作区接口不存在。')
        handler.json(result)
    except KeyError as exc:handler.json({'error':str(exc)},404)
    except InputError as exc:handler.json({'error':str(exc)},400)
    except (ValueError,OSError):handler.json({'error':'工作区读取失败，请核对输入或目录授权。'},409)
    return True


def post_workspace(handler,path,payload):
    work=handler.server.workspaces
    if path.startswith('/api/native/'):
        if work is None:raise InputError('本机工作区未启用。')
        access=handler.server.native_access
        if path=='/api/native/workspaces/safety':
            if payload:raise InputError('安全核验不接受参数。')
            return _safety(work,getattr(handler.server,'reports',None))
        if path=='/api/native/folders':
            if not isinstance(payload.get('path'),str) or payload.get('kind') not in ('input','output'):raise InputError('目录注册参数无效。')
            return access.register(payload.get('path'),payload.get('kind'))
        if path=='/api/native/folders/info':
            if not isinstance(payload.get('folder_id'),str) or payload.get('kind') not in ('input','output'):raise InputError('目录查询参数无效。')
            return access.info(payload.get('folder_id'),payload.get('kind'))
        if path=='/api/native/workspaces/restore':
            return work.restore(payload.get('workspace_id'))
        if path=='/api/native/outputs/check':
            fingerprints=payload.get('fingerprints')
            names=payload.get('expected_names')
            if not isinstance(fingerprints,list) or len(fingerprints)>100 or any(not isinstance(f,str) or not re.fullmatch('[a-f0-9]{64}',f) for f in fingerprints):raise InputError('指纹请求无效。')
            if names is not None and (not isinstance(names,dict) or set(names)!=set(fingerprints)):raise InputError('预期名称无效。')
            completed,conflicts=[],[]
            for fp in fingerprints:
                candidates=[names[fp]] if names is not None else access.names_for_fingerprint(payload.get('folder_id'),fp)
                for name in candidates:
                    result=access.verify_output(payload.get('folder_id'),name,fp)
                    if result['state']=='saved':completed.append(result)
                    elif result['state']=='conflict':conflicts.append({'fingerprint':fp,'name':name,'error':'同名文件已存在且无法核实。'})
            return {'ok':True,'completed':completed,'conflicts':conflicts}
        if path=='/api/native/outputs/save':
            route=payload.get('resource_path','')
            if not isinstance(route,str):raise InputError('输出资源无效。')
            match=re.fullmatch(r'/api/jobs/([a-f0-9]{32})/audio',route)
            if match:
                snapshot=handler.server.jobs.snapshot(match[1])
                if snapshot['status']!='done':raise InputError('音频未完成。')
                source=handler.server.jobs.items[match[1]]['folder']/'dialogue.mp3'
                return {**access.save_audio(payload.get('folder_id'),source,snapshot['filename'],snapshot['fingerprint']),'resource_path':route}
            match=re.fullmatch(r'/api/batches/([a-f0-9]{32})/audio/([0-9]{3})',route)
            if match:
                batches=handler.server.batches
                with batches.lock:
                    snapshot=batches.snapshot(match[1])
                    item=next((i for i in snapshot['items'] if i.get('download_url')==route),None)
                    if item is None:raise InputError('音频未完成。')
                    return {**access.save_audio(payload.get('folder_id'),batches.output(match[1],match[2]),item['filename'],item['fingerprint']),'resource_path':route}
            raise InputError('输出资源无效。')
        raise KeyError('原生接口不存在。')
    if work is None:raise InputError('本机工作区未启用。')
    parts=path.strip('/').split('/')[2:]
    if parts==['import']:return work.import_folder(payload.get('input_folder_id'))
    if len(parts)!=2:raise KeyError('工作区接口不存在。')
    ident,action=parts
    if action=='selection':return work.select(ident,payload)
    if action=='settings':return work.settings(ident,payload)
    if action=='folders':return work.folders(ident,payload)
    if action=='start':return work.start(ident,payload)
    return work.action(ident,action,payload)
