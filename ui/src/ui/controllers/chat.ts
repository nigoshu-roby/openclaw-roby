import { extractText } from "../chat/message-extract.ts";
import type { GatewayBrowserClient } from "../gateway.ts";
import type { ChatAttachment } from "../ui-types.ts";
import { generateUUID } from "../uuid.ts";

export type ChatState = {
  client: GatewayBrowserClient | null;
  connected: boolean;
  sessionKey: string;
  chatLoading: boolean;
  chatMessages: unknown[];
  chatThinkingLevel: string | null;
  chatSending: boolean;
  chatMessage: string;
  chatAttachments: ChatAttachment[];
  chatRunId: string | null;
  chatStream: string | null;
  chatStreamStartedAt: number | null;
  lastError: string | null;
};

export type ChatEventPayload = {
  runId: string;
  sessionKey: string;
  state: "delta" | "final" | "aborted" | "error";
  message?: unknown;
  errorMessage?: string;
};

type OrchestratorAttachmentMeta = {
  index?: number;
  path?: string;
  mimeType?: string;
  bytes?: number;
};

type OrchestratorRunResponse = {
  ok?: boolean;
  route?: string;
  result?: Record<string, unknown> | null;
  returnCode?: number | null;
  termination?: string | null;
  stdout?: string;
  stderr?: string;
  attachments?: { count?: number; files?: OrchestratorAttachmentMeta[] };
};

type OrchestratorResultMeta = {
  kind: "orchestrator_result";
  route: string;
  executed: boolean;
  ok: boolean;
  actionOk: boolean;
  elapsedMs: number | null;
  returnCode: number | null;
  attachmentsCount: number;
  command?: string;
  summary?: string;
  errorReason?: string;
  stdout?: string;
  stderr?: string;
};

const MAX_ORCHESTRATOR_ATTACHMENTS = 8;
const MAX_ORCHESTRATOR_ATTACHMENT_BYTES = 8_000_000;
const ORCHESTRATOR_CONTEXT_MAX_MESSAGES = 8;
const ORCHESTRATOR_CONTEXT_MAX_CHARS = 2400;
const ORCHESTRATOR_CONTEXT_ITEM_MAX_CHARS = 360;
const ORCHESTRATOR_SUMMARY_MAX_CHARS = 12_000;
const ORCHESTRATOR_STDOUT_MAX_CHARS = 12_000;
const ORCHESTRATOR_STDERR_MAX_CHARS = 6_000;

const CHAT_LOCAL_CACHE_PREFIX = "openclaw.control.chat.cache.v1:";
const CHAT_LOCAL_CACHE_LIMIT = 400;

function chatLocalCacheKey(sessionKey: string): string {
  return `${CHAT_LOCAL_CACHE_PREFIX}${sessionKey || "main"}`;
}

function canUseLocalStorage(): boolean {
  return typeof window !== "undefined" && typeof window.localStorage !== "undefined";
}

function messageSignature(message: unknown): string {
  try {
    return JSON.stringify(message);
  } catch {
    return String(message);
  }
}

function mergeChatMessages(remoteMessages: unknown[], localMessages: unknown[]): unknown[] {
  if (!remoteMessages.length) {
    return localMessages.slice(-CHAT_LOCAL_CACHE_LIMIT);
  }
  if (!localMessages.length) {
    return remoteMessages.slice(-CHAT_LOCAL_CACHE_LIMIT);
  }

  const merged: unknown[] = [];
  const seen = new Set<string>();
  for (const item of [...remoteMessages, ...localMessages]) {
    const sig = messageSignature(item);
    if (seen.has(sig)) {
      continue;
    }
    seen.add(sig);
    merged.push(item);
  }

  const hasTimestamps = merged.some((item) => {
    const msg = item as Record<string, unknown> | null;
    return !!msg && typeof msg === "object" && typeof msg.timestamp === "number";
  });
  if (hasTimestamps) {
    merged.sort((a, b) => {
      const ta =
        typeof (a as Record<string, unknown> | null)?.timestamp === "number"
          ? Number((a as Record<string, unknown>).timestamp)
          : 0;
      const tb =
        typeof (b as Record<string, unknown> | null)?.timestamp === "number"
          ? Number((b as Record<string, unknown>).timestamp)
          : 0;
      return ta - tb;
    });
  }

  return merged.slice(-CHAT_LOCAL_CACHE_LIMIT);
}

