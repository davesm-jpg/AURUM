"""Versioned normalization of timed FOMC rows in official monthly calendars.

The monthly HTML omits offsets. America/New_York is an explicit source
convention, cross-checked against the Board's EST/EDT release headers. Meeting
description date ranges never substitute for the published release-date column.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from html.parser import HTMLParser
from re import fullmatch
from urllib.parse import urlsplit

from aurum.economic_events import (
    Announcement,
    EconomicCalendar,
    EconomicSourceKind,
)
from aurum.exchange_calendar import TradingWindow
from aurum.market_data import canonical_json
from aurum.timestamps import minute_timestamp, source_timezone

FED_CALENDAR_VERSION = 1
FED_MONTH_POLICY_VERSION = 1
FED_SOURCE_TIMEZONE = "America/New_York"
FED_CALENDAR_ROOT = "https://www.federalreserve.gov/newsevents/calendar.htm"
MAX_FED_HTML_BYTES = 2 * 1024 * 1024
_MONTHS = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)
_VOID = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)


def fed_month_url(at: datetime) -> str:
    if at.tzinfo is not UTC:
        raise ValueError("Fed calendar request clock must be UTC")
    local = at.astimezone(source_timezone(FED_SOURCE_TIMEZONE))
    return (
        f"https://www.federalreserve.gov/newsevents/{local.year:04d}-{_MONTHS[local.month - 1]}.htm"
    )


def fed_source_month(url: str) -> tuple[int, int]:
    parsed = urlsplit(url)
    match = fullmatch(r"/newsevents/([0-9]{4})-([a-z]+)\.htm", parsed.path)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "www.federalreserve.gov"
        or parsed.query
        or parsed.fragment
        or match is None
        or match[2] not in _MONTHS
    ):
        raise ValueError("Fed calendar requires an official monthly source URL")
    year = int(match[1])
    datetime(year, 1, 1)
    return year, _MONTHS.index(match[2]) + 1


@dataclass(slots=True)
class _Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[_Node | str] = field(default_factory=list)

    def walk(self) -> Iterator[_Node]:
        yield self
        for child in self.children:
            if isinstance(child, _Node):
                yield from child.walk()

    def text(self) -> str:
        return " ".join(
            " ".join(
                child.text() if isinstance(child, _Node) else child for child in self.children
            ).split()
        )

    def has_class(self, name: str) -> bool:
        return name in self.attrs.get("class", "").split()


class _Document(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("document")
        self.stack = [self.root]
        self.nodes = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.nodes += 1
        if self.nodes > 50_000 or len(self.stack) >= 128:
            raise ValueError("Fed calendar HTML exceeds structural limits")
        for name in ("id", "class"):
            if sum(key == name for key, _ in attrs) > 1:
                raise ValueError("Fed calendar has ambiguous structural attributes")
        node = _Node(tag, {name: value or "" for name, value in attrs})
        self.stack[-1].children.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


@dataclass(frozen=True, slots=True)
class FedMonthlyCalendar:
    year: int
    month: int
    events: tuple[tuple[str, datetime], ...]

    @property
    def coverage(self) -> TradingWindow:
        next_year, next_month = (
            (self.year + 1, 1) if self.month == 12 else (self.year, self.month + 1)
        )
        zone = source_timezone(FED_SOURCE_TIMEZONE)
        return TradingWindow(
            minute_timestamp(f"{self.year:04d}-{self.month:02d}-01T00:00", zone),
            minute_timestamp(f"{next_year:04d}-{next_month:02d}-01T00:00", zone),
        )


def _column(panel: _Node, name: str) -> _Node:
    columns = [node for node in panel.walk() if node.has_class(name)]
    if len(columns) != 1:
        raise ValueError("Fed FOMC entry has missing or ambiguous columns")
    return columns[0]


def parse_fed_monthly(raw: bytes, *, source_url: str) -> FedMonthlyCalendar:
    year, month = fed_source_month(source_url)
    if not 0 < len(raw) <= MAX_FED_HTML_BYTES:
        raise ValueError("Fed monthly calendar has invalid size")
    try:
        html = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("Fed monthly calendar must be UTF-8 HTML") from None
    document = _Document()
    document.feed(html)
    document.close()
    articles = [node for node in document.root.walk() if node.attrs.get("id") == "article"]
    if len(articles) != 1 or not articles[0].has_class("cal-nojs"):
        raise ValueError("Fed monthly calendar layout is not recognized")
    article = articles[0]
    month_title = f"{_MONTHS[month - 1]} {year}"
    headings = [node.text().casefold() for node in article.walk() if node.tag == "h4"]
    if headings.count(month_title) != 1:
        raise ValueError("Fed monthly calendar title disagrees with its source URL")
    if headings.count("fomc meetings") != 1:
        raise ValueError("Fed monthly calendar has no recognized FOMC section or repeats it")
    within_fomc = seen_fomc = False
    events: dict[tuple[str, datetime], None] = {}
    for node in article.walk():
        if node.tag == "h4":
            within_fomc = node.text().casefold() == "fomc meetings"
            seen_fomc = seen_fomc or within_fomc
        if not within_fomc or not node.has_class("panel-body"):
            continue
        time_text = _column(node, "col-xs-2").text().casefold()
        title_column = _column(node, "col-xs-7")
        titles = [item for item in title_column.walk() if item.tag == "p"]
        title = titles[0].text() if titles else ""
        days = _column(node, "col-xs-3").text()
        clock = fullmatch(r"([0-9]{1,2}):([0-5][0-9])\s*([ap])\.m\.", time_text)
        if clock is None or not 1 <= int(clock[1]) <= 12:
            raise ValueError("Fed FOMC entry requires an explicit supported release time")
        if not title.startswith("FOMC ") or not len(title) <= 256:
            raise ValueError("Fed FOMC entry has an unsupported release title")
        if fullmatch(r"[0-9]{1,2}(?:\s*,\s*[0-9]{1,2})*", days) is None:
            raise ValueError("Fed FOMC entry requires explicit release dates")
        hour = int(clock[1]) % 12 + (12 if clock[3] == "p" else 0)
        for day in days.split(","):
            local = f"{year:04d}-{month:02d}-{int(day):02d}T{hour:02d}:{clock[2]}"
            stamp = minute_timestamp(local, source_timezone(FED_SOURCE_TIMEZONE))
            key = title, stamp
            if key in events or len(events) >= 128:
                raise ValueError("Fed FOMC entries are duplicated or exceed the local bound")
            events[key] = None
    if not seen_fomc:
        raise ValueError("Fed monthly calendar has no recognized FOMC section")
    return FedMonthlyCalendar(
        year, month, tuple(sorted(events, key=lambda item: (item[1], item[0])))
    )


def normalize_fed_monthly(
    raw: bytes,
    *,
    source_url: str,
    capture_id: str,
    observed_at: datetime,
    coverage: TradingWindow,
    title_prefixes: tuple[str, ...],
    data_kind: EconomicSourceKind,
) -> EconomicCalendar:
    if observed_at.tzinfo is not UTC or fullmatch(r"[a-f0-9]{64}", capture_id) is None:
        raise ValueError("Fed normalization requires a UTC receipt and archived capture identity")
    prefixes = tuple(prefix.strip() for prefix in title_prefixes)
    if not 1 <= len(prefixes) <= 32 or any(not prefix or len(prefix) > 256 for prefix in prefixes):
        raise ValueError("provide bounded nonempty Fed release title prefixes")
    parsed = parse_fed_monthly(raw, source_url=source_url)
    if coverage.start < parsed.coverage.start or coverage.end > parsed.coverage.end:
        raise ValueError("reviewed Fed coverage must lie within the captured source month")
    definition = {
        "fed_calendar_version": FED_CALENDAR_VERSION,
        "source_timezone": FED_SOURCE_TIMEZONE,
        "source_url": source_url,
        "source_sha256": sha256(raw).hexdigest(),
        "observed_at": observed_at.isoformat(),
        "title_prefixes": prefixes,
    }
    digest = sha256(canonical_json(definition).encode()).hexdigest()
    version = f"archive-v1/{capture_id}/fed-monthly-v1/{digest}"
    events = tuple(
        Announcement(
            sha256(canonical_json((source_url, title, stamp.isoformat())).encode()).hexdigest(),
            title,
            stamp,
            observed_at,
        )
        for title, stamp in parsed.events
        if coverage.start <= stamp < coverage.end
        and any(title.casefold().startswith(prefix.casefold()) for prefix in prefixes)
    )
    return EconomicCalendar(
        "Federal Reserve Board monthly calendar",
        version,
        coverage,
        observed_at,
        events,
        coverage_complete=False,
        data_kind=data_kind,
    )
