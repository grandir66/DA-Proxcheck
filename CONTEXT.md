# CONTEXT.md — DA-Proxcheck

Passaggio di consegne: **leggilo per primo**, poi `CLAUDE.md` per le regole
tecniche vincolanti. Si aggiorna quando cambia architettura, moduli o stato —
**non per ogni fix**.

## 1. Cos'è e perché esiste

Audit di sola lettura di un nodo o di un intero cluster Proxmox VE, eseguito dal computer del tecnico via SSH, che confronta quanto rilevato con le best practice del manuale operativo Domarc e della guida alla configurazione VM per carico, e produce due Markdown per il cliente: inventario e rilievi. Nato il 2026-09-03 come `tool/audit-nodo.py` dentro il repo del manuale (`DA-Proxmox-Docs`), spostato qui il 2026-09-04 perché è diventato un programma con un ciclo di vita proprio.

Nel repository vive anche il **questionario di migrazione** (`questionario/questionario-migrazione.html`), che sta prima nel tempo: si compila col cliente *prima* di installare, mentre l'audit gira *dopo*. Sono due strumenti dello stesso mestiere — cosa serve sapere prima, cosa si controlla dopo — e per questo stanno insieme.

**Non è:** un agente installato sui nodi (quello è **DA-PVE-Agent**), né un sistema di monitoraggio continuo (**Proxreporter**, DA-Zabbix): gira quando lo lancia un tecnico, non modifica nulla, non lascia file sui nodi. Non contiene le regole: le applica. Le regole vivono nel manuale.

## 2. Stato — fotografia 2026-09-07

