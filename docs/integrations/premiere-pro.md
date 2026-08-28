# Search Premiere Pro media with VidXP

The VidXP Premiere Pro extension lets an editor select clips or bins from the
open project, index their existing source files, and search the resulting
library without leaving Premiere. It discovers dialogue, sound, scene, actor,
and future search features from the connected VidXP runtime instead of keeping
a fixed capability list in the extension.

## Install the extension

Install VidXP Desktop from the official
[GitHub release](https://github.com/grayhatdevelopers/vidxp/releases). No Git
checkout, Node.js installation, local build, Adobe developer mode, or Premiere
upgrade is required for Premiere Pro 23.2.

1. In Desktop setup, select **Premiere Pro extension** and the search features
   you want. Desktop automatically includes local video processing and its
   private app connection.
2. Complete an Adobe Creative Cloud confirmation window if one appears.
3. Restart Premiere Pro.

For an existing Desktop-managed setup, select **Set up Premiere** on its summary
screen. The Premiere requirements are preselected and Desktop installs the Adobe
package after updating VidXP. Use **Install for Premiere** only to reinstall or
retry the Adobe package without changing VidXP features.

Desktop ships both Adobe extension packages and chooses from the installed
Premiere versions:

| Premiere version | Extension | Open the panel from |
|---|---|---|
| 23.0–25.5 | CEP 11 | **Window > Extensions (Legacy) > VidXP Search** |
| 25.6 or newer | UXP | **Window > UXP Plugins > VidXP Search** |

The package ranges do not overlap. A workstation with an older and a current
Premiere installation can keep both packages installed without duplicate
panels in either host.

Desktop uses Adobe Creative Cloud's official Unified Plugin Installer Agent.
If the background installer is unavailable or needs user interaction, Desktop
opens the bundled `.zxp` or `.ccx` package so Creative Cloud can finish the
installation. It never builds extension code on the user's computer.

## Connect VidXP

The extension and VidXP must run on the same computer because Premiere gives
the panel paths to media already present in the project. VidXP indexes those
source files in place; it does not upload or duplicate them.

Premiere setup starts the private app service. Local video processing starts
when VidXP needs it; if you stop either service later, start it again from the
Desktop summary. Copy the displayed API address into the Premiere panel and
connect.

Desktop can choose an available local port, so use its displayed address
instead of assuming the default `http://127.0.0.1:32191`.

## Index Premiere media

1. Open a Premiere project and connect the panel.
2. Search the project tree or choose **Use current selection** to mirror the
   Project panel selection.
3. Select clips or bins. A Premiere bin expands to its file-backed descendants;
   it is not treated as a filesystem directory.
4. Choose any indexing features reported by VidXP and start indexing.
5. Keep the panel open while it reports batch progress. A dismissible notice
   reports completion and identifies individual failures.

Offline clips, sequences, generated items without a media path, and duplicate
source paths are not submitted. Large selections are split into durable VidXP
ingestion sessions.

## Search indexed moments

Enter a description, choose one indexed video or the complete active library,
and select any searchable features reported by VidXP. Results show the source
video, time range, contributing features, and fused score.

Timeline navigation, Source Monitor actions, marker creation, and snippet
insertion remain future host-adapter operations. They can be added without
changing the VidXP client or shared search workflow.

## Current release limits

- Windows is the first supported packaging target. Premiere Pro 23.2 must pass
  the CEP host checklist before the release is promoted beyond preview.
- Adobe blocks ordinary `http://` URLs in Premiere UXP on macOS. The 25.6+
  macOS panel needs a trusted loopback HTTPS transport before it is supported.
- CEP on macOS uses its native Node transport, but installation and media-path
  behavior still require host validation.
- Proxy, subclip, Productions, UNC, mounted-volume, offline-media, and React 19
  control behavior remain explicit manual release gates.
- Completion appears inside the panel because neither host generation exposes
  a dependable native Premiere notification API for this workflow.

Contributors should read the [extension architecture](../../premiere/README.md)
and run the [manual host checklist](../../premiere/docs/MANUAL_TEST_CHECKLIST.md).

## Adobe references

- [Premiere UXP introduction](https://developer.adobe.com/premiere-pro/uxp/introduction/)
- [Package a UXP plugin](https://developer.adobe.com/premiere-pro/uxp/plugins/distribution/package/)
- [Install a UXP plugin](https://developer.adobe.com/premiere-pro/uxp/plugins/distribution/install/)
- [Premiere UXP network operations](https://developer.adobe.com/premiere-pro/uxp/resources/recipes/network/)
- [Adobe CEP PProPanel sample](https://github.com/Adobe-CEP/Samples/tree/master/PProPanel)
- [Adobe CEP 11 cookbook](https://github.com/Adobe-CEP/CEP-Resources/blob/master/CEP_11.x/Documentation/CEP%2011.1%20HTML%20Extension%20Cookbook.md)
