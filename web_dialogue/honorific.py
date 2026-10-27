"""Conservative direct-address detection for fictional or user-supplied dialogue.

No inference of real-world gender: only a clear form of address can select a
voice presentation. Ambiguous or quoted uses preserve the caller's choice.
"""
import re

_QUOTED = re.compile(r'“[^”]*”|「[^」]*」|『[^』]*』|\"[^\"]*\"|‘[^’]*’')
_DIRECT = re.compile(r'(?:^|[，,。！？!?；;])\s*(?:您好|你好|请问)?\s*(女士|先生)\s*(?:[，,。！？!?：:]|您好|你好|请问)')
_OTHER = re.compile(r'(?:这位|那位|一位|某位|该|他是|她是|称为|叫做|称呼为)\s*(?:女士|先生)')

def direct_address(rows):
    """Return female/male/undetermined, ignoring muted/non-AGENT content."""
    found = set()
    for row in rows:
        if str(row.get('speaker', '')).strip().upper() != 'AGENT' or row.get('muted'):
            continue
        text = str(row.get('text', ''))
        if any(ch in text for ch in ('\"', "'", '“', '”', '‘', '’', '「', '」', '『', '』')) and re.search(r'女士|先生', text):
            return 'undetermined'
        if re.search(r'(?:说|称呼|称作|转告|告诉|提到|询问)[^。！？!?；;]*(?:女士|先生)', text):
            return 'undetermined'
        quoted = _QUOTED.findall(text)
        if any('女士' in quote or '先生' in quote for quote in quoted) or _OTHER.search(text):
            return 'undetermined'
        text = _QUOTED.sub('', text)
        direct = _DIRECT.findall(text)
        if len(direct) != len(re.findall(r'女士|先生', text)):
            return 'undetermined'
        found.update(direct)
    if found == {'女士'}: return 'female'
    if found == {'先生'}: return 'male'
    return 'undetermined'
