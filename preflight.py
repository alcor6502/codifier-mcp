"""
preflight.py — 9 controlli che NON avvisano: se uno fallisce esce 2 e il
servizio NON parte (un controllo che crasha conta come FALLITO, non passato).

Core identici al vault-mcp: oauth · log · token_store · funnel · chiave ·
dns_pubblico. Di dominio, nuovi: db (apribile, integro, WAL) · schema (tabelle
E TRIGGER presenti: senza trigger lo storico non si scrive e nessuno se ne
accorge) · codice (ADMIN_ACCESS_CODE presente e non placeholder) · proprieta'
(il processo e' root e il database NON e' scrivibile da altri: se lo diventasse,
qualcuno potrebbe modificarlo dalla share aggirando i trigger).

SKIP selettivo (solo per collaudo, mai in produzione):
  PREFLIGHT_SKIP="funnel,chiave"
"""
from __future__ import annotations
import os, sqlite3, subprocess, sys, secrets

SKIP = {s.strip() for s in os.environ.get("PREFLIGHT_SKIP", "").split(",") if s.strip()}
ESITI: list[tuple[str, bool, str]] = []


def check(nome):
    def deco(fn):
        def run():
            if nome in SKIP:
                ESITI.append((nome, True, "SALTATO (PREFLIGHT_SKIP)")); return
            try:
                msg = fn()
                ESITI.append((nome, True, msg or "ok"))
            except Exception as e:  # crash = fallito
                ESITI.append((nome, False, f"{type(e).__name__}: {e}"))
        return run
    return deco


DB = os.environ.get("DB_PATH", "/db/regole.db")
DBDIR = os.path.dirname(DB) or "/db"

TABELLE = ("rules", "rule_roles", "rule_refs", "rule_versions")
TRIGGER = ("trg_rules_ins", "trg_rules_upd", "trg_rules_del")


@check("db")
def c_db():
    from rules import Registro          # crea lo schema se il db e' nuovo
    r = Registro(DB)
    integ = r.cx.execute("PRAGMA integrity_check").fetchone()[0]
    if integ != "ok":
        raise RuntimeError(f"integrity_check: {integ} — ripristina dallo snapshot ZFS")
    jm = r.cx.execute("PRAGMA journal_mode").fetchone()[0]
    if jm.lower() != "wal":
        raise RuntimeError(f"journal_mode={jm}: atteso WAL (il mount supporta il locking?)")
    n = r.cx.execute("SELECT COUNT(*) FROM rules").fetchone()[0]
    rip = r.riparato
    r.cx.close()
    if rip:
        # non e' un guasto (lo schema si e' riparato da solo) ma non deve essere
        # muto: qualcuno aveva tolto questi oggetti dal database.
        return f"{DB}: integro, WAL, {n} regole — RIPRISTINATI: {', '.join(rip)}"
    return f"{DB}: integro, WAL, {n} regole"


@check("schema")
def c_schema():
    # un trigger mancante non da' errori: smette solo di scrivere lo storico,
    # in silenzio. Si controlla per nome, uno per uno.
    cx = sqlite3.connect(DB, timeout=10)
    nomi = {r[0] for r in cx.execute("SELECT name FROM sqlite_master")}
    cx.close()
    cols = {r[1] for r in cx2.execute("PRAGMA table_info(projects)")} if (cx2 := sqlite3.connect(DB, timeout=10)) else set()
    cx2.close()
    if "codice" not in cols:
        raise RuntimeError("la tabella projects non ha la colonna `codice`: database di uno "
                           "schema precedente. Ricrealo o migralo prima di partire.")
    mancanti = [x for x in TABELLE + TRIGGER if x not in nomi]
    if mancanti:
        raise RuntimeError(f"mancano nello schema: {', '.join(mancanti)} — "
                           "il ripristino automatico non ha funzionato")
    return f"{len(TABELLE)} tabelle + {len(TRIGGER)} trigger (post-condizione)"


@check("scrittura")
def c_scrittura():
    p = os.path.join(DBDIR, f".preflight-{secrets.token_hex(4)}")
    open(p, "w").write("x")
    os.unlink(p)  # su certi mount la cancellazione fallisce dove la scrittura riesce
    return f"{DBDIR}: scrive E cancella"


@check("codice")
def c_codice():
    v = os.environ.get("ADMIN_ACCESS_CODE", "")
    if not v or "CAMBIAMI" in v:
        raise RuntimeError("ADMIN_ACCESS_CODE mancante o placeholder: senza, la scrittura "
                           "sarebbe aperta a qualunque chat")
    if len(v) < 12:
        raise RuntimeError(f"ADMIN_ACCESS_CODE di {len(v)} caratteri: troppo corto (>=12)")
    return f"presente ({len(v)} caratteri)"


