from __future__ import annotations

import asyncio
import base64
import csv
import hashlib
from email.utils import parsedate_to_datetime
import io
import json
import logging
import os
import secrets
import sqlite3
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel, Field

from . import db as database
from .mailpit import mailpit_loop
from .receivers import start_receivers

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger(__name__)
PASSWORD = os.getenv("LOG_VIEW_ADMIN_PASSWORD", "")
SECRET = os.getenv("LOG_VIEW_SECRET_KEY", "")
if not PASSWORD or not SECRET:
    raise RuntimeError("LOG_VIEW_ADMIN_PASSWORD and LOG_VIEW_SECRET_KEY are required")
PASSWORD_HASH = hashlib.scrypt(PASSWORD.encode("utf-8"), salt=hashlib.sha256(SECRET.encode()).digest()[:16], n=2**14, r=8, p=1)
serializer = URLSafeTimedSerializer(SECRET, salt="log-view-session")
sessions: dict[str, str] = {}
csrf_by_session: dict[str, str] = {}
receiver_handles: list[Any] = []
mail_stop: asyncio.Event | None = None
mail_task: asyncio.Task[None] | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global receiver_handles, mail_stop, mail_task
    database.initialize()
    receiver_handles = await start_receivers()
    mail_stop = asyncio.Event()
    mail_task = asyncio.create_task(mailpit_loop(mail_stop))
    yield
    if mail_stop:
        mail_stop.set()
    if mail_task:
        await mail_task
    for handle in receiver_handles:
        handle.close()


app = FastAPI(title="NW Log View", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="/app/static"), name="static")


def current_session(request: Request) -> str:
    token = request.cookies.get("lv_session", "")
    try:
        sid = serializer.loads(token, max_age=60 * 60 * 12)
    except BadSignature:
        raise HTTPException(401, "Login required")
    if sid not in sessions:
        raise HTTPException(401, "Login required")
    return sid


def authorized(sid: str = Depends(current_session)) -> str:
    return sid


def csrf(request: Request, sid: str = Depends(authorized)) -> str:
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        token = request.headers.get("x-csrf-token", "")
        if not secrets.compare_digest(token, csrf_by_session.get(sid, "")):
            raise HTTPException(403, "CSRF validation failed")
    return sid


class LoginBody(BaseModel):
    password: str


class NodeBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    addresses: list[str] = Field(default_factory=list)
    description: str = ""
    group_ids: list[int] = []


class GroupBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""


class DeleteBody(BaseModel):
    before: str | None = None
    kind: str | None = None
    confirm: bool = False


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return Path("/app/static/index.html").read_text(encoding="utf-8")


@app.post("/api/login")
async def login(body: LoginBody) -> JSONResponse:
    candidate = hashlib.scrypt(body.password.encode("utf-8"), salt=hashlib.sha256(SECRET.encode()).digest()[:16], n=2**14, r=8, p=1)
    if not secrets.compare_digest(candidate, PASSWORD_HASH):
        raise HTTPException(401, "Invalid password")
    sid, csrf_token = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    sessions[sid] = "admin"
    csrf_by_session[sid] = csrf_token
    response = JSONResponse({"ok": True, "csrf_token": csrf_token})
    response.set_cookie("lv_session", serializer.dumps(sid), httponly=True, samesite="strict", max_age=43200)
    return response


@app.get("/api/session")
async def session_info(sid: str = Depends(authorized)) -> dict[str, str]:
    return {"csrf_token": csrf_by_session[sid]}


@app.post("/api/logout")
async def logout(sid: str = Depends(csrf)) -> JSONResponse:
    sessions.pop(sid, None)
    csrf_by_session.pop(sid, None)
    response = JSONResponse({"ok": True})
    response.delete_cookie("lv_session")
    return response


