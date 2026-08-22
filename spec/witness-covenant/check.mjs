#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { verifyWitnessCovenant } from "./kernel.mjs";

function loadDirectory(root) {
  const rootStat = fs.lstatSync(root);
  if (rootStat.isSymbolicLink() || !rootStat.isDirectory()) throw new Error("dossier root must be a real directory");
  const bundle = new Map();
  function visit(directory) {
    for (const item of fs.readdirSync(directory, { withFileTypes: true })) {
      const absolute = path.join(directory, item.name);
      const stat = fs.lstatSync(absolute);
      if (stat.isSymbolicLink()) throw new Error("dossier contains a symlink");
      if (item.isDirectory()) visit(absolute);
      else if (item.isFile()) bundle.set(path.relative(root, absolute).split(path.sep).join("/"), new Uint8Array(fs.readFileSync(absolute)));
      else throw new Error("dossier contains a non-regular member");
    }
  }
  visit(root); return bundle;
}

export async function main(argv = process.argv.slice(2)) {
  const contextIndex = argv.indexOf("--context");
  if (argv.length !== 3 || contextIndex < 0 || !argv[contextIndex + 1]) throw new Error("usage: check.mjs DOSSIER --context CONTEXT");
  const dossier = argv[0]; const context = argv[contextIndex + 1];
  const report = await verifyWitnessCovenant(loadDirectory(dossier), new Uint8Array(fs.readFileSync(context)));
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`); return report.exit_code;
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) main().then((code) => { process.exitCode = code; }).catch((error) => { process.stderr.write(`${error.message}\n`); process.exitCode = 2; });
