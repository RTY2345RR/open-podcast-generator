# Changelog

## 0.1.0 (2026-11-15)
- First release: `make`, `script`, `render`, `voices` and `doctor` commands.
- Sources: PDF, web URL, YouTube captions (optional extra), text/Markdown/HTML/SRT files, stdin, inline text.
- Script writing with any OpenAI-compatible chat completions endpoint; five styles, target length, language; one automatic format repair.
- Markdown script format with chapters, pauses and notes.
- One cloned voice per host (reference clip URL or saved voice id); male and female demo hosts by default.
- Stitched MP3 at -16 LUFS with ID3 tags, ID3 chapters, cover art, intro/outro.
- Transcript (SRT, WebVTT, Podcasting 2.0 JSON, TXT), chapters.json, show notes, RSS item and feed.
- Resumable rendering with per-line idempotency keys; edited lines are the only ones re-voiced.