def _event_dict(row: sqlite3.Row, *, include_raw: bool = False) -> dict[str, Any]:
    item = dict(row)
    item["parsed"] = json.loads(item["parsed"] or "{}")
    if item.get("kind") == "snmp":
        try:
            from .mibs import resolve_oid
            parsed = item["parsed"]
            trap_oid = parsed.get("trap_oid")
            if trap_oid:
                parsed["display_trap_oid"] = resolve_oid(str(trap_oid)) or trap_oid
            for varbind in parsed.get("varbinds", []):
                varbind["display_oid"] = resolve_oid(varbind.get("oid", "")) or varbind.get("oid", "")
                if varbind.get("type") == "ObjectIdentifier" and varbind.get("value"):
                    varbind["display_value_oid"] = resolve_oid(varbind["value"]) or varbind["value"]
        except Exception:
            pass
    if include_raw:
        item["raw_base64"] = base64.b64encode(item.pop("raw_bytes") or b"").decode("ascii")
    else:
        item.pop("raw_bytes", None)
        item.pop("raw_text", None)
    return item


def _where(from_: str | None, to: str | None, q: str | None, kinds: list[str] | None,
           node_ids: list[int] | None, group_ids: list[int] | None,
           source_ip: str | list[str] | None = None, cursor: int | None = None) -> tuple[str, list[Any]]:
    clauses, params = [], []
    if from_:
        clauses.append("e.received_at >= ?")
        params.append(from_)
    if to:
        clauses.append("e.received_at < ?")
        params.append(to)
    if kinds:
        clauses.append("e.kind IN (" + ",".join("?" for _ in kinds) + ")")
        params.extend(kinds)
    if node_ids:
        clauses.append("e.node_id IN (" + ",".join("?" for _ in node_ids) + ")")
        params.extend(node_ids)
    if group_ids:
        clauses.append("EXISTS (SELECT 1 FROM node_groups ng WHERE ng.node_id=e.node_id AND ng.group_id IN (" + ",".join("?" for _ in group_ids) + "))")
        params.extend(group_ids)
    if isinstance(source_ip, list) and source_ip:
        clauses.append("e.source_ip IN (" + ",".join("?" for _ in source_ip) + ")")
        params.extend(source_ip)
    elif source_ip:
        clauses.append("e.source_ip = ?")
        params.append(source_ip)
    if cursor is not None:
        clauses.append("e.id > ?")
        params.append(cursor)
    if q:
        words = q.strip()
        if database.FTS_AVAILABLE:
            fts_query = " AND ".join(f'"{part.replace(chr(34), chr(34) * 2)}"*' for part in words.split())
            clauses.append("(e.id IN (SELECT rowid FROM events_fts WHERE events_fts MATCH ?) OR e.source_ip LIKE ?)")
            params.extend([fts_query, f"%{words}%"])
        else:
            clauses.append("(e.summary LIKE ? OR e.message LIKE ? OR e.source_ip LIKE ?)")
            params.extend([f"%{words}%"] * 3)
    return (" WHERE " + " AND ".join(clauses) if clauses else "", params)


@app.get("/api/events")
async def events(from_: str | None = Query(None, alias="from"), to: str | None = None,
                 q: str | None = None, kind: list[str] | None = Query(None),
                 node_id: list[int] | None = Query(None), group_id: list[int] | None = Query(None),
                 source_ip: list[str] | None = Query(None), after_id: int | None = None,
                 limit: int = Query(200, ge=1, le=1000), offset: int = Query(0, ge=0),
                 _: str = Depends(authorized)) -> dict[str, Any]:
    where, params = _where(from_, to, q, kind, node_id, group_id, source_ip, after_id)
    order = "ASC" if after_id is not None else "DESC"
    with database.connection() as db:
        rows = db.execute(f"SELECT e.*, n.name AS node_name FROM events e LEFT JOIN nodes n ON n.id=e.node_id{where} ORDER BY e.id {order} LIMIT ? OFFSET ?", [*params, limit, offset]).fetchall()
        total = db.execute(f"SELECT COUNT(*) FROM events e{where}", params).fetchone()[0]
        return {"events": [_event_dict(row) for row in rows], "total": total,
                "next_after_id": rows[-1]["id"] if rows and after_id is not None else None}


