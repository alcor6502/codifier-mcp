# Codifier MCP <img align="right" src="https://img.shields.io/badge/License-MIT-yellow.svg">

<img src="https://img.shields.io/badge/versione-2.0.1-blue.svg"> <img src="https://img.shields.io/badge/python-3.12-3776AB.svg?logo=python&logoColor=white"> <img src="https://img.shields.io/badge/Unraid-7-F15A2C.svg"> <img src="https://img.shields.io/badge/MCP-29%20tool-8A63D2.svg">

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

    proposta ──(lotto approvato)──> attiva + provvisoria ──(promozione)──> permanente
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

**Si approva a lotti, contro il loro digest.** Le proposte si accumulano, e le
vedi tutte insieme, che è l'unico momento in cui salta all'occhio che tre
dicono la stessa cosa. `rules_batch` restituisce le proposte pendenti — ognuna
col suo perché — e un digest sull'insieme; `rules_approve` rivuole quel digest,
quindi ciò che viene approvato è provabilmente il lotto che è stato **letto**:
una proposta che arriva nel mezzo sposta il digest e invalida l'approvazione
stantia. L'approvazione sta dietro il codice di manutenzione. (Sopra viaggiava
una firma ed25519; è uscita nella v2.0.0 — era il modo goffo di far entrare una
persona invece di una chat, e la UI di amministrazione risolve il problema alla
radice.)

Il diniego non vuole digest: negare non può fare danno. La riga della regola
respinta resta, col suo motivo, e `rules_pending` mostra a una chat i propri
rifiuti — così la stessa idea che torna da un'altra chat fra tre settimane è
una cosa che si vede, non una cosa che il registro può impedire.

## Il numero non lo scegli tu

`rules_propose` prende il **dominio**, non l'ID: il numero lo assegna il
registro, quattro cifre, e lo restituisce. Un numero non è una scelta, è una
posizione in una successione — e chi non lo passa non lo può scegliere. Quattro
cifre perché gli ID non si riusano mai, quindi un dominio brucia numeri anche
restandone vive venti.

Non esiste più il referto sui buchi di numerazione, ed è la stessa decisione
vista dall'altro lato: col contatore un buco è impossibile, quindi segnalarne
uno avrebbe potuto solo voler dire che qualcuno aveva scelto.

## Le citazioni si marcano, si controllano, si espandono

Una citazione è un ID fra **parentesi tonde**, `(VA-0002)`. Una parentesi
qualunque è prosa qualunque — quello che rende un token una citazione è la forma
`XX-NNNN`, non la parentesi — così i `[[link del vault]]` restano liberi.

Alla porta il registro rifiuta la sigla nuda lasciata fuori da una parentesi
tutta sua (maiuscole o minuscole non cambia nulla), quella che non risolve,
quella che punta a una regola **non ancora approvata**, e qualunque nota tua
scritta dentro le parentesi — lì dentro non si conserva niente, e un registro
che ti mangia le parole in silenzio è peggio di uno che te le rifiuta. Si
cercano solo i domini che il progetto ha dichiarato, quindi un numero di ticket
o un locale dentro un URL resta prosa. È quest'ultima che dà la forma al
lavoro: prima entra la regola citata, poi la si approva, poi entra quella che la
cita. Il numero di una proposta non è definitivo finché non è dentro, quindi un
lotto i cui membri si citano fra loro si può approvare in uno stato dove i
rimandi erano giusti solo mentre li si scriveva.

In lettura ogni citazione porta il titolo attuale di ciò che punta:

    (AL-0004)  →  (AL-0004 — le quote degli alternativi non si vendono in perdita)

La glossa è generata, mai scritta — nel database entra solo il puntatore, ed è
per questo che non può invecchiare — e un rimando a una regola ritirata arriva
già marcato come tale, nel testo.

## Com'è fatto in pratica

```
rules_list(project="<codice>", consumer="tax monitor")

  VA-0002  Rileggi le fonti          via _ALL_          ampiezza 7
  PE-0001  Il metodo dei quattro     via deliberativi   ampiezza 4
  FI-0003  Stima del bracket         via tax monitor    ampiezza 1
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
3. **La cartella del database dev'essere storage locale**, mai una share di
   rete: SQLite in WAL vuole locking vero.

Il template in questo repository **è** la configurazione, e le descrizioni dei
campi sono la vera documentazione del deploy. Punta Unraid lì, riempi i campi,
Apply.

Il resto lo controlla il preflight all'avvio, ed è bloccante: un controllo
fallito esce 2 e al server non ci si arriva — un servizio che parte lo stesso e
avvisa è un servizio di cui nessuno legge gli avvisi.

## Sicurezza

- **OAuth 2.1 con GitHub, ristretto a un solo username.** È la porta d'ingresso.
- **Filtro sull'IP sorgente**, sopra OAuth e non al suo posto. Tutti e due i
  controlli scattano a ogni richiesta MCP, handshake compreso — non solo sulle
  chiamate ai tool. OAuth ferma chi non è autenticato, non chi si autentica col
  proprio account GitHub, e fino alla 1.1 compresa un estraneo così poteva
  comunque elencare ogni tool con la sua descrizione. Nessuna regola è mai
  uscita, ma la forma della superficie sì. Nessuno dei due controlli copre le
  rotte OAuth: un estraneo fuori dagli intervalli ammessi può completare il
  login lo stesso. Quello che non può fare è parlare MCP.
- **Il codice di manutenzione viaggia a ogni chiamata** che scrive: nessuna
  sessione, quindi nessuna modalità rimasta aperta per sbaglio. Leggere le
  proprie regole e depositare una proposta sono gratis — una chat di lavoro il
  codice non ce l'ha mai.
- **Un manuale solo, con la riga di stop.** `reference_guide` non prende
  nessun argomento: chiunque il gate lasci entrare lo legge. La parte per i
  consumer viene prima e finisce a una riga di stop; i tool di manutenzione
  oltre la riga vogliono il codice a ogni chiamata. Il manuale del legislatore
  della v1.4 è stato riassorbito: la sua porta proteggeva un'igiene senza
  lettori — il manuale lo leggono tre chat, e le skill non lo leggono affatto.
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
python3 test_collaudo.py    # il motore, rifiuti compresi
python3 test_surface.py     # la cucitura, l'immagine, il template
python3 test_crash.py       # SIGKILL a metà transazione, come fa Docker
```

Ogni suite stampa da sé quanti casi ha, e nessun file lo ripete. Un numero
scritto in due posti sono due numeri, e qui l'abbiamo già pagato una volta.

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
