"""nw-ldapadmin: NW機器 RADIUS 認証用 LDAP 管理 Web UI"""
import hmac
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from ldap3.utils.dn import escape_rdn
from starlette.middleware.sessions import SessionMiddleware

from . import config, csvio, domain, radiuspolicy
from .directory import Directory, DirectoryError, check_name

HERE = Path(__file__).parent
app = FastAPI(title="nw-ldapadmin", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(SessionMiddleware, secret_key=config.session_secret(), same_site="strict",
                   max_age=8 * 3600, session_cookie="nwldapadmin")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")


class LoginRequired(Exception):
    pass


@app.exception_handler(LoginRequired)
async def _login_required(request: Request, exc):
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(config.SettingsMissing)
async def _settings_missing(request: Request, exc):
    return HTMLResponse(f"<h1>nw-ldapadmin</h1><p>{exc}</p>", status_code=503)


# ------------------------------------------------------------------ helpers
def guard(request: Request):
    if not request.session.get("auth"):
        raise LoginRequired()


def flash(request: Request, message: str, kind: str = "ok"):
    request.session.setdefault("flash", []).append({"kind": kind, "message": message})


def back(url: str):
    return RedirectResponse(url, status_code=303)


def page(request: Request, name: str, **ctx):
    cfg = config.read_json(config.SETTINGS) or {}
    ctx.update(
        flashes=request.session.pop("flash", []),
        state=config.apply_state(),
        base_dn=cfg.get("base_dn", ""),
        domain=config.base_to_domain(cfg.get("base_dn", "")),
        admin_name=cfg.get("admin_rdn", "cn=admin").split("=", 1)[-1],
        nav=ctx.get("nav", ""),
    )
    return templates.TemplateResponse(request, name, ctx)


def soft_error(request: Request, e: Exception):
    """反映中（slapd 再起動中）の一時的な接続エラーは表示しない。"""
    if not config.apply_state()["pending"]:
        flash(request, str(e), "error")


def sync_policy(d: Directory | None = None):
    try:
        radiuspolicy.sync((d or Directory()).list_groups())
    except DirectoryError:
        pass


async def form(request: Request):
    f = await request.form()
    return f, (lambda k, default="": (f.get(k) or default).strip() if isinstance(f.get(k), str) else default)


@app.on_event("startup")
def _startup():
    try:
        sync_policy()
    except config.SettingsMissing:
        pass


# ------------------------------------------------------------------ auth
@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request):
    return page(request, "login.html")


@app.post("/login")
async def login_post(request: Request):
    _, v = await form(request)
    cfg = config.load_settings()
    d = Directory(cfg)
    expected = cfg["admin_rdn"].split("=", 1)[-1]
    password = v("password")
    ok = False
    if v("username") == expected:
        r = d.verify_bind(d.admin_dn, password)
        if r is None:   # LDAPに届かない場合は保存済み設定で照合（設定ミスで締め出されないように）
            r = hmac.compare_digest(password.encode(), cfg["admin_password"].encode())
        ok = bool(r)
    if not ok:
        flash(request, "ユーザー名またはパスワードが違います", "error")
        return back("/login")
    request.session["auth"] = True
    return back("/users")


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return back("/login")


@app.get("/")
def root():
    return back("/users")


@app.get("/api/state")
def api_state(request: Request):
    guard(request)
    return JSONResponse(config.apply_state())


# ------------------------------------------------------------------ users
@app.get("/users", response_class=HTMLResponse)
def users_list(request: Request):
    guard(request)
    try:
        users, groups = Directory().overview()
    except DirectoryError as e:
        soft_error(request, e)
        users, groups = [], []
    return page(request, "users.html", users=users, groups=groups, nav="users")


@app.get("/users/new", response_class=HTMLResponse)
def user_new(request: Request):
    guard(request)
    groups = Directory().list_groups()
    return page(request, "user_form.html", user=None, groups=groups, nav="users")


