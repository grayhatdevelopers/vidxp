import { describe, expect, it } from "vitest";

import {
  chunkPaths,
  collectSelectedClips,
  filterLibrary,
} from "../src/premiere/library";
import type { PremiereMediaNode } from "../src/premiere/types";

const library: PremiereMediaNode[] = [
  {
    kind: "bin",
    id: "interviews",
    name: "Interviews",
    children: [
      {
        kind: "clip",
        id: "clip-a",
        name: "A camera.mp4",
        nativePath: "C:/Media/a.mp4",
        availability: "ready",
      },
      {
        kind: "clip",
        id: "clip-offline",
        name: "Offline.mov",
        availability: "offline",
      },
    ],
  },
  {
    kind: "clip",
    id: "clip-b",
    name: "B-roll.mov",
    nativePath: "C:/Media/b.mov",
    availability: "ready",
  },
];

describe("Premiere media library helpers", () => {
  it("expands bins, skips unavailable items, and deduplicates source paths", () => {
    const duplicate = {
      ...library[1],
      id: "duplicate-b",
    };
    const clips = collectSelectedClips(
      [...library, duplicate],
      new Set(["interviews", "clip-b", "duplicate-b"]),
    );

    expect(clips.map((clip) => clip.nativePath)).toEqual([
      "C:/Media/a.mp4",
      "C:/Media/b.mov",
    ]);
  });

  it("keeps a matching bin with only matching descendants", () => {
    expect(filterLibrary(library, "camera")).toEqual([
      {
        kind: "bin",
        id: "interviews",
        name: "Interviews",
        children: [library[0].kind === "bin" ? library[0].children[0] : null],
      },
    ]);
  });

  it("chunks large Premiere selections for the ten-path ingestion contract", () => {
    const paths = Array.from({ length: 23 }, (_, index) => `clip-${index}`);
    expect(chunkPaths(paths).map((batch) => batch.length)).toEqual([10, 10, 3]);
  });
});
