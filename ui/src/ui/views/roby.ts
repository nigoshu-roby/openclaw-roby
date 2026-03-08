import { html, nothing } from "lit";
import { formatRelativeTimestamp } from "../format.ts";
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
  skillsLoading: boolean;
  skillsError: string | null;
  skillsReport: SkillStatusReport | null;
  onRefresh: () => void;
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
  const gmailSkill = findSkill(skills, ["roby-mail", "gog"]);
  const notionSkill = findSkill(skills, ["notion"]);
  const neuronicSkill = findSkill(skills, ["roby-mail"]);
  const ops = props.robyOpsStatus;
  const evalStatus = ops?.evaluationHarness;
  const drillStatus = ops?.runbookDrill;
  const weeklyStatus = ops?.weeklyReport;
  const localFirst = ops?.localFirst;

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
                  ?disabled=${props.cronBusy || !job}
                  @click=${() => props.onLoadRuns(job.id)}
                >
                  実行履歴を取得
                </button>
              </div>
              ${props.cronError ? html`<div class="callout danger" style="margin-top: 12px;">${props.cronError}</div>` : nothing}
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
            <a class="link" href=${cronHref}>スケジューラを開く</a>
          </div>
        </div>
        <div class="stat">
          <div class="stat-label">ゲートウェイログ</div>
          <div class="stat-value">
            <a class="link" href=${logsHref}>ログを開く</a>
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
    <section class="grid grid-cols-4" style="margin-top: 18px;">
      ${renderOpsCard({
        title: "Evaluation Harness",
        status: formatOpsLabel(evalStatus?.allOk, evalStatus?.present),
        tone: formatOpsTone(evalStatus?.allOk),
        subtitle: evalStatus?.present
          ? `失敗 ${evalStatus?.failed ?? 0} / ${evalStatus?.total ?? 0} · p95 ${evalStatus?.p95Ms ?? 0}ms`
          : "最新結果なし",
        meta: evalStatus?.ts ? formatRelativeTimestamp(evalStatus.ts) : "—",
      })}
      ${renderOpsCard({
        title: "Runbook Drill",
        status: formatOpsLabel(drillStatus?.allOk, drillStatus?.present),
        tone: formatOpsTone(drillStatus?.allOk),
        subtitle: drillStatus?.present
          ? `失敗 ${drillStatus?.failed ?? 0} / ${drillStatus?.total ?? 0} · skip ${drillStatus?.skipped ?? 0}`
          : "最新結果なし",
        meta: drillStatus?.ts ? formatRelativeTimestamp(drillStatus.ts) : "—",
      })}
      ${renderOpsCard({
        title: "Weekly Report",
        status:
          weeklyStatus?.present === false
            ? "未生成"
            : weeklyStatus?.auditOk === false || (weeklyStatus?.staleCount ?? 0) > 0
              ? "要対応"
              : "正常",
        tone:
          weeklyStatus?.present === false
            ? "muted"
            : weeklyStatus?.auditOk === false || (weeklyStatus?.staleCount ?? 0) > 0
              ? "warn"
              : "ok",
        subtitle: weeklyStatus?.present
          ? `eval ${weeklyStatus?.evalRuns ?? 0}件 / drill ${weeklyStatus?.drillRuns ?? 0}件 / stale ${weeklyStatus?.staleCount ?? 0}`
          : "最新レポートなし",
        meta: weeklyStatus?.ts ? formatRelativeTimestamp(weeklyStatus.ts) : "—",
      })}
      ${renderOpsCard({
        title: "Local First",
        status: localFirst?.ollamaApiOk ? "準備完了" : localFirst?.ollamaCli ? "API待ち" : "未導入",
        tone: localFirst?.ollamaApiOk ? "ok" : localFirst?.ollamaCli ? "warn" : "muted",
        subtitle: localFirst
          ? `${localFirst.configuredModel} · ${localFirst.modelAvailable ? "利用可" : "未pull"}`
          : "状態未取得",
        meta: localFirst ? (localFirst.error ? localFirst.error : localFirst.baseUrl) : "—",
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
        skill: gmailSkill,
        skillsHref,
        loading: props.skillsLoading,
        error: props.skillsError,
      })}
      ${renderIntegrationCard({
        title: "Notion",
        subtitle: "Notion API",
        skill: notionSkill,
        skillsHref,
        loading: props.skillsLoading,
        error: props.skillsError,
      })}
      ${renderIntegrationCard({
        title: "Neuronic",
        subtitle: "タスク同期（roby-mail経由）",
        skill: neuronicSkill,
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
}) {
  return html`
    <div class="card">
      <div class="card-title">${params.title}</div>
      <div class="row" style="margin-top: 12px;">
        <span class="pill ${params.tone}">${params.status}</span>
        <span class="muted" style="margin-left:auto;">${params.meta}</span>
      </div>
      <div class="muted" style="margin-top: 8px;">${params.subtitle}</div>
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

function resolveSkillStatus(skill: SkillStatusEntry | undefined) {
  if (!skill) {
    return { label: "未インストール", tone: "danger" };
  }
  const missing = computeSkillMissing(skill);
  if (missing.length > 0) {
    return { label: `不足: ${missing.join(", ")}`, tone: "warn" };
  }
  if (skill.disabled) {
    return { label: "無効", tone: "warn" };
  }
  if (!skill.eligible) {
    return { label: "ブロック中", tone: "warn" };
  }
  return { label: "準備完了", tone: "ok" };
}

function renderIntegrationCard(params: {
  title: string;
  subtitle: string;
  skill?: SkillStatusEntry;
  skillsHref: string;
  loading: boolean;
  error: string | null;
  requiredHint?: string;
}) {
  const status = resolveSkillStatus(params.skill);
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