@app.post("/users/new")
async def user_create(request: Request):
    guard(request)
    f, v = await form(request)
    if v("password") != v("password2"):
        flash(request, "確認用パスワードが一致しません", "error")
        return back("/users/new")
    try:
        d = Directory()
        uid = v("uid")
        d.create_user(uid, v("sn"), v("given"), v("mail"), v("password"), f.getlist("groups"), v("vlan"))
        flash(request, f"ユーザー「{uid}」を作成しました")
        return back("/users")
    except DirectoryError as e:
        flash(request, str(e), "error")
        return back("/users/new")


@app.get("/users/{uid}", response_class=HTMLResponse)
def user_edit(request: Request, uid: str):
    guard(request)
    try:
        d = Directory()
        user = d.get_user(uid)
        groups = d.list_groups()
    except DirectoryError as e:
        flash(request, str(e), "error")
        return back("/users")
    return page(request, "user_form.html", user=user, groups=groups, nav="users")


@app.post("/users/{uid}")
async def user_update(request: Request, uid: str):
    guard(request)
    f, v = await form(request)
    new_uid = v("uid") or uid
    try:
        Directory().update_user(uid, sn=v("sn"), given=v("given"), mail=v("mail"),
                                groups=f.getlist("groups"), vlan=v("vlan"), new_uid=new_uid)
        flash(request, f"ユーザー「{new_uid}」を更新しました")
        return back("/users")
    except DirectoryError as e:
        flash(request, str(e), "error")
        return back(f"/users/{uid}")


@app.post("/users/{uid}/password")
async def user_password(request: Request, uid: str):
    guard(request)
    _, v = await form(request)
    if v("password") != v("password2"):
        flash(request, "確認用パスワードが一致しません", "error")
        return back(f"/users/{uid}")
    try:
        Directory().set_user_password(uid, v("password"))
        flash(request, f"ユーザー「{uid}」のパスワードを変更しました")
        return back("/users")
    except DirectoryError as e:
        flash(request, str(e), "error")
        return back(f"/users/{uid}")


@app.post("/users/{uid}/delete")
def user_delete(request: Request, uid: str):
    guard(request)
    try:
        Directory().delete_user(uid)
        flash(request, f"ユーザー「{uid}」を削除しました")
    except DirectoryError as e:
        flash(request, str(e), "error")
    return back("/users")


# ------------------------------------------------------------------ groups
@app.get("/groups", response_class=HTMLResponse)
def groups_list(request: Request):
    guard(request)
    try:
        users, groups = Directory().overview()
    except DirectoryError as e:
        soft_error(request, e)
        users, groups = [], []
    return page(request, "groups.html", groups=groups, users=users, nav="groups")


@app.get("/groups/new", response_class=HTMLResponse)
def group_new(request: Request):
    guard(request)
    users, _ = Directory().overview()
    return page(request, "group_form.html", group=None, users=users, nav="groups")


@app.post("/groups/new")
async def group_create(request: Request):
    guard(request)
    f, v = await form(request)
    try:
        d = Directory()
        cn = v("cn")
        d.create_group(cn, v("description"), f.getlist("members"), v("vlan"), v("priority"))
        sync_policy(d)
        flash(request, f"グループ「{cn}」を作成しました")
        return back("/groups")
    except DirectoryError as e:
        flash(request, str(e), "error")
        return back("/groups/new")


@app.get("/groups/{cn}", response_class=HTMLResponse)
def group_edit(request: Request, cn: str):
    guard(request)
    try:
        d = Directory()
        group = d.get_group(cn)
        users, _ = d.overview()
    except DirectoryError as e:
        flash(request, str(e), "error")
        return back("/groups")
    return page(request, "group_form.html", group=group, users=users, nav="groups")


@app.post("/groups/{cn}")
async def group_update(request: Request, cn: str):
    guard(request)
    f, v = await form(request)
    new_cn = v("cn") or cn
    try:
        d = Directory()
        d.update_group(cn, description=v("description"), members=f.getlist("members"),
                       vlan=v("vlan"), priority=v("priority"), new_cn=new_cn)
        sync_policy(d)
        flash(request, f"グループ「{new_cn}」を更新しました")
        return back("/groups")
    except DirectoryError as e:
        flash(request, str(e), "error")
        return back(f"/groups/{cn}")


