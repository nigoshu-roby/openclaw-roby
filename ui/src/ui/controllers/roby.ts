import type { GatewayBrowserClient } from "../gateway.ts";
import type { RobyOpsStatus } from "../types.ts";

export type RobyStatusState = {
  client: GatewayBrowserClient | null;
  connected: boolean;
  robyOpsLoading: boolean;
  robyOpsStatus: RobyOpsStatus | null;
  robyOpsError: string | null;
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
