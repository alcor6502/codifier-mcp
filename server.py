"""
server.py — MCP self-hosted per il REGISTRO REGOLE. UN database, N progetti.

Gemello del vault-mcp: stessa architettura, stesso gate OAuth, stesso preflight
bloccante. Due differenze deliberate:
- gira come ROOT e i file del database sono 644: chi monta la share li LEGGE e
  non li tocca. Toccare il database a mano rompe lo storico in silenzio;
- non c'e' un container per progetto: il progetto e' un argomento — e non e' il
  suo NOME ma un CODICE alfanumerico opaco, che sta in testa alle istruzioni del
  progetto. Nessun tool di lettura elenca i progetti e nessun errore ne nomina
  uno: chi non ha il codice non trova la porta.

Nomi dei tool tutti prefissati `rules_`: nella stessa chat vivono anche i tool
del vault (status, history, diff, search...) e due omonimi si confondono.

Config via variabili d'ambiente:
  DB_PATH                 database unico (es. /db/regole.db)
  BACKUP_DIR              copie VACUUM INTO (default: <dir del db>/backup)
  ADMIN_ACCESS_CODE       codice di scrittura: viaggia a ogni chiamata
  BASE_URL                URL pubblico (es. https://svc-a2.<tailnet>.ts.net)
  GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET / ALLOWED_GITHUB_LOGIN / JWT_SIGNING_KEY
  PORT                    default 3001
  ANTHROPIC_CIDR          default 160.79.104.0/21; stringa vuota = filtro spento
"""
from __future__ import annotations

import ipaddress
import logging
import os
import secrets
import sys

from fastmcp import FastMCP
from fastmcp.server.auth.providers.github import GitHubProvider
from fastmcp.server.dependencies import get_access_token, get_http_request
from fastmcp.server.middleware import Middleware, MiddlewareContext

from rules import Registro, RulesError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("rules-mcp")


def env(name: str, default: str | None = None) -> str:
    v = os.environ.get(name, default)
    if v is None:
        log.error("variabile d'ambiente mancante: %s", name)
        sys.exit(2)
    return v


DB_PATH = env("DB_PATH", "/db/regole.db")
BASE_URL = env("BASE_URL")
ALLOWED_LOGIN = env("ALLOWED_GITHUB_LOGIN")
CODE = env("ADMIN_ACCESS_CODE")
PORT = int(env("PORT", "3001"))
CIDR = os.environ.get("ANTHROPIC_CIDR", "160.79.104.0/21").strip()
BACKUP_DIR = os.environ.get("BACKUP_DIR") or os.path.join(os.path.dirname(DB_PATH), "backup")

reg = Registro(DB_PATH)
log.info("registro: %s — %s", DB_PATH, reg.stato())

auth = GitHubProvider(
    client_id=env("GITHUB_CLIENT_ID"),
    client_secret=env("GITHUB_CLIENT_SECRET"),
    base_url=BASE_URL,
    jwt_signing_key=env("JWT_SIGNING_KEY"),
    require_authorization_consent=True,
)

mcp = FastMCP(name="Registro Regole", auth=auth)


class Gate(Middleware):
    """Due controlli su OGNI messaggio: (1) IP nell'egress Anthropic (XFF del
    Funnel, che lo compila lui: proxy fidato); (2) login GitHub == ALLOWED_LOGIN."""

    def __init__(self) -> None:
        self.net = ipaddress.ip_network(CIDR) if CIDR else None

    async def on_message(self, ctx: MiddlewareContext, call_next):
        if self.net is not None:
            try:
                req = get_http_request()
                xff = (req.headers.get("x-forwarded-for") or "").split(",")[0].strip()
                ip = xff or (req.client.host if req.client else "")
                if not ip or ipaddress.ip_address(ip) not in self.net:
                    log.warning("rifiutato: IP %r fuori da %s", ip, CIDR)
                    raise RulesError("accesso rifiutato (origine non ammessa)")
            except RulesError:
                raise
            except Exception:
                pass  # niente request HTTP nel contesto (es. ping interno)
        tok = get_access_token()
        login = (tok.claims or {}).get("login") if tok else None
        if login != ALLOWED_LOGIN:
            log.warning("rifiutato: login GitHub %r != %r", login, ALLOWED_LOGIN)
            raise RulesError("accesso rifiutato (utente non ammesso)")
        return await call_next(ctx)