@app.post("/groups/{cn}/delete")
def group_delete(request: Request, cn: str):
    guard(request)
    try:
        d = Directory()
        n = d.delete_group(cn)
        sync_policy(d)
        msg = f"グループ「{cn}」を削除しました"
        if n:
            msg += f"（このグループを主グループにしていたユーザーが {n} 人います。認証には影響しません）"
        flash(request, msg)
    except DirectoryError as e:
        flash(request, str(e), "error")
    return back("/groups")


# ------------------------------------------------------------------ Dynamic VLAN
@app.get("/vlan", response_class=HTMLResponse)
def vlan_page(request: Request):
    guard(request)
    try:
        users, groups = Directory().overview()
    except DirectoryError as e:
        soft_error(request, e)
        users, groups = [], []
    from .directory import by_priority
    policy = radiuspolicy.render(groups)
    return page(request, "vlan.html", users=users, groups=by_priority(groups), policy=policy, nav="vlan")


@app.post("/vlan")
async def vlan_save(request: Request):
    guard(request)
    f, v = await form(request)
    try:
        d = Directory()
        changed = 0
        for g in d.list_groups():
            key = g["cn"]
            vlan, prio = v(f"vlan__{key}"), v(f"prio__{key}")
            if vlan != (g["vlan"] or "") or str(prio or "") != str(g["priority"]):
                d.update_group(key, vlan=vlan, priority=prio)
                changed += 1
        sync_policy(d)
        flash(request, f"VLAN設定を保存しました（{changed} グループ更新）" if changed else "変更はありませんでした")
    except DirectoryError as e:
        flash(request, str(e), "error")
    return back("/vlan")


@app.post("/vlan/resync")
def vlan_resync(request: Request):
    guard(request)
    try:
        changed = radiuspolicy.sync(Directory().list_groups())
        flash(request, "FreeRADIUS へ再反映しました" if changed else "ポリシーは最新です（変更なし）")
    except DirectoryError as e:
        flash(request, str(e), "error")
    return back("/vlan")


# ------------------------------------------------------------------ CSV
@app.get("/import", response_class=HTMLResponse)
def import_page(request: Request):
    guard(request)
    return page(request, "import.html", results=None, nav="import")


@app.post("/import", response_class=HTMLResponse)
async def import_post(request: Request):
    guard(request)
    f, v = await form(request)
    upload = f.get("file")
    results, error = None, None
    dry_run = v("dry_run") == "1"
    try:
        if upload is None or not getattr(upload, "filename", ""):
            raise DirectoryError("CSVファイルを選択してください")
        data = await upload.read()
        d = Directory()
        results = csvio.run_import(d, data, v("on_exists", "skip"), v("create_groups") == "1", dry_run)
        if not dry_run:
            sync_policy(d)
    except DirectoryError as e:
        error = str(e)
    if error:
        flash(request, error, "error")
    summary = {}
    for r in results or []:
        summary[r["result"]] = summary.get(r["result"], 0) + 1
    return page(request, "import.html", results=results, summary=summary, dry_run=dry_run,
                opts={"on_exists": v("on_exists", "skip"), "create_groups": v("create_groups") == "1"},
                nav="import")


