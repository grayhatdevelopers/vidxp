import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  defaultEvaluationRoot,
  evaluationEnvironment,
  indexContainsPilot,
  libsqlBindingName,
  serializeEnvironment,
  versionAtLeast,
} from './setup-lib.mjs';

test('checks the required Node version numerically', () => {
  assert.equal(versionAtLeast('22.21.9'), false);
  assert.equal(versionAtLeast('22.22.0'), true);
  assert.equal(versionAtLeast('23.0.0'), true);
});

test('recognizes a complete pilot index regardless of extra media', () => {
  const index = {
    items: [
      { original_filename: 'alpha.mp4' },
      { original_filename: 'beta.mp4' },
      { original_filename: 'unrelated.mp4' },
    ],
    modalities: ['scene', 'action', 'sound', 'speech'],
  };

  assert.equal(
    indexContainsPilot(index, ['alpha', 'beta'], ['scene', 'action', 'sound', 'speech']),
    true,
  );
  assert.equal(indexContainsPilot(index, ['alpha', 'missing'], ['scene']), false);
  assert.equal(indexContainsPilot(index, ['alpha'], ['scene', 'ocr']), false);
});

test('uses one optional root override for mutable setup state', () => {
  assert.equal(
    defaultEvaluationRoot({ VIDXP_EVAL_ROOT: 'C:/custom/eval' }, 'win32'),
    'C:\\custom\\eval',
  );
  assert.equal(
    defaultEvaluationRoot({ LOCALAPPDATA: 'C:/Users/test/AppData/Local' }, 'win32'),
    'C:\\Users\\test\\AppData\\Local\\VidXP\\benchmarks\\codex-mcp',
  );
  assert.equal(
    defaultEvaluationRoot({ XDG_DATA_HOME: '/tmp/data' }, 'linux'),
    '/tmp/data/vidxp/benchmarks/codex-mcp',
  );
});

test('selects the required Promptfoo SQLite binding for the host', () => {
  assert.equal(libsqlBindingName('win32', 'x64'), '@libsql/win32-x64-msvc');
  assert.equal(libsqlBindingName('darwin', 'arm64'), '@libsql/darwin-arm64');
  assert.equal(libsqlBindingName('linux', 'x64', '2.39'), '@libsql/linux-x64-gnu');
  assert.equal(libsqlBindingName('linux', 'x64'), '@libsql/linux-x64-musl');
  assert.throws(() => libsqlBindingName('win32', 'arm64'), /no pinned libsql binding/);
});

test('builds and serializes the environment consumed by Promptfoo', () => {
  const environment = evaluationEnvironment({
    benchmarkRoot: 'C:/repo/benchmarks/codex-mcp',
    repositoryRoot: 'C:/repo',
    evaluationRoot: 'C:/eval',
    environment: {},
    platform: 'win32',
  });
  const serialized = serializeEnvironment(environment);

  assert.match(serialized, /VIDXP_EVAL_WORKSPACE="C:\/eval\/workspace"/);
  assert.match(serialized, /VIDXP_MCP_COMMAND="C:\/repo\/\.venv\/Scripts\/vidxp-mcp\.exe"/);
  assert.match(serialized, /VIDXP_EVAL_MODEL="gpt-5\.6-sol"/);
  assert.doesNotMatch(serialized, /VIDXP_EVAL_ENV_FILE/);
  assert.doesNotMatch(serialized, /VIDXP_EVAL_ARTIFACT_DIR/);
});
