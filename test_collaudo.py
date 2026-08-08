"""Collaudo di rules.py v2 — ogni percorso, casi di RIFIUTO compresi.
Gira senza rete e senza fastmcp: e' il layer dove stanno i bug veri."""
import os, sqlite3, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rules import Registro, RulesError, TUTTI, MODO_FILE, ERR_PROGETTO

OK = FAIL = 0

def t(nome, fn):
    global OK, FAIL
    try:
        fn(); print(f"  OK    {nome}"); OK += 1
    except Exception as e:
        print(f"  FAIL  {nome}: {type(e).__name__}: {e}"); FAIL += 1

def rifiuta(fn, frammento):
    try:
        fn()
    except RulesError as e:
        assert frammento.lower() in str(e).lower(), f"messaggio inatteso: {e}"
        return
    raise AssertionError(f"doveva rifiutare ({frammento})")

d = tempfile.mkdtemp()
R = Registro(os.path.join(d, "regole.db"))

# ---------- progetti ----------
CFP, CHT, CCASA = "K7m2Qx91Ab", "Zt4Rn8Wq02", "Pd6Hj3Lv77"

t("codice mancante: errore cieco", lambda: rifiuta(
    lambda: R.regole("", "architect"), "progetto non specificato"))
t("codice inventato: STESSO errore, nessun indizio", lambda: rifiuta(
    lambda: R.regole("Inventato99", "architect"), "progetto non specificato"))

t("crea progetto FP", lambda: R.crea_progetto(
    CFP, "Financial Portfolio",
    ["architect", "fidelity advisory", "interval funds sale", "tax monitor", "market news"],
    {"VA": "vault e file", "ST": "struttura", "RL": "ruoli", "VE": "verifica", "FI": "fisco"},
    "il progetto storico"))
t("crea progetto HT (ruoli DIVERSI)", lambda: R.crea_progetto(
    CHT, "Health Tracking", ["architect", "coach"], {"VA": "vault e file", "MS": "misure"}))

t("il NOME non e' una chiave d'accesso", lambda: rifiuta(
    lambda: R.regole("Financial Portfolio", "architect"), "progetto non specificato"))
t("codice di un progetto, ruolo di un altro: rifiutato", lambda: rifiuta(
    lambda: R.regole(CFP, "coach"), "non esiste in Financial Portfolio"))

t("progetto duplicato rifiutato", lambda: rifiuta(
    lambda: R.crea_progetto("Aa11Bb22Cc", "Financial Portfolio", ["x"], {"AA": ""}), "esiste gia"))
t("codice duplicato rifiutato", lambda: rifiuta(
    lambda: R.crea_progetto(CFP, "Altro", ["x"], {"AA": ""}), "gia' in uso"))
t("codice corto rifiutato", lambda: rifiuta(
    lambda: R.crea_progetto("abc", "Altro", ["x"], {"AA": ""}), "8-32 caratteri"))
t("codice con simboli rifiutato", lambda: rifiuta(
    lambda: R.crea_progetto("ab-cd-ef-gh", "Altro", ["x"], {"AA": ""}), "8-32 caratteri"))
t("progetto senza ruoli rifiutato", lambda: rifiuta(
    lambda: R.crea_progetto("Qq11Ww22Ee", "Vuoto", [], {"AA": ""}), "senza ruoli"))
t("dominio malformato rifiutato", lambda: rifiuta(
    lambda: R.crea_progetto("Qq11Ww22Ee", "Vuoto", ["a"], {"vault": ""}), "due lettere"))
t("'*' non e' un ruolo", lambda: rifiuta(
    lambda: R.crea_progetto("Qq11Ww22Ee", "Vuoto", ["*"], {"AA": ""}), "non e' un ruolo"))
t("'_ALL_' non e' un ruolo", lambda: rifiuta(
    lambda: R.crea_progetto("Qq11Ww22Ee", "Vuoto", ["_ALL_"], {"AA": ""}), "non e' un ruolo"))

def info():
    i = R.info_progetto(CFP)
    assert i["progetto"] == "Financial Portfolio"
    assert "tax monitor" in i["ruoli"] and "VA" in i["domini"]
    assert "codice" not in i                       # non si ripete il codice
    assert "regole_attive" not in i                # niente conteggi a chi applica
    assert i["perimetro_speciale"] == TUTTI
    assert "Health" not in repr(i)                 # non nomina altri progetti
t("info_progetto: dice ruoli e domini, nient'altro", info)

FP, HT = CFP, CHT