function loadLocalChatMessages(sessionKey: string): unknown[] {
  if (!canUseLocalStorage()) {
    return [];
  }
  try {
    const raw = window.localStorage.getItem(chatLocalCacheKey(sessionKey));
    if (!raw) {
      return [];
    }
    const parsed = JSON.parse(raw) as { messages?: unknown[] } | unknown[];
    if (Array.isArray(parsed)) {
      return parsed.slice(-CHAT_LOCAL_CACHE_LIMIT);
    }
    if (Array.isArray((parsed as { messages?: unknown[] }).messages)) {
      return (parsed as { messages: unknown[] }).messages.slice(-CHAT_LOCAL_CACHE_LIMIT);
    }
    return [];
  } catch {
    return [];
  }
}

function persistLocalChatMessages(state: ChatState) {
  if (!canUseLocalStorage()) {
    return;
  }
  try {
    const payload = {
      sessionKey: state.sessionKey,
      updatedAt: Date.now(),
      messages: state.chatMessages.slice(-CHAT_LOCAL_CACHE_LIMIT),
    };
    window.localStorage.setItem(chatLocalCacheKey(state.sessionKey), JSON.stringify(payload));
  } catch {
    // Ignore local persistence failures.
  }
}

export async function loadChatHistory(state: ChatState) {
  if (!state.client || !state.connected) {
    return;
  }
  const localMessages = loadLocalChatMessages(state.sessionKey);
  if (localMessages.length > 0 && state.chatMessages.length === 0) {
    state.chatMessages = localMessages;
  }
  state.chatLoading = true;
  state.lastError = null;
  try {
    const res = await state.client.request<{ messages?: Array<unknown>; thinkingLevel?: string }>(
      "chat.history",
      {
        sessionKey: state.sessionKey,
        limit: 200,
      },
    );
    const remoteMessages = Array.isArray(res.messages) ? res.messages : [];
    const mergedMessages = mergeChatMessages(remoteMessages, localMessages);
    state.chatMessages = mergedMessages;
    persistLocalChatMessages(state);
    state.chatThinkingLevel = res.thinkingLevel ?? null;
  } catch (err) {
    state.lastError = String(err);
    if (state.chatMessages.length === 0 && localMessages.length > 0) {
      state.chatMessages = localMessages;
    }
  } finally {
    state.chatLoading = false;
  }
}

function dataUrlToBase64(dataUrl: string): { content: string; mimeType: string } | null {
  const match = /^data:([^;]+);base64,(.+)$/.exec(dataUrl);
  if (!match) {
    return null;
  }
  return { mimeType: match[1], content: match[2] };
}

function estimateBase64DecodedBytes(content: string): number | null {
  const normalized = content.replace(/\s+/g, "");
  if (!normalized) {
    return 0;
  }
  if (normalized.length % 4 !== 0) {
    return null;
  }
  if (!/^[A-Za-z0-9+/]+={0,2}$/.test(normalized)) {
    return null;
  }
  let padding = 0;
  if (normalized.endsWith("==")) {
    padding = 2;
  } else if (normalized.endsWith("=")) {
    padding = 1;
  }
  const bytes = (normalized.length / 4) * 3 - padding;
  return Number.isFinite(bytes) && bytes >= 0 ? bytes : null;
}

function prepareOrchestratorAttachments(attachments: ChatAttachment[]): Array<{
  type: "image";
  mimeType: string;
  content: string;
}> {
  if (attachments.length > MAX_ORCHESTRATOR_ATTACHMENTS) {
    throw new Error(`添付画像は最大${MAX_ORCHESTRATOR_ATTACHMENTS}件までです。`);
  }

  return attachments.map((att, index) => {
    const itemIndex = index + 1;
    if (!att.mimeType.startsWith("image/")) {
      throw new Error(`添付画像${itemIndex}の形式が不正です（image/*のみ対応）。`);
    }
    const parsed = dataUrlToBase64(att.dataUrl);
    if (!parsed) {
      throw new Error(`添付画像${itemIndex}のデータ形式が不正です。`);
    }
    const sizeBytes = estimateBase64DecodedBytes(parsed.content);
    if (sizeBytes == null) {
      throw new Error(`添付画像${itemIndex}のデータを解析できませんでした。`);
    }
    if (sizeBytes <= 0) {
      throw new Error(`添付画像${itemIndex}が空です。`);
    }
    if (sizeBytes > MAX_ORCHESTRATOR_ATTACHMENT_BYTES) {
      throw new Error(
        `添付画像${itemIndex}が大きすぎます（最大 ${Math.floor(MAX_ORCHESTRATOR_ATTACHMENT_BYTES / 1_000_000)}MB）。`,
      );
    }
    return {
      type: "image" as const,
      mimeType: parsed.mimeType,
      content: parsed.content,
    };
  });
}

