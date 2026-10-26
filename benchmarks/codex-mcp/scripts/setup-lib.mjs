import { homedir } from 'node:os';
import { posix, win32 } from 'node:path';

export const REQUIRED_NODE_VERSION = [22, 22, 0];

export function versionAtLeast(actual, required = REQUIRED_NODE_VERSION) {
  const parts = actual.split('.').map(Number);
  return required.every((requiredPart, index) => {
    const actualPart = parts[index] ?? 0;
    const prefixMatches = required
      .slice(0, index)
      .every((part, prefixIndex) => (parts[prefixIndex] ?? 0) === part);
    return !prefixMatches || actualPart >= requiredPart;
  });
}

export function defaultEvaluationRoot(environment, platform = process.platform) {
  const paths = platform === 'win32' ? win32 : posix;
  if (environment.VIDXP_EVAL_ROOT) {
    return paths.resolve(environment.VIDXP_EVAL_ROOT);
  }
  if (platform === 'win32') {
    if (!environment.LOCALAPPDATA) {
      throw new Error('LOCALAPPDATA is required when VIDXP_EVAL_ROOT is unset.');
    }
    return paths.join(environment.LOCALAPPDATA, 'VidXP', 'benchmarks', 'codex-mcp');
  }
  const dataHome = environment.XDG_DATA_HOME || paths.join(homedir(), '.local', 'share');
  return paths.join(dataHome, 'vidxp', 'benchmarks', 'codex-mcp');
}

export function evaluationEnvironment({
  benchmarkRoot,
  repositoryRoot,
  evaluationRoot,
  environment = process.env,
  platform = process.platform,
}) {
  const paths = platform === 'win32' ? win32 : posix;
  const executable = platform === 'win32' ? 'vidxp-mcp.exe' : 'vidxp-mcp';
  const scriptsDirectory = platform === 'win32' ? 'Scripts' : 'bin';
  return {
    VIDXP_EVAL_CODEX_HOME: paths.join(evaluationRoot, 'codex-home'),
    VIDXP_EVAL_WORKSPACE: paths.join(evaluationRoot, 'workspace'),
    VIDXP_EVAL_DATA_DIR: paths.join(evaluationRoot, 'vidxp-data'),
    VIDXP_EVAL_INDEX_DIR: paths.join(evaluationRoot, 'vidxp-index'),
    VIDXP_MCP_COMMAND: paths.join(repositoryRoot, '.venv', scriptsDirectory, executable),
    VIDXP_EVAL_REPOSITORY: environment.VIDXP_EVAL_REPOSITORY || 'default',
    VIDXP_EVAL_DEVICE: environment.VIDXP_EVAL_DEVICE || 'cpu',
    VIDXP_EVAL_MODEL: environment.VIDXP_EVAL_MODEL || 'gpt-5.6-sol',
    VIDXP_EVAL_REASONING: environment.VIDXP_EVAL_REASONING || 'medium',
    VIDXP_EVAL_ARTIFACT_DIR: paths.join(evaluationRoot, 'longvale-artifacts'),
    VIDXP_EVAL_ENV_FILE: paths.join(benchmarkRoot, '.env'),
  };
}

export function serializeEnvironment(environment) {
  return Object.entries(environment)
    .filter(([name]) => name !== 'VIDXP_EVAL_ARTIFACT_DIR' && name !== 'VIDXP_EVAL_ENV_FILE')
    .map(([name, value]) => `${name}=${JSON.stringify(value.replaceAll('\\', '/'))}`)
    .join('\n') + '\n';
}

export function indexContainsPilot(index, videoIds, modalities) {
  if (!index) {
    return false;
  }
  const filenames = new Set((index.items || []).map((item) => item.original_filename));
  const indexedModalities = new Set(index.modalities || []);
  return videoIds.every((id) => filenames.has(`${id}.mp4`))
    && modalities.every((modality) => indexedModalities.has(modality));
}

export function libsqlBindingName(platform, architecture, glibcVersion = undefined) {
  if (platform === 'win32' && architecture === 'x64') {
    return '@libsql/win32-x64-msvc';
  }
  if (platform === 'darwin' && ['arm64', 'x64'].includes(architecture)) {
    return `@libsql/darwin-${architecture}`;
  }
  if (platform === 'linux' && ['arm', 'arm64', 'x64'].includes(architecture)) {
    const libc = glibcVersion ? (architecture === 'arm' ? 'gnueabihf' : 'gnu')
      : (architecture === 'arm' ? 'musleabihf' : 'musl');
    return `@libsql/linux-${architecture}-${libc}`;
  }
  throw new Error(`Promptfoo has no pinned libsql binding for ${platform}-${architecture}.`);
}
