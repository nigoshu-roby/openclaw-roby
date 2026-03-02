import { describe, expect, it, vi } from "vitest";
import type { GatewayBrowserClient } from "../gateway.ts";
import type { ChatAttachment } from "../ui-types.ts";
import { sendChatMessage, type ChatState } from "./chat.ts";

function createState(client: GatewayBrowserClient): ChatState {
  return {
    client,
    connected: true,
    sessionKey: "main",
    chatLoading: false,
    chatMessages: [],
    chatThinkingLevel: null,
    chatSending: false,
    chatMessage: "",
    chatAttachments: [],
    chatRunId: null,
    chatStream: null,
    chatStreamStartedAt: null,
    lastError: null,
  };
}

function sampleAttachment(): ChatAttachment {
  return {
    id: "att-1",
    mimeType: "image/png",
    dataUrl: "data:image/png;base64,aGVsbG8=",
  };
}

describe("sendChatMessage orchestrator attachment routing", () => {
  it("routes to orchestrator.run when attachment is present even if message starts with slash", async () => {
    const request = vi.fn(async (method: string) => {
      if (method === "orchestrator.run") {
        return {
          result: {
            route: "qa_gemini",
            elapsed_ms: 12,
            action: { route: "qa_gemini", executed: true, ok: true, output: "ok" },
          },
          returnCode: 0,
          termination: "exit",
          attachments: { count: 1 },
        };
      }
      throw new Error(`unexpected method: ${method}`);
    });
    const client = { request } as unknown as GatewayBrowserClient;
    const state = createState(client);

    await sendChatMessage(state, "/help", [sampleAttachment()]);

    expect(request).toHaveBeenCalledTimes(1);
    expect(request).toHaveBeenCalledWith(
      "orchestrator.run",
      expect.objectContaining({
        sessionKey: "main",
        message: "/help",
        execute: true,
        attachments: expect.any(Array),
      }),
    );
  });

  it("keeps native chat.send for slash commands without attachments", async () => {
    const request = vi.fn(async (method: string) => {
      if (method === "chat.send") {
        return {};
      }
      throw new Error(`unexpected method: ${method}`);
    });
    const client = { request } as unknown as GatewayBrowserClient;
    const state = createState(client);

    await sendChatMessage(state, "/help", []);

    expect(request).toHaveBeenCalledTimes(1);
    expect(request).toHaveBeenCalledWith(
      "chat.send",
      expect.objectContaining({
        sessionKey: "main",
        message: "/help",
        deliver: false,
      }),
    );
  });

  it("rejects over-limit attachment count before RPC call", async () => {
    const request = vi.fn(async () => ({}));
    const client = { request } as unknown as GatewayBrowserClient;
    const state = createState(client);
    const attachments = Array.from({ length: 9 }, (_, i) => ({
      ...sampleAttachment(),
      id: `att-${i + 1}`,
    }));

    const runId = await sendChatMessage(state, "画像を確認して", attachments);

    expect(runId).toBeNull();
    expect(request).not.toHaveBeenCalled();
    expect(state.lastError).toContain("最大8件");
    expect(state.chatMessages.at(-1)).toMatchObject({
      role: "assistant",
    });
  });

  it("rejects oversized attachment before RPC call", async () => {
    const request = vi.fn(async () => ({}));
    const client = { request } as unknown as GatewayBrowserClient;
    const state = createState(client);
    const bigBase64 = "A".repeat(11_000_000);
    const attachment: ChatAttachment = {
      id: "big-1",
      mimeType: "image/png",
      dataUrl: `data:image/png;base64,${bigBase64}`,
    };

    const runId = await sendChatMessage(state, "大きい画像", [attachment]);

    expect(runId).toBeNull();
    expect(request).not.toHaveBeenCalled();
    expect(state.lastError).toContain("大きすぎます");
  });
});