mcp.add_middleware(Gate())


def _admin(codice: str) -> None:
    """Il gate di MANUTENZIONE: scrittura, e le letture che escono dal perimetro
    di chi chiede (stato, audit, storico, export). Non e' uno stato di sessione:
    il codice viaggia a ogni chiamata, cosi' non esiste una 'modalita' rimasta
    aperta per sbaglio."""
    if not secrets.compare_digest((codice or "").strip(), CODE):
        raise RulesError("codice admin mancante o errato: questa operazione la fa solo "
                         "la chat che MANTIENE il registro, col codice che le da' "
                         "Alfredo. Non provare a indovinarlo: chiedilo.")


# ---------------- lettura: aperta a tutti i ruoli ----------------

@mcp.tool
def rules_project_info(progetto: str) -> dict:
    """Cosa c'e' dentro il progetto di cui hai il codice: i RUOLI che esistono e
    i DOMINI delle sigle. Chiamalo per primo, se non sai quale ruolo dichiarare:
    e' anche la prova che il registro risponde.
    `progetto` e' il CODICE alfanumerico in testa alle istruzioni del progetto,
    non il suo nome. Non esiste un tool che elenchi i progetti: senza codice il
    registro non risponde."""
    return reg.info_progetto(progetto)


@mcp.tool
def rules_status(progetto: str, codice: str) -> dict:
    """MANUTENZIONE. Verdetto sul registro: integrita' del database, permessi dei
    file, conteggi per dominio e per ruolo, ultima modifica. I conteggi
    riguardano tutti i perimetri, per questo vuole il codice admin."""
    _admin(codice)
    return reg.stato(progetto)


@mcp.tool
def rules_list(progetto: str, ruolo: str) -> dict:
    """TUTTE le regole che valgono per te, in UNA chiamata: passa il CODICE del
    progetto (quello in testa alle sue istruzioni) e il tuo ruolo, e le ricevi
    complete, ordinate per dominio. Sostituisce l'apertura dei
    file di regole — non serve leggere altro.
    I ruoli non sono fissi: li dichiara ogni progetto (vedi rules_projects). Le
    regole con perimetro '*' arrivano sempre, a chiunque.
    Il verdetto dice anche QUANTE regole restano fuori dal tuo perimetro: se un
    ID che ti serve non e' nell'elenco, non e' inesistente — e' di qualcun altro.
    Solo regole IN VIGORE: le ritirate non compaiono mai (restano raggiungibili
    per ID, perche' le citazioni devono risolvere)."""
    return reg.regole(progetto, ruolo)


@mcp.tool
def rules_get(progetto: str, id: str, ruolo: str) -> dict:
    """Una regola sola dal suo ID (es. "VA-02"; il suffisso di tipo si tollera ma
    la citazione corretta e' nuda). Tre risposte DIVERSE, e la differenza conta:
    la regola · "esiste ma non e' di tua competenza" (con chi la tiene) · "ID mai
    definito" — che significa citazione rotta, da segnalare, OPPURE che stai
    usando il codice di un altro progetto."""
    return reg.regola(progetto, id, ruolo)


@mcp.tool
def rules_search(progetto: str, testo: str, ruolo: str) -> dict:
    """Cerca una stringa nel titolo e nel corpo delle regole attive del tuo
    perimetro. Dice anche quante corrispondenze sono cadute fuori perimetro,
    cosi' sai che esistono senza vederle."""
    return reg.cerca(progetto, testo, ruolo)


@mcp.tool
def rules_check(progetto: str, codice: str) -> dict:
    """MANUTENZIONE. Audit di un progetto: puntatori rotti (sigle citate che non esistono),
    citazioni verso regole ritirate, regole senza perimetro, buchi di
    numerazione. Verdetto, non dump: se torna "coerente" non c'e' altro da fare.
    Elenca ID di tutti i perimetri: per questo vuole il codice admin."""
    _admin(codice)
    return reg.verifica(progetto)