function buildOrchestratorContext(
  messages: unknown[],
  maxMessages = ORCHESTRATOR_CONTEXT_MAX_MESSAGES,
  maxChars = ORCHESTRATOR_CONTEXT_MAX_CHARS,
): string {
  if (!Array.isArray(messages) || messages.length === 0) {
    return "";
  }
  const tail = messages.slice(-Math.max(1, maxMessages));
  const lines: string[] = [];
  for (const entry of tail) {
    if (!entry || typeof entry !== "object") {
      continue;
    }
    const row = entry as Record<string, unknown>;
    const roleRaw = typeof row.role === "string" ? row.role : "assistant";
    const role = roleRaw === "user" ? "あなた" : "Roby";
    const text = extractContextText(row);
    if (!text) {
      continue;
    }
    lines.push(`${role}: ${text}`);
  }
  if (lines.length === 0) {
    return "";
  }
  const joined = lines.join("\n");
  if (joined.length <= maxChars) {
    return joined;
  }
  return joined.slice(Math.max(0, joined.length - maxChars));
}

function stripContextNoise(input: string): string {
  if (!input) {
    return "";
  }
  let text = input;
  text = text.replace(/```[\s\S]*?```/g, " ");
  text = text.replace(/\*\*標準出力\*\*[\s\S]*/g, " ");
  text = text.replace(/\*\*標準エラー\*\*[\s\S]*/g, " ");
  text = text.replace(/\*\*実行ログ\*\*[\s\S]*/g, " ");
  text = text.replace(/###\s*オーケストレーション実行結果[\s\S]*/g, " ");
  text = text.replace(/\s+/g, " ").trim();
  if (text.length > ORCHESTRATOR_CONTEXT_ITEM_MAX_CHARS) {
    return text.slice(0, ORCHESTRATOR_CONTEXT_ITEM_MAX_CHARS);
  }
  return text;
}

function extractContextText(message: Record<string, unknown>): string {
  const meta = message.__openclaw as Record<string, unknown> | undefined;
  if (meta?.kind === "orchestrator_result") {
    const summary = typeof meta.summary === "string" ? meta.summary : "";
    const errorReason = typeof meta.errorReason === "string" ? meta.errorReason : "";
    const fromMeta = (summary || errorReason).trim();
    if (fromMeta) {
      return stripContextNoise(fromMeta);
    }
  }
  return stripContextNoise(extractText(message) || "");
}

function buildOrchestratorMessage(message: string, history: unknown[]): string {
  const trimmed = message.trim();
  if (!trimmed) {
    return trimmed;
  }
  const contextText = buildOrchestratorContext(history);
  if (!contextText) {
    return trimmed;
  }
  return [
    "[直近会話コンテキスト]",
    contextText,
    "",
    "[ユーザーの最新依頼]",
    trimmed,
    "",
    "上記コンテキストを前提に回答してください。不要な文脈は無視して構いません。",
  ].join("\n");
}

function isImageTextIntent(message: string): boolean {
  const normalized = message.toLowerCase();
  const hints = ["画像", "添付", "ocr", "文字", "テキスト", "読み取", "読取", "抽出"];
  return hints.some((hint) => normalized.includes(hint) || message.includes(hint));
}

function buildOrchestratorProgressPhases(message: string, hasAttachments: boolean): string[] {
  const phases = ["送信中…", "オーケストレーターでルート判定中…"];
  if (hasAttachments) {
    phases.push("添付画像を検証中…");
    if (isImageTextIntent(message)) {
      phases.push("OCRを実行中…");
    }
  }
  phases.push("回答を生成中…");
  return phases;
}

function startProgressTicker(state: ChatState, phases: string[], intervalMs = 1300): () => void {
  if (phases.length === 0) {
    state.chatStream = "送信中…";
    return () => {};
  }
  state.chatStream = phases[0];
  let index = 0;
  const timer = setInterval(() => {
    index = Math.min(index + 1, phases.length - 1);
    state.chatStream = phases[index];
  }, intervalMs);
  return () => {
    clearInterval(timer);
  };
}

type AssistantMessageNormalizationOptions = {
  roleRequirement: "required" | "optional";
  roleCaseSensitive?: boolean;
  requireContentArray?: boolean;
  allowTextField?: boolean;
};

function normalizeAssistantMessage(
  message: unknown,
  options: AssistantMessageNormalizationOptions,
): Record<string, unknown> | null {
  if (!message || typeof message !== "object") {
    return null;
  }
  const candidate = message as Record<string, unknown>;
  const roleValue = candidate.role;
  if (typeof roleValue === "string") {
    const role = options.roleCaseSensitive ? roleValue : roleValue.toLowerCase();
    if (role !== "assistant") {
      return null;
    }
  } else if (options.roleRequirement === "required") {
    return null;
  }

  if (options.requireContentArray) {
    return Array.isArray(candidate.content) ? candidate : null;
  }
  if (!("content" in candidate) && !(options.allowTextField && "text" in candidate)) {
    return null;
  }
  return candidate;
}

function normalizeAbortedAssistantMessage(message: unknown): Record<string, unknown> | null {
  return normalizeAssistantMessage(message, {
    roleRequirement: "required",
    roleCaseSensitive: true,
    requireContentArray: true,
  });
}

function normalizeFinalAssistantMessage(message: unknown): Record<string, unknown> | null {
  return normalizeAssistantMessage(message, {
    roleRequirement: "optional",
    allowTextField: true,
  });
}

export async function sendChatMessage(
  state: ChatState,
  message: string,
  attachments?: ChatAttachment[],
): Promise<string | null> {
  if (!state.client || !state.connected) {
    return null;
  }
  const msg = message.trim();
  const hasAttachments = attachments && attachments.length > 0;
  if (!msg && !hasAttachments) {
    return null;
  }

  state.chatSending = true;
  state.lastError = null;
  state.chatRunId = null;
  state.chatStream = null;
  state.chatStreamStartedAt = null;

  try {
    const apiAttachments = hasAttachments
      ? prepareOrchestratorAttachments(attachments ?? [])
      : undefined;
    const now = Date.now();

    // Build user message content blocks
    const contentBlocks: Array<{ type: string; text?: string; source?: unknown }> = [];
    if (msg) {
      contentBlocks.push({ type: "text", text: msg });
    }
    if (hasAttachments) {
      for (const att of attachments ?? []) {
        contentBlocks.push({
          type: "image",
          source: { type: "base64", media_type: att.mimeType, data: att.dataUrl },
        });
      }
    }

    state.chatMessages = [
      ...state.chatMessages,
      {
        role: "user",
        content: contentBlocks,
        timestamp: now,
      },
    ];
    persistLocalChatMessages(state);

    const runId = generateUUID();
    state.chatRunId = runId;
    state.chatStream = "送信中…";
    state.chatStreamStartedAt = now;
    const nativeChatMode = shouldUseNativeChatMode(msg, hasAttachments);
    if (!nativeChatMode) {
      // Native chat runId is not created for orchestrator RPC calls.
      state.chatRunId = null;
    }

    if (nativeChatMode) {
      await state.client.request("chat.send", {
        sessionKey: state.sessionKey,
        message: msg,
        deliver: false,
        idempotencyKey: runId,
        attachments: apiAttachments,
      });
      return runId;
    }

    const historyForContext = state.chatMessages.slice(0, -1);
    const orchestratorMessage = buildOrchestratorMessage(msg, historyForContext);
    const stopTicker = startProgressTicker(
      state,
      buildOrchestratorProgressPhases(msg, hasAttachments),
    );
    let response: OrchestratorRunResponse;
    try {
      response = await state.client.request<OrchestratorRunResponse>("orchestrator.run", {
        sessionKey: state.sessionKey,
        message: orchestratorMessage || "添付画像を確認して対応してください。",
        execute: true,
        attachments: apiAttachments,
      });
    } finally {
      stopTicker();
    }
    state.chatStream = null;
    state.chatRunId = null;
    state.chatStreamStartedAt = null;
    const orchestratorMeta = buildOrchestratorResultMeta(response);
    state.chatMessages = [
      ...state.chatMessages,
      {
        role: "assistant",
        content: [{ type: "text", text: formatOrchestratorResult(response) }],
        timestamp: Date.now(),
        __openclaw: orchestratorMeta,
      },
    ];
    persistLocalChatMessages(state);
    return runId;
  } catch (err) {
    const error = String(err);
    state.chatRunId = null;
    state.chatStream = null;
    state.chatStreamStartedAt = null;
    state.lastError = error;
    state.chatMessages = [
      ...state.chatMessages,
      {
        role: "assistant",
        content: [{ type: "text", text: "Error: " + error }],
        timestamp: Date.now(),
      },
    ];
    persistLocalChatMessages(state);
    return null;
  } finally {
    state.chatSending = false;
  }
}

function shouldUseNativeChatMode(message: string, hasAttachments = false): boolean {
  // Image/file attachments should go through orchestrator path so the
  // pipeline can inspect files consistently.
  if (hasAttachments) {
    return false;
  }
  const trimmed = message.trim();
  if (!trimmed) {
    return false;
  }
  return trimmed.startsWith("/") || trimmed.startsWith("!") || trimmed.startsWith("@");
}

function truncateForDisplay(text: string, maxChars: number): string {
  if (text.length <= maxChars) {
    return text;
  }
  return `${text.slice(0, maxChars)}\n...(省略)...`;
}

function firstNonEmptyText(values: Array<unknown>): string {
  for (const value of values) {
    if (typeof value !== "string") {
      continue;
    }
    const normalized = value.trim();
    if (normalized) {
      return normalized;
    }
  }
  return "";
}

function parseOrchestratorAction(result: Record<string, unknown> | null | undefined): {
  route: string;
  elapsedMs: number | null;
  action: Record<string, unknown>;
} {
  if (!result || typeof result !== "object") {
    return { route: "unknown", elapsedMs: null, action: {} };
  }
  const route = typeof result.route === "string" ? result.route : "unknown";
  const elapsedMs = typeof result.elapsed_ms === "number" ? result.elapsed_ms : null;
  const action =
    result.action && typeof result.action === "object"
      ? (result.action as Record<string, unknown>)
      : {};
  return { route, elapsedMs, action };
}

function buildOrchestratorResultMeta(response: OrchestratorRunResponse): OrchestratorResultMeta {
  const payload = response.result;
  const { route, elapsedMs, action } = parseOrchestratorAction(payload);
  const actionRoute = typeof action.route === "string" ? action.route : route;
  const actionOk = action.ok === true;
  const executed = action.executed === true;
  const returnCode = typeof response.returnCode === "number" ? response.returnCode : null;
  const attachmentsCount = Number(response.attachments?.count ?? 0);
  const command = typeof action.command === "string" ? action.command.trim() : "";
  const output = typeof action.output === "string" ? action.output.trim() : "";
  const stdout = firstNonEmptyText([action.stdout, response.stdout]);
  const stderr = firstNonEmptyText([action.stderr, response.stderr]);
  const termination = firstNonEmptyText([response.termination]);
  const terminationReason = termination && termination.toLowerCase() !== "exit" ? termination : "";
  const rawErrorReason = firstNonEmptyText([
    action.error,
    action.detail,
    stderr,
    terminationReason,
  ]);
  const errorReason = actionOk ? "" : rawErrorReason;
  const summary = firstNonEmptyText([output, stdout]);

  return {
    kind: "orchestrator_result",
    route: actionRoute,
    executed,
    ok: actionOk,
    actionOk,
    elapsedMs,
    returnCode,
    attachmentsCount,
    command: command || undefined,
    summary: summary || undefined,
    errorReason: errorReason || undefined,
    stdout: stdout || undefined,
    stderr: stderr || undefined,
  };
}

function formatOrchestratorResult(response: OrchestratorRunResponse): string {
  const meta = buildOrchestratorResultMeta(response);
  const lines = [
    "### オーケストレーション実行結果",
    `- ルート: \`${meta.route}\``,
    `- 実行: ${meta.executed ? "実行済み" : "未実行"}`,
    `- 結果: ${meta.actionOk ? "成功" : "要確認"}`,
  ];
  if (meta.elapsedMs != null) {
    lines.push(`- 経過時間: ${Math.max(0, Math.round(meta.elapsedMs / 1000))}秒`);
  }
  if (meta.returnCode != null) {
    lines.push(`- 終了コード: ${meta.returnCode}`);
  }
  if (meta.attachmentsCount > 0) {
    lines.push(`- 添付画像: ${meta.attachmentsCount}件`);
  }

  if (meta.command) {
    lines.push("", "**実行コマンド**", "```bash", meta.command, "```");
  }

  if (meta.summary) {
    lines.push("", "**要約**", truncateForDisplay(meta.summary, ORCHESTRATOR_SUMMARY_MAX_CHARS));
  }

  const shownStdout = meta.stdout ?? "";
  const shownStderr = meta.stderr ?? "";

  if (shownStdout) {
    lines.push(
      "",
      "**標準出力**",
      "```text",
      truncateForDisplay(shownStdout, ORCHESTRATOR_STDOUT_MAX_CHARS),
      "```",
    );
  }
  if (shownStderr) {
    lines.push(
      "",
      "**標準エラー**",
      "```text",
      truncateForDisplay(shownStderr, ORCHESTRATOR_STDERR_MAX_CHARS),
      "```",
    );
  }

  if (!shownStdout && !shownStderr && !meta.summary) {
    lines.push("", "詳細ログはありません。");
  }
  return lines.join("\n");
}
export async function abortChatRun(state: ChatState): Promise<boolean> {
  if (!state.client || !state.connected) {
    return false;
  }
  const runId = state.chatRunId;
  try {
    await state.client.request(
      "chat.abort",
      runId ? { sessionKey: state.sessionKey, runId } : { sessionKey: state.sessionKey },
    );
    return true;
  } catch (err) {
    state.lastError = String(err);
    return false;
  }
}

export function handleChatEvent(state: ChatState, payload?: ChatEventPayload) {
  if (!payload) {
    return null;
  }
  if (payload.sessionKey !== state.sessionKey) {
    return null;
  }

  // Final from another run (e.g. sub-agent announce): refresh history to show new message.
  // See https://github.com/openclaw/openclaw/issues/1909
  if (payload.runId && state.chatRunId && payload.runId !== state.chatRunId) {
    if (payload.state === "final") {
      const finalMessage = normalizeFinalAssistantMessage(payload.message);
      if (finalMessage) {
        state.chatMessages = [...state.chatMessages, finalMessage];
        persistLocalChatMessages(state);
        return null;
      }
      return "final";
    }
    return null;
  }

  if (payload.state === "delta") {
    const next = extractText(payload.message);
    if (typeof next === "string") {
      const current = state.chatStream ?? "";
      if (!current || next.length >= current.length) {
        state.chatStream = next;
      }
    }
  } else if (payload.state === "final") {
    const finalMessage = normalizeFinalAssistantMessage(payload.message);
    if (finalMessage) {
      state.chatMessages = [...state.chatMessages, finalMessage];
      persistLocalChatMessages(state);
    }
    state.chatStream = null;
    state.chatRunId = null;
    state.chatStreamStartedAt = null;
  } else if (payload.state === "aborted") {
    const normalizedMessage = normalizeAbortedAssistantMessage(payload.message);
    if (normalizedMessage) {
      state.chatMessages = [...state.chatMessages, normalizedMessage];
      persistLocalChatMessages(state);
    } else {
      const streamedText = state.chatStream ?? "";
      if (streamedText.trim()) {
        state.chatMessages = [
          ...state.chatMessages,
          {
            role: "assistant",
            content: [{ type: "text", text: streamedText }],
            timestamp: Date.now(),
          },
        ];
        persistLocalChatMessages(state);
      }
    }
    state.chatStream = null;
    state.chatRunId = null;
    state.chatStreamStartedAt = null;
  } else if (payload.state === "error") {
    state.chatStream = null;
    state.chatRunId = null;
    state.chatStreamStartedAt = null;
    state.lastError = payload.errorMessage ?? "chat error";
  }
  return payload.state;
}
