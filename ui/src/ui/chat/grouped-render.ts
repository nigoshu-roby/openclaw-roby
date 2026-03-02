import { html, nothing } from "lit";
import { unsafeHTML } from "lit/directives/unsafe-html.js";
import type { AssistantIdentity } from "../assistant-identity.ts";
import { icons } from "../icons.ts";
import { toSanitizedMarkdownHtml } from "../markdown.ts";
import { openExternalUrlSafe } from "../open-external-url.ts";
import { detectTextDirection } from "../text-direction.ts";
import type { MessageGroup } from "../types/chat-types.ts";
import { renderCopyAsMarkdownButton } from "./copy-as-markdown.ts";
import {
  extractTextCached,
  extractThinkingCached,
  formatReasoningMarkdown,
} from "./message-extract.ts";
import { isToolResultMessage, normalizeRoleForGrouping } from "./message-normalizer.ts";
import { extractToolCards, renderToolCardSidebar } from "./tool-cards.ts";

type ImageBlock = {
  url: string;
  alt?: string;
};

type OrchestratorResultMeta = {
  kind: "orchestrator_result";
  route?: string;
  executed?: boolean;
  ok?: boolean;
  actionOk?: boolean;
  elapsedMs?: number | null;
  returnCode?: number | null;
  attachmentsCount?: number;
  command?: string;
  summary?: string;
  errorReason?: string;
  stdout?: string;
  stderr?: string;
};

function parseOrchestratorResultMeta(message: unknown): OrchestratorResultMeta | null {
  if (typeof message !== "object" || message === null) {
    return null;
  }
  const raw = (message as Record<string, unknown>).__openclaw;
  if (typeof raw !== "object" || raw === null) {
    return null;
  }
  const meta = raw as Record<string, unknown>;
  if (meta.kind !== "orchestrator_result") {
    return null;
  }
  return raw as OrchestratorResultMeta;
}

function formatElapsedSeconds(elapsedMs: number | null | undefined): string {
  if (typeof elapsedMs !== "number" || Number.isNaN(elapsedMs) || elapsedMs < 0) {
    return "-";
  }
  return `${Math.round(elapsedMs / 1000)}秒`;
}

function renderOrchestratorResultCard(meta: OrchestratorResultMeta) {
  const executed = meta.executed === true;
  const success = meta.actionOk === true;
  const statusClass = !executed ? "warn" : success ? "ok" : "error";
  const statusLabel = !executed ? "未実行" : success ? "成功" : "失敗";
  const statusIcon = !executed ? icons.loader : success ? icons.check : icons.x;
  const routeLabel = (meta.route ?? "").trim() || "-";
  const resultSummary = (meta.summary ?? "").trim();
  const errorReason = (meta.errorReason ?? "").trim();
  const command = (meta.command ?? "").trim();
  const stdout = (meta.stdout ?? "").trim();
  const stderr = (meta.stderr ?? "").trim();
  const returnCodeText = typeof meta.returnCode === "number" ? String(meta.returnCode) : "-";
  const attachmentsCount =
    typeof meta.attachmentsCount === "number" && meta.attachmentsCount > 0
      ? meta.attachmentsCount
      : 0;
  const hasExecutionLog = Boolean(command || stdout || stderr || returnCodeText !== "-");
  const conclusionText =
    resultSummary || (success ? "実行が完了しました。" : "実行結果を確認してください。");

  return html`
    <section class="chat-orch-card chat-orch-card--${statusClass}">
      <header class="chat-orch-card__header">
        <span class="chat-orch-card__icon" aria-hidden="true">${statusIcon}</span>
        <div class="chat-orch-card__title-wrap">
          <strong class="chat-orch-card__title">オーケストレーター</strong>
          <span class="chat-orch-card__status chat-orch-card__status--${statusClass}">${statusLabel}</span>
        </div>
      </header>
      <dl class="chat-orch-card__meta">
        <div>
          <dt>ルート</dt>
          <dd>${routeLabel}</dd>
        </div>
        <div>
          <dt>経過</dt>
          <dd>${formatElapsedSeconds(meta.elapsedMs)}</dd>
        </div>
        <div>
          <dt>終了コード</dt>
          <dd>${returnCodeText}</dd>
        </div>
      </dl>
      ${
        attachmentsCount > 0
          ? html`<p class="chat-orch-card__line">添付画像: ${attachmentsCount}件</p>`
          : nothing
      }

      <section class="chat-orch-card__section">
        <h4 class="chat-orch-card__section-title">結論</h4>
        <p class="chat-orch-card__summary">${conclusionText}</p>
      </section>

      ${
        hasExecutionLog
          ? html`
              <section class="chat-orch-card__section">
                <h4 class="chat-orch-card__section-title">実行ログ</h4>
                <details class="chat-orch-card__details">
                  <summary class="chat-orch-card__details-summary">実行ログを表示</summary>
                  <div class="chat-orch-card__log-grid">
                    ${command ? html`<p class="chat-orch-card__line"><span>実行:</span> <code>${command}</code></p>` : nothing}
                    ${stdout ? html`<p class="chat-orch-card__line"><span>標準出力:</span> ${stdout}</p>` : nothing}
                    ${stderr ? html`<p class="chat-orch-card__line"><span>標準エラー:</span> ${stderr}</p>` : nothing}
                  </div>
                </details>
              </section>
            `
          : nothing
      }

      ${
        errorReason
          ? html`
              <section class="chat-orch-card__section">
                <h4 class="chat-orch-card__section-title">エラー理由</h4>
                <p class="chat-orch-card__error">${errorReason}</p>
              </section>
            `
          : nothing
      }
    </section>
  `;
}

