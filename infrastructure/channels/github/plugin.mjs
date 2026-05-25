/**
 * commaBot GitHub Webhook Channel Adapter
 *
 * Receives pre-processed webhook messages from the Flask listener and relays
 * them into the Letta Code listener queue via the channel adapter contract.
 * This puts the agent on the listener code path, which has working transcript
 * writing and reflection launching (unlike the SDK headless path).
 *
 * Architecture:
 *   GitHub webhook → Tailscale Funnel → Flask listener (port 8080)
 *     → POST localhost:3000/relay → this adapter → onMessage → listener queue → agent
 *
 * The Flask listener handles signature verification, event formatting, and
 * self-filtering (commabot[bot]). This adapter just accepts the pre-processed
 * format and feeds it into the channel system.
 */

import http from "node:http";

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const PORT = parseInt(process.env.GITHUB_CHANNEL_PORT || "3000", 10);
const HOST = process.env.GITHUB_CHANNEL_HOST || "127.0.0.1";
const PATH = "/relay";

// ---------------------------------------------------------------------------
// Channel plugin
// ---------------------------------------------------------------------------

export const channelPlugin = {
  metadata: {
    id: "github",
    displayName: "GitHub Webhooks",
  },

  // The MessageChannel tool dispatches through plugin.messageActions.handleAction(),
  // NOT through adapter.sendMessage(). This channel is one-way inbound — the agent
  // does real work via `gh` CLI (Bash tool), then sends a status receipt via
  // MessageChannel to close out the turn. The receipt is a one-line summary of
  // what was already done on GitHub, not the work itself.
  messageActions: {
    describeMessageTool(_params) {
      return {
        actions: [],
        capabilities: [],
      };
    },
    async handleAction(ctx) {
      const msg = ctx.request?.message ?? "";
      const threadId = ctx.request?.threadId ?? "";
      // Log the receipt for traceability in docker logs
      console.log(
        `[github-channel] receipt: thread=${threadId} msg=${msg.slice(0, 200)}`,
      );
      return (
        `✅ Receipt logged. ` +
        `GitHub interactions are via \`gh\` CLI (Bash tool), not MessageChannel. ` +
        `MessageChannel is for status receipts only — one-line summaries of ` +
        `work already completed on GitHub.`
      );
    },
  },

  async createAdapter(account) {
    let server = null;
    let running = false;

    const adapter = {
      id: `github:${account.accountId}`,
      channelId: "github",
      accountId: account.accountId,
      name: account.displayName ?? "GitHub Webhooks",

      async start() {
        if (running) return;

        server = http.createServer(async (req, res) => {
          try {
            const url = new URL(req.url, `http://${req.headers.host}`);

            // Health check
            if (req.method === "GET" && url.pathname === "/health") {
              res.writeHead(200, { "content-type": "application/json" });
              res.end(
                JSON.stringify({
                  status: "ok",
                  channel: "github",
                  running: true,
                }),
              );
              return;
            }

            // Relay endpoint — accepts Flask's pre-processed format
            if (req.method !== "POST" || url.pathname !== PATH) {
              res.writeHead(404).end("not found");
              return;
            }

            // Read request body
            const body = await readBody(req);
            const data = JSON.parse(body.toString("utf-8"));

            if (!data.message || typeof data.message !== "string") {
              res.writeHead(400, { "content-type": "application/json" });
              res.end(JSON.stringify({ error: "missing or invalid 'message' field" }));
              return;
            }

            // ACK immediately — the listener queue owns the agent turn
            res.writeHead(202, { "content-type": "application/json" });
            res.end(JSON.stringify({ ok: true }));

            // Feed into the channel system
            if (!adapter.onMessage) {
              console.warn("[github-channel] onMessage not registered yet; dropping message");
              return;
            }

            // Use a stable chatId for routing. Flask already formats the
            // message with repo info, so we use a fixed chatId that maps
            // to our route in routing.yaml.
            const chatId = data.chatId || "carl-stone/comma";

            void adapter
              .onMessage({
                channel: "github",
                accountId: account.accountId,
                chatId,
                chatType: "channel",
                senderId: data.senderId || "github",
                senderName: data.senderName || "GitHub",
                text: data.message,
                timestamp: Date.now(),
                messageId: data.deliveryId || `delivery-${Date.now()}`,
                threadId: null,  // Route uses __root__ key; issue/PR number is in the message text
              })
              .catch((err) => {
                console.error("[github-channel] failed to enqueue message:", err);
              });
          } catch (err) {
            console.error("[github-channel] request error:", err);
            if (!res.headersSent) {
              res.writeHead(500).end("error");
            }
          }
        });

        await new Promise((resolve, reject) => {
          server.once("error", reject);
          server.listen(PORT, HOST, () => resolve());
        });

        running = true;
        console.log(`[github-channel] listening on http://${HOST}:${PORT}${PATH}`);
      },

      async stop() {
        if (!server || !running) return;
        await new Promise((resolve) => server.close(() => resolve()));
        server = null;
        running = false;
        console.log("[github-channel] stopped");
      },

      isRunning() {
        return running;
      },

      // One-way inbound: the agent uses gh CLI to interact with GitHub,
      // not MessageChannel. The messageActions property on the channelPlugin
      // handles the MessageChannel tool dispatch — this adapter method is
      // not called by the MessageChannel tool but may be called by other
      // framework paths (e.g., unbound route instructions). Return a clear
      // error for any framework path that tries to send outbound.
      async sendMessage(_chatId, _text, _opts) {
        throw new Error(
          "GitHub channel is one-way inbound. Use the gh CLI (Bash tool) to " +
            "post comments, create PRs, etc. Do not use MessageChannel for GitHub.",
        );
      },

      async sendDirectReply(chatId, text) {
        console.warn(
          `[github-channel] sendDirectReply called for ${chatId}: ${text}. ` +
            "This channel is one-way inbound — use gh CLI instead.",
        );
      },

      onMessage: undefined,
    };

    return adapter;
  },
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function readBody(req, maxBytes = 5_000_000) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on("data", (chunk) => {
      size += chunk.length;
      if (size > maxBytes) {
        reject(new Error("request body too large"));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => resolve(Buffer.concat(chunks)));
    req.on("error", reject);
  });
}
