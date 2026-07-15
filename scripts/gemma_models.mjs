/**
 * Shared Gemma 4 model registry + CLI option parsing for gemma_chat.mjs.
 */
import { execSync } from "node:child_process";

export const GEMMA_MODELS = {
  e2b: {
    id: "onnx-community/gemma-4-E2B-it-ONNX",
    label: "Gemma 4 E2B",
    size_gb: 3.1,
    effective_b: 2.3,
  },
  e4b: {
    id: "onnx-community/gemma-4-E4B-it-ONNX",
    label: "Gemma 4 E4B",
    size_gb: 6.0,
    effective_b: 4.5,
  },
};

const DEVICE_CHOICES = ["auto", "cpu", "dml", "webgpu", "cuda", "wasm"];

function hasNvidiaGpu() {
  try {
    const out = execSync("nvidia-smi -L", { stdio: ["ignore", "pipe", "ignore"] }).toString();
    return /NVIDIA/i.test(out);
  } catch {
    return false;
  }
}

/** Pick the best local inference device when --device auto. */
export function resolveDevice(requested = "auto") {
  if (requested !== "auto") {
    return requested;
  }
  if (hasNvidiaGpu()) {
    // WebGPU matches the browser chatbot and works in Node on Windows/Linux.
    return "webgpu";
  }
  return "cpu";
}

export function resolveModel(requested = "e2b") {
  const key = String(requested || "e2b").toLowerCase();
  const info = GEMMA_MODELS[key];
  if (!info) {
    throw new Error(
      `Unknown model '${requested}'. Choose one of: ${Object.keys(GEMMA_MODELS).join(", ")}`,
    );
  }
  return { key, ...info };
}

/** Device for the small embedding model (keep on CPU; WebGPU can crash in Node). */
export function resolveEmbedDevice(requested = "cpu") {
  if (requested === "auto") {
    return "cpu";
  }
  return requested;
}

export function parseGemmaCli(argv = process.argv.slice(2)) {
  let device = "auto";
  let model = "e2b";
  const positional = [];
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--device" && argv[i + 1]) {
      device = argv[++i];
      continue;
    }
    if (arg === "--model" && argv[i + 1]) {
      model = argv[++i];
      continue;
    }
    if (arg === "--server") {
      continue;
    }
    positional.push(arg);
  }
  if (!DEVICE_CHOICES.includes(device)) {
    throw new Error(`Unsupported device '${device}'. Use one of: ${DEVICE_CHOICES.join(", ")}`);
  }
  const modelInfo = resolveModel(model);
  const chosenDevice = resolveDevice(device);
  return { device, chosenDevice, model, modelInfo, positional };
}
