import { html, nothing } from "lit";
import { formatRelativeTimestamp } from "../format.ts";
import { icons } from "../icons.ts";
import { pathForTab } from "../navigation.ts";
import { formatCronSchedule } from "../presenter.ts";
import type {
  CronJob,
  CronRunLogEntry,
  RobyOpsStatus,
  SkillStatusEntry,
  SkillStatusReport,
} from "../types.ts";
import { computeSkillMissing } from "./skills-shared.ts";

const SELF_GROWTH_JOB_NAME = "Roby Self-Growth Hourly";

export type RobyProps = {
  basePath: string;
  connected: boolean;
  cronLoading: boolean;
  cronError: string | null;
  cronBusy: boolean;
  cronJobs: CronJob[];
  cronRunsJobId: string | null;
  cronRuns: CronRunLogEntry[];
  robyOpsLoading: boolean;
  robyOpsStatus: RobyOpsStatus | null;
  robyOpsError: string | null;
  robyOpsNotifyBusy: boolean;
  robyOpsNotifyMessage: string | null;
  skillsLoading: boolean;
  skillsError: string | null;
  skillsReport: SkillStatusReport | null;
  onRefresh: () => void;
  onNotifyOpsSummary: () => void;
  onRunJob: (job: CronJob) => void;
  onLoadRuns: (jobId: string) => void;
};

function findSelfGrowthJob(jobs: CronJob[]) {
  return jobs.find((job) => job.name === SELF_GROWTH_JOB_NAME);
}

function formatRunStatus(status?: string | null): string {
  switch (status) {
    case "ok":
      return "成功";
    case "error":
      return "失敗";
    case "skipped":
      return "スキップ";
    default:
      return status ?? "—";
  }
}

function formatOpsTone(ok?: boolean | null) {
  return ok === true ? "ok" : ok === false ? "danger" : "muted";
}

function formatOpsLabel(ok?: boolean | null, present?: boolean) {
  if (present === false) {
    return "未実行";
  }
  if (ok === true) {
    return "正常";
  }
  if (ok === false) {
    return "要対応";
  }
  return "—";
}

function joinList(items: string[] | undefined, fallback = "なし") {
  if (!items || items.length === 0) {
    return fallback;
  }
  return items.join(" / ");
}

async function copyTextToClipboard(text: string): Promise<boolean> {
  if (!text.trim()) {
    return false;
  }
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    try {
      const textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.setAttribute("readonly", "true");
      textarea.style.position = "fixed";
      textarea.style.top = "-1000px";
      document.body.appendChild(textarea);
      textarea.select();
      const copied = document.execCommand("copy");
      document.body.removeChild(textarea);
      return copied;
    } catch {
      return false;
    }
  }
}

