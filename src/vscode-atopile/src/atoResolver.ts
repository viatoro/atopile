import { execFile, spawn } from "child_process";
import * as fs from "fs";
import * as path from "path";
import { promisify } from "util";
import * as vscode from "vscode";
import { ChannelLogger } from "./logger";

const execFileAsync = promisify(execFile);

export interface ResolvedBinary {
  /** Path to the executable to launch */
  command: string;
  /** Arguments prepended before any ato subcommand args */
  prefixArgs: string[];
  /** Extra environment variables required to launch the command */
  env?: Record<string, string>;
  /** Whether we're running a configured local ato binary */
  isLocal: boolean;
  /** Resolved absolute path to the ato binary */
  atoBinary?: string;
}

export class AtoNotResolveableMisconfigured extends Error {
  constructor(configuredAto: string) {
    super(
      `Configured ato binary not found: ${configuredAto}. Fix the atopile.ato setting or clear it to use the managed install.`,
    );
    this.name = "AtoNotResolveableMisconfigured";
  }
}

export class AtoNotResolveableWhichFailed extends Error {
  constructor(configuredAto: string, detail: string) {
    super(`Configured ato binary failed verification: ${configuredAto}. ${detail}`);
    this.name = "AtoNotResolveableWhichFailed";
  }
}

export async function resolveAto(
  context: vscode.ExtensionContext,
  logger: ChannelLogger,
  version: string,
): Promise<ResolvedBinary> {
  const configuredAto = getConfiguredAtoPath();
  let resolved: Omit<ResolvedBinary, "atoBinary">;
  let verifyPrefixArgs: string[] = [];

  if (!configuredAto) {
    const managedUv = await resolveManagedUv(context, logger);
    const constraintsPath = path.join(context.extensionPath, "constraints.txt");
    verifyPrefixArgs = [
      "tool",
      "run",
      ...managedUv.prefixArgs,
      "-p",
      "3.14",
      ...(fs.existsSync(constraintsPath) ? ["--constraints", constraintsPath] : []),
      "--from",
      `atopile==${version}`,
    ];
    logger.info(`production mode: atopile==${version}`);
    resolved = {
      command: managedUv.command,
      prefixArgs: [...verifyPrefixArgs, "ato"],
      env: managedUv.env,
      isLocal: false,
    };
  } else {
    if (!fs.existsSync(configuredAto)) {
      throw new AtoNotResolveableMisconfigured(configuredAto);
    }

    resolved = {
      command: configuredAto,
      prefixArgs: [],
      isLocal: true,
    };
  }

  try {
    const atoBinary = await resolveAndVerifyAtoBinary(
      logger,
      resolved.command,
      verifyPrefixArgs,
      resolved.env,
    );
    if (resolved.isLocal) {
      logger.info(`local mode: ${atoBinary}`);
    }
    return {
      ...resolved,
      atoBinary,
    };
  } catch (err) {
    if (!configuredAto) {
      throw err;
    }

    const detail = err instanceof Error ? err.message : String(err);
    throw new AtoNotResolveableWhichFailed(configuredAto, detail);
  }
}

async function resolveManagedUv(
  context: vscode.ExtensionContext,
  logger: ChannelLogger,
): Promise<{
  command: string;
  prefixArgs: string[];
  env: Record<string, string>;
}> {
  const rootDir = path.join(context.globalStorageUri.fsPath, "uv");
  const command = path.join(rootDir, process.platform === "win32" ? "uv.exe" : "uv");
  const cacheDir = path.join(rootDir, "cache");
  const pythonInstallDir = path.join(rootDir, "data", "python");
  const managedUv = {
    command,
    prefixArgs: ["--cache-dir", cacheDir],
    env: {
      UV_PYTHON_INSTALL_DIR: pythonInstallDir,
    },
  };

  for (const dir of [cacheDir, pythonInstallDir]) {
    fs.mkdirSync(dir, { recursive: true });
  }

  logger.info(`uv cache dir: ${cacheDir}`);
  logger.info(`uv python install dir: ${pythonInstallDir}`);

  if (fs.existsSync(command)) {
    logger.info(`Found bootstrapped uv: ${command}`);
    return managedUv;
  }

  await vscode.window.withProgress(
    {
      location: vscode.ProgressLocation.Notification,
      title: "atopile: Installing uv...",
      cancellable: false,
    },
    () => spawnAndLog(logger, "uv install", ...getUvBootstrapCommand(rootDir)),
  );

  if (!fs.existsSync(command)) {
    throw new Error(`uv bootstrap completed but binary not found at ${command}`);
  }

  logger.info(`Bootstrapped uv to: ${command}`);
  return managedUv;
}

/** Resolve the absolute path to the ato binary and verify it via self-check. */
async function resolveAndVerifyAtoBinary(
  logger: ChannelLogger,
  command: string,
  prefixArgs: string[] = [],
  env?: Record<string, string>,
): Promise<string> {
  const execOptions = { env: { ...process.env, ...env }, encoding: "utf8" as const };
  let atoBinary = command;
  if (prefixArgs.length > 0) {
    const { stdout } = await execFileAsync(
      command,
      [...prefixArgs, "python", "-c", `import shutil; print(shutil.which("ato"))`],
      execOptions,
    );
    const result = stdout.trim();
    if (!result || result === "None") {
      logger.warn("Could not resolve ato binary");
      throw new Error("Could not resolve ato binary");
    }
    atoBinary = result;
  }

  try {
    await execFileAsync(atoBinary, ["self-check"], execOptions);
  } catch (error) {
    const execError = error as Error & { stderr?: string; stdout?: string };
    throw new Error(
      `ato self-check failed: ${execError.stderr?.trim() || execError.stdout?.trim() || execError.message}`,
    );
  }

  return atoBinary;
}

export function getConfiguredAtoPath(): string {
  const rawValue = vscode.workspace.getConfiguration("atopile").get<string>("ato", "")?.trim() ?? "";
  if (!rawValue) {
    return "";
  }

  const workspaceFolder = vscode.workspace.workspaceFolders?.[0];
  if (!workspaceFolder) {
    return rawValue;
  }

  return rawValue.replace(/\$\{workspace(?:Folder|Root)\}/g, workspaceFolder.uri.fsPath);
}

function getUvBootstrapCommand(installDir: string): [string, string[]] {
  fs.mkdirSync(installDir, { recursive: true });
  return process.platform === "win32"
    ? [
        "powershell",
        [
          "-NoProfile",
          "-ExecutionPolicy",
          "Bypass",
          "-Command",
          `$env:UV_INSTALL_DIR="${installDir}"; irm https://astral.sh/uv/install.ps1 | iex`,
        ],
      ]
    : [
        "sh",
        [
          "-c",
          `curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="${installDir}" sh`,
        ],
      ];
}

function spawnAndLog(
  logger: ChannelLogger,
  label: string,
  command: string,
  args: string[],
): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    const proc = spawn(command, args, { stdio: ["ignore", "pipe", "pipe"] });
    const scopedLogger = logger.scope(label);

    const log = (chunk: Buffer) => {
      for (const line of chunk.toString().trimEnd().split("\n")) {
        scopedLogger.info(line);
      }
    };

    proc.stdout?.on("data", log);
    proc.stderr?.on("data", log);
    proc.on("error", (err) => reject(new Error(`${label} failed to start: ${err.message}`)));
    proc.on("exit", (code) => {
      if (code === 0) {
        resolve();
      } else {
        reject(new Error(`${label} exited with code ${code}`));
      }
    });
  });
}
