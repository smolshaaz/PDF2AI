from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import errno
import pytest
from pdf2ai.utils.paths import publish, path_key
from pdf2ai.extraction.validation import inspect_chunks, markdown_parts
from pdf2ai.workers.conversion_worker import run_batch
from pdf2ai.extraction.converter import friendly_error


def chunk(number, text):
    return {"metadata": {"page_number": number}, "text": text}


def test_order_preservation_and_utf8(tmp_path):
    text = "Exclusion: café — 保険.  \r\nNever rewrite this."
    ordered, warnings = inspect_chunks([chunk(2, text), chunk(1, "Exact legal wording")], 2)
    output = "".join(markdown_parts("policy.pdf", 2, ordered))
    target = tmp_path / "test.md"
    target.write_text(output, encoding="utf-8")
    assert target.read_bytes().decode("utf-8") == output
    assert output.index("PAGE 1") < output.index("PAGE 2")
    assert text.replace("\r\n", "\n") in output
    assert not warnings


def test_sparse_missing_and_duplicates():
    ordered, warnings = inspect_chunks([chunk(1, " \n---\n"), chunk(1, "preserve duplicate text")], 3)
    assert len(ordered) == 2
    assert any("little or no" in w for w in warnings)
    assert any("2, 3" in w for w in warnings)
    assert any("duplicate" in w for w in warnings)
    assert any("Expected 3" in w for w in warnings)


@pytest.mark.parametrize("number", [0, 4, None, "1", True])
def test_invalid_numbers(number):
    with pytest.raises(ValueError):
        inspect_chunks([chunk(number, "text")], 3)


def test_collision_race(tmp_path):
    source = tmp_path / "policy.pdf"
    folder = tmp_path / "PDF2AI Output"
    folder.mkdir()
    existing = folder / "policy.ai.md"
    existing.write_text("ORIGINAL")
    def save(n):
        temp = folder / f"{n}.tmp"
        temp.write_text(f"Unique {n}", encoding="utf-8")
        return publish(temp, source)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(save, range(8)))
    assert existing.read_text() == "ORIGINAL"
    assert len(set(results)) == 8
    assert {p.name for p in results} == {f"policy.ai ({n}).md" for n in range(2, 10)}
    assert not list(folder.glob("*.tmp"))
    assert path_key(tmp_path / "x/../policy.pdf") == path_key(source)


def test_publish_with_explicit_output_dir(tmp_path):
    """publish() writes to the given output_dir, not source.parent/PDF2AI Output."""
    from pdf2ai.utils.paths import output_candidates
    source = tmp_path / "sub" / "doc.pdf"
    source.parent.mkdir()
    custom_out = tmp_path / "my_output"
    custom_out.mkdir()
    # output_candidates uses the given dir
    cands = list(c for c, _ in zip(output_candidates(source, custom_out), range(3)))
    assert all(c.parent == custom_out for c in cands)
    assert cands[0].name == "doc.ai.md"
    # Default (None) falls back to source.parent/PDF2AI Output
    cands_default = list(c for c, _ in zip(output_candidates(source, None), range(1)))
    assert cands_default[0].parent == source.parent / "PDF2AI Output"
    # publish() writes to the custom dir
    temp = custom_out / ".tmp"
    temp.write_text("content")
    result = publish(temp, source, custom_out)
    assert result.parent == custom_out
    assert result.read_text() == "content"



def test_batch_continues_and_stop():
    events = []
    def convert(path):
        if path == "bad":
            raise ValueError("PRIVATE DOCUMENT TEXT must not be exposed")
        return {"ok": True}
    run_batch(["bad", "good"], events.append, lambda: False, convert)
    assert events[-1] == ("result", 1, {"ok": True})
    assert not events[1][2]["ok"]
    assert "PRIVATE" not in str(events)
    events.clear()
    run_batch(["first", "second"], events.append, lambda: bool(events), convert)
    assert len(events) == 2


def test_disk_full_message():
    assert "disk space" in friendly_error(OSError(errno.ENOSPC, "full"))
