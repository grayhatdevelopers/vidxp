Build a persistent multi-video library:

- stream, hash, and ffprobe local imports before publishing them under opaque media IDs
- store each indexed video as an immutable generation and atomically update the active multi-video snapshot only after validation
- preserve the previous searchable snapshot when indexing fails or is cancelled
- re-index, remove, or clear individual media without rebuilding unrelated videos
- rank top-k results across the active collection or restrict search and grounded questions with a `media_id`
- list registered and actively indexed videos with bounded, stable cursor pages that reject malformed, noncanonical, or overflowing cursors
- import large local files without routing their bytes through the browser upload control
