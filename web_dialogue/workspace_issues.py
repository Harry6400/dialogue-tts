"""Bounded issue categories from persisted item status and real error codes."""
from .normalize import InputError

ERROR_STATUSES = ('invalid', 'synthesis_error', 'save_error', 'conflict', 'changed')
ERROR_WHERE = "status IN ('invalid','synthesis_error','save_error','conflict','changed')"
ISSUE_CODES = {
    'pairing': ('missing_track', 'duplicate_track'),
    'input': ('invalid_xml', 'invalid_content', 'invalid_filename'),
    'changed': ('input_changed',),
    'conflict': ('duplicate_filename', 'output_conflict'),
    'synthesis': ('synthesis_failed', 'missing_cache'),
    'save': ('save_failed', 'cache_changed'),
}
ISSUE_KEYS = (*ISSUE_CODES, 'other')
KNOWN_CODES = tuple(code for codes in ISSUE_CODES.values() for code in codes)


def normalize_issue(issue=''):
    if not isinstance(issue, str) or issue not in ('', 'all', *ISSUE_KEYS):
        raise InputError('异常分类参数无效。')
    return '' if issue == 'all' else issue


def issue_where(issue=''):
    issue = normalize_issue(issue)
    if not issue:
        return '1', ()
    codes = KNOWN_CODES if issue == 'other' else ISSUE_CODES[issue]
    placeholders = ','.join('?' for _ in codes)
    code_test = ('(error_code IS NULL OR error_code NOT IN (' + placeholders + '))') if issue == 'other' else ('error_code IN (' + placeholders + ')')
    return ERROR_WHERE + ' AND ' + code_test, codes


def issue_counts(db, ident):
    # CASE reduces the result to seven rows even if an older/unknown code is
    # present. No item list, raw XML, or dialogue body is materialized here.
    cases, args = [], []
    for key, codes in ISSUE_CODES.items():
        cases.append('WHEN error_code IN (' + ','.join('?' for _ in codes) + ') THEN ?')
        args.extend((*codes, key))
    classification = 'CASE ' + ' '.join(cases) + " ELSE 'other' END"
    counts = dict.fromkeys(ISSUE_KEYS, 0)
    for key, total in db.execute('SELECT ' + classification + ' AS issue,count(*) FROM items WHERE wid=? AND ' + ERROR_WHERE + ' GROUP BY issue', (*args, ident)):
        counts[key] = total
    return {'total': sum(counts.values()), **counts}
