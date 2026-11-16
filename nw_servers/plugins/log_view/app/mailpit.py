from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote

import httpx

from .db import already_imported, insert_event

log = logging.getLogger(__name__)
mailpit_status: dict[str, str | None] = {"last_success": None, "last_error": None}


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "head"}:
            self.hidden += 1
        if not self.hidden and tag in {"br", "p", "div", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "head"} and self.hidden:
            self.hidden -= 1
        if not self.hidden and tag in {"p", "div", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def strip_html(source: str) -> str:
    parser = TextExtractor()
    try:
        parser.feed(source)
        value = "".join(parser.parts)
    except Exception:
        value = re.sub(r"<[^>]*>", " ", source)
    return re.sub(r"[ \t\r\f\v]+", " ", html.unescape(value)).strip()


def _get(data: dict[str, Any], *names: str, default: Any = "") -> Any:
    lookup = {str(k).lower(): v for k, v in data.items()}
    for name in names:
        if name.lower() in lookup:
            return lookup[name.lower()]
    return default


def _address(value: Any) -> str:
    if isinstance(value, dict):
        name = value.get("Name", value.get("name", ""))
        addr = value.get("Address", value.get("address", value.get("Email", "")))
        return f"{name} <{addr}>" if name else str(addr)
    if isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, dict):
                name = item.get("Name", item.get("name", ""))
                addr = item.get("Address", item.get("address", item.get("Email", "")))
                out.append(f"{name} <{addr}>" if name else str(addr))
            else:
                out.append(str(item))
        return ", ".join(x for x in out if x)
    return str(value or "")


async def poll_mailpit_once(client: httpx.AsyncClient, base_url: str) -> int:
    inserted = 0
    start = 0
    while True:
        resp = await client.get(f"{base_url.rstrip('/')}/api/v1/messages", params={"start": start, "limit": 50})
        resp.raise_for_status()
        listing = resp.json()
        messages = listing.get("messages", []) if isinstance(listing, dict) else listing
        if not messages:
            break
        page_has_new = False
        for item in messages:
            mid = str(_get(item, "ID", "id"))
            if not mid or already_imported(f"mailpit:{mid}"):
                continue
            page_has_new = True
            detail_resp = await client.get(f"{base_url.rstrip('/')}/api/v1/message/{quote(mid, safe='')}")
            detail_resp.raise_for_status()
            detail = detail_resp.json()
            message_id = str(_get(detail, "ID", "id", default=mid))
            sender = _address(_get(detail, "From", "from"))
            to = _address(_get(detail, "To", "to"))
            subject = str(_get(detail, "Subject", "subject"))
            text_body = str(_get(detail, "Text", "text", "TextBody", default="") or "")
            html_body = str(_get(detail, "HTML", "html", "HTMLBody", default="") or "")
            body = text_body.strip() or strip_html(html_body)
            received = str(_get(detail, "Date", "date", "Created", "created")) or None
            if received:
                try:
                    parsed_date = datetime.fromisoformat(received.replace("Z", "+00:00"))
                except ValueError:
                    try:
                        parsed_date = parsedate_to_datetime(received)
                    except (TypeError, ValueError):
                        parsed_date = None
                if parsed_date:
                    if parsed_date.tzinfo is None:
                        parsed_date = parsed_date.replace(tzinfo=UTC)
                    received = parsed_date.astimezone(UTC).isoformat()
            parsed = {"from": sender, "to": to, "cc": _address(_get(detail, "Cc", "cc")),
                      "subject": subject, "text": body, "html_text": strip_html(html_body) if html_body else "",
                      "mailpit_id": message_id}
            original = json.dumps(detail, ensure_ascii=False)
            result = insert_event(kind="mail", summary=f"{subject} — {sender}"[:500],
                                  message=" ".join((sender, to, subject, body)), parsed=parsed,
                                  event_at=received, raw_text=original, source_key=f"mailpit:{message_id}")
            inserted += int(result is not None)
        start += len(messages)
        if len(messages) < 50 or not page_has_new:
            break
    return inserted


async def mailpit_loop(stop: asyncio.Event) -> None:
    base_url = os.getenv("MAILPIT_API_URL", "http://mailpit:8025")
    interval = max(2.0, float(os.getenv("MAILPIT_POLL_SECONDS", "2")))
    username = os.getenv("MAILPIT_USERNAME", "")
    password = os.getenv("MAILPIT_PASSWORD", "")
    auth = (username, password) if username else None
    timeout = httpx.Timeout(10, connect=3)
    async with httpx.AsyncClient(auth=auth, timeout=timeout) as client:
        while not stop.is_set():
            try:
                count = await poll_mailpit_once(client, base_url)
                mailpit_status["last_success"] = datetime.now(UTC).isoformat()
                mailpit_status["last_error"] = None
                log.info("Mailpit sync completed; %s new message(s)", count)
            except Exception as exc:
                mailpit_status["last_error"] = str(exc)
                log.warning("Mailpit is unavailable or returned an invalid response: %s", exc)
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except TimeoutError:
                pass
