"""
rules.py — registro delle regole su SQLite. UN database, N progetti.

v2.0 — le tre differenze rispetto alla v1.0:
1. MULTI-PROGETTO. Il progetto e' una colonna, non una tabella: stesso schema,
   una query sola, e la chiave e' (progetto, id) — cosi' VA-02 di Financial
   Portfolio e VA-02 di Health Tracking convivono senza collidere.
2. RUOLI E DOMINI SONO DATI, non costanti del codice: ogni progetto dichiara i
   suoi. Un progetto nuovo non richiede una riga di Python.
   Il progetto NON si indirizza col nome ma con un CODICE alfanumerico opaco
   che sta in testa alle istruzioni del progetto: chi non ce l'ha non arriva al
   registro sbagliato per distrazione. ⚠ E' una protezione contro l'ERRORE, non
   contro la volonta': una chat a cui si dia il codice di un altro progetto ci
   scrive. Il confine vero e' il gate OAuth, che sta a monte.
   Corollario NON negoziabile: nessun errore elenca i progetti esistenti. Un
   messaggio che dicesse "progetti: A, B, C" regalerebbe cio' che il codice
   protegge — e per la stessa ragione non si dice mai che un ID esiste altrove.
3. IL DATABASE E' DI ROOT. Il processo gira come root e i file nascono 644:
   chi monta la share via SMB lo LEGGE e non lo tocca. Qui, al contrario del
   vault, la scrittura a mano non e' una comodita' — e' il modo di rompere lo
   storico senza accorgersene.

Principi invariati: l'ID e' un puntatore e non si riusa MAI; lo storico lo
scrivono i TRIGGER (non il codice, quindi non e' aggirabile per distrazione);
cancellare non esiste, si ritira; ogni operazione restituisce un VERDETTO.
"""
from __future__ import annotations

import difflib
import hashlib
import os
import re
import secrets
import sqlite3
from datetime import datetime, timezone

TIPI = ("R", "M", "F")
TUTTI = "_ALL_"                  # perimetro speciale: la regola vale per chiunque.
                                 # Token esplicito e non ambiguo: "*" si legge come
                                 # un glob e in un elenco di ruoli sembra un errore.
ALIAS_TUTTI = {"_all_", "*", "tutti", "chiunque"}   # tollerati in ingresso
RE_ID = re.compile(r"^([A-Z]{2})-(\d{2,3})$")
RE_SIGLA = re.compile(r"\b([A-Z]{2})-(\d{2,3})\b")
MODO_FILE = 0o644                # root scrive, tutti gli altri leggono e basta
MODO_DIR = 0o755
RE_CODICE = re.compile(r"^[A-Za-z0-9]{8,32}$")
ERR_PROGETTO = ("progetto non specificato: serve il CODICE del progetto, quello in testa "
                "alle sue istruzioni. Senza, il registro non risponde — e non esiste un "
                "modo di elencarli: o ce l'hai, o lo chiedi ad Alfredo.")


class RulesError(Exception):
    """Errore parlante: dice cosa e' successo E cosa fare."""


def _ora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _proprietario(path: str) -> str:
    st = os.stat(path)
    return f"{st.st_uid}:{st.st_gid}" + (" (root)" if st.st_uid == 0 else "")


def _norm_id(rid: str) -> str:
    s = (rid or "").strip().upper()
    if s.count("-") == 2:        # 'VA-02-R': la citazione va NUDA, ma non litighiamo
        s = s.rsplit("-", 1)[0]
    if not RE_ID.match(s):
        raise RulesError(f"ID {rid!r} malformato: serve DOMINIO-NN, es. VA-02")
    return s


SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
  nome        TEXT PRIMARY KEY,
  codice      TEXT NOT NULL UNIQUE,   -- handle opaco: l'unico modo di indirizzarlo
  descrizione TEXT,
  creato      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_roles (
  progetto TEXT NOT NULL REFERENCES projects(nome) ON DELETE CASCADE,
  ruolo    TEXT NOT NULL,
  PRIMARY KEY (progetto, ruolo)
);

CREATE TABLE IF NOT EXISTS project_domains (
  progetto    TEXT NOT NULL REFERENCES projects(nome) ON DELETE CASCADE,
  dominio     TEXT NOT NULL,
  descrizione TEXT,
  PRIMARY KEY (progetto, dominio)
);

CREATE TABLE IF NOT EXISTS rules (
  progetto      TEXT NOT NULL REFERENCES projects(nome) ON DELETE CASCADE,
  id            TEXT NOT NULL,
  domain        TEXT NOT NULL,
  seq           INTEGER NOT NULL,
  tipo          TEXT NOT NULL CHECK (tipo IN ('R','M','F')),
  titolo        TEXT NOT NULL,
  corpo         TEXT NOT NULL,          -- MARKDOWN libero, scritto a mano e reso
                                        -- verbatim: l'unica cosa che ne viene
                                        -- estratta sono le sigle citate (rule_refs)
  stato         TEXT NOT NULL DEFAULT 'attiva' CHECK (stato IN ('attiva','ritirata')),
  superseded_by TEXT,
  changelog     TEXT,
  fonte         TEXT,
  motivo        TEXT NOT NULL DEFAULT 'creazione',
  updated_at    TEXT NOT NULL,
  PRIMARY KEY (progetto, id),
  UNIQUE (progetto, domain, seq)
);

