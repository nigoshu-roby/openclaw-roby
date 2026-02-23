import { html, nothing } from "lit";
import { formatRelativeTimestamp } from "../format.ts";
import type {
  ChannelAccountSnapshot,
  ChannelUiMetaEntry,
  ChannelsStatusSnapshot,
  SlackStatus,
} from "../types.ts";
import { renderChannelConfigSection } from "./channels.config.ts";
import { channelEnabled, renderChannelAccountCount } from "./channels.shared.ts";
import { renderSlackCard } from "./channels.slack.ts";
import type { ChannelKey, ChannelsChannelData, ChannelsProps } from "./channels.types.ts";

export function renderChannels(props: ChannelsProps) {
  const channels = props.snapshot?.channels as Record<string, unknown> | null;
  const slack = (channels?.slack ?? null) as SlackStatus | null;
  const channelOrder = resolveChannelOrder(props.snapshot);
  const orderedChannels = channelOrder
    .map((key, index) => ({
      key,
      enabled: channelEnabled(key, props),
      order: index,
    }))
    .filter((channel) => channel.key !== "whatsapp")
    .toSorted((a, b) => {
      if (a.enabled !== b.enabled) {
        return a.enabled ? -1 : 1;
      }
      return a.order - b.order;
    });

  return html`
    <section class="grid grid-cols-2">
      ${orderedChannels.map((channel) =>
        renderChannel(channel.key, props, {
          slack,
          channelAccounts: props.snapshot?.channelAccounts ?? null,
        }),
      )}
    </section>

    <section class="card" style="margin-top: 18px;">
      <div class="row" style="justify-content: space-between;">
        <div>
          <div class="card-title">チャネルの状態</div>
          <div class="card-sub">ゲートウェイからのスナップショット。</div>
        </div>
        <div class="muted">${props.lastSuccessAt ? formatRelativeTimestamp(props.lastSuccessAt) : "—"}</div>
      </div>
      ${
        props.lastError
          ? html`<div class="callout danger" style="margin-top: 12px;">
            ${props.lastError}
          </div>`
          : nothing
      }
      <pre class="code-block" style="margin-top: 12px;">
${props.snapshot ? JSON.stringify(props.snapshot, null, 2) : "まだスナップショットはありません。"}
      </pre>
    </section>
  `;
}

function resolveChannelOrder(snapshot: ChannelsStatusSnapshot | null): ChannelKey[] {
  if (snapshot?.channelMeta?.length) {
    const onlySlack = snapshot.channelMeta.map((entry) => entry.id).filter((id) => id === "slack");
    if (onlySlack.length) {
      return onlySlack as ChannelKey[];
    }
  }
  if (snapshot?.channelOrder?.length) {
    const onlySlack = snapshot.channelOrder.filter((id) => id === "slack");
    if (onlySlack.length) {
      return onlySlack as ChannelKey[];
    }
  }
  return ["slack"];
}

function renderChannel(key: ChannelKey, props: ChannelsProps, data: ChannelsChannelData) {
  const accountCountLabel = renderChannelAccountCount(key, data.channelAccounts);
  switch (key) {
    case "slack":
      return renderSlackCard({
        props,
        slack: data.slack,
        accountCountLabel,
      });
    default:
      return renderGenericChannelCard(key, props, data.channelAccounts ?? {});
  }
}

