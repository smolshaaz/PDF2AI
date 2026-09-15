"""Read-only inspection of hidden text on image pages; needs only PyMuPDF.

Usage: python scripts/diagnose_ocr_layers.py <pdf-or-folder> [...]
Reports potential pre-existing OCR layers, not an accuracy assessment.
"""
from pathlib import Path
import sys

import pymupdf


def check_pdf(path):
    hidden_only, mixed = [], []
    with pymupdf.open(path) as document:
        if document.needs_pass:
            raise ValueError('Password-protected PDF')
        for number, page in enumerate(document, 1):
            if not page.get_images():
                continue
            hidden = visible = False
            for block in page.get_text('dict', flags=pymupdf.TEXT_ACCURATE_BBOXES)['blocks']:
                for line in block.get('lines', ()):
                    for span in line['spans']:
                        if not span['text'].strip():
                            continue
                        if span['char_flags'] & (16 | 32):
                            visible = True
                        else:
                            hidden = True
            if hidden:
                (mixed if visible else hidden_only).append(number)
    return hidden_only, mixed


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print('Usage: python scripts/diagnose_ocr_layers.py <pdf-or-folder> [...]')
        return 1
    paths = []
    for arg in args:
        path = Path(arg)
        paths.extend(sorted(p for p in path.rglob('*') if p.suffix.lower() == '.pdf')
                     if path.is_dir() else [path])
    count = failures = 0
    for path in dict.fromkeys(p.resolve() for p in paths):
        try:
            hidden, mixed = check_pdf(path)
            count += len(hidden) + len(mixed)
            if hidden or mixed:
                print(f'[HIDDEN TEXT] {path.name}: hidden-only pages {hidden}; mixed visible/hidden pages {mixed}')
            else:
                print(f'[NONE DETECTED] {path.name}: no hidden text found on image pages')
        except Exception as exc:
            failures += 1
            # Do not expose library messages that may contain document text.
            print(f'[UNREADABLE] {path.name}: {type(exc).__name__}')
    print(f'{count} image page(s) contain hidden text. This does not establish that the text is incorrect.')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
