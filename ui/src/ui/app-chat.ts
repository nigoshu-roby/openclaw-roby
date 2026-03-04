import { parseAgentSessionKey } from "../../../src/sessions/session-key-utils.js";
import { scheduleChatScroll } from "./app-scroll.ts";
import { setLastActiveSessionKey } from "./app-settings.ts";
import { resetToolStream } from "./app-tool-stream.ts";
import type { OpenClawApp } from "./app.ts";
import {
  abortChatRun,
  loadChatHistory,
  sendChatMessage,
  type ChatSendOptions,
} from "./controllers/chat.ts";
import { loadSessions } from "./controllers/sessions.ts";
import type { GatewayHelloOk } from "./gateway.ts";
import { normalizeBasePath } from "./navigation.ts";
import type { ChatAttachment, ChatQueueItem } from "./ui-types.ts";
import { generateUUID } from "./uuid.ts";

export type ChatHost = {
  connected: boolean;
  chatMessages: unknown[];
  chatMessage: string;
  chatAttachments: ChatAttachment[];
  chatQueue: ChatQueueItem[];
  chatRunId: string | null;
  chatSending: boolean;
  lastError: string | null;
  sessionKey: string;
  basePath: string;
  hello: GatewayHelloOk | null;
  chatAvatarUrl: string | null;
  refreshSessionsAfterChat: Set<string>;
};

export type OrchestratorCardMeta = {
  kind?: string;
  requestId?: string;
  route?: string;
  mode?: string;
  inputMessage?: string;
  ocrText?: string;
};

export const CHAT_SESSIONS_ACTIVE_MINUTES = 120;

export function isChatBusy(host: ChatHost) {
  return host.chatSending || Boolean(host.chatRunId);
}

export function isChatStopCommand(text: string) {
  const trimmed = text.trim();
  if (!trimmed) {
    return false;
  }
  const normalized = trimmed.toLowerCase();
  if (normalized === "/stop") {
    return true;
  }
  return (
    normalized === "stop" ||
    normalized === "esc" ||
    normalized === "abort" ||
    normalized === "wait" ||
    normalized === "exit"
  );
}

function isChatResetCommand(text: string) {
  const trimmed = text.trim();
  if (!trimmed) {
    return false;
  }
  const normalized = trimmed.toLowerCase();
  if (normalized === "/new" || normalized === "/reset") {
    return true;
  }
  return normalized.startsWith("/new ") || normalized.startsWith("/reset ");
}

export async function handleAbortChat(host: ChatHost) {
  if (!host.connected) {
    return;
  }
  host.chatMessage = "";
  await abortChatRun(host as unknown as OpenClawApp);
}

function enqueueChatMessage(
  host: ChatHost,
  text: string,
  attachments?: ChatAttachment[],
  refreshSessions?: boolean,
  sendOptions?: ChatSendOptions,
) {
  const trimmed = text.trim();
  const hasAttachments = Boolean(attachments && attachments.length > 0);
  if (!trimmed && !hasAttachments) {
    return;
  }
  host.chatQueue = [
    ...host.chatQueue,
    {
      id: generateUUID(),
      text: trimmed,
      createdAt: Date.now(),
      attachments: hasAttachments ? attachments?.map((att) => ({ ...att })) : undefined,
      refreshSessions,
      routeOverride: sendOptions?.routeOverride,
      forceOrchestrator: sendOptions?.forceOrchestrator,
    },
  ];
}

