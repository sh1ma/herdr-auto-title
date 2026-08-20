// @ts-nocheck -- Pi supplies Node's runtime types when it loads this extension.
// Installed by herdr-auto-title. The installer substitutes no paths: Pi's configured
// agent directory is used at runtime so PI_CODING_AGENT_DIR keeps working.
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const configDir =
  process.env.PI_CODING_AGENT_DIR || join(homedir(), ".pi", "agent");
const generatorPath = join(
  configDir,
  "herdr-auto-title",
  "herdr-auto-title.py",
);

function enabled(): boolean {
  return (
    process.env.HERDR_ENV === "1" &&
    Boolean(process.env.HERDR_SOCKET_PATH) &&
    Boolean(process.env.HERDR_PANE_ID || process.env.HERDR_TAB_ID)
  );
}

export default function (pi: any) {
  if (!enabled() || !existsSync(generatorPath)) {
    return;
  }

  pi.on("input", (event: any, ctx: any) => {
    // Only actual user input should rename the tab; extensions can enqueue their
    // own messages, which are not a new piece of user work.
    if (
      event?.source === "extension" ||
      typeof event?.text !== "string" ||
      !event.text.trim()
    ) {
      return;
    }

    const model =
      ctx?.model?.provider && ctx?.model?.id
        ? `${ctx.model.provider}/${ctx.model.id}`
        : undefined;
    const payload = {
      agent: "pi",
      session_id: ctx?.sessionManager?.getSessionId?.() || "unknown",
      prompt: event.text,
      model,
    };
    const child = spawn("python3", [generatorPath], {
      cwd: ctx?.cwd || process.cwd(),
      detached: true,
      env: process.env,
      stdio: ["pipe", "ignore", "ignore"],
    });

    // A missing Python runtime or a removed hook must never stop a Pi prompt.
    child.on("error", () => undefined);
    child.stdin.on("error", () => undefined);
    child.stdin.end(`${JSON.stringify(payload)}\n`);
    child.unref();
  });
}
