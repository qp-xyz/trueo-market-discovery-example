# Trueo market discovery: reference script

The reference implementation for the Trueo × Fortuna market discovery proposal. It reads
stories, their supporting posts and the positions those posts take from the
[Fortuna](https://fortuna.cc) `/v2` API, which covers about 13,000 monitored X accounts. It then
drafts preliminary market candidates in Trueo's format: a question, resolution criteria, a source
and a deadline.

The drafts are inputs to Trueo's existing proposal workflow, not finished markets. Trueo reviews
and approves every candidate before anything reaches the launchpad, and decides which markets to
propose, fund and advance. The script is configurable: categories, eligibility checks, house
style and the language model are all meant to be adjusted to Trueo's conventions.

## Where it fits

```
X activity → Fortuna API → Opportunity screening → Draft markets → Trueo review → Launchpad
                           └──────────── this script ────────────┘
```

| Fortuna endpoint | Function | How the script uses it |
|---|---|---|
| `/v2/stories` | Stories and narrative information | Finds the day's developing stories, with how many accounts discuss each and which way they lean |
| `/v2/stories/{key}/calls` | Supporting posts and expressed positions | Gives the drafter the original posts on both sides of a story |
| `/v2/feed` | Additional event context for screening | Finds supporting posts for narrative stories that have no call list of their own |

A trending story is not automatically a suitable market. The script screens each candidate for
genuine uncertainty, an independently verifiable outcome, a defined timeframe and Trueo's
integrity standards. Anything it cannot settle in code is left for Trueo's review.

## Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env      # add LLM_API_KEY (and FORTUNA_API_KEY once issued)
uv sync
uv run trueo-discover
```

## Usage

```bash
uv run trueo-discover                                 # top 20 stories of the last day, up to 3 drafts each
uv run trueo-discover --stories 40 --per-story 4      # more stories, more drafts per story
uv run trueo-discover --window 7d                     # stories from the last week
uv run trueo-discover --json > drafts.json            # JSON to stdout instead of the list
```

Each run prints the drafts and writes `out/markets-<time>.json` (or `--out PATH`). Progress goes
to stderr. Other options: `--min-accounts`, `--budget`, `--no-review`, `--workers`.

```
 1. OpenAI pulls GPT-6.2 Astra or Sol by Dec 18?
    ai_tech · closes 2026-12-18 · starts YES 12% · 22 accounts, 13 calls for / 8 against
    source: OpenAI official model documentation and release notes (openai.com)
```

`accounts` is the number of distinct monitored X accounts discussing the story when it was
drafted. It measures how broad the conversation is, not how many people would trade.

## Screening and drafting

1. **Stories** (`fortuna.py`). Fortuna's news and narrative stories for the window, ranked by
   accounts × (1 + disagreement), where disagreement = 2·min(for, against) / (for + against).
   A story many accounts argue about from both sides ranks above one they agree on. Near-duplicate
   headlines are merged. Each story comes with up to 10 posts, alternating sides.
2. **Draft** (`markets.draft`). One model call per story writes up to `--per-story` candidates,
   each from a different angle: the core event with a near deadline, a later deadline or a higher
   bar, a follow-on event, and an official non-price figure. Each draft has a question, rules in
   Trueo's template, a source, a deadline and a starting split taken from a stated base rate.
3. **Checks** (`markets.problems`). Code drops a draft that settles on a traded price, has a title
   over 70 characters, closes outside 3–120 days, starts outside 5–95%, or cites no post it was
   given.
4. **Review** (`markets.review`). A second model pass reads the drafts 20 at a time and keeps,
   fixes or drops each one. It catches wordy titles, rules that leave an edge case open,
   duplicates and near-certainties.

Candidates are real-world events only: launches, filings, rulings, financings, votes, official
data releases. Nothing settles on a traded price, market cap or index level; IPO valuations are
the one exception. The prompt says so, and `PRICE_RE` enforces it.

## Before proposing a draft

The script narrows the field; it does not establish that a market is novel, valid or likely to
attract trading. Before a candidate goes to the launchpad, confirm that:

- the event is still unresolved, and the deadline leaves real uncertainty;
- the source is authoritative and will publish the outcome;
- the rules settle every edge case the question raises, such as an announcement against a
  completed launch, or a refinancing against new debt;
- Trueo does not already list an equivalent market.

## Output

```json
{
  "generated_at": "2026-11-18T06:27:11+00:00",
  "usage": {"model": "deepseek-flash", "calls": 7, "tokens_in": 11372, "tokens_out": 5698, "spent_usd": 0.0102},
  "stopped_early": null,
  "markets": [
    {
      "question": "OpenAI pulls GPT-6.2 Astra or Sol by Dec 18?",
      "category": "ai_tech",
      "rules": "Resolves Yes if OpenAI withdraws, disables, or replaces …",
      "source": "OpenAI official model documentation and release notes (openai.com)",
      "close_time": "2026-12-18T23:59:00Z",
      "prior_yes": 0.12,
      "prior_basis": "Major labs rarely pull a flagship within a month of launch …",
      "yes_case": "…",
      "no_case": "…",
      "evidence_urls": ["https://x.com/…"],
      "story": {"headline": "…", "accounts": 22, "calls_for": 13, "calls_against": 8, "score": 41.0}
    }
  ]
}
```

| Draft field | Trueo launchpad field |
|---|---|
| `question` | Market question |
| `rules` | Additional info |
| `source` | Resolution source |
| `close_time` | End of trading |
| `prior_yes` | Starting split |

`prior_basis`, `yes_case`, `no_case` and `evidence_urls` are there for the reviewer. They hold
the base rate behind the split, each side as people argue it, and the posts the draft came from.

## Model provider

Any OpenAI-compatible chat endpoint works, as long as the model supports JSON mode
(`response_format: json_object`). Settings come from the environment first, then `.env`:

| Setting | Default | |
|---|---|---|
| `LLM_API_KEY` | (required) | Provider key |
| `LLM_BASE_URL` | `https://api.deepseek.com` | Endpoint |
| `LLM_MODEL` | `deepseek-flash` | Model name |
| `LLM_PRICE_IN`, `LLM_PRICE_OUT` | unset | $ per 1M tokens. Set both to meter spend and enforce `--budget` |
| `LLM_EXTRA_BODY` | `{}` | JSON merged into each request, for provider-specific options |
| `FORTUNA_API_KEY` | unset | Fortuna API key, sent as `Authorization: Bearer …` |
| `FORTUNA_API_BASE` | `https://social-api.fortuna.cc/v2` | |

```bash
# OpenAI
LLM_BASE_URL=https://api.openai.com/v1  LLM_MODEL=gpt-5-mini
# OpenRouter
LLM_BASE_URL=https://openrouter.ai/api/v1  LLM_MODEL=anthropic/claude-sonnet-5.5
# Local (Ollama)
LLM_BASE_URL=http://localhost:11434/v1  LLM_API_KEY=ollama  LLM_MODEL=qwen3
```

`--budget` (default $1.00) is a hard cap: a request whose worst case would cross it is not sent,
and the run saves what it has. A 20-story run on DeepSeek costs about $0.03.

## Configuring it for Trueo

- **Categories, eligibility and deadlines.** `CATEGORIES`, `MIN_DAYS`, `MAX_DAYS`, `TITLE_MAX`
  and `PRICE_RE` at the top of `markets.py`.
- **House style, rules template and integrity standards.** `STYLE`, `DRAFT` and `REVIEW` in
  `markets.py`.
- **Which stories are considered.** `Fortuna.stories` in `fortuna.py`, and `--min-accounts` on
  the command line.

The script runs on demand and fits Trueo's current proposal workflow without platform changes.
Scheduled feeds, monitoring or deeper drafting integrations would be scoped separately.

## Limits

- `prior_yes` is the model's base-rate estimate and is not calibrated.
- Review misses some near-duplicates and some contradictions inside rules, so every draft needs
  a human read.
- Spend is metered at the cache-miss rate for every input token, so it is an overestimate.
