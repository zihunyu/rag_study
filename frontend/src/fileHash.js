import { sha256 } from "@noble/hashes/sha2.js";
import { bytesToHex } from "@noble/hashes/utils.js";

export const FILE_HASH_CHUNK_SIZE = 2 * 1024 * 1024;

export async function sha256File(file, options = {}) {
  const chunkSize = options.chunkSize ?? FILE_HASH_CHUNK_SIZE;
  if (!Number.isSafeInteger(chunkSize) || chunkSize < 1) {
    throw new Error("FILE_HASH_CHUNK_SIZE_INVALID");
  }
  const hasher = sha256.create();
  let processed = 0;
  while (processed < file.size) {
    const end = Math.min(file.size, processed + chunkSize);
    const chunk = file.slice(processed, end);
    hasher.update(new Uint8Array(await chunk.arrayBuffer()));
    processed = end;
    options.onProgress?.(file.size ? processed / file.size : 1);
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
  if (file.size === 0) options.onProgress?.(1);
  return bytesToHex(hasher.digest());
}
