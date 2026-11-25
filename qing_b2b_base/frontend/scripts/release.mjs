import { createHash } from "node:crypto";
import { readdir, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";

async function files(root, prefix = "") {
  const entries = await readdir(join(root, prefix), { withFileTypes: true });
  const children = await Promise.all(entries.map((entry) => {
    const name = join(prefix, entry.name);
    return entry.isDirectory() ? files(root, name) : name;
  }));
  return children.flat().sort();
}
const { version } = JSON.parse(await readFile("package.json", "utf8"));
const packages = ["vue", "@vue/runtime-dom", "@vue/runtime-core", "@vue/reactivity", "@vue/shared", "vant", "@vant/use", "@vant/popperjs"];
const notices = await Promise.all(packages.map(async (name) => {
  const root = join("node_modules", name);
  const { version: packageVersion } = JSON.parse(await readFile(join(root, "package.json"), "utf8"));
  const license = await readFile(join(root, "LICENSE"), "utf8");
  return [name + " " + packageVersion, license.trim()].join("\n\n");
}));
for (const [name, file] of [["Barlow Semi Condensed", "barlow-semi-condensed"], ["Nunito", "nunito"], ["Jost", "jost"], ["Bodoni Moda", "bodoni-moda"]]) {
  notices.push([name + " (bundled font)", (await readFile("src/fonts/" + file + "-OFL.txt", "utf8")).trim()].join("\n\n"));
}
await writeFile("dist/THIRD_PARTY_NOTICES.txt", notices.join("\n\n") + "\n");
const checksums = {};
for (const name of await files("dist")) {
  if (["release.json", "runtime.js"].includes(name)) continue;
  checksums[name] = createHash("sha256").update(await readFile(join("dist", name))).digest("hex");
}
const manifest = {
  version,
  supportedApi: "1.x",
  sha256: createHash("sha256").update(JSON.stringify(checksums)).digest("hex"),
  files: checksums,
};
await writeFile("dist/release.json", JSON.stringify(manifest, null, 2) + "\n");
