---
name: Bug report
about: Report a defect in ai-watermark-remover (text/image cleaning, skill docs, or scripts)
title: "[bug] "
labels: bug
assignees: ""
---

## What happened

A clear description of the unexpected behaviour.

## What you expected

What should have happened instead.

## Steps to reproduce

1.
2.
3.

## Environment

- OS and arch:
- Python version (`python3 --version`):
- How you run the skill (Grok skill path / symlink / scripts only):
- Optional tools present (`c2patool`, `exiftool`) and versions if relevant:

## Input type

- [ ] Text (paste / `.txt` / `.md` / other)
- [ ] Image (PNG / JPEG)
- [ ] Both / batch directory
- Layer involved: A (Unicode) / B (rewrite guidance) / Files (C2PA/metadata)

## Diagnostics

Paste relevant CLI output (redact private content):

```bash
SCRIPTS=service/scripts
python3 "$SCRIPTS/inspect_file.py" path
# or:
python3 "$SCRIPTS/inspect_text.py" path/or/-
python3 "$SCRIPTS/inspect_image.py" path.png
```

## Extra context

Sample files (if shareable), screenshots, or related issues. Do not paste secrets, private documents, or material you do not own.
