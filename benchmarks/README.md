# SynthID-text benchmark corpus

Seed documents for `bench_synthid_text.py`. Each file is a factual,
neutral prompt; the benchmark extends it with MarkLLM's `facebook/opt-1.3b`
generator (300 new tokens by default) and uses the full prompt+continuation
as the watermarked artifact.

- Keep seeds short (50-90 words) so the generated document is mostly
  model output — that is where the token-sampling watermark lives.
- Vary domains and style so results are not an artifact of one topic.
- Add your own files for a custom corpus; pass `--corpus /path/to/dir`.

Seeds are deterministic inputs only — the watermark comes from the
generation step, not from these files.
