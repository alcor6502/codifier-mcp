# Codifier MCP <img align="right" src="https://img.shields.io/badge/License-MIT-yellow.svg">

<img src="https://img.shields.io/badge/versione-4.1.0-blue.svg"> <img src="https://img.shields.io/badge/python-3.12-3776AB.svg?logo=python&logoColor=white"> <img src="https://img.shields.io/badge/Unraid-7-F15A2C.svg"> <img src="https://img.shields.io/badge/MCP-16%20tool-8A63D2.svg">

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
| Due regole che dicono la stessa cosa | prima o poi qualcuno se ne accorge | la coda si legge intera prima di approvare |
| Qualcuno modifica il file a mano | invisibile | lo registra un trigger |

Il salto vero non è la consultazione: è che **il database rifiuta**. L'ID non si
riusa, il motivo non si omette, la cancellazione non esiste, e lo storico lo
scrivono i trigger — quindi c'è dentro anche una modifica fatta a mano con
`sqlite3`. Quelle che prima erano consegne adesso sono vincoli.

## Il modello in sei frasi

**Un progetto è un database**, nominato in un registro di testo che scrive a
mano il proprietario. I progetti non si vedono fra loro e nessun tool li elenca:
un progetto è un file, non una colonna, quindi un backup, un ripristino e una
corruzione riguardano un progetto solo e mai tutta la casa.

**Consumer** è chi si scarica le regole: chat *e* skill. Una skill agisce, e ciò
che agisce sta sotto regole. Una persona non è un consumer — una regola che
vincola una persona lo dice nel proprio corpo. Il nome di un consumer è UNA
PAROLA, perché quel nome si cita a mano nelle istruzioni delle chat e nei file
delle skill, e lo spazio è l'errore che nessuno vede.

**La platea è MISTA, e si dichiara invece di dedurla.** `reach` vale `all` —
tutti, e nessuna riga di platea — oppure `targeted`, e allora la platea è i
**gruppi** UNIONE le **eccezioni**: consumer singoli che stanno ACCANTO ai
gruppi e possono solo aggiungere. Una regola `targeted` che non nomina nessuno
la rifiuta un trigger, e così una `all` che nomina qualcuno.

**L'ordine di lettura è l'ampiezza della porta da cui sei entrato.** Prima
l'universale, poi i gruppi dal più largo, ultimo ciò che ti è stato indirizzato
per nome — e siccome l'ampiezza è un `COUNT` dei membri vivi calcolato adesso,
l'ordine resta giusto da solo quando nasce un consumer o un gruppo si svuota.

**Allargare vincola qualcuno di nuovo, quindi è promulgazione**, non una
modifica: `rules_amend` restringe un perimetro e si rifiuta di allargarlo. Per
allargare si propone un supersede e una persona lo approva.

**Lo storico è una fotografia.** Ogni versione registra sia cosa era stato
dichiarato sia chi era raggiunto davvero quel giorno, per nome, così cambiare un
gruppo domani non riscrive quello che era vero ieri.

**I task vivono nello stesso registro e sono modellati come l'opposto di una
regola**: niente platea, niente approvazione, niente scadenza. Le regole
vincolano, i task aspettano.

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
dicono la stessa cosa. La pagina del lotto mostra la coda intera, ogni proposta
col suo perché, e calcola un digest su quello che ha mostrato; l'azione quel
digest deve restituirlo, quindi ciò che viene approvato è provabilmente il lotto
che è stato **letto** — una proposta che arriva nel mezzo sposta il digest e
invalida l'approvazione stantia. Approvare non è un tool dalla v3.0.0: succede
in un browser, dietro la password della UI, così nessun segreto di quel livello
viaggia mai in una conversazione. (Sopra viaggiava una firma ed25519; è uscita
nella v2.0.0 — era il modo goffo di far entrare una persona invece di una chat,
e la UI di amministrazione risolve il problema alla radice.)

Il diniego non vuole digest: negare non può fare danno. Costa però una frase,
una per proposta. La riga della regola respinta resta, col suo motivo, e
`rules_list(pending=True)` mostra a una chat i propri rifiuti — così la stessa
idea che torna da un'altra chat fra tre settimane è una cosa che si vede, non
una cosa che il registro può impedire.

