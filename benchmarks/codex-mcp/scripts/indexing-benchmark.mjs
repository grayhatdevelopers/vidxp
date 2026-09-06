import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const benchmarkRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const repositoryRoot = resolve(benchmarkRoot, '..', '..');
const manifestPath = join(benchmarkRoot, 'tasks', 'longvale-part9-pilot.json');
const modalities = ['scene', 'action', 'sound', 'speech'];

function requireValue(name) {
  const value = process.env[name];
  if (!value) {
    throw new Error(`${name} is required; run benchmark setup first.`);
  }
  return value;
}

function positiveInteger(value) {
  if (!/^\d+$/.test(value) || Number(value) < 1) {
    throw new Error('Indexing repetitions must be a positive integer.');
  }
  return Number(value);
}

function run(command, args, { capture = false, env = process.env } = {}) {
  const result = spawnSync(command, args, {
    cwd: repositoryRoot,
    env,
    encoding: capture ? 'utf8' : undefined,
    stdio: capture ? 'pipe' : 'inherit',
  });
  if (result.error || result.status !== 0) {
    throw new Error(
      result.stderr?.trim()
        || result.stdout?.trim()
        || result.error?.message
        || `${command} exited with status ${result.status}.`,
    );
  }
  return capture ? result.stdout.trim() : '';
}

function directorySize(path) {
  return readdirSync(path, { withFileTypes: true }).reduce((total, entry) => {
    const child = join(path, entry.name);
    return total + (entry.isDirectory() ? directorySize(child) : statSync(child).size);
  }, 0);
}

function stats(values) {
  const sorted = [...values].sort((left, right) => left - right);
  const mean = sorted.reduce((total, value) => total + value, 0) / sorted.length;
  const middle = Math.floor(sorted.length / 2);
  const median = sorted.length % 2
    ? sorted[middle]
    : (sorted[middle - 1] + sorted[middle]) / 2;
  const variance = sorted.length < 2
    ? 0
    : sorted.reduce((total, value) => total + (value - mean) ** 2, 0)
      / (sorted.length - 1);
  return {
    mean,
    median,
    standard_deviation: Math.sqrt(variance),
    min: sorted[0],
    max: sorted.at(-1),
  };
}

function secondsSince(started) {
  return Number(process.hrtime.bigint() - started) / 1e9;
}

function sha256(path) {
  return createHash('sha256').update(readFileSync(path)).digest('hex');
}

function gitRevision() {
  try {
    return run('git', ['rev-parse', 'HEAD'], { capture: true });
  } catch {
    return 'unknown';
  }
}

function uniqueVideos(tasks, workspace) {
  const seen = new Set();
  return tasks.flatMap((task) => {
    if (seen.has(task.video_id)) return [];
    seen.add(task.video_id);
    const mediaPath = join(workspace, task.media_relpath);
    if (!existsSync(mediaPath)) {
      throw new Error(`Pilot media is missing: ${task.media_relpath}`);
    }
    return [{
      video_id: task.video_id,
      media_path: mediaPath,
      duration_seconds: task.duration_seconds,
    }];
  });
}

function aggregate(repetitions, videos) {
  const perVideo = Object.fromEntries(videos.map((video) => {
    const measurements = repetitions.flatMap((repetition) => (
      repetition.videos.filter((item) => item.video_id === video.video_id)
    ));
    return [video.video_id, {
      duration_seconds: video.duration_seconds,
      import_seconds: stats(measurements.map((item) => item.import_seconds)),
      index_seconds: stats(measurements.map((item) => item.index_seconds)),
      end_to_end_seconds: stats(measurements.map((item) => item.end_to_end_seconds)),
      index_realtime_factor: stats(
        measurements.map((item) => item.index_seconds / video.duration_seconds),
      ),
    }];
  }));
  return {
    total_elapsed_seconds: stats(repetitions.map((item) => item.total_elapsed_seconds)),
    total_import_seconds: stats(repetitions.map((item) => item.total_import_seconds)),
    total_index_seconds: stats(repetitions.map((item) => item.total_index_seconds)),
    index_size_bytes: stats(repetitions.map((item) => item.index_size_bytes)),
    per_video: perVideo,
  };
}

