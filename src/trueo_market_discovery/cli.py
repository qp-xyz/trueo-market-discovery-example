"""Find prediction markets in what X is arguing about.

    uv run trueo-discover                       # top 20 stories of the last day, up to 3 markets each
    uv run trueo-discover --stories 40 --per-story 4 --window 7d
    uv run trueo-discover --json > markets.json

Prints the markets and writes them, with the stories behind them, to out/markets-<time>.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from .fortuna import Fortuna, score
from .llm import LLM, BudgetExceeded
from .markets import draft, problems, review

OUT = Path(__file__).resolve().parents[2] / "out"


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stories", type=int, default=20, help="how many stories to draft from")
    ap.add_argument("--per-story", type=int, default=3, help="most markets per story")
    ap.add_argument("--window", default="1d", help="story window: 1d or 7d")
    ap.add_argument("--min-accounts", type=int, default=5)
    ap.add_argument("--budget", type=float, default=1.0, help="hard spend cap in $ (needs LLM_PRICE_IN/OUT)")
    ap.add_argument("--no-review", action="store_true", help="skip the second review pass")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--json", action="store_true", help="print the JSON to stdout instead of the list")
    ap.add_argument("--out", type=Path, help="JSON path (default out/markets-<time>.json)")
    a = ap.parse_args(argv)

    now = datetime.now(timezone.utc)
    api, llm = Fortuna(), LLM(a.budget)
    if not llm.price_in:
        log("LLM_PRICE_IN/LLM_PRICE_OUT not set: spend is not metered and --budget is not enforced")
        llm.budget = float("inf")

    stories = api.stories(a.window, a.stories, a.min_accounts)
    log(f"{len(stories)} stories · drafting with {llm.model}")

    def one(story: dict):
        posts = api.posts(story, a.window)
        try:
            drafts = draft(llm, story, posts, a.per_story, now)
        except BudgetExceeded as e:
            return story, [], str(e)
        seen = {p["url"] for p in posts if p["url"]}
        kept = [m for m in drafts if not problems(m, seen, now)]
        log(f"  {len(kept)}/{len(drafts)} · {story['headline'][:70]}")
        return story, kept, None

    markets, stopped = [], None
    with ThreadPoolExecutor(a.workers) as pool:
        for story, kept, err in pool.map(one, stories):
            stopped = stopped or err
            markets += [(story, m) for m in kept]

    if not a.no_review and markets:
        reviewed = []
        for i in range(0, len(markets), 20):
            chunk = markets[i:i + 20]
            try:
                verdicts = review(llm, [m for _, m in chunk], now)
            except BudgetExceeded as e:
                stopped = stopped or str(e)
                verdicts = [{"verdict": "unreviewed"}] * len(chunk)
            for (story, m), v in zip(chunk, verdicts):
                if v.get("verdict") == "drop":
                    continue
                for field in ("question", "rules", "close_time"):
                    if v.get(field):
                        setattr(m, field, v[field])
                if not problems(m, set(m.evidence_urls), now):
                    reviewed.append((story, m))
        log(f"review kept {len(reviewed)} of {len(markets)}")
        markets = reviewed

    markets.sort(key=lambda sm: score(sm[0]), reverse=True)
    result = {
        "generated_at": now.isoformat(timespec="seconds"),
        "usage": llm.usage(),
        "stopped_early": stopped,
        "markets": [{**m.model_dump(), "story": {"headline": s["headline"], "accounts": s.get("accounts"),
                                                  "calls_for": s.get("long") or 0, "calls_against": s.get("short") or 0,
                                                  "score": score(s)}}
                    for s, m in markets],
    }
    text = json.dumps(result, indent=2, ensure_ascii=False)
    path = a.out or OUT / f"markets-{now:%Y%m%d-%H%M%S}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)

    if a.json:
        print(text)
    else:
        for i, row in enumerate(result["markets"], 1):
            s = row["story"]
            print(f"\n{i:>2}. {row['question']}\n"
                  f"    {row['category']} · closes {row['close_time'][:10]} · starts YES {row['prior_yes']:.0%} · "
                  f"{s['accounts']} accounts, {s['calls_for']} calls for / {s['calls_against']} against\n"
                  f"    source: {row['source']}")
    u = result["usage"]
    spent = f"${u['spent_usd']:.4f}" if u["spent_usd"] is not None else "spend not metered"
    log(f"\n{len(markets)} markets · {u['calls']} calls · {spent} · {path}"
        + (f"\nstopped early: {stopped}" if stopped else ""))


if __name__ == "__main__":
    main()