# ---------- regole ----------
t("crea regola globale", lambda: R.crea(FP, "VA-02", "R", "Rileggi le fonti",
    "I dati SORGENTE si rileggono subito prima di scrivere il derivato. Vedi ST-07.",
    [TUTTI], motivo="import iniziale"))
t("crea regola di ruolo", lambda: R.crea(FP, "FI-03", "M", "Stima del bracket",
    "Il bracket si stima dal rollup per carattere. Incrocia con VE-03.",
    ["tax monitor"], motivo="import iniziale"))
t("crea regola architect", lambda: R.crea(FP, "RL-01", "R", "Le regole le scrive solo l'Architect",
    "Se scopri un problema di processo segnalalo.", ["architect"], motivo="import iniziale"))

t("stesso ID in un ALTRO progetto: ammesso", lambda: R.crea(
    HT, "VA-02", "R", "Omonima ma diversa", "Corpo di Health Tracking.", [TUTTI],
    motivo="import iniziale"))

t("ID duplicato nello STESSO progetto rifiutato", lambda: rifiuta(
    lambda: R.crea(FP, "VA-02", "R", "x", "y", [TUTTI], motivo="m"), "esiste gia"))
t("dominio non dichiarato rifiutato", lambda: rifiuta(
    lambda: R.crea(FP, "ZZ-01", "R", "x", "y", [TUTTI], motivo="m"), "non dichiarato"))
t("dominio di un ALTRO progetto rifiutato", lambda: rifiuta(
    lambda: R.crea(FP, "MS-01", "R", "x", "y", [TUTTI], motivo="m"), "non dichiarato"))
t("ruolo di un ALTRO progetto rifiutato", lambda: rifiuta(
    lambda: R.crea(FP, "ST-01", "R", "x", "y", ["coach"], motivo="m"), "non esiste in"))
t("tipo X rifiutato", lambda: rifiuta(
    lambda: R.crea(FP, "VA-03", "X", "x", "y", [TUTTI], motivo="m"), "non e' un tipo"))
t("motivo mancante rifiutato", lambda: rifiuta(
    lambda: R.crea(FP, "VA-04", "R", "x", "y", [TUTTI], motivo=""), "motivo"))
t("perimetro vuoto rifiutato", lambda: rifiuta(
    lambda: R.crea(FP, "VA-05", "R", "x", "y", [], motivo="m"), "perimetro vuoto"))

# ---------- perimetro ----------
def perimetro():
    tax = R.regole(FP, "tax monitor")
    assert [x["id"] for x in tax["regole"]] == ["FI-03", "VA-02"]
    assert tax["fuori_perimetro"] == 1 and "RL" in tax["domini_con_regole_altrui"]
    assert [x["id"] for x in R.regole(FP, "architect")["regole"]] == ["RL-01", "VA-02"]
t("regole(progetto,ruolo) filtra e DICHIARA quante restano fuori", perimetro)

def isolamento():
    ht = R.regole(HT, "coach")
    assert [x["id"] for x in ht["regole"]] == ["VA-02"]
    assert ht["regole"][0]["corpo"].startswith("Corpo di Health")   # NON quella di FP
    assert R.regola(FP, "VA-02", "tax monitor")["corpo"].startswith("I dati SORGENTE")
t("i progetti sono isolati: stesso ID, contenuti diversi", isolamento)

t("ruolo inesistente NEL progetto rifiutato", lambda: rifiuta(
    lambda: R.regole(HT, "tax monitor"), "non esiste in Health Tracking"))
t("regola propria: si legge", lambda: R.regola(FP, "FI-03", "tax monitor"))
t("citazione col suffisso tollerata", lambda: R.regola(FP, "FI-03-M", "tax monitor"))
t("regola altrui: 'ESISTE ma non e' tua'", lambda: rifiuta(
    lambda: R.regola(FP, "RL-01", "tax monitor"), "esiste in financial portfolio ma non e' di tua"))
t("ID inesistente: distinto dal rifiuto", lambda: rifiuta(
    lambda: R.regola(FP, "VE-99", "tax monitor"), "mai definito"))
def niente_oracolo():
    try:
        R.regola(HT, "FI-03", "coach")            # FI-03 esiste, ma in FP
    except RulesError as e:
        m = str(e)
        assert "mai definito" in m
        assert "Financial" not in m, f"ORACOLO: {m}"   # non deve trapelare
        return
    raise AssertionError("doveva rifiutare")
t("ID che esiste in un ALTRO progetto: NON lo rivela", niente_oracolo)

