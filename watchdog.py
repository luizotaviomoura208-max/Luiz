#!/usr/bin/env python3
"""
Garfio — Watchdog
Inicia o servidor, aguarda ficar pronto, abre o navegador e monitora.
"""

import subprocess, sys, os, time, json, socket, threading, webbrowser
import urllib.request, urllib.error
from datetime import datetime

PORT           = 8080
CHECK_INTERVAL = 30      # segundos entre verificações
FAIL_THRESHOLD = 3       # falhas antes de reiniciar
MAX_PER_HOUR   = 10      # reinícios máximos por hora
MEM_LIMIT_MB   = 600     # reinicia se RAM passar disso

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
SERVER_FILE = os.path.join(BASE_DIR, "servidor.py")
LOG_FILE    = os.path.join(BASE_DIR, "data", "watchdog.log")

# ── Log ──────────────────────────────────────────────────────
def log(msg, level="INFO"):
    line = f"[{datetime.now().strftime('%d/%m %H:%M:%S')}] [{level}] {msg}"
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        lines = []
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
        if len(lines) > 400:
            lines = lines[-200:]
        lines.append(line + "\n")
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            f.writelines(lines)
    except:
        pass

# ── Verifica porta ────────────────────────────────────────────
def porta_aberta():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2)
        ok = s.connect_ex(("127.0.0.1", PORT)) == 0
        s.close()
        return ok
    except:
        return False

# ── Health check ─────────────────────────────────────────────
def health():
    try:
        req = urllib.request.Request(
            f"http://localhost:{PORT}/health",
            headers={"User-Agent": "Watchdog"}
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode())
    except:
        return None

# ── Inicia servidor ──────────────────────────────────────────
def iniciar():
    log("Iniciando servidor...", "START")
    proc = subprocess.Popen(
        [sys.executable, SERVER_FILE],
        cwd=BASE_DIR
        # SEM stdout=PIPE — evita travamento no Windows
    )
    log(f"Processo criado. PID={proc.pid}", "START")

    # Aguarda a porta abrir (até 60s)
    log("Aguardando porta 8080...", "START")
    for i in range(30):
        time.sleep(2)
        if porta_aberta():
            log(f"Servidor pronto! ({(i+1)*2}s)", "OK")
            return proc
    log("Porta não abriu em 60s — verifique o servidor.", "ERRO")
    return proc

# ── Para servidor ────────────────────────────────────────────
def parar(proc):
    if proc is None: return
    try:
        log(f"Encerrando PID={proc.pid}...", "STOP")
        proc.terminate()
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        try: proc.kill()
        except: pass
    except: pass

# ── Reinicia ─────────────────────────────────────────────────
restarts_hora = 0
hora_inicio   = time.time()

def reiniciar(proc, motivo):
    global restarts_hora, hora_inicio
    if time.time() - hora_inicio > 3600:
        restarts_hora = 0
        hora_inicio = time.time()
    if restarts_hora >= MAX_PER_HORA:
        log(f"Muitos reinícios ({MAX_PER_HORA}/hora). Aguardando 5 min.", "ALERTA")
        time.sleep(300)
        restarts_hora = 0
        hora_inicio = time.time()
    log(f"Reiniciando. Motivo: {motivo}", "RESTART")
    parar(proc)
    time.sleep(3)
    novo = iniciar()
    restarts_hora += 1
    return novo

# ── Main ─────────────────────────────────────────────────────
def main():
    log("=" * 50, "INFO")
    log("  Garfio Watchdog iniciado", "INFO")
    log(f"  Verificação a cada {CHECK_INTERVAL}s", "INFO")
    log("=" * 50, "INFO")

    proc = iniciar()

    # Abre navegador só quando porta estiver de fato aberta
    if porta_aberta():
        try:
            webbrowser.open(f"http://localhost:{PORT}")
            log("Navegador aberto.", "OK")
        except:
            log(f"Abra: http://localhost:{PORT}", "INFO")
    else:
        log(f"Abra manualmente: http://localhost:{PORT}", "INFO")

    fails = 0
    try:
        while True:
            time.sleep(CHECK_INTERVAL)

            # Processo morreu?
            if proc and proc.poll() is not None:
                log(f"Processo encerrou (código={proc.returncode})", "ERRO")
                proc = reiniciar(proc, "processo encerrado")
                fails = 0
                continue

            # Porta fechou?
            if not porta_aberta():
                fails += 1
                log(f"Sem resposta na porta {PORT} ({fails}/{FAIL_THRESHOLD})", "WARN")
                if fails >= FAIL_THRESHOLD:
                    proc = reiniciar(proc, f"sem resposta após {FAIL_THRESHOLD} tentativas")
                    fails = 0
                continue

            # Tudo ok
            fails = 0
            h = health()
            if h:
                mem = h.get("mem_mb", 0)
                log(f"OK | uptime={h.get('uptime','--')} | reqs={h.get('requests',0)} | erros={h.get('errors',0)} | mem={mem}MB", "OK")
                if mem and mem > MEM_LIMIT_MB:
                    log(f"RAM alta: {mem}MB", "WARN")
                    proc = reiniciar(proc, f"RAM alta ({mem}MB)")
            else:
                log(f"Porta {PORT} ativa.", "OK")

    except KeyboardInterrupt:
        log("Encerrando...", "INFO")
        parar(proc)
        log("Watchdog encerrado.", "INFO")

if __name__ == "__main__":
    main()
