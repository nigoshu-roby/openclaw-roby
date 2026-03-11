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

function normalizeEpochMs(value: number): number {
  return value > 0 && value < 1_000_000_000_000 ? value * 1000 : value;
}

function parseTimestampMs(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return normalizeEpochMs(value);
  }
  if (typeof value === "string" && value.trim()) {
    const trimmed = value.trim();
    if (/^\d+(?:\.\d+)?$/.test(trimmed)) {
      const numeric = Number(trimmed);
      return Number.isFinite(numeric) ? normalizeEpochMs(numeric) : null;
    }
    const parsed = Date.parse(trimmed);
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

function parseTimestampDate(value: unknown): Date | null {
  const ms = parseTimestampMs(value);
  if (ms !== null) {
    return new Date(ms);
  }
  if (typeof value === "string" && value.trim()) {
    const text = value.trim().endsWith("Z") ? value.trim() : value.trim();
    const parsed = Date.parse(text);
    if (Number.isFinite(parsed)) {
      return new Date(parsed);
    }
  }
  return null;
}

function resolveMinutesLocalPreprocessModel(): string {
  const profile = (process.env.ROBY_ORCH_MINUTES_LLM_PROFILE ?? "hybrid").trim().toLowerCase();
  const explicit = process.env.MINUTES_LOCAL_PREPROCESS_MODEL?.trim();
  if (explicit) {
    return explicit;
  }
  const fast = (process.env.ROBY_ORCH_MINUTES_LOCAL_FAST_MODEL ?? "ollama/llama3.2:3b").trim();
  const quality = (process.env.ROBY_ORCH_MINUTES_LOCAL_QUALITY_MODEL ?? "ollama/qwen2.5:7b").trim();
  const cloud = (
    process.env.ROBY_ORCH_MINUTES_CLOUD_MODEL ??
    process.env.MINUTES_GEMINI_MODEL ??
    "google/gemini-3-flash-preview"
  ).trim();
  if (profile === "local") {
    return quality || fast || cloud;
  }
  if (profile === "cloud") {
    return fast || quality || cloud;
  }
  return fast || quality || cloud;
}

async function readJsonLinesLastTimestamp(filePath: string): Promise<Date | null> {
  try {
    const raw = await fs.readFile(filePath, "utf-8");
    const lines = raw
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean);
    for (let index = lines.length - 1; index >= 0; index -= 1) {
      try {
        const parsed = JSON.parse(lines[index]) as Record<string, unknown>;
        const dt = parseTimestampDate(parsed.ts ?? parsed.timestamp);
        if (dt) {
          return dt;
        }
      } catch {
        continue;
      }
    }
  } catch {
    return null;
  }
  return null;
}

function ageMinutesFrom(date: Date | null, nowMs: number): number | null {
  if (!date) {
    return null;
  }
  return Math.max(0, Math.floor((nowMs - date.getTime()) / 60000));
}