export function renderRoby(props: RobyProps) {
  const job = findSelfGrowthJob(props.cronJobs);
  const isLoaded = job && props.cronRunsJobId === job.id;
  const latestRun = isLoaded ? props.cronRuns.toSorted((a, b) => b.ts - a.ts)[0] : null;
  const statusBadge =
    latestRun?.status === "ok"
      ? "ok"
      : latestRun?.status === "error"
        ? "danger"
        : latestRun?.status === "skipped"
          ? "warn"
          : "";
  const cronHref = pathForTab("cron", props.basePath);
  const logsHref = pathForTab("logs", props.basePath);
  const skillsHref = pathForTab("skills", props.basePath);

  const skills = props.skillsReport?.skills ?? [];
  const robyMailSkill = findSkill(skills, ["roby-mail"]);
  const gogSkill = findSkill(skills, ["gog"]);
  const notionSkill = findSkill(skills, ["notion"]);
  const skillsReady = Boolean(props.skillsReport);
  const gmailStatus = resolveCombinedSkillStatus(
    [robyMailSkill, gogSkill],
    ["roby-mail", "gog"],
    skillsReady,
  );
  const notionStatus = resolveCombinedSkillStatus([notionSkill], ["notion"], skillsReady);
  const neuronicStatus = resolveCombinedSkillStatus([robyMailSkill], ["roby-mail"], skillsReady);
  const ops = props.robyOpsStatus;
  const evalStatus = ops?.evaluationHarness;
  const drillStatus = ops?.runbookDrill;
  const liveFreshness = ops?.liveFreshness;
  const weeklyStatus = ops?.weeklyReport;
  const feedbackLoop = ops?.feedbackLoop;
  const localFirst = ops?.localFirst;
  const weeklyLoaded = Boolean(weeklyStatus);
  const currentIssues = [
    evalStatus?.present === true && evalStatus?.allOk === false,
    drillStatus?.present === true && drillStatus?.allOk === false,
    liveFreshness?.present === true && (liveFreshness?.staleCount ?? 0) > 0,
  ].some(Boolean);
  const weeklyNeedsAttention = weeklyStatus?.present === true && currentIssues;

  return html`
    <section class="grid grid-cols-2">
      <div class="card">
      <div class="card-title">自己成長オートメーション</div>
      <div class="card-sub">パッチ適用 → テスト → 再起動を毎時実行します。</div>
        ${
          job
            ? html`
              <div class="stat-grid" style="margin-top: 16px;">
                <div class="stat">
                  <div class="stat-label">有効</div>
                  <div class="stat-value">${job.enabled ? "はい" : "いいえ"}</div>
                </div>
                <div class="stat">
                  <div class="stat-label">スケジュール</div>
                  <div class="stat-value">${formatCronSchedule(job)}</div>
                </div>
                <div class="stat">
                  <div class="stat-label">最終実行</div>
                  <div class="stat-value">
                    ${latestRun ? formatRelativeTimestamp(latestRun.ts) : "未取得"}
                  </div>
                </div>
                <div class="stat">
                  <div class="stat-label">状態</div>
                  <div class="stat-value">
                    ${
                      latestRun
                        ? html`<span class="pill ${statusBadge}">${formatRunStatus(latestRun.status)}</span>`
                        : html`
                            <span class="pill muted">—</span>
                          `
                    }
                  </div>
                </div>
              </div>
              <div class="row" style="margin-top: 12px; gap: 8px;">
                <button class="btn" ?disabled=${props.cronBusy} @click=${() => props.onRunJob(job)}>
                  今すぐ実行
                </button>
                <button class="btn btn--ghost" ?disabled=${props.cronLoading} @click=${props.onRefresh}>
                  ${props.cronLoading ? "更新中…" : "更新"}
                </button>
                <button
                  class="btn btn--ghost"
                  ?disabled=${props.robyOpsNotifyBusy}
                  @click=${props.onNotifyOpsSummary}
                >
                  ${props.robyOpsNotifyBusy ? "Slack再送中…" : "品質サマリーをSlackへ再送"}
                </button>
                <button
                  class="btn btn--ghost"
                  ?disabled=${props.cronBusy || !job}
                  @click=${() => props.onLoadRuns(job.id)}
                >
                  実行履歴を取得
                </button>
              </div>
              ${props.cronError ? html`<div class="callout danger" style="margin-top: 12px;">${props.cronError}</div>` : nothing}
              ${
                props.robyOpsNotifyMessage
                  ? html`
                      <div class="callout" style="margin-top: 12px;">${props.robyOpsNotifyMessage}</div>
                    `
                  : nothing
              }
              ${
                latestRun?.summary
                  ? html`
                      <div class="callout" style="margin-top: 12px;">
                        <div class="callout-title">最新サマリー</div>
                        <pre class="callout-pre">${latestRun.summary}</pre>
                      </div>
                    `
                  : nothing
              }
            `
            : html`
              <div class="callout warn" style="margin-top: 16px;">
                自己成長ジョブが見つかりません。<a href=${cronHref}>スケジューラ</a> で作成してください。
              </div>
            `
        }
      </div>

      <div class="card">
      <div class="card-title">ショートカット</div>
      <div class="card-sub">よく使う管理画面への導線。</div>
        <div class="stat-grid" style="margin-top: 16px;">
          <div class="stat">
          <div class="stat-label">スケジューラ</div>
          <div class="stat-value">
            <a class="link" href=${cronHref}>OPEN</a>
          </div>
        </div>
        <div class="stat">
          <div class="stat-label">ゲートウェイログ</div>
          <div class="stat-value">
            <a class="link" href=${logsHref}>OPEN</a>
          </div>
        </div>
        <div class="stat">
          <div class="stat-label">接続状態</div>
          <div class="stat-value">${props.connected ? "接続中" : "オフライン"}</div>
        </div>
        <div class="stat">
          <div class="stat-label">最終更新</div>
          <div class="stat-value">
            ${latestRun ? formatRelativeTimestamp(latestRun.ts) : "—"}
          </div>
        </div>
        </div>
      </div>
    </section>
    <section class="grid" style="margin-top: 18px; grid-template-columns: repeat(5, minmax(0, 1fr));">
      ${renderOpsCard({
        title: "現在の稼働状況",
        status:
          liveFreshness?.present === false ? "未取得" : liveFreshness?.allFresh ? "正常" : "要対応",
        tone: liveFreshness?.present === false ? "muted" : liveFreshness?.allFresh ? "ok" : "warn",
        subtitle: liveFreshness?.present
          ? `stale ${liveFreshness?.staleCount ?? 0} / ${liveFreshness?.components?.length ?? 0} 系統`
          : "現在状態未取得",
        meta: liveFreshness?.ts ? formatRelativeTimestamp(liveFreshness.ts) : "—",
        cardStyle:
          "background: linear-gradient(180deg, rgba(56, 99, 255, 0.16) 0%, rgba(17, 20, 33, 0.98) 100%); border-color: rgba(98, 131, 255, 0.38); box-shadow: inset 0 0 0 1px rgba(98, 131, 255, 0.14);",
        details: liveFreshness?.present
          ? html`
          <div class="muted">現在の鮮度</div>
          <div class="muted">- stale component: ${joinList(liveFreshness?.staleComponents, "なし")}</div>
          ${
            liveFreshness?.components?.length
              ? html`
                  <div class="muted" style="margin-top: 8px;">系統別の状態</div>
                  ${liveFreshness.components.map(
                    (row) => html`
                      <div class="row" style="gap: 8px; align-items: flex-start;">
                        <div style="display: grid; gap: 4px; flex: 1; min-width: 0;">
                          <span class="pill ${row.stale ? "warn" : row.missing ? "danger" : "ok"}" style="width: fit-content;">
                            ${row.name}
                          </span>
                          <span class="muted">
                            ${row.missing ? "未実行" : `${row.ageMinutes ?? 0}分前 / 閾値 ${row.thresholdMinutes}分`}
                          </span>
                        </div>
                        ${
                          row.stale || row.missing
                            ? html`
                                <button
                                  class="btn btn--ghost"
                                  type="button"
                                  title="対処コマンドをコピー"
                                  aria-label="対処コマンドをコピー"
                                  style="padding: 8px; min-width: 40px;"
                                  @click=${async (e: Event) => {
                                    const button = e.currentTarget as HTMLButtonElement | null;
                                    if (!button) {
                                      return;
                                    }
                                    const ok = await copyTextToClipboard(row.remedyCommand);
                                    button.classList.toggle("is-success", ok);
                                    button.classList.toggle("is-danger", !ok);
                                    button.title = ok ? "コピー済み" : "コピー失敗";
                                    button.setAttribute(
                                      "aria-label",
                                      ok ? "コピー済み" : "コピー失敗",
                                    );
                                    window.setTimeout(
                                      () => {
                                        if (button.isConnected) {
                                          button.classList.remove("is-success", "is-danger");
                                          button.title = "対処コマンドをコピー";
                                          button.setAttribute("aria-label", "対処コマンドをコピー");
                                        }
                                      },
                                      ok ? 1200 : 1800,
                                    );
                                  }}
                                >
                                  ${icons.copy}
                                </button>
                              `
                            : nothing
                        }
                      </div>
                    `,
                  )}
                `
              : nothing
          }
        `
          : nothing,
      })}
      ${renderOpsCard({
        title: "Evaluation Harness",
        status: formatOpsLabel(evalStatus?.allOk, evalStatus?.present),
        tone: formatOpsTone(evalStatus?.allOk),
        subtitle: evalStatus?.present
          ? `失敗 ${evalStatus?.failed ?? 0} / ${evalStatus?.total ?? 0} · p95 ${evalStatus?.p95Ms ?? 0}ms`
          : "最新結果なし",
        meta: evalStatus?.ts ? formatRelativeTimestamp(evalStatus.ts) : "—",
        details: evalStatus?.present
          ? html`
              <div class="muted">retry 合計: ${evalStatus?.retriesTotal ?? 0}</div>
              <div class="muted">route別失敗: ${
                evalStatus?.routes?.length
                  ? evalStatus.routes
                      .map((row) => `${row.route} ${row.failed}/${row.total}`)
                      .join(" / ")
                  : "なし"
              }</div>
              <div class="muted">直近fail case: ${
                evalStatus?.failedCases?.length
                  ? evalStatus.failedCases
                      .map((row) => row.description || row.id || "unknown")
                      .join(" / ")
                  : "なし"
              }</div>
            `
          : nothing,
      })}
      ${renderOpsCard({
        title: "Runbook Drill",
        status: formatOpsLabel(drillStatus?.allOk, drillStatus?.present),
        tone: formatOpsTone(drillStatus?.allOk),
        subtitle: drillStatus?.present
          ? `失敗 ${drillStatus?.failed ?? 0} / ${drillStatus?.total ?? 0} · skip ${drillStatus?.skipped ?? 0}`
          : "最新結果なし",
        meta: drillStatus?.ts ? formatRelativeTimestamp(drillStatus.ts) : "—",
        details: drillStatus?.present
          ? html`
              <div class="muted">failed check: ${
                drillStatus?.failedChecks?.length
                  ? drillStatus.failedChecks
                      .map((row) => `${row.id}${row.kind ? ` (${row.kind})` : ""}`)
                      .join(" / ")
                  : "なし"
              }</div>
              ${
                drillStatus?.failedChecks?.length
                  ? html`
                      <div class="muted" style="margin-top: 6px;">
                        ${joinList(
                          drillStatus.failedChecks
                            .map((row) => row.detail)
                            .filter((detail) => detail.trim().length > 0),
                        )}
                      </div>
                    `
                  : nothing
              }
            `
          : nothing,
      })}
      ${renderOpsCard({
        title: "週次集計スナップショット",
        status: !weeklyLoaded
          ? props.robyOpsLoading
            ? "読込中"
            : "未取得"
          : weeklyStatus?.present === false
            ? "未生成"
            : weeklyNeedsAttention
              ? "未対応あり"
              : "正常",
        tone: !weeklyLoaded
          ? "muted"
          : weeklyStatus?.present === false
            ? "muted"
            : weeklyNeedsAttention
              ? "warn"
              : "ok",
        subtitle: !weeklyLoaded
          ? props.robyOpsLoading
            ? "週次スナップショットを取得中"
            : "週次スナップショット未取得"
          : weeklyStatus?.present
            ? `過去7日: eval ${weeklyStatus?.evalRuns ?? 0}件 / drill ${weeklyStatus?.drillRuns ?? 0}件 / stale ${weeklyStatus?.staleCount ?? 0}（履歴）`
            : "週次レポートなし",
        meta: weeklyLoaded && weeklyStatus?.ts ? formatRelativeTimestamp(weeklyStatus.ts) : "—",
        details: weeklyLoaded
          ? html`
              <div class="muted">7日集計の内訳（履歴）</div>
              <div class="muted">- eval fail run: ${weeklyStatus?.evalFailedRuns ?? 0}</div>
              <div class="muted">- drill fail run: ${weeklyStatus?.drillFailedRuns ?? 0}</div>
              <div class="muted">- audit error: ${weeklyStatus?.auditErrors ?? 0}</div>
              <div class="muted">- stale component: ${joinList(weeklyStatus?.staleComponents, "なし")}</div>
              <div class="muted">
                - ops error: ${
                  weeklyStatus?.opsErrors?.length
                    ? weeklyStatus.opsErrors
                        .map((row) => `${row.name} ${row.errors}/${row.runs}`)
                        .join(" / ")
                    : "なし"
                }
              </div>
            `
          : nothing,
      })}
      ${renderOpsCard({
        title: "Local First",
        status: localFirst?.ollamaApiOk ? "準備完了" : localFirst?.ollamaCli ? "API待ち" : "未導入",
        tone: localFirst?.ollamaApiOk ? "ok" : localFirst?.ollamaCli ? "warn" : "muted",
        subtitle: localFirst
          ? `${localFirst.configuredModel} · ${localFirst.modelAvailable ? "利用可" : "未pull"}`
          : "状態未取得",
        meta: localFirst ? (localFirst.error ? localFirst.error : localFirst.baseUrl) : "—",
        details: localFirst
          ? html`
              <div class="muted">base URL: ${localFirst.baseUrl}</div>
              <div class="muted">configured model: ${localFirst.configuredModel}</div>
              <div class="muted">available: ${joinList(localFirst.availableModels, "なし")}</div>
            `
          : nothing,
      })}
    </section>
    <section style="margin-top: 18px;">
      ${renderOpsCard({
        title: "評価ループ",
        status:
          feedbackLoop?.present === false
            ? "未実行"
            : (feedbackLoop?.actionableCount ?? 0) > 0
              ? "要確認"
              : (feedbackLoop?.reviewedCount ?? 0) > 0
                ? "正常"
                : "未評価",
        tone:
          feedbackLoop?.present === false
            ? "muted"
            : (feedbackLoop?.actionableCount ?? 0) > 0
              ? "warn"
              : (feedbackLoop?.reviewedCount ?? 0) > 0
                ? "ok"
                : "muted",
        subtitle: feedbackLoop?.present
          ? `良い ${feedbackLoop?.counts?.good ?? 0} / 要修正 ${feedbackLoop?.counts?.bad ?? 0} / 見落とし ${feedbackLoop?.counts?.missed ?? 0} / 保留 ${feedbackLoop?.counts?.pending ?? 0}`
          : "Neuronic評価未取得",
        meta: feedbackLoop?.ts ? formatRelativeTimestamp(feedbackLoop.ts) : "—",
        details: feedbackLoop?.present
          ? html`
              <div class="muted">reviewed: ${feedbackLoop?.reviewedCount ?? 0} / total: ${feedbackLoop?.totalTasks ?? 0}</div>
              <div class="muted">actionable: ${feedbackLoop?.actionableCount ?? 0}</div>
              ${
                (feedbackLoop?.recentActionable?.length ?? 0) > 0
                  ? html`
                      <div class="muted" style="margin-top: 8px;">要確認の最新タスク</div>
                      ${feedbackLoop?.recentActionable
                        ?.slice(0, 5)
                        .map(
                          (row) =>
                            html`<div class="muted">- [${row.feedbackState}] ${row.title || row.id}</div>`,
                        )}
                    `
                  : html`
                      <div class="muted" style="margin-top: 8px;">直近レビュー</div>
                      ${
                        feedbackLoop?.recentReviewed
                          ?.slice(0, 5)
                          .map(
                            (row) =>
                              html`<div class="muted">- [${row.feedbackState}] ${row.title || row.id}</div>`,
                          ) ?? nothing
                      }
                    `
              }
            `
          : nothing,
      })}
    </section>
    ${
      props.robyOpsError
        ? html`<div class="callout danger" style="margin-top: 12px;">${props.robyOpsError}</div>`
        : nothing
    }
    ${
      props.robyOpsLoading
        ? html`
            <div class="muted" style="margin-top: 8px">運用品質を更新中…</div>
          `
        : nothing
    }
    <section class="grid grid-cols-3" style="margin-top: 18px;">
      ${renderIntegrationCard({
        title: "Gmail",
        subtitle: "roby-mail + gog",
        status: gmailStatus,
        skillsHref,
        loading: props.skillsLoading,
        error: props.skillsError,
      })}
      ${renderIntegrationCard({
        title: "Notion",
        subtitle: "Notion API",
        status: notionStatus,
        skillsHref,
        loading: props.skillsLoading,
        error: props.skillsError,
      })}
      ${renderIntegrationCard({
        title: "Neuronic",
        subtitle: "タスク同期（roby-mail経由）",
        status: neuronicStatus,
        skillsHref,
        loading: props.skillsLoading,
        error: props.skillsError,
        requiredHint: "NEURONIC_TOKEN とローカルAPIが必要",
      })}
    </section>
  `;
}

