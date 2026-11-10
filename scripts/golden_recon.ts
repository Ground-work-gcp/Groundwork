/**
 * Golden-fixture exporter for the reconciliation engine port.
 *
 * Runs Magpie's ORIGINAL, unmodified TypeScript generator, ingestion and matcher (imported
 * from the legacy source tree) and writes the inputs it produced plus every MatchResult, so
 * the Python port in backend/app/domain/reconciliation/engine can be regression-tested
 * result-by-result. This is the evidence that the algorithm was ported, not rewritten.
 *
 * usage (from the Groundwork root):
 *   bun run scripts/golden_recon.ts <magpie-source-root> backend/tests/fixtures/recon_golden [count] [seed]
 */
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

const [rootArg, outArg, countArg, seedArg] = process.argv.slice(2);
if (!rootArg || !outArg) {
  console.error("usage: bun run scripts/golden_recon.ts <magpie-root> <out-dir> [count] [seed]");
  process.exit(2);
}
const root = resolve(rootArg);
const outDir = resolve(outArg);

const gen = await import(join(root, "lib/recon/generate.ts"));
const moneyMod = await import(join(root, "lib/recon/money.ts"));
const ingestMod = await import(join(root, "lib/recon/ingest.ts"));
const matchMod = await import(join(root, "lib/recon/match.ts"));
const cashMod = await import(join(root, "lib/recon/cash.ts"));

const count = countArg ? Number(countArg) : gen.DEFAULTS.count;
const seed = seedArg ? Number(seedArg) : gen.DEFAULTS.seed;
mkdirSync(outDir, { recursive: true });

// The seed script writes the CSVs; running it as-is keeps the files byte-identical to Magpie's.
const proc = Bun.spawnSync(
  ["bun", "run", join(root, "scripts/recon-seed.ts"), "--count", String(count), "--seed", String(seed), "--out", outDir],
  { cwd: root },
);
if (proc.exitCode !== 0) {
  console.error(new TextDecoder().decode(proc.stderr));
  process.exit(1);
}

const SOURCES = ["payments.csv", "refunds.csv", "chargebacks.csv", "settlements.csv", "recon.csv", "bank.csv", "ledger.csv"];
const sources: Record<string, string> = {};
for (const name of SOURCES) sources[name] = readFileSync(join(outDir, name), "utf8");

const ingested = ingestMod.ingestSources(sources);
const run = matchMod.runMatch(ingested);
const queue = run.results
  .filter((r: { outcome: string }) => r.outcome !== "AUTO_MATCHED")
  .sort((a: { amount: number }, b: { amount: number }) => Math.abs(b.amount) - Math.abs(a.amount));
const cash = cashMod.buildCashSeries(ingested, run.results, queue);
const { elapsedMs: _elapsed, ...stats } = run.stats;

writeFileSync(
  join(outDir, "expected.json"),
  JSON.stringify(
    {
      note: "Produced by the original Magpie TypeScript matcher (lib/recon/match.ts) via scripts/golden_recon.ts. Do not edit by hand.",
      options: { count, seed },
      ingest: {
        counts: Object.fromEntries(
          ["payments", "refunds", "chargebacks", "settlements", "recon", "bank", "ledger"].map((k) => [k, ingested[k].length]),
        ),
        rejections: ingested.rejections,
        files: ingested.files,
      },
      results: run.results,
      stats,
      cash,
      money: [0, 5, 100, 123456, 12345678, -98765432, 100000000000].map((p) => [p, moneyMod.toIndianDecimal(p), moneyMod.toDecimal(p)]),
    },
    null,
    1,
  ),
);
console.log(`results=${run.results.length} rejections=${ingested.rejections.length} byOutcome=${JSON.stringify(stats.byOutcome)}`);