function renderGenericChannelCard(
  key: ChannelKey,
  props: ChannelsProps,
  channelAccounts: Record<string, ChannelAccountSnapshot[]>,
) {
  const label = resolveChannelLabel(props.snapshot, key);
  const status = props.snapshot?.channels?.[key] as Record<string, unknown> | undefined;
  const configured = typeof status?.configured === "boolean" ? status.configured : undefined;
  const running = typeof status?.running === "boolean" ? status.running : undefined;
  const connected = typeof status?.connected === "boolean" ? status.connected : undefined;
  const lastError = typeof status?.lastError === "string" ? status.lastError : undefined;
  const accounts = channelAccounts[key] ?? [];
  const accountCountLabel = renderChannelAccountCount(key, channelAccounts);

  return html`
    <div class="card">
      <div class="card-title">${label}</div>
      <div class="card-sub">チャネルの状態と設定。</div>
      ${accountCountLabel}

      ${
        accounts.length > 0
          ? html`
            <div class="account-card-list">
              ${accounts.map((account) => renderGenericAccount(account))}
            </div>
          `
          : html`
            <div class="status-list" style="margin-top: 16px;">
              <div>
                <span class="label">設定済み</span>
                <span>${configured == null ? "—" : configured ? "はい" : "いいえ"}</span>
              </div>
              <div>
                <span class="label">稼働中</span>
                <span>${running == null ? "—" : running ? "はい" : "いいえ"}</span>
              </div>
              <div>
                <span class="label">接続中</span>
                <span>${connected == null ? "—" : connected ? "はい" : "いいえ"}</span>
              </div>
            </div>
          `
      }

      ${
        lastError
          ? html`<div class="callout danger" style="margin-top: 12px;">
            ${lastError}
          </div>`
          : nothing
      }

      ${renderChannelConfigSection({ channelId: key, props })}
    </div>
  `;
}

function resolveChannelMetaMap(
  snapshot: ChannelsStatusSnapshot | null,
): Record<string, ChannelUiMetaEntry> {
  if (!snapshot?.channelMeta?.length) {
    return {};
  }
  return Object.fromEntries(snapshot.channelMeta.map((entry) => [entry.id, entry]));
}

function resolveChannelLabel(snapshot: ChannelsStatusSnapshot | null, key: string): string {
  const meta = resolveChannelMetaMap(snapshot)[key];
  return meta?.label ?? snapshot?.channelLabels?.[key] ?? key;
}

const RECENT_ACTIVITY_THRESHOLD_MS = 10 * 60 * 1000; // 10 minutes

function hasRecentActivity(account: ChannelAccountSnapshot): boolean {
  if (!account.lastInboundAt) {
    return false;
  }
  return Date.now() - account.lastInboundAt < RECENT_ACTIVITY_THRESHOLD_MS;
}

function deriveRunningStatus(account: ChannelAccountSnapshot): "はい" | "いいえ" | "稼働中" {
  if (account.running) {
    return "はい";
  }
  // If we have recent inbound activity, the channel is effectively running
  if (hasRecentActivity(account)) {
    return "稼働中";
  }
  return "いいえ";
}

function deriveConnectedStatus(
  account: ChannelAccountSnapshot,
): "はい" | "いいえ" | "稼働中" | "—" {
  if (account.connected === true) {
    return "はい";
  }
  if (account.connected === false) {
    return "いいえ";
  }
  // If connected is null/undefined but we have recent activity, show as active
  if (hasRecentActivity(account)) {
    return "稼働中";
  }
  return "—";
}

function renderGenericAccount(account: ChannelAccountSnapshot) {
  const runningStatus = deriveRunningStatus(account);
  const connectedStatus = deriveConnectedStatus(account);

  return html`
    <div class="account-card">
      <div class="account-card-header">
        <div class="account-card-title">${account.name || account.accountId}</div>
        <div class="account-card-id">${account.accountId}</div>
      </div>
      <div class="status-list account-card-status">
        <div>
          <span class="label">稼働中</span>
          <span>${runningStatus}</span>
        </div>
        <div>
          <span class="label">設定済み</span>
          <span>${account.configured ? "はい" : "いいえ"}</span>
        </div>
        <div>
          <span class="label">接続中</span>
          <span>${connectedStatus}</span>
        </div>
        <div>
          <span class="label">最終受信</span>
          <span>${account.lastInboundAt ? formatRelativeTimestamp(account.lastInboundAt) : "—"}</span>
        </div>
        ${
          account.lastError
            ? html`
              <div class="account-card-error">
                ${account.lastError}
              </div>
            `
            : nothing
        }
      </div>
    </div>
  `;
}