async function buildLiveFreshness() {
  const nowMs = Date.now();
  const targets = [
    {
      name: "self_growth",
      type: "jsonl",
      path: path.join(ROBY_STATE_ROOT, "self_growth_runs.jsonl"),
      maxMinutes: Number.parseInt(process.env.ROBY_DRILL_SELF_GROWTH_MAX_MIN ?? "180", 10),
      remedy: `python3 ${OPENCLAW_REPO}/scripts/roby-self-growth.py`,
    },
    {
      name: "minutes_sync",
      type: "jsonl",
      path: path.join(ROBY_STATE_ROOT, "minutes_runs.jsonl"),
      maxMinutes: Number.parseInt(process.env.ROBY_DRILL_MINUTES_MAX_MIN ?? "240", 10),
      remedy: `python3 ${OPENCLAW_REPO}/scripts/roby-orchestrator.py --cron-task minutes_sync --execute --json`,
    },
    {
      name: "gmail_triage",
      type: "jsonl",
      path: path.join(ROBY_STATE_ROOT, "gmail_triage_runs.jsonl"),
      maxMinutes: Number.parseInt(process.env.ROBY_DRILL_GMAIL_MAX_MIN ?? "120", 10),
      remedy: `python3 ${OPENCLAW_REPO}/scripts/roby-orchestrator.py --cron-task gmail_triage --execute --json`,
    },
    {
      name: "notion_sync",
      type: "json",
      path: path.join(ROBY_STATE_ROOT, "notion_sync_state.json"),
      maxMinutes: Number.parseInt(process.env.ROBY_DRILL_NOTION_MAX_MIN ?? "1440", 10),
      remedy: `python3 ${OPENCLAW_REPO}/scripts/roby-notion-sync.py`,
    },
    {
      name: "weekly_report",
      type: "json",
      path: path.join(ROBY_STATE_ROOT, "reports", "weekly_latest.json"),
      maxMinutes: Number.parseInt(process.env.ROBY_DRILL_WEEKLY_MAX_MIN ?? "10080", 10),
      remedy: `python3 ${OPENCLAW_REPO}/scripts/roby-weekly-report.py --json`,
    },
  ] as const;

  const components = [] as Array<{
    name: string;
    ageMinutes: number | null;
    thresholdMinutes: number;
    stale: boolean;
    missing: boolean;
    ts: number | null;
    remedyCommand: string;
  }>;

  for (const target of targets) {
    let dt: Date | null = null;
    if (target.type === "jsonl") {
      dt = await readJsonLinesLastTimestamp(target.path);
    } else {
      const payload = await readJsonFile(target.path);
      dt = parseTimestampDate(payload?.updated_at ?? payload?.generated_at ?? payload?.ts);
    }
    const ageMinutes = ageMinutesFrom(dt, nowMs);
    const stale = ageMinutes === null || ageMinutes > target.maxMinutes;
    components.push({
      name: target.name,
      ageMinutes,
      thresholdMinutes: target.maxMinutes,
      stale,
      missing: ageMinutes === null,
      ts: dt ? dt.getTime() : null,
      remedyCommand: target.remedy,
    });
  }

  const staleComponents = components.filter((row) => row.stale).map((row) => row.name);
  return {
    present: true,
    ts: nowMs,
    staleCount: staleComponents.length,
    staleComponents,
    allFresh: staleComponents.length === 0,
    components,
  };
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
  const feedbackLatest = await readJsonFile(path.join(ROBY_STATE_ROOT, "feedback_sync_state.json"));
  const ollama = await readOllamaStatus();
  const liveFreshness = await buildLiveFreshness();
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
  const feedbackSummary = (feedbackLatest?.summary as Record<string, unknown> | undefined) ?? {};
  const feedbackCounts = (feedbackSummary.counts as Record<string, unknown> | undefined) ?? {};
  const feedbackReasonCounts =
    (feedbackSummary.actionable_reason_counts as Record<string, unknown> | undefined) ?? {};
  const feedbackImprovementTargets = Array.isArray(feedbackSummary.improvement_targets)
    ? (feedbackSummary.improvement_targets as Array<Record<string, unknown>>)
    : [];
  const feedbackRecentActionable = Array.isArray(feedbackSummary.recent_actionable)
    ? (feedbackSummary.recent_actionable as Array<Record<string, unknown>>)
    : [];
  const feedbackRecentReviewed = Array.isArray(feedbackSummary.recent_reviewed)
    ? (feedbackSummary.recent_reviewed as Array<Record<string, unknown>>)
    : [];

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
    liveFreshness,
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
    feedbackLoop: {
      present: Boolean(feedbackLatest),
      ts: parseTimestampMs(feedbackLatest?.updated_at),
      totalTasks: Number(feedbackSummary.total_tasks ?? 0),
      reviewedCount: Number(feedbackSummary.reviewed_count ?? 0),
      actionableCount: Number(feedbackSummary.actionable_count ?? 0),
      counts: {
        good: Number(feedbackCounts.good ?? 0),
        bad: Number(feedbackCounts.bad ?? 0),
        missed: Number(feedbackCounts.missed ?? 0),
        pending: Number(feedbackCounts.pending ?? 0),
        other: Number(feedbackCounts.other ?? 0),
      },
      actionableReasonCounts: Object.entries(feedbackReasonCounts).map(([reasonCode, count]) => ({
        reasonCode,
        count: Number(count ?? 0),
      })),
      improvementTargets: feedbackImprovementTargets.map((row) => ({
        target: typeof row.target === "string" ? row.target : "",
        label: typeof row.label === "string" ? row.label : "",
        count: Number(row.count ?? 0),
        recommendation: typeof row.recommendation === "string" ? row.recommendation : "",
        reasons: Array.isArray(row.reasons)
          ? row.reasons.map((reason) => ({
              reasonCode:
                typeof (reason as Record<string, unknown>).reason_code === "string"
                  ? ((reason as Record<string, unknown>).reason_code as string)
                  : "",
              count: Number((reason as Record<string, unknown>).count ?? 0),
            }))
          : [],
      })),
      recentActionable: feedbackRecentActionable.map((row) => ({
        id: typeof row.id === "string" ? row.id : "",
        title: typeof row.title === "string" ? row.title : "",
        feedbackState: typeof row.feedback_state === "string" ? row.feedback_state : "",
        feedbackReasonCode:
          typeof row.feedback_reason_code === "string" ? row.feedback_reason_code : "",
        updatedAt: typeof row.updated_at === "string" ? row.updated_at : "",
        originId: typeof row.origin_id === "string" ? row.origin_id : "",
      })),
      recentReviewed: feedbackRecentReviewed.map((row) => ({
        id: typeof row.id === "string" ? row.id : "",
        title: typeof row.title === "string" ? row.title : "",
        feedbackState: typeof row.feedback_state === "string" ? row.feedback_state : "",
        feedbackReasonCode:
          typeof row.feedback_reason_code === "string" ? row.feedback_reason_code : "",
        updatedAt: typeof row.updated_at === "string" ? row.updated_at : "",
        originId: typeof row.origin_id === "string" ? row.origin_id : "",
      })),
    },
    localFirst: {
      ollamaCli: ollama.cliPresent,
      ollamaApiOk: ollama.apiOk,
      configuredModel: ollama.model,
      modelAvailable: ollama.modelAvailable,
      baseUrl: ollama.baseUrl,
      availableModels: ollama.models,
      minutesProfile: (process.env.ROBY_ORCH_MINUTES_LLM_PROFILE ?? "hybrid").trim(),
      minutesLocalPreprocessEnabled:
        String(
          process.env.MINUTES_LOCAL_PREPROCESS_ENABLE ??
            (process.env.ROBY_ORCH_MINUTES_LLM_PROFILE ?? "hybrid").trim().toLowerCase() !==
              "cloud",
        )
          .trim()
          .toLowerCase()
          .match(/^(1|true|yes|on)$/) !== null,
      minutesLocalPreprocessModel: resolveMinutesLocalPreprocessModel().trim(),
      gmailProfile: (process.env.ROBY_ORCH_GMAIL_PROFILE ?? "hybrid").trim(),
      gmailLocalPreclassifyEnabled:
        String(
          process.env.GMAIL_TRIAGE_LOCAL_PRECLASSIFY_ENABLE ??
            process.env.ROBY_ORCH_GMAIL_LOCAL_PRECLASSIFY_FAST ??
            "1",
        )
          .trim()
          .toLowerCase()
          .match(/^(1|true|yes|on)$/) !== null,
      gmailLocalPreclassifyModel: (
        process.env.GMAIL_TRIAGE_LOCAL_PRECLASSIFY_MODEL ??
        process.env.ROBY_ORCH_GMAIL_LLM_FAST_MODEL ??
        "ollama/llama3.2:3b"
      ).trim(),
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
