Add durable multi-media index snapshots:

- build each media item as an immutable generation with an authoritative manifest
- atomically switch the active snapshot only after generation validation
- preserve the previous snapshot when indexing fails or is cancelled
- add CLI support for stable media IDs, incremental re-indexing, removal, and clearing
