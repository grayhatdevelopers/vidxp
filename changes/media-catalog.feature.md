Add managed local media catalogs:

- stream and hash local imports without loading whole videos into memory
- validate media with ffprobe before publishing a ready catalog entry
- index registered media by opaque ID instead of public filesystem path
- list media through a bounded cursor page shared by every adapter
