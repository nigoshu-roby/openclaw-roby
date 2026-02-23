import { html, nothing } from "lit";
import { formatRelativeTimestamp } from "../format.ts";
import { pathForTab } from "../navigation.ts";
import { formatCronSchedule } from "../presenter.ts";
import type { CronJob, CronRunLogEntry, SkillStatusEntry, SkillStatusReport } from "../types.ts";
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
                  <div class="stat-value">${formatCronSchedule(job.schedule)}</div>
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