async function sendChatMessageNow(
  host: ChatHost,
  message: string,
  opts?: {
    previousDraft?: string;
    restoreDraft?: boolean;
    attachments?: ChatAttachment[];
    previousAttachments?: ChatAttachment[];
    restoreAttachments?: boolean;
    refreshSessions?: boolean;
    sendOptions?: ChatSendOptions;
  },
) {
  resetToolStream(host as unknown as Parameters<typeof resetToolStream>[0]);
  const runId = await sendChatMessage(
    host as unknown as OpenClawApp,
    message,
    opts?.attachments,
    opts?.sendOptions,
  );
  const ok = Boolean(runId);
  if (!ok && opts?.previousDraft != null) {
    host.chatMessage = opts.previousDraft;
  }
  if (!ok && opts?.previousAttachments) {
    host.chatAttachments = opts.previousAttachments;
  }
  if (ok) {
    setLastActiveSessionKey(
      host as unknown as Parameters<typeof setLastActiveSessionKey>[0],
      host.sessionKey,
    );
  }
  if (ok && opts?.restoreDraft && opts.previousDraft?.trim()) {
    host.chatMessage = opts.previousDraft;
  }
  if (ok && opts?.restoreAttachments && opts.previousAttachments?.length) {
    host.chatAttachments = opts.previousAttachments;
  }
  scheduleChatScroll(host as unknown as Parameters<typeof scheduleChatScroll>[0]);
  if (ok && !host.chatRunId) {
    void flushChatQueue(host);
  }
  if (ok && opts?.refreshSessions && runId) {
    host.refreshSessionsAfterChat.add(runId);
  }
  return ok;
}

async function flushChatQueue(host: ChatHost) {
  if (!host.connected || isChatBusy(host)) {
    return;
  }
  const [next, ...rest] = host.chatQueue;
  if (!next) {
    return;
  }
  host.chatQueue = rest;
  const ok = await sendChatMessageNow(host, next.text, {
    attachments: next.attachments,
    refreshSessions: next.refreshSessions,
    sendOptions: {
      routeOverride: next.routeOverride,
      forceOrchestrator: next.forceOrchestrator,
    },
  });
  if (!ok) {
    host.chatQueue = [next, ...host.chatQueue];
  }
}

export function removeQueuedMessage(host: ChatHost, id: string) {
  host.chatQueue = host.chatQueue.filter((item) => item.id !== id);
}

function appendAssistantNotice(host: ChatHost, text: string) {
  host.chatMessages = [
    ...host.chatMessages,
    {
      role: "assistant",
      content: [{ type: "text", text }],
      timestamp: Date.now(),
    },
  ];
}

function extractUserMessageText(message: unknown): string {
  if (!message || typeof message !== "object") {
    return "";
  }
  const row = message as Record<string, unknown>;
  const content = row.content;
  if (!Array.isArray(content)) {
    return "";
  }
  const lines: string[] = [];
  for (const block of content) {
    if (!block || typeof block !== "object") {
      continue;
    }
    const b = block as Record<string, unknown>;
    if (b.type !== "text") {
      continue;
    }
    const text = typeof b.text === "string" ? b.text.trim() : "";
    if (text) {
      lines.push(text);
    }
  }
  return lines.join("\n").trim();
}

function extractUserMessageAttachments(message: unknown): ChatAttachment[] {
  if (!message || typeof message !== "object") {
    return [];
  }
  const row = message as Record<string, unknown>;
  const content = row.content;
  if (!Array.isArray(content)) {
    return [];
  }
  const attachments: ChatAttachment[] = [];
  for (const block of content) {
    if (!block || typeof block !== "object") {
      continue;
    }
    const b = block as Record<string, unknown>;
    if (b.type !== "image") {
      continue;
    }
    const source =
      b.source && typeof b.source === "object" ? (b.source as Record<string, unknown>) : null;
    if (!source || source.type !== "base64" || typeof source.data !== "string") {
      continue;
    }
    const mimeType =
      typeof source.media_type === "string" && source.media_type.trim()
        ? source.media_type.trim()
        : "image/png";
    const base = source.data.startsWith("data:")
      ? source.data
      : `data:${mimeType};base64,${source.data}`;
    attachments.push({
      id: generateUUID(),
      dataUrl: base,
      mimeType,
    });
  }
  return attachments;
}

