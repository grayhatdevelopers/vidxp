export interface PremiereLibrary {
  projectName: string;
  sequenceName?: string;
  items: PremiereMediaNode[];
}

export type PremiereMediaNode = PremiereBin | PremiereClip;

export interface PremiereBin {
  kind: "bin";
  id: string;
  name: string;
  children: PremiereMediaNode[];
}

export interface PremiereClip {
  kind: "clip";
  id: string;
  name: string;
  nativePath?: string;
  availability: "ready" | "offline" | "unavailable";
  detail?: string;
}

export interface PremiereAdapter {
  getLibrary(): Promise<PremiereLibrary | undefined>;
  getSelectedProjectItemIds(): Promise<string[]>;
}
