import { html, nothing } from "lit";
import { ifDefined } from "lit/directives/if-defined.js";
import type { CronFieldErrors, CronFieldKey } from "../controllers/cron.ts";
import { formatRelativeTimestamp, formatMs } from "../format.ts";
import { pathForTab } from "../navigation.ts";
import { formatCronSchedule, formatNextRun } from "../presenter.ts";
import type { ChannelUiMetaEntry, CronJob, CronRunLogEntry, CronStatus } from "../types.ts";
import type {
  CronDeliveryStatus,
  CronJobsEnabledFilter,
  CronRunScope,
  CronRunsStatusValue,
  CronJobsSortBy,
  CronRunsStatusFilter,
  CronSortDir,
} from "../types.ts";
import type { CronFormState } from "../ui-types.ts";

export type CronProps = {
  basePath: string;
  loading: boolean;
  jobsLoadingMore: boolean;
  status: CronStatus | null;
  jobs: CronJob[];
  jobsTotal: number;
  jobsHasMore: boolean;
  jobsQuery: string;
  jobsEnabledFilter: CronJobsEnabledFilter;
  jobsSortBy: CronJobsSortBy;
  jobsSortDir: CronSortDir;
  error: string | null;
  busy: boolean;
  form: CronFormState;
  fieldErrors: CronFieldErrors;
  canSubmit: boolean;
  editingJobId: string | null;
  channels: string[];
  channelLabels?: Record<string, string>;
  channelMeta?: ChannelUiMetaEntry[];
  runsJobId: string | null;
  runs: CronRunLogEntry[];
  runsTotal: number;
  runsHasMore: boolean;
  runsLoadingMore: boolean;
  runsScope: CronRunScope;
  runsStatuses: CronRunsStatusValue[];
  runsDeliveryStatuses: CronDeliveryStatus[];
  runsStatusFilter: CronRunsStatusFilter;
  runsQuery: string;
  runsSortDir: CronSortDir;
  agentSuggestions: string[];
  modelSuggestions: string[];
  thinkingSuggestions: string[];
  timezoneSuggestions: string[];
  deliveryToSuggestions: string[];
  onFormChange: (patch: Partial<CronFormState>) => void;
  onRefresh: () => void;
  onAdd: () => void;
  onEdit: (job: CronJob) => void;
  onClone: (job: CronJob) => void;
  onCancelEdit: () => void;
  onToggle: (job: CronJob, enabled: boolean) => void;
  onRun: (job: CronJob) => void;
  onRemove: (job: CronJob) => void;
  onLoadRuns: (jobId: string) => void;
  onLoadMoreJobs: () => void;
  onJobsFiltersChange: (patch: {
    cronJobsQuery?: string;
    cronJobsEnabledFilter?: CronJobsEnabledFilter;
    cronJobsSortBy?: CronJobsSortBy;
    cronJobsSortDir?: CronSortDir;
  }) => void | Promise<void>;
  onLoadMoreRuns: () => void;
  onRunsFiltersChange: (patch: {
    cronRunsScope?: CronRunScope;
    cronRunsStatuses?: CronRunsStatusValue[];
    cronRunsDeliveryStatuses?: CronDeliveryStatus[];
    cronRunsStatusFilter?: CronRunsStatusFilter;
    cronRunsQuery?: string;
    cronRunsSortDir?: CronSortDir;
  }) => void | Promise<void>;
};

const RUN_STATUS_OPTIONS: Array<{ value: CronRunsStatusValue; label: string }> = [
  { value: "ok", label: "OK" },
  { value: "error", label: "Error" },
  { value: "skipped", label: "Skipped" },
];

const RUN_DELIVERY_OPTIONS: Array<{ value: CronDeliveryStatus; label: string }> = [
  { value: "delivered", label: "Delivered" },
  { value: "not-delivered", label: "Not delivered" },
  { value: "unknown", label: "Unknown" },
  { value: "not-requested", label: "Not requested" },
];

function toggleSelection<T extends string>(selected: T[], value: T, checked: boolean): T[] {
  const set = new Set(selected);
  if (checked) {
    set.add(value);
  } else {
    set.delete(value);
  }
  return Array.from(set);
}

function summarizeSelection(selectedLabels: string[], allLabel: string) {
  if (selectedLabels.length === 0) {
    return allLabel;
  }
  if (selectedLabels.length <= 2) {
    return selectedLabels.join(", ");
  }
  return `${selectedLabels[0]} +${selectedLabels.length - 1}`;
}

