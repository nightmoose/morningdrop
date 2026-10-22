# MorningDrop 🦥

**Your data drops get checked overnight. You get two lines in the morning.**

MorningDrop is a small local agent for anyone who wakes up to a folder of
nightly exports: vendor CSVs, storefront dumps, CRM extracts. While you sleep it

1. finds the files that are new since last night,
2. checks every row against a [DataContractGate](https://github.com/nightmoose/contractgate) contract, fully offline,
3. asks a **local Gemma model** (via [Ollama](https://ollama.com)) for a two-line summary and suggested fixes,
4. checks Gemma's answer with **AISchemaGate** before it reaches you, and
5. writes `reports/YYYY-MM-DD.md`, `.json` and a two-line `latest.txt`.

No rows leave your machine. No API keys. No per-call bill.

```text
$ morningdrop run
The orders_2026-10-24.csv file failed for 63 rows due to currency and quantity issues.
Prioritize fixing the currency and quantity validation rules in the orders contract.
report: reports/2026-10-25.md  (21.7s, summary by model)
```

Full sample: [`samples/expected-report.md`](samples/expected-report.md) (a real `gemma3:4b` run on a CPU-only box).

## Why the model's output gets a contract too

A small local model is great at turning `60 × enum_violation on currency` into
a sentence a tired human can act on. It is also happy to invent a file
called `payments.csv`, say a red night is green, or repeat your prompt back.
So MorningDrop treats Gemma the way it treats a vendor: **its output has a
contract**.

| Check | Library | What it catches |
|---|---|---|
| Shape: `status` enum, `line1`/`line2` length, every fix has `file`, `field`, `kind`, `suggestion` | AISchemaGate ([`summary_contract.yaml`](morningdrop/summary_contract.yaml)) | prose instead of JSON, missing keys, a 400-character "line" |
| Status equals the worst file's status | MorningDrop grounding | "all green!" on a red night |
| line1/line2 name the worst file | MorningDrop grounding | leading with the wrong problem |
| Every number in the text already appears in the facts | MorningDrop grounding | "41 of 300 rows failed" when nothing like that happened |
| Fixes point at real files and real failing fields | MorningDrop grounding | invented files and columns |

When a check fails, the violations go back to Gemma as a repair prompt (up to
`max_retries`). If only some *fixes* are bad, those fixes are quarantined and
the rest are kept, the same way DataContractGate quarantines bad rows. If the
model never gets it right, or Ollama isn't running, a plain template writes
the report from the same facts. **You always get a correct morning report.**
And on a quiet night where everything passed, the model isn't called at all.

What we saw while building it (sample drops, CPU only):

| Model | Result |
|---|---|
| `gemma3:4b` | Accepted on the first attempt, about 20–35 s |
| `gemma3:1b` | Caught by grounding: invented failures in a file with no contract, or numbers copied from the prompt. Fallback report |
| `gemma3:270m` | Caught by grounding: echoed the instructions as its summary. Fallback report |

## Quick start

```bash
git clone https://github.com/nightmoose/morningdrop && cd morningdrop
python -m venv .venv && . .venv/bin/activate
pip install -e .
ollama pull gemma3:4b         # or gemma3:1b on a small laptop
morningdrop run               # uses morningdrop.yaml -> samples/drops
cat reports/latest.txt
```

No Ollama yet? `MORNINGDROP_LLM_BACKEND=mock morningdrop run` runs the whole
pipeline with a deterministic stand-in model.

Point it at your own folder by editing [`morningdrop.yaml`](morningdrop.yaml):

```yaml
watch: /data/nightly          # where the exports land
contracts: contracts          # one DataContractGate YAML per dataset
llm:
  backend: ollama             # ollama | openai | mock
  base_url: http://localhost:11434
  model: gemma3:4b
```

Any OpenAI-compatible local server works too (llama.cpp `llama-server`,
vLLM, LM Studio): set `backend: openai` and `base_url`. Environment overrides:
`MORNINGDROP_LLM_BACKEND`, `MORNINGDROP_LLM_URL`, `MORNINGDROP_LLM_MODEL`.

Then schedule it: [`examples/crontab.txt`](examples/crontab.txt), macOS
[`launchd`](examples/com.morningdrop.plist), or a systemd
[`watch` service](examples/morningdrop.service).

## How files map to contracts

`orders_2026-10-24.csv` → dataset `orders` → `contracts/orders.yaml`. The
date suffix is stripped. CSV, JSON (array or `{"records": [...]}`) and JSONL
are supported. CSV cells are coerced to the contract's declared types; a value
that won't coerce stays a string, so DataContractGate reports an honest
`type_mismatch`.

**New dataset, no contract yet?** MorningDrop drafts one from the file
(types, required fields, ranges, small enums, date patterns), compiles it with
DataContractGate to prove it's valid, and saves it to `contracts/_proposed/`.
It is never enforced until you move it into `contracts/`. You can also run
`morningdrop infer path/to/file.csv`.

Re-sent files are recognized by content hash, so the same file twice doesn't
produce a second report.

## What the model sees

Only counts, field names, violation messages and the contract rule that was
broken, never whole rows. Passing rows are not sent to the model at all.
Violation messages can quote a bad value (`value "usa" does not match`), which is the
point, and it all stays on your machine anyway.

## Optional: Sentry Agent Tracing

```bash
pip install -e '.[sentry]'
export SENTRY_DSN=...        # your own project
morningdrop run
```

Each run is a `gen_ai.invoke_agent` transaction with spans for every
DataContractGate check, every Gemma call (with attempt number) and every
AISchemaGate check, so you can see which nights needed retries. Without
`SENTRY_DSN` this is a no-op and nothing is sent anywhere.

## Development

```bash
pip install -e '.[dev]'
pytest -q                                                    # offline, uses scripted fake models
MORNINGDROP_LIVE=1 MORNINGDROP_LLM_MODEL=gemma3:4b pytest -s tests/test_live_gemma.py   # real Gemma
```

CI runs the offline suite on Python 3.9 and 3.12, plus a best-effort job
that installs Ollama and runs `gemma3:1b` end to end on a CPU runner.

## Built on

- [Gemma](https://ai.google.dev/gemma) open-weight models, served by [Ollama](https://ollama.com)
- [DataContractGate](https://github.com/nightmoose/contractgate) (MIT): the `contractgate` Python package's offline validator
- AISchemaGate (MIT): the `aischemagate` package, same engine, pointed at LLM output

## License

MIT
