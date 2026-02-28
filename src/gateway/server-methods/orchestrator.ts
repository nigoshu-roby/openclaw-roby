import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { runCommandWithTimeout } from "../../process/exec.js";
import { ErrorCodes, errorShape, validateOrchestratorRunParams } from "../protocol/index.js";
import type { GatewayRequestHandlers } from "./types.js";
import { assertValidParams } from "./validation.js";

const moduleDir = path.dirname(fileURLToPath(import.meta.url));
const defaultRepoRoot = path.resolve(moduleDir, "../../..");
const OPENCLAW_REPO = process.env.OPENCLAW_REPO?.trim() || defaultRepoRoot;
const ORCHESTRATOR_SCRIPT =
  process.env.ROBY_ORCH_SCRIPT?.trim() ||
  path.join(OPENCLAW_REPO, "scripts", "roby-orchestrator.py");
const DEFAULT_ORCH_TIMEOUT_MS = 3_600_000;
const MAX_ATTACHMENT_BYTES = 8_000_000;
const MAX_ATTACHMENTS = 8;

type OrchestratorAttachmentParam = {
  type?: string;
  mimeType?: string;
  fileName?: string;
  content?: string;
};

type SavedAttachment = {
  index: number;
  path: string;
  mimeType: string;
  bytes: number;
};

function sanitizeControlChars(input: string): string {
  let output = "";
  for (const char of input.normalize("NFC")) {
    const code = char.charCodeAt(0);
    if (code === 9 || code === 10 || code === 13 || (code >= 32 && code !== 127)) {
      output += char;
    }
  }
  return output;
}

function extFromMimeType(mimeType: string): string {
  const normalized = mimeType.toLowerCase();
  if (normalized.includes("png")) {
    return ".png";
  }
  if (normalized.includes("jpeg") || normalized.includes("jpg")) {
    return ".jpg";
  }
  if (normalized.includes("webp")) {
    return ".webp";
  }
  if (normalized.includes("gif")) {
    return ".gif";
  }
  if (normalized.includes("bmp")) {
    return ".bmp";
  }
  if (normalized.includes("svg")) {
    return ".svg";
  }
  return ".bin";
}

async function materializeAttachments(
  attachments: OrchestratorAttachmentParam[],
): Promise<{ dir: string | null; files: SavedAttachment[] }> {
  if (!attachments.length) {
    return { dir: null, files: [] };
  }
  if (attachments.length > MAX_ATTACHMENTS) {
    throw new Error(`too many attachments (max ${MAX_ATTACHMENTS})`);
  }

  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "roby-orch-"));
  const files: SavedAttachment[] = [];
  for (let i = 0; i < attachments.length; i++) {
    const item = attachments[i] ?? {};
    const mimeType = String(item.mimeType ?? "").trim() || "application/octet-stream";
    const base64 = String(item.content ?? "").trim();
    if (!base64) {
      continue;
    }
    let decoded: Buffer;
    try {
      decoded = Buffer.from(base64, "base64");
    } catch {
      throw new Error(`attachment ${i + 1} is not valid base64`);
    }
    if (!decoded.length) {
      continue;
    }
    if (decoded.length > MAX_ATTACHMENT_BYTES) {
      throw new Error(`attachment ${i + 1} too large (>${MAX_ATTACHMENT_BYTES} bytes)`);
    }
    const filePath = path.join(dir, `image-${i + 1}${extFromMimeType(mimeType)}`);
    await fs.writeFile(filePath, decoded);
    files.push({
      index: i + 1,
      path: filePath,
      mimeType,
      bytes: decoded.length,
    });
  }
  return { dir, files };
}

async function safeRemoveDir(dir: string | null) {
  if (!dir) {
    return;
  }
  await fs.rm(dir, { recursive: true, force: true });
}

