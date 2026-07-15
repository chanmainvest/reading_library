#!/usr/bin/env node
/**
 * Embed a single query string with the same model/options as the browser
 * chatbot and build_chatbot_embeddings.mjs. Writes one L2-normalized float32
 * vector (768 dims) to stdout as raw bytes for scripts/chatbot_rag.py.
 *
 * Usage:
 *   node embed_query.mjs "What is a black swan?"
 *   echo "What is a black swan?" | node embed_query.mjs
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { resolveEmbedDevice } from "./gemma_models.mjs";

const EMBED_MODEL_ID = "onnx-community/embeddinggemma-300m-ONNX";
const DIM = 768;
const HERE = dirname(fileURLToPath(import.meta.url));

async function main() {
  const argv = process.argv.slice(2);
  let device = "auto";
  const textParts = [];
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--device" && argv[i + 1]) {
      device = argv[++i];
      continue;
    }
    textParts.push(argv[i]);
  }
  device = resolveEmbedDevice(device);
  const text = (textParts.length ? textParts.join(" ") : readFileSync(0, "utf8")).trim();
  if (!text) {
    console.error("embed_query: empty query");
    process.exit(1);
  }

  const { pipeline } = await import("@huggingface/transformers");
  const extractor = await pipeline("feature-extraction", EMBED_MODEL_ID, {
    device,
    dtype: "q8",
  });
  const result = await extractor([text], { pooling: "mean", normalize: true });
  if (result.data.length !== DIM) {
    throw new Error(`expected ${DIM} floats, got ${result.data.length}`);
  }
  process.stdout.write(Buffer.from(result.data.buffer, result.data.byteOffset, DIM * 4));
}

main().catch((err) => {
  console.error("embed_query failed:", err);
  process.exit(1);
});