function renderOpsCard(params: {
  title: string;
  status: string;
  tone: "ok" | "warn" | "danger" | "muted" | "";
  subtitle: string;
  meta: string;
  details?: unknown;
  cardStyle?: string;
}) {
  return html`
    <div class="card" style=${params.cardStyle ?? nothing}>
      <div class="card-title">${params.title}</div>
      <div class="row" style="margin-top: 12px;">
        <span class="pill ${params.tone}">${params.status}</span>
        <span class="muted" style="margin-left:auto;">${params.meta}</span>
      </div>
      <div class="muted" style="margin-top: 8px;">${params.subtitle}</div>
      ${
        params.details && params.details !== nothing
          ? html`
              <details style="margin-top: 12px;">
                <summary class="link" style="cursor:pointer; font-weight: 600;">詳細を見る</summary>
                <div style="margin-top: 10px; display:grid; gap: 6px;">
                  ${params.details}
                </div>
              </details>
            `
          : nothing
      }
    </div>
  `;
}

function findSkill(skills: SkillStatusEntry[], keys: string[]) {
  const lowered = keys.map((k) => k.toLowerCase());
  return skills.find((entry) => {
    const name = entry.name?.toLowerCase() ?? "";
    const skillKey = entry.skillKey?.toLowerCase() ?? "";
    return lowered.some((key) => name === key || skillKey === key);
  });
}

