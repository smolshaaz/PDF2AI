"""Structural checks only; these do not assert extraction accuracy."""
import re


def inspect_chunks(chunks: list[dict], page_count: int) -> tuple[list[dict], list[str]]:
    warnings = []
    if len(chunks) != page_count:
        warnings.append(f"Expected {page_count} pages; extraction returned {len(chunks)} page chunks.")
    for chunk in chunks:
        number = chunk.get("metadata", {}).get("page_number")
        if type(number) is not int or not 1 <= number <= page_count:
            raise ValueError("The extraction engine returned invalid page numbers.")
        if not isinstance(chunk.get("text"), str):
            raise ValueError("The extraction engine returned an invalid page text.")
    ordered = sorted(chunks, key=lambda c: c["metadata"]["page_number"])
    numbers = [c["metadata"]["page_number"] for c in ordered]
    missing = sorted(set(range(1, page_count + 1)) - set(numbers))
    if missing:
        warnings.append("No page chunk returned for: " + ", ".join(map(str, missing)))
    if len(numbers) != len(set(numbers)):
        warnings.append("The extraction engine returned duplicate page numbers; all returned text was retained.")
    sparse = []
    for chunk in ordered:
        # Ignore Markdown/HTML decorations when detecting nearly empty pages.
        text = re.sub(r"<!--.*?-->|<[^>]+>|!\[[^\]]*\]\([^)]*\)", "", chunk["text"], flags=re.S)
        if sum(char.isalnum() for char in text) < 10:
            sparse.append(chunk["metadata"]["page_number"])
    if sparse:
        warnings.append(f"{len(sparse)} pages produced little or no extractable text: " + ", ".join(map(str, sparse)))
    return ordered, warnings


def markdown_parts(source_name: str, page_count: int, chunks: list[dict]):
    # A filename must not be allowed to close our metadata comment.
    safe_name = source_name.replace("--", "&#45;&#45;").replace("\n", " ").replace("\r", " ")
    yield f"<!-- PDF2AI\nSource: {safe_name}\nPages: {page_count}\nGenerated locally by PDF2AI\n-->\n"
    for chunk in chunks:
        yield f"\n<!-- PAGE {chunk['metadata']['page_number']} -->\n\n"
        yield chunk["text"].replace("\r\n", "\n").replace("\r", "\n")
        yield "\n"
