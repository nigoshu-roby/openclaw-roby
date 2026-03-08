import type { GatewayBrowserClient } from "../gateway.ts";
import type { RobyOpsStatus } from "../types.ts";

export type RobyStatusState = {
  client: GatewayBrowserClient | null;
  connected: boolean;
  robyOpsLoading: boolean;
  robyOpsStatus: RobyOpsStatus | null;
  robyOpsError: string | null;
  robyOpsNotifyBusy: boolean;
  robyOpsNotifyMessage: string | null;
};

export async function loadRobyOpsStatus(state: RobyStatusState) {
  if (!state.client || !state.connected || state.robyOpsLoading) {
    return;
  }
  state.robyOpsLoading = true;
  state.robyOpsError = null;
  try {
    const res = await state.client.request("roby.status", {});
    state.robyOpsStatus = res as RobyOpsStatus;
  } catch (err) {
    state.robyOpsError = String(err);
  } finally {
    state.robyOpsLoading = false;
  }
}

export async function notifyRobyOpsSummary(state: RobyStatusState) {
  if (!state.client || !state.connected || state.robyOpsNotifyBusy) {
    return;
  }
  state.robyOpsNotifyBusy = true;
  state.robyOpsNotifyMessage = null;
  try {
    const res = await state.client.request("roby.notifyOpsSummary", {});
    const generatedAt =
      typeof res.report?.generated_at === "string" && res.report.generated_at.trim()
        ? res.report.generated_at.trim()
        : "";
    state.robyOpsNotifyMessage =
      res.ok === true
        ? generatedAt
          ? `Slackへ再送しました (${generatedAt})`
          : "Slackへ再送しました"
        : `Slack再送に失敗しました (exit=${res.exitCode ?? "-"})${res.stderr ? `: ${res.stderr}` : ""}`;
  } catch (err) {
    state.robyOpsNotifyMessage = `Slack再送に失敗しました: ${String(err)}`;
  } finally {
    state.robyOpsNotifyBusy = false;
  }
}