@app.get("/import/template.csv")
def import_template(request: Request):
    guard(request)
    return Response(csvio.to_csv(csvio.TEMPLATE_ROWS), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="users_template.csv"'})


@app.get("/export/users.csv")
def export_users(request: Request):
    guard(request)
    users, _ = Directory().overview()
    return Response(csvio.export_users(users), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="users.csv"'})


# ------------------------------------------------------------------ settings
@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    guard(request)
    cfg = config.load_settings()
    try:
        accounts = Directory(cfg).list_service_accounts()
    except DirectoryError as e:
        soft_error(request, e)
        accounts = []
    return page(request, "settings.html", accounts=accounts, cfg=cfg, nav="settings")


@app.post("/settings/domain")
async def settings_domain(request: Request):
    guard(request)
    _, v = await form(request)
    if v("confirm") != "1":
        flash(request, "確認チェックを入れてください", "error")
        return back("/settings")
    try:
        n = domain.change_domain(v("domain"), v("rewrite_mail") == "1")
        flash(request, f"ドメイン変更を開始しました（{n} エントリ）。反映完了まで数秒〜十数秒お待ちください")
    except DirectoryError as e:
        flash(request, str(e), "error")
    return back("/settings")


@app.post("/settings/admin")
async def settings_admin(request: Request):
    guard(request)
    _, v = await form(request)
    try:
        cfg = config.load_settings()
        d = Directory(cfg)
        if not d.verify_bind(d.admin_dn, v("current_password")):
            raise DirectoryError("現在の管理者パスワードが違います")
        name = check_name(v("name") or cfg["admin_rdn"].split("=", 1)[-1], "管理者名")
        if v("password") != v("password2"):
            raise DirectoryError("確認用パスワードが一致しません")
        if len(v("password")) < 8:
            raise DirectoryError("パスワードは8文字以上にしてください")
        cfg["admin_rdn"] = f"cn={escape_rdn(name)}"
        cfg["admin_password"] = v("password")
        config.save_settings(cfg)
        flash(request, f"管理者を「{name}」に変更しました。次回ログインから新しい情報を使用してください")
    except DirectoryError as e:
        flash(request, str(e), "error")
    return back("/settings")


@app.post("/settings/accounts/new")
async def account_new(request: Request):
    guard(request)
    _, v = await form(request)
    try:
        if v("password") != v("password2"):
            raise DirectoryError("確認用パスワードが一致しません")
        Directory().create_service_account(v("cn"), v("password"), v("description"))
        flash(request, f"検索用アカウント「{v('cn')}」を作成しました")
    except DirectoryError as e:
        flash(request, str(e), "error")
    return back("/settings")


@app.post("/settings/accounts/{cn}/password")
async def account_password(request: Request, cn: str):
    guard(request)
    _, v = await form(request)
    try:
        if v("password") != v("password2"):
            raise DirectoryError("確認用パスワードが一致しません")
        cfg = config.load_settings()
        d = Directory(cfg)
        dn = d.set_service_password(cn, v("password"))
        if dn.lower() == d.bind_dn.lower():
            cfg["bind_password"] = v("password")
            config.save_settings(cfg)
            flash(request, f"「{cn}」のパスワードを変更し、FreeRADIUS へ反映しました")
        else:
            flash(request, f"「{cn}」のパスワードを変更しました。連携しているNW機器の設定も変更してください")
    except DirectoryError as e:
        flash(request, str(e), "error")
    return back("/settings")


@app.post("/settings/accounts/{cn}/delete")
def account_delete(request: Request, cn: str):
    guard(request)
    try:
        Directory().delete_service_account(cn)
        flash(request, f"検索用アカウント「{cn}」を削除しました")
    except DirectoryError as e:
        flash(request, str(e), "error")
    return back("/settings")


@app.post("/settings/accounts/{cn}/radius")
async def account_radius(request: Request, cn: str):
    guard(request)
    _, v = await form(request)
    try:
        cfg = config.load_settings()
        d = Directory(cfg)
        dn = d.service_dn(cn)
        if not d.verify_bind(dn, v("password")):
            raise DirectoryError("パスワードが違います（そのアカウントでLDAPにログインできません）")
        cfg["bind_rdn"] = dn[: len(dn) - len(d.base) - 1]
        cfg["bind_password"] = v("password")
        config.save_settings(cfg)
        flash(request, f"FreeRADIUS の検索用アカウントを「{cn}」に切り替えました")
    except DirectoryError as e:
        flash(request, str(e), "error")
    return back("/settings")
