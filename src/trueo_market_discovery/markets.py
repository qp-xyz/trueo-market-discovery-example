"""Draft markets from a story, check them in code, and review them with a second pass."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ValidationError

from .llm import LLM

MIN_DAYS, MAX_DAYS = 3, 120
TITLE_MAX = 70
CATEGORIES = ("crypto", "ai_tech", "policy", "macro", "business", "politics", "culture", "other")


class Market(BaseModel):
    question: str
    category: Literal[CATEGORIES]
    rules: str
    source: str
    close_time: str
    prior_yes: float
    prior_basis: str
    yes_case: str
    no_case: str
    evidence_urls: list[str]


STYLE = """House style (from Trueo and Polymarket listings):
- Titles are short headlines, 30-55 characters, never over 70: subject + outcome + deadline.
  No parentheses, no lists, no timezone. Examples: "OpenAI IPO in 2026?",
  "Howard Lutnick out as Commerce Secretary by Dec 31?", "Lighter November revenue exceeds $5M?",
  "ZelleUSD live on Solana mainnet by Feb 28?"
- Rules follow this template; everything the title leaves out goes here:
    [Optional one-sentence context, only if the market depends on a recent event.]
    Resolves Yes if <who> <does what> between market creation and <Month D, YYYY>, 11:59 PM UTC. Otherwise No.
    Counts: <qualifying cases>.
    Does not count: <rumours, intent, testnets, third-party reports, partial events>.
- Source is a separate field naming the official site, filing, regulator or dataset.

Trueo integrity standards (break one and the market resolves Cancelled): binary only; a clear
event and a concrete deadline; title, rules and source agree; only events after market creation
count; no subjective criteria; a credible public source exists; nothing about private individuals
or that rewards harm."""

DRAFT = """You write prediction markets for Trueo, an onchain prediction-market venue.
Today is {today}. Markets are proposed today.

You get one story that accounts on X are arguing about, with posts from both sides. Write up to
{k} binary markets about real-world outcomes in it, each from a different angle:
1. the core event with a near deadline;
2. the same event with a later deadline or a higher bar (official, completed, bigger);
3. a follow-on event (a regulator responds, a rival reacts, a vote follows);
4. an official non-price figure if one applies (protocol revenue on DefiLlama, users, a data print).
Skip an angle rather than pad. No near-certainties, nothing already resolved, no opinions.
If the story has no real-world market in it, return an empty list.

Never write a market that settles on a traded price, market cap, FDV, index level or
"token reaches $X". IPO valuations are allowed.
Deadline: {min_days} to {max_days} days from today.
prior_yes: the starting YES probability, from a base rate you state in prior_basis.
How the posts lean is not a probability.
evidence_urls: only URLs from the posts you are given.

{style}

Reply with JSON only, in this shape:
{{"markets": [{{"question": "...", "category": "{categories}", "rules": "...", "source": "...",
"close_time": "YYYY-MM-DDT23:59:00Z", "prior_yes": 0.3, "prior_basis": "...",
"yes_case": "the yes side as people argue it", "no_case": "the no side", "evidence_urls": ["..."]}}]}}"""

REVIEW = """You review prediction-market drafts for Trueo. Today is {today}.

{style}

For each draft decide:
- "drop" if it settles on a price, breaks an integrity standard, cannot be settled objectively,
  is already resolved or near-certain, is an opinion, or repeats another draft (keep the better one);
- "fix" if the title is wordier than house style, or the rules leave an edge case open, or title,
  rules and source disagree: return the corrected fields;
- "keep" if a trader could read it once and know exactly how it settles.

Reply with JSON only:
{{"reviews": [{{"index": 0, "verdict": "keep|fix|drop", "reason": "...",
"question": null, "rules": null, "close_time": null}}]}}
Leave question, rules and close_time null unless you are fixing them."""

PRICE_RE = re.compile(
    r"\b(close[sd]?|trade[sd]?|settle[sd]?|finish(es|ed)?)\s+(at\s+or\s+)?(above|below|over|under)\b"
    r"|\b(dip|drop|fall|rise|hit|reach|touch|pump|crash)(e?s|ed|ing)?\s+(to|above|below)?\s*\$"
    r"|\bmarket\s*cap|\bmcap\b|\bFDV\b|\ball[- ]time\s+high\b|\bATH\b"
    r"|\bprice\s+(of|above|below|at|target|reach)",
    re.IGNORECASE)


def draft(llm: LLM, story: dict, posts: list[dict], k: int, now: datetime) -> list[Market]:
    system = DRAFT.format(today=now.strftime("%A %d %B %Y"), k=k, min_days=MIN_DAYS, max_days=MAX_DAYS,
                          style=STYLE, categories="|".join(CATEGORIES))
    lines = [f"STORY: {story['headline']}",
             f"ACCOUNTS: {story.get('accounts')}; {story.get('long') or 0} for, {story.get('short') or 0} against"]
    if story.get("summary"):
        lines.append(f"SUMMARY: {story['summary']}")
    lines.append("POSTS:")
    lines += [f"- [{p['side'] or 'neutral'}] @{p['account']}: {p['text']} | {p['url']}" for p in posts]
    out = []
    for m in llm.json(system, "\n".join(lines), max_tokens=900 * k + 400).get("markets") or []:
        try:
            out.append(Market.model_validate(m))
        except ValidationError:
            pass
    return out[:k]


def problems(m: Market, seen_urls: set[str], now: datetime) -> list[str]:
    """Code checks. Any problem drops the draft."""
    found = []
    if PRICE_RE.search(m.question) or PRICE_RE.search(m.rules):
        found.append("settles on a price")
    if len(m.question) > TITLE_MAX:
        found.append(f"title is {len(m.question)} characters")
    try:
        days = (datetime.fromisoformat(m.close_time.replace("Z", "+00:00")) - now).total_seconds() / 86400
        if not MIN_DAYS <= days <= MAX_DAYS:
            found.append(f"closes in {days:.0f} days")
    except ValueError:
        found.append(f"bad close_time {m.close_time!r}")
    if not 0.05 <= m.prior_yes <= 0.95:
        found.append(f"prior_yes {m.prior_yes} is too lopsided")
    m.evidence_urls = [u for u in m.evidence_urls if u in seen_urls]
    if not m.evidence_urls:
        found.append("no evidence from the posts")
    return found


def review(llm: LLM, markets: list[Market], now: datetime) -> list[dict]:
    """One review per draft, in the same order: {"verdict", "reason", optional fixes}."""
    system = REVIEW.format(today=now.strftime("%A %d %B %Y"), style=STYLE)
    drafts = "\n\n".join(f"### Draft {i}\n" + json.dumps(m.model_dump(exclude={"evidence_urls"}), indent=1)
                         for i, m in enumerate(markets))
    by_index = {}
    for r in llm.json(system, drafts, max_tokens=300 * len(markets) + 400).get("reviews") or []:
        if isinstance(r, dict) and isinstance(r.get("index"), int):
            by_index[r["index"]] = r
    return [by_index.get(i, {"verdict": "unreviewed"}) for i in range(len(markets))]
