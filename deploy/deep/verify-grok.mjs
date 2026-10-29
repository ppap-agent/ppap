// Extract the pinned Grok guest executable after `npm audit signatures`.
// npm verifies the registry signature of the installed package. It does not
// pin the decompressed guest binary; that PPAP supply-chain pin is the
// sha256 below. There is no npm CLI for that byte pin.
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { readFileSync, writeFileSync, chmodSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { brotliDecompressSync } from 'node:zlib';

const require = createRequire(import.meta.url);
const version = JSON.parse(readFileSync(new URL('./package.json', import.meta.url))).dependencies['@xai-official/grok'];
const name = '@xai-official/grok-linux-x64';
const expectedSha256 = '22d281c5964c5444147328096ba2a221f3f0874c6858e27c825656a80f6e58e9';

if (process.platform !== 'linux' || process.arch !== 'x64') {
  throw new Error('The verified Grok artifact requires linux-x64');
}
const topPath = require.resolve('@xai-official/grok/package.json');
const nativePath = require.resolve(`${name}/package.json`, { paths: [dirname(topPath)] });
if (JSON.parse(readFileSync(topPath)).version !== version || JSON.parse(readFileSync(nativePath)).version !== version) {
  throw new Error('Grok package version mismatch');
}
const executable = brotliDecompressSync(readFileSync(join(dirname(nativePath), 'bin/grok.br')));
if (createHash('sha256').update(executable).digest('hex') !== expectedSha256) {
  throw new Error('Grok executable digest mismatch');
}
writeFileSync('/grok', executable);
chmodSync('/grok', 0o755);
