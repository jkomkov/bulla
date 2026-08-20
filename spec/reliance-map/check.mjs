#!/usr/bin/env node

import { readFileSync } from 'node:fs'
import { pathToFileURL } from 'node:url'
import { computeRelianceMap } from './kernel.mjs'

async function main() {
  const args = process.argv.slice(2); const ledger = args.indexOf('--ledger'); const context = args.indexOf('--context')
  if (args.length !== 5 || ledger !== 1 || context !== 3) throw new Error('usage: node check.mjs GRAPH --ledger LEDGER --context CONTEXT')
  const report = await computeRelianceMap(readFileSync(args[0]), readFileSync(args[2]), readFileSync(args[4]))
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`)
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? '').href) main().catch((error) => { process.stderr.write(`${error.message}\n`); process.exitCode = 2 })
