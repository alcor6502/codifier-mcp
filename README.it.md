# Codifier MCP <img align="right" src="https://img.shields.io/badge/License-MIT-yellow.svg">

<img src="https://img.shields.io/badge/versione-1.0.2-blue.svg"> <img src="https://img.shields.io/badge/python-3.12-3776AB.svg?logo=python&logoColor=white"> <img src="https://img.shields.io/badge/Unraid-7-F15A2C.svg"> <img src="https://img.shields.io/badge/MCP-30%20tool-8A63D2.svg">

**Le regole di un progetto in un registro invece che sparse nei Markdown — così
una chat può rispondere in una chiamata a «sotto quali regole sto?».**

Self-hosted. Niente esce dalla macchina se non verso la conversazione che lo ha
chiesto. Le regole non si cancellano, gli ID non si riusano, e lo storico lo
scrive il database.

🇬🇧 [Read in English](README.md)

---

## Perché esiste

Le regole di un progetto LLM nascono in un file. Poi un ruolo ne vuole di
proprie, poi un secondo, e diciotto mesi dopo sono 177 sparse su tre documenti
più le memorie dei ruoli. Ogni chat ne apre tre per usarne quaranta.

Il costo di contesto è il sintomo. **La malattia è che nessuno può rispondere in
fretta a: questa chat, adesso, sotto quali regole sta?** Per rispondere oggi
bisogna leggere tre file, tenere a mente quale vale per chi, e fidarsi che
nessuno abbia scritto due volte la stessa cosa in due posti. È un lavoro di
lettura, e siccome è un lavoro di lettura viene fatto male.

|  | Regole nei Markdown | Codifier |
|---|---|---|
| «Quali regole valgono per me?» | apri tre file e filtri a mano | una chiamata, ordinata |
| Cambiare una regola che vive in tre memorie | tre modifiche, e la terza la dimentichi | una |
| Riusare il numero di una regola ritirata | niente te lo impedisce | il database rifiuta |
| «Perché c'è questa regola?» | chiedi a chi l'ha scritta | il motivo è obbligatorio, e resta |
| Una regola che non serve più | resta per sempre | scade se nessuno la rinnova |
| Due regole che dicono la stessa cosa | prima o poi qualcuno se ne accorge | segnalate come coppia sospetta |
| Qualcuno modifica il file a mano | invisibile | lo registra un trigger |

Il salto vero non è la consultazione: è che **il database rifiuta**. L'ID non si
riusa, il motivo non si omette, la cancellazione non esiste, e lo storico lo
scrivono i trigger — quindi c'è dentro anche una modifica fatta a mano con
`sqlite3`. Quelle che prima erano consegne adesso sono vincoli.

## Il modello in cinque frasi

**Consumer** è chi si scarica le regole: chat *e* skill. Una skill agisce, e ciò
che agisce sta sotto regole. Una persona non è un consumer — una regola che
vincola una persona lo dice nel proprio corpo.

**Scope** sono insiemi nominati di consumer. Non esiste una nozione separata di
«gruppo»: un consumer singolo è un insieme con un elemento, e il suo scope
singoletto lo crea un trigger quando il consumer nasce. Un solo tipo di
puntatore, nessun ramo dove sbagliarsi.

**L'ordine di lettura è l'ampiezza dello scope.** Prima quella che vale per
chiunque, ultima quella che vale solo per te — e siccome l'ampiezza è un
`COUNT`, l'ordine resta giusto da solo quando nasce un consumer nuovo.

**Una regola punta a un insieme di scope.** Allargarla è una riga in più; il
gruppo a cui apparteneva non si tocca, perché quel gruppo ha altri inquilini.

**Lo storico è una fotografia.** Ogni versione registra sia cosa era stato
dichiarato (`scopes`) sia chi era raggiunto davvero quel giorno (`consumers`),
così cambiare un gruppo domani non riscrive quello che era vero ieri.

## Come entra una regola

    proposta ──(lotto firmato)──> attiva + provvisoria ──(firma)──> permanente
        │                              │
        │                              └──> ritirata
        └──> respinta  (col motivo, e la riga RESTA)

Due meccanismi, e nascono dalla stessa diagnosi: un progetto è passato da 63
regole a 172, non perché qualcuno scrivesse senza permesso, ma perché
**aggiungere costa una chiamata e togliere costa una decisione che nessuno
prende.**

**La scadenza inverte l'asimmetria.** Una regola approvata è provvisoria ed esce
dalle liste da sola se nessuno decide di tenerla. Tenerla costa una decisione,
lasciarla andare è gratis.

