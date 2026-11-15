import httpx
import pytest

from open_podcast_generator.sources import SourceError, html_to_text, load_source, vtt_to_text

HTML = """<html><head><title>Rayleigh scattering</title><script>var x=1;</script></head>
<body><nav>Home | About | Contact</nav><article><h1>Rayleigh scattering</h1>
<p>Rayleigh scattering is the scattering of light by particles much smaller than the wavelength of the light.</p>
<p>It explains why the sky is blue during the day and red at sunset, because blue light scatters more strongly.</p>
</article><footer>Copyright 2026</footer></body></html>"""


def test_html_to_text_drops_chrome():
    text, title = html_to_text(HTML)
    assert title == "Rayleigh scattering"
    assert "why the sky is blue" in text
    assert "Home | About" not in text and "var x" not in text and "Copyright" not in text


def test_load_url_html():
    transport = httpx.MockTransport(lambda r: httpx.Response(200, html=HTML))
    src = load_source("https://example.com/sky", transport=transport)
    assert src.kind == "web" and src.title == "Rayleigh scattering"


def test_load_url_http_error():
    transport = httpx.MockTransport(lambda r: httpx.Response(404))
    with pytest.raises(SourceError):
        load_source("https://example.com/missing", transport=transport)


def test_load_text_file_and_inline(tmp_path):
    f = tmp_path / "notes.md"
    f.write_text("# Notes\n" + "word " * 50)
    src = load_source(str(f))
    assert src.kind == "text" and src.title == "notes"
    assert load_source("word " * 30).kind == "text"
    with pytest.raises(SourceError):
        load_source("too short to use")


def test_pdf(tmp_path):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    # Build a tiny one-page PDF with real text using pypdf only.
    w = PdfWriter()
    page = w.add_blank_page(612, 792)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): w._add_object(font)})})
    stream = DecodedStreamObject()
    words = "Podcasts turn papers into conversations that people can follow on a walk " * 3
    stream.set_data(f"BT /F1 10 Tf 20 700 Td ({words}) Tj ET".encode())
    page[NameObject("/Contents")] = w._add_object(stream)
    path = tmp_path / "paper.pdf"
    with path.open("wb") as fh:
        w.write(fh)
    src = load_source(str(path))
    assert src.kind == "pdf" and "conversations" in src.text


def test_vtt_to_text_dedupes():
    vtt = "WEBVTT\n\n00:00.000 --> 00:01.000\nhello <c>there</c>\n\n00:01.000 --> 00:02.000\nhello there\n\n00:02.000 --> 00:03.000\nfriend\n"
    assert vtt_to_text(vtt) == "hello there friend"
