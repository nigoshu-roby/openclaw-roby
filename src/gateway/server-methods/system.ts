import { spawnSync } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { resolveMainSessionKeyFromConfig } from "../../config/sessions.js";
import { getLastHeartbeatEvent } from "../../infra/heartbeat-events.js";
import { setHeartbeatsEnabled } from "../../infra/heartbeat-runner.js";
import { enqueueSystemEvent, isSystemEventContextChanged } from "../../infra/system-events.js";
import { listSystemPresence, updateSystemPresence } from "../../infra/system-presence.js";
import { runCommandWithTimeout } from "../../process/exec.js";
import { ErrorCodes, errorShape } from "../protocol/index.js";
import { broadcastPresenceSnapshot } from "../server/presence-events.js";
import type { GatewayRequestHandlers } from "./types.js";

const moduleDir = path.dirname(fileURLToPath(import.meta.url));
const defaultRepoRoot = path.resolve(moduleDir, "../../..");
const OPENCLAW_REPO = process.env.OPENCLAW_REPO?.trim() || defaultRepoRoot;
const ROBY_STATE_ROOT = path.join(os.homedir(), ".openclaw", "roby");

function parseTimestampMs(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim()) {
    const parsed = Date.parse(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

async function readJsonFile(filePath: string): Promise<Record<string, unknown> | null> {
  try {
    const raw = await fs.readFile(filePath, "utf-8");
    const parsed = JSON.parse(raw) as unknown;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

async function readOllamaStatus() {
  const cliPresent =
    spawnSync("sh", ["-lc", "command -v ollama >/dev/null 2>&1"], {
      stdio: "ignore",
    }).status === 0;
  const baseUrl = (process.env.ROBY_ORCH_OLLAMA_BASE_URL ?? "http://127.0.0.1:11434").trim();
  const model = (process.env.ROBY_ORCH_OLLAMA_MODEL ?? "qwen2.5:7b").trim();
  let apiOk = false;
  let modelAvailable = false;
  let models: string[] = [];
  let error = "";
  try {
    const res = await fetch(`${baseUrl.replace(/\/+$/, "")}/api/tags`, {
      method: "GET",
      signal: AbortSignal.timeout(2500),
    });
    if (res.ok) {
      const payload = (await res.json()) as { models?: Array<{ name?: string | null }> };
      models = Array.isArray(payload.models)
        ? payload.models
            .map((item) => (typeof item?.name === "string" ? item.name.trim() : ""))
            .filter(Boolean)
        : [];
      apiOk = true;
      modelAvailable = models.includes(model);
    } else {
      error = `HTTP ${res.status}`;
    }
  } catch (err) {
    error = String(err);
  }
  return {
    cliPresent,
    apiOk,
    baseUrl,
    model,
    modelAvailable,
    models,
    error,
  };
}

async function buildRobyStatus() {
  const evalLatest = await readJsonFile(path.join(ROBY_STATE_ROOT, "evals", "latest.json"));
  const drillLatest = await readJsonFile(path.join(ROBY_STATE_ROOT, "drills", "latest.json"));
  const weeklyLatest = await readJsonFile(
    path.join(ROBY_STATE_ROOT, "reports", "weekly_latest.json"),
  );
  const ollama = await readOllamaStatus();
  const evalResults = Array.isArray(evalLatest?.results)
    ? (evalLatest.results as Array<Record<string, unknown>>)
    : [];
  const drillChecks = Array.isArray(
    (drillLatest?.latest as Record<string, unknown> | undefined)?.checks,
  )
    ? ((drillLatest?.latest as Record<string, unknown> | undefined)?.checks as Array<
        Record<string, unknown>
      >)
    : [];
  const weeklyOps = (weeklyLatest?.ops as Record<string, unknown> | undefined) ?? {};
  const freshness = (weeklyLatest?.freshness as Record<string, unknown> | undefined) ?? {};
  const audit = (weeklyLatest?.audit as Record<string, unknown> | undefined) ?? {};
  const staleComponents = Array.isArray(freshness.stale_components)
    ? freshness.stale_components
        .map((value) => (typeof value === "string" ? value.trim() : ""))
        .filter(Boolean)
    : [];
  const freshnessDetail = typeof freshness.detail === "string" ? freshness.detail : "";
  const remedyCommands = freshnessDetail.includes("/ remedy:")
    ? freshnessDetail
        .split("/ remedy:", 2)[1]
        .split(";")
        .map((entry) => entry.trim())
        .filter(Boolean)
        .map((entry) => {
          const [name, command] = entry.split("=>", 2);
          return {
            name: (name ?? "").trim(),
            command: (command ?? "").trim(),
          };
        })
        .filter((row) => row.name && row.command)
    : [];
  const opsErrors = Object.entries(weeklyOps)
    .map(([name, value]) => {
      const row = (value as Record<string, unknown> | undefined) ?? {};
      return {
        name,
        errors: Number(row.errors ?? 0),
        runs: Number(row.runs ?? 0),
      };
    })
    .filter((row) => row.errors > 0);

  return {
    generatedAtMs: Date.now(),
    evaluationHarness: {
      present: Boolean(evalLatest),
      ts: parseTimestampMs(evalLatest?.ts),
      allOk: Boolean(evalLatest?.all_ok),
      total: Number(evalLatest?.total ?? 0),
      passed: Number(evalLatest?.passed ?? 0),
      failed: Number(evalLatest?.failed ?? 0),
      p95Ms: Number(
        ((evalLatest?.latency as Record<string, unknown> | undefined)?.p95_ms as
          | number
          | undefined) ?? 0,
      ),
      retriesTotal: Number(
        ((evalLatest?.retries as Record<string, unknown> | undefined)?.total as
          | number
          | undefined) ?? 0,
      ),
      routes: Object.entries((evalLatest?.routes as Record<string, unknown> | undefined) ?? {}).map(
        ([route, value]) => {
          const row = (value as Record<string, unknown> | undefined) ?? {};
          return {
            route,
            total: Number(row.total ?? 0),
            failed: Number(row.failed ?? 0),
          };
        },
      ),
      failedCases: evalResults
        .filter((row) => row.ok === false)
        .map((row) => ({
          id: typeof row.id === "string" ? row.id : "",
          description: typeof row.description === "string" ? row.description : "",
          route: typeof row.route === "string" ? row.route : "",
          failures: Array.isArray(row.failures)
            ? row.failures
                .map((value) => (typeof value === "string" ? value.trim() : ""))
                .filter(Boolean)
            : [],
        })),
    },
    runbookDrill: {
      present: Boolean(drillLatest),
      ts: parseTimestampMs(drillLatest?.ts),
      allOk: Boolean(drillLatest?.all_ok),
      total: Number(drillLatest?.total ?? 0),
      passed: Number(drillLatest?.passed ?? 0),
      failed: Number(drillLatest?.failed ?? 0),
      skipped: Number(drillLatest?.skipped ?? 0),
      failedChecks: drillChecks
        .filter((row) => row.ok === false)
        .map((row) => ({
          id: typeof row.id === "string" ? row.id : "",
          kind: typeof row.kind === "string" ? row.kind : "",
          detail: typeof row.detail === "string" ? row.detail : "",
        })),
    },
    weeklyReport: {
      present: Boolean(weeklyLatest),
      ts: parseTimestampMs(weeklyLatest?.generated_at),
      evalRuns: Number(
        ((weeklyLatest?.eval as Record<string, unknown> | undefined)?.runs as number | undefined) ??
          0,
      ),
      evalFailedRuns: Number(
        ((weeklyLatest?.eval as Record<string, unknown> | undefined)?.failed_runs as
          | number
          | undefined) ?? 0,
      ),
      drillRuns: Number(
        ((weeklyLatest?.drill as Record<string, unknown> | undefined)?.runs as
          | number
          | undefined) ?? 0,
      ),
      drillFailedRuns: Number(
        ((weeklyLatest?.drill as Record<string, unknown> | undefined)?.failed_runs as
          | number
          | undefined) ?? 0,
      ),
      auditOk: Boolean(
        ((weeklyLatest?.audit as Record<string, unknown> | undefined)?.ok as boolean | undefined) ??
        false,
      ),
      staleCount: Number(
        ((weeklyLatest?.freshness as Record<string, unknown> | undefined)?.stale_count as
          | number
          | undefined) ?? 0,
      ),
      staleComponents,
      remedyCommands,
      auditErrors: Number(audit.errors ?? 0),
      opsErrors,
    },
    localFirst: {
      ollamaCli: ollama.cliPresent,
      ollamaApiOk: ollama.apiOk,
      configuredModel: ollama.model,
      modelAvailable: ollama.modelAvailable,
      baseUrl: ollama.baseUrl,
      availableModels: ollama.models,
      error: ollama.error,
    },
  };
}

async function runWeeklyNotify() {
  const scriptPath = path.join(OPENCLAW_REPO, "scripts", "roby-weekly-report.py");
  const run = await runCommandWithTimeout(["python3", scriptPath, "--json", "--notify"], {
    cwd: OPENCLAW_REPO,
    timeoutMs: 60_000,
    noOutputTimeoutMs: 30_000,
  });
  let parsed: Record<string, unknown> | null = null;
  const stdout = (run.stdout ?? "").trim();
  try {
    const raw = JSON.parse(stdout) as unknown;
    if (raw && typeof raw === "object" && !Array.isArray(raw)) {
      parsed = raw as Record<string, unknown>;
    }
  } catch {
    parsed = null;
  }
  return {
    ok: run.code === 0,
    exitCode: run.code ?? 1,
    signal: run.signal ?? "",
    stdout,
    stderr: (run.stderr ?? "").trim(),
    report: parsed,
  };
}

export const systemHandlers: GatewayRequestHandlers = {
  "last-heartbeat": ({ respond }) => {
    respond(true, getLastHeartbeatEvent(), undefined);
  },
  "set-heartbeats": ({ params, respond }) => {
    const enabled = params.enabled;
    if (typeof enabled !== "boolean") {
      respond(
        false,
        undefined,
        errorShape(
          ErrorCodes.INVALID_REQUEST,
          "invalid set-heartbeats params: enabled (boolean) required",
        ),
      );
      return;
    }
    setHeartbeatsEnabled(enabled);
    respond(true, { ok: true, enabled }, undefined);
  },
  "system-presence": ({ respond }) => {
    const presence = listSystemPresence();
    respond(true, presence, undefined);
  },
  "roby.status": async ({ respond }) => {
    respond(true, await buildRobyStatus(), undefined);
  },
  "roby.notifyOpsSummary": async ({ respond }) => {
    respond(true, await runWeeklyNotify(), undefined);
  },
  "system-event": ({ params, respond, context }) => {
    const text = typeof params.text === "string" ? params.text.trim() : "";
    if (!text) {
      respond(false, undefined, errorShape(ErrorCodes.INVALID_REQUEST, "text required"));
      return;
    }
    const sessionKey = resolveMainSessionKeyFromConfig();
    const deviceId = typeof params.deviceId === "string" ? params.deviceId : undefined;
    const instanceId = typeof params.instanceId === "string" ? params.instanceId : undefined;
    const host = typeof params.host === "string" ? params.host : undefined;
    const ip = typeof params.ip === "string" ? params.ip : undefined;
    const mode = typeof params.mode === "string" ? params.mode : undefined;
    const version = typeof params.version === "string" ? params.version : undefined;
    const platform = typeof params.platform === "string" ? params.platform : undefined;
    const deviceFamily = typeof params.deviceFamily === "string" ? params.deviceFamily : undefined;
    const modelIdentifier =
      typeof params.modelIdentifier === "string" ? params.modelIdentifier : undefined;
    const lastInputSeconds =
      typeof params.lastInputSeconds === "number" && Number.isFinite(params.lastInputSeconds)
        ? params.lastInputSeconds
        : undefined;
    const reason = typeof params.reason === "string" ? params.reason : undefined;
    const roles =
      Array.isArray(params.roles) && params.roles.every((t) => typeof t === "string")
        ? params.roles
        : undefined;
    const scopes =
      Array.isArray(params.scopes) && params.scopes.every((t) => typeof t === "string")
        ? params.scopes
        : undefined;
    const tags =
      Array.isArray(params.tags) && params.tags.every((t) => typeof t === "string")
        ? params.tags
        : undefined;
    const presenceUpdate = updateSystemPresence({
      text,
      deviceId,
      instanceId,
      host,
      ip,
      mode,
      version,
      platform,
      deviceFamily,
      modelIdentifier,
      lastInputSeconds,
      reason,
      roles,
      scopes,
      tags,
    });
    const isNodePresenceLine = text.startsWith("Node:");
    if (isNodePresenceLine) {
      const next = presenceUpdate.next;
      const changed = new Set(presenceUpdate.changedKeys);
      const reasonValue = next.reason ?? reason;
      const normalizedReason = (reasonValue ?? "").toLowerCase();
      const ignoreReason =
        normalizedReason.startsWith("periodic") || normalizedReason === "heartbeat";
      const hostChanged = changed.has("host");
      const ipChanged = changed.has("ip");
      const versionChanged = changed.has("version");
      const modeChanged = changed.has("mode");
      const reasonChanged = changed.has("reason") && !ignoreReason;
      const hasChanges = hostChanged || ipChanged || versionChanged || modeChanged || reasonChanged;
      if (hasChanges) {
        const contextChanged = isSystemEventContextChanged(sessionKey, presenceUpdate.key);
        const parts: string[] = [];
        if (contextChanged || hostChanged || ipChanged) {
          const hostLabel = next.host?.trim() || "Unknown";
          const ipLabel = next.ip?.trim();
          parts.push(`Node: ${hostLabel}${ipLabel ? ` (${ipLabel})` : ""}`);
        }
        if (versionChanged) {
          parts.push(`app ${next.version?.trim() || "unknown"}`);
        }
        if (modeChanged) {
          parts.push(`mode ${next.mode?.trim() || "unknown"}`);
        }
        if (reasonChanged) {
          parts.push(`reason ${reasonValue?.trim() || "event"}`);
        }
        const deltaText = parts.join(" · ");
        if (deltaText) {
          enqueueSystemEvent(deltaText, {
            sessionKey,
            contextKey: presenceUpdate.key,
          });
        }
      }
    } else {
      enqueueSystemEvent(text, { sessionKey });
    }
    broadcastPresenceSnapshot({
      broadcast: context.broadcast,
      incrementPresenceVersion: context.incrementPresenceVersion,
      getHealthVersion: context.getHealthVersion,
    });
    respond(true, { ok: true }, undefined);
  },
};
