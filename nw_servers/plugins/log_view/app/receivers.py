from __future__ import annotations

import asyncio
import json
import logging
import re
import socket
from typing import Any

from .db import insert_event
from .parsers import parse_syslog

log = logging.getLogger(__name__)


class SyslogDatagram(asyncio.DatagramProtocol):
    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        parsed = parse_syslog(data)
        msg = str(parsed.get("message", ""))
        insert_event(kind="syslog", summary=msg[:500], message=msg, parsed=parsed,
                     event_at=parsed.get("event_at"), source_ip=addr[0], raw_bytes=data)


def _tlv(buf: bytes, offset: int = 0) -> tuple[int, bytes, int]:
    tag = buf[offset]
    offset += 1
    length = buf[offset]
    offset += 1
    if length & 0x80:
        count = length & 0x7f
        if count == 0 or count > 4 or offset + count > len(buf):
            raise ValueError("invalid BER length")
        length = int.from_bytes(buf[offset:offset + count], "big")
        offset += count
    end = offset + length
    if end > len(buf):
        raise ValueError("truncated BER value")
    return tag, buf[offset:end], end


def _children(buf: bytes) -> list[tuple[int, bytes]]:
    out = []
    offset = 0
    while offset < len(buf):
        tag, value, offset = _tlv(buf, offset)
        out.append((tag, value))
    return out


def _integer(value: bytes, signed: bool = True) -> int:
    return int.from_bytes(value, "big", signed=signed)


