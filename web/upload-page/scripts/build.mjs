import { copyFile, mkdir, readFile, readdir, writeFile } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { build } from 'esbuild'

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const repositoryRoot = resolve(packageRoot, '..', '..')
const outputRoot = join(repositoryRoot, 'src', 'vidxp', 'assets', 'upload_page')

await mkdir(outputRoot, { recursive: true })

const bundle = await build({
  entryPoints: [join(packageRoot, 'src', 'app.js')],
  outfile: join(outputRoot, 'upload-page.js'),
  bundle: true,
  charset: 'utf8',
  format: 'esm',
  legalComments: 'none',
  metafile: true,
  minify: true,
  sourcemap: false,
  target: ['es2020'],
})

const bundledJavaScriptPath = join(outputRoot, 'upload-page.js')
const bundledJavaScript = await readFile(bundledJavaScriptPath, 'utf8')
await writeFile(
  bundledJavaScriptPath,
  bundledJavaScript.replace(/[\t ]+$/gm, ''),
  'utf8',
)

await copyFile(
  join(packageRoot, 'src', 'index.html'),
  join(outputRoot, 'index.html'),
)

const lock = JSON.parse(
  await readFile(join(packageRoot, 'package-lock.json'), 'utf8'),
)
const notices = [
  'VidXP upload page third-party notices',
  '',
  'This file covers JavaScript packages bundled into upload-page.js.',
  'Build-only development packages are not included.',
]

const bundledPackagePaths = new Set()
for (const inputPath of Object.keys(bundle.metafile.inputs)) {
  const normalized = inputPath.replaceAll('\\', '/')
  const marker = 'node_modules/'
  const markerIndex = normalized.lastIndexOf(marker)
  if (markerIndex === -1) continue

  const packageParts = normalized.slice(markerIndex + marker.length).split('/')
  const packageName = packageParts[0].startsWith('@')
    ? packageParts.slice(0, 2).join('/')
    : packageParts[0]
  bundledPackagePaths.add(`node_modules/${packageName}`)
}

for (const [packagePath, locked] of Object.entries(lock.packages)) {
  if (
    !packagePath ||
    locked.dev ||
    !locked.version ||
    !bundledPackagePaths.has(packagePath.replaceAll('\\', '/'))
  ) {
    continue
  }

  const installedRoot = join(packageRoot, packagePath)
  const manifest = JSON.parse(
    await readFile(join(installedRoot, 'package.json'), 'utf8'),
  )
  const entries = await readdir(installedRoot)
  const licenseFiles = entries
    .filter((name) => /^(licen[cs]e|notice)(\.|$)/i.test(name))
    .sort((left, right) => left.localeCompare(right))

  notices.push('', '='.repeat(72), '')
  notices.push(`${manifest.name}@${locked.version}`)
  notices.push(`Declared license: ${manifest.license ?? locked.license ?? 'unknown'}`)

  if (licenseFiles.length > 0) {
    for (const filename of licenseFiles) {
      notices.push('', `--- ${filename} ---`, '')
      notices.push((await readFile(join(installedRoot, filename), 'utf8')).trim())
    }
  } else {
    const readmeName = entries.find((name) => /^readme(\.|$)/i.test(name))
    const readme = readmeName
      ? await readFile(join(installedRoot, readmeName), 'utf8')
      : ''
    const licenseHeading = readme.search(/^#{1,3} licen[cs]e(?:\(s\))?\s*$/im)
    const embeddedLicense =
      licenseHeading === -1 ? '' : readme.slice(licenseHeading).trim()
    if (embeddedLicense.length < 200) {
      throw new Error(`No complete license notice found for ${manifest.name}`)
    }
    notices.push('', `--- ${readmeName} license section ---`, '')
    notices.push(embeddedLicense)
  }
}

notices.push('')
await writeFile(
  join(outputRoot, 'THIRD_PARTY_NOTICES.txt'),
  notices.join('\n'),
  'utf8',
)
