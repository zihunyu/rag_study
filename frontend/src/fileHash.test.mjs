import assert from "node:assert/strict";
import test from "node:test";

import { FILE_HASH_CHUNK_SIZE, sha256File } from "./fileHash.js";

test("file hashing reads bounded slices and never loads the whole file", async () => {
  const bytes = new Uint8Array(FILE_HASH_CHUNK_SIZE * 2 + 17).fill(97);
  const reads = [];
  const file = {
    size: bytes.length,
    arrayBuffer() {
      throw new Error("whole-file arrayBuffer must not be used");
    },
    slice(start, end) {
      reads.push([start, end]);
      return { arrayBuffer: async () => bytes.slice(start, end).buffer };
    },
  };

  const digest = await sha256File(file);

  assert.equal(digest.length, 64);
  assert.equal(reads.length, 3);
  assert.ok(reads.every(([start, end]) => end - start <= FILE_HASH_CHUNK_SIZE));
});

test("incremental SHA-256 matches the standard known vector", async () => {
  const bytes = new TextEncoder().encode("abc");
  const file = {
    size: bytes.length,
    slice(start, end) {
      return { arrayBuffer: async () => bytes.slice(start, end).buffer };
    },
  };

  assert.equal(
    await sha256File(file, { chunkSize: 1 }),
    "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
  );
});
