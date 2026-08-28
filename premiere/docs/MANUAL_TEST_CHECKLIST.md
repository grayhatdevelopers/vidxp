# Premiere Pro manual test checklist

Automated tests do not exercise Premiere, CEP, UXP, Creative Cloud, media
paths, a real VidXP process, or model inference. Complete this checklist on a
clean Windows test machine with Premiere Pro 23.2 and Premiere Pro 25.6 or
newer before claiming host support.

## Package installation

- Install a release VidXP Desktop build; do not clone the repository or enable
  Adobe developer modes for this test.
- Confirm Desktop detects Premiere 23.2 as CEP and Premiere 25.6+ as UXP.
- Select **Install for Premiere** and confirm Adobe's installer accepts the
  signed `.zxp` and `.ccx` packages.
- Restart both Premiere versions. Confirm 23.2 shows one panel under **Window >
  Extensions (Legacy)** and 25.6+ shows one panel under **Window > UXP
  Plugins**.
- Refresh Desktop and confirm both installed statuses are reported.
- Remove both extensions from Desktop, restart Premiere, and confirm both
  panels are absent. Reinstall before continuing.

## Load and layout

- Run every remaining item once in Premiere 23.2 and once in Premiere 25.6+.
- Dock, float, resize, close, and reopen the panel in supported Premiere themes.
- Confirm UXP's built-in Spectrum controls and CEP's native controls render
  correctly and consistently with the active Premiere theme.
- Confirm Tab, Space, and Enter work as expected and focus remains visible.
- Confirm controlled values do not reset and actions fire only once after UDT
  reloads and React Strict Mode remounts.
- Confirm the native bearer-token password input and media-scope selector remain
  usable on Windows and macOS.
- Confirm scrolling, disclosure details, and the 320-pixel minimum layout remain
  usable without CSS Grid.

## Premiere media discovery

- Open a project containing nested bins, online clips, offline clips, a
  sequence, a subclip, a proxy, and duplicate project items for one source.
- Confirm refresh shows the active project and sequence.
- Confirm nested bins and file-backed media appear without sequences.
- Confirm offline and pathless media cannot be selected.
- Select clips and bins in the panel and confirm the count is deduplicated.
- Select clips and bins in Premiere's Project panel, choose
  **Use current selection**, and confirm the same media becomes selected.
- Repeat with a Windows UNC path and, on macOS, a mounted-volume path.

## VidXP connection

- In Desktop, start local processing and the app integration service locally.
- Copy Desktop's dynamic API address into the extension and connect.
- Confirm CEP connects through its native loopback transport without adding a
  `null` browser origin to VidXP's CORS allowlist.
- Confirm the panel shows the capabilities and indexed media reported by that
  runtime, including any capability added after this extension was built.
- Stop the service and confirm the panel reports a safe, actionable error.
- Try a wrong bearer token against shared mode and confirm the token is not
  shown in the error or console.

## Ingestion and status

- Index one short real video and confirm registration, real model inference,
  active-snapshot update, and the completion notice.
- Confirm the indexing action disables while work is active and enables again
  after success or failure.
- Index more than ten selected clips and confirm the panel progresses through
  multiple batches without duplicate media paths.
- Include one missing, unsupported, or disallowed source and confirm the other
  files can finish while the failed item is identified.
- Confirm Premiere remains responsive and the panel reports meaningful progress
  during a long index.
- Close and reopen the panel during a job. Record the current POC behavior; job
  recovery across panel reload is not yet implemented.

## Search

- Search all indexed media with each searchable capability individually and in
  combination.
- Search one selected VidXP media item and confirm every result belongs to it.
- Confirm each result shows the correct source name, time range, capability
  labels, rank, and score.
- Search for no-match text, stop the worker, and use an unprepared capability;
  confirm empty, unavailable, and model-remediation states are readable.

## Safety and cleanup

- Confirm no source video is copied into `premiere/`, CEP or UXP plugin data,
  or host temporary storage.
- Confirm no API token, source path, media file, index, build output, or local
  UXP Developer Tool setting appears in `git status`.
- Stop services started for the test and remove both packages from Desktop when
  finished.
