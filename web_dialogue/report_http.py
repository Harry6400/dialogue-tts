"""Small authenticated adapters for metadata-only whole-batch CSV exports.

The HTTP composition root verifies app/native tokens before entering this module.
Only the private native save route can construct a destination capability.
"""
import re

from native_reports import NativeReportDestination
from .normalize import InputError
from .workspace_reports import ReportError


CREATE = re.compile(r'/api/workspaces/([a-f0-9]{32})/report')
DISCARD = re.compile(r'/api/reports/([a-f0-9]{32})/discard')
CANCEL_PENDING = '/api/reports/cancel-pending'
NATIVE = {'/api/native/reports/info', '/api/native/reports/save'}


def is_report_post(path):
    return bool(CREATE.fullmatch(path) or DISCARD.fullmatch(path) or path == CANCEL_PENDING or path.startswith('/api/native/reports/'))


def post_report(server, path, payload):
    reports = getattr(server, 'reports', None)
    if reports is None:
        raise InputError('请使用启用本机工作区的桌面应用导出清单。')
    if not isinstance(payload, dict):
        raise InputError('清单请求必须为对象。')
    match = CREATE.fullmatch(path)
    try:
        if match:
            if payload:
                raise InputError('清单固定导出本批次全部记录，不接受搜索、筛选或保存路径。')
            return reports.create(match[1])
        if path == CANCEL_PENDING:
            if payload:
                raise InputError('取消临时 CSV 清单不接受额外参数。')
            return reports.cancel_pending()
        match = DISCARD.fullmatch(path)
        if match:
            if payload:
                raise InputError('取消清单不接受额外参数。')
            return reports.discard(match[1])
        if path not in NATIVE:
            raise KeyError('清单接口不存在。')
        if path == '/api/native/reports/info':
            if set(payload) != {'report_id'}:
                raise InputError('清单标识请求无效。')
            return reports.info(payload['report_id'])
        if set(payload) != {'report_id', 'path'}:
            raise InputError('清单保存请求无效。')
        destination = NativeReportDestination.from_native_selection(payload['path'])
        return reports.save_to_native_destination(payload['report_id'], destination)
    except ReportError as exc:
        if path in NATIVE:
            return {'ok': False, 'error': str(exc)}
        raise InputError(str(exc)) from exc
    except FileExistsError:
        return {'ok': False, 'conflict': True, 'error': '同名文件已存在，请选择新的 CSV 文件名。'}
    except OSError:
        if path in NATIVE:
            return {'ok': False, 'error': '清单保存失败，请检查可用空间和权限；任务与 MP3 保留。'}
        raise InputError('无法生成清单，请检查本机可用空间和权限。')
    except ValueError as exc:
        if path in NATIVE:
            return {'ok': False, 'error': str(exc)}
        raise
