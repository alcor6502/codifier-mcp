import base64, os, sys, tempfile, time
sys.path.insert(0, "/sessions/zealous-sleepy-goldberg/mnt/MCP/codifier-mcp")
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization as ser
from rules import Registry, RulesError, _plus_days

OK = FAIL = 0
def ok(cond, label, extra=""):
    global OK, FAIL
    if cond: OK += 1; print(f"  PASS  {label}")
    else: FAIL += 1; print(f"  FAIL  {label}  {extra}")
def must_fail(label, fn, kind=Exception):
    global OK, FAIL
    try:
        fn(); FAIL += 1; print(f"  FAIL  {label}: did NOT fail")
    except kind as e: OK += 1; print(f"  PASS  {label} (refused: {str(e)[:60]})")

sk = Ed25519PrivateKey.generate()
PUB = base64.b64encode(sk.public_key().public_bytes(ser.Encoding.Raw, ser.PublicFormat.Raw)).decode()
sign = lambda m: base64.b64encode(sk.sign(m.encode())).decode()

d = tempfile.mkdtemp()
r = Registry(os.path.join(d, "t.db"), public_key=PUB, provisional_days=90)
CODE, CODE2 = "fpcode12345678", "htcode87654321"

DOM = {"VA":"vault","ST":"struttura","RL":"ruoli","PE":"perimetro"}
CONS = [("architect","chat"),("advisory","chat"),("alt-funds","chat"),
        ("tax","chat"),("market-news","chat"),("update-tax","skill")]
r.create_project(CODE, "Financial Portfolio", CONS, DOM)
r.create_project(CODE2, "Health Tracking", [("architect","chat"),("coach","chat")], {"VA":"x","ST":"y"})

print("\n== schema e trigger ==")
info = r.project_info(CODE)
names = {s["name"] for s in info["scopes"]}
ok(names == {"_ALL_","architect","advisory","alt-funds","tax","market-news","update-tax"},
   "il trigger crea uno scope singoletto per ogni consumer", names)
ok(all(s["managed"] for s in info["scopes"]), "tutti gli scope nati sono managed")
ok([s for s in info["scopes"] if s["name"]=="_ALL_"][0]["breadth"] == 6, "_ALL_ vale 6 consumer")
r.create_scope(CODE, "deliberativi", ["architect","advisory","alt-funds","tax"])
ok(r._breadth("Financial Portfolio","deliberativi") == 4, "deliberativi vale 4")
must_fail("scope managed rifiuta un secondo membro",
          lambda: r.cx.execute("INSERT INTO scope_members VALUES ('Financial Portfolio','tax','advisory')"))
must_fail("scope managed non si rinomina",
          lambda: r.cx.execute("UPDATE scopes SET name='x' WHERE project='Financial Portfolio' AND name='tax'"))
must_fail("un consumer non si rinomina",
          lambda: r.cx.execute("UPDATE consumers SET name='x' WHERE project='Financial Portfolio' AND name='tax'"))
must_fail("create_scope con un membro solo", lambda: r.create_scope(CODE,"solo",["tax"]), RulesError)

print("\n== proposta, lotto, firma ==")
r.propose(CODE,"VA-02","R","Regola di tutti","vale per chiunque, vedi ST-03",["*"],"perche si", "architect")
r.propose(CODE,"PE-01","M","Regola dei deliberativi","metodo dei quattro",["deliberativi"],"perche si","architect")
r.propose(CODE,"ST-03","R","Regola di tax","solo tax",["tax"],"perche si","tax")
ok(len(r.list_rules(CODE,"tax")["rules"]) == 0, "una proposta non raggiunge nessuno")
b = r.batch(CODE)
ok(b["count"] == 3, "il lotto ha 3 proposte")
must_fail("digest sbagliato rifiutato", lambda: r.approve(CODE,"deadbeef",sign("deadbeef")), RulesError)
must_fail("firma su un altro messaggio rifiutata", lambda: r.approve(CODE,b["digest"],sign("altro")), RulesError)
a = r.approve(CODE, b["digest"], sign(b["digest"]))
ok(a["signed"] and a["count"] == 3, "lotto approvato con firma valida")
ok(r.batch(CODE)["count"] == 0, "il lotto e vuoto dopo l'approvazione")

print("\n== ordinamento per ampiezza ==")
lst = r.list_rules(CODE,"tax")
ids = [x["id"] for x in lst["rules"]]
ok(ids == ["VA-02","PE-01","ST-03"], "ordine: _ALL_, gruppo, singoletto", ids)
ok(lst["rules"][0]["via"] == ["_ALL_"] and lst["rules"][0]["breadth"] == 6, "via e breadth riportati")
ok(r.list_rules(CODE,"market-news")["count"] == 1, "market-news vede solo la regola di tutti")
r.add_consumers(CODE, [("genera-dashboard","skill")])
ids2 = [x["id"] for x in r.list_rules(CODE,"tax")["rules"]]
ok(ids2 == ids, "aggiunto un consumer, l'ordine resta giusto da solo", ids2)
ok(r.list_rules(CODE,"genera-dashboard")["count"] == 1, "_ALL_ raggiunge un consumer creato DOPO la regola")

