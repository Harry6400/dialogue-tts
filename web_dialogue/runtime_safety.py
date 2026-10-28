"""Combine live workspace and CSV activity without granting filesystem access."""


def combined_safety(workspaces=None, reports=None):
    try:
        state = workspaces.safety() if workspaces is not None else dict(busy=False, unsaved=False, saving=False, pending=False)
        fields = ('busy', 'unsaved', 'saving', 'pending')
        if not isinstance(state, dict) or any(type(state.get(key)) is not bool for key in fields):
            raise ValueError('Unknown workspace safety')
        result = dict(state)
        if reports is not None:
            exports = reports.safety()
            if not isinstance(exports, dict) or any(type(exports.get(key)) is not bool for key in ('busy', 'saving', 'pending')):
                raise ValueError('Unknown report safety')
            for key in ('busy', 'saving', 'pending'):
                result[key] = result[key] or exports[key]
        return result
    except Exception:
        return dict(busy=True, unsaved=True, saving=True, pending=True, unknown=True)
