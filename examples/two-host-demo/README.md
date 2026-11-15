# Two-host AI podcast demo (cloned male + female voice)

Real output of `open-podcast-generator render script.md`: one line from **Alex** (male host, voice cloned from CMU ARCTIC `bdl`) and one line from **Sam** (female host, voice cloned from CMU ARCTIC `slt`).

- Audio: [episode.mp3](episode.mp3) (19 s, -16 LUFS target, 2 ID3 chapters)
- Script: [script.md](script.md)
- Transcript: [transcript.srt](transcript.srt), [transcript.vtt](transcript.vtt), [transcript.json](transcript.json), [transcript.txt](transcript.txt)
- Chapters: [chapters.json](chapters.json); show notes: [show-notes.md](show-notes.md)
- RSS: [feed-item.xml](feed-item.xml), [feed.xml](feed.xml)

Reproduce:

```bash
export MIRAGEA_API_KEY=...
open-podcast-generator render examples/two-host-demo/script.md -o two-host-demo \
  --show "Open Podcast Generator Demos" --author RTY2345RR
```