def _oid(value: bytes) -> str:
    if not value:
        return ""
    subids: list[int] = []
    n = 0
    for byte in value:
        n = (n << 7) | (byte & 0x7f)
        if byte < 0x80:
            subids.append(n)
            n = 0
    first = subids.pop(0)
    arcs = [min(first // 40, 2), first - min(first // 40, 2) * 40, *subids]
    return ".".join(map(str, arcs))


def _decode_value(tag: int, value: bytes) -> dict[str, Any]:
    if tag == 0x02:
        return {"type": "Integer32", "value": _integer(value)}
    if tag == 0x04:
        try:
            data = value.decode("utf-8")
            if all(ch.isprintable() or ch in "\r\n\t" for ch in data):
                return {"type": "OctetString", "value": data}
        except UnicodeDecodeError:
            pass
        return {"type": "OctetString", "hex": value.hex()}
    if tag == 0x05:
        return {"type": "Null", "value": None}
    if tag == 0x06:
        return {"type": "ObjectIdentifier", "value": _oid(value)}
    if tag == 0x40:
        return {"type": "IpAddress", "value": socket.inet_ntop(socket.AF_INET, value) if len(value) == 4 else value.hex()}
    names = {0x41: "Counter32", 0x42: "Gauge32", 0x43: "TimeTicks", 0x46: "Counter64", 0x80: "noSuchObject", 0x81: "noSuchInstance", 0x82: "endOfMibView"}
    if tag in (0x41, 0x42, 0x43, 0x46):
        return {"type": names[tag], "value": _integer(value, signed=False)}
    return {"type": names.get(tag, f"BER-0x{tag:02x}"), "hex": value.hex()}


def parse_snmp(data: bytes) -> dict[str, Any]:
    outer_tag, outer, _ = _tlv(data)
    if outer_tag != 0x30:
        raise ValueError("SNMP message is not a sequence")
    parts = _children(outer)
    version = _integer(parts[0][1])
    result: dict[str, Any] = {"version": {0: "v1", 1: "v2c", 3: "v3"}.get(version, str(version)), "varbinds": []}
    if version == 3:
        result["decode"] = "SNMPv3 security parameters retained as raw BER; credentials required to decode scoped PDU"
        return result
    result["community_present"] = bool(parts[1][1])
    pdu_tag, pdu = parts[2]
    pdu_items = _children(pdu)
    result["pdu_type"] = {0xa4: "trap-v1", 0xa7: "trap-v2", 0xa6: "inform"}.get(pdu_tag, f"0x{pdu_tag:02x}")
    if pdu_tag == 0xa4:
        result["enterprise_oid"] = _oid(pdu_items[0][1])
        result["agent_address"] = socket.inet_ntop(socket.AF_INET, pdu_items[1][1]) if len(pdu_items[1][1]) == 4 else pdu_items[1][1].hex()
        result["generic_trap"] = _integer(pdu_items[2][1])
        result["specific_trap"] = _integer(pdu_items[3][1])
        result["uptime"] = _integer(pdu_items[4][1], signed=False)
        varbind_list = pdu_items[5][1]
        result["trap_oid"] = result["enterprise_oid"]
    else:
        result["request_id"] = _integer(pdu_items[0][1])
        result["error_status"] = _integer(pdu_items[1][1])
        result["error_index"] = _integer(pdu_items[2][1])
        varbind_list = pdu_items[3][1]
    for vb_tag, vb in _children(varbind_list):
        if vb_tag != 0x30:
            continue
        vb_items = _children(vb)
        if len(vb_items) < 2:
            continue
        oid_tag, oid_raw = vb_items[0]
        val_tag, val_raw = vb_items[1]
        if oid_tag != 0x06:
            continue
        oid = _oid(oid_raw)
        value = _decode_value(val_tag, val_raw)
        if oid == "1.3.6.1.6.3.1.1.4.1.0" and "value" in value:
            result["trap_oid"] = value["value"]
        if oid == "1.3.6.1.2.1.1.3.0" and "value" in value:
            result["uptime"] = value["value"]
        result["varbinds"].append({"oid": oid, **value})
    return result


class TrapDatagram(asyncio.DatagramProtocol):
    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        _store_trap(data, addr[0])


def _store_trap(data: bytes, source_ip: str) -> None:
    try:
        parsed = parse_snmp(data)
        trap_oid = parsed.get("trap_oid", "")
        summary = f"{parsed.get('pdu_type', 'SNMP')} {trap_oid}".strip()
        message = json.dumps(parsed, ensure_ascii=False)
    except Exception as exc:
        parsed = {"decode_error": str(exc)}
        summary = "SNMP Trap (decode error)"
        message = data.hex()
    insert_event(kind="snmp", summary=summary, message=message, parsed=parsed,
                 source_ip=source_ip, raw_bytes=data)


async def _read_snmp_tcp_message(reader: asyncio.StreamReader) -> bytes:
    first = await reader.readexactly(2)
    if first[0] != 0x30:
        raise ValueError("SNMP TCP frame must start with BER SEQUENCE")
    length_byte = first[1]
    if length_byte < 0x80:
        length, header = length_byte, first
    else:
        count = length_byte & 0x7f
        if count == 0 or count > 4:
            raise ValueError("unsupported BER length form")
        size_bytes = await reader.readexactly(count)
        length, header = int.from_bytes(size_bytes, "big"), first + size_bytes
    if length > 1_048_576:
        raise ValueError("SNMP TCP frame exceeds 1 MiB")
    return header + await reader.readexactly(length)


async def _serve_snmp_tcp(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    peer = writer.get_extra_info("peername")
    try:
        while True:
            try:
                packet = await _read_snmp_tcp_message(reader)
            except asyncio.IncompleteReadError:
                break
            _store_trap(packet, peer[0] if peer else "")
    except (ValueError, ConnectionError) as exc:
        log.debug("closing malformed SNMP TCP stream: %s", exc)
    finally:
        writer.close()
        await writer.wait_closed()


async def _serve_tcp(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    peer = writer.get_extra_info("peername")
    try:
        while True:
            first = await reader.read(1)
            if not first:
                break
            if first.isdigit():
                header = first + await reader.readuntil(b" ")
                try:
                    size = int(header[:-1])
                    if 0 < size <= 1_048_576:
                        payload = await reader.readexactly(size)
                        packets = [payload]
                    else:
                        packets = [header + await reader.readline()]
                except (ValueError, asyncio.IncompleteReadError):
                    packets = [header + await reader.readline()]
            else:
                packets = [first + await reader.readline()]
            for packet in packets:
                parsed = parse_syslog(packet)
                msg = str(parsed.get("message", ""))
                insert_event(kind="syslog", summary=msg[:500], message=msg, parsed=parsed,
                             event_at=parsed.get("event_at"), source_ip=peer[0] if peer else None,
                             raw_bytes=packet)
    except (asyncio.IncompleteReadError, ConnectionError):
        pass
    finally:
        writer.close()
        await writer.wait_closed()


async def start_receivers() -> list[Any]:
    loop = asyncio.get_running_loop()
    transports = []
    transport, _ = await loop.create_datagram_endpoint(lambda: SyslogDatagram(), local_addr=("0.0.0.0", 1514))
    transports.append(transport)
    transport, _ = await loop.create_datagram_endpoint(lambda: TrapDatagram(), local_addr=("0.0.0.0", 1162))
    transports.append(transport)
    snmp_tcp = await asyncio.start_server(_serve_snmp_tcp, "0.0.0.0", 1162, limit=1_048_576)
    transports.append(snmp_tcp)
    server = await asyncio.start_server(_serve_tcp, "0.0.0.0", 1514, limit=1_048_576)
    transports.append(server)
    log.info("listening for syslog UDP/TCP on 1514 and SNMP Trap UDP/TCP on 1162")
    return transports