function buildChannelOptions(props: CronProps): string[] {
  const options = ["last", ...props.channels.filter(Boolean)];
  const current = props.form.deliveryChannel?.trim();
  if (current && !options.includes(current)) {
    options.push(current);
  }
  const seen = new Set<string>();
  return options.filter((value) => {
    if (seen.has(value)) {
      return false;
    }
    seen.add(value);
    return true;
  });
}

const NA_LABEL = "—";

function resolveChannelLabel(props: CronProps, channel: string): string {
  if (channel === "last") {
    return "前回のチャネル";
  }
  const meta = props.channelMeta?.find((entry) => entry.id === channel);
  if (meta?.label) {
    return meta.label;
  }
  return props.channelLabels?.[channel] ?? channel;
}

function formatSessionTarget(value?: string | null): string {
  switch (value) {
    case "main":
      return "メイン";
    case "isolated":
      return "隔離";
    default:
      return value ?? NA_LABEL;
  }
}

function formatWakeMode(value?: string | null): string {
  switch (value) {
    case "now":
      return "即時";
    case "next-heartbeat":
      return "次のハートビート";
    default:
      return value ?? NA_LABEL;
  }
}

function formatStatusLabel(status?: string | null): string {
  switch (status) {
    case "ok":
      return "成功";
    case "error":
      return "失敗";
    case "skipped":
      return "スキップ";
    case "n/a":
      return NA_LABEL;
    default:
      return status ?? NA_LABEL;
  }
}

function formatDeliveryChannel(channel?: string | null): string {
  if (!channel) {
    return NA_LABEL;
  }
  return channel === "last" ? "前回" : channel;
}

