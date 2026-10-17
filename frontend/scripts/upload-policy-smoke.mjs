// Exercise Next's actual body-cloning stream with a payload above its default limit.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { Readable } from 'node:stream';
import bodyStreams from 'next/dist/server/body-streams.js';
import config from '../next.config.mjs';

const data = Buffer.alloc(12 * 1024 * 1024, 0x5a);
const chunks = () => Array.from({ length: data.length / 65536 }, (_, i) => data.subarray(i * 65536, (i + 1) * 65536));
async function measure(limit) {
  const stream = bodyStreams.getCloneableBody(Readable.from(chunks()), limit).cloneBodyStream();
  const hash = createHash('sha256');
  let bytes = 0;
  for await (const chunk of stream) { bytes += chunk.length; hash.update(chunk); }
  return { bytes, hash: hash.digest('hex') };
}
const truncated = await measure(undefined);
assert(truncated.bytes < data.length, 'Reproduce the actual 10 MiB default truncation');
const restored = await measure(config.experimental.middlewareClientMaxBodySize);
assert.equal(restored.bytes, data.length);
assert.equal(restored.hash, createHash('sha256').update(data).digest('hex'));
assert.equal(Number(config.env.NEXT_PUBLIC_MARS_UPLOAD_MAX_BYTES), config.experimental.middlewareClientMaxBodySize);
console.log('Next body clone: 12 MiB truncated by default; configured upload is byte-identical.');
