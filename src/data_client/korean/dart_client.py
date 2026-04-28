"""DART OpenDartReader thin wrapper — shared by fundamentals/news/RAG sources.

Wraps the third-party ``OpenDartReader`` so the rest of the data layer never
imports it directly. Adds three concerns the bare client lacks:

1. **Singleton lifecycle.** OpenDartReader downloads ``corp_code.xml`` (~5 MB)
   on construction and caches it in process memory. Re-instantiating per
   request would burn a few seconds of every overview load, so the client is
   built once on first use and reused. Lock + double-checked init prevents
   concurrent ``/overview`` requests from each paying that cost.

2. **Redis-backed result cache.** ``finstate_all`` responses are immutable
   once a filing is published — Q3 2024 will not retroactively change. We
   cache the **extracted** values (six floats per row) rather than the raw
   DataFrame so the cached payload is small and the cache key never has to
   embed schema details. TTL is long for prior years (365 d) and short for
   the current year (1 d) since new quarters file mid-year.

3. **Retry + exponential backoff.** DART's free-tier API hiccups occasionally
   on transient network or rate-limit conditions. Single failures used to
   leave a quarter blank in the response; three retries with 1 s/2 s/4 s
   backoff masks all the cases we have actually observed in production.

The fundamentals source consumes only :func:`fetch_finstate_extracted` — the
singleton and accounts constants are intentionally not part of the public
surface so future callers stay decoupled from OpenDartReader.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from src.utils.cache.redis_cache import get_cache_client

logger = logging.getLogger(__name__)

_KST = ZoneInfo("Asia/Seoul")

# ---------------------------------------------------------------------------
# Account matching (XBRL account_id primary, account_nm substring fallback)
# ---------------------------------------------------------------------------

_REVENUE_IDS = (
    "ifrs-full_Revenue",
    "ifrs-full_RevenueFromContractsWithCustomers",
)
_REVENUE_KEYWORDS = ("매출액", "수익(매출액)", "영업수익")

_OPERATING_INCOME_IDS = ("dart_OperatingIncomeLoss",)
_OPERATING_INCOME_KEYWORDS = ("영업이익",)

_NET_INCOME_IDS = ("ifrs-full_ProfitLoss",)
_NET_INCOME_KEYWORDS = ("당기순이익", "분기순이익", "반기순이익")

_OPERATING_CF_IDS = ("ifrs-full_CashFlowsFromUsedInOperatingActivities",)
_OPERATING_CF_KEYWORDS = ("영업활동현금흐름", "영업활동으로 인한 현금흐름")

_INVESTING_CF_IDS = ("ifrs-full_CashFlowsFromUsedInInvestingActivities",)
_INVESTING_CF_KEYWORDS = ("투자활동현금흐름", "투자활동으로 인한 현금흐름")

_FINANCING_CF_IDS = ("ifrs-full_CashFlowsFromUsedInFinancingActivities",)
_FINANCING_CF_KEYWORDS = ("재무활동현금흐름", "재무활동으로 인한 현금흐름")

# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

# Lock + double-checked init: concurrent /overview requests in to_thread can
# all hit _get_dart_client in parallel. Without the lock, threads racing the
# init could see ``init_attempted=True`` while ``singleton`` was still being
# constructed by another thread — and receive ``None`` instead of waiting.
_dart_singleton: Any | None = None
_dart_init_attempted: bool = False
_dart_init_lock = threading.Lock()


def _get_dart_client() -> Any | None:
    """Lazy singleton OpenDartReader. ``None`` if API key missing or import fails."""
    global _dart_singleton, _dart_init_attempted
    if _dart_init_attempted:
        return _dart_singleton
    with _dart_init_lock:
        if _dart_init_attempted:
            return _dart_singleton
        api_key = os.getenv("DART_API_KEY")
        if not api_key:
            logger.info("dart.no_api_key")
            _dart_singleton = None
        else:
            try:
                import OpenDartReader  # noqa: N813 — package name is camelCase
                _dart_singleton = OpenDartReader(api_key)
            except Exception:
                logger.warning("dart.client_init.failed", exc_info=True)
                _dart_singleton = None
        _dart_init_attempted = True
    return _dart_singleton


def reset_singleton_for_test() -> None:
    """Test-only: clear the cached client so the next call re-inits."""
    global _dart_singleton, _dart_init_attempted
    with _dart_init_lock:
        _dart_singleton = None
        _dart_init_attempted = False


# ---------------------------------------------------------------------------
# Value extraction
# ---------------------------------------------------------------------------


def _safe_float(value: Any) -> float | None:
    """Coerce to float. None for NaN / None / non-numeric.

    DART finstate_all 의 ``thstrm_amount`` 는 천단위 콤마 string
    ("79,140,503,000,000") 이므로 strip 후 파싱.
    """
    if value is None:
        return None
    if isinstance(value, str):
        value = value.replace(",", "").strip()
        if not value or value == "-":
            return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def _row_cumulative_amount(row: pd.Series) -> float | None:
    """Pick the "cumulative-since-year-start" amount, normalizing IS/CF schema.

    * IS (분기보고서): ``thstrm_amount`` = 단일 분기 (3M),
      ``thstrm_add_amount`` = 누적. → add_amount 우선.
    * IS (Q1 분기보고서): ``thstrm_add_amount`` 빈 값 → amount 가 곧 누적.
    * CF / BS: ``thstrm_add_amount`` NaN → amount 사용.
    """
    add_val = _safe_float(row.get("thstrm_add_amount"))
    if add_val is not None:
        return add_val
    return _safe_float(row.get("thstrm_amount"))


def _extract_dart_amount(
    df: pd.DataFrame,
    account_ids: tuple[str, ...] = (),
    account_keywords: tuple[str, ...] = (),
    sj_divs: tuple[str, ...] = ("IS", "CIS", "CF"),
) -> float | None:
    """First matching row's cumulative amount, or ``None``.

    Match priority: ``account_id`` exact (XBRL/IFRS standard, robust
    cross-issuer) → ``account_nm`` substring (K-GAAP fallback). Schema
    changes / missing columns degrade to ``None`` rather than raise — direct
    indexing would propagate KeyError into a 500 from /overview.
    """
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return None
    if "thstrm_amount" not in df.columns:
        return None

    candidates = df[df["sj_div"].isin(sj_divs)] if "sj_div" in df.columns else df
    if "fs_div" in candidates.columns:
        cfs = candidates[candidates["fs_div"] == "CFS"]
        if not cfs.empty:
            candidates = cfs

    if account_ids and "account_id" in candidates.columns:
        for aid in account_ids:
            matches = candidates[candidates["account_id"] == aid]
            if not matches.empty:
                return _row_cumulative_amount(matches.iloc[0])

    if account_keywords and "account_nm" in candidates.columns:
        for kw in account_keywords:
            for _, row in candidates.iterrows():
                if kw in str(row.get("account_nm", "")):
                    return _row_cumulative_amount(row)
    return None


def _extract_all(df: pd.DataFrame) -> dict[str, float | None]:
    """Extract the six fundamentals/cashflow values from one finstate_all df."""
    return {
        "revenue": _extract_dart_amount(df, _REVENUE_IDS, _REVENUE_KEYWORDS, sj_divs=("IS", "CIS")),
        "operatingIncome": _extract_dart_amount(df, _OPERATING_INCOME_IDS, _OPERATING_INCOME_KEYWORDS, sj_divs=("IS", "CIS")),
        "netIncome": _extract_dart_amount(df, _NET_INCOME_IDS, _NET_INCOME_KEYWORDS, sj_divs=("IS", "CIS")),
        "operatingCashFlow": _extract_dart_amount(df, _OPERATING_CF_IDS, _OPERATING_CF_KEYWORDS, sj_divs=("CF",)),
        "investingCashFlow": _extract_dart_amount(df, _INVESTING_CF_IDS, _INVESTING_CF_KEYWORDS, sj_divs=("CF",)),
        "financingCashFlow": _extract_dart_amount(df, _FINANCING_CF_IDS, _FINANCING_CF_KEYWORDS, sj_divs=("CF",)),
    }


# ---------------------------------------------------------------------------
# Cache + retry layer
# ---------------------------------------------------------------------------

# Past years' filings never change once published — cache aggressively.
_TTL_PAST_YEAR_S = 365 * 24 * 3600  # 1 year
# Current year's filings can land mid-year — re-check daily.
_TTL_CURRENT_YEAR_S = 24 * 3600
# Negative cache windows. Keep current-year shorter so a missing Q3 that gets
# filed shows up in the next overview load instead of waiting a full day.
_NEG_TTL_PAST_YEAR_S = 7 * 24 * 3600
_NEG_TTL_CURRENT_YEAR_S = 60 * 60  # 1 hour

_RETRY_BACKOFF_S = (1.0, 2.0, 4.0)


def _ttl_for(year: int, has_data: bool) -> int:
    current_year = datetime.now(_KST).year
    is_past = year < current_year
    if has_data:
        return _TTL_PAST_YEAR_S if is_past else _TTL_CURRENT_YEAR_S
    return _NEG_TTL_PAST_YEAR_S if is_past else _NEG_TTL_CURRENT_YEAR_S


def _cache_key(stock_code: str, year: int, reprt_code: str, fs_div: str) -> str:
    return f"dart:finstate:{stock_code}:{year}:{reprt_code}:{fs_div}"


def _call_finstate_all_with_retry(
    dart: Any, stock_code: str, year: int, reprt_code: str, fs_div: str,
) -> pd.DataFrame | None:
    """Synchronous DART call with exponential backoff. Off-loaded via to_thread.

    Returns the raw DataFrame on success, ``None`` if all retries fail. Empty
    DataFrame (no row matches the filing) is treated as success — distinct
    from "transient error" so we cache it as a negative result.
    """
    last_exc: Exception | None = None
    for attempt, delay in enumerate((0.0, *_RETRY_BACKOFF_S)):
        if delay:
            # The function runs in a worker thread (to_thread); time.sleep is
            # the right primitive here, not asyncio.sleep.
            time.sleep(delay)
        try:
            return dart.finstate_all(stock_code, year, reprt_code=reprt_code, fs_div=fs_div)
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "dart.finstate_all.retry | corp=%s year=%s reprt=%s attempt=%s err=%s",
                stock_code, year, reprt_code, attempt + 1, exc,
            )
    logger.warning(
        "dart.finstate_all.exhausted | corp=%s year=%s reprt=%s last_err=%s",
        stock_code, year, reprt_code, last_exc,
    )
    return None


_EMPTY_EXTRACT: dict[str, float | None] = {
    "revenue": None,
    "operatingIncome": None,
    "netIncome": None,
    "operatingCashFlow": None,
    "investingCashFlow": None,
    "financingCashFlow": None,
}


async def fetch_finstate_extracted(
    stock_code: str,
    year: int,
    reprt_code: str,
    fs_div: str = "CFS",
) -> dict[str, float | None] | None:
    """Return extracted DART values for one (corp, year, reprt) — cached.

    Cache layer (Redis, JSON):

    * HIT  → return the six-key dict directly, no DART hit.
    * MISS → call ``finstate_all`` (with retry), extract, cache, return.

    Returns ``None`` only when the DART client is not configured
    (``DART_API_KEY`` missing). Genuine "no data" answers from DART are
    represented as a dict whose values are all ``None`` — distinguishing
    "DART says nothing here" (cacheable) from "we never asked" (not).
    """
    dart = _get_dart_client()
    if dart is None:
        return None

    cache = get_cache_client()
    key = _cache_key(stock_code, year, reprt_code, fs_div)
    cached = await cache.get(key)
    if cached is not None and isinstance(cached, dict):
        return cached

    df = await asyncio.to_thread(
        _call_finstate_all_with_retry, dart, stock_code, year, reprt_code, fs_div,
    )
    if df is None:
        # All retries failed — do NOT cache so the next call retries.
        return _EMPTY_EXTRACT.copy()

    extracted = _extract_all(df) if not df.empty else _EMPTY_EXTRACT.copy()
    has_data = any(v is not None for v in extracted.values())
    await cache.set(key, extracted, ttl=_ttl_for(year, has_data))
    return extracted
