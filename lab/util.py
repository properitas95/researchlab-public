"""Small readers and HTML pieces shared by the page generators and stages."""
import html
import json


def esc(value):
    return html.escape(str(value if value is not None else ''), quote=True)


def text(p):
    try: return p.read_text(encoding='utf-8')
    except (OSError, UnicodeError): return ''


def load(p, default=None):
    try: return json.loads(text(p))
    except ValueError: return {} if default is None else default


def lines(p):
    rows = []
    for line in text(p).splitlines():
        try: row = json.loads(line)
        except ValueError: continue
        if isinstance(row, dict): rows.append(row)
    return rows


def table(head, rows):
    return '<div class="scroll"><table><thead><tr>' + ''.join(f'<th>{esc(h)}</th>' for h in head) + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join(f'<td>{esc(c)}</td>' for c in row) + '</tr>' for row in rows) + '</tbody></table></div>'


def read(p):
    return p.read_text() if p.exists() else "(missing)"
