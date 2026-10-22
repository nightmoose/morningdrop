---
title: "MorningDrop: a local Gemma agent that checks last night's data drops, and gets checked itself"
published: false
tags: devchallenge, hf26challenge, gemma, opensource
---

*This is a submission for the [Hacktoberfest Open-Source AI Challenge Week 3: Slow and Steady](https://dev.to/challenges/hacktoberfest-week3-2026-10-19)*

## What I Built

I'm a data engineer. Most of my mornings used to start the same way: open the folder where the nightly exports landed, open each CSV, scroll, squint, and try to work out whether anything was wrong before someone downstream noticed. Usually nothing was. That's what makes it so tiring: you do the same check every day so you can catch the one day something breaks.

**MorningDrop** does that check overnight so I don't have to. It's a small local agent you run from cron. When it wakes up it:

1. finds the files in a folder that are new since last night,
2. validates every row against a data contract with **DataContractGate**, offline,
3. hands a compact summary of the failures to **Gemma running locally in Ollama**, which writes a two-line morning summary and suggests fixes,
4. **checks Gemma's answer with AISchemaGate** before I ever see it, and
5. writes a Markdown report plus a two-line `latest.txt` I can read before coffee.

On a quiet night where everything passed, it doesn't even wake the model. Sloths conserve energy. 🦥

It's for anyone who gets data they don't control: vendor CSVs, storefront exports, CRM dumps, the "can you just drop it in the shared folder" files.

## Demo

<!-- TODO(Alex): record a 30–60 s terminal GIF: `ls samples/drops`, `morningdrop run`, `cat reports/latest.txt`, open the .md report. Upload and embed it here. -->

The sample drops in the repo have the kind of problems real exports have: a supplier that started sending `usd` instead of `USD` partway through the file, refunds arriving as `quantity = -1`, a customer with `"usa"` as a country code, and a brand-new `shipments` file nobody wrote a contract for. This is a real run with `gemma3:4b` on a CPU-only box:

```text
$ morningdrop run
The orders_2026-10-24.csv file failed for 63 rows due to currency and quantity issues.
Prioritize fixing the currency and quantity validation rules in the orders contract.
report: reports/2026-10-25.md  (21.7s, summary by model)
```

The full report adds suggested fixes and the evidence behind them:

```markdown
## Suggested fixes
- `orders_2026-10-24.csv` · `currency` · fix_data: Correct currency values to USD, EUR, or GBP.
- `orders_2026-10-24.csv` · `quantity` · fix_data: Ensure quantity values are greater than or equal to 1.
- `orders_2026-10-24.csv` · `customer_id` · fix_data: Validate customer_id against pattern: cus_[0-9]{5}

| File                       | Contract  | Rows | Passed | Failed | Status |
|----------------------------|-----------|-----:|-------:|-------:|--------|
| orders_2026-10-24.csv      | orders    |  240 |    177 |     63 | 🔴     |
| customers_2026-10-24.jsonl | customers |  120 |    116 |      4 | 🟡     |
| shipments_2026-10-24.csv   | —         |   80 |      0 |      0 | 🟡     |

### shipments_2026-10-24.csv
- No contract yet. Drafted one at contracts/_proposed/shipments.yaml; review it and move it into contracts/ to start enforcing.
```

The next night, after the exporter is fixed, the whole report is two lines and Gemma never gets called:

```text
1 drop(s) checked overnight; every row passed.
Everything passed. Nothing to do.
```

## Code

{% github nightmoose/morningdrop %}

MIT licensed, with tests (offline, using scripted fake models), CI on Python 3.9 and 3.12, and a CI job that installs Ollama and runs real Gemma end to end on a plain CPU runner.

## How I Built It

**The pipeline is boring on purpose.** Everything that has a correct answer is done in plain code. The model only does the one thing it's good at: turning `60 × enum_violation on currency` into a sentence a tired human will act on.

- **Validation: DataContractGate.** Each dataset has a YAML contract (types, required fields, regex patterns, enums, ranges). I use the `contractgate` Python package's offline validator, so no server and no network. CSV cells are coerced to the contract's types first. A value that doesn't parse stays a string, so the validator reports an honest `type_mismatch` instead of my code hiding it.
- **New datasets: draft a contract.** When a file shows up with no contract, MorningDrop infers one (types, ranges, small enums, date patterns), compiles it with DataContractGate to prove it's valid, and saves it to `contracts/_proposed/`. It never enforces a contract you haven't reviewed.
- **The model: Gemma via Ollama.** MorningDrop sends Gemma counts, field names, violation messages and the contract rule each one broke. It never sends whole rows, and passing rows never go to the model at all. The request uses Ollama's JSON-schema `format` so the model is steered toward the right shape from the start. Any OpenAI-compatible local server (llama.cpp, vLLM, LM Studio) works too.
- **Checking the model: AISchemaGate.** This is the part I care about most. More on it below.
- **Scheduling:** `morningdrop run` from cron, launchd or a systemd timer (examples in the repo), or `morningdrop watch` to poll.

### The model's output gets a contract too

Small local models are great until they aren't. While building this I watched them:

- **`gemma3:270m`** repeat my instructions back as its "summary" ("one sentence, what happened overnight (10-160 chars)"),
- **`gemma3:1b`** return perfectly valid JSON that announced *"shipments_2026-10-24.csv: 12 of 80 rows failed"*, for a file with no contract and no failures,
- **`gemma3:4b`** suggest a quantity range of "1–1000" when the contract says 1–500. It had never been shown the contract, so I started including the rule each violation broke.

Shape-checking alone wouldn't have caught most of that. So the model's answer goes through two layers:

1. **AISchemaGate** validates the JSON against [`summary_contract.yaml`](https://github.com/nightmoose/morningdrop/blob/main/morningdrop/summary_contract.yaml): `status` must be green, yellow or red; each line must be 10–160 characters; every fix needs `file`, `field`, `kind` and `suggestion`. It's the same engine and the same contract format as the data side, pointed at LLM output.
2. **Grounding checks** tie the answer to the facts. The status must equal the worst file's status. The two lines must name the worst file. **Every number in the text must already appear in the facts**, which turned out to be the single most useful check. And every fix must point at a real file and a real failing field.

If either layer fails, the violations go back to Gemma as a repair prompt, with the temperature nudged up so a stuck model can get unstuck. If only some of the *fixes* are wrong, those are quarantined and the rest are kept, the same way DataContractGate quarantines bad rows. If the model still can't get it right, or Ollama isn't even running, a plain template writes the report from the same facts.

**The result: every morning report is correct, and the model makes it nicer to read.** Data contracts at the edges of the pipeline, and an output contract at the edge of the model.

With the final prompt, `gemma3:4b` passes on the first try in about 20–35 seconds on a CPU-only box. The 1B and 270M models still get caught and fall back to the template, which is exactly what should happen.

### Optional: Sentry Agent Tracing

Set `SENTRY_DSN` and install the `sentry` extra, and each run becomes a `gen_ai.invoke_agent` transaction with spans for every DataContractGate check, every Gemma call (tagged with its attempt number) and every AISchemaGate check. That's how you find out which nights needed retries. Without a DSN it's a no-op and nothing is sent.

<!-- TODO(Alex): only keep this section, and the Sentry category below, if you connect a real Sentry project and add a screenshot of one trace. -->

## Why Does Open Innovation Matter?

For this tool, open weights aren't a nice-to-have. They're the reason it can exist.

- **The data never leaves the machine.** These are customer exports: emails, order values, sometimes things covered by a DPA. "Send last night's customer file to a hosted API" is a conversation with legal. "Run Gemma on the box that already has the file" isn't.
- **It works at 5:30 AM with no internet and no keys.** Nightly jobs fail in boring ways: an expired token, a rate limit, an outage on the provider's side. A local model on a cron job has none of those failure modes.
- **Set it up once, never pay per call.** A job that runs every night forever should cost nothing to run every night forever.
- **I can pick the model size for the hardware,** from a 270M model on a Raspberry Pi to 4B on a laptop, and swap Ollama for llama.cpp or vLLM by changing one line of config.
- **The checks are open too.** DataContractGate and AISchemaGate are MIT-licensed, so the thing deciding whether the model's answer is trustworthy is code you can read, not another black box.

A closed API would probably write slightly nicer sentences. But it would make me ship my data somewhere else every night, and that's what I was trying to stop worrying about.

## The Lazy Payoff

<!-- TODO(Alex): replace with your real numbers. Suggested framing: -->

Before: about **[N] minutes** every morning opening exports and eyeballing them, and still missing things like 60 lowercase currency codes at the bottom of a 240-row file.

After: two lines in `latest.txt`. On a clean night that's the whole job. On a bad night the report names the file, the field, the row count and a first fix, before I've opened anything. And when a new dataset shows up, its first contract is already drafted for me to review.

## Prize Categories

- **Best Use of Gemma**: Gemma (via Ollama) writes the summary and the fix suggestions, and the whole design is built around making a small open-weight model safe to trust unattended.
- **Best Use of a .tech Domain**: <!-- TODO(Alex): keep only if you actually host the demo/report page there, e.g. morningdrop.clankerspanker.tech -->
- **Best Use of Sentry Agent Tracing**: <!-- TODO(Alex): keep only with a real trace screenshot -->
