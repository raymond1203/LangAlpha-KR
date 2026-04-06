"""Tavily search tool for LangChain integration."""

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

from langchain_core.tools import tool

from src.tools.search_services.tavily.tavily_search_api_wrapper import (
    TavilySearchWrapper,
)

logger = logging.getLogger(__name__)

# Module-level configuration
_api_wrapper: Optional[TavilySearchWrapper] = None
_max_results: int = 10
_default_time_range: Optional[str] = None
_verbose: bool = True
_search_depth: str = "advanced"
_include_domains: Optional[List[str]] = None
_exclude_domains: Optional[List[str]] = None
_include_answer: bool = False
_include_favicon: bool = True
_country: Optional[str] = None


def _get_api_wrapper() -> TavilySearchWrapper:
    """Get or create the API wrapper instance."""
    global _api_wrapper
    if _api_wrapper is None:
        _api_wrapper = TavilySearchWrapper(country=_country)
    return _api_wrapper


def configure(
    max_results: int = 10,
    default_time_range: Optional[str] = None,
    verbose: bool = True,
    search_depth: str = "advanced",
    include_domains: Optional[List[str]] = None,
    exclude_domains: Optional[List[str]] = None,
    include_answer: bool = False,
    include_favicon: bool = True,
    country: Optional[str] = None,
) -> None:
    """Configure the Tavily search tool settings.

    Args:
        max_results: Maximum number of search results to return.
        default_time_range: Default time range filter (d/w/m/y or day/week/month/year).
            Used as fallback if LLM doesn't specify time_range in query.
        verbose: Control verbosity of search results.
            True (default): Include images (raw_content always disabled).
            False: Text-only results without images (lightweight for planning).
        search_depth: Search depth - "basic" or "advanced" (default).
        include_domains: List of domains to include in search.
        exclude_domains: List of domains to exclude from search.
        include_answer: Whether to include Tavily's answer in results.
        include_favicon: Whether to include favicon URLs in artifact.
        country: Country for localized results (lowercase, e.g., "united states").
            Only valid for topic="general". Examples: "china", "japan", "germany".
    """
    global _api_wrapper, _max_results, _default_time_range, _verbose, _search_depth
    global _include_domains, _exclude_domains, _include_answer, _include_favicon
    global _country

    _max_results = max_results
    _default_time_range = default_time_range
    _verbose = verbose
    _search_depth = search_depth
    _include_domains = include_domains or []
    _exclude_domains = exclude_domains or []
    _include_answer = include_answer
    _include_favicon = include_favicon
    _country = country
    _api_wrapper = None  # Reset wrapper to pick up new country config


def _validate_date_format(date_str: Optional[str]) -> Optional[str]:
    """Validate date format is YYYY-MM-DD."""
    if date_str is None:
        return None

    # Check format with regex
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
        raise ValueError(f"Date must be in YYYY-MM-DD format, got: {date_str}")

    # Validate it's a valid date
    try:
        datetime.strptime(date_str, '%Y-%m-%d')
    except ValueError as e:
        raise ValueError(f"Invalid date: {date_str}. {str(e)}")

    return date_str


def _filter_artifact_for_frontend(raw_results: Dict) -> Dict:
    """Remove duplicated content fields from artifact.

    The artifact is sent to frontend alongside cleaned_results.
    Since cleaned_results already contains content/raw_content/score,
    we remove these fields from the artifact to avoid duplication.

    Keeps: query, answer, images, response_time, follow_up_questions,
           results[].title, results[].url, results[].favicon
    Removes: results[].content, results[].raw_content, results[].score

    Args:
        raw_results: Complete API response from Tavily

    Returns:
        Filtered artifact with content fields removed from results array
    """
    filtered = raw_results.copy()
    filtered["type"] = "web_search"

    if "results" in filtered:
        filtered["results"] = [
            {
                k: v
                for k, v in result.items()
                if k not in ("content", "raw_content", "score")
            }
            for result in filtered["results"]
        ]

    return filtered


@tool(response_format="content_and_artifact")
async def web_search(
    query: str,
    time_range: Optional[Literal["day", "week", "month", "year", "d", "w", "m", "y"]] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    topic: Optional[Literal["general", "news", "finance"]] = "general",
) -> Tuple[Union[List[Dict[str, Any]], str], Dict[str, Any]]:
    """Search the web for current information, news, and facts.

    Use when you need to:
    - Find recent news or current events
    - Look up facts, statistics, or real-time data
    - Research topics beyond your knowledge cutoff
    - Verify or update information

    Args:
        query: Search query to look up
        time_range: Filter by recency - 'day'/'d' (24h), 'week'/'w' (7d),
            'month'/'m' (30d), 'year'/'y' (365d).
            Ignored if start_date or end_date is provided.
        start_date: Start date for results (YYYY-MM-DD format).
            Takes priority over time_range.
        end_date: End date for results (YYYY-MM-DD format).
            Takes priority over time_range.
        topic: Search topic - 'general' (default), 'news', or 'finance'
    """
    try:
        # Validate date formats
        _validate_date_format(start_date)
        _validate_date_format(end_date)

        # Prioritization logic: dates > LLM time_range > default_time_range
        effective_time_range = None
        effective_start_date = None
        effective_end_date = None

        if start_date or end_date:
            # Use dates if provided (highest priority)
            effective_start_date = start_date
            effective_end_date = end_date
            logger.debug(
                f"Using date range: start_date={start_date}, end_date={end_date} "
                f"(ignoring time_range={time_range}, default={_default_time_range})"
            )
        else:
            # Use LLM-provided time_range, or fall back to default
            effective_time_range = time_range or _default_time_range
            logger.debug(
                f"Using time_range: {effective_time_range} "
                f"(LLM: {time_range}, default: {_default_time_range})"
            )

        # Verbosity control: determine what to include based on _verbose (set at configure)
        # Always disable raw_content to reduce response size
        include_raw_content = False
        if _verbose:
            include_images = True
            include_image_descriptions = True
            logger.debug("Verbose mode: including images (raw_content disabled)")
        else:
            include_images = False
            include_image_descriptions = False
            logger.debug("Lightweight mode: text-only results")

        api = _get_api_wrapper()
        raw_results = await api.raw_results(
            query,
            _max_results,
            _search_depth,
            _include_domains,
            _exclude_domains,
            _include_answer,
            include_raw_content,
            include_images,
            include_image_descriptions,
            include_favicon=_include_favicon,
            time_range=effective_time_range,
            start_date=effective_start_date,
            end_date=effective_end_date,
            topic=topic,
        )

        cleaned_results = await api.clean_results_with_images(raw_results)
        logger.debug(f"Tavily search completed: {len(cleaned_results)} results")

        # Filter artifact to remove duplicated content fields
        filtered_artifact = _filter_artifact_for_frontend(raw_results)

        return cleaned_results, filtered_artifact

    except Exception as e:
        logger.error(f"Tavily search failed: {e}", exc_info=True)
        return repr(e), {"error": str(e), "query": query}


# Backwards compatibility alias
TavilySearchTool = web_search
