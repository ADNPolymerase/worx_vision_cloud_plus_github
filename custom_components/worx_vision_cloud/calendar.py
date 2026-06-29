"""Calendar platform for Worx Vision Cloud Plus."""
from __future__ import annotations

import datetime as dt
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .entity import WorxVisionEntity
from .helpers import (
    get_dict_value,
    schedule_day_index,
    schedule_day_label,
    schedule_language,
    schedule_slots,
)

# Calendar event text is free-form and outside translations/*.json, so it is
# localized here from the UI language (falls back to English).
EVENT_SUMMARY = {
    "en": "Mowing",
    "de": "Mähen",
    "fr": "Tonte",
    "pl": "Koszenie trawnika",
}
EVENT_LABELS = {
    "en": {"day": "Day", "duration": "Duration", "edge": "Edge cutting", "source": "Source", "yes": "yes"},
    "de": {"day": "Tag", "duration": "Dauer", "edge": "Kantenschnitt", "source": "Quelle", "yes": "ja"},
    "fr": {"day": "Jour", "duration": "Durée", "edge": "Coupe de bordure", "source": "Source", "yes": "oui"},
    "pl": {"day": "Dzień", "duration": "Czas trwania", "edge": "Koszenie krawędzi", "source": "Źródło", "yes": "tak"},
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up calendar entities."""
    runtime = hass.data[DOMAIN][entry.entry_id]
    coordinator = runtime.coordinator

    async_add_entities(
        WorxVisionScheduleCalendar(coordinator, entry, serial_number)
        for serial_number in coordinator.data
    )


class WorxVisionScheduleCalendar(WorxVisionEntity, CalendarEntity):
    """Read-only mowing schedule calendar."""

    _attr_icon = "mdi:calendar-clock"
    _attr_translation_key = "schedule"

    def __init__(self, coordinator, entry, serial_number: str) -> None:
        """Initialize schedule calendar."""
        super().__init__(coordinator, entry, serial_number, "schedule_calendar")

    @property
    def _language(self) -> str:
        """Return the active Home Assistant UI language."""
        config = getattr(self.hass, "config", None)
        return getattr(config, "language", None) or "en"

    @property
    def event(self) -> CalendarEvent | None:
        """Return the current or next scheduled mowing event."""
        now = dt_util.now()
        events = self._events_between(
            now - dt.timedelta(minutes=1),
            now + dt.timedelta(days=8),
        )
        active = [event for event in events if event.start <= now < event.end]
        if active:
            return active[0]
        return events[0] if events else None

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: dt.datetime,
        end_date: dt.datetime,
    ) -> list[CalendarEvent]:
        """Return mowing events in a time range."""
        return self._events_between(start_date, end_date)

    def _events_between(
        self,
        start_date: dt.datetime,
        end_date: dt.datetime,
    ) -> list[CalendarEvent]:
        """Build weekly schedule occurrences for the requested range."""
        events: list[CalendarEvent] = []
        language = self._language
        tzinfo = start_date.tzinfo or dt_util.DEFAULT_TIME_ZONE
        first_day = start_date.date() - dt.timedelta(days=1)
        last_day = end_date.date() + dt.timedelta(days=1)
        day_count = (last_day - first_day).days + 1

        for offset in range(day_count):
            current_day = first_day + dt.timedelta(days=offset)
            for slot in schedule_slots(self.device):
                if schedule_day_index(get_dict_value(slot, "day")) != current_day.weekday():
                    continue

                event = _slot_to_event(slot, current_day, tzinfo, language)
                if event is None:
                    continue
                if event.end <= start_date or event.start >= end_date:
                    continue
                events.append(event)

        return sorted(events, key=lambda event: event.start)


def _slot_to_event(
    slot: Any,
    event_date: dt.date,
    tzinfo: dt.tzinfo,
    language: str = "en",
) -> CalendarEvent | None:
    """Convert one schedule slot to a localized calendar event occurrence."""
    start_time = _parse_time(get_dict_value(slot, "start"))
    if start_time is None:
        return None

    start = dt.datetime.combine(event_date, start_time, tzinfo=tzinfo)
    end_time = _parse_time(get_dict_value(slot, "end"))
    if end_time is not None:
        end = dt.datetime.combine(event_date, end_time, tzinfo=tzinfo)
        if end <= start:
            end += dt.timedelta(days=1)
    else:
        duration = _duration_minutes(slot)
        if duration is None:
            return None
        end = start + dt.timedelta(minutes=duration)

    lang = schedule_language(language)
    labels = EVENT_LABELS[lang]
    day_label = schedule_day_label(get_dict_value(slot, "day"), lang)
    duration = _duration_minutes(slot)
    description_parts = [f"{labels['day']}: {day_label}"]
    if duration is not None:
        description_parts.append(f"{labels['duration']}: {duration} min")
    if get_dict_value(slot, "boundary"):
        description_parts.append(f"{labels['edge']}: {labels['yes']}")
    source = get_dict_value(slot, "source")
    if source is not None:
        description_parts.append(f"{labels['source']}: {source}")

    return CalendarEvent(
        start=start,
        end=end,
        summary=EVENT_SUMMARY[lang],
        description="\n".join(description_parts),
    )


def _parse_time(value: Any) -> dt.time | None:
    """Parse HH:MM time from pyworxcloud schedule data."""
    if not isinstance(value, str) or ":" not in value:
        return None
    hour, minute, *_ = value.split(":")
    try:
        return dt.time(hour=int(hour), minute=int(minute))
    except ValueError:
        return None


def _duration_minutes(slot: Any) -> int | None:
    """Return the effective slot duration in minutes."""
    duration = get_dict_value(slot, "duration_extended")
    if duration is None:
        duration = get_dict_value(slot, "duration")
    try:
        return int(duration)
    except (TypeError, ValueError):
        return None
