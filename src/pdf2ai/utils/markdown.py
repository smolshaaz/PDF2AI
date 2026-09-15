"""Local Markdown rendering. Remote resources and active HTML are never loaded."""
import csv
import html
from html.parser import HTMLParser
import io
import re

from markdown_it import MarkdownIt

PAGE_MARKER = re.compile(r"^<!-- PAGE (\d+) -->\s*$")
CSS = """
body { color: #243143; background-color: white; font-family: 'Segoe UI', sans-serif; font-size: 11pt; }
h1, h2, h3 { color: #18345b; margin-top: 20px; margin-bottom: 10px; }
p { margin-top: 6px; margin-bottom: 12px; line-height: 145%; }
table { border-collapse: collapse; width: 100%; margin-top: 12px; margin-bottom: 14px; }
th { background-color: #eaf0f8; font-weight: bold; }
th, td { border: 1px solid #cbd5e1; padding: 8px; }
pre { background-color: #f1f5f9; padding: 12px; white-space: pre-wrap; }
blockquote { color: #526178; margin-left: 18px; }
"""


class SafeHTML(HTMLParser):
    allowed = set("p br hr h1 h2 h3 h4 h5 h6 strong b em i u s del sub sup pre code blockquote ul ol li table thead tbody tfoot tr th td div span".split())
    blocked = {"script", "style", "iframe", "object", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.suppressed = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.blocked:
            self.suppressed += 1
        if self.suppressed or tag not in self.allowed:
            return
        safe = []
        for key, value in attrs:
            if key in {"colspan", "rowspan"} and value and value.isdigit() and 1 <= int(value) <= 100:
                safe.append(f'{key}="{value}"')
            if key == "dir" and value in {"rtl", "ltr", "auto"}:
                safe.append(f'dir="{value}"')
        self.parts.append("<" + tag + (" " + " ".join(safe) if safe else "") + ">")

    def handle_endtag(self, tag):
        if tag in self.blocked:
            self.suppressed = max(0, self.suppressed - 1)
            return
        if not self.suppressed and tag in self.allowed:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.suppressed:
            self.parts.append(html.escape(data))


def parser():
    return MarkdownIt("commonmark", {"html": True, "breaks": False}).enable("table")


def rendered_html(markdown):
    cleaner = SafeHTML()
    cleaner.feed(parser().render(markdown))
    cleaner.close()
    return '<html><head><meta charset="utf-8"></head><body>' + "".join(cleaner.parts) + "</body></html>"


def iter_pages(path):
    """Stream one source page at a time; preserve the exact text for source view."""
    number, lines = None, []
    # Universal newlines keep the in-memory page text identical to normal
    # UTF-8 reads on Windows as well as macOS/Linux.
    with path.open(encoding="utf-8", newline=None) as stream:
        for line in stream:
            match = PAGE_MARKER.match(line)
            if match:
                if number is not None:
                    yield number, "".join(lines)
                    lines = []
                number = int(match[1])
                lines.append(line)
            else:
                lines.append(line)
        if number is not None:
            yield number, "".join(lines)
        elif lines:
            yield 1, "".join(lines)


def plain_text(markdown):
    """Preserve text and table cells; remove Markdown presentation syntax.

    Tables use tab-separated rows with quoted multiline cells, so column
    relationships are retained. This does not claim a tokenizer-specific saving.
    """
    result, cells, cell, in_table = [], [], [], False
    lists = []

    def inline_text(token):
        parts = []
        for child in token.children or ():
            if child.type in {"text", "code_inline", "image"}:
                parts.append(child.content)
            elif child.type in {"softbreak", "hardbreak"}:
                parts.append("\n")
            elif child.type == "html_inline" and re.match(r"<br\s*/?>", child.content, re.I):
                parts.append("\n")
        return "".join(parts)

    for token in parser().parse(markdown):
        if token.type == "table_open":
            in_table = True
        elif token.type == "table_close":
            in_table = False
            result.append("\n")
        elif token.type == "tr_open":
            cells = []
        elif token.type in {"td_open", "th_open"}:
            cell = []
        elif token.type in {"td_close", "th_close"}:
            cells.append("".join(cell))
        elif token.type == "tr_close":
            stream = io.StringIO()
            csv.writer(stream, delimiter="\t", lineterminator="\n").writerow(cells)
            result.append(stream.getvalue())
        elif token.type in {"ordered_list_open", "bullet_list_open"}:
            lists.append([token.type == "ordered_list_open", int(token.attrGet("start") or 1)])
        elif token.type in {"ordered_list_close", "bullet_list_close"}:
            lists.pop()
        elif token.type == "list_item_open" and lists:
            ordered, number = lists[-1]
            result.append("  " * (len(lists) - 1) + (f"{number}. " if ordered else "- "))
            lists[-1][1] += 1
        elif token.type == "inline":
            if in_table:
                cell.append(inline_text(token))
            else:
                result.append(inline_text(token) + "\n")
        elif token.type in {"fence", "code_block"}:
            result.append(token.content + "\n")
        elif token.type == "html_block" and not token.content.lstrip().startswith("<!--"):
            # Keep raw HTML content rather than silently discard unusual tables.
            result.append(token.content)
    return "".join(result)