def cerca():
    r = R.cerca(FP, "bracket", "tax monitor")
    assert r["trovate"] == 1 and r["regole"][0]["id"] == "FI-03"
    r2 = R.cerca(FP, "Architect", "tax monitor")
    assert r2["trovate"] == 0 and r2["fuori_perimetro"] == 1, r2
    assert R.cerca(HT, "bracket", "coach")["trovate"] == 0     # non travasa fra progetti
t("cerca resta nel perimetro E nel progetto", cerca)

# ---------- riferimenti ----------
def refs():
    v = R.verifica(FP)
    assert {"src": "VA-02", "dst": "ST-07"} in v["puntatori_rotti"], v
    assert R.verifica(HT)["anomalie"] == 0                     # i refs non travasano
t("verifica trova ST-07 rotto, e resta nel progetto", refs)

def correggi():
    v = R.regola(FP, "VA-02", "tax monitor")["versione"]
    R.correggi(FP, "VA-02", v, motivo="tolto il rimando rotto",
               corpo="I dati SORGENTE si rileggono subito.")
    assert R.regola(FP, "VA-02", "tax monitor")["versione"] == v + 1
    rotti = R.verifica(FP)["puntatori_rotti"]
    assert {"src": "VA-02", "dst": "ST-07"} not in rotti
    assert {"src": "FI-03", "dst": "VE-03"} in rotti           # gli altri restano
t("correggi: nuova versione, refs ricalcolate", correggi)

t("CAS: versione vecchia rifiutata", lambda: rifiuta(
    lambda: R.correggi(FP, "VA-02", 1, motivo="m", corpo="z"), "conflitto"))

def storia():
    s = R.storico(FP, "VA-02")
    assert s["versioni"] == 2
    assert [x["azione"] for x in s["storia"]] == ["creata", "modificata"]
    assert s["storia"][1]["motivo"] == "tolto il rimando rotto"
    assert R.storico(HT, "VA-02")["versioni"] == 1             # storici separati
t("storico: separato per progetto, azioni e motivi giusti", storia)

def diff():
    c = R.confronta(FP, "VA-02", 1, 2)
    assert "ST-07" in c["diff"] and c["motivo_di_arrivo"] == "tolto il rimando rotto"
    assert "ruoli: \n" not in c["diff"], "la v1 e' nata senza perimetro nello storico"
    assert R.storico(FP, "VA-02")["versioni"] == 2
t("confronta: il diff mostra cosa e' cambiato", diff)

t("versione inesistente rifiutata", lambda: rifiuta(
    lambda: R.confronta(FP, "VA-02", 1, 9), "inesistente"))

def supersede():
    R.crea(FP, "FI-04", "M", "Stima del bracket (rev)", "Metodo nuovo.", ["tax monitor"],
           motivo="decisione superata: FI-03 sottostimava", changelog="Tax Monitor/0003")
    r = R.ritira(FP, "FI-03", motivo="superata da FI-04", superseded_by="FI-04")
    assert r["superata_da"] == "FI-04"
    assert R.regola(FP, "FI-03", "tax monitor")["stato"] == "ritirata"
    assert "FI-03" not in [x["id"] for x in R.regole(FP, "tax monitor")["regole"]]
    assert R.regola(FP, "FI-03", "tax monitor")["stato"] == "ritirata"   # per ID: si trova
t("ritira + superseded_by: fuori dalle attive, resta risolvibile", supersede)

t("ID ritirato non riusabile", lambda: rifiuta(
    lambda: R.crea(FP, "FI-03", "R", "x", "y", [TUTTI], motivo="m"), "non si riusano"))
t("doppio ritiro rifiutato", lambda: rifiuta(
    lambda: R.ritira(FP, "FI-03", motivo="m"), "gia' ritirata"))
t("superseded_by inesistente rifiutato", lambda: rifiuta(
    lambda: R.ritira(FP, "RL-01", motivo="m", superseded_by="RL-99"), "non esiste in"))

def trigger_delete():
    R.cx.execute("PRAGMA foreign_keys=OFF")
    R.cx.execute("DELETE FROM rules WHERE progetto=? AND id='RL-01'", ("Financial Portfolio",))
    R.cx.execute("PRAGMA foreign_keys=ON")
    assert R.storico(FP, "RL-01")["storia"][-1]["azione"] == "CANCELLATA"
t("TRIGGER: anche un DELETE a mano finisce nello storico", trigger_delete)

# ---------- progetto: aggiunte ----------
def aggiorna():
    R.aggiorna_progetto(HT, ruoli=["nutrizionista"], domini={"AL": "alimentazione"})
    R.crea(HT, "AL-01", "R", "Dominio nuovo", "Corpo.", ["nutrizionista"], motivo="prova")
    assert [x["id"] for x in R.regole(HT, "nutrizionista")["regole"]] == ["AL-01", "VA-02"]
