import { spawnSync } from 'node:child_process';
import {
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import { basename, dirname, join, resolve } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { fileURLToPath, pathToFileURL } from 'node:url';

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const benchmarkRoot = resolve(scriptDirectory, '..');
const repositoryRoot = resolve(benchmarkRoot, '../..');
const outputDirectory = join(repositoryRoot, 'docs', 'benchmarking', 'runs');
const promptfooEntrypoint = join(
  benchmarkRoot,
  'node_modules',
  'promptfoo',
  'dist',
  'src',
  'entrypoint.js',
);
const SENSITIVE_KEY = /api.?key|access.?token|refresh.?token|secret|password|authorization|cookie/i;

function replaceAll(value, replacements) {
  let result = value;
  for (const [source, replacement] of replacements) {
    if (source) {
      result = result.split(source).join(replacement);
    }
  }
  return result;
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function sanitizeValue(value, replacements, userName) {
  if (typeof value === 'string') {
    const withPathsReplaced = replaceAll(value, replacements);
    const withHomeDirectoriesReplaced = withPathsReplaced
      .replace(/\/Users\/[^/\s'"\\]+/g, '<HOME>')
      .replace(/[A-Za-z]:[\\/]Users[\\/][^\\/\s'"\\]+/gi, '<HOME>');
    return userName
      ? withHomeDirectoriesReplaced.replace(
        new RegExp(`\\b${escapeRegExp(userName)}\\b`, 'g'),
        '<USER>',
      )
      : withHomeDirectoriesReplaced;
  }
  if (Array.isArray(value)) {
    return value.map((item) => sanitizeValue(item, replacements, userName));
  }
  if (!value || typeof value !== 'object') {
    return value;
  }
  const result = {};
  for (const [key, item] of Object.entries(value)) {
    if (key === 'sessionId') {
      continue;
    }
    result[key] = SENSITIVE_KEY.test(key)
      ? '<REDACTED>'
      : sanitizeValue(item, replacements, userName);
  }
  return result;
}

export function sanitizePromptfooExport(
  document,
  {
    repoRoot = repositoryRoot,
    userHome = homedir(),
    machineId = process.env.VIDXP_EVAL_MACHINE_ID,
  } = {},
) {
  if (!machineId) {
    throw new Error('VIDXP_EVAL_MACHINE_ID is required to export a run.');
  }
  const copy = structuredClone(document);
  for (const result of copy?.results?.results || []) {
    if (
      result?.response
      && typeof result.response === 'object'
      && result.response.metadata?.agentRuntime !== 'local-slm'
    ) {
      delete result.response.raw;
    }
  }
  const replacements = [
    [repoRoot, '<REPO>'],
    [userHome, '<HOME>'],
  ].sort((left, right) => right[0].length - left[0].length);
  const sanitized = sanitizeValue(copy, replacements, basename(userHome));
  sanitized.metadata = {
    ...sanitized.metadata,
    vidxpExport: {
      version: 2,
      machineId,
      sanitized: true,
      omitted: ['Codex raw response bodies', 'session IDs', 'secret values'],
      pathPlaceholders: ['<REPO>', '<HOME>', '<USER>'],
    },
  };
  return sanitized;
}

function latestEvaluationId() {
  const configDirectory = process.env.PROMPTFOO_CONFIG_DIR || join(homedir(), '.promptfoo');
  const database = new DatabaseSync(join(configDirectory, 'promptfoo.db'), { readOnly: true });
  try {
    const evaluation = database.prepare(
      'SELECT id FROM evals ORDER BY created_at DESC LIMIT 1',
    ).get();
    if (!evaluation) {
      throw new Error('Promptfoo has no saved evaluation.');
    }
    return evaluation.id;
  } finally {
    database.close();
  }
}

function artifactName(evaluationId) {
  return `${evaluationId.replaceAll(':', '-')}.json`;
}

function exportEvaluation(evaluationId) {
  if (!/^eval-[A-Za-z0-9._:-]+$/.test(evaluationId)) {
    throw new Error(`Invalid Promptfoo evaluation ID: ${evaluationId}`);
  }
  const temporaryDirectory = mkdtempSync(join(tmpdir(), 'vidxp-promptfoo-export-'));
  const rawPath = join(temporaryDirectory, 'raw.json');
  try {
    const exported = spawnSync(
      process.execPath,
      [promptfooEntrypoint, 'export', 'eval', evaluationId, '-o', rawPath],
      { cwd: benchmarkRoot, env: process.env, stdio: 'inherit' },
    );
    if (exported.status !== 0) {
      throw new Error(`Promptfoo export failed for ${evaluationId}.`);
    }
    const document = JSON.parse(readFileSync(rawPath, 'utf8'));
    const sanitized = sanitizePromptfooExport(document);
    mkdirSync(outputDirectory, { recursive: true });
    const destination = join(outputDirectory, artifactName(evaluationId));
    writeFileSync(destination, `${JSON.stringify(sanitized, null, 2)}\n`);
    process.stdout.write(`Saved sanitized Promptfoo run: ${destination}\n`);
  } finally {
    rmSync(temporaryDirectory, { recursive: true, force: true });
  }
}

function main() {
  const evaluationIds = process.argv.slice(2);
  for (const evaluationId of evaluationIds.length ? evaluationIds : [latestEvaluationId()]) {
    exportEvaluation(evaluationId);
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main();
}