**Si approva a lotti, e si firma.** Una chat non può chiederti di firmare in
mezzo a una conversazione — le proposte si accumulano, e le vedi tutte insieme,
che è l'unico momento in cui salta all'occhio che tre dicono la stessa cosa. La
firma è ed25519 sul digest del lotto; il registro tiene **solo la chiave
pubblica**, quindi anche col database in mano nessuno può fabbricare
un'approvazione. La metà privata non entra mai in una conversazione — non per
disciplina, per costruzione.

Il diniego non vuole firma: negare non può fare danno. E la riga di una regola
respinta resta, così la stessa idea non torna da un'altra chat fra tre
settimane.

## Com'è fatto in pratica

```
rules_list(project="<codice>", consumer="tax monitor")

  VA-02  Rileggi le fonti            via _ALL_          ampiezza 7
  PE-01  Il metodo dei quattro       via deliberativi   ampiezza 4
  FI-03  Stima del bracket           via tax monitor    ampiezza 1
  ...
  38 regole in vigore · 132 fuori dal tuo perimetro
```

`via` dice *perché* una regola è nel tuo elenco, che è esattamente
l'informazione che serve per decidere se sta nel posto giusto.

## Installazione

Fatto per Unraid col plugin Tailscale, ma è un container normale: un mount per
il database, uno per lo stato, e variabili d'ambiente.

1. **Una OAuth App GitHub dedicata.** Homepage `BASE_URL`, callback
   `BASE_URL/auth/callback`. Non riciclare quella di un altro servizio, o i due
   si contendono la callback.
2. **`JWT_SIGNING_KEY`**: `openssl rand -hex 32`. Stabile per sempre — cambiarla
   invalida ogni token già emesso.
3. **Una coppia di chiavi ed25519**, sulla tua macchina:
   `python3 sign.py --keygen`. Stampa la metà pubblica, che va in
   `APPROVAL_PUBKEY`; la privata resta in `~/.codifier/approval.key` a 0600 e
   non viaggia mai. Lo stesso script firma poi i digest dei lotti:
   `python3 sign.py <digest>`.
   Gli serve `cryptography`, e macOS e Linux recenti rifiutano un `pip install`
   normale nel Python di sistema — quindi fanne un venv una volta sola,
   `python3 -m venv ~/.codifier/venv`, installalo lì, e dimenticatene: sign.py
   quel venv lo trova e ci si ri-esegue dentro.
   Finché stai ancora montando tutto puoi lasciare la chiave vuota e mettere
   `APPROVAL_GRACE_UNTIL` a una data vicina — è una data e non un interruttore,
   quindi si chiude da sola.
4. **La cartella del database dev'essere storage locale**, mai una share di
   rete: SQLite in WAL vuole locking vero.

Il template in questo repository **è** la configurazione, e le descrizioni dei
campi sono la vera documentazione del deploy. Punta Unraid lì, riempi i campi,
Apply.

Il resto lo controlla il preflight all'avvio, ed è bloccante: un controllo
fallito esce 2 e al server non ci si arriva — un servizio che parte lo stesso e
avvisa è un servizio di cui nessuno legge gli avvisi.

## Sicurezza

- **OAuth 2.1 con GitHub, ristretto a un solo username.** È la porta d'ingresso.
- **Filtro sull'IP sorgente** a ogni chiamata, sopra OAuth, non al suo posto.
- **Il codice di manutenzione viaggia a ogni chiamata** che scrive: nessuna
  sessione, quindi nessuna modalità rimasta aperta per sbaglio. Leggere le
  proprie regole e depositare una proposta sono gratis — una chat di lavoro il
  codice non ce l'ha mai.
- **Il processo gira come root e il database è 644.** È l'opposto del gemello
  vault, di proposito: dalla share si legge e non si tocca, perché una scrittura
  a mano aggirerebbe i trigger e romperebbe lo storico in silenzio.
- **I codici di progetto non sono un confine di sicurezza.** Sono opachi perché
  due progetti non si trovino per sbaglio; nessun tool li elenca e nessun errore
  ne nomina uno, e un codice sbagliato risponde esattamente come uno mancante.
  Il confine vero è il gate OAuth davanti.

## Collaudo

Tre suite. Niente rete, niente FastMCP, niente Docker.

```
python3 test_collaudo.py    # 161 casi — il motore, rifiuti compresi
python3 test_surface.py     # 185 casi — la cucitura, l'immagine, il template, il firmatario
python3 test_crash.py       # SIGKILL a metà transazione, come fa Docker
```

`test_surface.py` legge il sorgente invece di eseguirlo: ogni chiamata al motore
deve esistere con firma compatibile, ogni tool che scrive deve passare dal gate
di manutenzione, e nessuna docstring può nominare un tool che non esiste.

## Il gemello

[archivist-mcp](https://github.com/alcor6502/archivist-mcp) — un vault di
documenti con versionamento git per dataset. Stessa architettura, stesso gate
OAuth, stesso preflight bloccante. Quello custodisce file, questo custodisce
regole.

## Licenza

MIT.
