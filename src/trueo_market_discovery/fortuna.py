"""Stories and posts from the public Fortuna /v2 API."""

from __future__ import annotations

import re

import httpx

from .llm import env


class Fortuna:
    def __init__(self):
        headers = {"User-Agent": "trueo-market-discovery-example"}
        if key := env("FORTUNA_API_KEY"):
            headers["Authorization"] = f"Bearer {key}"
        self.http = httpx.Client(base_url=env("FORTUNA_API_BASE", "https://social-api.fortuna.cc/v2"),
                                 headers=headers, timeout=45)

    def get(self, path: str, **params) -> dict:
        r = self.http.get(path, params={k: v for k, v in params.items() if v not in (None, "")})
        r.raise_for_status()
        return r.json()

    def stories(self, window: str, limit: int, min_accounts: int) -> list[dict]:
        """The most-discussed stories from Fortuna's news and narrative boards, best first."""
        rows = []
        for board in ({"news": "true"}, {"kinds": "news_event,thesis"}):
            rows += self.get("/stories", window=window, page_size=50, **board).get("data") or []
        kept: list[dict] = []
        for r in sorted(rows, key=score, reverse=True):
            if (r.get("accounts") or 0) >= min_accounts and not any(_same(r, k) for k in kept):
                kept.append(r)
        return kept[:limit]

    def posts(self, story: dict, window: str, n: int = 10) -> list[dict]:
        """Posts behind a story: its own calls when it has them, else a feed search on the headline."""
        if story.get("story_key"):
            rows = self.get(f"/stories/{story['story_key']}/calls").get("members") or []
            posts = [{"account": (m.get("account") or {}).get("handle"), "side": m.get("direction"),
                      "text": m.get("prediction"), "url": m.get("source_url")} for m in rows]
        else:
            rows = self.get("/feed", q=story["headline"], since=window, page_size=n).get("data") or []
            posts = [{"account": t.get("handle"), "side": (t.get("match") or {}).get("direction"),
                      "text": t.get("prediction"), "url": t.get("source_url")} for t in (r["thesis"] for r in rows)]
        # alternate sides so both make it into the sample
        longs = [p for p in posts if p["side"] == "long"]
        others = [p for p in posts if p["side"] != "long"]
        mixed = [p for pair in zip(longs, others) for p in pair] + longs[len(others):] + others[len(longs):]
        return mixed[:n]


def score(story: dict) -> float:
    """Accounts discussing the story x (1 + disagreement); disagreement is 1 at an even split."""
    yes, no = story.get("long") or 0, story.get("short") or 0
    disagreement = 2 * min(yes, no) / (yes + no) if yes + no else 0.0
    return round((story.get("accounts") or 0) * (1 + disagreement), 1)


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9$]+", s.lower()) if len(w) > 2}


def _same(a: dict, b: dict) -> bool:
    wa, wb = _words(a["headline"]), _words(b["headline"])
    return len(wa & wb) / max(1, min(len(wa), len(wb))) >= 0.6   # share most of the shorter headline
