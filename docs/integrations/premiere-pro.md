# Search Premiere Pro media with VidXP

The VidXP Premiere Pro extension preview lets an editor choose clips or bins
from the open Premiere project, index the underlying video files, and search
the resulting library without leaving Premiere. Search features are discovered
from the connected VidXP runtime, so the panel does not assume a fixed list of
capabilities.

This is a source-built proof of concept. It targets Premiere Pro 25.6 or newer
on the current UXP platform. It has automated client and build coverage, but
the repository does not yet claim host validation or a packaged `.ccx`
release.

## Prepare VidXP Desktop

The extension and VidXP must run on the same computer because the panel passes
Premiere's existing local media paths to VidXP. It does not upload or duplicate
the source videos.

1. In VidXP Desktop, enable **Local video processing** and
   **App integration service** for the selected installation.
2. Prepare the search features you intend to use.
3. Start **Local video processing**.
4. Under **App integration service**, choose **Start locally** and copy the
   displayed **API address**.

Keep the service private to the computer. The Premiere preview does not need
the shared-network mode or a bearer token for this setup.

The command-line equivalent is:

```bash
uv tool install --python 3.14 --torch-backend cpu \
  "vidxp[local-worker,server]"
vidxp init
vidxp prepare
vidxp-api
```

The default API address is `http://127.0.0.1:32191`. Desktop may choose a
different available local port; always use the address it displays.

## Build and load the preview

Install Node.js 22.19, 24.15, or 26 and Adobe UXP Developer Tool 2.2 or newer.
From the repository root, run:

```bash
npm --prefix premiere ci
npm --prefix premiere run build
```

In UXP Developer Tool, add `premiere/dist/manifest.json`, load the plugin, then
open **Window > UXP Plugins > VidXP Search** in Premiere Pro.

Adobe's supported distribution format is a `.ccx` package installed through
Creative Cloud Desktop. Packaging and signing are intentionally deferred until
the preview passes the host checklist across supported Premiere versions.

## Index Premiere media

1. Open a Premiere project and connect the panel to the VidXP API address.
2. Search the project tree or choose **Use current selection** to mirror the
   Project panel selection.
3. Select clips or bins. A bin expands to its file-backed descendants; Premiere
   bins are logical containers and do not imply a filesystem directory.
4. Choose the indexing features reported by VidXP and start indexing.
5. Keep the panel open while it reports per-batch progress. A dismissible
   notification reports completion or individual failures.

Offline clips, sequences, generated items without a media path, and duplicate
source paths are not submitted. Selections larger than ten files are divided
into durable VidXP ingestion sessions of ten files each.

## Search indexed moments

Enter a description, choose one indexed video or the complete active library,
and select any searchable features reported by VidXP. Results show the source
video, time range, contributing features, and fused score.

The proof of concept does not yet move Premiere's playhead, open a result in the
Source Monitor, create timeline markers, or insert generated snippets. Those
operations remain behind the Premiere adapter so they can be added without
changing the VidXP client or search UI contracts.

## Current compatibility limits

- Adobe documents Premiere UXP as available from Premiere Pro 25.6 with
  manifest v5 and UXP Developer Tool 2.2.
- Adobe documents `getMediaFilePath()` for file-backed project items, but proxy,
  subclip, Productions, UNC, and offline-media behavior still needs host
  coverage.
- Adobe restricts ordinary `http://` network access on macOS. The loopback
  transport in this preview must be validated before macOS support is claimed.
- React 19, built-in Spectrum control events and values, panel resize behavior,
  network permissions with Desktop's dynamic port, and UXP request-origin
  behavior remain manual release gates.
- Completion appears inside the panel. UXP does not expose a dependable native
  Premiere toast API, and the preview does not rely on modal `alert()` calls.

Contributors should read the [extension architecture](../../premiere/README.md)
and run the [manual host checklist](../../premiere/docs/MANUAL_TEST_CHECKLIST.md).

## Adobe references

- [Premiere UXP introduction](https://developer.adobe.com/premiere-pro/uxp/introduction/)
- [Premiere UXP manifest](https://developer.adobe.com/premiere-pro/uxp/plugins/concepts/manifest/)
- [Premiere network operations](https://developer.adobe.com/premiere-pro/uxp/resources/recipes/network/)
- [ClipProjectItem API](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/clipprojectitem)
- [Official Premiere UXP samples](https://github.com/AdobeDocs/uxp-premiere-pro-samples)
- [Package and install a UXP plugin](https://developer.adobe.com/premiere-pro/uxp/plugins/distribution/package/)