function main() {
  const repetitionsRequested = positiveInteger(process.argv[2] || '3');
  if (process.argv.length > 3) {
    throw new Error('Usage: indexing-benchmark.mjs [repetitions]');
  }
  const machineId = requireValue('VIDXP_EVAL_MACHINE_ID');
  const workspace = requireValue('VIDXP_EVAL_WORKSPACE');
  const python = requireValue('PROMPTFOO_PYTHON');
  const cli = join(dirname(python), process.platform === 'win32' ? 'vidxp.exe' : 'vidxp');
  if (!existsSync(cli)) {
    throw new Error(`The prepared VidXP CLI was not found at ${cli}.`);
  }
  const tasks = JSON.parse(readFileSync(manifestPath, 'utf8'));
  const videos = uniqueVideos(tasks, workspace);
  const commandEnvironment = {
    ...process.env,
    VIDXP_ALLOW_MODEL_DOWNLOADS: 'false',
    VIDXP_MODEL_CACHE: requireValue('VIDXP_MODEL_CACHE'),
  };

  const preflight = spawnSync(
    process.execPath,
    [join(benchmarkRoot, 'scripts', 'preflight.mjs')],
    { cwd: benchmarkRoot, env: commandEnvironment, stdio: 'inherit' },
  );
  if (preflight.status !== 0) {
    throw new Error('Benchmark preflight failed.');
  }

  const startedAt = new Date();
  const runId = `indexing-${startedAt.toISOString().replaceAll(':', '-').replace(/\.\d{3}Z$/, 'Z')}`;
  const outputPath = join(repositoryRoot, 'docs', 'benchmarking', 'runs', `${runId}.json`);
  const repetitionResults = [];
  process.stdout.write(
    `Indexing benchmark on ${machineId}: ${videos.length} videos × `
    + `${repetitionsRequested} fresh-index repetitions.\n`,
  );

  for (let repetition = 1; repetition <= repetitionsRequested; repetition += 1) {
    const root = mkdtempSync(join(tmpdir(), 'vidxp-index-benchmark-'));
    const dataDirectory = join(root, 'data');
    const indexDirectory = join(root, 'index');
    mkdirSync(dataDirectory, { recursive: true });
    mkdirSync(indexDirectory, { recursive: true });
    const offset = (repetition - 1) % videos.length;
    const ordered = videos.slice(offset).concat(videos.slice(0, offset));
    const measuredVideos = [];
    const repetitionStarted = process.hrtime.bigint();
    try {
      for (const video of ordered) {
        process.stdout.write(
          `Repetition ${repetition}/${repetitionsRequested}: ${video.video_id}\n`,
        );
        const base = [
          '--data-dir', dataDirectory,
          '--index-dir', indexDirectory,
          '--device', process.env.VIDXP_EVAL_DEVICE || 'cpu',
          '--format', 'json',
        ];
        const importStarted = process.hrtime.bigint();
        const imported = JSON.parse(run(
          cli,
          [...base, 'media', 'import', video.media_path],
          { capture: true, env: commandEnvironment },
        ));
        const importSeconds = secondsSince(importStarted);
        const indexStarted = process.hrtime.bigint();
        run(
          cli,
          [
            ...base,
            'index', 'create', imported.media_id,
            ...modalities.flatMap((modality) => ['--modality', modality]),
          ],
          { capture: true, env: commandEnvironment },
        );
        const indexSeconds = secondsSince(indexStarted);
        measuredVideos.push({
          video_id: video.video_id,
          duration_seconds: video.duration_seconds,
          import_seconds: importSeconds,
          index_seconds: indexSeconds,
          end_to_end_seconds: importSeconds + indexSeconds,
          index_realtime_factor: indexSeconds / video.duration_seconds,
        });
      }
      repetitionResults.push({
        repetition,
        order: ordered.map((video) => video.video_id),
        total_elapsed_seconds: secondsSince(repetitionStarted),
        total_import_seconds: measuredVideos.reduce(
          (total, video) => total + video.import_seconds,
          0,
        ),
        total_index_seconds: measuredVideos.reduce(
          (total, video) => total + video.index_seconds,
          0,
        ),
        index_size_bytes: directorySize(indexDirectory),
        videos: measuredVideos,
      });
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  }

  const finishedAt = new Date();
  const summary = aggregate(repetitionResults, videos);
  const document = {
    schema_version: 1,
    run_id: runId,
    status: 'complete',
    machine_id: machineId,
    git_revision: gitRevision(),
    started_at: startedAt.toISOString(),
    completed_at: finishedAt.toISOString(),
    task_manifest_sha256: sha256(manifestPath),
    modalities,
    repetitions: repetitionsRequested,
    media_count: videos.length,
    media_duration_seconds: videos.reduce(
      (total, video) => total + video.duration_seconds,
      0,
    ),
    protocol: {
      purpose: 'Measure the offline cost paid before the timed agent comparison.',
      fresh_data_and_index_per_repetition: true,
      prepared_model_cache_reused: true,
      model_downloads_allowed: false,
      execution: 'Sequential VidXP CLI import and four-modality index per video.',
      timing: {
        import_seconds: 'Media validation and copy into isolated managed storage.',
        index_seconds: 'Blocking four-modality index command, including process and model load.',
        total_elapsed_seconds: 'All imports and indexes in one repetition plus loop overhead.',
      },
      excluded: ['benchmark setup', 'dataset download', 'model preparation', 'agent inference'],
    },
    results: repetitionResults,
    aggregate: summary,
  };
  mkdirSync(dirname(outputPath), { recursive: true });
  writeFileSync(outputPath, `${JSON.stringify(document, null, 2)}\n`);
  console.log('Per-video mean across repetitions:');
  console.table(Object.entries(summary.per_video).map(([videoId, measurement]) => ({
    video: videoId,
    'media seconds': measurement.duration_seconds.toFixed(3),
    'import seconds': measurement.import_seconds.mean.toFixed(3),
    'index seconds': measurement.index_seconds.mean.toFixed(3),
    'combined seconds': measurement.end_to_end_seconds.mean.toFixed(3),
    'index RTF': measurement.index_realtime_factor.mean.toFixed(3),
  })));
  console.table([{
    repetitions: repetitionsRequested,
    'mean wall seconds': summary.total_elapsed_seconds.mean.toFixed(3),
    'mean import seconds': summary.total_import_seconds.mean.toFixed(3),
    'mean index seconds': summary.total_index_seconds.mean.toFixed(3),
    'mean index bytes': Math.round(summary.index_size_bytes.mean).toLocaleString('en-US'),
  }]);
  process.stdout.write(`Saved reviewable indexing run: ${outputPath}\n`);
}

try {
  main();
} catch (error) {
  process.stderr.write(`Indexing benchmark failed: ${error.message}\n`);
  process.exitCode = 1;
}
