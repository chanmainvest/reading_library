#!/usr/bin/env node
/**
 * Local Gemma 4 inference for the reading-library CLI.
 *
 * Text-only via Gemma4ForCausalLM (embed_tokens + decoder; skips audio/vision).
 *
 * Server mode (model loaded once, reused across turns):
 *   node gemma_chat.mjs --server [--model e2b|e4b] [--device auto|webgpu|dml|cpu]
 *   stderr: {"type":"ready","model":"...","device":"..."}
 *   stdin:  one JSON object per line: {"id":1,"messages":[...]}
 *   stdout: {"type":"token","text":"..."} chunks, then {"type":"done","id":1,"text":"..."}
 *
 * One-shot mode:
 *   echo '{"messages":[...]}' | node gemma_chat.mjs --model e2b --device webgpu
 */
import { createInterface } from "node:readline";
import { stdin } from "node:process";
import { parseGemmaCli } from "./gemma_models.mjs";

const MAX_NEW_TOKENS = 512;

const { chosenDevice, modelInfo } = parseGemmaCli();

function logStatus(obj) {
  process.stderr.write(JSON.stringify(obj) + "\n");
}

function emit(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

let generatorPromise = null;
let lastDownload = null;

function onDownloadProgress(data) {
  if (data?.status === "progress" && data.total) {
    const pct = Math.round((100 * (data.loaded || 0)) / data.total);
    const key = `${data.file}:${pct}`;
    if (key !== lastDownload) {
      lastDownload = key;
      logStatus({ type: "download", file: data.file, pct });
    }
  }
}

async function loadGenerator(device = chosenDevice) {
  if (!generatorPromise) {
    generatorPromise = (async () => {
      logStatus({
        type: "loading",
        model: modelInfo.id,
        model_key: modelInfo.key,
        model_label: modelInfo.label,
        size_gb: modelInfo.size_gb,
        device,
      });
      const { AutoProcessor, Gemma4ForCausalLM, TextStreamer } =
        await import("@huggingface/transformers");
      const processor = await AutoProcessor.from_pretrained(modelInfo.id, {
        progress_callback: onDownloadProgress,
      });
      const model = await Gemma4ForCausalLM.from_pretrained(modelInfo.id, {
        dtype: "q4f16",
        device,
        progress_callback: onDownloadProgress,
      });
      return { processor, model, TextStreamer };
    })();
  }
  return generatorPromise;
}

async function generate(messages, { id = 0, max_new_tokens = MAX_NEW_TOKENS } = {}) {
  const { processor, model, TextStreamer } = await loadGenerator();
  const prompt = processor.apply_chat_template(messages, {
    enable_thinking: false,
    add_generation_prompt: true,
    tokenize: false,
  });
  const inputs = await processor(prompt, null, null, { add_special_tokens: false });

  let acc = "";
  const streamer = new TextStreamer(processor.tokenizer, {
    skip_prompt: true,
    skip_special_tokens: true,
    callback_function: (text) => {
      if (typeof text !== "string" || !text) return;
      acc += text;
      emit({ type: "token", id, text });
    },
  });

  const outputs = await model.generate({
    ...inputs,
    max_new_tokens,
    do_sample: false,
    streamer,
  });

  if (!acc) {
    const inputLen = inputs.input_ids.dims.at(-1);
    const decoded = processor.batch_decode(outputs.slice(null, [inputLen, null]), {
      skip_special_tokens: true,
    });
    acc = (decoded && decoded[0]) || "";
    if (acc) emit({ type: "token", id, text: acc });
  }
  emit({ type: "done", id, text: acc });
  return acc;
}

async function readStdinJson() {
  const chunks = [];
  for await (const chunk of stdin) chunks.push(chunk);
  const raw = Buffer.concat(chunks).toString("utf8").trim();
  if (!raw) throw new Error("empty stdin");
  return JSON.parse(raw);
}

async function runServer() {
  await loadGenerator(chosenDevice);
  logStatus({
    type: "ready",
    model: modelInfo.id,
    model_key: modelInfo.key,
    model_label: modelInfo.label,
    device: chosenDevice,
  });

  const rl = createInterface({ input: stdin, crlfDelay: Infinity });
  for await (const line of rl) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    let req;
    try {
      req = JSON.parse(trimmed);
    } catch (err) {
      emit({ type: "error", message: `invalid JSON: ${err}` });
      continue;
    }
    try {
      await generate(req.messages || [], {
        id: req.id ?? 0,
        max_new_tokens: req.max_new_tokens ?? MAX_NEW_TOKENS,
      });
    } catch (err) {
      emit({ type: "error", id: req.id ?? 0, message: String(err?.message || err) });
    }
  }
}

async function main() {
  if (process.argv.includes("--server")) {
    await runServer();
    return;
  }
  const payload = await readStdinJson();
  await generate(payload.messages || [], { id: 0, max_new_tokens: payload.max_new_tokens });
}

main().catch((err) => {
  logStatus({ type: "error", message: String(err?.message || err) });
  process.exit(1);
});
