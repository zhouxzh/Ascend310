# Partitura Voice Separation

This directory contains an adapted copy of Partitura's Chew/Wu voice
separation implementation.

- Project: `CPJKU/partitura`
- Version: `v1.9.0`
- Commit: `427ff875bd5a49a0eec894fdd7c6631ed7f597ea`
- Source: <https://github.com/CPJKU/partitura/blob/427ff875bd5a49a0eec894fdd7c6631ed7f597ea/partitura/musicanalysis/voice_separation.py>
- Original SHA256: `32d9af3ccc16c75efdf7679ddb810e0b5080cbb459495481dd5205bdbb640eb8`
- License: Apache License 2.0, reproduced in `LICENSE`

Local changes are limited to replacing Partitura's note-array conversion
helpers with a structured NumPy-array adapter. The voice-separation algorithm
itself remains the upstream implementation.
