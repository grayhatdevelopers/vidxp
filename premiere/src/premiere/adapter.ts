import type {
  FolderItem,
  premierepro,
  ProjectItem,
} from "@adobe/premierepro";

import type {
  PremiereAdapter,
  PremiereClip,
  PremiereLibrary,
  PremiereMediaNode,
} from "./types";

// Premiere supplies this module at runtime and Vite leaves it external.
// eslint-disable-next-line @typescript-eslint/no-require-imports
const ppro = require("premierepro") as premierepro;

export function createPremiereAdapter(): PremiereAdapter {
  return {
    async getLibrary(): Promise<PremiereLibrary | undefined> {
      const project = await ppro.Project.getActiveProject();
      if (!project) return undefined;

      const [sequence, root] = await Promise.all([
        project.getActiveSequence(),
        project.getRootItem(),
      ]);
      const items = await readFolder(root);

      return {
        projectName: project.name,
        sequenceName: sequence?.name,
        items,
      };
    },

    async getSelectedProjectItemIds(): Promise<string[]> {
      const project = await ppro.Project.getActiveProject();
      if (!project) return [];
      const selection = await ppro.ProjectUtils.getSelection(project);
      const items = await selection.getItems();
      return items.map((item) => item.getId());
    },
  };
}

async function readFolder(folder: FolderItem): Promise<PremiereMediaNode[]> {
  const children = await folder.getItems();
  const nodes = await Promise.all(children.map(readProjectItem));
  return nodes.filter((node): node is PremiereMediaNode => node !== undefined);
}

async function readProjectItem(
  item: ProjectItem,
): Promise<PremiereMediaNode | undefined> {
  const id = item.getId();
  if (
    item.type === ppro.ProjectItem.TYPE_BIN ||
    item.type === ppro.ProjectItem.TYPE_ROOT
  ) {
    const folder = ppro.FolderItem.cast(item);
    return {
      kind: "bin",
      id,
      name: item.name,
      children: await readFolder(folder),
    };
  }

  try {
    const clip = ppro.ClipProjectItem.cast(item);
    if (await clip.isSequence()) return undefined;
    if (await clip.isOffline()) {
      return unavailableClip(id, item.name, "offline", "Media is offline in Premiere.");
    }
    const nativePath = (await clip.getMediaFilePath()).trim();
    if (!nativePath) {
      return unavailableClip(
        id,
        item.name,
        "unavailable",
        "Premiere did not return a file-backed media path.",
      );
    }
    return {
      kind: "clip",
      id,
      name: item.name,
      nativePath,
      availability: "ready",
    };
  } catch {
    return undefined;
  }
}

function unavailableClip(
  id: string,
  name: string,
  availability: PremiereClip["availability"],
  detail: string,
): PremiereClip {
  return { kind: "clip", id, name, availability, detail };
}
