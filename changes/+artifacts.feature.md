Add managed video artifacts:

- create source-preserving or broadly compatible MP4 clips from a media ID while rejecting ranges outside the source or configured duration limit
- reuse a ready clip for the same source checksum, interval, and output profile
- render actor overlays from stable composite cluster IDs bound to an immutable snapshot
- validate generated video and the requested output profile before atomic publication
- durably synchronize managed publication and removal on platforms that support directory `fsync`
- expose opaque artifact metadata and authorized downloads through CLI, HTTP, and MCP without leaking repository paths