@app.get("/api/events/{event_id}")
async def event_detail(event_id: int, _: str = Depends(authorized)) -> dict[str, Any]:
    with database.connection() as db:
        row = db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Event not found")
        result = _event_dict(row, include_raw=True)
        if result.get("raw_text"):
            try:
                result["raw_mail"] = json.loads(result["raw_text"])
            except json.JSONDecodeError:
                result["raw_mail"] = result["raw_text"]
        return result


@app.get("/api/nodes")
async def list_nodes(_: str = Depends(authorized)) -> dict[str, Any]:
    with database.connection() as db:
        nodes = db.execute("""SELECT n.*, group_concat(DISTINCT g.id) group_ids,
          group_concat(DISTINCT g.name) group_names,
          group_concat(DISTINCT a.address) addresses FROM nodes n
          LEFT JOIN node_addresses a ON a.node_id=n.id
          LEFT JOIN node_groups ng ON ng.node_id=n.id LEFT JOIN groups g ON g.id=ng.group_id
          GROUP BY n.id ORDER BY n.name""").fetchall()
        unknown = db.execute("SELECT DISTINCT source_ip FROM events WHERE source_ip IS NOT NULL AND node_id IS NULL ORDER BY source_ip").fetchall()
        groups = db.execute("SELECT * FROM groups ORDER BY name").fetchall()
        return {"nodes": [dict(x) for x in nodes], "unknown_addresses": [x[0] for x in unknown], "groups": [dict(x) for x in groups]}


def _set_node_groups(db: sqlite3.Connection, node_id: int, group_ids: list[int]) -> None:
    db.execute("DELETE FROM node_groups WHERE node_id=?", (node_id,))
    for group_id in sorted(set(group_ids)):
        if not db.execute("SELECT 1 FROM groups WHERE id=?", (group_id,)).fetchone():
            raise HTTPException(400, f"Unknown group id: {group_id}")
        db.execute("INSERT INTO node_groups(node_id, group_id) VALUES (?, ?)", (node_id, group_id))


@app.post("/api/nodes")
async def create_node(body: NodeBody, _: str = Depends(csrf)) -> dict[str, Any]:
    now = database.now_iso()
    with database.connection() as db:
        cur = db.execute("INSERT INTO nodes(name, description, created_at, updated_at) VALUES (?, ?, ?, ?)",
                         (body.name.strip(), body.description, now, now))
        node_id = int(cur.lastrowid)
        try:
            for address in body.addresses:
                db.execute("INSERT INTO node_addresses(node_id, address) VALUES (?, ?)", (node_id, address.strip()))
            _set_node_groups(db, node_id, body.group_ids)
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Address is already assigned to another node")
        database.refresh_node_links(db, node_id)
    return {"id": node_id}


@app.put("/api/nodes/{node_id}")
async def update_node(node_id: int, body: NodeBody, _: str = Depends(csrf)) -> dict[str, bool]:
    with database.connection() as db:
        if not db.execute("SELECT 1 FROM nodes WHERE id=?", (node_id,)).fetchone():
            raise HTTPException(404, "Node not found")
        db.execute("UPDATE nodes SET name=?, description=?, updated_at=? WHERE id=?", (body.name.strip(), body.description, database.now_iso(), node_id))
        db.execute("DELETE FROM node_addresses WHERE node_id=?", (node_id,))
        try:
            for address in body.addresses:
                db.execute("INSERT INTO node_addresses(node_id, address) VALUES (?, ?)", (node_id, address.strip()))
            _set_node_groups(db, node_id, body.group_ids)
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Address is already assigned to another node")
        db.execute("UPDATE events SET node_id=NULL WHERE node_id=?", (node_id,))
        database.refresh_node_links(db, node_id)
    return {"ok": True}


