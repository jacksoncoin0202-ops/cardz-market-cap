import { cpSync, existsSync, mkdirSync, rmSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

if (process.env.CARDZ_BUILD_TARGET === "node") {
  const appRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
  const standaloneApp = resolve(appRoot, ".next", "standalone", "apps", "web");
  const copies = [
    [resolve(appRoot, ".next", "static"), resolve(standaloneApp, ".next", "static")],
    [resolve(appRoot, "public"), resolve(standaloneApp, "public")],
  ];

  for (const [source, destination] of copies) {
    if (!existsSync(source)) throw new Error(`Standalone build input is missing: ${source}`);
    rmSync(destination, { recursive: true, force: true });
    mkdirSync(dirname(destination), { recursive: true });
    cpSync(source, destination, { recursive: true });
  }
  // Node production serves every market image through the generation-aware
  // route. Keeping the build-time seed assets under public/ would shadow that
  // route and allow an image from another generation to return HTTP 200.
  rmSync(resolve(standaloneApp, "public", "market-assets"), {
    recursive: true,
    force: true,
  });
  process.stdout.write("Prepared Node standalone assets without build-time market images.\n");
}