function appendAttachmentHints(message: string, files: SavedAttachment[]): string {
  const base = message.trim() || "添付画像を確認し、必要な回答または実装方針を提示してください。";
  if (!files.length) {
    return base;
  }
  const hints = files
    .map((file) => `- 画像${file.index}: ${file.path} (${file.mimeType}, ${file.bytes} bytes)`)
    .join("\n");
  return [base, "", "[添付画像]", hints, "", "添付画像の内容も考慮して対応してください。"].join(
    "\n",
  );
}

function parseOrchestratorJson(stdout: string): Record<string, unknown> | null {
  const trimmed = stdout.trim();
  if (!trimmed) {
    return null;
  }
  try {
    return JSON.parse(trimmed) as Record<string, unknown>;
  } catch {
    const lines = trimmed.split(/\r?\n/).map((line) => line.trim());
    for (let i = lines.length - 1; i >= 0; i--) {
      const line = lines[i];
      if (!line) {
        continue;
      }
      try {
        return JSON.parse(line) as Record<string, unknown>;
      } catch {
        continue;
      }
    }
  }
  return null;
}

export const orchestratorHandlers: GatewayRequestHandlers = {
  "orchestrator.run": async ({ params, respond, context }) => {
    if (!assertValidParams(params, validateOrchestratorRunParams, "orchestrator.run", respond)) {
      return;
    }

    const p = params as {
      message: string;
      route?: string;
      execute?: boolean;
      verbose?: boolean;
      attachments?: OrchestratorAttachmentParam[];
    };
    const sanitized = sanitizeControlChars(String(p.message ?? ""));
    const attachments = Array.isArray(p.attachments) ? p.attachments : [];
    if (!sanitized.trim() && attachments.length === 0) {
      respond(
        false,
        undefined,
        errorShape(ErrorCodes.INVALID_REQUEST, "message or attachment required"),
      );
      return;
    }

    let attachmentDir: string | null = null;
    try {
      const materialized = await materializeAttachments(attachments);
      attachmentDir = materialized.dir;
      const stdinMessage = appendAttachmentHints(sanitized, materialized.files);

      const timeoutMsRaw = Number(process.env.ROBY_ORCH_RPC_TIMEOUT_MS ?? "");
      const timeoutMs =
        Number.isFinite(timeoutMsRaw) && timeoutMsRaw > 0
          ? Math.floor(timeoutMsRaw)
          : DEFAULT_ORCH_TIMEOUT_MS;

      const argv = ["python3", ORCHESTRATOR_SCRIPT, "--message-stdin", "--json"];
      const route = typeof p.route === "string" ? p.route.trim() : "";
      if (route) {
        argv.push("--route", route);
      }
      if (p.execute !== false) {
        argv.push("--execute");
      }
      if (p.verbose) {
        argv.push("--verbose");
      }

      const run = await runCommandWithTimeout(argv, {
        timeoutMs,
        cwd: OPENCLAW_REPO,
        input: `${stdinMessage}\n`,
        env: {
          ...process.env,
          ROBY_ORCH_ATTACHMENT_DIR: attachmentDir ?? "",
          ROBY_ORCH_ATTACHMENT_FILES: JSON.stringify(materialized.files),
        },
      });

      const parsed = parseOrchestratorJson(run.stdout);
      const ok = run.code === 0 && parsed !== null;
      if (!ok) {
        context.logGateway.warn(
          `orchestrator.run returned code=${run.code} parsed=${parsed !== null} termination=${run.termination}`,
        );
      }

      respond(
        true,
        {
          ok,
          route: parsed && typeof parsed.route === "string" ? parsed.route : route || "auto",
          result: parsed,
          returnCode: run.code,
          termination: run.termination,
          stdout: run.stdout,
          stderr: run.stderr,
          attachments: {
            count: materialized.files.length,
            files: materialized.files,
          },
        },
        undefined,
      );
    } catch (err) {
      respond(false, undefined, errorShape(ErrorCodes.INTERNAL_ERROR, String(err)));
    } finally {
      await safeRemoveDir(attachmentDir);
    }
  },
};
