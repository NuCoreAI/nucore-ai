from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

import httpx

from intent_handler import BaseIntentHandler, IntentHandlerResult
from utils import get_logger

logger = get_logger(__name__)


def _extract_year(query: str) -> int:
    match = re.search(r"\b(19\d{2}|20\d{2}|21\d{2})\b", query)
    if match:
        return int(match.group(1))
    return date.today().year


class HebcalIntentHandler(BaseIntentHandler):
    """Resolve Jewish holiday timing using Hebcal and publish step context.

    The runtime appends this handler's context update into shared
    ``step_contexts`` so downstream intents (for example routine_automation)
    can consume trusted temporal boundaries without asking the user.
    """

    async def get_prompt_runtime_replacements(
        self,
        query,
        *,
        framework_context=None,
        route_result=None,
    ) -> dict[str, str]:
        location_information = await self.nucore_interface.get_timespecs() if self.nucore_interface else None 
        return {"<<location_information>>": "Get from the user" if not location_information else f"```json\n{json.dumps(location_information, indent=2)}\n```"}  

    async def handle(
        self,
        query,
        *,
        route_result=None,
        framework_context: str = None,
        raw_response: IntentHandlerResult | None = None,
        tool_calls=None,
    ) -> IntentHandlerResult | None:
        """Return a short confirmation; structured data is published via step context hook."""
        response = raw_response
        if response is None:
            return None

        # Do not surface model-generated structured output for this intent.
        # The structured payload is propagated through step_contexts instead.
        response.set_output({"text": "Temporal window resolved."})
        response.set_route_result(route_result=route_result)
        return response

    async def get_step_context_update(
        self,
        *,
        query: str,
        route_result=None,
        framework_context: str | None = None,
        result: IntentHandlerResult | None = None,
    ) -> dict[str, Any] | None:
        holiday_key = self._detect_holiday(query)
        if holiday_key is None:
            return None

        year = _extract_year(query)
        location_information = await self.nucore_interface.get_timespecs() if self.nucore_interface else None
        tzid = (location_information or {}).get("timezone")
        israel = bool(tzid and str(tzid).lower() in {"asia/jerusalem"})

        window = await self._resolve_holiday_window(holiday_key=holiday_key, year=year, israel=israel)
        if window is None:
            return {
                "temporal_resolution": {
                    "status": "unresolved",
                    "source": "hebcal",
                    "holiday_key": holiday_key,
                    "year": year,
                }
            }

        return {
            "temporal_resolution": {
                "status": "resolved",
                "source": "hebcal",
                "holiday_key": holiday_key,
                "year": year,
                "israel_calendar": israel,
                "timezone": tzid,
                "window": window,
            }
        }

    def _detect_holiday(self, query: str) -> str | None:
        normalized = query.lower()

        if any(token in normalized for token in ("pesach", "pessach", "passover")):
            return "pesach"
        if "shavuot" in normalized:
            return "shavuot"
        if "yom kippur" in normalized:
            return "yom_kippur"
        if any(token in normalized for token in ("rosh hashana", "rosh hashanah")):
            return "rosh_hashana"
        if "sukkot" in normalized:
            return "sukkot"

        return None

    async def _resolve_holiday_window(
        self,
        *,
        holiday_key: str,
        year: int,
        israel: bool,
    ) -> dict[str, Any] | None:
        api_url = "https://www.hebcal.com/hebcal"
        params = {
            "cfg": "json",
            "year": year,
            "v": 1,
            "maj": "on",
            "i": "on" if israel else "off",
        }

        timeout = httpx.Timeout(10.0, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(api_url, params=params)
            response.raise_for_status()
            payload = response.json()

        items = payload.get("items", []) if isinstance(payload, dict) else []
        if not items:
            return None

        mapping = {
            "pesach": {
                "start_titles": {"Erev Pesach"},
                "end_titles": {"Pesach VII"} if israel else {"Pesach VIII", "Pesach VII"},
                "name": "Pesach",
            },
            "shavuot": {
                "start_titles": {"Erev Shavuot"},
                "end_titles": {"Shavuot I"} if israel else {"Shavuot II", "Shavuot I"},
                "name": "Shavuot",
            },
            "yom_kippur": {
                "start_titles": {"Erev Yom Kippur"},
                "end_titles": {"Yom Kippur"},
                "name": "Yom Kippur",
            },
            "rosh_hashana": {
                "start_titles": {"Erev Rosh Hashana"},
                "end_titles": {"Rosh Hashana 5787", "Rosh Hashana II"},
                "name": "Rosh Hashana",
            },
            "sukkot": {
                "start_titles": {"Erev Sukkot"},
                "end_titles": {"Sukkot VII (Hoshana Raba)", "Sukkot VI (CH''M)"},
                "name": "Sukkot",
            },
        }

        holiday = mapping.get(holiday_key)
        if holiday is None:
            return None

        start_date = self._find_holiday_date(items, holiday["start_titles"])
        end_date = self._find_holiday_date(items, holiday["end_titles"])
        if not start_date or not end_date:
            return None

        return {
            "holiday": holiday["name"],
            "start": {
                "date": start_date,
                "boundary": "sunset",
            },
            "end": {
                "date": end_date,
                "boundary": "nightfall",
            },
            "notes": (
                "Use sunset start and nightfall end for schedule construction. "
                "If nightfall is unsupported by schedule schema, approximate with a small positive sunset offset."
            ),
        }

    def _find_holiday_date(self, items: list[dict[str, Any]], titles: set[str]) -> str | None:
        for item in items:
            title = item.get("title")
            if title in titles:
                return item.get("date")
        return None