@mcp.tool
def rules_history(progetto: str, id: str, codice: str) -> dict:
    """MANUTENZIONE. Come quella regola e' cambiata nel tempo: una riga per versione, con data,
    azione e MOTIVO. Lo storico lo scrivono i trigger del database, non i tool:
    c'e' dentro anche una modifica fatta a mano. Serve a chi MANTIENE la regola,
    non a chi la applica: a quest'ultimo basta il testo in vigore."""
    _admin(codice)
    return reg.storico(progetto, id)


@mcp.tool
def rules_diff(progetto: str, id: str, versione_a: int, versione_b: int, codice: str) -> dict:
    """MANUTENZIONE. Cosa e' cambiato fra due versioni di UNA regola (i numeri li da'
    rules_history). Si conservano versioni intere, non diff: il confronto si
    calcola al volo fra due qualsiasi, anche lontane."""
    _admin(codice)
    return reg.confronta(progetto, id, versione_a, versione_b)


@mcp.tool
def rules_export(progetto: str, codice: str, ruolo: str = "") -> dict:
    """MANUTENZIONE. Snapshot Markdown, da scrivere nel vault con write_file del
    vault-mcp. Due usi:
    - con `ruolo`: solo il perimetro di quel ruolo, regole in vigore — e' il
      testo da incollare nella MEMORIA di quella chat;
    - senza `ruolo`: il progetto intero, ritirate comprese — documento di
      manutenzione, e la copia che finisce in git.
    Domini in ordine alfabetico (arrivano a blocchi), dentro il blocco per
    progressivo. E' un DERIVATO: la verita' resta il database, si rigenera."""
    _admin(codice)
    return reg.esporta(progetto, ruolo)


# ---------------- scrittura: codice a OGNI chiamata ----------------

@mcp.tool
def rules_registry(codice: str) -> dict:
    """L'elenco COMPLETO dei progetti del registro, CODICI COMPRESI. E' l'unica
    porta da cui i codici possono uscire, e per questo vuole il codice di
    scrittura. Serve ad Alfredo per ritrovare un codice smarrito, non alle chat
    di lavoro: quelle il loro codice ce l'hanno gia' nelle istruzioni."""
    _admin(codice)
    return reg.progetti()


@mcp.tool
def rules_project_create(codice_progetto: str, nome: str, ruoli: list[str], domini: dict,
                         codice: str, descrizione: str = "") -> dict:
    """Crea un progetto nuovo nel registro. Serve prima di qualunque regola.
    `codice_progetto`: l'handle con cui quel progetto sara' indirizzato per
    sempre — 8-32 caratteri alfanumerici, generato da Alfredo, da mettere in
    testa alle istruzioni del progetto. Non e' il nome e non si deduce dal nome.
    `ruoli`: i ruoli che quel progetto avra' (es. ["architect","tax monitor"]).
    `domini`: le sigle con la loro descrizione, es. {"VA":"vault e file",
    "ST":"struttura e convenzioni"} — due lettere maiuscole ciascuna.
    Ruoli e domini sono dati: un progetto nuovo non richiede codice nuovo."""
    _admin(codice)
    return reg.crea_progetto(codice_progetto, nome, ruoli, domini, descrizione)


@mcp.tool
def rules_project_rekey(progetto: str, codice_progetto_nuovo: str, codice: str) -> dict:
    """Cambia il codice di accesso di un progetto (se e' finito dove non doveva).
    Le regole non si toccano: dentro il registro il progetto e' indirizzato per
    nome, il codice e' solo la porta. Aggiorna le istruzioni del progetto PRIMA
    di chiudere la chat: col vecchio codice non ci si arriva piu'."""
    _admin(codice)
    return reg.cambia_codice(progetto, codice_progetto_nuovo)


@mcp.tool
def rules_project_update(progetto: str, codice: str, ruoli: list[str] | None = None,
                         domini: dict | None = None) -> dict:
    """Aggiunge ruoli o domini a un progetto esistente. Si AGGIUNGE soltanto:
    togliere un ruolo o un dominio orfanerebbe le regole che li usano."""
    _admin(codice)
    return reg.aggiorna_progetto(progetto, ruoli, domini)


