from docx import Document

from unbox_show import script_doc
from unbox_show.model import load_show


def test_script_has_sections_speakers_and_no_tags(real_show_copy, tmp_path):
    show = load_show(real_show_copy)
    out = script_doc.write(show, real_show_copy, tmp_path / "script.docx")
    text = "\n".join(p.text for p in Document(out).paragraphs)
    assert "Warming up" in text and "MISSION CONTROL" in text
    assert "Commander Matt A. Fore" in text  # the subtitle spelling, not the spoken one
    assert "[" not in text  # audio tags become (directions)
    assert "Countdown and launch" in text
