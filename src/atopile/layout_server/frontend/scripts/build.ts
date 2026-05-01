import { mkdir } from "node:fs/promises";
import * as esbuild from "esbuild";
import { fileURLToPath } from "node:url";

const watch = process.argv.includes("--watch");

const staticDirUrl = new URL("../../static/", import.meta.url);
const staticDir = fileURLToPath(staticDirUrl);

await mkdir(staticDir, { recursive: true });

const buildOptions: esbuild.BuildOptions = {
  entryPoints: [fileURLToPath(new URL("../src/main.ts", import.meta.url))],
  bundle: true,
  outfile: fileURLToPath(new URL("editor.js", staticDirUrl)),
  format: "esm",
  sourcemap: true,
};

if (watch) {
  const ctx = await esbuild.context(buildOptions);
  await ctx.watch();
  await new Promise(() => {});
} else {
  await esbuild.build(buildOptions);
}