@mcp.tool
def rules_create(progetto: str, id: str, tipo: str, titolo: str, corpo: str,
                 ruoli: list[str], motivo: str, codice: str, changelog: str = "") -> dict:
    """Crea una regola nuova.
    `tipo`: R vincolante · M metodo · F fatto tecnico. Il ritiro NON e' un tipo.
    `ruoli`: fra quelli dichiarati dal progetto, oppure ["*"] se vale per chiunque.
    `motivo` e' obbligatorio: senza il perche' la regola non si difende e alla
    prima occasione viene riaperta. `changelog` e' il riferimento alla voce
    (es. "Architect/0007"), cartella + numero, mai il numero nudo.
    L'ID non si riusa MAI: se e' gia' stato usato in quel progetto, anche da una
    regola ritirata, la creazione viene rifiutata. Il dominio dev'essere gia'
    dichiarato (rules_project_update per aggiungerne uno)."""
    _admin(codice)
    return reg.crea(progetto, id, tipo, titolo, corpo, ruoli, motivo, changelog or None)


@mcp.tool
def rules_fix(progetto: str, id: str, versione_attesa: int, motivo: str, codice: str,
              titolo: str = "", corpo: str = "", tipo: str = "",
              ruoli: list[str] | None = None, changelog: str = "") -> dict:
    """Corregge un DIFETTO sul posto: un numero sbagliato, un puntatore rotto,
    una frase che dice il falso. Stesso ID, la regola resta in vigore, nasce una
    versione nuova nello storico.
    Una DECISIONE superata NON si corregge cosi': si crea la regola nuova e si
    ritira la vecchia puntando a lei (rules_create + rules_retire).
    `versione_attesa` e' il numero che hai letto con rules_get: se nel frattempo
    ha scritto qualcun altro, la modifica viene rifiutata e ti dice qual e' la
    versione corrente. Lascia vuoti i campi che non cambi."""
    _admin(codice)
    return reg.correggi(progetto, id, versione_attesa, motivo,
                        titolo or None, corpo or None, tipo or None, ruoli, changelog or None)


@mcp.tool
def rules_retire(progetto: str, id: str, motivo: str, codice: str,
                 superata_da: str = "", changelog: str = "") -> dict:
    """Ritira una regola: esce dalle liste dei ruoli ma la riga RESTA, perche'
    l'ID non si riusa mai e le citazioni devono restare risolvibili. Non esiste
    una cancellazione.
    `superata_da` va compilato quando la regola e' sostituita da una nuova (che
    dev'essere gia' stata creata). Il verdetto elenca le regole attive che la
    citano ancora: vanno sistemate."""
    _admin(codice)
    return reg.ritira(progetto, id, motivo, superata_da or None, changelog or None)


@mcp.tool
def rules_import(progetto: str, regole: list[dict], motivo: str, codice: str) -> dict:
    """Import in blocco per la MIGRAZIONE dai file MD. Solo su progetto VUOTO:
    una migrazione si fa una volta sola, su tavolo pulito (gli altri progetti
    dello stesso database non c'entrano).
    Ogni elemento: {"id","tipo","titolo","corpo","ruoli",["changelog"],["fonte"]}.
    Le regole rifiutate vengono elencate col motivo, le altre passano: si
    correggono e si rilanciano con rules_create. In coda gira rules_check."""
    _admin(codice)
    return reg.importa(progetto, regole, motivo)


@mcp.tool
def rules_backup(codice: str) -> dict:
    """Copia quiescente dell'INTERO database (VACUUM INTO) nella cartella di
    backup: si apre senza recovery, ed e' quella da portare off-site. Sicuro
    anche a database vivo. Gli snapshot ZFS restano la rete principale."""
    _admin(codice)
    return reg.backup(BACKUP_DIR)


if __name__ == "__main__":
    log.info("avvio su 127.0.0.1:%s — base_url %s — utente ammesso: %s — filtro IP: %s — "
             "db: %s (uid processo: %s) — store token: %s",
             PORT, BASE_URL, ALLOWED_LOGIN, CIDR or "SPENTO", DB_PATH, os.geteuid(),
             os.environ.get("FASTMCP_HOME", "(default NON persistente!)"))
    mcp.run(transport="http", host=os.environ.get("BIND_HOST", "127.0.0.1"), port=PORT)