CREATE TABLE IF NOT EXISTS rule_roles (
  progetto TEXT NOT NULL,
  rule_id  TEXT NOT NULL,
  ruolo    TEXT NOT NULL,
  PRIMARY KEY (progetto, rule_id, ruolo),
  FOREIGN KEY (progetto, rule_id) REFERENCES rules(progetto, id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS rule_refs (
  progetto TEXT NOT NULL,
  src      TEXT NOT NULL,
  dst      TEXT NOT NULL,
  PRIMARY KEY (progetto, src, dst),
  FOREIGN KEY (progetto, src) REFERENCES rules(progetto, id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS rule_versions (
  progetto      TEXT NOT NULL,
  rule_id       TEXT NOT NULL,
  versione      INTEGER NOT NULL,
  tipo          TEXT,
  titolo        TEXT,
  corpo         TEXT,
  stato         TEXT,
  superseded_by TEXT,
  changelog     TEXT,
  ruoli         TEXT,
  ts            TEXT NOT NULL,
  azione        TEXT NOT NULL,
  motivo        TEXT,
  PRIMARY KEY (progetto, rule_id, versione)
);

CREATE INDEX IF NOT EXISTS ix_roles_ruolo ON rule_roles(progetto, ruolo);
CREATE INDEX IF NOT EXISTS ix_refs_dst    ON rule_refs(progetto, dst);

-- Lo storico lo scrive il MOTORE, non il codice del tool.
CREATE TRIGGER IF NOT EXISTS trg_rules_ins AFTER INSERT ON rules BEGIN
  INSERT INTO rule_versions (progetto, rule_id, versione, tipo, titolo, corpo, stato,
                             superseded_by, changelog, ruoli, ts, azione, motivo)
  VALUES (NEW.progetto, NEW.id,
          (SELECT IFNULL(MAX(versione),0)+1 FROM rule_versions
             WHERE progetto=NEW.progetto AND rule_id=NEW.id),
          NEW.tipo, NEW.titolo, NEW.corpo, NEW.stato, NEW.superseded_by, NEW.changelog,
          (SELECT IFNULL(GROUP_CONCAT(ruolo,','),'') FROM rule_roles
             WHERE progetto=NEW.progetto AND rule_id=NEW.id),
          NEW.updated_at, 'creata', NEW.motivo);
END;

CREATE TRIGGER IF NOT EXISTS trg_rules_upd AFTER UPDATE ON rules BEGIN
  INSERT INTO rule_versions (progetto, rule_id, versione, tipo, titolo, corpo, stato,
                             superseded_by, changelog, ruoli, ts, azione, motivo)
  VALUES (NEW.progetto, NEW.id,
          (SELECT IFNULL(MAX(versione),0)+1 FROM rule_versions
             WHERE progetto=NEW.progetto AND rule_id=NEW.id),
          NEW.tipo, NEW.titolo, NEW.corpo, NEW.stato, NEW.superseded_by, NEW.changelog,
          (SELECT IFNULL(GROUP_CONCAT(ruolo,','),'') FROM rule_roles
             WHERE progetto=NEW.progetto AND rule_id=NEW.id),
          NEW.updated_at, 'modificata', NEW.motivo);
END;

-- Rete di sicurezza: se qualcuno cancella a mano, resta la traccia.
CREATE TRIGGER IF NOT EXISTS trg_rules_del AFTER DELETE ON rules BEGIN
  INSERT INTO rule_versions (progetto, rule_id, versione, tipo, titolo, corpo, stato,
                             superseded_by, changelog, ruoli, ts, azione, motivo)
  VALUES (OLD.progetto, OLD.id,
          (SELECT IFNULL(MAX(versione),0)+1 FROM rule_versions
             WHERE progetto=OLD.progetto AND rule_id=OLD.id),
          OLD.tipo, OLD.titolo, OLD.corpo, OLD.stato, OLD.superseded_by, OLD.changelog,
          '', strftime('%Y-%m-%dT%H:%M:%SZ','now'), 'CANCELLATA', 'DELETE fuori dai tool');
END;
"""


class Registro:
    def __init__(self, db_path: str) -> None:
        self.path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        nuovo = not os.path.exists(db_path)
        self.cx = sqlite3.connect(db_path, timeout=10, isolation_level=None)
        self.cx.row_factory = sqlite3.Row
        self.cx.execute("PRAGMA journal_mode=WAL")
        self.cx.execute("PRAGMA synchronous=FULL")
        self.cx.execute("PRAGMA foreign_keys=ON")
        self.cx.execute("PRAGMA busy_timeout=10000")
        # Lo schema si riapplica a ogni apertura: se un oggetto manca — tipicamente
        # un TRIGGER droppato a mano — viene rifatto. Ma la riparazione si DICHIARA:
        # un trigger che sparisce non da' errori, smette solo di scrivere lo storico.
        prima = {r[0] for r in self.cx.execute("SELECT name FROM sqlite_master")}
        self.cx.executescript(SCHEMA)
        dopo = {r[0] for r in self.cx.execute("SELECT name FROM sqlite_master")}
        self.riparato = [] if nuovo else sorted(dopo - prima)
        self._modi()

    def _modi(self) -> None:
        """644 e' VOLUTO: chi monta la share legge e non tocca."""
        for f in (self.path, self.path + "-wal", self.path + "-shm"):
            if os.path.exists(f):
                try:
                    os.chmod(f, MODO_FILE)
                except OSError:
                    pass

    # ---------- progetti ----------

    def _progetto(self, progetto: str) -> str:
        """Dal CODICE al nome interno. Mai dal nome: il nome non e' una chiave
        d'accesso. Errore identico per codice mancante e codice sbagliato — un
        messaggio che distinguesse i due casi sarebbe un oracolo."""
        p = (progetto or "").strip()
        if not p:
            raise RulesError(ERR_PROGETTO)
        row = self.cx.execute("SELECT nome FROM projects WHERE codice=?", (p,)).fetchone()
        if row is None:
            raise RulesError(ERR_PROGETTO)
        return row[0]

    def _ruolo(self, progetto: str, ruolo: str) -> str:
        r = (ruolo or "").strip().lower()
        ammessi = [x[0] for x in self.cx.execute(
            "SELECT ruolo FROM project_roles WHERE progetto=? ORDER BY ruolo", (progetto,))]
        if not r:
            raise RulesError(f"ruolo mancante: dichiara chi sei. Ruoli di {progetto}: "
                             + ", ".join(ammessi))
        if r not in ammessi:
            raise RulesError(f"ruolo {ruolo!r} non esiste in {progetto}. Ruoli: " + ", ".join(ammessi))
        return r

    def _domini(self, progetto: str) -> list[str]:
        return [r[0] for r in self.cx.execute(
            "SELECT dominio FROM project_domains WHERE progetto=? ORDER BY dominio", (progetto,))]

    def progetti(self) -> dict:
        """Elenco COMPLETO, codici inclusi. Il server lo espone solo dietro il
        codice di scrittura: e' l'unica porta da cui i codici possono uscire."""
        out = []
        for p in self.cx.execute("SELECT * FROM projects ORDER BY nome"):
            out.append({
                "progetto": p["nome"], "codice": p["codice"], "descrizione": p["descrizione"],
                "regole_attive": self.cx.execute(
                    "SELECT COUNT(*) FROM rules WHERE progetto=? AND stato='attiva'",
                    (p["nome"],)).fetchone()[0],
                "ruoli": [r[0] for r in self.cx.execute(
                    "SELECT ruolo FROM project_roles WHERE progetto=? ORDER BY ruolo", (p["nome"],))],
                "domini": self._domini(p["nome"])})
        return {"progetti": out, "conteggio": len(out)}

    def info_progetto(self, progetto: str) -> dict:
        """Cosa c'e' DENTRO il progetto di cui hai il codice: ruoli e domini.
        Non nomina nessun altro progetto e non ripete il codice."""
        p = self._progetto(progetto)
        return {"progetto": p,
                "ruoli": [r[0] for r in self.cx.execute(
                    "SELECT ruolo FROM project_roles WHERE progetto=? ORDER BY ruolo", (p,))],
                "domini": {r["dominio"]: r["descrizione"] for r in self.cx.execute(
                    "SELECT dominio, descrizione FROM project_domains WHERE progetto=? "
                    "ORDER BY dominio", (p,))},
                "perimetro_speciale": TUTTI,
                "nota": "usa uno di questi ruoli in rules_list. Le regole con perimetro "
                        f"{TUTTI} arrivano a chiunque, sempre."}

    def crea_progetto(self, codice: str, nome: str, ruoli: list[str], domini,
                      descrizione: str = "") -> dict:
        n = (nome or "").strip()
        cod = (codice or "").strip()
        if not n:
            raise RulesError("nome del progetto mancante")
        if not RE_CODICE.match(cod):
            raise RulesError("codice del progetto: 8-32 caratteri alfanumerici, senza spazi "
                             "ne' simboli. Lo generi tu e lo metti in testa alle istruzioni "
                             "del progetto.")
        if self.cx.execute("SELECT 1 FROM projects WHERE codice=?", (cod,)).fetchone():
            raise RulesError("codice gia' in uso: generane un altro")
        if self.cx.execute("SELECT 1 FROM projects WHERE nome=? COLLATE NOCASE", (n,)).fetchone():
            raise RulesError(f"progetto {n!r} esiste gia'")
        rl = sorted({str(x).strip().lower() for x in (ruoli or []) if str(x).strip()})
        if not rl:
            raise RulesError("un progetto senza ruoli non lo puo' leggere nessuno")
        if any(x in ALIAS_TUTTI for x in rl):
            raise RulesError(f"{TUTTI} non e' un ruolo: e' il perimetro che li comprende "
                             "tutti, e si usa nel campo `ruoli` di una regola")
        dom = domini if isinstance(domini, dict) else {str(d).strip().upper(): "" for d in (domini or [])}
        dom = {k.strip().upper(): v for k, v in dom.items() if str(k).strip()}
        if not dom:
            raise RulesError("servono i domini delle sigle, es. {'VA': 'vault e file'}")
        for k in dom:
            if not re.match(r"^[A-Z]{2}$", k):
                raise RulesError(f"dominio {k!r}: due lettere maiuscole (es. VA)")
        try:
            self.cx.execute("BEGIN IMMEDIATE")
            self.cx.execute("INSERT INTO projects (nome, codice, descrizione, creato) "
                            "VALUES (?,?,?,?)", (n, cod, descrizione.strip() or None, _ora()))
            for r in rl:
                self.cx.execute("INSERT INTO project_roles (progetto, ruolo) VALUES (?,?)", (n, r))
            for k, v in sorted(dom.items()):
                self.cx.execute("INSERT INTO project_domains (progetto, dominio, descrizione) "
                                "VALUES (?,?,?)", (n, k, (v or None)))
            self.cx.execute("COMMIT")
        except Exception:
            self.cx.execute("ROLLBACK")
            raise
        return {"esito": "progetto creato", "progetto": n, "codice": cod,
                "ruoli": rl, "domini": sorted(dom),
                "nota": "metti il codice in testa alle istruzioni del progetto: e' "
                        "l'unico modo di arrivare a queste regole"}

    def cambia_codice(self, progetto: str, codice_nuovo: str) -> dict:
        """Rotazione dell'handle. Le regole non si toccano: dentro il registro il
        progetto e' indirizzato per nome, il codice e' solo la porta."""
        p = self._progetto(progetto)
        cod = (codice_nuovo or "").strip()
        if not RE_CODICE.match(cod):
            raise RulesError("codice nuovo: 8-32 caratteri alfanumerici")
        if self.cx.execute("SELECT 1 FROM projects WHERE codice=?", (cod,)).fetchone():
            raise RulesError("codice gia' in uso: generane un altro")
        self.cx.execute("UPDATE projects SET codice=? WHERE nome=?", (cod, p))
        return {"esito": "codice cambiato", "progetto": p, "codice": cod,
                "nota": "aggiorna le istruzioni del progetto PRIMA di chiudere questa chat: "
                        "col vecchio codice non ci si arriva piu'"}

    def aggiorna_progetto(self, progetto: str, ruoli: list[str] | None = None,
                          domini: dict | None = None) -> dict:
        p = self._progetto(progetto)
        agg = {}
        try:
            self.cx.execute("BEGIN IMMEDIATE")
            if ruoli:
                nuovi = sorted({str(x).strip().lower() for x in ruoli if str(x).strip()})
                for r in nuovi:
                    self.cx.execute("INSERT OR IGNORE INTO project_roles (progetto, ruolo) "
                                    "VALUES (?,?)", (p, r))
                agg["ruoli_aggiunti"] = nuovi
            if domini:
                for k, v in domini.items():
                    self.cx.execute("INSERT OR IGNORE INTO project_domains "
                                    "(progetto, dominio, descrizione) VALUES (?,?,?)",
                                    (p, str(k).strip().upper(), v or None))
                agg["domini_aggiunti"] = sorted(str(k).strip().upper() for k in domini)
            self.cx.execute("COMMIT")
        except Exception:
            self.cx.execute("ROLLBACK")
            raise
        if not agg:
            raise RulesError("niente da aggiungere: passa ruoli o domini")
        agg.update({"esito": "progetto aggiornato", "progetto": p,
                    "nota": "ruoli e domini si AGGIUNGONO e basta: toglierli orfanerebbe "
                            "le regole che li usano"})
        return agg

    # ---------- lettura ----------

    def _riga(self, p: str, rid: str):
        return self.cx.execute("SELECT * FROM rules WHERE progetto=? AND id=?", (p, rid)).fetchone()

    def _ruoli(self, p: str, rid: str) -> list[str]:
        return [r[0] for r in self.cx.execute(
            "SELECT ruolo FROM rule_roles WHERE progetto=? AND rule_id=? ORDER BY ruolo", (p, rid))]

    def _versione(self, p: str, rid: str) -> int:
        return int(self.cx.execute(
            "SELECT IFNULL(MAX(versione),0) FROM rule_versions WHERE progetto=? AND rule_id=?",
            (p, rid)).fetchone()[0])

    def _dict(self, row) -> dict:
        d = {k: row[k] for k in row.keys() if k not in ("motivo", "fonte")}
        d["ruoli"] = self._ruoli(row["progetto"], row["id"])
        d["versione"] = self._versione(row["progetto"], row["id"])
        return d

    def regole(self, progetto: str, ruolo: str) -> dict:
        """Solo regole ATTIVE. Le ritirate non si mostrano a chi applica le regole:
        una regola morta in elenco e' un invito a seguirla. Restano raggiungibili
        per ID (le citazioni devono risolvere) e nell'export di manutenzione."""
        p = self._progetto(progetto)
        r = self._ruolo(p, ruolo)
        righe = self.cx.execute(
            "SELECT * FROM rules WHERE progetto=? AND stato='attiva' AND EXISTS "
            "(SELECT 1 FROM rule_roles rr WHERE rr.progetto=rules.progetto AND rr.rule_id=rules.id "
            " AND rr.ruolo IN (?,?)) ORDER BY domain, seq",
            (p, TUTTI, r)).fetchall()
        tot = self.cx.execute("SELECT COUNT(*) FROM rules WHERE progetto=? AND stato='attiva'",
                              (p,)).fetchone()[0]
        fuori = tot - len(righe)
        domini_fuori = [d[0] for d in self.cx.execute(
            "SELECT DISTINCT domain FROM rules WHERE progetto=? AND stato='attiva' AND NOT EXISTS "
            "(SELECT 1 FROM rule_roles rr WHERE rr.progetto=rules.progetto AND rr.rule_id=rules.id "
            " AND rr.ruolo IN (?,?)) ORDER BY domain", (p, TUTTI, r))]
        return {
            "progetto": p, "ruolo": r,
            "regole": [self._dict(x) for x in righe],
            "conteggio": len(righe),
            "fuori_perimetro": fuori,
            "domini_con_regole_altrui": domini_fuori,
            "nota": (f"{fuori} regole attive di {p} esistono ma non sono di questo ruolo. "
                     "Un ID che non trovi qui non e' inesistente: e' di qualcun altro. "
                     "Chiedilo con rules_get e ti dira' di chi e'." if fuori else
                     "questo ruolo vede tutte le regole attive del progetto"),
        }

    def regola(self, progetto: str, rid: str, ruolo: str) -> dict:
        p = self._progetto(progetto)
        i = _norm_id(rid)
        r = self._ruolo(p, ruolo)
        row = self._riga(p, i)
        if row is None:
            # NON si dice se quell'ID esiste in un altro progetto: sarebbe un
            # oracolo sull'esistenza di registri di cui non hai il codice.
            raise RulesError(f"{i}: ID mai definito in {p}. Non e' 'non tuo': non esiste. "
                             "Se lo hai letto in una citazione, la citazione e' rotta: segnalala. "
                             "Se invece ti aspettavi di trovarlo, controlla di aver usato il "
                             "codice del progetto giusto.")
        ruoli = self._ruoli(p, i)
        if TUTTI not in ruoli and r not in ruoli:
            raise RulesError(f"{i} ESISTE in {p} ma non e' di tua competenza "
                             f"(e' di: {', '.join(ruoli)}). Non e' un errore del registro: "
                             "e' il perimetro. Non insistere, chiedilo a chi tiene le regole.")
        d = self._dict(row)
        if row["stato"] == "ritirata":
            d["avviso"] = ("regola RITIRATA: non e' piu' in vigore" +
                           (f", superata da {row['superseded_by']}" if row["superseded_by"] else ""))
        return d

    def cerca(self, progetto: str, q: str, ruolo: str) -> dict:
        p = self._progetto(progetto)
        r = self._ruolo(p, ruolo)
        if not (q or "").strip():
            raise RulesError("cerca: stringa vuota")
        like = f"%{q.strip()}%"
        righe = self.cx.execute(
            "SELECT * FROM rules WHERE progetto=? AND stato='attiva' AND (titolo LIKE ? OR corpo LIKE ?) "
            "AND EXISTS (SELECT 1 FROM rule_roles rr WHERE rr.progetto=rules.progetto "
            " AND rr.rule_id=rules.id AND rr.ruolo IN (?,?)) ORDER BY domain, seq",
            (p, like, like, TUTTI, r)).fetchall()
        nascosti = self.cx.execute(
            "SELECT COUNT(*) FROM rules WHERE progetto=? AND stato='attiva' "
            "AND (titolo LIKE ? OR corpo LIKE ?) AND NOT EXISTS "
            "(SELECT 1 FROM rule_roles rr WHERE rr.progetto=rules.progetto AND rr.rule_id=rules.id "
            " AND rr.ruolo IN (?,?))", (p, like, like, TUTTI, r)).fetchone()[0]
        return {"progetto": p, "ruolo": r, "cercato": q, "trovate": len(righe),
                "fuori_perimetro": nascosti, "regole": [self._dict(x) for x in righe]}

    def stato(self, progetto: str = "") -> dict:
        c = self.cx.execute
        base = {"db": self.path,
                "integrity_check": c("PRAGMA integrity_check").fetchone()[0],
                "journal_mode": c("PRAGMA journal_mode").fetchone()[0],
                "proprietario_file": _proprietario(self.path),
                "modo_file": oct(os.stat(self.path).st_mode & 0o777)}
        if not (progetto or "").strip():
            base.update({
                "progetti": [dict(r) for r in c(
                    "SELECT p.nome AS progetto, "
                    "(SELECT COUNT(*) FROM rules WHERE progetto=p.nome AND stato='attiva') AS attive, "
                    "(SELECT COUNT(*) FROM rules WHERE progetto=p.nome AND stato='ritirata') AS ritirate "
                    "FROM projects p ORDER BY p.nome")],
                "versioni_storico": c("SELECT COUNT(*) FROM rule_versions").fetchone()[0]})
            return base
        p = self._progetto(progetto)
        ultima = c("SELECT rule_id, ts, azione, motivo FROM rule_versions WHERE progetto=? "
                   "ORDER BY ts DESC, rowid DESC LIMIT 1", (p,)).fetchone()
        base.update({
            "progetto": p,
            "attive": c("SELECT COUNT(*) FROM rules WHERE progetto=? AND stato='attiva'", (p,)).fetchone()[0],
            "ritirate": c("SELECT COUNT(*) FROM rules WHERE progetto=? AND stato='ritirata'", (p,)).fetchone()[0],
            "versioni_storico": c("SELECT COUNT(*) FROM rule_versions WHERE progetto=?", (p,)).fetchone()[0],
            "per_dominio": {r["domain"]: r["n"] for r in c(
                "SELECT domain, COUNT(*) n FROM rules WHERE progetto=? AND stato='attiva' "
                "GROUP BY domain ORDER BY domain", (p,))},
            "per_ruolo": {r["ruolo"]: r["n"] for r in c(
                "SELECT rr.ruolo, COUNT(*) n FROM rule_roles rr JOIN rules r "
                "ON r.progetto=rr.progetto AND r.id=rr.rule_id "
                "WHERE rr.progetto=? AND r.stato='attiva' GROUP BY rr.ruolo ORDER BY rr.ruolo", (p,))},
            "ultima_modifica": dict(ultima) if ultima else None})
        return base

    def verifica(self, progetto: str) -> dict:
        p = self._progetto(progetto)
        c = self.cx.execute
        rotti = [dict(r) for r in c(
            "SELECT src, dst FROM rule_refs WHERE progetto=? AND dst NOT IN "
            "(SELECT id FROM rules WHERE progetto=?) ORDER BY src, dst", (p, p))]
        verso_ritirate = [dict(r) for r in c(
            "SELECT src, dst FROM rule_refs WHERE progetto=? AND dst IN "
            "(SELECT id FROM rules WHERE progetto=? AND stato='ritirata') AND src IN "
            "(SELECT id FROM rules WHERE progetto=? AND stato='attiva') ORDER BY src, dst", (p, p, p))]
        senza_ruolo = [r[0] for r in c(
            "SELECT id FROM rules WHERE progetto=? AND stato='attiva' AND id NOT IN "
            "(SELECT rule_id FROM rule_roles WHERE progetto=?) ORDER BY id", (p, p))]
        superseded_rotti = [r[0] for r in c(
            "SELECT id FROM rules WHERE progetto=? AND superseded_by IS NOT NULL AND superseded_by "
            "NOT IN (SELECT id FROM rules WHERE progetto=?)", (p, p))]
        buchi = []
        for d in self._domini(p):
            seqs = sorted(r[0] for r in c("SELECT seq FROM rules WHERE progetto=? AND domain=?", (p, d)))
            if seqs:
                mancanti = [n for n in range(1, max(seqs) + 1) if n not in seqs]
                if mancanti:
                    buchi.append({"dominio": d, "seq_mai_usate": mancanti})
        n = len(rotti) + len(verso_ritirate) + len(senza_ruolo) + len(superseded_rotti)
        return {"progetto": p, "anomalie": n, "puntatori_rotti": rotti,
                "citazioni_verso_ritirate": verso_ritirate,
                "regole_senza_perimetro": senza_ruolo,
                "superseded_by_rotti": superseded_rotti,
                "buchi_di_numerazione": buchi,
                "verdetto": "coerente" if n == 0 else f"{n} anomalie da sanare"}

    # ---------- storico ----------

    def storico(self, progetto: str, rid: str) -> dict:
        p = self._progetto(progetto)
        i = _norm_id(rid)
        righe = self.cx.execute(
            "SELECT versione, ts, azione, motivo, stato, tipo, titolo, LENGTH(corpo) AS byte "
            "FROM rule_versions WHERE progetto=? AND rule_id=? ORDER BY versione", (p, i)).fetchall()
        if not righe:
            raise RulesError(f"{i}: nessuna versione in archivio per {p} (ID mai definito)")
        return {"progetto": p, "id": i, "versioni": len(righe), "storia": [dict(r) for r in righe]}

    def confronta(self, progetto: str, rid: str, v_a: int, v_b: int) -> dict:
        p = self._progetto(progetto)
        i = _norm_id(rid)

        def prendi(v):
            r = self.cx.execute("SELECT * FROM rule_versions WHERE progetto=? AND rule_id=? "
                                "AND versione=?", (p, i, v)).fetchone()
            if r is None:
                disp = [x[0] for x in self.cx.execute(
                    "SELECT versione FROM rule_versions WHERE progetto=? AND rule_id=? "
                    "ORDER BY versione", (p, i))]
                raise RulesError(f"{i}: versione {v} inesistente. Disponibili: {disp}")
            return r

        a, b = prendi(int(v_a)), prendi(int(v_b))

        def testo(r):
            return (f"tipo: {r['tipo']}\nstato: {r['stato']}\nruoli: {r['ruoli']}\n"
                    f"superseded_by: {r['superseded_by']}\nchangelog: {r['changelog']}\n"
                    f"titolo: {r['titolo']}\n\n{r['corpo']}\n").splitlines(keepends=True)

        d = "".join(difflib.unified_diff(testo(a), testo(b),
                                         fromfile=f"{i} v{v_a} ({a['ts']})",
                                         tofile=f"{i} v{v_b} ({b['ts']})", n=2))
        return {"progetto": p, "id": i, "da": int(v_a), "a": int(v_b),
                "motivo_di_arrivo": b["motivo"], "azione": b["azione"],
                "diff": d or "(nessuna differenza)"}

    # ---------- scrittura ----------

    def _refs(self, p: str, rid: str, corpo: str) -> int:
        self.cx.execute("DELETE FROM rule_refs WHERE progetto=? AND src=?", (p, rid))
        dom = set(self._domini(p))     # solo le sigle dei domini DICHIARATI sono riferimenti
        citate = {f"{m.group(1)}-{m.group(2)}" for m in RE_SIGLA.finditer(corpo)
                  if m.group(1) in dom} - {rid}
        for dst in sorted(citate):
            self.cx.execute("INSERT OR IGNORE INTO rule_refs (progetto, src, dst) VALUES (?,?,?)",
                            (p, rid, dst))
        return len(citate)

    def _set_ruoli(self, p: str, rid: str, ruoli: list[str]) -> list[str]:
        ammessi = [x[0] for x in self.cx.execute(
            "SELECT ruolo FROM project_roles WHERE progetto=?", (p,))]
        norm = []
        for x in ruoli or []:
            s = str(x).strip().lower()
            if s in ALIAS_TUTTI:
                norm = [TUTTI]
                break
            if s not in ammessi:
                raise RulesError(f"ruolo {x!r} non esiste in {p}. Ammessi: "
                                 f"{', '.join(sorted(ammessi))} oppure '*' (chiunque)")
            norm.append(s)
        if not norm:
            raise RulesError("perimetro vuoto: una regola senza ruoli non la leggerebbe nessuno. "
                             "Usa ['*'] se vale per chiunque tocchi un file.")
        self.cx.execute("DELETE FROM rule_roles WHERE progetto=? AND rule_id=?", (p, rid))
        for r in sorted(set(norm)):
            self.cx.execute("INSERT INTO rule_roles (progetto, rule_id, ruolo) VALUES (?,?,?)",
                            (p, rid, r))
        return sorted(set(norm))

    def crea(self, progetto: str, rid: str, tipo: str, titolo: str, corpo: str,
             ruoli: list[str], motivo: str, changelog: str | None = None,
             fonte: str | None = None) -> dict:
        p = self._progetto(progetto)
        i = _norm_id(rid)
        dom, seq = i.split("-")[0], int(i.split("-")[1])
        if dom not in self._domini(p):
            raise RulesError(f"dominio {dom} non dichiarato in {p}. Domini: "
                             f"{', '.join(self._domini(p))}. Uno nuovo si aggiunge con "
                             "rules_project_update.")
        t = (tipo or "").strip().upper()
        if t not in TIPI:
            raise RulesError(f"tipo {tipo!r}: ammessi R (vincolante), M (metodo), F (fatto tecnico). "
                             "Il ritiro NON e' un tipo: e' uno stato, si fa con rules_retire.")
        if not (titolo or "").strip() or not (corpo or "").strip():
            raise RulesError("titolo e corpo sono obbligatori")
        if not (motivo or "").strip():
            raise RulesError("motivo obbligatorio: senza il perche' la regola non si difende "
                             "e viene riaperta")
        vecchia = self._riga(p, i)
        if vecchia is not None:
            raise RulesError(f"{i} esiste gia' in {p} (stato: {vecchia['stato']}). "
                             "Gli ID non si riusano MAI: prendi il prossimo libero del dominio.")
        try:
            self.cx.execute("BEGIN IMMEDIATE")
            self.cx.execute(
                "INSERT INTO rules (progetto,id,domain,seq,tipo,titolo,corpo,stato,changelog,"
                "fonte,motivo,updated_at) VALUES (?,?,?,?,?,?,?,'attiva',?,?,?,?)",
                (p, i, dom, seq, t, titolo.strip(), corpo.strip(), changelog, fonte,
                 motivo.strip(), _ora()))
            r = self._set_ruoli(p, i, ruoli)
            n = self._refs(p, i, corpo)
            # Il trigger di INSERT e' scattato PRIMA che i ruoli esistessero (la FK
            # vuole la riga della regola per prima): la v1 nascerebbe con perimetro
            # vuoto e lo storico direbbe il falso al primo diff. Si completa qui,
            # dentro la stessa transazione.
            self.cx.execute("UPDATE rule_versions SET ruoli=? WHERE progetto=? AND rule_id=? "
                            "AND versione=(SELECT MAX(versione) FROM rule_versions "
                            "WHERE progetto=? AND rule_id=?)", (",".join(r), p, i, p, i))
            self.cx.execute("COMMIT")
        except Exception:
            self.cx.execute("ROLLBACK")
            raise
        return {"esito": "creata", "progetto": p, "id": i, "tipo": t, "ruoli": r,
                "versione": self._versione(p, i), "sigle_citate": n}

    def correggi(self, progetto: str, rid: str, versione_attesa: int, motivo: str,
                 titolo: str | None = None, corpo: str | None = None, tipo: str | None = None,
                 ruoli: list[str] | None = None, changelog: str | None = None) -> dict:
        """DIFETTO corretto sul posto: stesso ID, nuova versione. Per una DECISIONE
        superata si usa crea() + ritira(), non questa."""
        p = self._progetto(progetto)
        i = _norm_id(rid)
        if not (motivo or "").strip():
            raise RulesError("motivo obbligatorio")
        row = self._riga(p, i)
        if row is None:
            raise RulesError(f"{i}: ID mai definito in {p}")
        v = self._versione(p, i)
        if int(versione_attesa) != v:
            raise RulesError(f"CONFLITTO su {p}/{i}: hai letto la versione {versione_attesa}, "
                             f"quella corrente e' la {v}. Qualcuno ha scritto dopo la tua lettura: "
                             "rileggi con rules_get e riprova.")
        t = (tipo or row["tipo"]).strip().upper()
        if t not in TIPI:
            raise RulesError(f"tipo {tipo!r}: ammessi {', '.join(TIPI)}")
        nuovo_corpo = row["corpo"] if corpo is None else corpo.strip()
        try:
            self.cx.execute("BEGIN IMMEDIATE")
            if ruoli is not None:
                self._set_ruoli(p, i, ruoli)
            self.cx.execute(
                "UPDATE rules SET tipo=?, titolo=?, corpo=?, changelog=?, motivo=?, updated_at=? "
                "WHERE progetto=? AND id=?",
                (t, (titolo or row["titolo"]).strip(), nuovo_corpo,
                 changelog if changelog is not None else row["changelog"],
                 motivo.strip(), _ora(), p, i))
            self._refs(p, i, nuovo_corpo)
            self.cx.execute("COMMIT")
        except Exception:
            self.cx.execute("ROLLBACK")
            raise
        return {"esito": "corretta", "progetto": p, "id": i, "versione": self._versione(p, i),
                "ruoli": self._ruoli(p, i),
                "nota": "difetto corretto sul posto: la regola resta in vigore con lo stesso ID"}

    def ritira(self, progetto: str, rid: str, motivo: str, superseded_by: str | None = None,
               changelog: str | None = None) -> dict:
        p = self._progetto(progetto)
        i = _norm_id(rid)
        if not (motivo or "").strip():
            raise RulesError("motivo obbligatorio: un ritiro senza perche' viene riaperto")
        row = self._riga(p, i)
        if row is None:
            raise RulesError(f"{i}: ID mai definito in {p}")
        if row["stato"] == "ritirata":
            raise RulesError(f"{i} e' gia' ritirata (dal {row['updated_at']})")
        sb = _norm_id(superseded_by) if superseded_by else None
        if sb and self._riga(p, sb) is None:
            raise RulesError(f"superseded_by {sb}: non esiste in {p}. Crea prima la regola "
                             "nuova, poi ritira la vecchia puntando a lei.")
        try:
            self.cx.execute("BEGIN IMMEDIATE")
            self.cx.execute(
                "UPDATE rules SET stato='ritirata', superseded_by=?, changelog=?, motivo=?, "
                "updated_at=? WHERE progetto=? AND id=?",
                (sb, changelog if changelog is not None else row["changelog"],
                 motivo.strip(), _ora(), p, i))
            self.cx.execute("COMMIT")
        except Exception:
            self.cx.execute("ROLLBACK")
            raise
        orfane = [r[0] for r in self.cx.execute(
            "SELECT src FROM rule_refs WHERE progetto=? AND dst=? AND src IN "
            "(SELECT id FROM rules WHERE progetto=? AND stato='attiva')", (p, i, p))]
        return {"esito": "ritirata", "progetto": p, "id": i, "superata_da": sb,
                "versione": self._versione(p, i), "la_citano_ancora": orfane,
                "nota": ("la riga RESTA: l'ID non si riusa mai e le citazioni restano risolvibili" +
                         (f" — ATTENZIONE: {len(orfane)} regole attive la citano ancora" if orfane else ""))}

    def importa(self, progetto: str, regole: list[dict], motivo: str) -> dict:
        """Import in blocco (migrazione dai MD). Rifiuta se il PROGETTO ha gia'
        regole: una migrazione si fa una volta sola, su tavolo pulito. Gli altri
        progetti dello stesso database non c'entrano."""
        p = self._progetto(progetto)
        # ATTENZIONE: le chiamate interne ripassano `progetto` (il CODICE), non `p`
        # (il nome): i metodi pubblici risolvono sempre dal codice, mai dal nome.
        n = self.cx.execute("SELECT COUNT(*) FROM rules WHERE progetto=?", (p,)).fetchone()[0]
        if n:
            raise RulesError(f"{p} ha gia' {n} regole: l'import in blocco si fa solo su "
                             "progetto vuoto. Per aggiungerne una: rules_create.")
        if not regole:
            raise RulesError("nessuna regola da importare")
        fatte, errori = [], []
        for r in regole:
            try:
                self.crea(progetto, r["id"], r.get("tipo", "R"), r["titolo"], r["corpo"],
                          r.get("ruoli") or [TUTTI], motivo, r.get("changelog"), r.get("fonte"))
                fatte.append(r["id"])
            except Exception as e:
                errori.append({"id": r.get("id"), "errore": str(e)})
        return {"progetto": p, "importate": len(fatte), "rifiutate": len(errori),
                "errori": errori, "verifica": self.verifica(progetto)}

    # ---------- servizio ----------

    def esporta(self, progetto: str, ruolo: str = "") -> dict:
        """Snapshot MD. Con `ruolo`: solo il perimetro di quel ruolo (regole ATTIVE),
        pronto da incollare nella memoria di quella chat. Senza: il progetto
        INTERO, ritirate comprese — e' il documento di manutenzione.
        Ordine: domini in ordine alfabetico, cosi' arrivano a blocchi, e dentro
        ogni blocco per progressivo numerico (VA-09 prima di VA-10).
        E' un DERIVATO: la verita' resta il database."""
        p = self._progetto(progetto)
        r = self._ruolo(p, ruolo) if (ruolo or "").strip() else ""
        if r:
            filtro = ("AND stato='attiva' AND EXISTS (SELECT 1 FROM rule_roles rr "
                      "WHERE rr.progetto=rules.progetto AND rr.rule_id=rules.id "
                      "AND rr.ruolo IN (?,?))")
            extra = (TUTTI, r)
            testa = [f"# {p} — Regole di: {r}", "",
                     f"> Perimetro `{r}` + le regole valide per chiunque. Solo regole in",
                     "> vigore. Generato dal registro: non si modifica a mano."]
        else:
            filtro, extra = "", ()
            testa = [f"# {p} — Registro Regole (completo)", "",
                     "> Tutti i perimetri, ritirate comprese. Documento di manutenzione."]
        out = [f"<!-- GENERATO da rules-mcp il {_ora()} — progetto: {p}"
               + (f" — ruolo: {r}" if r else " — completo") + ".",
               "     NON si modifica a mano: la verita' e' il database.",
               "     Modifiche via tool, poi si rigenera. -->", ""] + testa + [""]
        n = 0
        for d in self._domini(p):
            righe = self.cx.execute(
                f"SELECT * FROM rules WHERE progetto=? AND domain=? {filtro} ORDER BY seq",
                (p, d) + extra).fetchall()
            if not righe:
                continue
            n += len(righe)
            desc = self.cx.execute("SELECT descrizione FROM project_domains WHERE progetto=? "
                                   "AND dominio=?", (p, d)).fetchone()[0]
            out += [f"## {d}" + (f" — {desc}" if desc else ""), ""]
            for r in righe:
                marca = "" if r["stato"] == "attiva" else " — **RITIRATA**" + (
                    f", superata da {r['superseded_by']}" if r["superseded_by"] else "")
                out += [f"**{r['id']}-{r['tipo']}** · {r['titolo']}{marca}",
                        f"*perimetro: {', '.join(self._ruoli(p, r['id']))} · "
                        f"v{self._versione(p, r['id'])} · {r['updated_at']}*", "",
                        r["corpo"], ""]
        testo = "\n".join(out)
        return {"progetto": p, "ruolo": r or "(tutti i perimetri)", "regole": n,
                "markdown": testo, "byte": len(testo.encode()),
                "sha256": hashlib.sha256(testo.encode()).hexdigest(),
                "nota": "scrivilo nel vault con write_file (e' un derivato, si rigenera)"}

    def backup(self, dest_dir: str) -> dict:
        os.makedirs(dest_dir, exist_ok=True)
        try:
            os.chmod(dest_dir, MODO_DIR)
        except OSError:
            pass
        nome = f"regole-{datetime.now(timezone.utc).strftime('%Y-%b-%d-%H%M')}.db"
        dest = os.path.join(dest_dir, nome)
        if os.path.exists(dest):
            dest = os.path.join(dest_dir, f"regole-{secrets.token_hex(3)}.db")
        self.cx.execute("VACUUM INTO ?", (dest,))
        try:
            os.chmod(dest, MODO_FILE)
        except OSError:
            pass
        return {"esito": "copia quiescente creata", "file": dest, "byte": os.path.getsize(dest),
                "nota": "apribile senza recovery: e' la copia da portare off-site"}
