#!/usr/bin/env python3
"""
=============================================================
  Garfio — Sistema de Gestão para Restaurantes
  Clique duplo para iniciar. Acesse pelo navegador:
  http://localhost:8080
=============================================================
"""

import http.server
import socketserver
import json
import os
import sys
import uuid
import hashlib
import html
import threading
import webbrowser
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs

# ─── CONFIG ──────────────────────────────────────────────────
PORT = 8080

# ─── MONITOR STATS ───────────────────────────────────────────
import time as _time
_stats = {
    "start_time": _time.time(),
    "requests_total": 0,
    "requests_errors": 0,
    "requests_ok": 0,
    "active_connections": 0,
    "peak_connections": 0,
    "last_error": None,
    "last_error_time": None,
}
_stats_lock = threading.Lock()

def stat_inc(key, val=1):
    with _stats_lock:
        _stats[key] = _stats.get(key, 0) + val
        if key == "active_connections":
            if _stats["active_connections"] > _stats.get("peak_connections", 0):
                _stats["peak_connections"] = _stats["active_connections"]

def stat_set(key, val):
    with _stats_lock:
        _stats[key] = val

def get_uptime_str():
    secs = int(_time.time() - _stats["start_time"])
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    if h: return f"{h}h {m}m {s}s"
    if m: return f"{m}m {s}s"
    return f"{s}s"
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)

# ─── HELPERS ─────────────────────────────────────────────────
def uid(): return str(uuid.uuid4())[:12]
def now(): return datetime.now().isoformat()
def now_br(): return datetime.now().strftime("%d/%m/%Y %H:%M")
def hash_pass(p): return hashlib.sha256(p.encode()).hexdigest()
def fmt_brl(v):
    try: return f"R$ {float(v):.2f}".replace(".", ",")
    except: return "R$ 0,00"

# In-memory cache to avoid repeated disk reads
_cache = {}

_write_lock = threading.Lock()

def save_json(path, data):
    _cache[path] = data  # update cache first
    with _write_lock:
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, separators=(',',':'))
            os.replace(tmp, path)  # atomic replace — prevents corruption
        except Exception as e:
            print(f"[ERRO] save_json {path}: {e}")

def load_json(path, default=None):
    import copy
    if default is None: default = {}
    if path in _cache:
        return copy.deepcopy(_cache[path])  # always return deep copy to prevent cache mutation
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                _cache[path] = data
                return copy.deepcopy(data)
    except: pass
    return default

# ─── DATA PATHS ──────────────────────────────────────────────
MASTER_FILE   = os.path.join(DATA_DIR, "master.json")
PLANS_FILE    = os.path.join(DATA_DIR, "plans.json")
TIMELINE_FILE = os.path.join(DATA_DIR, "timeline.json")

def rest_dir(rid):
    d = os.path.join(DATA_DIR, "restaurants", rid)
    os.makedirs(d, exist_ok=True)
    return d

def rest_file(rid, name):
    return os.path.join(rest_dir(rid), name + ".json")

# ─── INIT DEFAULT DATA ───────────────────────────────────────
def init_data():
    if not os.path.exists(MASTER_FILE):
        save_json(MASTER_FILE, {
            "auth": {"user": "admin", "password": hash_pass("admin123")},
            "settings": {"sysName": "Garfio", "contact": ""},
            "restaurants": []
        })
    if not os.path.exists(PLANS_FILE):
        save_json(PLANS_FILE, [
            {"id": "plan1", "name": "Básico",       "price": 79.90,  "type": "mensal", "desc": "Ideal para pequenos estabelecimentos", "features": ["Sistema de Comandas","Até 3 usuários","Suporte por email"], "maxUsers": 3,  "featured": "no",  "color": "#64748b", "createdAt": now()},
            {"id": "plan2", "name": "Profissional", "price": 149.90, "type": "mensal", "desc": "Para restaurantes em crescimento",      "features": ["Tudo do Básico","Até 10 usuários","Relatórios avançados","Suporte prioritário"], "maxUsers": 10, "featured": "yes", "color": "#6366f1", "createdAt": now()},
            {"id": "plan3", "name": "Enterprise",   "price": 299.90, "type": "mensal", "desc": "Para redes e grandes operações",       "features": ["Usuários ilimitados","Multi-unidade","Gerente de conta","Suporte 24h"], "maxUsers": 999,"featured": "no",  "color": "#0d7c4a", "createdAt": now()},
        ])
    if not os.path.exists(TIMELINE_FILE):
        save_json(TIMELINE_FILE, [])

# ─── SESSION (in-memory) ─────────────────────────────────────
sessions = {}  # token -> {type, rid, expires}
_login_attempts = {}  # ip -> {count, first_at}  (brute-force guard)
_LOGIN_MAX = 10       # max attempts per 5 min
_LOGIN_WINDOW = 300   # seconds

def check_login_rate(ip):
    """Returns True if login is allowed, False if blocked."""
    now_ts = _time.time()
    entry = _login_attempts.get(ip)
    if entry:
        if now_ts - entry["first_at"] > _LOGIN_WINDOW:
            _login_attempts[ip] = {"count": 1, "first_at": now_ts}
            return True
        if entry["count"] >= _LOGIN_MAX:
            return False
        entry["count"] += 1
    else:
        _login_attempts[ip] = {"count": 1, "first_at": now_ts}
    return True

def reset_login_rate(ip):
    _login_attempts.pop(ip, None)

def create_session(stype, rid=None):
    token = uid() + uid()
    sessions[token] = {"type": stype, "rid": rid, "expires": (datetime.now() + timedelta(hours=8)).isoformat()}
    return token

def get_session(token):
    if not token or token not in sessions: return None
    s = sessions[token]
    if datetime.now() > datetime.fromisoformat(s["expires"]): del sessions[token]; return None
    return s

def get_token_from_req(handler):
    cookie = handler.headers.get("Cookie", "")
    for part in cookie.split(";"):
        k, _, v = part.strip().partition("=")
        if k.strip() == "session": return v.strip()
    return None