function findUserMessageByRequestId(host: ChatHost, requestId: string): unknown {
  if (!requestId) {
    return null;
  }
  for (let i = host.chatMessages.length - 1; i >= 0; i--) {
    const row = host.chatMessages[i] as Record<string, unknown> | null;
    if (!row || typeof row !== "object") {
      continue;
    }
    if (row.role !== "user") {
      continue;
    }
    if (row.__openclawRequestId === requestId) {
      return row;
    }
  }
  return null;
}

function findLatestUserMessage(host: ChatHost): unknown {
  for (let i = host.chatMessages.length - 1; i >= 0; i--) {
    const row = host.chatMessages[i] as Record<string, unknown> | null;
    if (!row || typeof row !== "object") {
      continue;
    }
    if (row.role === "user") {
      return row;
    }
  }
  return null;
}

function normalizeRoute(route: unknown): string | undefined {
  if (typeof route !== "string") {
    return undefined;
  }
  const value = route.trim();
  return value && value !== "unknown" ? value : undefined;
}

function buildOcrTaskExtractionPrompt(ocrText: string): string {
  const maxChars = 12000;
  const trimmed = ocrText.trim();
  const payload =
    trimmed.length > maxChars
      ? `${trimmed.slice(0, maxChars)}\n...(OCR全文が長いため一部省略)...`
      : trimmed;
  return [
    "以下は画像OCR結果です。この内容だけを根拠にタスクを抽出してください。",
    "不要なメモや感想は除外し、実行可能な作業項目だけを出してください。",
    "出力フォーマット:",
    "## タスク抽出結果",
    "### プロジェクト: <推定名>",
    "- [ ] タスク名",
    "  - 期限: <あれば>",
    "  - 根拠: <1行>",
    "",
    "[OCR結果]",
    payload,
  ].join("\n");
}

export async function rerunOrchestratorResult(host: ChatHost, meta: OrchestratorCardMeta) {
  if (!host.connected) {
    appendAssistantNotice(host, "再実行できません。ゲートウェイ未接続です。");
    return;
  }
  const requestId = typeof meta.requestId === "string" ? meta.requestId.trim() : "";
  const originUserMessage = requestId
    ? (findUserMessageByRequestId(host, requestId) ?? findLatestUserMessage(host))
    : findLatestUserMessage(host);
  const textFromHistory = originUserMessage ? extractUserMessageText(originUserMessage) : "";
  const attachmentsFromHistory = originUserMessage
    ? extractUserMessageAttachments(originUserMessage)
    : [];
  const fallbackMessage = typeof meta.inputMessage === "string" ? meta.inputMessage.trim() : "";
  const message = textFromHistory || fallbackMessage;

  if (!message && attachmentsFromHistory.length === 0) {
    appendAssistantNotice(
      host,
      "再実行に必要な入力が見つかりませんでした。元のユーザーメッセージが履歴に残っているか確認してください。",
    );
    return;
  }

  await handleSendChat(host, message, {
    attachmentsOverride: attachmentsFromHistory,
    sendOptions: {
      forceOrchestrator: true,
      routeOverride: normalizeRoute(meta.route),
    },
  });
}

export async function extractTasksFromOcrResult(host: ChatHost, meta: OrchestratorCardMeta) {
  if (!host.connected) {
    appendAssistantNotice(
      host,
      "OCR結果からのタスク抽出を開始できません。ゲートウェイ未接続です。",
    );
    return;
  }
  const ocrText = typeof meta.ocrText === "string" ? meta.ocrText.trim() : "";
  if (!ocrText) {
    appendAssistantNotice(host, "OCRテキストが見つからないため、タスク抽出に進めませんでした。");
    return;
  }
  await handleSendChat(host, buildOcrTaskExtractionPrompt(ocrText), {
    sendOptions: {
      forceOrchestrator: true,
      routeOverride: "qa_gemini",
    },
  });
}

