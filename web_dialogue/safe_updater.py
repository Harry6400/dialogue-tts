"""Fail-closed updater primitives. Never touches preferences or output directories."""
from __future__ import annotations
import hashlib, json, os, platform, plistlib, re, shutil, ssl, stat, subprocess, sys, time, urllib.parse, urllib.request, zipfile, uuid, tempfile
from pathlib import Path, PurePosixPath
from .updates import version_tuple, LATEST_API_URL

REPO = 'Harry6400/dialogue-tts'
BUNDLE_ID = 'io.github.Harry6400.dialoguetts'
ASSETS = {'darwin': 'DialogueTTS-macOS-arm64.zip', 'win32': 'DialogueTTS-Windows-x64.zip'}
MAX_ARCHIVE = 128 * 1024 * 1024
MAX_EXPANDED = 512 * 1024 * 1024

class UpdateError(ValueError): pass

def _url_ok(url):
    u = urllib.parse.urlsplit(url)
    if u.scheme != 'https' or u.username or u.password or u.port not in (None,443) or u.fragment: return False
    if u.hostname == 'github.com':
        return u.path.startswith('/' + REPO + '/releases/download/') and not u.query and '..' not in urllib.parse.unquote(u.path).split('/')
    return u.hostname in ('release-assets.githubusercontent.com', 'objects.githubusercontent.com')

class AssetRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _url_ok(newurl): raise UpdateError('Unexpected update redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def opener():
    import certifi
    ctx = ssl.create_default_context(); ctx.load_verify_locations(certifi.where())
    return urllib.request.build_opener(AssetRedirect(), urllib.request.HTTPSHandler(context=ctx))

def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda:f.read(1024*1024), b''): h.update(block)
    return h.hexdigest()

