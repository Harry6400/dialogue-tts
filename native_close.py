"""Shared, fail-closed native exit decision. Prompts belong to the platform shell."""
def workspace_safety(server):
    """Read every workspace; an unavailable/malformed manager is never idle."""
    from web_dialogue.runtime_safety import combined_safety
    state = combined_safety(getattr(server, 'workspaces', None), getattr(server, 'reports', None))
    if state.get('unknown') is True: return None
    return {key: state[key] for key in ('busy', 'unsaved', 'saving', 'pending')}


def merge_close_safety(page, backend):
    if not isinstance(page, dict) or any(type(page.get(k)) is not bool for k in ('running', 'saving', 'pending', 'unsaved')):
        return None
    if not isinstance(backend, dict) or any(type(backend.get(k)) is not bool for k in ('busy', 'unsaved', 'saving', 'pending')):
        return None
    return {**page, 'running': page['running'] or backend['busy'],
            'saving': page['saving'] or backend['saving'], 'pending': page['pending'] or backend['pending'],
            # Page unsaved retains legacy manual-save protection; durable workspace
            # audio requires a preserve-and-exit confirmation, never a discard prompt.
            'persistent_unsaved': backend['unsaved'],
            'persistent_workspace': page.get('persistent_workspace') is True or backend['busy'] or backend['unsaved']}


def close_decision(state, *, running=False, saving=False, prompt):
    fields = ('running', 'saving', 'pending', 'unsaved')
    if not isinstance(state, dict) or any(type(state.get(k)) is not bool for k in fields):
        prompt('unknown')
        return 'cancel'
    if saving or state['saving'] or state['pending']:
        prompt('saving')
        return 'cancel'
    if state['unsaved']:
        answer = prompt('unsaved')
        return 'save' if answer == 'save' else 'exit' if answer == 'discard' else 'cancel'
    if state.get('persistent_unsaved') is True:
        return 'exit' if prompt('persistent') == 'preserve' else 'cancel'
    if running or state['running']:
        return 'exit' if prompt('running') == 'discard' else 'cancel'
    return 'exit'