export async function handleSendChat(
  host: ChatHost,
  messageOverride?: string,
  opts?: {
    restoreDraft?: boolean;
    sendOptions?: ChatSendOptions;
    attachmentsOverride?: ChatAttachment[];
  },
) {
  if (!host.connected) {
    return;
  }
  const previousDraft = host.chatMessage;
  const message = (messageOverride ?? host.chatMessage).trim();
  const attachments = host.chatAttachments ?? [];
  const attachmentsToSend =
    messageOverride == null ? attachments : (opts?.attachmentsOverride ?? []);
  const hasAttachments = attachmentsToSend.length > 0;

  // Allow sending with just attachments (no message text required)
  if (!message && !hasAttachments) {
    return;
  }

  if (isChatStopCommand(message)) {
    await handleAbortChat(host);
    return;
  }

  const refreshSessions = isChatResetCommand(message);
  if (messageOverride == null) {
    host.chatMessage = "";
    // Clear attachments when sending
    host.chatAttachments = [];
  }

  if (isChatBusy(host)) {
    enqueueChatMessage(host, message, attachmentsToSend, refreshSessions, opts?.sendOptions);
    return;
  }

  await sendChatMessageNow(host, message, {
    previousDraft: messageOverride == null ? previousDraft : undefined,
    restoreDraft: Boolean(messageOverride && opts?.restoreDraft),
    attachments: hasAttachments ? attachmentsToSend : undefined,
    previousAttachments: messageOverride == null ? attachments : undefined,
    restoreAttachments: Boolean(messageOverride && opts?.restoreDraft),
    refreshSessions,
    sendOptions: opts?.sendOptions,
  });
}

export async function refreshChat(host: ChatHost, opts?: { scheduleScroll?: boolean }) {
  await Promise.all([
    loadChatHistory(host as unknown as OpenClawApp),
    loadSessions(host as unknown as OpenClawApp, {
      activeMinutes: CHAT_SESSIONS_ACTIVE_MINUTES,
    }),
    refreshChatAvatar(host),
  ]);
  if (opts?.scheduleScroll !== false) {
    scheduleChatScroll(host as unknown as Parameters<typeof scheduleChatScroll>[0]);
  }
}

export const flushChatQueueForEvent = flushChatQueue;

type SessionDefaultsSnapshot = {
  defaultAgentId?: string;
};

function resolveAgentIdForSession(host: ChatHost): string | null {
  const parsed = parseAgentSessionKey(host.sessionKey);
  if (parsed?.agentId) {
    return parsed.agentId;
  }
  const snapshot = host.hello?.snapshot as
    | { sessionDefaults?: SessionDefaultsSnapshot }
    | undefined;
  const fallback = snapshot?.sessionDefaults?.defaultAgentId?.trim();
  return fallback || "main";
}

function buildAvatarMetaUrl(basePath: string, agentId: string): string {
  const base = normalizeBasePath(basePath);
  const encoded = encodeURIComponent(agentId);
  return base ? `${base}/avatar/${encoded}?meta=1` : `/avatar/${encoded}?meta=1`;
}

export async function refreshChatAvatar(host: ChatHost) {
  if (!host.connected) {
    host.chatAvatarUrl = null;
    return;
  }
  const agentId = resolveAgentIdForSession(host);
  if (!agentId) {
    host.chatAvatarUrl = null;
    return;
  }
  host.chatAvatarUrl = null;
  const url = buildAvatarMetaUrl(host.basePath, agentId);
  try {
    const res = await fetch(url, { method: "GET" });
    if (!res.ok) {
      host.chatAvatarUrl = null;
      return;
    }
    const data = (await res.json()) as { avatarUrl?: unknown };
    const avatarUrl = typeof data.avatarUrl === "string" ? data.avatarUrl.trim() : "";
    host.chatAvatarUrl = avatarUrl || null;
  } catch {
    host.chatAvatarUrl = null;
  }
}
