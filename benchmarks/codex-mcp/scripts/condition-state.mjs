import { spawnSync } from 'node:child_process';
import {
  cpSync,
  existsSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import {
  dirname,
  isAbsolute,
  join,
  resolve,
} from 'node:path';

export const EVALUATION_PERMISSION_PROFILE = 'vidxp-eval-isolated';

function tomlString(value) {
  return JSON.stringify(value.replaceAll('\\', '/'));
}

export function resolveExecutablePath(command, environment = process.env) {
  const locator = process.platform === 'win32' ? 'where.exe' : 'which';
  const result = spawnSync(locator, [command], {
    env: environment,
    encoding: 'utf8',
    stdio: 'pipe',
  });
  const first = result.stdout?.trim().split(/\r?\n/, 1)[0];
  if (result.status !== 0 || !first || !isAbsolute(first)) {
    throw new Error(`${command} must be installed before configuring benchmark isolation.`);
  }
  return resolve(first);
}

export function executableInstallRoots(executablePaths) {
  return [...new Set(executablePaths.map((path) => dirname(dirname(path))))].sort();
}

export function permissionProfile({ networkEnabled, readableRoots = [] }) {
  const extraReads = [...new Set(readableRoots)].sort().map(
    (path) => `${tomlString(path)} = "read"`,
  );
  return [
    `default_permissions = "${EVALUATION_PERMISSION_PROFILE}"`,
    '',
    `[permissions.${EVALUATION_PERMISSION_PROFILE}.filesystem]`,
    '":root" = "deny"',
    '":minimal" = "read"',
    ...extraReads,
    '',
    `[permissions.${EVALUATION_PERMISSION_PROFILE}.filesystem.":workspace_roots"]`,
    '"." = "write"',
    '',
    `[permissions.${EVALUATION_PERMISSION_PROFILE}.network]`,
    `enabled = ${networkEnabled}`,
    '',
  ].join('\n');
}

function writeIfChanged(path, content) {
  if (!existsSync(path) || readFileSync(path, 'utf8') !== content) {
    writeFileSync(path, content, 'utf8');
  }
}

export function evaluationPermissionConfigs(environment = process.env) {
  const ffmpeg = resolveExecutablePath('ffmpeg', environment);
  const ffprobe = resolveExecutablePath('ffprobe', environment);
  const directLocalRoots = executableInstallRoots([ffmpeg, ffprobe]);
  return {
    ffmpeg,
    configs: {
      vidxpOn: permissionProfile({ networkEnabled: false }),
      vidxpOff: permissionProfile({
        networkEnabled: false,
        readableRoots: directLocalRoots,
      }),
      cleanUser: permissionProfile({ networkEnabled: true }),
    },
  };
}

export function configureEvaluationIsolation(environment = process.env) {
  const requiredHomes = {
    vidxpOn: environment.VIDXP_EVAL_VIDXP_ON_CODEX_HOME,
    vidxpOff: environment.VIDXP_EVAL_VIDXP_OFF_CODEX_HOME,
    cleanUser: environment.VIDXP_EVAL_CLEAN_USER_CODEX_HOME,
  };
  for (const [condition, home] of Object.entries(requiredHomes)) {
    if (!home || !isAbsolute(home) || !existsSync(home)) {
      throw new Error(`The ${condition} Codex home must exist before configuring isolation.`);
    }
  }
  const resolved = evaluationPermissionConfigs(environment);
  for (const [condition, home] of Object.entries(requiredHomes)) {
    writeIfChanged(join(home, 'config.toml'), resolved.configs[condition]);
  }
  return resolved;
}

export function syncEvidenceSkill({ repositoryRoot, environment = process.env }) {
  const source = join(
    repositoryRoot,
    'plugins',
    'vidxp',
    'skills',
    'vidxp-find-video-evidence',
  );
  const destination = join(
    environment.VIDXP_EVAL_VIDXP_ON_WORKSPACE || '',
    '.agents',
    'skills',
    'vidxp-find-video-evidence',
  );
  if (!existsSync(join(source, 'SKILL.md'))) {
    throw new Error(`The VidXP evidence skill is missing: ${source}`);
  }
  if (!isAbsolute(destination)) {
    throw new Error('The VidXP-on workspace must exist before syncing the evidence skill.');
  }
  rmSync(destination, { recursive: true, force: true });
  cpSync(source, destination, { recursive: true });
}

export function prepareConditionState({ repositoryRoot, environment = process.env }) {
  const isolation = configureEvaluationIsolation(environment);
  syncEvidenceSkill({ repositoryRoot, environment });
  return isolation;
}
