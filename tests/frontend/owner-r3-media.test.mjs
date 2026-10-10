import test from 'node:test';
import assert from 'node:assert/strict';
import { inflateSync } from 'node:zlib';
import { syntheticPixelPng } from '../fixtures/owner-r3-media.mjs';

// Independent PNG validation catches corruption that Chromium may tolerate but
// WebKit correctly rejects. Browser image.decode() assertions remain mandatory.
function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
  }
  return (crc ^ 0xffffffff) >>> 0;
}
function parsePng(bytes) {
  assert.deepEqual(bytes.subarray(0, 8), Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
  const chunks = [];
  let offset = 8;
  while (offset < bytes.length) {
    const length = bytes.readUInt32BE(offset);
    assert.ok(offset + length + 12 <= bytes.length, 'Complete PNG chunk');
    const type = bytes.toString('ascii', offset + 4, offset + 8);
    const payload = bytes.subarray(offset + 8, offset + 8 + length);
    assert.equal(bytes.readUInt32BE(offset + 8 + length), crc32(bytes.subarray(offset + 4, offset + 8 + length)), `${type} CRC is valid`);
    chunks.push({ type, payload });
    offset += length + 12;
  }
  assert.equal(offset, bytes.length);
  return chunks;
}

test('shared synthetic upload PNG has valid chunks, checksums and exact 1px RGBA data', () => {
  const chunks = parsePng(syntheticPixelPng());
  assert.deepEqual(chunks.map(chunk => chunk.type), ['IHDR', 'IDAT', 'IEND']);
  const header = chunks[0].payload;
  assert.equal(header.readUInt32BE(0), 1);
  assert.equal(header.readUInt32BE(4), 1);
  assert.deepEqual([...header.subarray(8)], [8, 6, 0, 0, 0]);
  assert.deepEqual([...inflateSync(chunks[1].payload)], [0, 18, 63, 50, 255]);
  assert.equal(chunks[2].payload.length, 0);
});

test('synthetic media integrity gate rejects altered IDAT bytes rather than relaxing browser decoding', () => {
  const corrupt = syntheticPixelPng();
  corrupt[41] ^= 1;
  assert.throws(() => parsePng(corrupt), /IDAT CRC is valid/);
});