def fetch_release(tag=None):
    if tag is not None and (not isinstance(tag, str) or not re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+', tag)):
        raise UpdateError('Invalid release tag')
    url = LATEST_API_URL if tag is None else f'https://api.github.com/repos/{REPO}/releases/tags/{tag}'
    req = urllib.request.Request(url, headers={'User-Agent':'DialogueTTS-Updater','Accept':'application/vnd.github+json'})
    # API must not redirect to arbitrary endpoints.
    from .updates import NoRedirect
    import certifi
    ctx=ssl.create_default_context();ctx.load_verify_locations(certifi.where())
    with urllib.request.build_opener(NoRedirect(),urllib.request.HTTPSHandler(context=ctx)).open(req,timeout=15) as r:
        data=r.read(1024*1024+1)
    if len(data)>1024*1024: raise UpdateError('Release metadata too large')
    return json.loads(data)

def release_offer(payload, current, target_platform):
    if target_platform not in ASSETS: raise UpdateError('Unsupported platform')
    if payload.get('draft') is not False or payload.get('prerelease') is not False: raise UpdateError('Not a stable release')
    tag=payload.get('tag_name',''); version=tag.removeprefix('v')
    if version_tuple(version)<=version_tuple(current): return {'state':'latest','current_version':current}
    name=ASSETS[target_platform]
    assets=payload.get('assets',[])
    selected=[]
    for filename in (name,name+'.sha256'):
        matches=[a for a in assets if a.get('name')==filename]
        if len(matches)!=1: raise UpdateError('Missing or ambiguous platform artifact')
        a=matches[0]
        expected=f'https://github.com/{REPO}/releases/download/{tag}/{filename}'
        if a.get('browser_download_url')!=expected: raise UpdateError('Unexpected artifact URL')
        digest=a.get('digest','')
        if not isinstance(digest,str) or not re.fullmatch(r'sha256:[0-9a-f]{64}',digest): raise UpdateError('GitHub asset SHA256 required')
        size=a.get('size')
        if type(size) is not int or not 0<size<=(MAX_ARCHIVE if filename==name else 4096): raise UpdateError('Unexpected artifact size')
        selected.append({'name':filename,'url':expected,'sha256':digest[7:],'size':size})
    return {'state':'available','version':version,'platform':target_platform,'archive':selected[0],'checksum':selected[1]}

def download(asset, destination, progress=None):
    if not _url_ok(asset['url']): raise UpdateError('Unsafe URL')
    total=0; h=hashlib.sha256();deadline=time.monotonic()+600
    try:
        with opener().open(urllib.request.Request(asset['url'],headers={'User-Agent':'DialogueTTS-Updater'}),timeout=30) as r, open(destination,'xb') as f:
            while True:
                if time.monotonic()>deadline:raise UpdateError('Update download exceeded ten minutes')
                block=r.read1(256*1024)
                if not block: break
                total+=len(block)
                if total>asset['size']: raise UpdateError('Artifact exceeds advertised size')
                f.write(block);h.update(block)
                if progress: progress(total,asset['size'])
            f.flush();os.fsync(f.fileno())
        if total!=asset['size'] or h.hexdigest()!=asset['sha256']: raise UpdateError('Artifact SHA256/size mismatch')
    except BaseException:
        Path(destination).unlink(missing_ok=True);raise

def extract_safe(archive, destination, target_platform):
    """Validate all names/links before writing; links created only after files."""
    root=Path(destination)
    if root.exists(): raise UpdateError('Extraction destination exists')
    root.mkdir(mode=0o700)
    root=root.resolve(strict=True)  # macOS /var and /tmp may be aliases of /private/...
    prefix=ASSETS[target_platform][:-4]
    try:
        with zipfile.ZipFile(archive) as z:
            infos=z.infolist(); names={}; links=[]; total=0
            if len(infos)>20000: raise UpdateError('Too many archive entries')
            for info in infos:
                n=info.filename
                p=PurePosixPath(n)
                # ditto adds __MACOSX metadata; it is neither needed nor executed.
                if p.parts and p.parts[0]=='__MACOSX': continue
                if not p.parts or p.parts[0]!=prefix or p.is_absolute() or any(x in ('..','') for x in p.parts) or '\\' in n or ':' in n or '\x00' in n: raise UpdateError('Unsafe archive path')
                if any(x.endswith((' ','.')) for x in p.parts): raise UpdateError('Ambiguous archive path')
                if target_platform=='win32' and any(re.search(r'[<>"|?*\x00-\x1f]',x) or re.fullmatch(r'(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?',x,re.I) for x in p.parts): raise UpdateError('Windows reserved path')
                key=str(p).casefold()
                if key in names: raise UpdateError('Duplicate archive path')
                names[key]=info
                total+=info.file_size
                if total>MAX_EXPANDED or info.file_size>MAX_ARCHIVE or (info.file_size>1024*1024 and info.file_size>max(info.compress_size,1)*250): raise UpdateError('Archive expansion limit')
                mode=info.external_attr>>16; kind=stat.S_IFMT(mode)
                if kind not in (0,stat.S_IFREG,stat.S_IFDIR,stat.S_IFLNK): raise UpdateError('Special file forbidden')
                if stat.S_ISLNK(mode):
                    if target_platform!='darwin' or info.file_size>4096: raise UpdateError('Invalid symlink')
                    target=z.read(info).decode('utf-8')
                    if target.startswith('/') or '\\' in target: raise UpdateError('Absolute symlink')
                    resolved=os.path.normpath(str(p.parent/target))
                    app_prefix=prefix+'/对白.app/'
                    if not resolved.startswith(app_prefix) or resolved==str(p): raise UpdateError('Symlink escapes app')
                    links.append((p,target))
            link_paths={str(p) for p,_ in links}
            for info in names.values():
                p=PurePosixPath(info.filename)
                if any(str(parent) in link_paths for parent in p.parents): raise UpdateError('Entry under symlink')
                if str(p) in link_paths: continue
                out=root.joinpath(*p.parts)
                if info.is_dir(): out.mkdir(parents=True,exist_ok=True); continue
                out.parent.mkdir(parents=True,exist_ok=True)
                with z.open(info) as src, open(out,'xb') as dst: shutil.copyfileobj(src,dst,1024*1024)
                out.chmod(0o755 if (info.external_attr>>16)&0o111 else 0o644)
            for p,target in links:
                out=root.joinpath(*p.parts);out.parent.mkdir(parents=True,exist_ok=True);out.symlink_to(target)
            for p,_ in links:
                out=root.joinpath(*p.parts)
                try: resolved=out.resolve(strict=True)
                except (RuntimeError,OSError) as e: raise UpdateError('Broken/cyclic symlink') from e
                if not resolved.is_relative_to(root/prefix/'对白.app'): raise UpdateError('Escaping symlink chain')
        return root/prefix/('对白.app' if target_platform=='darwin' else 'DialogueTTS.exe')
    except BaseException:
        shutil.rmtree(root);raise

def validate_payload(path, version, target_platform):
    path=Path(path)
    if target_platform=='darwin':
        with (path/'Contents/Info.plist').open('rb') as f: info=plistlib.load(f)
        if info.get('CFBundleIdentifier')!=BUNDLE_ID or info.get('CFBundleShortVersionString')!=version or info.get('CFBundleExecutable')!='DialogueTTS': raise UpdateError('Wrong bundle identity/version')
        subprocess.run(['/usr/bin/codesign','--verify','--deep','--strict',str(path)],check=True,capture_output=True,timeout=60)
        arch=subprocess.check_output(['/usr/bin/lipo','-archs',str(path/'Contents/MacOS/DialogueTTS')],text=True,timeout=10).split()
        if 'arm64' not in arch: raise UpdateError('Wrong Mac architecture')
    elif target_platform=='win32':
        validate_windows_version(path, version)
    else: raise UpdateError('Unsupported platform')

def validate_windows_version(path, version):
    import ctypes, struct
    # PE machine + version resource; never execute unverified downloaded code.
    with open(path,'rb') as f:
        if f.read(2)!=b'MZ': raise UpdateError('Not PE')
        f.seek(0x3c); offset=struct.unpack('<I',f.read(4))[0]; f.seek(offset)
        if f.read(6)!=b'PE\x00\x00d\x86': raise UpdateError('Not x64 PE')
    v=ctypes.WinDLL('version',use_last_error=True)
    v.GetFileVersionInfoSizeW.argtypes=[ctypes.c_wchar_p,ctypes.POINTER(ctypes.c_uint32)]
    v.GetFileVersionInfoSizeW.restype=ctypes.c_uint32
    v.GetFileVersionInfoW.argtypes=[ctypes.c_wchar_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p]
    v.GetFileVersionInfoW.restype=ctypes.c_int
    v.VerQueryValueW.argtypes=[ctypes.c_void_p,ctypes.c_wchar_p,ctypes.POINTER(ctypes.c_void_p),ctypes.POINTER(ctypes.c_uint)]
    v.VerQueryValueW.restype=ctypes.c_int
    size=v.GetFileVersionInfoSizeW(str(path),None)
    if not size or size>1024*1024: raise UpdateError('Missing version resource')
    buf=ctypes.create_string_buffer(size)
    if not v.GetFileVersionInfoW(str(path),0,size,buf): raise UpdateError('Unreadable version')
    def query(key):
        ptr=ctypes.c_void_p(); length=ctypes.c_uint()
        if not v.VerQueryValueW(buf,'\\StringFileInfo\\040904B0\\'+key,ctypes.byref(ptr),ctypes.byref(length)): raise UpdateError('Missing identity field')
        return ctypes.wstring_at(ptr.value,length.value).rstrip('\0')
    if query('ProductName')!='DialogueTTS' or query('InternalName')!='DialogueTTS' or query('FileVersion')!=version: raise UpdateError('Wrong Windows product/version')

def prepare(offer, stage, progress=None):
    stage=Path(stage);stage.mkdir(mode=0o700,parents=True,exist_ok=False)
    archive=stage/offer['archive']['name']; checksum=stage/offer['checksum']['name']
    if offer.get('source')=='gitee':
        from . import gitee_provider as g
        try: g.download_verified(g.make_opener(),offer,archive,progress)
        except g.NetworkError:
            from .update_sources import matching_github
            alternate=matching_github(offer)
            download(alternate['archive'],archive,progress);download(alternate['checksum'],checksum)
            expected=alternate['archive']['sha256']+'  '+alternate['archive']['name']
            if checksum.read_text('ascii').strip()!=expected: raise UpdateError('Checksum file disagrees with GitHub digest')
    else:
        download(offer['archive'],archive,progress);download(offer['checksum'],checksum)
        expected=offer['archive']['sha256']+'  '+offer['archive']['name']
        if checksum.read_text('ascii').strip()!=expected: raise UpdateError('Checksum file disagrees with GitHub digest')
    payload=extract_safe(archive,stage/'extracted',offer['platform'])
    validate_payload(payload,offer['version'],offer['platform'])
    if offer['platform']=='darwin':
        # Mark internet-origin code; never remove quarantine or bypass Gatekeeper.
        os.setxattr(payload,'com.apple.quarantine',f'0083;{int(time.time()):x};DialogueTTS;'.encode())
    return payload

def replace_transaction(target, staged, launch_and_verify):
    """Replace only app/exe; use a private same-filesystem incoming directory."""
    target=Path(target);staged=Path(staged)
    if target.is_symlink() or not target.exists(): raise UpdateError('Installed target is missing or symlink')
    backup=target.with_name(target.name+'.update-backup-'+uuid.uuid4().hex)
    if backup.exists() or backup.is_symlink(): raise UpdateError('Backup collision')
    work=Path(tempfile.mkdtemp(prefix='.'+target.name+'.update-',dir=target.parent))
    incoming=work/target.name
    moved_old=False
    try:
        if staged.is_dir(): shutil.copytree(staged,incoming,symlinks=True)
        else:
            with open(staged,'rb') as src,open(incoming,'xb') as dst:
                shutil.copyfileobj(src,dst,1024*1024);dst.flush();os.fsync(dst.fileno())
            shutil.copystat(staged,incoming)
            if sys.platform=='win32':
                with open(str(incoming)+':Zone.Identifier','w',encoding='ascii') as zone:
                    zone.write('[ZoneTransfer]\nZoneId=3\nHostUrl=https://github.com/'+REPO+'/releases\n')
        target.rename(backup);moved_old=True
        incoming.rename(target)
        launch_and_verify(target)
    except BaseException as exc:
        if type(exc).__name__ == 'RecoveryRequired': raise
        if moved_old:
            if target.is_symlink(): raise UpdateError('Replacement target changed; backup retained') from exc
            if target.exists():
                if target.is_dir(): shutil.rmtree(target)
                else: target.unlink()
            backup.rename(target)
        raise
    finally:
        shutil.rmtree(work,ignore_errors=True)
    return backup
