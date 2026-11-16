from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

PRI = re.compile(r"^<(\d{1,3})>")
RFC5424 = re.compile(
    r"^<(\d{1,3})>(\d{1,3}) (\S+) (\S+) (\S+) (\S+) (\S+) (?:((?:\[[^]]*\])+|-))(?: (.*))?$"
)
RFC3164 = re.compile(r"^<(\d{1,3})>([A-Z][a-z]{2}\s+\d{1,2}\s+\d\d:\d\d:\d\d)\s+(\S+)\s+(.*)$")
KV = re.compile(r'([A-Za-z0-9_.-]+)=("(?:\\.|[^"\\])*"|\S+)')


def parse_syslog(raw: bytes) -> dict[str, Any]:
    text = raw.decode("utf-8", errors="replace").rstrip("\x00\r\n")
    result: dict[str, Any] = {"format": "unknown", "message": text}
    m = RFC5424.match(text)
    if m:
        pri, version, timestamp, host, app, procid, msgid, structured, message = m.groups()
        result = {"format": "rfc5424", "pri": int(pri), "facility": int(pri) // 8,
                  "severity": int(pri) % 8, "version": int(version), "timestamp": timestamp,
                  "hostname": host, "app_name": app, "procid": procid, "msgid": msgid,
                  "structured_data": structured, "message": message or ""}
        if timestamp != "-":
            try:
                result["event_at"] = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(UTC).isoformat()
            except ValueError:
                pass
        return result
    m = RFC3164.match(text)
    if m:
        pri, timestamp, host, message = m.groups()
        result = {"format": "rfc3164", "pri": int(pri), "facility": int(pri) // 8,
                  "severity": int(pri) % 8, "timestamp_raw": timestamp,
                  "hostname": host, "message": message,
                  "time_note": "RFC3164 has no year or timezone; received_at is used for ordering"}
        return result
    if any(token in text for token in ("date=", "devname=", "devid=", "logid=", "type=", "subtype=")):
        fields: dict[str, str] = {}
        for key, value in KV.findall(text):
            if value.startswith('"'):
                value = value[1:-1].replace('\\"', '"').replace('\\\\', '\\')
            fields[key] = value
        pri_match = PRI.match(text)
        pri = int(pri_match.group(1)) if pri_match else None
        result = {"format": "fortigate", "pri": pri,
                  "facility": pri // 8 if pri is not None else None,
                  "severity": pri % 8 if pri is not None else None,
                  "vendor_fields": fields, "message": text}
        return result
    if PRI.match(text):
        pri = int(PRI.match(text).group(1))
        result.update({"format": "legacy_or_unknown", "pri": pri,
                       "facility": pri // 8, "severity": pri % 8,
                       "message": text[PRI.match(text).end():].lstrip()})
    return result