export function renderCron(props: CronProps) {
  const isEditing = Boolean(props.editingJobId);
  const isAgentTurn = props.form.payloadKind === "agentTurn";
  const isCronSchedule = props.form.scheduleKind === "cron";
  const channelOptions = buildChannelOptions(props);
  const selectedJob =
    props.runsJobId == null ? undefined : props.jobs.find((job) => job.id === props.runsJobId);
  const selectedRunTitle = selectedJob?.name ?? props.runsJobId ?? "ジョブを選択";
  const orderedRuns = props.runs.toSorted((a, b) => b.ts - a.ts);
  const supportsAnnounce =
    props.form.sessionTarget === "isolated" && props.form.payloadKind === "agentTurn";
  const selectedDeliveryMode =
    props.form.deliveryMode === "announce" && !supportsAnnounce ? "none" : props.form.deliveryMode;
  const blockingFields = collectBlockingFields(props.fieldErrors, props.form, selectedDeliveryMode);
  const blockedByValidation = !props.busy && blockingFields.length > 0;
  const submitDisabledReason =
    blockedByValidation && !props.canSubmit
      ? `Fix ${blockingFields.length} ${blockingFields.length === 1 ? "field" : "fields"} to continue.`
      : "";
  return html`
    <section class="grid grid-cols-2">
      <div class="card">
        <div class="card-title">スケジューラ</div>
        <div class="card-sub">ゲートウェイのスケジューラ状況。</div>
        <div class="stat-grid" style="margin-top: 16px;">
          <div class="stat">
            <div class="stat-label">有効</div>
            <div class="stat-value">
              ${props.status ? (props.status.enabled ? "はい" : "いいえ") : NA_LABEL}
            </div>
          </div>
          <div class="stat">
            <div class="stat-label">ジョブ数</div>
            <div class="stat-value">${props.status?.jobs ?? NA_LABEL}</div>
          </div>
          <div class="stat">
            <div class="stat-label">次回起動</div>
            <div class="stat-value">${formatNextRun(props.status?.nextWakeAtMs ?? null)}</div>
          </div>
        </div>
        <div class="row" style="margin-top: 12px;">
          <button class="btn" ?disabled=${props.loading} @click=${props.onRefresh}>
            ${props.loading ? "更新中…" : "更新"}
          </button>
          ${props.error ? html`<span class="muted">${props.error}</span>` : nothing}
        </div>
      </div>
      <div class="cron-summary-strip__actions">
        <button class="btn" ?disabled=${props.loading} @click=${props.onRefresh}>
          ${props.loading ? "Refreshing..." : "Refresh"}
        </button>
        ${props.error ? html`<span class="muted">${props.error}</span>` : nothing}
      </div>
    </section>

      <div class="card">
        <div class="card-title">新規ジョブ</div>
        <div class="card-sub">定期起動やエージェント実行を作成します。</div>
        <div class="form-grid" style="margin-top: 16px;">
          <label class="field">
            <span>名前</span>
            <input
              .value=${props.form.name}
              @input=${(e: Event) =>
                props.onFormChange({ name: (e.target as HTMLInputElement).value })}
            />
          </label>
          <label class="field">
            <span>説明</span>
            <input
              .value=${props.form.description}
              @input=${(e: Event) =>
                props.onFormChange({ description: (e.target as HTMLInputElement).value })}
            />
          </label>
          <label class="field">
            <span>エージェントID</span>
            <input
              .value=${props.form.agentId}
              @input=${(e: Event) =>
                props.onFormChange({ agentId: (e.target as HTMLInputElement).value })}
              placeholder="default"
            />
          </label>
          <label class="field checkbox">
            <span>有効</span>
            <input
              type="checkbox"
              .checked=${props.form.enabled}
              @change=${(e: Event) =>
                props.onFormChange({ enabled: (e.target as HTMLInputElement).checked })}
            />
          </label>
          <label class="field">
            <span>スケジュール</span>
            <select
              .value=${props.form.scheduleKind}
              @change=${(e: Event) =>
                props.onFormChange({
                  scheduleKind: (e.target as HTMLSelectElement)
                    .value as CronFormState["scheduleKind"],
                })}
            >
              <option value="every">間隔</option>
              <option value="at">日時</option>
              <option value="cron">Cron式</option>
            </select>
          </label>
        </div>
        ${renderScheduleFields(props)}
        <div class="form-grid" style="margin-top: 12px;">
          <label class="field">
            <span>セッション</span>
            <select
              .value=${props.form.sessionTarget}
              @change=${(e: Event) =>
                props.onFormChange({
                  sessionTarget: (e.target as HTMLSelectElement)
                    .value as CronFormState["sessionTarget"],
                })}
            >
              <option value="main">メイン</option>
              <option value="isolated">隔離</option>
            </select>
          </label>
          <label class="field">
            <span>起動モード</span>
            <select
              .value=${props.form.wakeMode}
              @change=${(e: Event) =>
                props.onFormChange({
                  wakeMode: (e.target as HTMLSelectElement).value as CronFormState["wakeMode"],
                })}
            >
              <option value="now">今すぐ</option>
              <option value="next-heartbeat">次のハートビート</option>
            </select>
          </label>
          <label class="field">
            <span>ペイロード</span>
            <select
              .value=${props.form.payloadKind}
              @change=${(e: Event) =>
                props.onFormChange({
                  payloadKind: (e.target as HTMLSelectElement)
                    .value as CronFormState["payloadKind"],
                })}
            >
              <option value="systemEvent">システムイベント</option>
              <option value="agentTurn">エージェント実行</option>
            </select>
          </label>
        </div>
        <label class="field" style="margin-top: 12px;">
          <span>${props.form.payloadKind === "systemEvent" ? "システム本文" : "エージェントメッセージ"}</span>
          <textarea
            .value=${props.form.payloadText}
            @input=${(e: Event) =>
              props.onFormChange({
                payloadText: (e.target as HTMLTextAreaElement).value,
              })}
            rows="4"
          ></textarea>
        </label>
        <div class="form-grid" style="margin-top: 12px;">
          <label class="field">
            <span>配信</span>
            <select
              .value=${selectedDeliveryMode}
              @change=${(e: Event) =>
                props.onFormChange({
                  deliveryMode: (e.target as HTMLSelectElement)
                    .value as CronFormState["deliveryMode"],
                })}
            >
              ${
                isAgentTurn
                  ? html`
                      <option value="announce">要約を通知（既定）</option>
                    `
                  : nothing
              }
              <option value="webhook">Webhook送信</option>
              <option value="none">なし（内部）</option>
            </select>
          </label>
          ${
            props.form.payloadKind === "agentTurn"
              ? html`
                  <label class="field">
                    <span>タイムアウト（秒）</span>
                    <input
                      .value=${props.form.timeoutSeconds}
                      @input=${(e: Event) =>
                        props.onFormChange({
                          timeoutSeconds: (e.target as HTMLInputElement).value,
                        })}
                    />
                  </label>
                `
              : nothing
          }
          ${
            selectedDeliveryMode !== "none"
              ? html`
                  <label class="field">
                    <span>${selectedDeliveryMode === "webhook" ? "Webhook URL" : "チャネル"}</span>
                    ${
                      selectedDeliveryMode === "webhook"
                        ? html`
                            <input
                              .value=${props.form.deliveryTo}
                              @input=${(e: Event) =>
                                props.onFormChange({
                                  deliveryTo: (e.target as HTMLInputElement).value,
                                })}
                              placeholder="https://example.invalid/cron"
                            />
                          `
                        : html`
                            <select
                              .value=${props.form.deliveryChannel || "last"}
                              @change=${(e: Event) =>
                                props.onFormChange({
                                  deliveryChannel: (e.target as HTMLSelectElement).value,
                                })}
                            >
                              ${channelOptions.map(
                                (channel) =>
                                  html`<option value=${channel}>
                                    ${resolveChannelLabel(props, channel)}
                                  </option>`,
                              )}
                            </select>
                          `
                    }
                  </label>
                  ${
                    supportsAnnounce
                      ? html`
                          <label class="field">
                            <span>宛先</span>
                            <input
                              .value=${props.form.deliveryTo}
                              @input=${(e: Event) =>
                                props.onFormChange({
                                  deliveryTo: (e.target as HTMLInputElement).value,
                                })}
                              placeholder="+1555… または チャットID"
                            />
                          </label>
                        `
                      : nothing
                  }
                  <option value="webhook">Webhook POST</option>
                  <option value="none">None (internal)</option>
                </select>
                <div class="cron-help">Announce posts a summary to chat. None keeps execution internal.</div>
              </label>
              ${
                selectedDeliveryMode !== "none"
                  ? html`
                      <label class="field ${selectedDeliveryMode === "webhook" ? "cron-span-2" : ""}">
                        ${renderFieldLabel(selectedDeliveryMode === "webhook" ? "Webhook URL" : "Channel", selectedDeliveryMode === "webhook")}
                        ${
                          selectedDeliveryMode === "webhook"
                            ? html`
                                <input
                                  id="cron-delivery-to"
                                  .value=${props.form.deliveryTo}
                                  list="cron-delivery-to-suggestions"
                                  aria-invalid=${props.fieldErrors.deliveryTo ? "true" : "false"}
                                  aria-describedby=${ifDefined(
                                    props.fieldErrors.deliveryTo
                                      ? errorIdForField("deliveryTo")
                                      : undefined,
                                  )}
                                  @input=${(e: Event) =>
                                    props.onFormChange({
                                      deliveryTo: (e.target as HTMLInputElement).value,
                                    })}
                                  placeholder="https://example.com/cron"
                                />
                              `
                            : html`
                                <select
                                  id="cron-delivery-channel"
                                  .value=${props.form.deliveryChannel || "last"}
                                  @change=${(e: Event) =>
                                    props.onFormChange({
                                      deliveryChannel: (e.target as HTMLSelectElement).value,
                                    })}
                                >
                                  ${channelOptions.map(
                                    (channel) =>
                                      html`<option value=${channel}>
                                        ${resolveChannelLabel(props, channel)}
                                      </option>`,
                                  )}
                                </select>
                              `
                        }
                        ${
                          selectedDeliveryMode === "announce"
                            ? html`
                                <div class="cron-help">Choose which connected channel receives the summary.</div>
                              `
                            : html`
                                <div class="cron-help">Send run summaries to a webhook endpoint.</div>
                              `
                        }
                      </label>
                      ${
                        selectedDeliveryMode === "announce"
                          ? html`
                              <label class="field cron-span-2">
                                ${renderFieldLabel("To")}
                                <input
                                  id="cron-delivery-to"
                                  .value=${props.form.deliveryTo}
                                  list="cron-delivery-to-suggestions"
                                  @input=${(e: Event) =>
                                    props.onFormChange({
                                      deliveryTo: (e.target as HTMLInputElement).value,
                                    })}
                                  placeholder="+1555... or chat id"
                                />
                                <div class="cron-help">Optional recipient override (chat id, phone, or user id).</div>
                              </label>
                            `
                          : nothing
                      }
                      ${
                        selectedDeliveryMode === "webhook"
                          ? renderFieldError(
                              props.fieldErrors.deliveryTo,
                              errorIdForField("deliveryTo"),
                            )
                          : nothing
                      }
                    `
                  : nothing
              }
            </div>
          </section>

          <details class="cron-advanced">
            <summary class="cron-advanced__summary">Advanced</summary>
            <div class="cron-help">
              Optional overrides for delivery guarantees, schedule jitter, and model controls.
            </div>
            <div class="form-grid cron-form-grid">
              <label class="field checkbox cron-checkbox">
                <input
                  type="checkbox"
                  .checked=${props.form.deleteAfterRun}
                  @change=${(e: Event) =>
                    props.onFormChange({
                      deleteAfterRun: (e.target as HTMLInputElement).checked,
                    })}
                />
                <span class="field-checkbox__label">Delete after run</span>
                <div class="cron-help">Best for one-shot reminders that should auto-clean up.</div>
              </label>
              <label class="field checkbox cron-checkbox">
                <input
                  type="checkbox"
                  .checked=${props.form.clearAgent}
                  @change=${(e: Event) =>
                    props.onFormChange({
                      clearAgent: (e.target as HTMLInputElement).checked,
                    })}
                />
                <span class="field-checkbox__label">Clear agent override</span>
                <div class="cron-help">Force this job to use the gateway default assistant.</div>
              </label>
              ${
                isCronSchedule
                  ? html`
                      <label class="field checkbox cron-checkbox cron-span-2">
                        <input
                          type="checkbox"
                          .checked=${props.form.scheduleExact}
                          @change=${(e: Event) =>
                            props.onFormChange({
                              scheduleExact: (e.target as HTMLInputElement).checked,
                            })}
                        />
                        <span class="field-checkbox__label">Exact timing (no stagger)</span>
                        <div class="cron-help">Run on exact cron boundaries with no spread.</div>
                      </label>
                      <div class="cron-stagger-group cron-span-2">
                        <label class="field">
                          ${renderFieldLabel("Stagger window")}
                          <input
                            id="cron-stagger-amount"
                            .value=${props.form.staggerAmount}
                            ?disabled=${props.form.scheduleExact}
                            aria-invalid=${props.fieldErrors.staggerAmount ? "true" : "false"}
                            aria-describedby=${ifDefined(
                              props.fieldErrors.staggerAmount
                                ? errorIdForField("staggerAmount")
                                : undefined,
                            )}
                            @input=${(e: Event) =>
                              props.onFormChange({
                                staggerAmount: (e.target as HTMLInputElement).value,
                              })}
                            placeholder="30"
                          />
                          ${renderFieldError(
                            props.fieldErrors.staggerAmount,
                            errorIdForField("staggerAmount"),
                          )}
                        </label>
                        <label class="field">
                          <span>Stagger unit</span>
                          <select
                            .value=${props.form.staggerUnit}
                            ?disabled=${props.form.scheduleExact}
                            @change=${(e: Event) =>
                              props.onFormChange({
                                staggerUnit: (e.target as HTMLSelectElement)
                                  .value as CronFormState["staggerUnit"],
                              })}
                          >
                            <option value="seconds">Seconds</option>
                            <option value="minutes">Minutes</option>
                          </select>
                        </label>
                      </div>
                    `
                  : nothing
              }
              ${
                isAgentTurn
                  ? html`
                      <label class="field">
                        ${renderFieldLabel("Model")}
                        <input
                          id="cron-payload-model"
                          .value=${props.form.payloadModel}
                          list="cron-model-suggestions"
                          @input=${(e: Event) =>
                            props.onFormChange({
                              payloadModel: (e.target as HTMLInputElement).value,
                            })}
                          placeholder="openai/gpt-5.2"
                        />
                        <div class="cron-help">
                          Start typing to pick a known model, or enter a custom one.
                        </div>
                      </label>
                      <label class="field">
                        ${renderFieldLabel("Thinking")}
                        <input
                          id="cron-payload-thinking"
                          .value=${props.form.payloadThinking}
                          list="cron-thinking-suggestions"
                          @input=${(e: Event) =>
                            props.onFormChange({
                              payloadThinking: (e.target as HTMLInputElement).value,
                            })}
                          placeholder="low"
                        />
                        <div class="cron-help">Use a suggested level or enter a provider-specific value.</div>
                      </label>
                    `
                  : nothing
              }
              ${
                selectedDeliveryMode !== "none"
                  ? html`
                      <label class="field checkbox cron-checkbox cron-span-2">
                        <input
                          type="checkbox"
                          .checked=${props.form.deliveryBestEffort}
                          @change=${(e: Event) =>
                            props.onFormChange({
                              deliveryBestEffort: (e.target as HTMLInputElement).checked,
                            })}
                        />
                        <span class="field-checkbox__label">Best effort delivery</span>
                        <div class="cron-help">Do not fail the job if delivery itself fails.</div>
                      </label>
                    `
                  : nothing
              }
            </div>
          </details>
        </div>
        ${
          blockedByValidation
            ? html`
                <div class="cron-form-status" role="status" aria-live="polite">
                  <div class="cron-form-status__title">Can't add job yet</div>
                  <div class="cron-help">Fill the required fields below to enable submit.</div>
                  <ul class="cron-form-status__list">
                    ${blockingFields.map(
                      (field) => html`
                        <li>
                          <button
                            type="button"
                            class="cron-form-status__link"
                            @click=${() => focusFormField(field.inputId)}
                          >
                            ${field.label}: ${field.message}
                          </button>
                        </li>
                      `,
                    )}
                  </ul>
                </div>
              `
            : nothing
        }
        <div class="row cron-form-actions">
          <button class="btn primary" ?disabled=${props.busy || !props.canSubmit} @click=${props.onAdd}>
            ${props.busy ? "Saving..." : isEditing ? "Save changes" : "Add job"}
          </button>
          ${
            submitDisabledReason
              ? html`<div class="cron-submit-reason" aria-live="polite">${submitDisabledReason}</div>`
              : nothing
          }
          ${
            isEditing
              ? html`
                  <button class="btn" ?disabled=${props.busy} @click=${props.onCancelEdit}>
                    Cancel
                  </button>
                `
              : nothing
          }
        </div>
        <div class="row" style="margin-top: 14px;">
          <button class="btn primary" ?disabled=${props.busy} @click=${props.onAdd}>
            ${props.busy ? "保存中…" : "ジョブ追加"}
          </button>
        </div>
      </div>
    </section>

    <section class="card" style="margin-top: 18px;">
      <div class="card-title">ジョブ一覧</div>
      <div class="card-sub">ゲートウェイに保存された全ジョブ。</div>
      ${
        props.jobs.length === 0
          ? html`
              <div class="muted" style="margin-top: 12px">ジョブはまだありません。</div>
            `
          : html`
            <div class="list" style="margin-top: 12px;">
              ${props.jobs.map((job) => renderJob(job, props))}
            </div>
          `
      }
    </section>

    <section class="card" style="margin-top: 18px;">
      <div class="card-title">実行履歴</div>
      <div class="card-sub">対象: ${selectedRunTitle} の最新実行。</div>
      ${
        props.runsJobId == null
          ? html`
              <div class="muted" style="margin-top: 12px">履歴を見るジョブを選択してください。</div>
            `
          : orderedRuns.length === 0
            ? html`
                <div class="muted" style="margin-top: 12px">実行履歴はありません。</div>
              `
            : html`
              <div class="list" style="margin-top: 12px;">
                ${orderedRuns.map((entry) => renderRun(entry, props.basePath))}
              </div>
            `
      }
    </section>
  `;
}

function renderScheduleFields(props: CronProps) {
  const form = props.form;
  if (form.scheduleKind === "at") {
    return html`
      <label class="field" style="margin-top: 12px;">
        <span>実行日時</span>
        <input
          id="cron-schedule-at"
          type="datetime-local"
          .value=${form.scheduleAt}
          aria-invalid=${props.fieldErrors.scheduleAt ? "true" : "false"}
          aria-describedby=${ifDefined(
            props.fieldErrors.scheduleAt ? errorIdForField("scheduleAt") : undefined,
          )}
          @input=${(e: Event) =>
            props.onFormChange({
              scheduleAt: (e.target as HTMLInputElement).value,
            })}
        />
        ${renderFieldError(props.fieldErrors.scheduleAt, errorIdForField("scheduleAt"))}
      </label>
    `;
  }
  if (form.scheduleKind === "every") {
    return html`
      <div class="form-grid cron-form-grid" style="margin-top: 12px;">
        <label class="field">
          <span>間隔</span>
          <input
            id="cron-every-amount"
            .value=${form.everyAmount}
            aria-invalid=${props.fieldErrors.everyAmount ? "true" : "false"}
            aria-describedby=${ifDefined(
              props.fieldErrors.everyAmount ? errorIdForField("everyAmount") : undefined,
            )}
            @input=${(e: Event) =>
              props.onFormChange({
                everyAmount: (e.target as HTMLInputElement).value,
              })}
            placeholder="30"
          />
          ${renderFieldError(props.fieldErrors.everyAmount, errorIdForField("everyAmount"))}
        </label>
        <label class="field">
          <span>単位</span>
          <select
            .value=${form.everyUnit}
            @change=${(e: Event) =>
              props.onFormChange({
                everyUnit: (e.target as HTMLSelectElement).value as CronFormState["everyUnit"],
              })}
          >
            <option value="minutes">分</option>
            <option value="hours">時間</option>
            <option value="days">日</option>
          </select>
        </label>
      </div>
    `;
  }
  return html`
    <div class="form-grid cron-form-grid" style="margin-top: 12px;">
      <label class="field">
        <span>式</span>
        <input
          id="cron-cron-expr"
          .value=${form.cronExpr}
          aria-invalid=${props.fieldErrors.cronExpr ? "true" : "false"}
          aria-describedby=${ifDefined(
            props.fieldErrors.cronExpr ? errorIdForField("cronExpr") : undefined,
          )}
          @input=${(e: Event) =>
            props.onFormChange({ cronExpr: (e.target as HTMLInputElement).value })}
          placeholder="0 7 * * *"
        />
        ${renderFieldError(props.fieldErrors.cronExpr, errorIdForField("cronExpr"))}
      </label>
      <label class="field">
        <span>タイムゾーン（任意）</span>
        <input
          .value=${form.cronTz}
          list="cron-tz-suggestions"
          @input=${(e: Event) =>
            props.onFormChange({ cronTz: (e.target as HTMLInputElement).value })}
          placeholder="America/Los_Angeles"
        />
        <div class="cron-help">Pick a common timezone or enter any valid IANA timezone.</div>
      </label>
      <div class="cron-help cron-span-2">Need jitter? Use Advanced → Stagger window / Stagger unit.</div>
    </div>
  `;
}

function renderFieldError(message?: string, id?: string) {
  if (!message) {
    return nothing;
  }
  return html`<div id=${ifDefined(id)} class="cron-help cron-error">${message}</div>`;
}

function renderJob(job: CronJob, props: CronProps) {
  const isSelected = props.runsJobId === job.id;
  const itemClass = `list-item list-item-clickable cron-job${isSelected ? " list-item-selected" : ""}`;
  const selectAnd = (action: () => void) => {
    props.onLoadRuns(job.id);
    action();
  };
  return html`
    <div class=${itemClass} @click=${() => props.onLoadRuns(job.id)}>
      <div class="list-main">
        <div class="list-title">${job.name}</div>
        <div class="list-sub">${formatCronSchedule(job)}</div>
        ${renderJobPayload(job)}
        ${job.agentId ? html`<div class="muted cron-job-agent">エージェント: ${job.agentId}</div>` : nothing}
      </div>
      <div class="list-meta">
        ${renderJobState(job)}
      </div>
      <div class="cron-job-footer">
        <div class="chip-row cron-job-chips">
          <span class=${`chip ${job.enabled ? "chip-ok" : "chip-danger"}`}>
            ${job.enabled ? "有効" : "無効"}
          </span>
          <span class="chip">${formatSessionTarget(job.sessionTarget)}</span>
          <span class="chip">${formatWakeMode(job.wakeMode)}</span>
        </div>
        <div class="row cron-job-actions">
          <button
            class="btn"
            ?disabled=${props.busy}
            @click=${(event: Event) => {
              event.stopPropagation();
              selectAnd(() => props.onEdit(job));
            }}
          >
            Edit
          </button>
          <button
            class="btn"
            ?disabled=${props.busy}
            @click=${(event: Event) => {
              event.stopPropagation();
              selectAnd(() => props.onClone(job));
            }}
          >
            Clone
          </button>
          <button
            class="btn"
            ?disabled=${props.busy}
            @click=${(event: Event) => {
              event.stopPropagation();
              selectAnd(() => props.onToggle(job, !job.enabled));
            }}
          >
            ${job.enabled ? "無効化" : "有効化"}
          </button>
          <button
            class="btn"
            ?disabled=${props.busy}
            @click=${(event: Event) => {
              event.stopPropagation();
              selectAnd(() => props.onRun(job));
            }}
          >
            実行
          </button>
          <button
            class="btn"
            ?disabled=${props.busy}
            @click=${(event: Event) => {
              event.stopPropagation();
              selectAnd(() => props.onLoadRuns(job.id));
            }}
          >
            履歴
          </button>
          <button
            class="btn danger"
            ?disabled=${props.busy}
            @click=${(event: Event) => {
              event.stopPropagation();
              selectAnd(() => props.onRemove(job));
            }}
          >
            削除
          </button>
        </div>
      </div>
    </div>
  `;
}

function renderJobPayload(job: CronJob) {
  if (job.payload.kind === "systemEvent") {
    return html`<div class="cron-job-detail">
      <span class="cron-job-detail-label">システム</span>
      <span class="muted cron-job-detail-value">${job.payload.text}</span>
    </div>`;
  }

  const delivery = job.delivery;
  const deliveryTarget =
    delivery?.mode === "webhook"
      ? delivery.to
        ? ` (${delivery.to})`
        : ""
      : delivery?.channel || delivery?.to
        ? ` (${formatDeliveryChannel(delivery.channel)}${delivery.to ? ` -> ${delivery.to}` : ""})`
        : "";

  return html`
    <div class="cron-job-detail">
      <span class="cron-job-detail-label">プロンプト</span>
      <span class="muted cron-job-detail-value">${job.payload.message}</span>
    </div>
    ${
      delivery
        ? html`<div class="cron-job-detail">
            <span class="cron-job-detail-label">配信</span>
            <span class="muted cron-job-detail-value">${
              delivery.mode === "announce"
                ? "通知"
                : delivery.mode === "webhook"
                  ? "Webhook"
                  : delivery.mode
            }${deliveryTarget}</span>
          </div>`
        : nothing
    }
  `;
}

function formatStateRelative(ms?: number) {
  if (typeof ms !== "number" || !Number.isFinite(ms)) {
    return NA_LABEL;
  }
  return formatRelativeTimestamp(ms);
}

function formatRunNextLabel(nextRunAtMs: number, nowMs = Date.now()) {
  const rel = formatRelativeTimestamp(nextRunAtMs);
  return nextRunAtMs > nowMs ? `Next ${rel}` : `Due ${rel}`;
}

function renderJobState(job: CronJob) {
  const status = job.state?.lastStatus ?? "n/a";
  const statusLabel = formatStatusLabel(status);
  const statusClass =
    status === "ok"
      ? "cron-job-status-ok"
      : status === "error"
        ? "cron-job-status-error"
        : status === "skipped"
          ? "cron-job-status-skipped"
          : "cron-job-status-na";
  const nextRunAtMs = job.state?.nextRunAtMs;
  const lastRunAtMs = job.state?.lastRunAtMs;

  return html`
    <div class="cron-job-state">
      <div class="cron-job-state-row">
        <span class="cron-job-state-key">状態</span>
        <span class=${`cron-job-status-pill ${statusClass}`}>${statusLabel}</span>
      </div>
      <div class="cron-job-state-row">
        <span class="cron-job-state-key">次回</span>
        <span class="cron-job-state-value" title=${formatMs(nextRunAtMs)}>
          ${formatStateRelative(nextRunAtMs)}
        </span>
      </div>
      <div class="cron-job-state-row">
        <span class="cron-job-state-key">前回</span>
        <span class="cron-job-state-value" title=${formatMs(lastRunAtMs)}>
          ${formatStateRelative(lastRunAtMs)}
        </span>
      </div>
    </div>
  `;
}

function renderRun(entry: CronRunLogEntry, basePath: string) {
  const chatUrl =
    typeof entry.sessionKey === "string" && entry.sessionKey.trim().length > 0
      ? `${pathForTab("chat", basePath)}?session=${encodeURIComponent(entry.sessionKey)}`
      : null;
  const status = entry.status ?? "unknown";
  const delivery = entry.deliveryStatus ?? "not-requested";
  const usage = entry.usage;
  const usageSummary =
    usage && typeof usage.total_tokens === "number"
      ? `${usage.total_tokens} tokens`
      : usage && typeof usage.input_tokens === "number" && typeof usage.output_tokens === "number"
        ? `${usage.input_tokens} in / ${usage.output_tokens} out`
        : null;
  return html`
    <div class="list-item">
      <div class="list-main">
        <div class="list-title">${formatStatusLabel(entry.status)}</div>
        <div class="list-sub">${entry.summary ?? ""}</div>
      </div>
      <div class="list-meta cron-run-entry__meta">
        <div>${formatMs(entry.ts)}</div>
        ${typeof entry.runAtMs === "number" ? html`<div class="muted">Run at ${formatMs(entry.runAtMs)}</div>` : nothing}
        <div class="muted">${entry.durationMs ?? 0}ms</div>
        ${
          typeof entry.nextRunAtMs === "number"
            ? html`<div class="muted">${formatRunNextLabel(entry.nextRunAtMs)}</div>`
            : nothing
        }
        ${
          chatUrl
            ? html`<div><a class="session-link" href=${chatUrl}>実行チャットを開く</a></div>`
            : nothing
        }
        ${entry.error ? html`<div class="muted">${entry.error}</div>` : nothing}
        ${entry.deliveryError ? html`<div class="muted">${entry.deliveryError}</div>` : nothing}
      </div>
    </div>
  `;
}
