"""Any OpenAI-compatible chat endpoint, with an optional hard spend cap."""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

from openai import OpenAI

DOTENV = Path(__file__).resolve().parents[2] / ".env"


def env(name: str, default: str = "") -> str:
    """A setting from the environment, else from the project's .env (only that key is read)."""
    if val := os.getenv(name):
        return val
    if DOTENV.exists():
        for line in DOTENV.read_text().splitlines():
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip("\"'") or default
    return default


class BudgetExceeded(RuntimeError):
    pass


class LLM:
    def __init__(self, budget_usd: float):
        key = env("LLM_API_KEY")
        if not key:
            raise SystemExit("LLM_API_KEY is not set (environment or .env, see .env.example)")
        self.model = env("LLM_MODEL", "deepseek-flash")
        self.price_in = float(env("LLM_PRICE_IN", "0"))     # $ per 1M tokens; 0 = unknown, no cap
        self.price_out = float(env("LLM_PRICE_OUT", "0"))
        self.extra_body = json.loads(env("LLM_EXTRA_BODY", "{}"))
        self.client = OpenAI(api_key=key, base_url=env("LLM_BASE_URL", "https://api.deepseek.com"), timeout=180)
        self.budget = budget_usd
        self.spent = self.reserved = 0.0
        self.tokens_in = self.tokens_out = self.calls = 0
        self.lock = threading.Lock()

    def cost(self, tokens_in: int, tokens_out: int) -> float:
        return (tokens_in * self.price_in + tokens_out * self.price_out) / 1e6

    def json(self, system: str, user: str, max_tokens: int) -> dict:
        """One chat call in JSON mode. Refuses to send a call whose worst case would cross the budget."""
        worst = self.cost(len(system + user) // 2, max_tokens)   # 2 chars per token: pessimistic
        with self.lock:
            if self.spent + self.reserved + worst > self.budget:
                raise BudgetExceeded(f"spent ${self.spent:.4f} of ${self.budget:.2f}")
            self.reserved += worst
        try:
            resp = self.client.chat.completions.create(
                model=self.model, max_tokens=max_tokens, temperature=0.4,
                response_format={"type": "json_object"}, extra_body=self.extra_body or None,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
        finally:
            with self.lock:
                self.reserved -= worst
        u = resp.usage
        with self.lock:
            self.spent += self.cost(u.prompt_tokens, u.completion_tokens)
            self.tokens_in += u.prompt_tokens
            self.tokens_out += u.completion_tokens
            self.calls += 1
        return parse_json(resp.choices[0].message.content or "")

    def usage(self) -> dict:
        return {"model": self.model, "calls": self.calls, "tokens_in": self.tokens_in,
                "tokens_out": self.tokens_out, "spent_usd": round(self.spent, 4) if self.price_in else None}


def parse_json(text: str) -> dict:
    text = re.sub(r"^```(json)?|```$", "", text.strip()).strip()
    try:
        return json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        try:
            return json.loads(text[start:end + 1]) if start >= 0 else {}
        except ValueError:
            return {}