@check("proprieta")
def c_proprieta():
    # Qui, al contrario del vault, i file NON devono essere scrivibili da fuori:
    # una modifica fatta dalla share aggira i trigger e rompe lo storico in
    # silenzio. Il processo deve essere root e il db non scrivibile da altri.
    if os.geteuid() != 0:
        raise RuntimeError(f"il processo gira come uid {os.geteuid()}, non root: "
                           "i file del database nascerebbero di un altro utente")
    st = os.stat(DB)
    if st.st_uid != 0:
        raise RuntimeError(f"{DB} appartiene a uid {st.st_uid}, non a root")
    if st.st_mode & 0o022:
        raise RuntimeError(f"{DB} e' {oct(st.st_mode & 0o777)}: scrivibile da gruppo o altri. "
                           "Deve essere 644 — dalla share si legge e basta.")
    return f"root, {oct(st.st_mode & 0o777)} (dalla share: sola lettura)"


@check("oauth")
def c_oauth():
    # il controllo piu' importante: senza credenziali il servizio sarebbe un
    # Funnel authless indicizzato dai CT logs in due minuti.
    for k in ("GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET", "ALLOWED_GITHUB_LOGIN", "BASE_URL"):
        v = os.environ.get(k, "")
        if not v or "CAMBIAMI" in v:
            raise RuntimeError(f"{k} mancante o placeholder")
    if len(os.environ.get("JWT_SIGNING_KEY", "")) < 32:
        raise RuntimeError("JWT_SIGNING_KEY assente o corta (openssl rand -hex 32)")
    if not os.environ["BASE_URL"].startswith("https://"):
        raise RuntimeError("BASE_URL deve essere https")
    return "credenziali presenti"


@check("log")
def c_log():
    d = os.environ.get("LOG_DIR", "/data/log")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, ".w"); open(p, "w").write("x"); os.unlink(p)
    return d


@check("token_store")
def c_token_store():
    h = os.environ.get("FASTMCP_HOME", "")
    if not h.startswith("/data"):
        raise RuntimeError(f"FASTMCP_HOME={h!r}: deve stare sotto /data (volume persistente)")
    os.makedirs(h, exist_ok=True)
    p = os.path.join(h, ".w"); open(p, "w").write("x"); os.unlink(p)
    return h


@check("funnel")
def c_funnel():
    r = subprocess.run(["tailscale", "funnel", "status"], capture_output=True, text=True, timeout=10)
    out = r.stdout + r.stderr
    if r.returncode != 0:
        raise RuntimeError(f"tailscale funnel status: {out.strip()[:200]}")
    if "Funnel on" not in out:
        raise RuntimeError(f"Funnel NON attivo: {out.strip()[:200]}")
    porta = os.environ.get("PORT", "3001")
    if porta not in out:
        raise RuntimeError(f"Funnel attivo ma non verso la porta {porta}: {out.strip()[:200]}")
    return "Funnel on, porta giusta"


@check("chiave")
def c_chiave():
    # "expires in 179 days" e' un guasto programmato silenzioso.
    r = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise RuntimeError("tailscale status non risponde")
    import json
    ke = (json.loads(r.stdout).get("Self") or {}).get("KeyExpiry")
    if ke:
        raise RuntimeError(f"la chiave del nodo SCADE ({ke}): disattiva la scadenza su "
                           "login.tailscale.com/admin/machines")
    return "scadenza chiave disattivata"


@check("dns_pubblico")
def c_dns():
    import socket
    host = os.environ["BASE_URL"].split("//", 1)[1].split("/")[0]
    socket.getaddrinfo(host, 443)
    return f"{host} risolve"


if __name__ == "__main__":
    controlli = [c_db, c_schema, c_scrittura, c_proprieta, c_codice, c_oauth,
                 c_log, c_token_store, c_funnel, c_chiave, c_dns]
    for fn in controlli:
        fn()
    larg = max(len(n) for n, _, _ in ESITI)
    ko = 0
    for nome, passed, msg in ESITI:
        print(f"  {'OK ' if passed else 'FAIL'}  {nome:<{larg}}  {msg}")
        ko += 0 if passed else 1
    if ko:
        print(f"PREFLIGHT: {ko} controlli falliti — il servizio NON parte.")
        sys.exit(2)
    print(f"PREFLIGHT: {len(ESITI)}/{len(ESITI)} — si parte.")
