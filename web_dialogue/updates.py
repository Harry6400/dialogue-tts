"""Read-only update discovery from this app's one fixed public repository."""
import json
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

CURRENT_VERSION = "0.4.2"
MAC_BUILD_NUMBER = "11"
GITHUB_OWNER = "Harry6400"
GITHUB_REPO = "dialogue-tts"
MAC_ASSET_NAME = "DialogueTTS-macOS-arm64.zip"
WINDOWS_ASSET_NAME = "DialogueTTS-Windows-x64.zip"
RELEASES_URL = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases"
LATEST_API_URL = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def version_tuple(value):
    if not isinstance(value, str):
        raise ValueError("Invalid release version")
    match = re.fullmatch(r"v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:\+[0-9A-Za-z.-]+)?", value)
    if not match:
        raise ValueError("Only stable semantic versions are accepted")
    return tuple(int(part) for part in match.groups())


def allowed_release_url(value, asset=False):
    if not isinstance(value, str):
        return False
    url = urllib.parse.urlsplit(value)
    prefix = f"/{GITHUB_OWNER}/{GITHUB_REPO}/releases/" + ("download/" if asset else "tag/")
    decoded = urllib.parse.unquote(url.path)
    return (url.scheme == "https" and url.netloc == "github.com" and not url.username and not url.password
            and not url.query and not url.fragment and decoded.startswith(prefix)
            and ".." not in decoded.split("/") and "\\" not in decoded)


def evaluate_release(payload):
    result = {"current_version": CURRENT_VERSION, "state": "latest", "message": "已是最新版本"}
    if not isinstance(payload, dict) or payload.get("draft") is True or payload.get("prerelease") is True:
        raise ValueError("Not a stable release")
    tag = payload.get("tag_name")
    newer = version_tuple(tag) > version_tuple(CURRENT_VERSION)
    result["latest_version"] = tag.lstrip("v")
    if newer:
        link = payload.get("html_url")
        if not allowed_release_url(link):
            raise ValueError("Release URL does not belong to the fixed repository")
        result.update(state="available", release_url=link, message="发现新版本，下载后替换应用")
        assets = payload.get("assets", [])
        if not isinstance(assets, list):
            raise ValueError("Invalid assets")
        for asset in assets:
            if isinstance(asset, dict) and asset.get("name") == (WINDOWS_ASSET_NAME if sys.platform == "win32" else MAC_ASSET_NAME):
                candidate = asset.get("browser_download_url")
                if allowed_release_url(candidate, asset=True):
                    result["download_url"] = candidate
                break
    return result


def check_update():
    try:
        from .update_sources import discover
        result=discover(CURRENT_VERSION,sys.platform)
        result.update(current_version=CURRENT_VERSION,latest_version=result.get('version',CURRENT_VERSION))
        result['message']='发现新版本' if result['state']=='available' else '已是最新版本'
        return result
    except Exception:
        return {'current_version':CURRENT_VERSION,'state':'error','message':'检查失败，请稍后重试'}
