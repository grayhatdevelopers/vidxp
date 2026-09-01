import { REQUIRED_NODE_VERSION, versionAtLeast } from './setup-lib.mjs';

if (!versionAtLeast(process.versions.node)) {
  process.stderr.write(
    `Node.js ${REQUIRED_NODE_VERSION.join('.')} or newer is required; found ${process.versions.node}.\n`,
  );
  process.exitCode = 1;
}
