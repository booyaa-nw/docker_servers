import hashlib
import hmac
from html import escape
import json
import os
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer

CONFIG = json.loads(Path("/config/settings.json").read_text(encoding="utf-8"))
USERNAME = os.getenv("NETRIG_ADMIN_USERNAME", CONFIG.get("admin_username", "admin"))
PASSWORD = os.environ["NETRIG_ADMIN_PASSWORD"]
SECRET = os.environ["NETRIG_SECRET"]
SESSION_TTL = int(CONFIG.get("session_ttl_seconds", 28800))
serializer = URLSafeTimedSerializer(SECRET, salt="netrig-session")
app = FastAPI(title="NetRig", docs_url=None, redoc_url=None, openapi_url=None)

LOGIN = """<!doctype html><html lang=ja><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>NetRig | ログイン</title><style>
:root{font:16px system-ui,'Meiryo UI',sans-serif;color:#172534;background:#f3f6fa}*{box-sizing:border-box}body{min-height:100vh;margin:0;display:grid;place-items:center;background:radial-gradient(ellipse at 15% 5%,#dceafb,transparent 45%),#f3f6fa}.card{width:min(420px,calc(100% - 32px));padding:36px;background:white;border:1px solid #dce5ef;border-radius:20px;box-shadow:0 24px 70px #16345216}.brand{font-weight:800;letter-spacing:.12em;color:#174e76}.mark{display:grid;place-items:center;width:48px;height:48px;margin-bottom:24px;background:#e8f2fa;border-radius:14px;color:#14567f;font-weight:800}h1{margin:8px 0;font-size:28px}p{color:#66788a;line-height:1.6}.field{display:grid;gap:7px;margin:20px 0}.field input{padding:12px;border:1px solid #cfdbe6;border-radius:9px;font:inherit}.field input:focus{outline:3px solid #1d78a52b;border-color:#217ca7}button{width:100%;padding:12px;border:0;border-radius:9px;background:#145b82;color:white;font:inherit;font-weight:700;cursor:pointer}button:hover{background:#104c6d}.error{color:#b42318}.sub{margin-top:20px;font-size:12px}</style><body><main class=card><div class=mark>NR</div><div class=brand>NETRIG</div><h1>おかえりなさい</h1><p>ネットワーク運用ポータルにログインしてください。</p>{error}<form method=post action='/login?return={return_to}'><label class=field>ユーザー名<input name=username autocomplete=username required autofocus value='{username}'></label><label class=field>パスワード<input name=password type=password autocomplete=current-password required></label><button>ログイン</button></form><p class=sub>認証後、利用可能なプラグインにアクセスできます。</p></main></body></html>"""


def valid(request: Request) -> bool:
    token = request.cookies.get("netrig_session", "")
    try:
        return serializer.loads(token, max_age=SESSION_TTL) == USERNAME
    except BadSignature:
        return False


def login_page(error: str, return_to: str, username: str) -> str:
    return (LOGIN.replace("{error}", error)
            .replace("{return_to}", quote(return_to, safe="/"))
            .replace("{username}", escape(username, quote=True)))


@app.get("/auth/check")
def auth_check(request: Request):
    if not valid(request):
        return Response(status_code=401)
    signature = hmac.new(SECRET.encode(), USERNAME.encode(), hashlib.sha256).hexdigest()
    return Response(status_code=204, headers={"X-NetRig-User": USERNAME, "X-NetRig-Signature": signature})


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, return_to: str = "/"):
    if valid(request):
        target = return_to if return_to.startswith("/") and not return_to.startswith("//") and "\\" not in return_to else "/"
        return RedirectResponse(target, 303)
    safe_return = return_to if return_to.startswith("/") and not return_to.startswith("//") and "\\" not in return_to else "/"
    return login_page("", safe_return, "")


@app.post("/login", response_class=HTMLResponse)
def login(request: Request, username: str = Form(...), password: str = Form(...), return_to: str = "/"):
    if not hmac.compare_digest(username.encode(), USERNAME.encode()) or not hmac.compare_digest(password.encode(), PASSWORD.encode()):
        return HTMLResponse(login_page("<p class=error>ユーザー名またはパスワードが違います。</p>", return_to, username), status_code=401)
    target = return_to if return_to.startswith("/") and not return_to.startswith("//") and "\\" not in return_to else "/"
    response = RedirectResponse(target, 303)
    response.set_cookie("netrig_session", serializer.dumps(USERNAME), httponly=True, samesite="lax", secure=request.url.scheme == "https", max_age=SESSION_TTL, path="/")
    return response


@app.api_route("/logout", methods=["GET", "POST"])
def logout():
    response = RedirectResponse("/login", 303)
    response.delete_cookie("netrig_session", path="/")
    return response


@app.get("/", response_class=HTMLResponse)
def portal(request: Request):
    if not valid(request):
        return RedirectResponse("/login?return=%2F", 303)
    return """<!doctype html><html lang=ja><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>NetRig</title><style>
:root{font:15px system-ui,'Meiryo UI',sans-serif;color:#172534;background:#f3f6fa}*{box-sizing:border-box}body{margin:0}header{display:flex;align-items:center;justify-content:space-between;padding:24px max(24px,calc((100vw - 1120px)/2));background:#123b57;color:white}header small{color:#afc9da;letter-spacing:.14em;font-weight:700}h1{font-size:28px;margin:5px 0}header p{margin:0;color:#c7d9e6}.logout{background:transparent;color:white;border:1px solid #668198;border-radius:8px;padding:9px 14px;cursor:pointer}main{max-width:1120px;margin:auto;padding:36px 24px}.intro{display:flex;align-items:end;justify-content:space-between;margin-bottom:18px}.intro h2{margin:0;font-size:20px}.intro p{margin:0;color:#708092}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}.card{display:flex;flex-direction:column;min-height:218px;padding:24px;border:1px solid #dce5ed;background:white;border-radius:16px;text-decoration:none;color:inherit;box-shadow:0 5px 22px #1538560a;transition:.16s}.card:hover{transform:translateY(-3px);border-color:#5c91b3;box-shadow:0 12px 30px #15385616}.icon{display:grid;place-items:center;width:48px;height:48px;border-radius:14px;background:#e9f3f9;color:#145b82;font-size:13px;font-weight:800}.card h3{margin:18px 0 5px;font-size:19px}.card p{margin:0;color:#687a8b;line-height:1.65}.open{margin-top:auto;padding-top:20px;color:#17618d;font-weight:700}footer{padding:28px 0;color:#91a0ad;font-size:12px}@media(max-width:680px){header{padding:20px}.grid{grid-template-columns:1fr}main{padding:26px 16px}.intro{display:block}.intro p{margin-top:5px}}
</style><header><div><small>NETWORK OPERATIONS</small><h1>NetRig</h1><p>ネットワーク検証と運用の統合ポータル</p></div><form method=post action=/logout><button class=logout>ログアウト</button></form></header><main><section class=intro><h2>プラグイン</h2><p>利用するツールを選択してください</p></section><section class=grid><a class=card href=/ldap/><span class=icon>LDAP</span><h3>LDAP 管理</h3><p>LDAPユーザー、グループ、Dynamic VLAN、ドメインを管理します。</p><span class=open>プラグインを開く →</span></a><a class=card href=/log-view/><span class=icon>LOGS</span><h3>ログビュー</h3><p>Syslog、SNMP Trap、Mailpitのイベントを検索・確認します。</p><span class=open>プラグインを開く →</span></a></section><footer>NetRig · Raspberry Pi / Docker Compose</footer></main></html>"""