function extractImages(message: unknown): ImageBlock[] {
  const m = message as Record<string, unknown>;
  const content = m.content;
  const images: ImageBlock[] = [];

  if (Array.isArray(content)) {
    for (const block of content) {
      if (typeof block !== "object" || block === null) {
        continue;
      }
      const b = block as Record<string, unknown>;

      if (b.type === "image") {
        // Handle source object format (from sendChatMessage)
        const source = b.source as Record<string, unknown> | undefined;
        if (source?.type === "base64" && typeof source.data === "string") {
          const data = source.data;
          const mediaType = (source.media_type as string) || "image/png";
          // If data is already a data URL, use it directly
          const url = data.startsWith("data:") ? data : `data:${mediaType};base64,${data}`;
          images.push({ url });
        } else if (typeof b.url === "string") {
          images.push({ url: b.url });
        }
      } else if (b.type === "image_url") {
        // OpenAI format
        const imageUrl = b.image_url as Record<string, unknown> | undefined;
        if (typeof imageUrl?.url === "string") {
          images.push({ url: imageUrl.url });
        }
      }
    }
  }

  return images;
}

export function renderReadingIndicatorGroup(assistant?: AssistantIdentity) {
  return html`
    <div class="chat-group assistant">
      ${renderAvatar("assistant", assistant)}
      <div class="chat-group-messages">
        <div class="chat-bubble chat-reading-indicator" aria-hidden="true">
          <span class="chat-reading-indicator__dots">
            <span></span><span></span><span></span>
          </span>
        </div>
      </div>
    </div>
  `;
}

export function renderStreamingGroup(
  text: string,
  startedAt: number,
  onOpenSidebar?: (content: string) => void,
  assistant?: AssistantIdentity,
) {
  const timestamp = new Date(startedAt).toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
  });
  const name = assistant?.name ?? "アシスタント";

  return html`
    <div class="chat-group assistant">
      ${renderAvatar("assistant", assistant)}
      <div class="chat-group-messages">
        ${renderGroupedMessage(
          {
            role: "assistant",
            content: [{ type: "text", text }],
            timestamp: startedAt,
          },
          { isStreaming: true, showReasoning: false },
          onOpenSidebar,
        )}
        <div class="chat-group-footer">
          <span class="chat-sender-name">${name}</span>
          <span class="chat-group-timestamp">${timestamp}</span>
        </div>
      </div>
    </div>
  `;
}

export function renderMessageGroup(
  group: MessageGroup,
  opts: {
    onOpenSidebar?: (content: string) => void;
    showReasoning: boolean;
    assistantName?: string;
    assistantAvatar?: string | null;
  },
) {
  const normalizedRole = normalizeRoleForGrouping(group.role);
  const assistantName = opts.assistantName ?? "アシスタント";
  const who = (() => {
    switch (normalizedRole) {
      case "user":
        return "あなた";
      case "assistant":
        return assistantName;
      case "tool":
        return "ツール";
      case "system":
        return "システム";
      default:
        return normalizedRole;
    }
  })();
  const roleClass =
    normalizedRole === "user" ? "user" : normalizedRole === "assistant" ? "assistant" : "other";
  const timestamp = new Date(group.timestamp).toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
  });

  return html`
    <div class="chat-group ${roleClass}">
      ${renderAvatar(group.role, {
        name: assistantName,
        avatar: opts.assistantAvatar ?? null,
      })}
      <div class="chat-group-messages">
        ${group.messages.map((item, index) =>
          renderGroupedMessage(
            item.message,
            {
              isStreaming: group.isStreaming && index === group.messages.length - 1,
              showReasoning: opts.showReasoning,
            },
            opts.onOpenSidebar,
          ),
        )}
        <div class="chat-group-footer">
          <span class="chat-sender-name">${who}</span>
          <span class="chat-group-timestamp">${timestamp}</span>
        </div>
      </div>
    </div>
  `;
}

