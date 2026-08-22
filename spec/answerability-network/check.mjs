#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { verifyAnswerabilityNetwork } from "./kernel.mjs";

function loadDirectory(root) {
  const stat = fs.lstatSync(root); if (stat.isSymbolicLink() || !stat.isDirectory()) throw new Error("network root must be a real directory");
  const bundle = new Map();
  function visit(directory) { for (const item of fs.readdirSync(directory, { withFileTypes: true })) { const absolute = path.join(directory, item.name); const found = fs.lstatSync(absolute); if (found.isSymbolicLink()) throw new Error("network dossier contains a symlink"); if (item.isDirectory()) visit(absolute); else if (item.isFile()) bundle.set(path.relative(root, absolute).split(path.sep).join("/"), new Uint8Array(fs.readFileSync(absolute))); else throw new Error("network dossier contains a non-regular member"); } }
  visit(root); return bundle;
}

export async function main(argv = process.argv.slice(2)) {
  const index = argv.indexOf("--context"); if (argv.length !== 3 || index !== 1) throw new Error("usage: check.mjs DOSSIER --context CONTEXT");
  const report = await verifyAnswerabilityNetwork(loadDirectory(argv[0]), new Uint8Array(fs.readFileSync(argv[2])));
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`); return report.exit_code;
}
if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) main().then((code) => { process.exitCode = code; }).catch((error) => { process.stderr.write(`${error.message}\n`); process.exitCode = 2; });
