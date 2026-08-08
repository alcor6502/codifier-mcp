"""test_crash.py — la domanda era: cosa succede se il container muore a meta'
scrittura? Qui si ammazza il processo con SIGKILL (che e' quello che fa Docker)
DENTRO una transazione aperta, e si riapre il database.

Atteso: la scrittura a meta' non c'e', il database e' integro, lo storico non ha
versioni fantasma. Nessun intervento manuale."""
import os, signal, sqlite3, subprocess, sys, tempfile, textwrap

qui = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, qui)
from rules import Registro, TUTTI

d = tempfile.mkdtemp()
db = os.path.join(d, "regole.db")

R = Registro(db)
CODICE = "Tst1Prova99"
R.crea_progetto(CODICE, "Prova", ["architect"], {"VA": "vault e file"})
R.crea(CODICE, "VA-02", "R", "Prima regola", "Corpo originale.", [TUTTI], motivo="setup")
R.cx.close()

# processo figlio: apre, scrive, NON committa, e si fa ammazzare
figlio = textwrap.dedent(f"""
    import os, sys, signal
    sys.path.insert(0, {qui!r})
    from rules import Registro
    R = Registro({db!r})
    R.cx.execute("BEGIN IMMEDIATE")
    R.cx.execute("UPDATE rules SET corpo='CORPO MAI COMMITTATO', motivo='crash', "
                 "updated_at='2026-01-01T00:00:00Z' WHERE progetto='Prova' AND id='VA-02'")
    R.cx.execute("INSERT INTO rules (progetto,id,domain,seq,tipo,titolo,corpo,stato,motivo,updated_at) "
                 "VALUES ('Prova','VA-99','VA',99,'R','fantasma','mai nato','attiva','crash','2026-01-01T00:00:00Z')")
    sys.stdout.write("scritto, non committato\\n"); sys.stdout.flush()
    os.kill(os.getpid(), signal.SIGKILL)
""")
p = subprocess.run([sys.executable, "-c", figlio], capture_output=True, text=True)
print(f"  figlio: {p.stdout.strip()!r} — segnale: {-p.returncode} (9 = SIGKILL)")
assert p.returncode == -signal.SIGKILL, p

R2 = Registro(db)
corpo = R2.cx.execute("SELECT corpo FROM rules WHERE id='VA-02'").fetchone()[0]
fantasma = R2.cx.execute("SELECT COUNT(*) FROM rules WHERE id='VA-99'").fetchone()[0]
integ = R2.cx.execute("PRAGMA integrity_check").fetchone()[0]
versioni = R2.storico(CODICE, "VA-02")["versioni"]

print(f"  corpo dopo il crash : {corpo!r}")
print(f"  righe fantasma      : {fantasma}")
print(f"  integrity_check     : {integ}")
print(f"  versioni in storico : {versioni}")

assert corpo == "Corpo originale.", "la scrittura non committata e' sopravvissuta!"
assert fantasma == 0, "riga fantasma sopravvissuta!"
assert integ == "ok"
assert versioni == 1, "versione fantasma nello storico!"
print("\nOK — rollback automatico, database integro, storico pulito.")
