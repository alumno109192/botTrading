"""
_test_fix_conn.py — Diagnóstico de conexión FIX (cTrader / Pepperstone)

Pasos que prueba:
  1. Resolución DNS del hostname
  2. Conexión TCP cruda (timeout 10 s) a Quote y Trade
  3. SSL handshake
  4. Envío de Logon FIX 4.4 y espera respuesta (10 s)

Uso:
    .venv\Scripts\python.exe _test_fix_conn.py
"""

import os, ssl, socket, time, sys, struct
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

HOST        = os.getenv('FIX_HOST',        'demo-us-eqx-01.p.c-trader.com')
QUOTE_PORT  = int(os.getenv('FIX_QUOTE_PORT', '5211'))
TRADE_PORT  = int(os.getenv('FIX_TRADE_PORT', '5212'))
SENDER      = os.getenv('FIX_SENDER_COMP', 'demo.pepperstone.5288500')
TARGET      = os.getenv('FIX_TARGET_COMP', 'cServer')
USERNAME    = os.getenv('FIX_USERNAME',    '5288500')
PASSWORD    = os.getenv('FIX_PASSWORD',    '')
SOH         = b'\x01'

# ──────────────────────────────────────────────────────────────
def sep(label): print(f"\n{'─'*60}\n  {label}\n{'─'*60}")

def ok(msg):  print(f"  ✅  {msg}")
def err(msg): print(f"  ❌  {msg}")
def inf(msg): print(f"  ℹ️   {msg}")

# ──────────────────────────────────────────────────────────────
# 1. DNS
# ──────────────────────────────────────────────────────────────
sep(f"1. DNS → {HOST}")
try:
    ips = socket.getaddrinfo(HOST, None)
    ip4 = [r[4][0] for r in ips if r[0] == socket.AF_INET]
    ok(f"Resuelto: {ip4 or [r[4][0] for r in ips]}")
except socket.gaierror as e:
    err(f"No se puede resolver: {e}")
    sys.exit(1)

# ──────────────────────────────────────────────────────────────
# 2. TCP + SSL en ambos puertos
# ──────────────────────────────────────────────────────────────
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode    = ssl.CERT_NONE

sockets = {}
for label, port in [('QUOTE', QUOTE_PORT), ('TRADE', TRADE_PORT)]:
    sep(f"2/{label} — TCP+SSL → {HOST}:{port}")
    try:
        t0 = time.perf_counter()
        raw = socket.create_connection((HOST, port), timeout=10)
        ms_tcp = (time.perf_counter() - t0) * 1000
        ok(f"TCP conectado en {ms_tcp:.0f} ms")

        t1 = time.perf_counter()
        ssl_sock = ctx.wrap_socket(raw, server_hostname=HOST)
        ms_ssl = (time.perf_counter() - t1) * 1000
        ok(f"SSL handshake OK en {ms_ssl:.0f} ms  [{ssl_sock.version()}]")
        sockets[label] = ssl_sock
    except socket.timeout:
        err("TCP timeout (10 s) — firewall o puerto bloqueado")
    except ConnectionRefusedError:
        err("Conexión rechazada — puerto cerrado")
    except ssl.SSLError as e:
        err(f"SSL error: {e}")
    except OSError as e:
        err(f"OSError: {e}")

if not sockets:
    print("\n[DIAGNÓSTICO] No se pudo conectar a ningún puerto. Verifica:")
    print("  - Que el hostname es correcto")
    print("  - Que el firewall/ISP no bloquea los puertos 5211/5212")
    sys.exit(1)