t("aggiorna progetto: ruolo e dominio nuovi funzionano subito", aggiorna)

t("aggiorna a vuoto rifiutato", lambda: rifiuta(
    lambda: R.aggiorna_progetto(HT), "niente da aggiungere"))

# ---------- servizio ----------
def export_backup():
    e = R.esporta(FP)                                          # completo
    assert "VA-02" in e["markdown"] and "RITIRATA" in e["markdown"]
    assert "Corpo di Health" not in e["markdown"]              # solo il suo progetto
    ex = R.esporta(FP, "tax monitor")                          # per memoria di chat
    assert "FI-04" in ex["markdown"] and "VA-02" in ex["markdown"]
    assert "RL-" not in ex["markdown"], "perimetro altrui nell'export di ruolo"
    assert "RITIRATA" not in ex["markdown"], "ritirata nell'export di ruolo"
    ordine = [l for l in ex["markdown"].splitlines() if l.startswith("## ")]
    assert ordine == sorted(ordine), f"domini non in ordine alfabetico: {ordine}"
    b = R.backup(os.path.join(d, "bk"))
    cx = sqlite3.connect(b["file"])
    assert cx.execute("SELECT COUNT(DISTINCT progetto) FROM rules").fetchone()[0] == 2
    assert (os.stat(b["file"]).st_mode & 0o777) == MODO_FILE
t("esporta completo + per ruolo (blocchi alfabetici) + backup 644", export_backup)

def import_pieno():
    rifiuta(lambda: R.importa(FP, [{"id": "VA-08", "titolo": "x", "corpo": "y"}], "m"),
            "ha gia'")
    R.crea_progetto(CCASA, "Casa", ["architect"], {"CA": "casa"})
    r = R.importa(CCASA, [{"id": "CA-01", "titolo": "Prima", "corpo": "Vedi CA-02.",
                            "ruoli": ["architect"]},
                           {"id": "CA-01", "titolo": "dup", "corpo": "z"}], motivo="migrazione MD")
    assert r["importate"] == 1 and r["rifiutate"] == 1, r
    assert r["verifica"]["anomalie"] == 1                      # CA-02 non esiste ancora
t("importa: solo su progetto vuoto, riporta i rifiuti, poi verifica", import_pieno)

def permessi():
    m = os.stat(R.path).st_mode & 0o777
    assert m == MODO_FILE, oct(m)
    assert R.stato()["modo_file"] == oct(MODO_FILE)
t("il database e' 644: chi monta la share legge e non tocca", permessi)

def all_sentinel():
    R.crea(HT, "MS-01", "F", "Universale", "Vale per chiunque.", ["_all_"], motivo="prova")
    assert R.regola(HT, "MS-01", "coach")["ruoli"] == [TUTTI]
    assert "MS-01" in [x["id"] for x in R.regole(HT, "architect")["regole"]]
    R.crea(HT, "MS-02", "F", "Anche cosi'", "Alias tollerato.", ["*"], motivo="prova")
    assert R.regola(HT, "MS-02", "coach")["ruoli"] == [TUTTI]
t("_ALL_ (e i suoi alias) danno il perimetro universale", all_sentinel)

def rekey():
    R.cambia_codice(CHT, "Nn99Mm88Kk")
    rifiuta(lambda: R.regole(CHT, "coach"), "progetto non specificato")   # vecchio: morto
    assert R.regole("Nn99Mm88Kk", "coach")["progetto"] == "Health Tracking"
    R.cambia_codice("Nn99Mm88Kk", CHT)            # rimesso com'era
t("rekey: il codice vecchio muore, le regole restano", rekey)

t("rekey verso un codice in uso rifiutato", lambda: rifiuta(
    lambda: R.cambia_codice(CHT, CFP), "gia' in uso"))

def elenco_completo():
    e = R.progetti()
    assert e["conteggio"] == 3
    assert {p["codice"] for p in e["progetti"]} == {CFP, CHT, CCASA}
t("progetti(): l'unica porta da cui escono i codici (server: gated)", elenco_completo)

def persistenza():
    R.cx.close()
    R3 = Registro(os.path.join(d, "regole.db"))
    s = R3.stato()
    assert s["integrity_check"] == "ok" and s["journal_mode"] == "wal"
    assert len(s["progetti"]) == 3
    assert R3.storico(CFP, "VA-02")["versioni"] == 2
t("riapertura: WAL, integro, tre progetti, storico intatto", persistenza)

print(f"\n{OK} passati, {FAIL} falliti")
sys.exit(1 if FAIL else 0)
