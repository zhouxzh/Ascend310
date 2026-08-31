# Local Test Data

The internal-test service creates `templates/` and `captures/` below this
directory at runtime. `templates/` contains plaintext `PWST1` embedding stores;
`captures/` contains user-triggered original images, ROI images, metadata, and
an atomic JSON index. These directories are intentionally ignored by Git and
must be excluded from GitHub, Hugging Face, and release synchronization.

Continuous camera preview frames are not archived. Template deletion removes
the embedding record but keeps capture evidence until an operator explicitly
cleans it.
