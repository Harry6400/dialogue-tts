"""Shared, fail-closed native exit decision. Prompts belong to the platform shell."""
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
    if running or state['running']:
        return 'exit' if prompt('running') == 'discard' else 'cancel'
    return 'exit'