- Un solo file, `audit-nodo.py` (versione: vedi `VERSIONE_SCRIPT` in testa al file), stdlib only, Python 3.7+ sul client e il `python3` di Debian sui nodi.
- In esercizio: verificato su due cluster reali (uno a 4 nodi con ~54 VM, 5 minuti con `--performance`; uno a nodo singolo) e un terzo raggiungibile solo via VPN. Indirizzi e accessi in `ACCESSI.md` (git-ignored, master nel vault).
- Windows: supportato per costruzione (niente ControlMaster, console UTF-8, colori via SetConsoleMode), **non ancora provato** su una macchina Windows vera.
- Stato locale del tecnico in `~/.config/audit-nodo/`: `hosts.json` (host, etichetta, cliente, codice, parametri, esito dell'ultimo audit) e `profili-<cluster>.json` (VMID → profilo). Mai la password.

## 3. Architettura in breve

- `COLLECTOR_NODO` (stringa Python nel file) → eseguito su OGNI nodo con `python3 -`: `pvesh get … --output-format json` + `smartctl`, `ip`, `lvs`, `zpool`, `timedatectl`, `corosync-cfgtool`, `ping` fra gli anelli, `pveperf` se richiesto. Restituisce `{nodo, vms, lxc, errori}`.
- `COLLECTOR_CLUSTER` → eseguito sul nodo d'ingresso: raccoglie `/cluster/*`, esegue il collector-nodo in locale e poi `ssh -o BatchMode=yes root@<ip> python3 -` verso ogni altro nodo online (il sorgente del collector-nodo viaggia dentro in base64). Restituisce `{ingresso, cluster, nodi{…}, errori}`.
- Locale: `raccogli()` → `costruisci_vms()` → `controlla_cluster/nodo/hardware/performance/generali/profilo/lxc` → `Esito` di `Rilievo(livello, ambito, messaggio, fonte)` → `stampa_report` (terminale) + `scrivi_inventario_md` + `scrivi_rilievi_md`.
- La categoria di un rilievo si ricava dall'`ambito` (`categoria_di`): cambiare il testo di un ambito può spostare un rilievo di categoria.
- `fonti_manuale.py` (generato da `strumenti/estrai-fonti.py`) → il testo delle regole citate, che il report riporta in fondo nella sezione «Le regole applicate». Non si modifica a mano: si rigenera dal manuale. L'audit lo importa con un try/except, così chi ha copiato solo `audit-nodo.py` ha un audit che funziona, con le citazioni ma senza il loro testo.
- `PROFILI` (7 tipologie di carico, dal 2026-09-04; prima 13) raggruppa la Parte 3 della guida-carico per regole uguali; `CONVERSIONE_PROFILI_V1` converte i file salvati con i vecchi numeri. `INDIZI_PROFILO` propone la tipologia dal nome/SO (regex in ordine di priorità: test/replica prima di tutto).

### Il questionario (`questionario/questionario-migrazione.html`)

Una pagina sola, offline, IT/EN, senza dipendenze oltre ai font. Undici sezioni, 120 domande, sei tabelle. Tre pezzi da tenere distinti, perché si somigliano e non sono la stessa cosa:

| Meccanismo | Dice | Dove sta |
| --- | --- | --- |
| `base` / `ess` → **livello** | quanto in profondità si scende: `essenziale` (57 domande) ⊂ `base` (84) ⊂ `completo` (120) | `livelloDi()`, `visibile()` |
| `rosso: true` → **campo bloccante** | questa domanda **va risposta** (o con «da verificare»): senza, l'export si ferma | `C(…, {rosso:true})` |
| `REGOLE_BLOCCO` → **esito** | che cosa comporta il **modo** in cui è stata risposta, e cosa serve per toglierlo | 30 regole, `valutaBlocchi()` |
| `fonte:"§…"` → **la regola del manuale** | da dove viene la regola, col testo per esteso | blocco generato + «Le regole applicate» |

- **Il perimetro dell'essenziale sta in `ESSENZIALE`**, un elenco di id in cima al modello dati, non sparso dentro le definizioni dei campi: è una scelta che si deve poter leggere tutta insieme. Un id che non esiste finisce in `ESSENZIALE_IGNOTI` e fa fallire il cancello.
- **Le regole valutano sempre tutte le risposte**, anche quelle di domande che il filtro attivo nasconde: un blocco che sparisce cambiando vista sarebbe una bugia. E non impediscono l'export — un blocco aperto si consegna.
- **Le regole citano il manuale e ne mostrano il testo.** Ogni regola porta `fonte:"§…"`; `strumenti/estrai-fonti.py` legge quelle ancore, estrae i passaggi dal manuale e li scrive **dentro l'HTML**, fra i marcatori `FONTI DAL MANUALE: BLOCCO GENERATO` e `fine del blocco generato` — la pagina è autonoma, offline il testo deve essere lì. L'esito mostra la citazione accanto a ogni rilievo, e in coda al documento la sezione **«Le regole applicate»** riporta ogni passaggio **una volta sola** con la sua origine (`file:riga`, edizione del manuale): §11.2.1 è citato da cinque regole. Un piccolo renderer traduce il Markdown del manuale in HTML — §11.13 è una tabella, e senza sarebbe una colata di pipe.
- **Le ancore del questionario si leggono con un regex loro** (`RE_FONTE_QUESTIONARIO`), agganciato al campo `fonte:"…"`: quello condiviso con l'audit si ferma sull'apostrofo, e taglierebbe a metà `§11.5 › 11.5.1 Regole per l'import wizard`.
- **Il test gira in node** (`tests/test_questionario.mjs` + `tests/carica-questionario.mjs`): carica lo script della pagina in un DOM finto e prova modello dati, livelli, traduzioni e l'accensione di ogni regola. `scripts/controlla.sh` lo salta se node non c'è.
- La pagina è **pubblicata su GitHub Pages** (link nel README): un cambiamento qui è visibile al cliente al `push`.

## Fotografia 2026-09-11 — da verifica a sistema di migrazione

Tre strumenti, un motore: `audit-nodo.py` (destinazione Proxmox, 57 regole in
undici famiglie, comandi che chiudono i rilievi, prontezza §11.4),
`strumenti/raccogli-vcenter.py` (sorgente vSphere, REST `/api/`, solo GET,
campione a rotazione fra gli host, collaudato su 427 macchine) e
`strumenti/analizza-vcenter.py` (assessment: rilievi sulla sorgente, metodo di
§11.5 per macchina, ondate dimensionate sui dischi di §11.5.3, precompilazione
del questionario). Il secondo importa il motore del primo invece di ricopiarlo.
Entrambi mandano al portale con `--invia`; `--rilievi-json` scrive i rilievi
come dato e `confronta()` dice chiusi/rimasti/nuovi fra due verifiche.
Il catalogo delle regole e la specifica del sistema completo stanno in
`docs/superpowers/specs/2026-09-10-*`.

## 4. Trappole già risolte (non ripercorrerle)

- **Backtick nei messaggi di commit**: zsh li esegue. `git commit -m "... `cmd` ..."`
  sostituisce il comando e il nome sparisce dal messaggio — successo il
  2026-09-10, il commit era già pushato e non si riscrive. Nei messaggi si usano
  le virgolette semplici o si scrive senza backtick.
- **`pvesh get /cluster/firewall/options` può fallire** (errore di Perl, visto su
  PX-NAS il 2026-09-10). Le regole del firewall tacciono su quell'interruttore e
  continuano con il resto: metà del dato è meglio di nessuna regola.

- **`FAILED` in SMART** (2026-09-04): la tabella ATA ha la colonna `WHEN_FAILED` → ogni disco sano sembrava guasto → il controllo è ancorato a `self-assessment test result:`.
- **NVMe a 65 °C sano** (2026-09-04): le soglie 45/55 °C di Proxreporter sono per SATA → su NVMe la temperatura è informativa, conta `Critical Warning`.
- **`zd*` in `lsblk`/`disks/list`** sono gli zvol delle VM, non dischi → filtrati.
- **Guest agent**: un comando non supportato (MikroTik CHR) restituisce `{"error": …}` al posto della lista → guardie di tipo su tutti i risultati agent.
- **Scarto orologio di esattamente +2 h** = l'agent riporta l'ora locale, non un orologio rotto → informativo se multiplo di un'ora.
- **`pveperf` su ZFS** non misura letture e seek → "non misurato", non 0. E non si ripete su una dir che sta sul filesystem di root.
- **Interfacce `tap*`** dei guest finivano fra le "NIC fisiche" di un anello quando il bridge ha porte guest → escluse `tap/veth/fwln/fwpr/fwbr`.
- **`ControlPath` troppo lungo su macOS** (limite ~104 caratteri del socket) → risolto alla radice togliendo del tutto ControlMaster: una connessione sola non ne ha bisogno, e Windows non ce l'ha.
- **Provare l'interattività** richiede un vero pty (`sys.stdin.isatty()`), e l'harness deve aspettare il testo esatto del prompt *nuovo*, non cercarlo in un buffer cumulativo.
- **Le citazioni sono ancore, non testo libero**: `manuale §8.3 › Cache mode` deve esistere nel manuale. Finché erano stringhe libere, tre citazioni sbagliate sono rimaste lì per giorni (fleecing su §12.7 invece che §12.6, le reti sulla Parte 5 che è lo storage a blocchi, i certificati sulla Parte 13). Ora il cancello le verifica.
- **Nel questionario, «bloccante» non voleva dire «blocca»** (2026-09-07): i 37 campi rossi imponevano solo di *rispondere*. `storage_unmap = No` e `= Sì` valevano uguale, barra al 100% ed export permesso. Il giudizio sulle risposte ora sta in `REGOLE_BLOCCO`; il rosso resta l'obbligo di compilare. Due cose diverse, due meccanismi diversi.
- **Nomi dei file**: l'IP tiene i punti, il resto va a trattini; segmenti uguali consecutivi si collassano (`Nodo_Nodo`).

## 5. Decisioni prese (non riaprirle senza chiedere)

| Decisione | Perché | Quando / dove |
| --- | --- | --- |
| Una sola connessione SSH, collector remoto | Windows non ha ControlMaster; una password sola; decine di round-trip → uno | 2026-09-04, CHANGELOG (5) |
| Gli altri nodi via SSH interno del cluster, dal nodo d'ingresso | Proxmox distribuisce già le chiavi root fra i nodi; dal client servirebbe una password per nodo | 2026-09-04, richiesta utente |
| Sola lettura, mai installare pacchetti sui nodi | È un audit: `fio`/`iperf3` non si installano; `pveperf` è già presente e resta opt-in | 2026-09-03 |
| Da Proxreporter solo la parte di lettura | Niente SFTP/email/GELF: questo non è un agente | 2026-09-04, CHANGELOG (3) |
| Due file (inventario, report) con nome `codcli_cliente_ip_*` | Lettori diversi: l'inventario si allega, il report è la lista di lavoro | 2026-09-04, CHANGELOG (6) |
| Profili VM salvati per cluster (o hostname del nodo singolo), mai la password | VMID unici nel cluster, VM che migrano; l'indirizzo cambia, l'hostname no | 2026-09-04 |
| 7 tipologie, non 13; proposta automatica ma sempre confermabile | Regole uguali → stessa tipologia; nessuna proposta è meglio di una sbagliata | 2026-09-04, CHANGELOG (8) |
| Tre livelli nella stessa pagina, non un secondo file «essenziale» | Un file separato va tenuto allineato a mano: alla prima regola che cambia i due si contraddicono, e nessuno sa quale vale. Stessa pagina, stessa bozza, stesso export | 2026-09-07, CHANGELOG (10) |
| L'esito NON impedisce l'esportazione | Un blocco aperto è un'informazione da consegnare al cliente. Impedire l'export spingerebbe a togliere la risposta scomoda per poter esportare | 2026-09-07 |
| Nel report il testo della regola, estratto dal manuale — non il manuale intero, non una copia scritta a mano | Il report deve bastare a sé stesso; il manuale resta il documento di Domarc nel suo repository, e una copia integrale lo contraddirebbe | 2026-09-04, CHANGELOG (9) |
| Nessuna frase «di sintesi» accanto al rilievo | Provata e scartata: la frase più forte della sezione non è quella che motiva QUEL rilievo, e una motivazione sbagliata accanto a un bloccante è peggio di nessuna | 2026-09-04 |

## 6. Igiene / stale noti — 2026-09-04

- `scripts/deploy.sh` del kit non serve: non c'è un runtime Linux, lo script gira sul client. Lasciato per lo standard, non usato.
- Due report di prova prodotti dal menu sono rimasti non tracciati nella cartella `tool/` del repo del manuale: da cancellare a mano, contengono dati di infrastruttura.