function renderAvatar(role: string, assistant?: Pick<AssistantIdentity, "name" | "avatar">) {
  const normalized = normalizeRoleForGrouping(role);
  const assistantName = assistant?.name?.trim() || "アシスタント";
  const assistantAvatar = assistant?.avatar?.trim() || "";
  const initial =
    normalized === "user"
      ? "あなた"
      : normalized === "assistant"
        ? assistantName || "Roby"
        : normalized === "tool"
          ? "ツール"
          : "?";
  const className =
    normalized === "user"
      ? "user"
      : normalized === "assistant"
        ? "assistant"
        : normalized === "tool"
          ? "tool"
          : "other";

  if (assistantAvatar && normalized === "assistant") {
    if (isAvatarUrl(assistantAvatar)) {
      return html`<img
        class="chat-avatar ${className}"
        src="${assistantAvatar}"
        alt="${assistantName}"
      />`;
    }
    return html`<div class="chat-avatar ${className}">${assistantAvatar}</div>`;
  }

  return html`<div class="chat-avatar ${className}">${initial}</div>`;
}

function isAvatarUrl(value: string): boolean {
  return (
    /^https?:\/\//i.test(value) || /^data:image\//i.test(value) || value.startsWith("/") // Relative paths from avatar endpoint
  );
}

function renderMessageImages(images: ImageBlock[]) {
  if (images.length === 0) {
    return nothing;
  }

  const openImage = (url: string) => {
    openExternalUrlSafe(url, { allowDataImage: true });
  };

  return html`
    <div class="chat-message-images">
      ${images.map(
        (img) => html`
          <img
            src=${img.url}
            alt=${img.alt ?? "添付画像"}
            class="chat-message-image"
            @click=${() => openImage(img.url)}
          />
        `,
      )}
    </div>
  `;
}

function renderGroupedMessage(
  message: unknown,
  opts: { isStreaming: boolean; showReasoning: boolean },
  onOpenSidebar?: (content: string) => void,
) {
  const m = message as Record<string, unknown>;
  const role = typeof m.role === "string" ? m.role : "unknown";
  const isToolResult =
    isToolResultMessage(message) ||
    role.toLowerCase() === "toolresult" ||
    role.toLowerCase() === "tool_result" ||
    typeof m.toolCallId === "string" ||
    typeof m.tool_call_id === "string";

  const toolCards = extractToolCards(message);
  const hasToolCards = toolCards.length > 0;
  const images = extractImages(message);
  const hasImages = images.length > 0;
  const orchestratorMeta = parseOrchestratorResultMeta(message);

  const extractedText = extractTextCached(message);
  const extractedThinking =
    opts.showReasoning && role === "assistant" ? extractThinkingCached(message) : null;
  const markdownBase = extractedText?.trim() ? extractedText : null;
  const reasoningMarkdown = extractedThinking ? formatReasoningMarkdown(extractedThinking) : null;
  const markdown = markdownBase;
  const canCopyMarkdown = role === "assistant" && Boolean(markdown?.trim()) && !orchestratorMeta;

  const bubbleClasses = [
    "chat-bubble",
    canCopyMarkdown ? "has-copy" : "",
    opts.isStreaming ? "streaming" : "",
    "fade-in",
  ]
    .filter(Boolean)
    .join(" ");

  if (!markdown && hasToolCards && isToolResult) {
    return html`${toolCards.map((card) => renderToolCardSidebar(card, onOpenSidebar))}`;
  }

  if (!markdown && !hasToolCards && !hasImages) {
    return nothing;
  }

  if (orchestratorMeta) {
    return html`
      <div class="${bubbleClasses} chat-bubble--orchestrator">
        ${renderOrchestratorResultCard(orchestratorMeta)}
        ${renderMessageImages(images)}
        ${toolCards.map((card) => renderToolCardSidebar(card, onOpenSidebar))}
      </div>
    `;
  }

  return html`
    <div class="${bubbleClasses}">
      ${canCopyMarkdown ? renderCopyAsMarkdownButton(markdown!) : nothing}
      ${renderMessageImages(images)}
      ${
        reasoningMarkdown
          ? html`<div class="chat-thinking">${unsafeHTML(
              toSanitizedMarkdownHtml(reasoningMarkdown),
            )}</div>`
          : nothing
      }
      ${
        markdown
          ? html`<div class="chat-text" dir="${detectTextDirection(markdown)}">${unsafeHTML(toSanitizedMarkdownHtml(markdown))}</div>`
          : nothing
      }
      ${toolCards.map((card) => renderToolCardSidebar(card, onOpenSidebar))}
    </div>
  `;
}
