import type {
  PremiereBin,
  PremiereClip,
  PremiereMediaNode,
} from "./types";

export function collectSelectedClips(
  items: PremiereMediaNode[],
  selectedIds: ReadonlySet<string>,
): PremiereClip[] {
  const clips = new Map<string, PremiereClip>();

  function visit(node: PremiereMediaNode, ancestorSelected: boolean) {
    const selected = ancestorSelected || selectedIds.has(node.id);
    if (node.kind === "bin") {
      node.children.forEach((child) => visit(child, selected));
      return;
    }
    if (selected && node.availability === "ready" && node.nativePath) {
      clips.set(node.nativePath, node);
    }
  }

  items.forEach((item) => visit(item, false));
  return [...clips.values()];
}

export function countReadyClips(items: PremiereMediaNode[]): number {
  return items.reduce(
    (total, item) =>
      total +
      (item.kind === "bin"
        ? countReadyClips(item.children)
        : item.availability === "ready"
          ? 1
          : 0),
    0,
  );
}

export function filterLibrary(
  items: PremiereMediaNode[],
  rawQuery: string,
): PremiereMediaNode[] {
  const query = rawQuery.trim().toLocaleLowerCase();
  if (!query) return items;

  return items.flatMap((node): PremiereMediaNode[] => {
    if (node.kind === "clip") {
      const searchable = `${node.name} ${node.nativePath ?? ""}`.toLocaleLowerCase();
      return searchable.includes(query) ? [node] : [];
    }
    const children = filterLibrary(node.children, query);
    return node.name.toLocaleLowerCase().includes(query) || children.length > 0
      ? [{ ...node, children } satisfies PremiereBin]
      : [];
  });
}

export function chunkPaths(paths: string[], maximum = 10): string[][] {
  if (!Number.isInteger(maximum) || maximum < 1) {
    throw new Error("The ingestion batch size must be a positive integer.");
  }
  const batches: string[][] = [];
  for (let index = 0; index < paths.length; index += maximum) {
    batches.push(paths.slice(index, index + maximum));
  }
  return batches;
}