Quante proposte possono aspettare insieme è `queue_cap`, ed è del **progetto**,
scritto nel database del progetto — non del container, che ne serve più d'uno.
NULL è senza limite, 0 chiude la coda, N è N.

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
rules_list(project="<codice>", consumer="tax")

  VA-0002  Rileggi le fonti          reach all        ti raggiunge: tutti
  PE-0001  Il metodo dei quattro     reach targeted   ti raggiunge: deliberativi
  FI-0003  Stima del bracket         reach targeted   ti raggiunge: per nome
  ...
  38 regole · e in fondo i task aperti sulla tua scrivania
```

`reaches_you` dice *perché* una regola è nel tuo elenco — tutti, un gruppo a cui
appartieni, o il tuo nome — che è esattamente l'informazione che serve per
decidere se sta nel posto giusto. La stessa chiamata porta il brief del
progetto, il tuo, e i task aperti sulla tua scrivania: una chiamata, perché
l'alternativa erano quattro, e una chat che deve farne quattro prima di poter
lavorare una volta ne sbaglia tre.

## La pagina di amministrazione

Approvare una regola non è lo stesso gesto che scriverla, e dalla v2.1 i due
non avvengono più nello stesso posto. Una chat propone; una persona approva, in
un browser, sulla LAN.

La pagina la serve lo stesso processo, su una seconda porta — 9443 per difetto
— perché due processi sullo stesso SQLite non condividono il lock del motore.
La home elenca i progetti che il registro serve, per NOME: chi è entrato ha già
dimostrato chi è con la password, e un URL può portare un nome dove una chat può
portare solo un codice. Tutto quello che sta sotto è per progetto.

La pagina del lotto mostra il pendente **intero e affiancato**, ogni proposta
col perché è stata depositata: è lì che tre proposte che dicono la stessa cosa
si riconoscono. Si spunta cosa entra, si dà una ragione per cosa no, e la
password si digita **una volta per azione** — quattro regole non sono quattro
password, e una password ripetuta quattro volte si digita senza guardare.

Una proposta che ne sostituisce un'altra lo dice **prima** che tu decida, con
l'ID della vittima e il suo titolo attuale: approvarla la ritira nella stessa
transazione, e chi approva legge le due metà della mossa.

Il digest copre quello che stavi **guardando**, non quello che hai spuntato. Se
una proposta arriva mentre leggi, l'azione torna respinta con la pagina com'è
adesso — lo stesso contratto del digest che aveva il tool MCP.

Accanto, letture che non scrivono niente: le regole in forza per un consumer,
esattamente come le legge la sua chat, brief in testa; il dettaglio di una
regola con la sua storia e il diff fra due versioni; i rinnovi e la coda delle
scadenze; e lo stato del progetto. E una pagina che scrive senza toccare
nessuna regola: **codes**, dove si conia un codice monouso.

La password si richiede a ogni gesto che SCRIVE — decidere il lotto, rinnovare,
promuovere, coniare un codice — perché una sessione da sola è un browser
lasciato aperto sull'iPad. Non si richiede per il backup né per il log: un
`VACUUM INTO` non cambia niente e il log è un anello in memoria, e una password
ridigitata dove non difende niente insegna solo alla mano a digitarla senza
guardare. Una password, dal template, e un'ora di inattività. Un riavvio del
servizio invalida tutte le sessioni, di proposito: il segreto della sessione
nasce a caso a ogni boot e non è scritto da nessuna parte.

**Cosa non c'è più, dalla v4.0.0: la pagina di deployment.** Creava i progetti,
li rekeyava e ne stampava i codici, e tutte e tre le cose sono morte col
registro dichiarativo — un progetto adesso è una riga in `projects.txt`, che
scrive da Unraid chi ne sceglie i codici. Al suo posto c'è la pagina dei codici:
coniare i monouso è l'unica cosa che il disegno dà a questa UI e a nient'altro.

**La superficie MCP si è mossa di nuovo nella v4.0.0 — riconnettere il
connettore e provare in una chat nuova.** Da 32 tool a 16, e i nomi si sono
mossi con lei: a una chat servono `reference_guide`, `project_info`,
`rules_list`, `rules_get`, `rules_propose` e i cinque `tasks_*`, e tutto quello
che fa un amministratore sta in sei — `project_amend`, `rules_amend`,
`rules_retire`, `project_status`, `rules_export`, `tasks_overview`. Un
connettore rimasto sulla superficie vecchia non degrada: elenca tool che non
ci sono.

La superficie intera, e un test tiene questo blocco contro il codice — un README
che promette un argomento che il tool non ha è la copia che diverge per prima:

    reference_guide(name='', project='', key='')
    project_info(project)
    rules_list(project, consumer, query='', pending=False)
    rules_get(project, ids, consumer, history=False)
    rules_propose(project, domain, type, title, body, reason, reach,
                  proposed_by, groups=[], exceptions=[], supersedes='',
                  source='', consumer_key='')
    tasks_add(project, consumer, title, body, created_by, urgent=False,
              idem_key='', consumer_key='')
    tasks_list(project, consumer, query='', since='', until='',
               authored=False)
    tasks_get(project, ids)
    tasks_close(project, id, by, outcome='', reason='', consumer_key='',
                key='')
    tasks_amend(project, id, by, title='', body='', consumer='',
                consumer_key='', key='')
    project_amend(project, entity, name, action, fields={}, reason='',
                  auth_code='', key='')
    rules_amend(project, id, reach, groups, exceptions, expected_version,
                reason, auth_code, key)
    rules_retire(project, id, reason, auth_code, key)
    project_status(project, key)
    rules_export(project, key, consumer='', expand=False)
    tasks_overview(project, key)

**Dalla v4.1.0 il manuale si prende un comando per volta.** Chiamato nudo,
`reference_guide` serve una pagina di modello corta più l'elenco dei nomi delle
schede; chiamato con un nome serve quel comando spiegato per intero, rifiuti
compresi. Il manuale nel suo insieme è cresciuto: a rimpicciolirsi è quello che
si paga per chiedere una cosa sola.

## Il task log

Le regole sono ciò che ti VINCOLA. I task sono ciò che ti ASPETTA — una cosa
diversa, e modellata come tale: niente platea, niente approvazione, niente
firma, niente scadenza. Il log esiste perché *cosa è aperto per me?* sia una
sola chiamata, e con l'esito obbligatorio alla chiusura lo diventa anche
*cosa ho fatto di recente?*. Sostituisce sia il changelog per ruolo sia le
sezioni «pending» che le memorie di ruolo si tenevano.

Gli ID sono `TK-NNNN`, mai riusati, citati come una regola: `(TK-0012)`. `TK`
non può essere dichiarato come dominio di regole — il registro rifiuta —
perché il codice deve significare una cosa sola.

**Chiunque può aprire un task per chiunque**, ed è così che un audit assegna
ogni correzione al ruolo competente. `created_by` è obbligatorio. ⚠ Aprire un
task per una **persona** non avvisa nessuno: le persone non chiamano tool, e la
loro posta la vede chi legge l'overview o la UI. **Chiudere costa una frase**:
`tasks_close` prende un `outcome` che lo completa o una `reason` che lo lascia
cadere, esattamente uno dei due, e il rifiuto sta nello schema oltre che alla
porta. **Chiuso è chiuso** — un task aperto si emenda liberamente, proprietario
compreso, uno chiuso per niente.

**`urgent` è di chi crea il task** e nessuno lo cambia dopo, perché chi lo
riceve è la parte che ha interesse a toglierlo. Non ci sono livelli; la
guardia contro l'inflazione è che `tasks_overview` conta gli urgenti per
CREATORE.

**I task non scadono.** Uno aperto da più di trenta giorni esce marcato, e
basta: una scadenza automatica sarebbe un drop senza ragione, scritto
dall'orologio. Le liste sono in forma breve e le ordina il server — urgenti
in testa, poi i più vecchi — così quando il tetto taglia il taglio colpisce
il lavoro fresco e mai quello che aspetta. Il troncamento si dichiara sempre,
col totale reale.

## Installazione

Fatto per Unraid col plugin Tailscale, ma è un container normale: un mount per
i database, uno per lo stato, e variabili d'ambiente.

1. **Una OAuth App GitHub dedicata.** Homepage `BASE_URL`, callback
   `BASE_URL/auth/callback`. Non riciclare quella di un altro servizio, o i due
   si contendono la callback.
2. **`JWT_SIGNING_KEY`**: `openssl rand -hex 32`. Stabile per sempre — cambiarla
   invalida ogni token già emesso.
3. **`WEB_UI_PASSWORD`**, dodici caratteri o più. Apre la pagina che promulga le
   regole, non c'è un secondo account e non c'è recupero.
4. **La cartella dei database dev'essere storage locale**, mai una share di
   rete: SQLite in WAL vuole locking vero.

Il template in questo repository **è** la configurazione, e le descrizioni dei
campi sono la vera documentazione del deploy. Punta Unraid lì, riempi i campi,
Apply.

**Poi si dichiara un progetto.** Finché non lo fai il servizio non serve niente.
Nella cartella dei database il primo avvio scrive `projects.txt`, solo root, con
le istruzioni già dentro; tu ci aggiungi una riga per progetto:

    Financial Portfolio | <codice di riferimento> | <codice admin>

Nome, codice di riferimento, codice admin. I due codici stanno lì scritti come
segnaposto e non come cifre plausibili di proposito, ed è la stessa decisione
per cui il modello dentro `projects.txt` non porta nessuna riga d'esempio: una
riga con codici verosimili è una riga che qualcuno copia. I due codici sono da 8 a 32 lettere e
cifre, li generi tu (`openssl rand -hex 12`), e nessun codice può comparire due
volte nel file — lo stesso codice su due progetti, o un codice di riferimento
uguale al proprio admin, viene rifiutato per nome e numero di riga. Il nome è
una **cartella** accanto al file, in quella grafia, e dentro c'è il `.db` del
progetto; rinominare un progetto vuol dire cambiare la riga *e* rinominare la
cartella. Una riga senza database ne crea uno, vuoto e corrente, e lo dice nel
log; un database senza riga non è servito. Il file si rilegge quando cambia
l'mtime, quindi aggiungere un progetto non vuole nessun riavvio, e un file che
non si legge ferma tutto citando la riga incriminata invece di servire mezza
verità.

Il codice di riferimento va in testa alle istruzioni delle chat di quel
progetto; il codice admin va a chi lo amministra, e da nessun'altra parte.

**Aggiornare dalla 3.x non è una migrazione.** Non ce n'è una, per decisione: un
database di generazione di schema diversa viene rifiutato all'avvio, nominando
il file e i due numeri, mai aggiornato in silenzio. Il registro v4 nasce vuoto.

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
- **Tre credenziali, e la scala è piatta.** Il **codice di riferimento** apre
  ogni lettura di un progetto e lascia a una chat depositare una proposta — una
  proposta non raggiunge nessuno finché una persona non l'approva, quindi non
  può fare danno, e chiedere a una chat di lavoro qualcosa di più forte
  metterebbe quel qualcosa in ogni chat. Il **codice admin** crea: un dominio,
  un consumer, un gruppo. Modificare qualcosa che esiste già — un perimetro, un
  ritiro, un rename, un brief, i membri di un gruppo — vuole il codice admin
  **più un codice monouso**, coniato nel browser sulla pagina di quel progetto,
  mostrato una volta sola, bruciato dentro la transazione del gesto riuscito. Un
  rifiuto fa rollback e non lo consuma; da solo non eleva nessuno. Il ruolo non
  eleva: eleva la chiave.
- **Due manuali, in due file.** `reference_guide()` nudo serve la metà per chi
  consuma; la metà di amministrazione vuole il codice di progetto *e* il codice
  admin. Sono due file e non un testo tagliato a un marcatore, così «il manuale
  admin servito senza chiave» non è un guasto da provare: è uno che non può
  succedere.
- **Il file del registro è la cassaforte.** `projects.txt` tiene in chiaro tutti
  i codici di riferimento e admin, che è la decisione, ed è l'unico file qui
  dentro solo root, 0600. Il modo si rimette a ogni rilettura, non solo alla
  creazione: si edita da una share, e un editor che scrive un file nuovo e lo
  rinomina sopra al vecchio ci porta il proprio modo.
- **Una chiamata malformata non stampa quello che portava.** FastMCP valida gli
  argomenti prima che parta qualunque tool e logga quello che ha rifiutato, con
  gli argomenti dentro la riga — un record che non obbedisce a nessun LOG_LEVEL
  nostro e non lascia nessuna riga `refused`, quindi un log pulito non prova che
  non sia successo. Qui quegli argomenti sono i codici del progetto. Dalla
  v2.1.1 il carico è redatto e la diagnosi no: restano il tool, il parametro e
  la regola violata.
- **Il processo gira come root e il database è 644.** È l'opposto del gemello
  vault, di proposito: dalla share si legge e non si tocca, perché una scrittura
  a mano aggirerebbe i trigger e romperebbe lo storico in silenzio.
- **I codici di progetto non sono un confine di sicurezza.** Sono opachi perché
  due progetti non si trovino per sbaglio; nessun tool li elenca e nessun errore
  ne nomina uno, e un codice sbagliato risponde esattamente come uno mancante.
  Il confine vero è il gate OAuth davanti.

## Collaudo

Cinque suite. Niente rete, niente FastMCP, niente Docker.

```
python3 test_schema.py      # il DDL: trigger, vincoli, generazione
python3 test_registry.py    # projects.txt, il router, i rifiuti che solleva
python3 test_collaudo.py    # il motore, rifiuti compresi
python3 test_surface.py     # la cucitura, l'immagine, il template
python3 test_crash.py       # SIGKILL a metà transazione, come fa Docker
```

Ogni suite stampa da sé quanti casi ha, e nessun file lo ripete. Un numero
scritto in due posti sono due numeri, e qui l'abbiamo già pagato una volta.

`test_surface.py` legge il sorgente invece di eseguirlo: ogni chiamata al motore
deve esistere con firma compatibile, ogni tool che scrive deve passare dal gate
che dichiara, nessuna docstring può nominare un tool che non esiste, e ogni
variabile che il template dichiara deve avere un lettore nel codice — l'ultimo
perché quattro manopole morte sono sopravvissute a tre grani dentro un modulo
che una persona compila con cura.

## L'icona, e dove si vede davvero

`codifier-icon.png` è puntata dal suo URL raw di GitHub da due file: il template
Unraid, che la mette sul container, e `server.py`, che la passa a FastMCP come
`icons=[…]`. Un controllo confronta i due URL, perché due copie a mano della
stessa stringa hanno una data di scadenza.

Passare `icons` compra **la pagina di consenso OAuth** — quella che compare
quando il connettore si aggiunge o si riconnette — dove FastMCP la mostra al
posto del proprio logo.

**Non** compra l'icona nella lista dei connettori di Claude. Quella superficie
ignora del tutto `serverInfo.icons`, che la spec MCP porta dalla revisione
`2025-11-25` (SEP-973); servire `/favicon.ico` e mettere un `<link rel="icon">`
su una pagina di radice vengono ignorati allo stesso modo. L'issue è
[anthropics/claude-ai-mcp#152](https://github.com/anthropics/claude-ai-mcp/issues/152).
Sotto Funnel quella lista mostra l'icona di Tailscale, che è coerente con
un'icona derivata dal DOMINIO — niente in questo repository ci arriva. Il campo
si manda lo stesso: il giorno che il client lo legge, la lista segue senza
toccare niente.

## Il gemello

[archivist-mcp](https://github.com/alcor6502/archivist-mcp) — un vault di
documenti con versionamento git per dataset. Stessa architettura, stesso gate
OAuth, stesso preflight bloccante. Quello custodisce file, questo custodisce
regole.

## Licenza

MIT.
