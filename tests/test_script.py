import pytest

from open_podcast_generator.script import ScriptError, parse_script, split_long


def test_parse_front_matter_chapters_and_lines(script_text):
    s = parse_script(script_text)
    assert s.title == "Why the sky is blue"
    assert s.description.startswith("Two hosts")
    assert [c.title for c in s.chapters] == ["Cold open", "The physics"]
    assert s.speakers == ["Alex", "Sam"]
    assert len(s.lines) == 4
    assert "(laughs)" not in s.lines[1].text
    assert s.lines[1].pause_after == 0.5


def test_round_trip_markdown(script_text):
    s = parse_script(script_text)
    again = parse_script(s.to_markdown())
    assert [(l.speaker, l.text, l.pause_after) for l in again.lines] == [(l.speaker, l.text, l.pause_after) for l in s.lines]


def test_host_filter_and_continuation_lines():
    text = "Note: this is not a host\nAlex: Hello\nand this continues.\n**Sam**: Hi *smiles* there"
    s = parse_script(text, ["Alex", "Sam"])
    assert [l.speaker for l in s.lines] == ["Alex", "Sam"]
    assert s.lines[0].text == "Hello and this continues."
    assert s.lines[1].text == "Hi there"
    assert s.chapters[0].title == "Intro"


def test_empty_script_raises():
    with pytest.raises(ScriptError):
        parse_script("## Only a heading\n")


def test_split_long_keeps_sentences():
    text = " ".join(f"Sentence number {i} is here." for i in range(60))
    parts = split_long(text, 200)
    assert all(len(p) <= 200 for p in parts)
    assert " ".join(parts) == text