@app.delete("/api/nodes/{node_id}")
async def delete_node(node_id: int, _: str = Depends(csrf)) -> dict[str, bool]:
    with database.connection() as db:
        db.execute("DELETE FROM nodes WHERE id=?", (node_id,))
    return {"ok": True}


@app.post("/api/groups")
async def create_group(body: GroupBody, _: str = Depends(csrf)) -> dict[str, int]:
    try:
        with database.connection() as db:
            cur = db.execute("INSERT INTO groups(name, description) VALUES (?, ?)", (body.name.strip(), body.description))
            return {"id": int(cur.lastrowid)}
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Group name already exists")


@app.put("/api/groups/{group_id}")
async def update_group(group_id: int, body: GroupBody, _: str = Depends(csrf)) -> dict[str, bool]:
    with database.connection() as db:
        cur = db.execute("UPDATE groups SET name=?, description=? WHERE id=?", (body.name.strip(), body.description, group_id))
        if not cur.rowcount:
            raise HTTPException(404, "Group not found")
    return {"ok": True}


@app.delete("/api/groups/{group_id}")
async def delete_group(group_id: int, _: str = Depends(csrf)) -> dict[str, bool]:
    with database.connection() as db:
        db.execute("DELETE FROM groups WHERE id=?", (group_id,))
    return {"ok": True}


@app.get("/api/status")
async def status(_: str = Depends(authorized)) -> dict[str, Any]:
    with database.connection() as db:
        count = db.execute("SELECT count(*) FROM events").fetchone()[0]
        latest = db.execute("SELECT max(received_at) FROM events").fetchone()[0]
        db_size = sum(p.stat().st_size for p in database.DB_PATH.parent.glob(database.DB_PATH.name + "*"))
    from .mailpit import mailpit_status
    return {"event_count": count, "latest_event_at": latest, "db_bytes": db_size,
            "mailpit_configured": bool(os.getenv("MAILPIT_API_URL")), "mailpit_last_success": mailpit_status.get("last_success"),
            "mailpit_error": mailpit_status.get("last_error"), "poll_seconds": float(os.getenv("MAILPIT_POLL_SECONDS", "2"))}


def _export_rows(filters: dict[str, Any], include_raw: bool) -> list[dict[str, Any]]:
    where, params = _where(filters.get("from"), filters.get("to"), filters.get("q"), filters.get("kind"),
                           filters.get("node_id"), filters.get("group_id"), filters.get("source_ip"))
    with database.connection() as db:
        rows = db.execute(f"SELECT e.* FROM events e{where} ORDER BY e.received_at, e.id", params).fetchall()
    return [_event_dict(row, include_raw=include_raw) for row in rows]


@app.get("/api/export")
async def export_events(format: str = Query("json", pattern="^(json|csv)$"), include_raw: bool = True,
                        from_: str | None = Query(None, alias="from"), to: str | None = None,
                        q: str | None = None, kind: list[str] | None = Query(None),
                        node_id: list[int] | None = Query(None), group_id: list[int] | None = Query(None),
                        source_ip: list[str] | None = Query(None), _: str = Depends(authorized)) -> Response:
    filters = {"from": from_, "to": to, "q": q, "kind": kind, "node_id": node_id,
               "group_id": group_id, "source_ip": source_ip}
    items = _export_rows(filters, include_raw)
    generated = datetime.now(UTC).isoformat()
    manifest = {"generated_at": generated, "tool_version": "0.1.0", "filters": filters,
                "record_count": len(items), "include_raw": include_raw}
    if format == "json":
        content = json.dumps({"manifest": manifest, "events": items}, ensure_ascii=False, indent=2)
        suffix, media = "json", "application/json; charset=utf-8"
    else:
        out = io.StringIO(newline="")
        fields = ["id", "kind", "received_at", "event_at", "source_ip", "node_id", "summary", "message", "parsed"]
        if include_raw:
            fields.extend(["raw_base64", "raw_text"])
        writer = csv.DictWriter(out, fieldnames=["_manifest", *fields])
        writer.writeheader()
        writer.writerow({"_manifest": json.dumps(manifest, ensure_ascii=False)})
        for item in items:
            row = {k: item.get(k, "") for k in fields}
            row["parsed"] = json.dumps(row["parsed"], ensure_ascii=False)
            writer.writerow(row)
        content, suffix, media = out.getvalue(), "csv", "text/csv; charset=utf-8"
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return Response(content, media_type=media, headers={"Content-Disposition": f'attachment; filename="log-view-{generated[:10]}.{suffix}"', "X-Content-SHA256": digest})


