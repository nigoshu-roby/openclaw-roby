import { describe, expect, it } from "vitest";
import {
  ConnectErrorDetailCodes,
  readConnectErrorDetailCode,
  readConnectErrorRecoveryAdvice,
} from "./connect-error-details.js";

describe("connect-error-details", () => {
  it("reads error detail code when present", () => {
    expect(
      readConnectErrorDetailCode({
        code: ConnectErrorDetailCodes.AUTH_TOKEN_MISMATCH,
      }),
    ).toBe(ConnectErrorDetailCodes.AUTH_TOKEN_MISMATCH);
  });

  it("returns null when code is missing", () => {
    expect(readConnectErrorDetailCode({})).toBeNull();
    expect(readConnectErrorDetailCode(null)).toBeNull();
  });

  it("reads recovery advice when valid", () => {
    expect(
      readConnectErrorRecoveryAdvice({
        canRetryWithDeviceToken: true,
        recommendedNextStep: "retry_with_device_token",
      }),
    ).toEqual({
      canRetryWithDeviceToken: true,
      recommendedNextStep: "retry_with_device_token",
    });
  });

  it("drops invalid recovery advice values", () => {
    expect(
      readConnectErrorRecoveryAdvice({
        canRetryWithDeviceToken: "yes",
        recommendedNextStep: "unknown",
      }),
    ).toEqual({});
  });
});
