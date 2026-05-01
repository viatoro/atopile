import * as net from "net";
import * as vscode from "vscode";

function isTruthy(value: string | undefined): boolean {
  if (!value) return false;
  const n = value.toLowerCase();
  return n === "1" || n === "true" || n === "yes";
}

export function isWebIdeUi(): boolean {
  if (vscode.env.uiKind === vscode.UIKind.Web) return true;
  const env = process.env;
  return (
    isTruthy(env.WEBIDE) ||
    isTruthy(env.WEB_IDE_MODE) ||
    Boolean(env.OPENVSCODE_SERVER_ROOT)
  );
}

export function findFreePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.listen(0, "127.0.0.1", () => {
      const port = (server.address() as net.AddressInfo).port;
      server.close(() => resolve(port));
    });
    server.on("error", reject);
  });
}

/**
 * Probe a TCP port with a single connect attempt.
 *
 * Resolves true if the port accepts the connection within `timeoutMs`,
 * false on ECONNREFUSED, timeout, or any other socket error.
 */
export function waitForPortListening(
  port: number,
  host = "127.0.0.1",
  timeoutMs = 2000,
): Promise<boolean> {
  return new Promise((resolve) => {
    const sock = new net.Socket();
    let done = false;
    const finish = (ok: boolean) => {
      if (done) return;
      done = true;
      sock.destroy();
      resolve(ok);
    };
    sock.setTimeout(timeoutMs);
    sock.once("connect", () => finish(true));
    sock.once("timeout", () => finish(false));
    sock.once("error", () => finish(false));
    sock.connect(port, host);
  });
}
