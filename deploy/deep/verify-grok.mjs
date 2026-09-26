// Verify the npm registry signature for the pinned native package and the
// exact executable before copying it into the Deep image.
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { readFileSync, writeFileSync, chmodSync } from 'node:fs';
import { createHash, createPublicKey, verify } from 'node:crypto';
import { brotliDecompressSync } from 'node:zlib';

const require = createRequire(import.meta.url);
const version = '1.0.40';
const name = '@xai-official/grok-linux-x64';
const integrity = 'sha512-vMD2GFDHrsViUg61G7Tc6/g6kz9IXYDn12yTMOhZy5J8E2qMxQaYsPJt2NSLjxy4Of8cTu8Zq7aws5xmZtTVEw==';
const signature = 'MEYCIQCgAd0oQ1p9Stupj1o5aQAx5axX1N7ZXxMkBnDVTD4NHQIhAJ+no0wGj3sgd9nwE55sF6jyDIQLui7fAZaTenb2JI6Q';
const npmPublicKey = 'MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEY6Ya7W++7aUPzvMTrezH6Ycx3c+HOKYCcNGybJZSCJq/fd7Qa8uuAKtdIkUQtQiEKERhAmE5lMMJhP8OkDOa2g==';
const expectedSha256 = '92c997dfd109c0672d40d5ae6fbd15835d53ffaf12cf9ea124d22aaef3ff23fc';

if (process.platform !== 'linux' || process.arch !== 'x64') {
  throw new Error('The verified Grok artifact requires linux-x64');
}
const key = createPublicKey({ key: Buffer.from(npmPublicKey, 'base64'), format: 'der', type: 'spki' });
if (!verify('sha256', Buffer.from(`${name}@${version}:${integrity}`), key, Buffer.from(signature, 'base64'))) {
  throw new Error('Grok npm registry signature did not verify');
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
