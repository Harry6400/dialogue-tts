"""Runtime resources and dynamic service entry points used by both builds."""

SERVICE_HIDDEN_IMPORTS = (
    'web_dialogue.worker', 'web_dialogue.mp3_frames', 'web_dialogue.xml_pair',
    'web_dialogue.updates', 'web_dialogue.fingerprints', 'web_dialogue.safe_updater',
    'web_dialogue.gitee_provider', 'web_dialogue.update_sources',
    'web_dialogue.update_helper', 'web_dialogue.update_cancel',
    'web_dialogue.updater_bridge', 'web_dialogue.updater_cli',
    'web_dialogue.install_lock',
)


def service_data(source, frontend=None):
    return [(str(source / 'web_dialogue' / 'index.html'), 'web_dialogue'),
            (str(source / 'web_dialogue' / 'silence.mp3'), 'web_dialogue'),
            (str(frontend or source / 'frontend' / 'dist'), 'frontend/dist')]