# ──────────────────────────────────────────────────────────────
# 3. Logon FIX 4.4 (sobre la sesión TRADE si disponible)
# ──────────────────────────────────────────────────────────────
def build_logon(sender: str, target: str, username: str, password: str) -> bytes:
    sender_b  = sender.encode()
    target_b  = target.encode()
    username_b = username.encode()
    password_b = password.encode()
    now = datetime.now(timezone.utc)
    ts  = f"{now.strftime('%Y%m%d-%H:%M:%S')}.{now.microsecond//1000:03d}".encode()

    sub = b'TRADE'  # SenderSubID + TargetSubID

    body_parts = [
        b'35=A' + SOH,
        b'49=' + sender_b  + SOH,
        b'56=' + target_b  + SOH,
        b'50=' + sub        + SOH,   # SenderSubID
        b'57=' + sub        + SOH,   # TargetSubID
        b'34=1'             + SOH,
        b'52=' + ts         + SOH,
        b'98=0'             + SOH,   # EncryptMethod = None
        b'108=30'           + SOH,   # HeartBtInt
        b'141=Y'            + SOH,   # ResetOnLogon
        b'553=' + username_b + SOH,  # Username
        b'554=' + password_b + SOH,  # Password
    ]
    body     = b''.join(body_parts)
    body_len = str(len(body)).encode()
    head     = b'8=FIX.4.4' + SOH + b'9=' + body_len + SOH
    full     = head + body
    chk      = sum(full) % 256
    full    += b'10=' + f'{chk:03d}'.encode() + SOH
    return full

def recv_until_checksum(sock, timeout=10.0) -> bytes:
    """Lee hasta recibir el tag 10= (fin de mensaje FIX)."""
    sock.settimeout(timeout)
    buf = b''
    try:
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
            if b'10=' in buf and buf.rstrip(b'\x01 ').endswith(b'\x01'):
                end = buf.find(b'10=')
                end2 = buf.find(SOH, end)
                if end2 != -1:
                    return buf[:end2 + 1]
    except socket.timeout:
        pass
    return buf

def parse_fields(raw: bytes) -> dict:
    fields = {}
    for part in raw.split(SOH):
        if b'=' in part:
            tag_b, _, val = part.partition(b'=')
            try: fields[int(tag_b)] = val.decode(errors='replace')
            except ValueError: pass
    return fields

if not PASSWORD:
    err("FIX_PASSWORD no está configurado — no se puede probar el Logon")
else:
    label = 'TRADE' if 'TRADE' in sockets else list(sockets.keys())[0]
    sock  = sockets[label]
    sep(f"3. Logon FIX 4.4 (sesión {label})")
    inf(f"SenderCompID : {SENDER}")
    inf(f"TargetCompID : {TARGET}")
    inf(f"Username     : {USERNAME}")

    logon_msg = build_logon(SENDER, TARGET, USERNAME, PASSWORD)
    inf(f"Mensaje Logon ({len(logon_msg)} bytes):\n"
        f"  {logon_msg.replace(SOH, b'|').decode(errors='replace')}")

    try:
        sock.sendall(logon_msg)
        ok("Logon enviado, esperando respuesta (10 s)...")

        resp = recv_until_checksum(sock, timeout=10.0)
        if resp:
            fields = parse_fields(resp)
            inf(f"Respuesta raw:\n  {resp.replace(SOH, b'|').decode(errors='replace')}")
            mt = fields.get(35, '?')
            if mt == 'A':
                ok("¡Logon confirmado! La sesión FIX está activa ✅")
            elif mt == '5':
                reason = fields.get(58, '—')
                err(f"Logout recibido — motivo: {reason}")
            elif mt == '3':
                reason = fields.get(58, '—')
                err(f"Reject recibido — motivo: {reason}")
            else:
                inf(f"MsgType inesperado: {mt} | tags: {fields}")
        else:
            err("Sin respuesta en 10 s (el servidor no respondió al Logon)")

    except socket.timeout:
        err("Timeout esperando respuesta al Logon")
    except OSError as e:
        err(f"Error al enviar/recibir: {e}")

# ──────────────────────────────────────────────────────────────
# 4. Cerrar
# ──────────────────────────────────────────────────────────────
for s in sockets.values():
    try: s.close()
    except Exception: pass

sep("FIN DEL DIAGNÓSTICO")