# ─── HTTP HANDLER ────────────────────────────────────────────
class Handler(http.server.BaseHTTPRequestHandler):

    def log_message(self, fmt, *args): pass  # suppress logs

    def log_error(self, fmt, *args): pass  # suppress error logs

    def handle_error(self, request, client_address):
        pass  # suppress connection errors

    def handle(self):
        try:
            super().handle()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass  # browser closed connection early — normal, ignore

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html, status=200, extra_headers=None):
        body = html.encode("utf-8") if isinstance(html, str) else html
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        if extra_headers:
            for k, v in extra_headers.items(): self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def send_redirect(self, location, extra_headers=None):
        self.send_response(302)
        self.send_header("Location", location)
        if extra_headers:
            for k, v in extra_headers.items(): self.send_header(k, v)
        self.end_headers()

    def read_body(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length == 0: return {}
            raw = self.rfile.read(length)
            return json.loads(raw)
        except: return {}

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type,Cookie")
        self.end_headers()

    def do_GET(self):
        stat_inc("requests_total")
        stat_inc("active_connections")
        _ok = True
        try:
            self._handle_GET()
        except Exception as e:
            _ok = False
            stat_inc("requests_errors")
            stat_set("last_error", str(e))
            stat_set("last_error_time", datetime.now().strftime("%d/%m %H:%M:%S"))
            try: self.send_json({"error": "internal error"}, 500)
            except: pass
        finally:
            stat_inc("active_connections", -1)
            if _ok: stat_inc("requests_ok")

    def _handle_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        token = get_token_from_req(self)
        session = get_session(token)

        # ── Health check (para watchdog) ──
        if path == "/health":
            import os as _os
            mem_mb = 0
            try:
                with open(f"/proc/{_os.getpid()}/status") as f:
                    for line in f:
                        if line.startswith("VmRSS:"):
                            mem_mb = round(int(line.split()[1]) / 1024, 1); break
            except:
                try:
                    import resource
                    mem_mb = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
                except: pass
            self.send_json({"status":"ok","uptime":get_uptime_str(),"requests":_stats["requests_total"],"errors":_stats["requests_errors"],"active":_stats["active_connections"],"mem_mb":mem_mb,"pid":_os.getpid(),"ts":datetime.now().isoformat()})
            return

        # ── Monitor API (requer master) ──
        if path == "/api/monitor":
            if not session or session["type"] != "master":
                self.send_json({"error": "unauthorized"}, 401); return
            import os as _os
            mem_mb = 0
            try:
                with open(f"/proc/{_os.getpid()}/status") as f:
                    for line in f:
                        if line.startswith("VmRSS:"):
                            mem_mb = round(int(line.split()[1]) / 1024, 1); break
            except:
                try:
                    import resource
                    mem_mb = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
                except: pass
            wlog = []
            wlog_path = os.path.join(DATA_DIR, "watchdog.log")
            try:
                if os.path.exists(wlog_path):
                    with open(wlog_path, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                    wlog = [l.strip() for l in lines[-30:] if l.strip()]
            except: pass
            with _stats_lock:
                snap = dict(_stats)
            snap["uptime"] = get_uptime_str()
            snap["mem_mb"] = mem_mb
            snap["pid"] = _os.getpid()
            snap["sessions"] = len(sessions)
            snap["cache_keys"] = len(_cache)
            snap["watchdog_log"] = wlog
            snap["ts"] = datetime.now().isoformat()
            self.send_json(snap)
            return

        # ── Static files ──
        if path.startswith("/static/"):
            fname = path[8:]
            fpath = os.path.join(STATIC_DIR, fname)
            if os.path.exists(fpath):
                with open(fpath, "rb") as f: data = f.read()
                self.send_response(200)
                ct = "text/css" if fname.endswith(".css") else "text/javascript" if fname.endswith(".js") else "application/octet-stream"
                self.send_header("Content-Type", ct)
                self.send_header("Content-Length", len(data))
                self.end_headers()
                self.wfile.write(data)
            else: self.send_json({"error": "not found"}, 404)
            return

        # ── Master panel ──
        if path == "/" or path == "/master":
            if not session or session["type"] != "master":
                self.send_html(render_master_login())
            else:
                self.send_html(render_master_panel())
            return

        # ── Restaurant app ── (only matches /r/{rid} NOT /r/{rid}/sub-paths)
        if path.startswith("/r/") and "/" not in path[3:]:
            rid = path[3:]
            master = load_json(MASTER_FILE)
            rest = next((r for r in master["restaurants"] if r["id"] == rid), None)
            if not rest:
                self.send_html("<h2>Restaurante não encontrado.</h2>", 404)
                return
            if rest.get("status") in ("suspended", "cancelled"):
                self.send_html(render_suspended(rest))
                return
            # Verifica trial expirado
            if rest.get("status") == "trial" and rest.get("trialEnds"):
                try:
                    trial_end = datetime.strptime(rest["trialEnds"], "%Y-%m-%d")
                    if datetime.now() > trial_end:
                        rest["status"] = "trial_expired"
                        master2 = load_json(MASTER_FILE)
                        for rr in master2["restaurants"]:
                            if rr["id"] == rest["id"]:
                                rr["status"] = "trial_expired"
                                break
                        save_json(MASTER_FILE, master2)
                        self.send_html(render_trial_expired(rest))
                        return
                except: pass
            if rest.get("status") == "trial_expired":
                self.send_html(render_trial_expired(rest))
                return
            # Check restaurant session
            rsess = get_session(token)
            if not rsess or rsess["type"] != "restaurant" or rsess["rid"] != rid:
                self.send_html(render_restaurant_login(rest, rid))
            else:
                html = render_restaurant_app(rest, rid)
                self.send_html(html, extra_headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"})
            return

        # ── PWA: manifest ──
        if path.endswith("/manifest.json") and path.startswith("/r/"):
            rid = path[3:].replace("/manifest.json","")
            master = load_json(MASTER_FILE)
            rest = next((r for r in master["restaurants"] if r["id"] == rid), None)
            if not rest: self.send_json({"error":"not found"},404); return
            cfg = load_json(rest_file(rid, "config"), {})
            name = cfg.get("name", rest.get("name","Restaurante"))
            color = cfg.get("color","#6366f1")
            manifest = json.dumps({
                "name": name, "short_name": name[:12],
                "start_url": "/r/"+rid, "display": "standalone",
                "background_color": "#f4f6fb", "theme_color": color,
                "orientation": "portrait",
                "icons": [{"src":"/r/"+rid+"/icon.png","sizes":"192x192","type":"image/png"},
                           {"src":"/r/"+rid+"/icon.png","sizes":"512x512","type":"image/png"}]
            }, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type","application/manifest+json")
            self.send_header("Content-Length",len(manifest))
            self.end_headers(); self.wfile.write(manifest)
            return

        # ── PWA: service worker ──
        if path.endswith("/sw.js") and path.startswith("/r/"):
            rid = path[3:].replace("/sw.js","")
            sw_lines = [
                "const CACHE='garfio-"+rid+"-v1';",
                "const APP_URL='/r/"+rid+"';",
                "const QUEUE_KEY='offline_queue_"+rid+"';",
                "",
                "// Install: cache the app shell",
                "self.addEventListener('install',e=>{",
                "  e.waitUntil(caches.open(CACHE).then(c=>c.add(APP_URL)));",
                "  self.skipWaiting();",
                "});",
                "",
                "// Activate: clean old caches",
                "self.addEventListener('activate',e=>{",
                "  e.waitUntil(caches.keys().then(keys=>Promise.all(",
                "    keys.filter(k=>k!==CACHE).map(k=>caches.delete(k))",
                "  )));",
                "  self.clients.claim();",
                "});",
                "",
                "// Fetch: network first, fallback to cache for GET",
                "self.addEventListener('fetch',e=>{",
                "  const req=e.request;",
                "  if(req.method==='GET'){",
                "    if(req.url.includes('/api/')) return; // never cache API GETs",
                "    e.respondWith(",
                "      fetch(req).then(res=>{",
                "        const clone=res.clone();",
                "        caches.open(CACHE).then(c=>c.put(req,clone));",
                "        return res;",
                "      }).catch(()=>caches.match(req).then(r=>r||caches.match(APP_URL)))",
                "    );",
                "    return;",
                "  }",
                "  // POST: try network, queue if offline",
                "  if(req.method==='POST' && req.url.includes('/api/')){",
                "    e.respondWith(",
                "      req.clone().json().then(body=>{",
                "        return fetch(req).catch(()=>{",
                "          // Store in queue for later sync",
                "          return self.registration.sync ? ",
                "            getQueue().then(q=>{",
                "              q.push({url:req.url,body,at:Date.now()});",
                "              return setQueue(q);",
                "            }).then(()=>new Response(JSON.stringify({ok:true,offline:true}),{headers:{'Content-Type':'application/json'}})) :",
                "            new Response(JSON.stringify({error:'offline'}),{status:503,headers:{'Content-Type':'application/json'}});",
                "        });",
                "      }).catch(()=>fetch(req))",
                "    );",
                "    return;",
                "  }",
                "});",
                "",
                "// Background sync",
                "self.addEventListener('sync',e=>{",
                "  if(e.tag==='flush-queue'){",
                "    e.waitUntil(flushQueue());",
                "  }",
                "});",
                "",
                "function getQueue(){",
                "  return caches.open('q-"+rid+"').then(c=>c.match('/queue')).then(r=>r?r.json():[]).catch(()=>[]);",
                "}",
                "function setQueue(q){",
                "  return caches.open('q-"+rid+"').then(c=>c.put('/queue',new Response(JSON.stringify(q),{headers:{'Content-Type':'application/json'}})));",
                "}",
                "async function flushQueue(){",
                "  const q=await getQueue();",
                "  if(!q.length)return;",
                "  const failed=[];",
                "  for(const item of q){",
                "    try{",
                "      await fetch(item.url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(item.body),credentials:'include'});",
                "    }catch(e){failed.push(item);}",
                "  }",
                "  await setQueue(failed);",
                "  // Notify clients",
                "  const clients=await self.clients.matchAll();",
                "  clients.forEach(c=>c.postMessage({type:'SYNC_DONE',pending:failed.length}));",
                "}"
            ]
            sw_code = "\n".join(sw_lines).encode()
            self.send_response(200)
            self.send_header("Content-Type","application/javascript")
            self.send_header("Content-Length",len(sw_code))
            self.end_headers(); self.wfile.write(sw_code)
            return

        # ── PWA: icon ──
        if path.endswith("/icon.png") and path.startswith("/r/"):
            rid = path[3:].replace("/icon.png","")
            cfg = load_json(rest_file(rid, "config"), {})
            color = cfg.get("color","#6366f1")
            import struct, zlib
            def make_png(color_hex, size=192):
                try:
                    r2=int(color_hex[1:3],16); g2=int(color_hex[3:5],16); b2=int(color_hex[5:7],16)
                except: r2,g2,b2=37,99,235
                raw=b''
                for y in range(size):
                    raw+=b'\x00'
                    for x in range(size):
                        m=size//6
                        if m<x<size-m and m<y<size-m: raw+=bytes([255,255,255])
                        else: raw+=bytes([r2,g2,b2])
                def chunk(name,data):
                    c=zlib.crc32(name+data)&0xffffffff
                    return struct.pack('>I',len(data))+name+data+struct.pack('>I',c)
                ihdr=struct.pack('>IIBBBBB',size,size,8,2,0,0,0)
                idat=zlib.compress(raw)
                return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',ihdr)+chunk(b'IDAT',idat)+chunk(b'IEND',b'')
            icon=make_png(color if (color.startswith("#") and len(color)==7) else "#ff6b35")
            self.send_response(200)
            self.send_header("Content-Type","image/png")
            self.send_header("Content-Length",len(icon))
            self.end_headers(); self.wfile.write(icon)
            return

        # ── API ──
        if path.startswith("/api/"):
            self.handle_api_get(path, parsed, session, token)
            return

        self.send_html("<h2>404 — Página não encontrada</h2>", 404)

    def do_POST(self):
        stat_inc("requests_total")
        stat_inc("active_connections")
        _ok = True
        try:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")
            token = get_token_from_req(self)
            session = get_session(token)
            body = self.read_body()

            # ── Monitor action ──
            if path == "/api/monitor/action":
                if not session or session["type"] != "master":
                    self.send_json({"error":"unauthorized"},401); return
                action = body.get("action","")
                if action == "clear_sessions":
                    with _stats_lock:
                        sessions.clear()
                    self.send_json({"ok":True,"msg":"Sessões limpas. Todos os usuários serão desconectados."})
                elif action == "clear_cache":
                    with _stats_lock:
                        _cache.clear()
                    self.send_json({"ok":True,"msg":"Cache limpo. Próximas leituras virão do disco."})
                elif action == "clear_errors":
                    with _stats_lock:
                        _stats["requests_errors"] = 0
                        _stats["last_error"] = None
                        _stats["last_error_time"] = None
                    self.send_json({"ok":True,"msg":"Contadores de erro zerados."})
                else:
                    self.send_json({"error":"Ação desconhecida"},400)
                return

            self.handle_api_post(path, body, session, token)
        except Exception as e:
            _ok = False
            stat_inc("requests_errors")
            stat_set("last_error", str(e))
            stat_set("last_error_time", datetime.now().strftime("%d/%m %H:%M:%S"))
            try: self.send_json({"error":"internal error"},500)
            except: pass
        finally:
            stat_inc("active_connections", -1)
            if _ok: stat_inc("requests_ok")

    # ── API GET ──────────────────────────────────────────────
    def handle_api_get(self, path, parsed, session, token):

        # Master: get all restaurants
        if path == "/api/master/restaurants":
            if not session or session["type"] != "master": self.send_json({"error":"auth"},401); return
            master = load_json(MASTER_FILE)
            plans = load_json(PLANS_FILE, [])
            rests = master.get("restaurants", [])
            # Enrich with plan name and stats
            for r in rests:
                plan = next((p for p in plans if p["id"] == r.get("planId","")), None)
                r["planName"] = plan["name"] if plan else "—"
                r["planPrice"] = plan["price"] if plan else 0
                # Dias restantes do trial
                if r.get("trialEnds"):
                    try:
                        te = datetime.strptime(r["trialEnds"], "%Y-%m-%d")
                        r["trialDaysLeft"] = max(0, (te - datetime.now()).days)
                    except: r["trialDaysLeft"] = 0
                else:
                    r["trialDaysLeft"] = 0
                # Count comandas
                cfg = load_json(rest_file(r["id"], "config"), {})
                comandas = load_json(rest_file(r["id"], "comandas"), [])
                r["totalComandas"] = len(comandas)
                r["openComandas"] = len([c for c in comandas if c.get("status") != "closed"])
            self.send_json({"restaurants": rests, "total": len(rests)})
            return

        if path == "/api/master/plans":
            if not session or session["type"] != "master": self.send_json({"error":"auth"},401); return
            self.send_json({"plans": load_json(PLANS_FILE, [])})
            return

        if path == "/api/master/timeline":
            if not session or session["type"] != "master": self.send_json({"error":"auth"},401); return
            tl = load_json(TIMELINE_FILE, [])
            self.send_json({"timeline": tl[-50:][::-1]})
            return

        if path == "/api/master/stats":
            if not session or session["type"] != "master": self.send_json({"error":"auth"},401); return
            master = load_json(MASTER_FILE)
            plans = load_json(PLANS_FILE, [])
            rests = master.get("restaurants", [])
            active = [r for r in rests if r.get("status") == "active"]
            mrr = sum(next((p["price"] for p in plans if p["id"] == r.get("planId","")), 0) for r in active)
            self.send_json({
                "total": len(rests), "active": len(active),
                "trial": len([r for r in rests if r.get("status") == "trial"]),
                "suspended": len([r for r in rests if r.get("status") == "suspended"]),
                "cancelled": len([r for r in rests if r.get("status") == "cancelled"]),
                "mrr": mrr
            })
            return

        # Restaurant: get data
        if path.startswith("/api/rest/"):
            parts = path[10:].split("/")
            if not parts: self.send_json({"error":"invalid"},400); return
            rid = parts[0]
            if not session or session["type"] != "restaurant" or session["rid"] != rid:
                self.send_json({"error":"auth"},401); return
            resource = parts[1] if len(parts) > 1 else "config"

            if resource == "config":
                self.send_json(load_json(rest_file(rid, "config"), {}))
            elif resource == "categories":
                self.send_json({"categories": load_json(rest_file(rid, "categories"), [])})
            elif resource == "products":
                self.send_json({"products": load_json(rest_file(rid, "products"), [])})
            elif resource == "users":
                # load_json já retorna deepcopy — seguro mutar sem afetar o cache
                users = load_json(rest_file(rid, "users"), [])
                for u in users: u.pop("password", None)
                self.send_json({"users": users})
            elif resource == "users-raw":
                # Retorna usuários com hashes (para o frontend poder re-salvar sem perder senhas)
                users = load_json(rest_file(rid, "users"), [])
                self.send_json({"users": users})
            elif resource == "comandas":
                self.send_json({"comandas": load_json(rest_file(rid, "comandas"), [])})
            elif resource == "transactions":
                self.send_json({"transactions": load_json(rest_file(rid, "transactions"), [])})
            elif resource == "historico":
                self.send_json({"historico": load_json(rest_file(rid, "historico"), [])})
            elif resource == "mesas":
                self.send_json({"mesas": load_json(rest_file(rid, "mesas"), [])})
            elif resource == "estoque":
                self.send_json({"estoque": load_json(rest_file(rid, "estoque"), {})})
            elif resource == "movimentacoes":
                self.send_json({"movimentacoes": load_json(rest_file(rid, "movimentacoes"), [])})
            else:
                self.send_json({"error":"unknown resource"},404)
            return

        self.send_json({"error":"not found"},404)

    # ── API POST ─────────────────────────────────────────────
    def handle_api_post(self, path, body, session, token):

        # ── AUTH ──
        if path == "/api/master/login":
            client_ip = self.client_address[0]
            if not check_login_rate(client_ip):
                self.send_json({"error":"Muitas tentativas. Aguarde alguns minutos."},429); return
            master = load_json(MASTER_FILE)
            auth = master.get("auth", {})
            if body.get("user") == auth.get("user") and hash_pass(body.get("password","")) == auth.get("password",""):
                reset_login_rate(client_ip)
                tok = create_session("master")
                add_timeline("🔐 Login master realizado", "#6366f1")
                body_bytes = json.dumps({"ok": True}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", len(body_bytes))
                self.send_header("Set-Cookie", f"session={tok}; Path=/; HttpOnly; SameSite=Lax")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body_bytes)
            else:
                self.send_json({"error":"Usuário ou senha incorretos"}, 401)
            return

        if path == "/api/master/logout":
            if token and token in sessions: del sessions[token]
            self.send_response(200)
            self.send_header("Content-Type","application/json")
            self.send_header("Set-Cookie","session=; Path=/; Max-Age=0")
            body_bytes = json.dumps({"ok":True}).encode()
            self.send_header("Content-Length", len(body_bytes))
            self.end_headers()
            self.wfile.write(body_bytes)
            return

        if path.startswith("/api/rest/") and path.endswith("/logout"):
            # Clear session cookie and invalidate session
            if token and token in sessions:
                del sessions[token]
            self.send_response(200)
            self.send_header("Content-Type","application/json")
            self.send_header("Set-Cookie","session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0")
            resp = b'{"ok":true}'
            self.send_header("Content-Length",len(resp))
            self.end_headers()
            self.wfile.write(resp)
            return

        if path.startswith("/api/rest/") and path.endswith("/login"):
            rid = path[10:].replace("/login","")
            client_ip = self.client_address[0]
            if not check_login_rate(client_ip):
                self.send_json({"error":"Muitas tentativas. Aguarde alguns minutos."},429); return
            master = load_json(MASTER_FILE)
            rest = next((r for r in master["restaurants"] if r["id"] == rid), None)
            if not rest: self.send_json({"error":"not found"},404); return
            users = load_json(rest_file(rid, "users"), [])
            raw_pass = body.get("password","")
            # Support both plain and hashed passwords for backwards compat
            def check_pass(stored, given):
                if len(stored) == 64:  # already hashed (sha256 hex)
                    return stored == hash_pass(given)
                return stored == given  # plain text legacy
            user = next((u for u in users if u["id"] == body.get("userId","") and check_pass(u.get("password",""), raw_pass)), None)
            if user:
                reset_login_rate(client_ip)
                tok = create_session("restaurant", rid)
                self.send_response(200)
                self.send_header("Content-Type","application/json")
                self.send_header("Set-Cookie",f"session={tok}; Path=/; HttpOnly; SameSite=Lax")
                resp = json.dumps({"ok":True,"user":{"id":user["id"],"name":user["name"],"role":user["role"],"color":user.get("color","#6366f1")}}).encode()
                self.send_header("Content-Length",len(resp))
                self.end_headers()
                self.wfile.write(resp)
            else:
                self.send_json({"error":"Senha incorreta"},401)
            return

        # ── MASTER: manage restaurants ──
        if path == "/api/master/restaurants":
            if not session or session["type"] != "master": self.send_json({"error":"auth"},401); return
            master = load_json(MASTER_FILE)
            r = body
            r["id"] = uid()
            r["createdAt"] = now()
            if not r.get("name"): self.send_json({"error":"Nome obrigatório"},400); return
            # Trial: 7 dias a partir de hoje
            if not r.get("trialEnds"):
                r["trialEnds"] = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
            if not r.get("status"):
                r["status"] = "trial"
            master["restaurants"].append(r)
            save_json(MASTER_FILE, master)
            # Init restaurant data
            init_restaurant(r["id"], r)
            add_timeline(f"🏪 {r['name']} cadastrado", "#16a34a")
            self.send_json({"ok":True, "id": r["id"]})
            return

        if path.startswith("/api/master/restaurants/"):
            if not session or session["type"] != "master": self.send_json({"error":"auth"},401); return
            rid = path[len("/api/master/restaurants/"):]
            action = ""
            if "/" in rid: rid, _, action = rid.partition("/")
            master = load_json(MASTER_FILE)
            idx = next((i for i,r in enumerate(master["restaurants"]) if r["id"]==rid), None)
            if idx is None: self.send_json({"error":"not found"},404); return
            r = master["restaurants"][idx]

            if action == "delete":
                name = r["name"]
                master["restaurants"].pop(idx)
                save_json(MASTER_FILE, master)
                add_timeline(f"🗑 {name} removido", "#dc2626")
                self.send_json({"ok":True})
            elif action == "status":
                new_status = body.get("status")
                r["status"] = new_status
                r["updatedAt"] = now()
                master["restaurants"][idx] = r
                save_json(MASTER_FILE, master)
                msgs = {"active":"✅","suspended":"⏸","cancelled":"❌","trial":"🔁","trial_expired":"⏰"}
                add_timeline(f"{msgs.get(new_status,'•')} {r['name']} → {new_status}", "#6366f1")
                self.send_json({"ok":True})
            else:
                # Update
                for k,v in body.items():
                    if k not in ("id","createdAt"): r[k] = v
                r["updatedAt"] = now()
                master["restaurants"][idx] = r
                save_json(MASTER_FILE, master)
                add_timeline(f"✏️ {r['name']} atualizado", "#d97706")
                self.send_json({"ok":True})
            return

        # ── MASTER: plans ──
        if path == "/api/master/plans":
            if not session or session["type"] != "master": self.send_json({"error":"auth"},401); return
            plans = load_json(PLANS_FILE, [])
            body["id"] = uid()
            body["createdAt"] = now()
            plans.append(body)
            save_json(PLANS_FILE, plans)
            self.send_json({"ok":True})
            return

        if path.startswith("/api/master/plans/"):
            if not session or session["type"] != "master": self.send_json({"error":"auth"},401); return
            pid = path[len("/api/master/plans/"):]
            action = ""
            if "/" in pid: pid, _, action = pid.partition("/")
            plans = load_json(PLANS_FILE, [])
            idx = next((i for i,p in enumerate(plans) if p["id"]==pid), None)
            if idx is None: self.send_json({"error":"not found"},404); return
            if action == "delete":
                plans.pop(idx); save_json(PLANS_FILE, plans); self.send_json({"ok":True})
            else:
                for k,v in body.items():
                    if k not in ("id","createdAt"): plans[idx][k] = v
                save_json(PLANS_FILE, plans); self.send_json({"ok":True})
            return

        # ── MASTER: settings ──
        if path == "/api/master/settings":
            if not session or session["type"] != "master": self.send_json({"error":"auth"},401); return
            master = load_json(MASTER_FILE)
            if "newPassword" in body:
                master["auth"]["password"] = hash_pass(body["newPassword"])
            if "settings" in body:
                master["settings"].update(body["settings"])
            save_json(MASTER_FILE, master)
            self.send_json({"ok":True})
            return

        # ── RESTAURANT: save data ──
        if path.startswith("/api/rest/"):
            parts = path[10:].split("/")
            if len(parts) < 2: self.send_json({"error":"invalid"},400); return
            rid = parts[0]; resource = parts[1]
            if not session or session["type"] != "restaurant" or session["rid"] != rid:
                self.send_json({"error":"auth"},401); return

            if resource == "config":
                save_json(rest_file(rid, "config"), body)
                self.send_json({"ok":True})
            elif resource in ("categories","products","comandas","transactions","historico","mesas","estoque","movimentacoes"):
                data = body.get(resource, body)
                if data is None: self.send_json({"error":"no data"},400); return
                save_json(rest_file(rid, resource), data)
                self.send_json({"ok":True})
            elif resource == "users":
                users = body.get("users", body)
                if isinstance(users, list):
                    # Carrega usuários atuais do disco para preservar senhas não enviadas
                    existing = {u["id"]: u for u in load_json(rest_file(rid, "users"), [])}
                    for u in users:
                        p = u.get("password", "")
                        if not p:
                            # Sem senha nova: restaura a senha existente do disco
                            if u["id"] in existing:
                                u["password"] = existing[u["id"]].get("password", "")
                        elif len(p) != 64:
                            # Senha em texto puro: faz o hash
                            u["password"] = hash_pass(p)
                        # Se len == 64: já é hash, mantém como está
                save_json(rest_file(rid, "users"), users)
                self.send_json({"ok":True})
            else:
                self.send_json({"error":"unknown"},404)
            return

        self.send_json({"error":"not found"},404)


# ─── RESTAURANT INIT ─────────────────────────────────────────
def init_restaurant(rid, rest_data):
    """Create default data files for a new restaurant - only if not exist"""
    # Config - only create if missing
    if not os.path.exists(rest_file(rid, "config")):
        cfg = {"name": rest_data.get("name","Restaurante"), "slogan": "", "logo": None, "color": "#6366f1", "configured": False}
        save_json(rest_file(rid, "config"), cfg)
    # Data files - only create if missing
    for resource in ("categories", "products", "comandas", "transactions"):
        if not os.path.exists(rest_file(rid, resource)):
            save_json(rest_file(rid, resource), [])
    # Users - only create if missing
    if not os.path.exists(rest_file(rid, "users")):
        admin_pass = rest_data.get("adminPass", "1234")
        hashed = hash_pass(admin_pass) if len(admin_pass) != 64 else admin_pass
        users = [{"id": "adm1", "name": "Administrador", "role": "admin", "password": hashed, "color": rest_data.get("planColor", "#6366f1"), "createdAt": now()}]
        save_json(rest_file(rid, "users"), users)

def add_timeline(msg, color="#6366f1"):
    tl = load_json(TIMELINE_FILE, [])
    tl.append({"id": uid(), "msg": msg, "color": color, "at": now()})
    if len(tl) > 200: tl = tl[-200:]
    save_json(TIMELINE_FILE, tl)


# ─── HTML RENDERS ────────────────────────────────────────────
def render_master_login():
    return """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Garfio — Painel Master</title>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link href="https://fonts.googleapis.com/css2?family=Syne:wght@700;800&family=DM+Sans:wght@300;400;500;600&display=swap" rel="stylesheet"/>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --ink:#0a0a14;--ink2:#11111e;
  --acc:#6366f1;--acc2:#a78bfa;
  --glow:rgba(124,111,255,.18);
  --txt:rgba(255,255,255,.88);
  --muted:rgba(255,255,255,.32);
  --border:rgba(255,255,255,.07);
  --card:rgba(255,255,255,.04);
}
html,body{height:100%;font-family:'DM Sans',system-ui,sans-serif;background:var(--ink);color:var(--txt);overflow:hidden}

/* ── NOISE OVERLAY ── */
body::before{content:'';position:fixed;inset:0;
  background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='.03'/%3E%3C/svg%3E");
  pointer-events:none;z-index:0}

/* ── GRADIENT ORBS ── */
.orb{position:fixed;border-radius:50%;pointer-events:none;filter:blur(80px)}
.o1{width:520px;height:520px;background:radial-gradient(circle,rgba(124,111,255,.22) 0%,transparent 70%);top:-120px;left:-80px}
.o2{width:380px;height:380px;background:radial-gradient(circle,rgba(167,139,250,.16) 0%,transparent 70%);bottom:-80px;right:30%}
.o3{width:280px;height:280px;background:radial-gradient(circle,rgba(99,102,241,.12) 0%,transparent 70%);top:40%;right:-60px}

/* ── LAYOUT ── */
.wrap{position:relative;z-index:1;min-height:100vh;display:flex}
.left{flex:1;display:flex;flex-direction:column;
  padding:52px 56px;border-right:1px solid var(--border);gap:0}
.right{width:480px;flex-shrink:0;display:flex;align-items:center;justify-content:center;padding:52px 52px;background:rgba(255,255,255,.015);border-left:1px solid var(--border)}

/* ── LEFT PANEL ── */
.brand{display:flex;align-items:center;gap:12px;margin-bottom:auto}
.brand-icon{width:42px;height:42px;background:linear-gradient(135deg,var(--acc),var(--acc2));
  border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:1.2rem;
  box-shadow:0 0 28px rgba(124,111,255,.4)}
.brand-name{font-family:'Syne',sans-serif;font-size:1.25rem;font-weight:800;letter-spacing:-.01em}
.hero{margin-top:0;margin-bottom:0;padding:32px 0;flex:1;display:flex;flex-direction:column;justify-content:center}
.hero-tag{display:inline-flex;align-items:center;gap:7px;
  background:rgba(124,111,255,.12);border:1px solid rgba(124,111,255,.2);
  border-radius:30px;padding:5px 14px;font-size:.72rem;font-weight:600;
  color:var(--acc2);letter-spacing:.05em;text-transform:uppercase;margin-bottom:22px}
.hero-tag::before{content:'';width:6px;height:6px;border-radius:50%;
  background:var(--acc2);animation:blink 2s ease-in-out infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.2}}
.hero h1{font-family:'Syne',sans-serif;font-size:3.2rem;font-weight:800;
  line-height:1.05;letter-spacing:-.03em;margin-bottom:16px;
  background:linear-gradient(160deg,#fff 40%,rgba(255,255,255,.38));
  -webkit-background-clip:text;-webkit-text-fill-color:transparent}
.hero p{font-size:.92rem;color:var(--muted);line-height:1.75;max-width:460px}

/* ── FEATURES ── */
.feats{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin-top:40px;max-width:560px}
.feat{display:flex;align-items:flex-start;gap:10px;
  background:var(--card);border:1px solid var(--border);
  border-radius:12px;padding:16px 18px;transition:.2s}
.feat:hover{background:rgba(124,111,255,.06);border-color:rgba(124,111,255,.15)}
.feat-ic{width:28px;height:28px;border-radius:7px;
  background:linear-gradient(135deg,rgba(124,111,255,.2),rgba(167,139,250,.1));
  display:flex;align-items:center;justify-content:center;font-size:.82rem;flex-shrink:0;margin-top:1px}
.feat-body strong{display:block;font-size:.78rem;font-weight:600;color:rgba(255,255,255,.7);margin-bottom:1px}
.feat-body span{font-size:.7rem;color:var(--muted)}

/* ── STATS STRIP ── */
.stats{display:flex;gap:0;margin-top:32px;max-width:560px;
  background:var(--card);border:1px solid var(--border);border-radius:12px;overflow:hidden}
.stat{flex:1;padding:14px 16px;text-align:center;border-right:1px solid var(--border)}
.stat:last-child{border-right:none}
.stat-v{font-family:'Syne',sans-serif;font-size:1.45rem;font-weight:700;
  background:linear-gradient(135deg,var(--acc),var(--acc2));
  -webkit-background-clip:text;-webkit-text-fill-color:transparent}
.stat-l{font-size:.62rem;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;margin-top:2px}

/* ── FORM PANEL ── */
.form-wrap{width:100%}
.form-hd{margin-bottom:36px}
.form-hd .pill{display:inline-flex;align-items:center;gap:6px;
  background:rgba(124,111,255,.1);border:1px solid rgba(124,111,255,.18);
  border-radius:20px;padding:4px 11px 4px 7px;font-size:.7rem;color:var(--acc2);
  font-weight:600;margin-bottom:14px;letter-spacing:.04em}
.form-hd h2{font-family:'Syne',sans-serif;font-size:2rem;font-weight:800;
  letter-spacing:-.03em;color:#fff;margin-bottom:8px}
.form-hd p{font-size:.84rem;color:var(--muted)}

/* ── ERROR ── */
.err{display:none;align-items:center;gap:9px;
  background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.18);
  border-radius:10px;padding:11px 14px;margin-bottom:18px}
.err-ic{width:20px;height:20px;background:rgba(239,68,68,.2);border-radius:50%;
  display:flex;align-items:center;justify-content:center;font-size:.7rem;flex-shrink:0}
.err span{font-size:.82rem;color:#fca5a5;font-weight:500}

/* ── FIELD ── */
.fg{margin-bottom:16px}
.fg label{display:block;font-size:.68rem;font-weight:700;color:rgba(255,255,255,.28);
  text-transform:uppercase;letter-spacing:.09em;margin-bottom:8px}
.iw{position:relative}
.iw svg{position:absolute;left:14px;top:50%;transform:translateY(-50%);
  width:15px;height:15px;opacity:.3;pointer-events:none;flex-shrink:0}
.iw input{width:100%;padding:13px 14px 13px 42px;
  background:rgba(255,255,255,.04);
  border:1.5px solid rgba(255,255,255,.08);
  border-radius:10px;font-size:.9rem;color:#fff;font-family:inherit;
  transition:all .2s;letter-spacing:.01em}
.iw input:focus{outline:none;
  border-color:rgba(124,111,255,.5);
  background:rgba(124,111,255,.06);
  box-shadow:0 0 0 3px rgba(124,111,255,.1)}
.iw input::placeholder{color:rgba(255,255,255,.15)}
.eye-btn{position:absolute;right:13px;top:50%;transform:translateY(-50%);
  background:none;border:none;cursor:pointer;padding:4px;
  color:rgba(255,255,255,.22);transition:.15s;display:flex;align-items:center}
.eye-btn:hover{color:rgba(255,255,255,.6)}

/* ── BUTTON ── */
.btn{width:100%;padding:14px;margin-top:4px;
  background:linear-gradient(135deg,var(--acc) 0%,var(--acc2) 100%);
  color:#fff;border:none;border-radius:10px;
  font-size:.92rem;font-weight:700;cursor:pointer;font-family:inherit;
  transition:all .22s;letter-spacing:.01em;position:relative;overflow:hidden;
  box-shadow:0 4px 24px rgba(124,111,255,.3)}
.btn::before{content:'';position:absolute;inset:0;
  background:linear-gradient(135deg,rgba(255,255,255,.08),transparent);
  opacity:0;transition:.2s}
.btn:hover:not(:disabled)::before{opacity:1}
.btn:hover:not(:disabled){transform:translateY(-1px);box-shadow:0 8px 32px rgba(124,111,255,.45)}
.btn:active{transform:translateY(0)}
.btn:disabled{opacity:.55;cursor:not-allowed}
.btn-inner{display:flex;align-items:center;justify-content:center;gap:8px}

/* ── FOOTER NOTE ── */
.foot-note{margin-top:24px;display:flex;align-items:center;gap:8px;
  padding:12px 14px;background:var(--card);border:1px solid var(--border);border-radius:10px}
.foot-dot{width:7px;height:7px;border-radius:50%;background:#10b981;flex-shrink:0;
  box-shadow:0 0 8px rgba(16,185,129,.5);animation:pulse2 2.5s ease-in-out infinite}
@keyframes pulse2{0%,100%{transform:scale(1)}50%{transform:scale(1.3)}}
.foot-note span{font-size:.73rem;color:var(--muted)}

@media(max-width:800px){
  .left{display:none}
  .right{width:100%;padding:32px 22px}
  html,body{overflow:auto}
}
</style>
</head>
<body>
<div class="orb o1"></div>
<div class="orb o2"></div>
<div class="orb o3"></div>
<div class="wrap">

  <!-- LEFT -->
  <div class="left">
    <div class="brand">
      <div class="brand-icon">🍴</div>
      <span class="brand-name">Garfio</span>
    </div>

    <div class="hero">
      <div class="hero-tag">🍴 Garfio</div>
      <h1>Painel de<br/>Controle<br/>Master</h1>
      <p>Gerencie todos os seus restaurantes, planos e assinaturas do <strong>Garfio</strong> em um único painel centralizado.</p>

      <div style="display:flex;align-items:center;gap:14px;margin:26px 0 22px;padding:15px 17px;background:rgba(99,102,241,.07);border:1px solid rgba(99,102,241,.14);border-radius:14px">
        <div style="width:38px;height:38px;background:linear-gradient(135deg,rgba(99,102,241,.3),rgba(167,139,250,.2));border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:1rem;flex-shrink:0">🔐</div>
        <div>
          <div style="font-size:.78rem;font-weight:700;color:rgba(255,255,255,.8);margin-bottom:2px">Acesso exclusivo ao proprietário</div>
          <div style="font-size:.71rem;color:var(--muted);line-height:1.5">Ative trials, gerencie clientes e monitore o servidor em tempo real</div>
        </div>
      </div>

      <div class="feats">
        <div class="feat">
          <div class="feat-ic">🍽️</div>
          <div class="feat-body"><strong>Multi-restaurante</strong><span>Vários estabelecimentos</span></div>
        </div>
        <div class="feat">
          <div class="feat-ic">📊</div>
          <div class="feat-body"><strong>Relatórios</strong><span>Dados em tempo real</span></div>
        </div>
        <div class="feat">
          <div class="feat-ic">🔒</div>
          <div class="feat-body"><strong>100% Local</strong><span>Seus dados ficam aqui</span></div>
        </div>
        <div class="feat">
          <div class="feat-ic">⚡</div>
          <div class="feat-body"><strong>Sem internet</strong><span>Funciona offline</span></div>
        </div>
      </div>

      <div class="stats">
        <div class="stat"><div class="stat-v" id="nr">—</div><div class="stat-l">Restaurantes</div></div>
        <div class="stat"><div class="stat-v">24h</div><div class="stat-l">Disponível</div></div>
        <div class="stat"><div class="stat-v">∞</div><div class="stat-l">Comandas</div></div>
      </div>
    </div>
  </div>

  <!-- RIGHT -->
  <div class="right">
    <div class="form-wrap">
      <div class="form-hd">
        <div class="pill">👑 <span>Acesso Restrito</span></div>
        <h2>Bem-vindo<br/>de volta</h2>
        <p>Entre com suas credenciais de administrador master</p>
      </div>

      <div id="err" class="err">
        <div class="err-ic">⚠</div>
        <span id="etxt">Credenciais inválidas</span>
      </div>

      <div class="fg">
        <label>Usuário</label>
        <div class="iw">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
          <input type="text" id="u" value="admin" placeholder="admin"
            onkeydown="if(event.key==='Enter')document.getElementById('p').focus()"/>
        </div>
      </div>

      <div class="fg">
        <label>Senha</label>
        <div class="iw">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
          <input type="password" id="p" placeholder="••••••••"
            onkeydown="if(event.key==='Enter')doLogin()"/>
          <button class="eye-btn" onclick="togglePw()" type="button" title="Mostrar/ocultar senha">
            <svg id="eye-ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:15px;height:15px">
              <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>
            </svg>
          </button>
        </div>
      </div>

      <button class="btn" id="btn" onclick="doLogin()">
        <span class="btn-inner" id="btn-inner">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="width:15px;height:15px"><path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"/><polyline points="10 17 15 12 10 7"/><line x1="15" y1="12" x2="3" y2="12"/></svg>
          Entrar no Painel
        </span>
      </button>

      <div class="foot-note">
        <div class="foot-dot"></div>
        <span>Servidor local ativo • Dados armazenados no seu computador</span>
      </div>
    </div>
  </div>

</div>
<script>
async function doLogin(){
  const u=document.getElementById('u').value.trim();
  const p=document.getElementById('p').value;
  const btn=document.getElementById('btn');
  const bi=document.getElementById('btn-inner');
  btn.disabled=true;
  bi.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="width:15px;height:15px;animation:spin .8s linear infinite"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg> Verificando...';
  document.getElementById('err').style.display='none';
  try{
    const r=await fetch('/api/master/login',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({user:u,password:p}),credentials:'include'});
    const d=await r.json();
    if(d.ok){
      bi.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="width:15px;height:15px"><polyline points="20 6 9 17 4 12"/></svg> Entrando...';
      setTimeout(()=>location.reload(),400);
    } else {
      document.getElementById('etxt').textContent=d.error||'Credenciais inválidas';
      document.getElementById('err').style.display='flex';
      bi.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="width:15px;height:15px"><path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"/><polyline points="10 17 15 12 10 7"/><line x1="15" y1="12" x2="3" y2="12"/></svg> Entrar no Painel';
      btn.disabled=false;
    }
  }catch(e){
    document.getElementById('etxt').textContent='Erro de conexão com o servidor';
    document.getElementById('err').style.display='flex';
    bi.innerHTML='Entrar no Painel'; btn.disabled=false;
  }
}
function togglePw(){
  const i=document.getElementById('p');
  i.type=i.type==='password'?'text':'password';
}
fetch('/api/master/restaurants',{credentials:'include'})
  .then(r=>r.json()).then(d=>{
    const e=document.getElementById('nr');
    if(e&&d.restaurants) e.textContent=d.restaurants.length;
  }).catch(()=>{});
document.addEventListener('keydown',e=>{if(e.key==='Enter'&&document.activeElement.tagName!='INPUT')doLogin();});
const style=document.createElement('style');
style.textContent='@keyframes spin{to{transform:rotate(360deg)}}';
document.head.appendChild(style);
</script>
</body>
</html>"""


def render_master_panel():
    return """<!DOCTYPE html><html lang="pt-BR"><head>
<meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Garfio — Painel Master</title>
<style>
*,*::before,*::after{margin:0;padding:0;box-sizing:border-box}
:root{--p:#6366f1;--pd:#4f46e5;--pl:#eef2ff;--s:#16a34a;--sl:#dcfce7;--w:#d97706;--wl:#fef3c7;--d:#dc2626;--dl:#fee2e2;
  --bg:#f8fafc;--wh:#fff;--tx:#0f172a;--mu:#64748b;--br:#e2e8f0;--sh:0 2px 16px rgba(0,0,0,.09);--r:12px;--rs:8px}
html,body{height:100%;font-family:'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--tx)}
button{cursor:pointer;border:none;font-family:inherit}input,select,textarea{font-family:inherit}
/* TOAST */
#toast{position:fixed;top:18px;right:18px;z-index:9999;display:flex;flex-direction:column;gap:8px;pointer-events:none}
.toast{padding:11px 16px;border-radius:var(--rs);font-size:.87rem;font-weight:500;box-shadow:var(--sh);animation:tin .3s ease;max-width:300px;pointer-events:auto}
.ts{background:#16a34a;color:#fff}.te{background:#dc2626;color:#fff}.ti{background:#6366f1;color:#fff}
@keyframes tin{from{opacity:0;transform:translateX(100%)}to{opacity:1;transform:translateX(0)}}
/* LAYOUT */
.layout{display:flex;min-height:100vh}
.sidebar{width:240px;background:linear-gradient(170deg,#0f172a 0%,#1a1040 100%);color:#fff;display:flex;flex-direction:column;position:fixed;top:0;left:0;bottom:0;z-index:150;box-shadow:4px 0 24px rgba(0,0,0,.18)}
.sb-brand{padding:24px 18px 20px;border-bottom:1px solid rgba(255,255,255,.07);display:flex;align-items:center;gap:11px}
.sb-brand-ic{width:36px;height:36px;background:linear-gradient(135deg,#6366f1,#a78bfa);border-radius:9px;display:flex;align-items:center;justify-content:center;font-size:1.1rem;box-shadow:0 0 16px rgba(99,102,241,.4);flex-shrink:0}
.sb-brand-txt h2{font-size:.95rem;font-weight:800;letter-spacing:-.01em}.sb-brand-txt p{font-size:.68rem;color:rgba(255,255,255,.35);margin-top:1px}
.sb-nav{flex:1;padding:16px 10px;display:flex;flex-direction:column;gap:2px;overflow-y:auto}
.sb-section{font-size:.58rem;font-weight:700;color:rgba(255,255,255,.22);text-transform:uppercase;letter-spacing:.1em;padding:8px 11px 4px;margin-top:6px}
.ni{display:flex;align-items:center;gap:10px;padding:10px 12px;border-radius:10px;color:rgba(255,255,255,.55);
  font-size:.82rem;font-weight:500;cursor:pointer;transition:all .18s;border:none;background:none;width:100%;text-align:left}
.ni:hover{background:rgba(255,255,255,.07);color:rgba(255,255,255,.9)}
.ni.active{background:linear-gradient(135deg,rgba(99,102,241,.35),rgba(99,102,241,.15));color:#fff;box-shadow:inset 0 0 0 1px rgba(99,102,241,.3)}
.ni .ic{font-size:1rem;flex-shrink:0;width:22px;text-align:center}
.nb{margin-left:auto;background:#dc2626;color:#fff;padding:1px 7px;border-radius:20px;font-size:.66rem;font-weight:700}
.sb-foot{padding:14px 12px;border-top:1px solid rgba(255,255,255,.07);display:flex;align-items:center;gap:10px}
.sb-av{width:34px;height:34px;border-radius:50%;background:linear-gradient(135deg,#6366f1,#a78bfa);display:flex;align-items:center;justify-content:center;font-size:.85rem;font-weight:800;flex-shrink:0;box-shadow:0 0 10px rgba(99,102,241,.35)}
.sb-info{flex:1;min-width:0}.sb-info strong{display:block;font-size:.8rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.sb-info span{font-size:.67rem;color:rgba(255,255,255,.32)}
.btn-out{background:rgba(255,255,255,.07);color:rgba(255,255,255,.5);padding:5px 9px;border-radius:7px;font-size:.74rem;cursor:pointer;border:1px solid rgba(255,255,255,.08);transition:all .18s;white-space:nowrap}
.btn-out:hover{background:rgba(255,255,255,.13);color:#fff}
.main{margin-left:240px;flex:1;display:flex;flex-direction:column;min-height:100vh}
.topbar{background:#fff;border-bottom:1px solid var(--br);padding:0 28px;height:60px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:120;box-shadow:0 1px 6px rgba(0,0,0,.05)}
.topbar h1{font-size:1.02rem;font-weight:700;color:#0f172a;letter-spacing:-.01em}
.tb-r{display:flex;align-items:center;gap:12px}
.tb-stat{font-size:.76rem;color:var(--mu);background:#f8fafc;padding:5px 10px;border-radius:7px;border:1px solid var(--br)}
.tb-stat strong{color:var(--p);font-size:.88rem;font-weight:700}
.content{flex:1;padding:26px 28px;background:#f8fafc}
/* PANELS */
.panel{display:none}.panel.active{display:block}
/* BTNS */
.btn{display:inline-flex;align-items:center;justify-content:center;gap:5px;padding:8px 15px;border-radius:var(--rs);font-size:.83rem;font-weight:600;transition:all .2s;cursor:pointer;border:none}
.bp{background:var(--p);color:#fff}.bp:hover{background:var(--pd)}
.bs{background:var(--s);color:#fff}.bw{background:var(--w);color:#fff}.bd{background:var(--d);color:#fff}
.bg{background:#f1f5f9;color:var(--tx);border:1px solid var(--br)}.bg:hover{background:#e2e8f0}
.bsm{padding:5px 10px;font-size:.76rem}.blg{padding:12px 20px;font-size:.93rem}
/* CARD CSS above */
/* STATS */
.sr{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:24px}
.sc{background:#fff;border-radius:var(--r);padding:20px;border:1px solid var(--br);transition:.2s;position:relative;overflow:hidden}
.sc::after{content:'';position:absolute;bottom:0;left:0;right:0;height:3px;background:var(--sc-bar,var(--p));opacity:.18}
.sc:hover{box-shadow:0 4px 20px rgba(0,0,0,.08);transform:translateY(-1px)}
.sc .si{font-size:1.7rem;margin-bottom:8px;display:block}
.sc .sv{font-size:1.9rem;font-weight:900;line-height:1;letter-spacing:-.02em}
.sc .sl{font-size:.73rem;color:var(--mu);margin-top:5px;font-weight:500;text-transform:uppercase;letter-spacing:.04em}
/* CARD */
.card{background:#fff;border-radius:var(--r);padding:20px;box-shadow:0 1px 4px rgba(0,0,0,.05);border:1px solid var(--br)}
.card h3{font-size:.87rem;font-weight:700;margin-bottom:14px;color:#0f172a}
/* TABLE */
.tw{overflow-x:auto;border-radius:var(--r);border:1px solid var(--br)}
table{width:100%;border-collapse:collapse;background:#fff}
thead{background:#f8fafc}
th{padding:11px 14px;text-align:left;font-size:.76rem;font-weight:700;color:var(--mu);text-transform:uppercase;letter-spacing:.04em;border-bottom:1px solid var(--br)}
td{padding:12px 14px;font-size:.84rem;border-bottom:1px solid #f1f5f9;vertical-align:middle}
tr:last-child td{border-bottom:none}tr:hover td{background:#fafbfc}
.tda{display:flex;gap:5px;align-items:center}
/* BADGE */
.badge{display:inline-flex;align-items:center;padding:2px 8px;border-radius:20px;font-size:.7rem;font-weight:700}
.ba{background:var(--sl);color:var(--s)}.bsu{background:var(--wl);color:var(--w)}
.bca{background:var(--dl);color:var(--d)}.btr{background:var(--pl);color:var(--p)}.bpl{background:#f1f5f9;color:#475569}
/* MODAL */
.mo{position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:200;display:none;align-items:center;justify-content:center;padding:18px}
.mo.active{display:flex}
.modal{background:#fff;border-radius:16px;width:100%;max-width:520px;padding:26px;max-height:92vh;overflow-y:auto;animation:min .3s ease;box-shadow:0 20px 60px rgba(0,0,0,.2)}
@keyframes min{from{opacity:0;transform:scale(.95)}to{opacity:1;transform:scale(1)}}
.mh{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px}
.mh h3{font-size:1.05rem;font-weight:700}
.mc{background:#f1f5f9;border:none;width:28px;height:28px;border-radius:50%;font-size:.95rem;cursor:pointer;display:flex;align-items:center;justify-content:center}
.fg{margin-bottom:13px}
.fg label{display:block;font-size:.79rem;font-weight:600;color:var(--mu);margin-bottom:5px}
.fg input,.fg select,.fg textarea{width:100%;padding:10px 12px;border:2px solid var(--br);border-radius:var(--rs);font-size:.91rem;transition:border .2s;background:#fafafa}
.fg input:focus,.fg select:focus{outline:none;border-color:var(--p)}
.fg textarea{resize:vertical;min-height:72px}
.fr2{display:grid;grid-template-columns:1fr 1fr;gap:11px}
.mf{display:flex;gap:9px;margin-top:18px;justify-content:flex-end}
/* PLANS GRID */
.pg{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:14px}
.pc{background:#fff;border-radius:var(--r);padding:18px;border:2px solid var(--br);position:relative;transition:all .2s}
.pc:hover{border-color:var(--p);transform:translateY(-2px);box-shadow:var(--sh)}
.pc.feat{border-color:var(--p);background:var(--pl)}
.pba{position:absolute;top:-10px;left:50%;transform:translateX(-50%);background:var(--p);color:#fff;padding:2px 11px;border-radius:20px;font-size:.68rem;font-weight:700;white-space:nowrap}
.pn{font-size:1.05rem;font-weight:800;margin-bottom:3px}
.ppr{font-size:1.9rem;font-weight:800;color:var(--p);line-height:1}
.ppr span{font-size:.85rem;font-weight:400;color:var(--mu)}
.pd{font-size:.8rem;color:var(--mu);margin:9px 0;line-height:1.5}
/* DETAIL SHEET */
.ds{position:fixed;top:0;right:-420px;width:420px;height:100vh;background:#fff;z-index:100;
  box-shadow:-4px 0 30px rgba(0,0,0,.12);transition:right .35s;overflow-y:auto;display:flex;flex-direction:column}
.ds.open{right:0}
.ds-backdrop{display:none;position:fixed;inset:0;background:rgba(0,0,0,.3);z-index:99}
.ds.open ~ .ds-backdrop{display:block}
.dsh{padding:20px 18px 16px;border-bottom:1px solid var(--br);display:flex;align-items:flex-start;gap:11px}
.dsc{background:#f1f5f9;border:none;width:30px;height:30px;border-radius:50%;cursor:pointer;font-size:.95rem;display:flex;align-items:center;justify-content:center;flex-shrink:0;margin-top:1px}
.dsb{flex:1;padding:18px}
.dss{margin-bottom:18px}
.dss h4{font-size:.76rem;font-weight:700;color:var(--mu);text-transform:uppercase;letter-spacing:.05em;margin-bottom:9px}
.dsr{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #f8fafc;font-size:.85rem}
.dsr .dk{color:var(--mu)}.dsr .dv{font-weight:600}
.dsa{padding:14px 18px;border-top:1px solid var(--br);display:flex;flex-direction:column;gap:7px}
.dsa .btn{width:100%;justify-content:flex-start}
/* OVERLAY */
#dsov{display:none}
/* SEARCH */
.sb2{display:flex;gap:9px;align-items:center;margin-bottom:16px;flex-wrap:wrap}
.sb2 input{padding:8px 13px;border:2px solid var(--br);border-radius:var(--rs);font-size:.85rem;min-width:210px;transition:border .2s}
.sb2 input:focus{outline:none;border-color:var(--p)}
.sb2 select{padding:8px 11px;border:2px solid var(--br);border-radius:var(--rs);font-size:.85rem}
/* TL */
.tl{display:flex;flex-direction:column;gap:0}
.tli{display:flex;gap:11px;padding:9px 0;border-bottom:1px solid #f1f5f9}
.tld{width:7px;height:7px;border-radius:50%;flex-shrink:0;margin-top:5px}
.tlm{font-size:.82rem;font-weight:500}.tlt{font-size:.71rem;color:var(--mu);margin-top:1px}
/* EMPTY */
.empty{text-align:center;padding:44px 18px;color:var(--mu)}
.empty .icon{font-size:2.7rem;display:block;margin-bottom:9px}
.empty p{font-size:.87rem}
@media(max-width:900px){.sidebar{display:none}.main{margin-left:0}.sr{grid-template-columns:repeat(2,1fr)}}
::-webkit-scrollbar{width:4px}::-webkit-scrollbar-thumb{background:#cbd5e1;border-radius:4px}
</style></head><body>
<div id="toast"></div>
<div id="js-err-bar" style="display:none;position:fixed;top:0;left:0;right:0;background:#dc2626;color:#fff;padding:10px 16px;font-size:.85rem;font-weight:600;z-index:99999;text-align:center"></div>

<!-- APP -->
<div class="layout">
  <aside class="sidebar">
    <div class="sb-brand">
      <div class="sb-brand-ic">🍴</div>
      <div class="sb-brand-txt"><h2>Garfio</h2><p>Painel Master</p></div>
    </div>
    <nav class="sb-nav">
      <div class="sb-section">Visão Geral</div>
      <button class="ni active" data-p="dashboard" onclick="SP('dashboard',this)"><span class="ic">📊</span>Dashboard</button>
      <button class="ni" data-p="restaurants" onclick="SP('restaurants',this)"><span class="ic">🏪</span>Restaurantes<span class="nb" id="nb">0</span></button>
      <div class="sb-section">Negócio</div>
      <button class="ni" data-p="plans" onclick="SP('plans',this)"><span class="ic">📦</span>Planos & Preços</button>
      <button class="ni" data-p="finance" onclick="SP('finance',this)"><span class="ic">💰</span>Financeiro</button>
      <div class="sb-section">Sistema</div>
      <button class="ni" data-p="monitor" onclick="SP('monitor',this)" id="ni-monitor"><span class="ic">🖥️</span>Monitor</button>
      <button class="ni" data-p="settings" onclick="SP('settings',this)"><span class="ic">⚙️</span>Configurações</button>
    </nav>
    <div class="sb-foot">
      <div class="sb-av">M</div>
      <div class="sb-info"><strong>Master Admin</strong><span>Proprietário</span></div>
      <button class="btn-out" onclick="logout()">Sair</button>
    </div>
  </aside>
  <main class="main">
    <div class="topbar">
      <h1 id="tb-title">Dashboard</h1>
      <div class="tb-r">
        <div id="js-status" style="font-size:.72rem;padding:4px 10px;border-radius:6px;background:#fee2e2;color:#dc2626;font-weight:700">JS: aguardando...</div>
        <div class="tb-stat">MRR <strong id="tb-mrr">R$ 0,00</strong></div>
        <button class="btn bp bsm" onclick="SP('restaurants');openNewRest()">＋ Restaurante</button>
      </div>
    </div>
    <div class="content">

      <!-- DASHBOARD -->
      <div class="panel active" id="panel-dashboard">
        <div class="sr" id="dash-stats"></div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:18px">
          <div class="card"><h3 style="font-size:.87rem;font-weight:700;margin-bottom:12px">🏪 Restaurantes Recentes</h3><div id="dash-rr"></div></div>
          <div class="card"><h3 style="font-size:.87rem;font-weight:700;margin-bottom:12px">📈 Atividade</h3><div class="tl" id="dash-tl"></div></div>
        </div>
        <div class="card"><h3 style="font-size:.87rem;font-weight:700;margin-bottom:12px">📊 Por Plano</h3><div id="dash-pd" style="display:flex;gap:10px;flex-wrap:wrap"></div></div>
      </div>

      <!-- RESTAURANTS -->
      <div class="panel" id="panel-restaurants">
        <div class="sb2">
          <input type="text" id="rs" placeholder="🔍 Buscar restaurante..." oninput="loadRestaurants()"/>
          <select id="rf-s" onchange="loadRestaurants()"><option value="">Todos status</option><option value="active">Ativo</option><option value="trial">Trial</option><option value="trial_expired">Trial Expirado</option><option value="suspended">Suspenso</option><option value="cancelled">Cancelado</option></select>
          <select id="rf-p" onchange="loadRestaurants()"><option value="">Todos planos</option></select>
          <button class="btn bp" onclick="openNewRest()">＋ Novo Restaurante</button>
        </div>
        <div class="tw"><table>
          <thead><tr><th>Restaurante</th><th>Plano</th><th>Status</th><th>Vencimento</th><th>Comandas</th><th>Link</th><th>Ações</th></tr></thead>
          <tbody id="rest-tb"></tbody>
        </table></div>
      </div>

      <!-- PLANS -->
      <div class="panel" id="panel-plans">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
          <div><h2 style="font-size:1rem;font-weight:700">Planos & Serviços</h2><p style="font-size:.8rem;color:var(--mu)">Gerencie os planos oferecidos</p></div>
          <button class="btn bp" onclick="openPlanModal()">＋ Novo Plano</button>
        </div>
        <div class="pg" id="plans-grid"></div>
      </div>

      <!-- FINANCE -->
      <div class="panel" id="panel-finance">
        <div class="sr" id="fin-stats" style="grid-template-columns:repeat(3,1fr)"></div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">
          <div class="card"><h3 style="font-size:.87rem;font-weight:700;margin-bottom:11px">📋 Próximas Cobranças</h3><div id="fin-up"></div></div>
          <div class="card"><h3 style="font-size:.87rem;font-weight:700;margin-bottom:11px">📅 Atividade Recente</h3><div class="tl" id="fin-tl"></div></div>
        </div>
      </div>

      <!-- MONITOR -->
      <div class="panel" id="panel-monitor">
        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:20px" id="mon-stats-grid"></div>
        <div style="display:grid;grid-template-columns:1.3fr 1fr;gap:14px;margin-bottom:14px">
          <div class="card">
            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:13px">
              <h3 style="font-size:.87rem;font-weight:700">📋 Log do Watchdog</h3>
              <span style="font-size:.72rem;color:var(--mu)" id="mon-wdog-status">Verificando...</span>
            </div>
            <div id="mon-wlog" style="font-family:monospace;font-size:.75rem;color:#374151;background:#f8fafc;border-radius:8px;padding:12px;max-height:280px;overflow-y:auto;border:1px solid var(--br);line-height:1.7"></div>
          </div>
          <div class="card">
            <h3 style="font-size:.87rem;font-weight:700;margin-bottom:13px">⚡ Ações Rápidas</h3>
            <div style="display:flex;flex-direction:column;gap:9px">
              <button class="btn bg" style="justify-content:flex-start;gap:9px" onclick="monAction('clear_sessions')">
                <span>🔑</span><div style="text-align:left"><div style="font-size:.83rem;font-weight:600">Limpar Sessões</div><div style="font-size:.72rem;color:var(--mu)">Desconecta todos os usuários</div></div>
              </button>
              <button class="btn bg" style="justify-content:flex-start;gap:9px" onclick="monAction('clear_cache')">
                <span>🗂️</span><div style="text-align:left"><div style="font-size:.83rem;font-weight:600">Limpar Cache</div><div style="font-size:.72rem;color:var(--mu)">Força releitura dos arquivos</div></div>
              </button>
              <button class="btn bw" style="justify-content:flex-start;gap:9px" onclick="monAction('clear_errors')">
                <span>🧹</span><div style="text-align:left"><div style="font-size:.83rem;font-weight:600">Zerar Contadores de Erro</div><div style="font-size:.72rem;color:var(--mu)">Reinicia estatísticas de erro</div></div>
              </button>
            </div>
            <div style="margin-top:16px;padding-top:13px;border-top:1px solid var(--br)">
              <div style="font-size:.77rem;font-weight:700;color:var(--mu);margin-bottom:8px;text-transform:uppercase;letter-spacing:.04em">Atualização automática</div>
              <div style="display:flex;align-items:center;gap:9px">
                <div id="mon-auto-dot" style="width:8px;height:8px;border-radius:50%;background:#16a34a;box-shadow:0 0 0 3px rgba(22,163,74,.2)"></div>
                <span style="font-size:.8rem;color:var(--mu)">A cada <strong>5 segundos</strong></span>
                <button class="btn bg bsm" onclick="toggleMonAuto()" id="mon-auto-btn">Pausar</button>
              </div>
            </div>
            <div style="margin-top:14px;padding:11px;background:#fef3c7;border-radius:8px;border:1px solid #fde68a">
              <div style="font-size:.77rem;font-weight:700;color:#92400e;margin-bottom:3px">⚠️ Watchdog</div>
              <div style="font-size:.76rem;color:#78350f;line-height:1.5">Mantenha o <strong>watchdog.py</strong> rodando em segundo plano para reinicialização automática.</div>
            </div>
          </div>
        </div>
        <div class="card">
          <h3 style="font-size:.87rem;font-weight:700;margin-bottom:13px">📊 Últimas Requisições</h3>
          <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:11px" id="mon-req-detail"></div>
        </div>
      </div>

      <!-- SETTINGS -->
      <div class="panel" id="panel-settings">
        <div style="max-width:480px;display:flex;flex-direction:column;gap:14px">
          <div class="card">
            <h3 style="font-size:.87rem;font-weight:700;margin-bottom:13px">🔐 Alterar Senha Master</h3>
            <div class="fg"><label>Nova Senha</label><input type="password" id="sp1" placeholder="Mínimo 4 caracteres"/></div>
            <div class="fg"><label>Confirmar</label><input type="password" id="sp2" placeholder="Repita a senha"/></div>
            <button class="btn bp" onclick="changePass()">Salvar Senha</button>
          </div>
          <div class="card">
            <h3 style="font-size:.87rem;font-weight:700;margin-bottom:13px">🏢 Sistema</h3>
            <div class="fg"><label>Nome do Sistema</label><input type="text" id="sn" placeholder="Garfio"/></div>
            <div class="fg"><label>Contato / Email</label><input type="text" id="sc" placeholder="seu@email.com"/></div>
            <button class="btn bp" onclick="saveSettings()">Salvar</button>
          </div>
          <div class="card" style="border-color:#fee2e2">
            <h3 style="font-size:.87rem;font-weight:700;margin-bottom:9px;color:#dc2626">⚠️ Zona de Perigo</h3>
            <p style="font-size:.8rem;color:var(--mu);margin-bottom:11px">Exportar backup de todos os dados do sistema.</p>
            <button class="btn bg" onclick="exportData()">📥 Exportar Backup JSON</button>
          </div>
        </div>
      </div>

    </div>
  </main>
</div>

<!-- Detail Sheet -->
<div class="ds" id="ds">
  <div class="dsh">
    <button class="dsc" onclick="closeDS()">✕</button>
    <div style="flex:1"><div style="font-size:1.1rem;font-weight:800" id="ds-title"></div><div style="font-size:.78rem;color:var(--mu);margin-top:2px" id="ds-sub"></div></div>
    <div id="ds-badge"></div>
  </div>
  <div class="dsb" id="ds-body"></div>
  <div class="dsa" id="ds-actions"></div>
</div>
<div class="ds-backdrop" onclick="closeDS()"></div>

<!-- MODALS -->
<div class="mo" id="mo-rest">
  <div class="modal">
    <div class="mh"><h3 id="mr-title">🏪 Novo Restaurante</h3><button class="mc" onclick="CM('mo-rest')">✕</button></div>
    <div class="fr2">
      <div class="fg"><label>Nome *</label><input type="text" id="r-name" placeholder="Nome do restaurante"/></div>
      <div class="fg"><label>Responsável *</label><input type="text" id="r-owner" placeholder="Nome do dono"/></div>
    </div>
    <div class="fr2">
      <div class="fg"><label>Telefone</label><input type="text" id="r-phone" placeholder="(11) 99999-9999"/></div>
      <div class="fg"><label>Email</label><input type="email" id="r-email" placeholder="email@exemplo.com"/></div>
    </div>
    <div class="fg"><label>Endereço</label><input type="text" id="r-addr" placeholder="Rua, número, cidade"/></div>
    <div class="fr2">
      <div class="fg"><label>Plano *</label><select id="r-plan"></select></div>
      <div class="fg"><label>Status</label>
        <select id="r-status"><option value="trial">Trial</option><option value="active">Ativo</option><option value="suspended">Suspenso</option><option value="cancelled">Cancelado</option></select>
      </div>
    </div>
    <div class="fr2">
      <div class="fg"><label>Senha ADM do restaurante</label><input type="text" id="r-pass" placeholder="ex: 1234" value="1234"/></div>
      <div class="fg"><label>Vencimento</label><input type="date" id="r-due"/></div>
    </div>
    <div class="fg"><label>Observações</label><textarea id="r-notes" placeholder="Notas internas..."></textarea></div>
    <input type="hidden" id="r-id"/>
    <div class="mf">
      <button class="btn bg" onclick="CM('mo-rest')">Cancelar</button>
      <button class="btn bp" onclick="saveRest()">💾 Salvar</button>
    </div>
  </div>
</div>

<div class="mo" id="mo-plan">
  <div class="modal">
    <div class="mh"><h3 id="mp-title">📦 Novo Plano</h3><button class="mc" onclick="CM('mo-plan')">✕</button></div>
    <div class="fg"><label>Nome do Plano *</label><input type="text" id="p-name" placeholder="Ex: Básico, Pro, Enterprise"/></div>
    <div class="fr2">
      <div class="fg"><label>Valor Mensal (R$) *</label><input type="number" id="p-price" placeholder="0.00" step="0.01"/></div>
      <div class="fg"><label>Tipo de Cobrança</label>
        <select id="p-type"><option value="mensal">Mensal</option><option value="trimestral">Trimestral</option><option value="semestral">Semestral</option><option value="anual">Anual</option></select>
      </div>
    </div>
    <div class="fg"><label>Descrição</label><textarea id="p-desc" placeholder="O que está incluído..."></textarea></div>
    <div class="fg"><label>Recursos (um por linha)</label><textarea id="p-feat" placeholder="Sistema de comandas&#10;Até 5 usuários&#10;Suporte básico"></textarea></div>
    <div class="fr2">
      <div class="fg"><label>Máx. Usuários</label><input type="number" id="p-mu" placeholder="5"/></div>
      <div class="fg"><label>Destaque</label><select id="p-feat2"><option value="no">Normal</option><option value="yes">⭐ Popular</option></select></div>
    </div>
    <div class="fr2">
      <div class="fg"><label>Cor do Plano</label><input type="color" id="p-color" value="#6366f1" style="height:40px;padding:4px"/></div>
    </div>
    <input type="hidden" id="p-id"/>
    <div class="mf">
      <button class="btn bg" onclick="CM('mo-plan')">Cancelar</button>
      <button class="btn bp" onclick="savePlan()">💾 Salvar Plano</button>
    </div>
  </div>
</div>

<div class="mo" id="mo-confirm">
  <div class="modal" style="max-width:360px">
    <div class="mh"><h3 id="cf-title">Confirmar</h3><button class="mc" onclick="CM('mo-confirm')">✕</button></div>
    <p id="cf-msg" style="font-size:.88rem;color:var(--mu);margin-bottom:18px"></p>
    <div class="mf"><button class="btn bg" onclick="CM('mo-confirm')">Cancelar</button><button class="btn bd" id="cf-btn">Confirmar</button></div>
  </div>
</div>

<script>
// ─── ERROR CATCHER ───────────────────────────────────────────
window.onerror = function(msg, src, line, col, err){
  const bar = document.getElementById('js-err-bar');
  if(bar){ bar.style.display='block'; bar.textContent = '⚠️ Erro JS: ' + msg + ' (linha ' + line + ')'; }
  return false;
};
window.addEventListener('unhandledrejection', function(e){
  const bar = document.getElementById('js-err-bar');
  if(bar){ bar.style.display='block'; bar.textContent = '⚠️ Erro JS: ' + (e.reason?.message || e.reason || 'Promise rejeitada'); }
});
// ─── HELPERS ────────────────────────────────────────────────
const $ = id => document.getElementById(id);
function fmt(v){ return 'R$ '+Number(v||0).toFixed(2).replace('.',','); }
function fmtd(d,s=false){ if(!d) return '—'; try{ return s?new Date(d).toLocaleDateString('pt-BR'):new Date(d).toLocaleString('pt-BR'); }catch{ return d; } }
function toast(msg,t='s'){ const c=$('toast'); const el=document.createElement('div'); el.className='toast t'+t; el.textContent=msg; c.appendChild(el); setTimeout(()=>{el.style.opacity='0';el.style.transform='translateX(100%)';el.style.transition='all .3s';setTimeout(()=>el.remove(),300);},2800); }
function OM(id){ $(id).classList.add('active'); }
function CM(id){ $(id).classList.remove('active'); }
function closeAll(){ // safety: closes any stuck overlay
  document.querySelectorAll('.mo.active').forEach(m=>m.classList.remove('active'));
  closeDS();
}
document.addEventListener('keydown',e=>{
  if(e.key==='Escape') closeAll();
});
document.querySelectorAll('.mo').forEach(o=>o.addEventListener('click',e=>{ if(e.target===o) o.classList.remove('active'); }));
async function api(method,url,body){
  const opts={method,headers:{'Content-Type':'application/json'},credentials:'include'};
  if(body) opts.body=JSON.stringify(body);
  try {
    const r=await fetch(url,opts);
    if(!r.ok && r.status===401){ location.reload(); return {}; }
    const text=await r.text();
    try { return JSON.parse(text); } catch(e){ return {error:'Resposta inválida do servidor'}; }
  } catch(e){ return {error:'Erro de conexão: '+e.message}; }
}

// ─── STATE ──────────────────────────────────────────────────
let plans=[], restaurants=[], tl=[];

// ─── INIT ────────────────────────────────────────────────────
async function init(){
  // Mark JS as running
  const jsStatus = document.getElementById('js-status');
  if(jsStatus){ jsStatus.style.background='#dcfce7'; jsStatus.style.color='#16a34a'; jsStatus.textContent='JS: ✅ ativo'; }
  try {
    await loadPlans();
    await loadTimeline();
    await renderDash();
    if(jsStatus){ jsStatus.style.display='none'; }
  } catch(e){ 
    console.error('init error',e);
    if(jsStatus){ jsStatus.style.background='#fee2e2'; jsStatus.style.color='#dc2626'; jsStatus.textContent='JS erro: '+e.message; }
  }
}

async function loadPlans(){
  try {
    const d=await api('GET','/api/master/plans');
    plans=d.plans||[];
    buildPlanSelects();
  } catch(e){ plans=[]; }
}
async function loadStats(){
  try {
    const d=await api('GET','/api/master/stats');
    updateMrr(d.mrr||0);
    return d;
  } catch(e){ return {total:0,active:0,trial:0,suspended:0,cancelled:0,mrr:0}; }
}
async function loadTimeline(){
  try {
    const d=await api('GET','/api/master/timeline');
    tl=d.timeline||[];
  } catch(e){ tl=[]; }
}

function updateMrr(v){ $('tb-mrr').textContent=fmt(v); }
function buildPlanSelects(){
  const sel=$('r-plan'); if(!sel) return;
  sel.innerHTML=plans.map(p=>`<option value="${p.id}">${p.name} — ${fmt(p.price)}/${p.type}</option>`).join('');
  // filter
  const fp=$('rf-p'); if(fp){ fp.innerHTML=`<option value="">Todos planos</option>`+plans.map(p=>`<option value="${p.id}">${p.name}</option>`).join(''); }
}

// ─── PANELS ─────────────────────────────────────────────────
function SP(name,el){
  closeDS(); // always close detail panel when navigating
  document.querySelectorAll('.mo.active').forEach(m=>m.classList.remove('active')); // close any open modals
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.ni').forEach(n=>n.classList.remove('active'));
  $('panel-'+name).classList.add('active');
  if(name==='monitor') startMonitor();
  if(el) el.classList.add('active');
  else{ const nb=document.querySelector(`[data-p="${name}"]`); if(nb) nb.classList.add('active'); }
  const titles={dashboard:'Dashboard',restaurants:'Restaurantes',plans:'Planos & Preços',finance:'Financeiro',settings:'Configurações'};
  $('tb-title').textContent=titles[name]||name;
  if(name==='restaurants'){ buildPlanSelects(); loadRestaurants(); }
  if(name==='plans') renderPlans();
  if(name==='finance') renderFinance();
}

// ─── DASHBOARD ──────────────────────────────────────────────
async function renderDash(){
  const d=await loadStats();
  const nbVal=(d.active||0)+(d.trial||0);
  $('nb').textContent=nbVal;
  $('dash-stats').innerHTML=`
    <div class="sc" style="--sc-bar:#6366f1"><span class="si">🏪</span><div class="sv" style="color:#6366f1">${d.total}</div><div class="sl">Total Restaurantes</div></div>
    <div class="sc" style="--sc-bar:#16a34a"><span class="si">✅</span><div class="sv" style="color:#16a34a">${d.active} <span style="font-size:1rem;color:#6366f1">${d.trial>0?'(+'+d.trial+' trial)':''}</span></div><div class="sl">Ativos</div></div>
    <div class="sc" style="--sc-bar:#d97706"><span class="si">⚠️</span><div class="sv" style="color:#d97706">${d.suspended}</div><div class="sl">Suspensos</div></div>
    <div class="sc" style="--sc-bar:#16a34a"><span class="si">💰</span><div class="sv" style="color:#16a34a">${fmt(d.mrr)}</div><div class="sl">MRR Estimado</div></div>`;
  updateMrr(d.mrr||0);
  // Recent restaurants
  const rd=await api('GET','/api/master/restaurants');
  restaurants=rd.restaurants||[];
  const rec=restaurants.slice(-5).reverse();
  $('dash-rr').innerHTML=rec.length===0?'<p style="font-size:.83rem;color:#64748b">Nenhum restaurante.</p>':
    rec.map(r=>`<div style="display:flex;align-items:center;gap:9px;padding:7px 0;border-bottom:1px solid #f8fafc;cursor:pointer" onclick="openDS('${r.id}')">
      <div style="width:32px;height:32px;border-radius:7px;background:#eef2ff;display:flex;align-items:center;justify-content:center;font-size:.95rem;flex-shrink:0">🏪</div>
      <div style="flex:1"><div style="font-size:.85rem;font-weight:600">${r.name}</div><div style="font-size:.72rem;color:#64748b">${r.planName}</div></div>
      <span class="badge b${stc(r.status)}">${slb(r.status)}</span>
    </div>`).join('');
  // Timeline
  await loadTimeline();
  $('dash-tl').innerHTML=tl.slice(0,8).map(t=>`<div class="tli"><div class="tld" style="background:${t.color||'#6366f1'}"></div><div><div class="tlm">${t.msg}</div><div class="tlt">${fmtd(t.at,true)}</div></div></div>`).join('')||'<p style="font-size:.83rem;color:#64748b">Nenhuma atividade.</p>';
  // Plan dist
  const dist={};restaurants.forEach(r=>{ if(r.planName) dist[r.planName]=(dist[r.planName]||0)+1; });
  $('dash-pd').innerHTML=Object.entries(dist).map(([k,v])=>`<div style="display:flex;align-items:center;gap:7px;padding:7px 12px;background:#f8fafc;border-radius:7px"><span style="font-size:.85rem;font-weight:700">${k}</span><span class="badge ba">${v} rest.</span></div>`).join('')||'<p style="font-size:.83rem;color:#64748b">Sem dados.</p>';
}

// ─── RESTAURANTS ─────────────────────────────────────────────
async function loadRestaurants(){
  const d=await api('GET','/api/master/restaurants');
  restaurants=d.restaurants||[];
  const q=($('rs').value||'').toLowerCase();
  const fs=$('rf-s').value, fp=$('rf-p').value;
  let list=restaurants;
  if(q) list=list.filter(r=>r.name.toLowerCase().includes(q)||(r.owner||'').toLowerCase().includes(q));
  if(fs) list=list.filter(r=>r.status===fs);
  if(fp) list=list.filter(r=>r.planId===fp);
  const tb=$('rest-tb');
  if(list.length===0){ tb.innerHTML=`<tr><td colspan="7"><div class="empty"><span class="icon">🏪</span><p>Nenhum restaurante.</p></div></td></tr>`; return; }
  const host=location.hostname; const port=location.port;
  tb.innerHTML=list.map(r=>{
    const url=`http://${host}:${port}/r/${r.id}`;
    return `<tr>
      <td><div style="font-weight:600">${r.name}</div><div style="font-size:.74rem;color:#64748b">${r.owner}</div></td>
      <td><span class="badge bpl">${r.planName}</span></td>
      <td><span class="badge b${stc(r.status)}">${slb(r.status)}</span></td>
      <td style="font-size:.81rem">${fmtd(r.dueDate,true)}</td>
      <td style="font-size:.81rem"><span style="font-weight:600;color:#6366f1">${r.openComandas}</span> abertas / ${r.totalComandas} total</td>
      <td><a href="${url}" target="_blank" style="font-size:.78rem;color:#6366f1;font-weight:600;text-decoration:none">🔗 Abrir</a></td>
      <td><div class="tda">
        <button class="btn bg bsm" onclick="openDS('${r.id}')">👁</button>
        <button class="btn bg bsm" onclick="editRest('${r.id}')">✏️</button>
        <button class="btn bd bsm" onclick="confirmDel('${r.id}')">🗑</button>
      </div></td>
    </tr>`;
  }).join('');
}

function stc(s){ return {active:'a',trial:'tr',trial_expired:'su',suspended:'su',cancelled:'ca'}[s]||'a'; }
function slb(s){ return {active:'Ativo',trial:'Trial 🔁',trial_expired:'Trial Expirado ⏰',suspended:'Suspenso',cancelled:'Cancelado'}[s]||s; }

function openNewRest(){
  $('r-name').value='';$('r-owner').value='';$('r-phone').value='';$('r-email').value='';
  $('r-addr').value='';$('r-notes').value='';$('r-pass').value='1234';$('r-status').value='trial';
  $('r-id').value='';
  const due=new Date();due.setDate(due.getDate()+30);
  $('r-due').value=due.toISOString().split('T')[0];
  $('mr-title').textContent='🏪 Novo Restaurante';
  buildPlanSelects();
  if(plans.length>0) $('r-plan').value=plans[0].id;
  OM('mo-rest');
}
async function editRest(id){
  const r=restaurants.find(x=>x.id===id); if(!r) return;
  buildPlanSelects();
  $('r-name').value=r.name;$('r-owner').value=r.owner||'';$('r-phone').value=r.phone||'';
  $('r-email').value=r.email||'';$('r-addr').value=r.address||'';$('r-notes').value=r.notes||'';
  $('r-status').value=r.status;$('r-plan').value=r.planId||'';$('r-due').value=r.dueDate||'';
  $('r-pass').value='';$('r-id').value=id;
  $('mr-title').textContent='✏️ Editar Restaurante';
  OM('mo-rest');
}
async function saveRest(){
  const name=$('r-name').value.trim(), owner=$('r-owner').value.trim(), planId=$('r-plan').value;
  if(!name){toast('Informe o nome','e');return;}
  if(!owner){toast('Informe o responsável','e');return;}
  const data={name,owner,phone:$('r-phone').value.trim(),email:$('r-email').value.trim(),
    address:$('r-addr').value.trim(),notes:$('r-notes').value.trim(),
    status:$('r-status').value,planId,dueDate:$('r-due').value,
    adminPass:$('r-pass').value||'1234'};
  const editId=$('r-id').value;
  let res;
  if(editId){ res=await api('POST',`/api/master/restaurants/${editId}`,data); }
  else{ res=await api('POST','/api/master/restaurants',data); }
  if(res.ok||res.id){ toast(editId?'Atualizado!':'Restaurante criado!'); CM('mo-rest'); await loadRestaurants(); await renderDash(); }
  else toast(res.error||'Erro','e');
}
function confirmDel(id){
  const r=restaurants.find(x=>x.id===id); if(!r) return;
  $('cf-title').textContent='Excluir Restaurante';
  $('cf-msg').textContent=`Excluir "${r.name}"? Todos os dados serão removidos permanentemente.`;
  $('cf-btn').onclick=async()=>{ const res=await api('POST',`/api/master/restaurants/${id}/delete`,{}); if(res.ok){toast('Excluído!','i');CM('mo-confirm');closeDS();await loadRestaurants();await renderDash();}else toast(res.error,'e'); };
  OM('mo-confirm');
}

// ─── DETAIL SHEET ───────────────────────────────────────────
function openDS(id){
  const r=restaurants.find(x=>x.id===id); if(!r) return;
  const plan=plans.find(p=>p.id===r.planId);
  const host=location.hostname, port=location.port;
  const url=`http://${host}:${port}/r/${id}`;
  $('ds-title').textContent=r.name;
  $('ds-sub').textContent=(r.owner||'')+(r.email?' · '+r.email:'');
  $('ds-badge').innerHTML=`<span class="badge b${stc(r.status)}">${slb(r.status)}</span>`;
  $('ds-body').innerHTML=`
    <div class="dss"><h4>Informações</h4>
      <div class="dsr"><span class="dk">Responsável</span><span class="dv">${r.owner||'—'}</span></div>
      <div class="dsr"><span class="dk">Telefone</span><span class="dv">${r.phone||'—'}</span></div>
      <div class="dsr"><span class="dk">Email</span><span class="dv">${r.email||'—'}</span></div>
      <div class="dsr"><span class="dk">Endereço</span><span class="dv">${r.address||'—'}</span></div>
      <div class="dsr"><span class="dk">Cadastrado</span><span class="dv">${fmtd(r.createdAt,true)}</span></div>
    </div>
    <div class="dss"><h4>Assinatura</h4>
      <div class="dsr"><span class="dk">Plano</span><span class="dv">${plan?plan.name:'—'}</span></div>
      <div class="dsr"><span class="dk">Valor</span><span class="dv" style="color:#6366f1;font-size:1rem">${plan?fmt(plan.price)+'/'+plan.type:'—'}</span></div>
      <div class="dsr"><span class="dk">Vencimento</span><span class="dv">${fmtd(r.dueDate,true)}</span></div>
      ${(r.status==='trial'||r.status==='trial_expired')?`
      <div class="dsr"><span class="dk">Trial até</span><span class="dv" style="color:${r.trialDaysLeft<=2?'#dc2626':'#d97706'};font-weight:700">${r.trialEnds||'—'} ${r.trialDaysLeft>0?'('+r.trialDaysLeft+' dias)':'(expirado)'}</span></div>`:''}
    </div>
    <div class="dss"><h4>Acesso do Restaurante</h4>
      <div style="background:#f8fafc;border-radius:8px;padding:11px;font-size:.82rem;word-break:break-all">
        <strong>Link:</strong> <a href="${url}" target="_blank" style="color:#6366f1">${url}</a>
      </div>
      <p style="font-size:.76rem;color:#64748b;margin-top:7px">Senha ADM padrão: definida no cadastro</p>
    </div>
    ${r.notes?`<div class="dss"><h4>Notas</h4><p style="font-size:.83rem;color:#64748b;line-height:1.6">${r.notes}</p></div>`:''}
    <div class="dss"><h4>Dados em Tempo Real</h4>
      <div class="dsr"><span class="dk">Comandas Abertas</span><span class="dv" style="color:#16a34a">${r.openComandas}</span></div>
      <div class="dsr"><span class="dk">Total de Comandas</span><span class="dv">${r.totalComandas}</span></div>
    </div>`;
  $('ds-actions').innerHTML=`
    <button class="btn bg" style="width:100%;justify-content:flex-start" onclick="editRest('${id}');closeDS()">✏️ Editar Dados</button>
    <a href="${url}" target="_blank" class="btn" style="background:#eef2ff;color:#6366f1;width:100%;justify-content:flex-start;text-decoration:none">🔗 Abrir Sistema do Restaurante</a>
    ${r.status!=='active'?`<button class="btn bs" style="width:100%;justify-content:flex-start" onclick="chStatus('${id}','active')">✅ Ativar como Pago</button>`:''}
    ${r.status==='active'?`<button class="btn bw" style="width:100%;justify-content:flex-start" onclick="chStatus('${id}','suspended')">⏸ Suspender</button>`:''}
    ${(r.status==='trial'||r.status==='trial_expired')?`<button class="btn" style="width:100%;justify-content:flex-start;background:#fef9c3;color:#d97706" onclick="extendTrial('${id}')">⏳ Estender Trial +7 dias</button>`:''}
    ${r.status!=='cancelled'?`<button class="btn bd" style="width:100%;justify-content:flex-start" onclick="chStatus('${id}','cancelled')">❌ Cancelar Assinatura</button>`:''}
    <button class="btn bg" style="width:100%;justify-content:flex-start" onclick="confirmDel('${id}')">🗑 Excluir Permanentemente</button>`;
  $('ds').classList.add('open');
}
function closeDS(){ $('ds').classList.remove('open'); }
async function chStatus(id,status){
  const res=await api('POST',`/api/master/restaurants/${id}/status`,{status});
  if(res.ok){ toast('Status atualizado!'); await loadRestaurants(); openDS(id); await renderDash(); }
  else toast(res.error||'Erro','e');
}
async function extendTrial(id){
  const r=restaurants.find(x=>x.id===id); if(!r) return;
  const cur=r.trialEnds?new Date(r.trialEnds):new Date();
  if(cur<new Date()) cur.setTime(new Date().getTime()); // if expired, start from today
  cur.setDate(cur.getDate()+7);
  const newDate=cur.toISOString().split('T')[0];
  const res=await api('POST',`/api/master/restaurants/${id}`,{trialEnds:newDate,status:'trial'});
  if(res.ok){ toast('Trial estendido até '+newDate+'! 🎉'); await loadRestaurants(); openDS(id); await renderDash(); }
  else toast(res.error||'Erro','e');
}

// ─── PLANS ──────────────────────────────────────────────────
function renderPlans(){
  const g=$('plans-grid'); if(!g) return;
  if(plans.length===0){ g.innerHTML=`<div class="empty" style="grid-column:1/-1"><span class="icon">📦</span><p>Nenhum plano criado.</p></div>`; return; }
  g.innerHTML=plans.map(p=>{
    const using=restaurants.filter(r=>r.planId===p.id&&r.status==='active').length;
    const feats=(p.features||[]);
    return `<div class="pc ${p.featured==='yes'?'feat':''}">
      ${p.featured==='yes'?'<div class="pba">⭐ Popular</div>':''}
      <div class="pn" style="color:${p.color||'#6366f1'}">${p.name}</div>
      <div style="font-size:.76rem;color:#64748b;margin-bottom:8px">${p.type.charAt(0).toUpperCase()+p.type.slice(1)}</div>
      <div class="ppr" style="color:${p.color||'#6366f1'}">${fmt(p.price)}<span>/${p.type}</span></div>
      <div class="pd">${p.desc||''}</div>
      ${feats.length?'<ul style="font-size:.78rem;color:#64748b;padding-left:15px;margin-bottom:8px">'+feats.map(f=>`<li>${f}</li>`).join('')+'</ul>':''}
      <div style="font-size:.76rem;color:#64748b;margin-bottom:10px">${using} ativo(s) · Máx ${p.maxUsers>=999?'∞':p.maxUsers} usuários</div>
      <div style="display:flex;gap:6px">
        <button class="btn bg bsm" onclick="editPlan('${p.id}')">✏️ Editar</button>
        <button class="btn bd bsm" onclick="delPlan('${p.id}')">🗑</button>
      </div>
    </div>`;
  }).join('');
}
function openPlanModal(){
  $('p-name').value='';$('p-price').value='';$('p-type').value='mensal';
  $('p-desc').value='';$('p-feat').value='';$('p-mu').value='5';
  $('p-feat2').value='no';$('p-color').value='#6366f1';$('p-id').value='';
  $('mp-title').textContent='📦 Novo Plano'; OM('mo-plan');
}
function editPlan(id){
  const p=plans.find(x=>x.id===id); if(!p) return;
  $('p-name').value=p.name;$('p-price').value=p.price;$('p-type').value=p.type;
  $('p-desc').value=p.desc||'';$('p-feat').value=(p.features||[]).join('\\n');
  $('p-mu').value=p.maxUsers||5;$('p-feat2').value=p.featured||'no';
  $('p-color').value=p.color||'#6366f1';$('p-id').value=id;
  $('mp-title').textContent='✏️ Editar Plano'; OM('mo-plan');
}
async function savePlan(){
  const name=$('p-name').value.trim(), price=parseFloat($('p-price').value);
  if(!name){toast('Informe o nome','e');return;}
  if(isNaN(price)||price<0){toast('Preço inválido','e');return;}
  const data={name,price,type:$('p-type').value,desc:$('p-desc').value.trim(),
    features:$('p-feat').value.split('\\n').map(f=>f.trim()).filter(Boolean),
    maxUsers:parseInt($('p-mu').value)||5,featured:$('p-feat2').value,color:$('p-color').value};
  const eid=$('p-id').value;
  let res;
  if(eid) res=await api('POST',`/api/master/plans/${eid}`,data);
  else res=await api('POST','/api/master/plans',data);
  if(res.ok){toast(eid?'Plano atualizado!':'Plano criado!');CM('mo-plan');await loadPlans();renderPlans();}
  else toast(res.error||'Erro','e');
}
async function delPlan(id){
  const p=plans.find(x=>x.id===id); if(!p) return;
  const using=restaurants.filter(r=>r.planId===id).length;
  if(using>0){toast(`${using} restaurante(s) usam este plano. Mude-os primeiro.`,'e');return;}
  if(!confirm(`Excluir plano "${p.name}"?`)) return;
  const res=await api('POST',`/api/master/plans/${id}/delete`,{});
  if(res.ok){toast('Plano excluído!','i');await loadPlans();renderPlans();}
}

// ─── FINANCE ────────────────────────────────────────────────
async function renderFinance(){
  const d=await loadStats();
  const active=restaurants.filter(r=>r.status==='active');
  const mrr=active.reduce((s,r)=>{const p=plans.find(x=>x.id===r.planId);return s+(p?p.price:0);},0);
  $('fin-stats').innerHTML=`
    <div class="sc"><span class="si">💰</span><div class="sv" style="color:#6366f1">${fmt(mrr)}</div><div class="sl">MRR</div></div>
    <div class="sc"><span class="si">📈</span><div class="sv" style="color:#16a34a">${fmt(mrr*12)}</div><div class="sl">ARR Estimado</div></div>
    <div class="sc"><span class="si">⚠️</span><div class="sv" style="color:#d97706">${d.suspended}</div><div class="sl">Suspensos</div></div>`;
  const up=restaurants.filter(r=>r.status==='active'&&r.dueDate).sort((a,b)=>new Date(a.dueDate)-new Date(b.dueDate)).slice(0,8);
  $('fin-up').innerHTML=up.length===0?'<p style="font-size:.83rem;color:#64748b">Nenhuma cobrança próxima.</p>':
    up.map(r=>{const p=plans.find(x=>x.id===r.planId);return`<div style="display:flex;justify-content:space-between;align-items:center;padding:7px 0;border-bottom:1px solid #f8fafc">
      <div><div style="font-size:.83rem;font-weight:600">${r.name}</div><div style="font-size:.72rem;color:#64748b">${fmtd(r.dueDate,true)}</div></div>
      <span style="font-weight:700;color:#6366f1">${p?fmt(p.price):'—'}</span>
    </div>`}).join('');
  await loadTimeline();
  $('fin-tl').innerHTML=tl.slice(0,10).map(t=>`<div class="tli"><div class="tld" style="background:${t.color||'#6366f1'}"></div><div><div class="tlm">${t.msg}</div><div class="tlt">${fmtd(t.at,true)}</div></div></div>`).join('')||'<p style="font-size:.83rem;color:#64748b">Nenhum histórico.</p>';
}

// ─── SETTINGS ───────────────────────────────────────────────
async function changePass(){
  const p=$('sp1').value, p2=$('sp2').value;
  if(!p||p.length<4){toast('Mínimo 4 caracteres','e');return;}
  if(p!==p2){toast('Senhas não conferem','e');return;}
  const res=await api('POST','/api/master/settings',{newPassword:p});
  if(res.ok){toast('Senha alterada!');$('sp1').value='';$('sp2').value='';}
  else toast(res.error||'Erro','e');
}
async function saveSettings(){
  const res=await api('POST','/api/master/settings',{settings:{sysName:$('sn').value.trim(),contact:$('sc').value.trim()}});
  if(res.ok) toast('Configurações salvas!');
  else toast(res.error||'Erro','e');
}
async function exportData(){
  const d=await api('GET','/api/master/restaurants');
  const p=await api('GET','/api/master/plans');
  const t=await api('GET','/api/master/timeline');
  const blob=new Blob([JSON.stringify({restaurants:d.restaurants,plans:p.plans,timeline:t.timeline},null,2)],{type:'application/json'});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=`backup_${new Date().toLocaleDateString('pt-BR').replace(/[/]/g,'-')}.json`;a.click();
  toast('Backup exportado!');
}
async function logout(){
  await api('POST','/api/master/logout',{});
  location.reload();
}

// ─── START ──────────────────────────────────────────────────
init();

// ── MONITOR ──────────────────────────────────────────────────
let _monTimer = null;
let _monAuto = true;

function startMonitor(){
  fetchMonitor();
  if(_monAuto && !_monTimer){
    _monTimer = setInterval(fetchMonitor, 5000);
  }
}

function stopMonitor(){
  if(_monTimer){ clearInterval(_monTimer); _monTimer=null; }
}

function toggleMonAuto(){
  _monAuto = !_monAuto;
  const btn = document.getElementById('mon-auto-btn');
  const dot = document.getElementById('mon-auto-dot');
  if(_monAuto){
    btn.textContent='Pausar';
    dot.style.background='#16a34a';
    dot.style.boxShadow='0 0 0 3px rgba(22,163,74,.2)';
    startMonitor();
  } else {
    btn.textContent='Retomar';
    dot.style.background='#94a3b8';
    dot.style.boxShadow='none';
    stopMonitor();
  }
}

async function fetchMonitor(){
  try {
    const r = await fetch('/api/monitor', {credentials:'include'});
    if(!r.ok) return;
    const d = await r.json();
    renderMonitor(d);
  } catch(e){ console.warn('monitor error', e); }
}

function renderMonitor(d){
  const errRate = d.requests_total > 0 ? ((d.requests_errors/d.requests_total)*100).toFixed(1) : '0.0';
  const memColor = d.mem_mb > 400 ? '#dc2626' : d.mem_mb > 200 ? '#d97706' : '#16a34a';
  const errColor = d.requests_errors > 10 ? '#dc2626' : d.requests_errors > 0 ? '#d97706' : '#16a34a';

  document.getElementById('mon-stats-grid').innerHTML = `
    <div class="sc" style="border-left:3px solid #6366f1">
      <div class="sv" style="color:#6366f1">${d.uptime||'--'}</div>
      <div class="sl">⏱️ Tempo ativo</div>
    </div>
    <div class="sc" style="border-left:3px solid #16a34a">
      <div class="sv" style="color:#16a34a">${d.requests_total||0}</div>
      <div class="sl">📥 Requisições totais</div>
    </div>
    <div class="sc" style="border-left:3px solid ${errColor}">
      <div class="sv" style="color:${errColor}">${d.requests_errors||0} <span style="font-size:1rem">(${errRate}%)</span></div>
      <div class="sl">❌ Erros</div>
    </div>
    <div class="sc" style="border-left:3px solid ${memColor}">
      <div class="sv" style="color:${memColor}">${d.mem_mb||0} <span style="font-size:1rem">MB</span></div>
      <div class="sl">🧠 Memória RAM</div>
    </div>`;

  // Watchdog log
  const wlog = d.watchdog_log||[];
  const wlogDiv = document.getElementById('mon-wlog');
  if(wlog.length === 0){
    wlogDiv.innerHTML = '<span style="color:#94a3b8">Nenhum log disponível. Watchdog rodando?</span>';
    document.getElementById('mon-wdog-status').innerHTML = '<span style="color:#94a3b8">Watchdog não detectado</span>';
  } else {
    wlogDiv.innerHTML = wlog.map(l => {
      const isErr = l.includes('ERRO') || l.includes('RESTART') || l.includes('DOWN');
      const isOk  = l.includes('OK') || l.includes('iniciado') || l.includes('ativo');
      const col = isErr ? '#dc2626' : isOk ? '#16a34a' : '#374151';
      return `<div style="color:${col};border-bottom:1px solid #f1f5f9;padding:2px 0">${l}</div>`;
    }).join('');
    wlogDiv.scrollTop = wlogDiv.scrollHeight;
    const last = wlog[wlog.length-1]||'';
    const lastIsOk = last.includes('OK') || last.includes('ativo');
    document.getElementById('mon-wdog-status').innerHTML = lastIsOk
      ? '<span style="color:#16a34a;font-weight:700">● Ativo</span>'
      : '<span style="color:#d97706;font-weight:700">● Verificar</span>';
  }

  // Req detail
  document.getElementById('mon-req-detail').innerHTML = `
    <div style="background:#f8fafc;border-radius:9px;padding:13px;border:1px solid var(--br)">
      <div style="font-size:.72rem;color:var(--mu);text-transform:uppercase;letter-spacing:.04em;margin-bottom:5px">Sessões ativas</div>
      <div style="font-size:1.5rem;font-weight:900;color:var(--tx)">${d.sessions||0}</div>
    </div>
    <div style="background:#f8fafc;border-radius:9px;padding:13px;border:1px solid var(--br)">
      <div style="font-size:.72rem;color:var(--mu);text-transform:uppercase;letter-spacing:.04em;margin-bottom:5px">Cache em memória</div>
      <div style="font-size:1.5rem;font-weight:900;color:var(--tx)">${d.cache_keys||0} <span style="font-size:.9rem;font-weight:500">chaves</span></div>
    </div>
    <div style="background:#f8fafc;border-radius:9px;padding:13px;border:1px solid var(--br)">
      <div style="font-size:.72rem;color:var(--mu);text-transform:uppercase;letter-spacing:.04em;margin-bottom:5px">PID do processo</div>
      <div style="font-size:1.5rem;font-weight:900;color:var(--tx)">${d.pid||'--'}</div>
    </div>`;
}

async function monAction(action){
  const labels = {
    clear_sessions: 'Limpar todas as sessões?',
    clear_cache: 'Limpar cache da memória?',
    clear_errors: 'Zerar contadores de erro?'
  };
  if(!confirm(labels[action]||'Confirmar?')) return;
  try {
    const r = await fetch('/api/monitor/action', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({action})
    });
    const d = await r.json();
    if(d.ok) { toast(d.msg||'Feito!','s'); fetchMonitor(); }
    else toast(d.error||'Erro','e');
  } catch(e){ toast('Erro de conexão','e'); }
}
</script></body></html>"""


def render_restaurant_login(rest, rid):
    name = html.escape(rest.get("name", "Restaurante"))
    color = "#6366f1"
    slogan = ""
    try:
        cfg = load_json(rest_file(rid, "config"), {})
        color = cfg.get("color", "#6366f1")
        slogan = html.escape(cfg.get("slogan", ""))
    except: pass
    users = load_json(rest_file(rid, "users"), [])
    users_json = json.dumps([{
        "id": u["id"],
        "name": u["name"],
        "role": u.get("role","garcom"),
        "color": u.get("color", color)
    } for u in users])

    # Generate complementary dark shade of color for gradients
    def hex_darken(hex_c, factor=0.55):
        try:
            r=int(hex_c[1:3],16); g=int(hex_c[3:5],16); b=int(hex_c[5:7],16)
            r=int(r*factor); g=int(g*factor); b=int(b*factor)
            return f"#{r:02x}{g:02x}{b:02x}"
        except: return "#1a0a00"

    dark = hex_darken(color, 0.3)
    mid  = hex_darken(color, 0.55)

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no"/>
<title>{name} — Entrar</title>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800&display=swap" rel="stylesheet"/>
<style>
*{{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}}
:root{{
  --p:{color};
  --dark:{dark};
  --mid:{mid};
  --txt:#fff;
  --muted:rgba(255,255,255,.5);
  --card:rgba(255,255,255,.1);
  --card-border:rgba(255,255,255,.14);
}}
html,body{{
  height:100%;font-family:'Outfit',system-ui,sans-serif;
  background:var(--dark);color:var(--txt);overflow:hidden;
}}

/* ── BG MESH ── */
.bg{{
  position:fixed;inset:0;z-index:0;
  background:
    radial-gradient(ellipse 80% 60% at 20% 10%, {color}55 0%, transparent 55%),
    radial-gradient(ellipse 60% 50% at 80% 80%, {color}33 0%, transparent 55%),
    radial-gradient(ellipse 100% 80% at 50% 50%, {dark} 0%, {dark}dd 100%);
}}
.bg-grid{{
  position:fixed;inset:0;z-index:0;opacity:.08;
  background-image:linear-gradient(rgba(255,255,255,.06) 1px,transparent 1px),
    linear-gradient(90deg,rgba(255,255,255,.06) 1px,transparent 1px);
  background-size:32px 32px;
}}

/* ── WRAP ── */
.wrap{{
  position:relative;z-index:1;min-height:100vh;
  display:flex;flex-direction:column;align-items:center;
  justify-content:center;padding:24px 16px;gap:0;
}}

/* ── HEADER ── */
.hdr{{
  text-align:center;margin-bottom:28px;
  animation:fadeUp .5s ease both;
}}
.hdr-logo{{
  width:64px;height:64px;border-radius:18px;margin:0 auto 14px;
  background:linear-gradient(135deg,{color},{color}aa);
  box-shadow:0 8px 32px {color}55;
  display:flex;align-items:center;justify-content:center;font-size:1.8rem;
  border:1.5px solid rgba(255,255,255,.18);
}}
.hdr h1{{font-size:1.7rem;font-weight:800;letter-spacing:-.02em;margin-bottom:3px}}
.hdr p{{font-size:.82rem;color:var(--muted);font-weight:400}}

/* ── MAIN CARD ── */
.card{{
  width:100%;max-width:420px;
  background:rgba(0,0,0,.28);
  backdrop-filter:blur(24px);-webkit-backdrop-filter:blur(24px);
  border:1px solid var(--card-border);border-radius:20px;
  overflow:hidden;
  box-shadow:0 20px 60px rgba(0,0,0,.4),0 0 0 1px rgba(255,255,255,.04);
  animation:fadeUp .5s .1s ease both;
}}

/* ── CARD HEADER ── */
.card-hdr{{
  padding:18px 20px;border-bottom:1px solid var(--card-border);
  display:flex;align-items:center;justify-content:space-between;
}}
.card-hdr-left{{font-size:.75rem;font-weight:600;color:var(--muted);letter-spacing:.05em;text-transform:uppercase}}
.user-count{{
  font-size:.68rem;font-weight:600;
  background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.12);
  border-radius:20px;padding:2px 9px;color:var(--muted);
}}

/* ── USER LIST ── */
.users{{
  display:flex;flex-direction:column;gap:0;
  max-height:270px;overflow-y:auto;
  scroll-behavior:smooth;
}}
.users::-webkit-scrollbar{{width:3px}}
.users::-webkit-scrollbar-thumb{{background:rgba(255,255,255,.12);border-radius:3px}}

.ub{{
  display:flex;align-items:center;gap:13px;
  padding:13px 20px;border:none;background:transparent;
  cursor:pointer;text-align:left;width:100%;
  border-bottom:1px solid rgba(255,255,255,.06);
  transition:all .16s;position:relative;overflow:hidden;
}}
.ub::before{{
  content:'';position:absolute;inset:0;
  background:linear-gradient(90deg,{color}14,transparent);
  opacity:0;transition:.16s;
}}
.ub:hover::before{{opacity:1}}
.ub.sel{{background:rgba(255,255,255,.07)}}
.ub.sel::before{{opacity:1}}
.ub:last-child{{border-bottom:none}}

.av{{
  width:40px;height:40px;border-radius:12px;
  display:flex;align-items:center;justify-content:center;
  font-size:1rem;font-weight:700;flex-shrink:0;
  border:1.5px solid rgba(255,255,255,.15);
  box-shadow:0 2px 8px rgba(0,0,0,.2);
  transition:.16s;
}}
.ub.sel .av{{transform:scale(1.05);box-shadow:0 4px 16px rgba(0,0,0,.3)}}

.ui{{flex:1}}
.ui strong{{display:block;font-size:.9rem;font-weight:600;color:rgba(255,255,255,.9);line-height:1.2}}
.ui span{{font-size:.72rem;color:var(--muted);margin-top:2px;display:block}}

.role-badge{{
  font-size:.62rem;font-weight:700;letter-spacing:.04em;text-transform:uppercase;
  padding:2px 8px;border-radius:20px;flex-shrink:0;
}}
.rb-admin{{background:rgba(250,204,21,.12);color:#fbbf24;border:1px solid rgba(250,204,21,.2)}}
.rb-caixa{{background:rgba(16,185,129,.12);color:#6ee7b7;border:1px solid rgba(16,185,129,.2)}}
.rb-garcom{{background:rgba(255,255,255,.07);color:rgba(255,255,255,.45);border:1px solid rgba(255,255,255,.1)}}

.chk-ic{{
  width:20px;height:20px;border-radius:50%;
  border:1.5px solid rgba(255,255,255,.15);
  display:flex;align-items:center;justify-content:center;
  font-size:.65rem;flex-shrink:0;transition:.16s;
}}
.ub.sel .chk-ic{{
  background:var(--p);border-color:var(--p);
  box-shadow:0 0 10px {color}66;
}}

/* ── PASSWORD SECTION ── */
.pw-section{{
  border-top:1px solid var(--card-border);
  padding:16px 20px;
  animation:slideDown .2s ease;
  display:none;
}}
.pw-section.show{{display:block}}
@keyframes slideDown{{from{{opacity:0;transform:translateY(-8px)}}to{{opacity:1;transform:translateY(0)}}}}

.pw-lbl{{
  display:flex;align-items:center;gap:8px;
  font-size:.73rem;font-weight:600;color:var(--muted);
  text-transform:uppercase;letter-spacing:.06em;margin-bottom:10px;
}}
.pw-lbl-name{{color:rgba(255,255,255,.7);font-family:inherit}}

.pw-iw{{position:relative;margin-bottom:12px}}
.pw-iw input{{
  width:100%;padding:12px 42px 12px 14px;
  background:rgba(255,255,255,.07);
  border:1.5px solid rgba(255,255,255,.12);
  border-radius:11px;font-size:.92rem;color:#fff;
  font-family:inherit;transition:all .2s;
  letter-spacing:.08em;
}}
.pw-iw input:focus{{
  outline:none;
  border-color:{color}88;
  background:rgba(255,255,255,.1);
  box-shadow:0 0 0 3px {color}22;
}}
.pw-iw input::placeholder{{color:rgba(255,255,255,.18);letter-spacing:.01em}}
.pw-eye{{
  position:absolute;right:12px;top:50%;transform:translateY(-50%);
  background:none;border:none;cursor:pointer;
  color:rgba(255,255,255,.25);transition:.15s;padding:3px;
}}
.pw-eye:hover{{color:rgba(255,255,255,.6)}}

/* ── ERR ── */
.err{{
  display:none;align-items:center;gap:8px;
  background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.2);
  border-radius:9px;padding:9px 13px;margin-bottom:10px;
  font-size:.8rem;color:#fca5a5;font-weight:500;
}}

/* ── LOGIN BTN ── */
.btn-login{{
  width:100%;padding:13px;
  background:linear-gradient(135deg,{color},{color}bb);
  color:#fff;border:none;border-radius:11px;
  font-size:.9rem;font-weight:700;cursor:pointer;
  font-family:inherit;transition:all .2s;
  box-shadow:0 4px 20px {color}44;
  display:flex;align-items:center;justify-content:center;gap:8px;
}}
.btn-login:hover:not(:disabled){{
  transform:translateY(-1px);
  box-shadow:0 8px 28px {color}55;
  filter:brightness(1.08);
}}
.btn-login:active{{transform:translateY(0)}}
.btn-login:disabled{{opacity:.55;cursor:not-allowed}}

/* ── FOOTER ── */
.foot{{
  margin-top:16px;text-align:center;
  font-size:.7rem;color:rgba(255,255,255,.2);
  animation:fadeUp .5s .2s ease both;
}}

/* ── ANIMATIONS ── */
@keyframes fadeUp{{from{{opacity:0;transform:translateY(14px)}}to{{opacity:1;transform:translateY(0)}}}}
@keyframes spin{{to{{transform:rotate(360deg)}}}}
</style>
</head>
<body>
<div class="bg"></div>
<div class="bg-grid"></div>

<div class="wrap">
  <div class="hdr">
    <div class="hdr-logo">🍽️</div>
    <h1>{name}</h1>
    <p>{slogan if slogan else 'Selecione seu perfil para entrar'}</p>
  </div>

  <div class="card">
    <div class="card-hdr">
      <span class="card-hdr-left">Quem é você?</span>
      <span class="user-count" id="ucnt">0 usuários</span>
    </div>

    <div class="users" id="ul"></div>

    <div class="pw-section" id="pw-section">
      <div class="pw-lbl">
        Senha de <span class="pw-lbl-name" id="sel-name">—</span>
      </div>
      <div id="errdiv" class="err">
        <span>⚠</span><span id="errtxt">Senha incorreta</span>
      </div>
      <div class="pw-iw">
        <input type="password" id="pass" placeholder="Digite sua senha"
          autocomplete="current-password"
          onkeydown="if(event.key==='Enter')doLogin()"/>
        <button class="pw-eye" onclick="togglePw()" type="button">
          <svg id="eye-ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:14px;height:14px">
            <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
            <circle cx="12" cy="12" r="3"/>
          </svg>
        </button>
      </div>
      <button class="btn-login" id="btn" onclick="doLogin()">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="width:15px;height:15px"><path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"/><polyline points="10 17 15 12 10 7"/><line x1="15" y1="12" x2="3" y2="12"/></svg>
        Entrar
      </button>
    </div>
  </div>

  <div class="foot">Acesso restrito à equipe de {name}</div>
</div>

<script>
const USERS={users_json};
const RID='{rid}';
let selId=null;

const ROLES={{admin:'👑 Admin',caixa:'💰 Caixa',garcom:'🍽️ Garçom'}};
const RBCLS={{admin:'rb-admin',caixa:'rb-caixa',garcom:'rb-garcom'}};

function initials(n){{return n.split(' ').map(w=>w[0]).join('').toUpperCase().slice(0,2)}}

function render(){{
  const ul=document.getElementById('ul');
  document.getElementById('ucnt').textContent=USERS.length+' usuário'+(USERS.length!==1?'s':'');
  ul.innerHTML=USERS.map(u=>`
    <button class="ub" id="ub_${{u.id}}" onclick="sel('${{u.id}}','${{u.name.replace(/'/g,\'\')}}')">
      <div class="av" style="background:${{u.color||'var(--p)'}}22;color:${{u.color||'var(--p)'}}">${{initials(u.name)}}</div>
      <div class="ui">
        <strong>${{u.name}}</strong>
        <span>${{ROLES[u.role]||'🍽️ Garçom'}}</span>
      </div>
      <span class="role-badge ${{RBCLS[u.role]||'rb-garcom'}}">${{u.role||'garçom'}}</span>
      <div class="chk-ic" id="chk_${{u.id}}">✓</div>
    </button>`).join('');
  if(USERS.length===0){{
    ul.innerHTML='<div style="padding:28px 20px;text-align:center;color:rgba(255,255,255,.3);font-size:.84rem">Nenhum usuário cadastrado</div>';
  }}
}}

function sel(id, name){{
  if(selId===id)return;
  selId=id;
  document.querySelectorAll('.ub').forEach(b=>b.classList.remove('sel'));
  document.getElementById('ub_'+id).classList.add('sel');
  document.getElementById('sel-name').textContent=name;
  document.getElementById('errdiv').style.display='none';
  const pw=document.getElementById('pw-section');
  pw.classList.add('show');
  document.getElementById('pass').value='';
  setTimeout(()=>document.getElementById('pass').focus(),150);
  // Scroll user into view on mobile
  document.getElementById('ub_'+id).scrollIntoView({{behavior:'smooth',block:'nearest'}});
}}

async function doLogin(){{
  if(!selId)return;
  const pass=document.getElementById('pass').value;
  if(!pass){{showErr('Digite sua senha');return;}}
  const btn=document.getElementById('btn');
  btn.disabled=true;
  btn.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="width:15px;height:15px;animation:spin .7s linear infinite"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg> Verificando...';
  document.getElementById('errdiv').style.display='none';
  try{{
    const r=await fetch(`/api/rest/${{RID}}/login`,{{
      method:'POST',credentials:'include',
      headers:{{'Content-Type':'application/json'}},
      body:JSON.stringify({{userId:selId,password:pass}})
    }});
    const d=await r.json();
    if(d.ok){{
      btn.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="width:15px;height:15px"><polyline points="20 6 9 17 4 12"/></svg> Abrindo...';
      sessionStorage.setItem('cu_'+RID, JSON.stringify(d.user));
      setTimeout(()=>window.location.replace('/r/'+RID),350);
    }} else {{
      showErr(d.error||'Senha incorreta');
      btn.disabled=false;
      btn.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="width:15px;height:15px"><path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"/><polyline points="10 17 15 12 10 7"/><line x1="15" y1="12" x2="3" y2="12"/></svg> Entrar';
    }}
  }}catch(e){{
    showErr('Erro de conexão');
    btn.disabled=false;
    btn.innerHTML='Entrar';
  }}
}}

function showErr(msg){{
  const e=document.getElementById('errdiv');
  document.getElementById('errtxt').textContent=msg;
  e.style.display='flex';
  document.getElementById('pass').focus();
}}

function togglePw(){{
  const i=document.getElementById('pass');
  i.type=i.type==='password'?'text':'password';
}}

render();
</script>
</body>
</html>"""

def render_restaurant_app(rest, rid):
    """Serve the full restaurant app with server-sync"""
    name = rest.get("name", "Restaurante")
    # Load config for theme
    cfg = load_json(rest_file(rid, "config"), {})
    color = cfg.get("color", "#6366f1")
    # Inject rid into the app HTML
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "app.html"), "r", encoding="utf-8") as f:
        html = f.read()
    trial_ends = rest.get("trialEnds", "")
    status     = rest.get("status", "active")
    html = html.replace("__RID__", rid).replace("__COLOR__", color).replace("__NAME__", name)\
               .replace("__TRIAL_ENDS__", trial_ends).replace("__STATUS__", status)
    return html


def render_trial_expired(rest):
    name = html.escape(rest.get("name","Restaurante"))
    color = rest.get("color","#6366f1")
    return f"""<!DOCTYPE html><html lang="pt-BR"><head>
<meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{name} — Trial Expirado</title>
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;600;800;900&display=swap" rel="stylesheet"/>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Outfit',system-ui,sans-serif;min-height:100vh;display:flex;align-items:center;
  justify-content:center;background:linear-gradient(135deg,#0f172a 0%,#1e293b 100%);padding:20px}}
.card{{background:#fff;border-radius:24px;padding:48px 40px;text-align:center;max-width:440px;width:100%;
  box-shadow:0 20px 60px rgba(0,0,0,.4)}}
.icon{{font-size:4rem;margin-bottom:20px;display:block}}
.badge{{display:inline-block;background:#fef3c7;color:#d97706;font-size:.75rem;font-weight:800;
  padding:4px 12px;border-radius:24px;text-transform:uppercase;letter-spacing:.06em;margin-bottom:16px}}
h1{{font-size:1.6rem;font-weight:900;color:#0f172a;margin-bottom:10px}}
.sub{{color:#64748b;font-size:.92rem;line-height:1.7;margin-bottom:28px}}
.price-box{{background:#f8fafc;border-radius:16px;padding:20px;margin-bottom:24px;border:2px solid #e2e8f0}}
.price-label{{font-size:.75rem;color:#94a3b8;text-transform:uppercase;letter-spacing:.06em;font-weight:700}}
.price-val{{font-size:2rem;font-weight:900;color:{color};margin:4px 0}}
.price-period{{font-size:.8rem;color:#94a3b8;font-weight:600}}
.features{{list-style:none;text-align:left;margin-bottom:24px;display:flex;flex-direction:column;gap:8px}}
.features li{{font-size:.88rem;color:#475569;display:flex;align-items:center;gap:8px;font-weight:500}}
.features li::before{{content:'✅';font-size:.8rem}}
.btn-main{{display:block;width:100%;padding:16px;background:{color};color:#fff;border:none;border-radius:14px;
  font-size:1rem;font-weight:800;cursor:pointer;text-decoration:none;
  box-shadow:0 4px 20px rgba(99,102,241,.35);transition:all .2s;margin-bottom:12px}}
.btn-main:hover{{transform:translateY(-2px);opacity:.9}}
.contact{{font-size:.8rem;color:#94a3b8}}
.contact a{{color:{color};font-weight:700;text-decoration:none}}
.name-badge{{font-size:.85rem;color:#64748b;margin-bottom:8px;font-weight:600}}
</style></head>
<body>
<div class="card">
  <span class="icon">⏰</span>
  <div class="badge">Trial Expirado</div>
  <p class="name-badge">{name}</p>
  <h1>Seu período gratuito encerrou</h1>
  <p class="sub">Os 7 dias de teste gratuito chegaram ao fim.<br/>
  Assine agora para continuar usando o sistema sem interrupções.</p>
  <div class="price-box">
    <div class="price-label">A partir de</div>
    <div class="price-val">R$ 79,90</div>
    <div class="price-period">por mês · cancele quando quiser</div>
  </div>
  <ul class="features">
    <li>Comandas ilimitadas</li>
    <li>Relatórios financeiros completos</li>
    <li>Controle de estoque</li>
    <li>Suporte via WhatsApp</li>
  </ul>
  <a class="btn-main" href="https://wa.me/5565998048585?text=Olá! Quero assinar o Garfio para {name}" target="_blank">
    📱 Assinar via WhatsApp
  </a>
  <p class="contact">Já assinou? Entre em contato: <a href="mailto:luizotaviomoura208@gmail.com">luizotaviomoura208@gmail.com</a></p>
</div>
</body></html>"""


def render_suspended(rest):
    name = html.escape(rest.get("name","Restaurante"))
    status = rest.get("status","suspended")
    msg = "Assinatura suspensa" if status=="suspended" else "Assinatura cancelada"
    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{name}</title><style>body{{font-family:system-ui,sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center;background:#f8fafc;padding:20px}}
.box{{background:#fff;border-radius:16px;padding:40px;text-align:center;max-width:400px;box-shadow:0 4px 20px rgba(0,0,0,.1)}}
h1{{font-size:1.4rem;font-weight:800;margin-bottom:10px;color:#0f172a}}.msg{{color:#64748b;font-size:.9rem;line-height:1.6}}</style></head>
<body><div class="box"><div style="font-size:3rem;margin-bottom:14px">🔒</div><h1>{name}</h1>
<p class="msg">{msg}.<br/>Entre em contato com o suporte para reativar.</p></div></body></html>"""


# ─── SERVER START ─────────────────────────────────────────────
def cleanup_sessions():
    """Remove expired sessions periodically"""
    import time
    while True:
        time.sleep(300)  # every 5 min
        now_dt = datetime.now()
        expired = [k for k,v in list(sessions.items()) if datetime.fromisoformat(v["expires"]) < now_dt]
        for k in expired: sessions.pop(k, None)

def start_server():
    init_data()
    t = threading.Thread(target=cleanup_sessions, daemon=True)
    t.start()

    # Check if app.html exists
    app_html = os.path.join(STATIC_DIR, "app.html")
    if not os.path.exists(app_html):
        print("⚠️  AVISO: static/app.html não encontrado.")
        print("   Copie o arquivo comanda-universal-server.html para static/app.html")

    print("=" * 60)
    print("  🍴  Garfio — Sistema de Gestão para Restaurantes")
    print("=" * 60)
    print(f"\n  ✅  Servidor iniciado na porta {PORT}")
    print(f"\n  🔗  Painel Master:  http://localhost:{PORT}/")
    print(f"  🔗  Na rede local:  http://SEU_IP:{PORT}/")
    print(f"\n  👤  Login Master:   admin / admin123")
    print(f"\n  ⚠️  Para parar: feche esta janela ou Ctrl+C")
    print("=" * 60)

    # Try to detect local IP
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        print(f"\n  📱  Celulares: http://{ip}:{PORT}/r/[id-restaurante]")
    except: pass

    print()

    socketserver.TCPServer.allow_reuse_address = True
    socketserver.TCPServer.request_queue_size = 50
    class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
        daemon_threads = True
    with ThreadedTCPServer(("", PORT), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n\n  👋  Servidor encerrado.")


if __name__ == "__main__":
    start_server()