@app.post("/api/admin/backup")
async def create_backup(_: str = Depends(csrf)) -> Response:
    # SQLite's backup API needs a database connection as destination; stream via a temporary database file.
    temp = database.DB_PATH.parent / f"backup-{secrets.token_hex(5)}.sqlite3"
    try:
        with database.connection() as source:
            dest = sqlite3.connect(temp)
            source.backup(dest)
            dest.close()
        content = temp.read_bytes()
    finally:
        temp.unlink(missing_ok=True)
    digest = hashlib.sha256(content).hexdigest()
    return Response(content, media_type="application/vnd.sqlite3", headers={"Content-Disposition": 'attachment; filename="log-view-backup.sqlite3"', "X-Content-SHA256": digest})


@app.post("/api/admin/restore")
async def restore_backup(file: UploadFile = File(...), confirm: bool = Form(False), _: str = Depends(csrf)) -> dict[str, Any]:
    if not confirm:
        raise HTTPException(400, "confirm=true is required to restore")
    if not file.filename or not file.filename.endswith((".sqlite3", ".db")):
        raise HTTPException(400, "Upload a .sqlite3 or .db backup")
    content = await file.read(512 * 1024 * 1024 + 1)
    if len(content) > 512 * 1024 * 1024:
        raise HTTPException(413, "Backup exceeds 512 MiB limit")
    tmp = database.DB_PATH.parent / f"restore-{secrets.token_hex(5)}.sqlite3"
    try:
        tmp.write_bytes(content)
        check = sqlite3.connect(tmp)
        integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
        table = check.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='events'").fetchone()
        check.close()
        if integrity != "ok" or not table:
            raise HTTPException(400, "Invalid SQLite backup")
        safety_path = database.DB_PATH.parent / f"pre-restore-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.sqlite3"
        with database.connection() as dest:
            safety = sqlite3.connect(safety_path)
            dest.backup(safety)
            safety.close()
            source = sqlite3.connect(tmp)
            source.backup(dest)
            source.close()
        return {"ok": True, "sha256": hashlib.sha256(content).hexdigest(), "previous_database": safety_path.name}
    finally:
        tmp.unlink(missing_ok=True)


@app.post("/api/admin/retention")
async def delete_events(body: DeleteBody, dry_run: bool = True, _: str = Depends(csrf)) -> dict[str, Any]:
    if not body.before:
        raise HTTPException(400, "before is required")
    clauses, params = ["received_at < ?"], [body.before]
    if body.kind:
        clauses.append("kind=?")
        params.append(body.kind)
    where = " AND ".join(clauses)
    with database.connection() as db:
        count = db.execute(f"SELECT count(*) FROM events WHERE {where}", params).fetchone()[0]
        if not dry_run:
            if not body.confirm:
                raise HTTPException(400, "confirm=true is required to delete")
            db.execute(f"DELETE FROM events WHERE {where}", params)
    return {"dry_run": dry_run, "deleted_or_matching": count}


@app.get("/api/admin/retention/default")
async def retention_default(_: str = Depends(authorized)) -> dict[str, Any]:
    days = max(1, int(os.getenv("LOG_RETENTION_DAYS", "30")))
    return {"days": days, "cutoff": (datetime.now(UTC) - timedelta(days=days)).isoformat()}

