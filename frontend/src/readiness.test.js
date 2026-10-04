import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { inflateSync } from 'node:zlib';

test('all existing icon references resolve to a valid manifest-sized PNG', async () => {
  const manifest = JSON.parse(await readFile('public/manifest.webmanifest', 'utf8'));
  const icon = manifest.icons[0];
  assert.equal(icon.src, '/icon.png');
  const png = await readFile('public' + icon.src);
  assert.deepEqual([...png.subarray(0, 8)], [137, 80, 78, 71, 13, 10, 26, 10]);
  assert.equal(`${png.readUInt32BE(16)}x${png.readUInt32BE(20)}`, icon.sizes);
  const chunks = [];
  for (let offset = 8; offset < png.length;) {
    const length = png.readUInt32BE(offset);
    if (png.toString('ascii', offset + 4, offset + 8) === 'IDAT') {
      chunks.push(png.subarray(offset + 8, offset + 8 + length));
    }
    offset += 12 + length;
  }
  assert.equal(inflateSync(Buffer.concat(chunks)).length, 192 * (1 + 192 * 3));
});

test('frontend headers are defensive without CSP or API rewrite changes', async () => {
  const config = JSON.parse(await readFile('vercel.json', 'utf8'));
  const headers = Object.fromEntries(config.headers[0].headers.map(({ key, value }) => [key, value]));
  assert.equal(headers['X-Content-Type-Options'], 'nosniff');
  assert.equal(headers['X-Frame-Options'], 'SAMEORIGIN');
  assert.equal(headers['Referrer-Policy'], 'strict-origin-when-cross-origin');
  assert.equal(headers['Content-Security-Policy'], undefined);
  assert.equal(config.rewrites[0].destination, 'https://sports-friday-backend.vercel.app/api/:path*');
});
