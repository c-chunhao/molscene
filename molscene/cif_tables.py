"""Bounded CIF1 token/table reader. No backend, evaluation or optional imports."""
from dataclasses import dataclass

MAX_BYTES = 20 * 1024 * 1024
MAX_TOKEN = 1024 * 1024
MAX_TOKENS = 2_000_000
MAX_ROWS = 250_000


@dataclass(frozen=True)
class _Token:
    value: str
    quoted: bool = False


def _tokens(text):
    i, count, size = 0, 0, len(text)
    while i < size:
        c = text[i]
        if c.isspace():
            i += 1
            continue
        if c == '#':
            end = text.find('\n', i)
            i = size if end < 0 else end+1
            continue
        start, quoted = i, False
        if c == ';' and (i == 0 or text[i-1] == '\n'):
            quoted = True
            end = text.find('\n;', i+1)
            if end < 0:
                raise ValueError('Unterminated CIF semicolon text')
            value = text[i+1:end].rstrip('\r\n')
            i = end+2
            if i < size and not text[i].isspace():
                raise ValueError('Unexpected data after CIF text terminator')
        elif c in "'\"":
            quoted = True
            i += 1
            begin = i
            while i < size and not (text[i] == c and (i+1 == size or text[i+1].isspace())):
                if text[i] in '\r\n':
                    raise ValueError('Newline in quoted CIF string')
                i += 1
            if i == size:
                raise ValueError('Unterminated CIF quote')
            value = text[begin:i]
            i += 1
        else:
            while i < size and not text[i].isspace():
                i += 1
            value = text[start:i]
        if i-start > MAX_TOKEN:
            raise ValueError('CIF token exceeds size limit')
        count += 1
        if count > MAX_TOKENS:
            raise ValueError('CIF exceeds token limit')
        yield _Token(value, quoted)


def _control(token):
    word = token.value.lower()
    return not token.quoted and (word.startswith(('_', 'data_', 'save_')) or word in ('loop_', 'stop_', 'global_'))


def read_tables(text, *, categories=None):
    """Read one unambiguous data block; validate even discarded category loops.

    Unquoted dot/question values become None; quoted control words remain data.
    Category and item names are lower case, values retain case and whitespace.
    """
    if not isinstance(text, str) or len(text.encode('utf-8')) > MAX_BYTES or '\x00' in text:
        raise ValueError('CIF must be bounded UTF-8 text without NUL')
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    tokens = iter(_tokens(text)); current = next(tokens, None)
    tables, scalars, seen, blocks, row_count = {}, {}, set(), 0, 0
    def tag(token):
        value = token.value.lower()
        if token.quoted or not value.startswith('_') or value.count('.') != 1:
            raise ValueError('Expected CIF category.item tag')
        category, item = value.split('.')
        if not category[1:] or not item or value in seen:
            raise ValueError('Missing or duplicate CIF tag')
        seen.add(value)
        return category, item
    def value(token):
        return None if not token.quoted and token.value in ('.', '?') else token.value
    while current is not None:
        word = current.value.lower()
        if not current.quoted and word.startswith('data_'):
            blocks += 1
            if blocks != 1 or not word[5:]:
                raise ValueError('CIF requires exactly one named data block')
            current = next(tokens, None)
        elif blocks != 1:
            raise ValueError('CIF data block is required')
        elif not current.quoted and word == 'loop_':
            current = next(tokens, None); columns = []
            while current is not None and not current.quoted and current.value.startswith('_'):
                columns.append(tag(current)); current = next(tokens, None)
            if not columns or len({x[0] for x in columns}) != 1:
                raise ValueError('CIF loop needs one category and at least one tag')
            category = columns[0][0]
            if category in tables or category in scalars:
                raise ValueError('Ambiguous split CIF category')
            keep = categories is None or category in categories
            rows, row, loop_rows = [], [], 0
            while current is not None and not _control(current):
                row.append(value(current)); current = next(tokens, None)
                if len(row) == len(columns):
                    row_count += 1
                    loop_rows += 1
                    if row_count > MAX_ROWS: raise ValueError('CIF exceeds row limit')
                    if keep: rows.append(dict(zip((x[1] for x in columns), row)))
                    row = []
            if row or not loop_rows:
                raise ValueError('Incomplete or empty CIF loop')
            tables[category] = rows if keep else []
            if current is not None and not current.quoted and current.value.lower() == 'stop_':
                current = next(tokens, None)
        elif not current.quoted and word.startswith('_'):
            category, item = tag(current)
            if category in tables: raise ValueError('Ambiguous scalar/loop category')
            current = next(tokens, None)
            if current is None or _control(current): raise ValueError('Missing scalar CIF value')
            scalars.setdefault(category, {})[item] = value(current)
            current = next(tokens, None)
        else:
            raise ValueError('Unsupported or unexpected CIF control/data: '+word[:80])
    if blocks != 1: raise ValueError('CIF data block is required')
    for category, row in scalars.items():
        if categories is None or category in categories: tables[category] = [row]
    return {key: rows for key, rows in tables.items() if categories is None or key in categories}
