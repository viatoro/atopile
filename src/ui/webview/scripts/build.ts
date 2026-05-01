import { copyFile, mkdir, rm } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const watch = process.argv.includes("--watch");

const appRoot = fileURLToPath(new URL("../../../../", import.meta.url));
const webviewRoot = fileURLToPath(new URL("../", import.meta.url));
const outDirUrl = new URL("../../../vscode-atopile/webview-dist/", import.meta.url);
const outDir = fileURLToPath(outDirUrl);

const assets = [
  ["../common/logo.png", "logo.png"],
  ["../common/logo-dark.svg", "logo-dark.svg"],
  ["../common/logo-light.svg", "logo-light.svg"],
  ["../sidebar-details/assets/occt-import-js.wasm", "occt-import-js.wasm"],
  ["../sidebar-details/assets/model-viewer.min.js", "model-viewer.min.js"],
] as const;
const entrypoints = await Array.fromAsync(
  new Bun.Glob("*/main.tsx").scan({
    cwd: webviewRoot,
    onlyFiles: true,
  }),
);

if (entrypoints.length === 0) {
  throw new Error(`No webview entrypoints found under ${webviewRoot}`);
}

await run(
  ["uv", "run", "python", "-m", "atopile.generate_types"],
  appRoot,
);

if (!watch) {
  await rm(outDir, { force: true, recursive: true });
}
await mkdir(outDir, { recursive: true });
await copyAssets();

const buildArgs = [
  process.execPath,
  "build",
  ...entrypoints,
  "--root",
  webviewRoot,
  "--outdir",
  outDir,
  "--entry-naming",
  "[dir]/index.[ext]",
  "--chunk-naming",
  "chunks/[name]-[hash].[ext]",
  "--splitting",
  "--format",
  "esm",
];

if (watch) {
  buildArgs.push("--watch");
} else {
  buildArgs.push("--minify");
}

await run(buildArgs, webviewRoot);

async function copyAssets(): Promise<void> {
  await Promise.all(
    assets.map(([source, target]) =>
      copyFile(
        fileURLToPath(new URL(source, import.meta.url)),
        fileURLToPath(new URL(target, outDirUrl)),
      ),
    ),
  );
}

async function run(cmd: string[], cwd: string): Promise<void> {
  const proc = Bun.spawn(cmd, {
    cwd,
    stdin: "inherit",
    stdout: "inherit",
    stderr: "inherit",
  });
  const exitCode = await proc.exited;
  if (exitCode !== 0) {
    process.exit(exitCode);
  }
}