print("\n== allargare una regola ==")
v_before = r.history(CODE,"PE-01")["count"]
w = r.widen(CODE,"PE-01",["market-news"])
ok("market-news" in r.list_rules(CODE,"market-news")["rules"][0]["via"] or
   any(x["id"]=="PE-01" for x in r.list_rules(CODE,"market-news")["rules"]),
   "PE-01 adesso arriva anche a market-news")
ok(r.history(CODE,"PE-01")["count"] == v_before+1, "allargare scrive una versione")
ok(sorted(r.list_rules(CODE,"architect")["rules"][1]["via"]) == ["deliberativi"],
   "per l'architect PE-01 arriva ancora via deliberativi")

print("\n== lo storico e una fotografia ==")
h = r.history(CODE,"PE-01")["versions"]
old = [v for v in h if v["action"]=="created"][0]
r.edit_scope(CODE,"deliberativi", remove=["alt-funds"])
h2 = r.history(CODE,"PE-01")["versions"]
old2 = [v for v in h2 if v["action"]=="created"][0]
ok(old2["consumers"] == old["consumers"] and "alt-funds" in old2["consumers"],
   "la riga storica scritta PRIMA riporta ancora i consumer di allora", old2["consumers"])

print("\n== diniego e memoria dei rifiuti ==")
r.propose(CODE,"ST-04","R","Doppione","dice la stessa cosa di ST-03",["tax"],"boh","tax")
r.deny(CODE,["ST-04"],"caso singolo, non un pattern")
must_fail("un ID gia respinto non si ripropone",
          lambda: r.propose(CODE,"ST-04","R","Doppione","x",["tax"],"y","tax"), RulesError)
pend = r.pending(CODE,"tax")
ok(len(pend["denied"])==1 and pend["denied"][0]["denied_reason"].startswith("caso singolo"),
   "la bacheca mostra il diniego col motivo")

print("\n== scadenza ==")
r.cx.execute("UPDATE rules SET expires_at=? WHERE project='Financial Portfolio' AND id='ST-03'",
             ("2020-01-01T00:00:00Z",))
ok(all(x["id"]!="ST-03" for x in r.list_rules(CODE,"tax")["rules"]),
   "una provvisoria scaduta esce dalle liste da sola")
r.promote(CODE,["VA-02"], sign(__import__("hashlib").sha256(("promote|Financial Portfolio|VA-02").encode()).hexdigest()))
ok(r._row("Financial Portfolio","VA-02")["permanence"]=="permanent", "promozione a permanente")

print("\n== get multiplo ==")
g = r.get_rules(CODE, ["VA-02","PE-01","ZZ-99"], "market-news")
ok(len(g["found"])==2 and g["never_defined"]==["ZZ-99"], "found / never_defined separati", g)
g2 = r.get_rules(CODE, ["ST-03"], "market-news")
ok(len(g2["not_yours"])==1 and "tax" in g2["not_yours"][0]["held_by"], "not_yours dice chi la tiene")

print("\n== isolamento fra progetti ==")
r.propose(CODE2,"VA-02","R","Omonima","altro progetto",["*"],"x")
ok(r._row("Health Tracking","VA-02")["title"]=="Omonima" and
   r._row("Financial Portfolio","VA-02")["title"]=="Regola di tutti", "VA-02 convive nei due progetti")
must_fail("un consumer dell'altro progetto non esiste qui",
          lambda: r.list_rules(CODE2,"tax"), RulesError)
ok(r.project_info(CODE2)["scopes"][0]["breadth"] == 2, "_ALL_ e per progetto")
must_fail("codice sbagliato = codice mancante", lambda: r.project_info("xxxxxxxxxx"), RulesError)

print("\n== verdetti ==")
st = r.status(CODE); ck = r.check(CODE)
ok(st["database"]["integrity"]=="ok" and st["database"]["journal_mode"]=="wal", "integrity ok, WAL")
ok(st["rules"]["denied"]==1 and st["rules"]["proposed"]==0, "conteggi coerenti", st["rules"])
ok(any(b["from"]=="VA-02" for b in ck["broken_pointers"]) or ck["coherent"],
   "check gira", ck["verdict"])
ex = r.export(CODE,"architect")
ok("Reaching" in ex["markdown"] and ex["bytes"]>100, "export markdown per consumer")
bk = r.backup(os.path.join(d,"bk"))
ok(os.path.getsize(bk["backup"])>0, "backup VACUUM INTO")

print(f"\n{OK} passati, {FAIL} falliti")
sys.exit(1 if FAIL else 0)