function resolveSkillStatus(skill: SkillStatusEntry | undefined, reportLoaded = true) {
  if (!reportLoaded) {
    return { label: "未取得", tone: "warn" as const };
  }
  if (!skill) {
    return { label: "未インストール", tone: "danger" as const };
  }
  const missing = computeSkillMissing(skill);
  if (missing.length > 0) {
    return { label: `不足: ${missing.join(", ")}`, tone: "warn" as const };
  }
  if (skill.disabled) {
    return { label: "無効", tone: "warn" as const };
  }
  if (!skill.eligible) {
    return { label: "ブロック中", tone: "warn" as const };
  }
  return { label: "準備完了", tone: "ok" as const };
}

function resolveCombinedSkillStatus(
  skills: Array<SkillStatusEntry | undefined>,
  requiredNames: string[],
  reportLoaded = true,
) {
  if (!reportLoaded) {
    return { label: "未取得", tone: "warn" as const };
  }
  const present = skills.filter((skill): skill is SkillStatusEntry => Boolean(skill));
  if (present.length === 0) {
    return { label: "未インストール", tone: "danger" as const };
  }
  if (present.length < requiredNames.length) {
    const presentNames = new Set(present.map((skill) => skill.skillKey));
    const missingNames = requiredNames.filter((name) => !presentNames.has(name));
    return { label: `不足: ${missingNames.join(", ")}`, tone: "warn" as const };
  }
  const labels = new Set<string>();
  let hasDanger = false;
  for (const skill of present) {
    const status = resolveSkillStatus(skill, reportLoaded);
    if (status.tone === "danger") {
      hasDanger = true;
    }
    if (status.label !== "準備完了") {
      labels.add(status.label);
    }
  }
  if (labels.size > 0) {
    return {
      label: Array.from(labels).join(" / "),
      tone: hasDanger ? ("danger" as const) : ("warn" as const),
    };
  }
  return { label: "準備完了", tone: "ok" as const };
}

function renderIntegrationCard(params: {
  title: string;
  subtitle: string;
  status: { label: string; tone: "ok" | "warn" | "danger" };
  skillsHref: string;
  loading: boolean;
  error: string | null;
  requiredHint?: string;
}) {
  const status = params.status;
  return html`
    <div class="card">
      <div class="card-title">${params.title}</div>
      <div class="card-sub">${params.subtitle}</div>
      <div class="row" style="margin-top: 12px;">
        <span class="pill ${status.tone}">${status.label}</span>
        <a class="link" style="margin-left: auto;" href=${params.skillsHref}>スキル</a>
      </div>
      ${
        params.loading
          ? html`
              <div class="muted" style="margin-top: 8px">状態確認中…</div>
            `
          : nothing
      }
      ${
        params.error
          ? html`<div class="muted" style="margin-top: 8px;">${params.error}</div>`
          : nothing
      }
      ${
        params.requiredHint
          ? html`<div class="muted" style="margin-top: 8px;">${params.requiredHint}</div>`
          : nothing
      }
    </div>
  `;
}
