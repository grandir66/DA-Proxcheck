# Changelog — DA-Proxcheck

Cosa è cambiato e **perché**, per chi non usa git. Più recenti in alto.

## 2026-09-04 (8)

### Sette tipologie di VM invece di tredici, e una proposta automatica dal nome

Richiesta: "vorrei semplificare le tipologie". Le 13 di prima erano la trascrizione uno a uno dei paragrafi della guida-carico, e diverse condividevano le stesse regole.

- **Sette tipologie**, raggruppate per regole uguali: 1 domain controller/DNS · 2 database · 3 applicativo/web/monitoraggio · 4 rete (firewall, proxy, load balancer) · 5 dati (file server, log/SIEM, backup server) · 6 terminal server/VDI · 7 test/legacy/replica. Dove due profili uniti differivano in un dettaglio, il dettaglio resta come nota informativa, non come rilievo
- **Proposta automatica** dal nome della VM e dal sistema operativo visto dall'agent (`INDIZI_PROFILO`): la tabella la mostra con un asterisco, INVIO accetta tutte le proposte, un VMID la cambia, `t` chiede una per una con la proposta come default. Senza terminale vale la proposta. Sul cluster di prova (54 VM) propone 42 tipologie e nessuna sbagliata; le 12 senza indizio restano non classificate — meglio nessuna proposta che una errata
- **I file di profili già salvati si convertono da soli** (13 → 7, tabella `CONVERSIONE_PROFILI_V1`); il file porta ora `"_versione": "2"`
- Il file dei profili di un nodo singolo prende il nome dall'hostname, non dall'indirizzo con cui ci si collega (prima `root@ip` e `ip` davano due file diversi)
- Bug trovato provando: le VM salvate come "non classificata" non ricevevano la proposta al giro successivo

## 2026-09-04 (7)

### Nasce il repository: `audit-nodo.py` esce dal manuale Proxmox

Lo script è cresciuto per due giorni dentro `manuali/proxmox/tool/` (repo `DA-Proxmox-Docs`), accanto al manuale di cui applica le regole. È diventato un programma di 2.000 righe con un proprio ciclo di vita: da oggi ha un repository suo, `DA-Proxcheck`, creato con `bin/nuovo-progetto` secondo lo standard di `~/Progetti`. Nel manuale resta il rimando (Appendice A.17-bis) e la storia qui sotto, copiata dal CHANGELOG del manuale perché è la storia di questo programma.

- Aggiunti `tests/test_parser.py` (i parser dei formati verificati su nodi reali — SMART ATA/NVMe, `corosync-cfgtool`, `pveperf`, nomi dei file — così una regressione fa fallire `scripts/controlla.sh`, non il tecnico dal cliente)
- I report generati (`*_inventory.md`, `*_report.md`, `*.json`) sono git-ignored: contengono dati di infrastrutture di clienti

---

## Storia precedente (dentro `DA-Proxmox-Docs`, `tool/audit-nodo.py`)

## 2026-09-04 (6)

### `audit-nodo.py`: due file separati (inventario e rilievi), nome e codice cliente nel nome dei file

Richiesta: "il report spezzato in due parti, una di inventario e una di rilevamenti; deve essere richiesto il nome del cliente e possibilmente il codice, per generare i file col nome corretto: codcli_nomecli_IP_inventory o report".

- **Due Markdown** al posto di uno: `<codcli>_<cliente>_<ip>_inventory.md` con cluster, nodi, hardware, storage, rete, performance, VM e container — solo dati, nessun giudizio — e `<codcli>_<cliente>_<ip>_report.md` con riepilogo esecutivo, legenda dei livelli, rilievi su cluster/nodi/hardware/storage/rete/performance, e una scheda per VM con i soli rilievi e il profilo assegnato. Separati perché hanno lettori diversi: l'inventario lo si allega a un'offerta o a un passaggio di consegne, i rilievi sono la lista di lavoro
- **Cliente**: `--cliente` e `--codice` da riga di comando, campi per host nel menu (salvati in `hosts.json`, insieme all'etichetta), oppure chiesti all'avvio se mancano e c'è un terminale. Senza terminale e senza argomenti si usa il nome del cluster
- `--output` ora è la **cartella** dei due file (default: quella corrente); i nomi li compone lo script. Nel nome l'indirizzo tiene i punti, il resto va a trattini; il codice si omette se non c'è

**Verificato** rianalizzando i JSON dei due cluster: `D001_Domarc-Srl_<ip>_inventory.md` (55 kB, zero rilievi dentro) e `_report.md` (38 kB); con un terminale, le domande "Nome cliente" e "Codice cliente" precedono la raccolta.

## 2026-09-04 (5)

### `audit-nodo.py` 2.0: tutto il cluster con una connessione, collector remoto, performance, report completo

Riscrittura dell'architettura, su richiesta: "dell'host viene riportato pochissimo così come delle VM", "verifica anche le caratteristiche del cluster e del corosync", "se il sistema fa parte di un cluster indaga tutti i nodi e tutte le vm", "deve essere eseguito da un client osx, linux o windows".

**Architettura nuova.** Prima: decine di comandi SSH separati, uno per dato, dal Mac. Ora: un **collector** Python (solo stdlib) viene inviato al nodo d'ingresso e gira **una volta**; interroga l'API locale (`pvesh … --output-format json`, che è la fonte più ricca e stabile) e pochi comandi di sola lettura, e restituisce un unico JSON. Se il nodo è in cluster, il collector ripete la raccolta sugli **altri nodi via l'SSH interno del cluster** (le chiavi che Proxmox distribuisce da sé per migrazioni e `pvecm`): dal client parte **una sola connessione**, una sola password. Nessun file resta sui nodi.

- Conseguenza per i **client Windows**: senza `ControlMaster` (che l'OpenSSH di Windows non ha) una connessione sola è l'unico modo per non chiedere la password a ogni comando. Aggiunti anche: console forzata a UTF-8 (i simboli 🔴🟡 su `cp1252` facevano crashare `print`), abilitazione dei colori ANSI via `SetConsoleMode`, niente `accept-new` dove l'OpenSSH è troppo vecchio per conoscerlo
- **Cosa si raccoglie in più** (tutto verificato sugli endpoint reali di PVE 9.2): stato nodo con boot/secure boot/KSM, sottoscrizione e repository, aggiornamenti pendenti e kernel installato vs in esecuzione, servizi, certificati, rrddata dell'ultima ora (CPU, iowait, PSI, swap, ARC), dischi con `wearout` dell'API + SMART, topologia di rete (`ip link`, `/proc/net/bonding`), corosync.conf + `pvecm status` + `corosync-cfgtool -s`, **ping fra gli anelli** di tutti i nodi, job di backup e guest scoperti, HA, replica, Ceph, SDN; per VM: config, stato runtime con `blockstat` (latenze di scrittura e flush dall'avvio), snapshot, modifiche pendenti, rrddata, e via guest agent SO, filesystem, IP e orologio; container LXC
- **Controlli nuovi**: quorum e voti, due nodi senza QDevice (bloccante), numero pari senza QDevice, anelli sulle stesse NIC fisiche o stessa subnet, latenza > 2/5 ms, perdita pacchetti, nodi online ma non raccolti; repository enterprise senza sottoscrizione (bloccante: il nodo non si aggiorna), riavvio pendente, certificati in scadenza, RAM > 90 %, swap > 25 %, overcommit vCPU e RAM, storage inattivo, ZFS > 80 % e `zpool status -x` (bloccante se c'è corruzione), bond degradato o **con un solo slave**; snapshot > 30 giorni, agent che non risponde, filesystem del guest > 90 %, orologio del guest, latenza flush > 20 ms, I/O falliti, machine 11.0 senza pve2 su Windows/Intel; container privilegiati
- **`--performance`**: `pveperf` su `/` e sui primi due storage locali (fsync/s con soglie indicative <200 / 200-1000 / >1000, DNS interno > 500 ms → attenzione). Opzionale perché scrive un file temporaneo per il test fsync; è l'unica cosa che non è pura lettura
- **Report Markdown** rifatto: riepilogo esecutivo con tabella per categoria e "da risolvere prima di tutto", cluster (nodi, anelli per nodo con NIC fisiche, latenze, HA, job di backup), una sezione per nodo (stato, storage, ZFS, LVM-thin, rete, bond, hardware con usura e ore, performance, pveperf), inventario VM a 22 colonne con nodo, tabella performance delle VM accese, scheda per VM (dischi, filesystem del guest, IP, rilievi), container, rilievi per categoria, dati non raccolti
- **UI**: colori (spenti con `--no-color` o senza tty), avanzamento nodo per nodo su stderr, `--breve`, `--json` per i dati grezzi e `--da-json` per rianalizzarli senza riconnettersi, `--solo-questo-nodo` per non estendere al cluster; nel menu voci "solo questo nodo" e "performance", e l'esito dell'ultimo audit accanto a ogni host
- I profili VM si salvano **per cluster** (`profili-<cluster>.json`), perché i VMID sono unici nel cluster e le VM migrano fra nodi

**Bug trovati provando su infrastruttura vera**, non a occhio: il guest agent restituisce `{"error": …}` invece di una lista quando un comando non è supportato (MikroTik CHR) e il parser lo prendeva per un elenco; `pveperf` su ZFS non misura letture e seek e mostravo 0 invece di "non misurato"; uno scarto di orologio di esattamente +2 h è l'agent che riporta l'ora locale, non un orologio rotto — ora è informativo; le interfacce `tap` dei guest finivano fra le "NIC fisiche" di un anello quando il bridge ha porte guest; senza `--host` lo script eseguiva il collector sul Mac invece di aprire il menu.

**Verificato**: cluster di prova, 4 nodi e 54 VM, con `--performance`, in 5 minuti e una sola connessione — tutti e quattro i nodi raccolti via SSH interno. Ha trovato due bloccanti veri: su un nodo lo slave `nic3` di `bond10` è DOWN e `zpool status` riporta corruzione dati sul pool `zfs`; più swap al 100 % su un nodo e DNS interno a 1 s su tutti i nodi. il nodo singolo singolo (20 VM, 1 CT) in 2 minuti. Menu e tabella interattiva provati con uno pseudo-terminale. Il terzo cluster (<terzo-cluster>) non è raggiungibile da questa rete.

## 2026-09-04 (4)

### `audit-nodo.py`: le classificazioni VM si salvano da sole, per host

Su richiesta esplicita: "i parametri della scansione devono essere salvati per ripeterli, non la password, ma la tipologia di VM e l'IP sì".

- **`~/.config/audit-nodo/profili-<host>.json`**, uno per host, scritto automaticamente dopo ogni scansione interattiva — nessun bisogno di ricordarsi `--salva-profili`. Ripetere una scansione sullo stesso host mostra subito, in tabella, le VM già classificate: si preme INVIO e si procede, senza rispondere di nuovo alle stesse domande
- **L'IP/host** era già salvato dal menu (`hosts.json`); resta l'unica altra cosa persistita
- **La password non è mai stata salvata, e resta così**: verificato che l'unico contenuto scritto su disco sia `{vmid: profilo}` — l'autenticazione passa sempre direttamente al processo `ssh`, mai per una variabile di questo script
- `--profili`/`--salva-profili` restano per un percorso esplicito quando serve (es. condividere un file tra colleghi); senza, si usa il percorso automatico
- Il menu riconosce da solo il file automatico: la voce "Vedi/modifica i profili VM già assegnati" ora compare anche senza aver configurato a mano il campo "File profili"

**Verificato con un vero pseudo-terminale su due giri consecutivi**: primo giro, classificazione di una VM e salvataggio automatico confermato a video; secondo giro sullo stesso host, messaggio "Profili già noti" e la VM già mostrata in tabella con il suo profilo, INVIO diretto ai controlli senza richieste aggiuntive.

## 2026-09-04 (3)

### `audit-nodo.py`: inventario completo in Markdown, hardware da Proxreporter, controlli VM estesi

Tre richieste, affrontate insieme perché si toccano: l'inventario in Markdown doveva includere anche l'hardware, e i controlli VM "il più possibile" dovevano coprire cose che l'audit non guardava ancora.

**Dati di acquisizione presi da Proxreporter** (il tool di reporting Domarc, 19.037 righe, già in produzione), su indicazione esplicita: solo la parte di **lettura** (`hardware_monitor.py`), niente di quanto in Proxreporter esiste per inviare allarmi, email o report a Domarc — questo script resta un lettore locale via SSH, non un agente.

- **SMART su tutti i dischi fisici**: salute complessiva, settori riallocati, settori pending, settori non correggibili, temperatura. Filtrati automaticamente gli `zd*` (zvol ZFS, i dischi virtuali delle VM: comparivano nello stesso elenco dei dischi fisici e `smartctl` su di loro non ha senso)
- **Parser SMART separato per NVMe**: il formato `smartctl -A` di un NVMe (chiave: valore — Critical Warning, Percentage Used, Media and Data Integrity Errors) non ha nulla in comune con la tabella di attributi numerati di un disco ATA/SATA che Proxreporter già interpreta. Un parser scritto solo per l'uno, sull'altro, non troverebbe nulla — e infatti non lo trovava, finché non ho verificato l'output reale di un NVMe
- **Memoria ECC** (sysfs, `edac-util` non è presente sui nodi reali verificati) e **RAID mdadm** (`/proc/mdstat`)

**Due bug trovati per caso mentre verificavo i risultati su infrastruttura reale, non nel codice a occhio:**

1. **Falso bloccante sistematico su ogni disco SATA/SAS**: il controllo di salute cercava la sottostringa `"FAILED" in output`, ma la tabella attributi ATA ha un'intestazione di colonna `WHEN_FAILED` che **contiene** letteralmente "FAILED" — su ogni disco, sano o no. Il codice originale di Proxreporter evita il problema con l'ordine if/elif (`PASSED` controllato per primo); nel portare la logica avevo invertito quell'ordine. Risultato: due dischi perfettamente sani (`smartctl -H` diceva PASSED) segnalati come guasto imminente. Corretto ancorando il controllo alla frase esatta di smartctl, non a una sottostringa
2. **Soglia di temperatura sbagliata per genere di disco**: le soglie 45°C/55°C di Proxreporter, tarate sul suo parco di dischi SATA, segnalavano bloccante un NVMe a 65°C — verificato che il firmware del disco stesso riportava `Critical Warning: 0x00` e non aveva mai superato la propria soglia critica. Un NVMe lavora normalmente più caldo di uno SATA. Corretto: su NVMe la temperatura resta solo informativa, il segnale autorevole è il bit Critical Warning (già controllato)

**Controlli VM estesi** (§1.1-1.4 della guida-carico, non ancora coperti):
- `scsihw` deve essere `virtio-scsi-single` quando la VM usa bus SCSI — **trovate 3 VM reali** con `virtio-scsi-pci` sul cluster di prova
- Bus SATA/IDE su un disco dati (non un lettore CD vuoto o cloudinit) segnalato, salvo intento dichiaratamente legacy
- **Multiqueue non più solo una nota di testo**: per i profili che lo richiedono su tutte le interfacce senza condizioni (Appliance di rete, Proxy), ora si verifica davvero che ogni interfaccia abbia `queues` impostato e pari alle vCPU

**Inventario in Markdown** (`--output`), sia dell'host che delle VM, non solo i rilievi: CPU, storage, pool ZFS, LVM-thin, dischi fisici, bond, corosync, orologio, firewall per il nodo; profilo, vCPU, CPU type, RAM, balloon, scsihw, numero dischi, rete (con eventuale multiqueue), agent, protection, onboot per ogni VM.

**Verificato su due cluster indipendenti dopo ogni correzione**, non solo sul primo che ha rivelato il problema.

## 2026-09-04 (2)

### `audit-nodo.py`: tabella di tutte le VM dopo la scansione, assegnazione manuale del profilo

Il ciclo precedente chiedeva il profilo VM per VM, senza vista d'insieme. Ora, da un terminale vero, dopo la scansione compare **prima una tabella con tutte le VM trovate** — VMID, nome, vCPU, RAM, disco, rete, e il profilo assegnato finora — e si sceglie un VMID per classificarlo (o correggerlo), `t` per farle tutte in sequenza, o INVIO per procedere. La tabella si aggiorna a ogni assegnazione, così si vede subito cosa manca.

L'uso non interattivo (`--profili`, da script o cron) resta invariato: nessun prompt si accende quando non c'è un terminale a guardarlo.

**Un bug vero, trovato per caso mentre verificavo la nuova colonna "Rete" della tabella**: `parse_reti()` non aveva mai valorizzato il campo `modello` da quando è stato scritto. Il formato reale di `netN` è `virtio=MAC,bridge=...` — il modello è la *chiave* del primo campo, non un valore isolato senza `=` come la funzione assumeva. Il controllo "scheda di rete non-VirtIO" in `controlla_generali()` non è **mai** scattato, su nessun cluster, da quando esiste. Corretto riconoscendo il primo campo dal fatto che il suo valore è un MAC address. Verificato subito dopo sul cluster di prova: **due VM Windows con `vmxnet3` e una con `e1000e`** — tre schede di rete non-VirtIO reali, mai segnalate finora.

**Verificato con un vero pseudo-terminale**, non solo a occhio sul codice: le prime prove con input simulato via pipe fallivano perché senza un terminale vero `sys.stdin.isatty()` restituisce `False`, e il ramo interattivo non si attivava affatto — poi, con uno pseudo-terminale reale (`pty`), un primo harness basato sui tempi di attesa ha inviato l'input troppo presto (durante la connessione SSH); il test finale attende il testo esatto del prompt prima di rispondere. Con quel test: tabella iniziale corretta, assegnazione di VM 107 a "Domain Controller" riflessa subito nella tabella, sezione "VM — profilo di carico" popolata nel resoconto finale, file `--salva-profili` scritto correttamente.

## 2026-09-04

### `audit-nodo.py`: default a root con password, e controlli estesi su storage/rete

Due richieste dopo un tentativo reale fallito (host senza `root@`, respinto in `BatchMode`).

**Connessione SSH ripensata:**
- **Default automatico a `root`** se l'indirizzo non specifica un utente — risolve esattamente il caso che era appena fallito, invece di limitarsi a spiegarlo meglio
- **Autenticazione a password come fallback**, non solo a chiave: la connessione iniziale non usa più `BatchMode=yes`, quindi SSH può chiedere la password sul terminale vero quando la chiave non basta
- **Una sola autenticazione per tutto l'audit**, non una per comando: la connessione iniziale è multiplexata (`ControlMaster`/`ControlPath`/`ControlPersist`), e le decine di comandi successivi (uno per VM, uno per disco, uno per storage...) la riusano senza richiedere nulla

**Un bug trovato durante la verifica**: il percorso del socket di controllo superava il limite di ~104 caratteri di un socket Unix su macOS (`tempfile.gettempdir()` è lunghissimo lì, e il token `%C` di ssh aggiunge un hash intero) — ssh falliva con *"too long for Unix domain socket"* invece di connettersi. Corretto con un percorso fisso e corto (`/tmp/audit-nodo-ssh/cm<pid>`).

**Controlli nuovi**, tutti verificati sul formato reale dei comandi prima di scrivere il parser (stessa disciplina delle volte scorse, dopo i tre bug di parsing già trovati):
- **LVM-thin**: il metadato del pool, che si esaurisce indipendentemente dallo spazio dati e rende il pool di sola lettura quando succede — un limite separato da sorvegliare separatamente (manuale Appendice A.8)
- **Orologio**: `System clock synchronized` — bloccante se `no`, coerente con quanto già scritto in `manuale/02-installare.md` su un disallineamento che rompe il cluster in modi difficili da leggere
- **Bond e firewall PVE**: riportati come informazione, non come rilievo — il manuale non prescrive una modalità di bond o uno stato del firewall giusti in assoluto, dipendono dal disegno di rete del cliente. Dove non c'è una soglia da citare, lo script non ne inventa una

**Il resoconto ora è organizzato per categoria** (Cluster/quorum, Storage, Rete, VM) invece che come elenco piatto: una tabella di riepilogo in cima, poi il dettaglio raggruppato. La categoria si deriva dall'ambito del rilievo in un solo punto (`categoria_di()`), non è un parametro in più da passare nei ~30 punti che generano un rilievo.

**Verificato su due cluster indipendenti** dopo ogni modifica: la connessione multiplexata confermata attiva a processo (`ps aux` mostra il master `ControlMaster` e i comandi figli che vi si agganciano via `ControlPath`); i nuovi controlli silenziosi su nodi sani, nessun falso positivo.

## 2026-09-03 (5)

### `audit-nodo.py`: menu interattivo con elenco host salvati

Su richiesta: uno script che chiede tutti i parametri a memoria (`--host`, `--solo-accese`, `--profili`...) non è comodo da usare tutti i giorni su clienti diversi. Aggiunto un menu, senza dipendenze in più:

- **Nessun argomento, da un terminale vero** apre il menu (`--menu` lo forza sempre, anche insieme ad altri flag — utile per gli script di test). Con qualunque flag esplicito il comportamento a riga di comando resta quello di sempre, invariato: niente si accende di sorpresa in uno script o in un cron
- **Elenco host**: indirizzo SSH, etichetta, e i parametri già associati (solo VM accese, solo nodo, file profili, output) in un colpo d'occhio — aggiungere, modificare, rimuovere
- **Menu parametri per host**: ogni valore si vede e si cambia con un numero; `s` salva, `a` lancia l'audit con quei parametri sul momento
- **Profili VM salvati**: se un host ha un file `--profili`, una voce in più li elenca (VM → tipologia) e permette di correggerne uno senza rifare l'intervista da capo
- **L'elenco vive solo in locale**, in `~/.config/audit-nodo/hosts.json` — mai nel repository, stesso principio delle credenziali clienti nel vault

**Verificato con un giro completo end-to-end**: creazione host da menu, modifica di un parametro, salvataggio, lancio reale via `a` contro il cluster di prova (rispettando `--solo-accese` salvato: 10 VM, non 13), fino all'uscita pulita.

**Un difetto di robustezza corretto durante quella stessa verifica**: un EOF imprevisto sullo stdin (terminale chiuso, Ctrl-D, o — nel test — input che finiva prima del previsto) mandava lo script in traceback invece di uscire con un messaggio. `EOFError`/`KeyboardInterrupt` ora si intercettano a livello di programma e producono un'uscita pulita.

## 2026-09-03 (4)

### `audit-nodo.py`: quattro profili in più, e un filtro per le VM spente

Su richiesta, dopo il primo uso reale: quattro tipologie di VM che il parco Domarc ha spesso e la guida-carico originale non copriva.

- **Replica** — VM di replica/DR: cita `manuale §4.5`, con l'avvertenza di non avere `onboot` insieme alla primaria (rischio di conflitto IP/hostname) e di verificare che la schedulazione `pvesr` esista davvero
- **Test / sviluppo** — CPU generica accettata, nessuna protezione richiesta, con l'avvertenza di verificare che non riparta da sola col nodo
- **Backup server (PBS o Veeam)** — cita `manuale §12`: storage con checksum per il datastore PBS, storage file-level per Veeam, e la domanda se il disco del repository debba restare fuori dal backup di se stesso
- **Proxy / reverse proxy** — variante del profilo application/web mirata sul multiqueue su tutte le interfacce

**`--solo-accese`**: salta le VM spente. Di default il comportamento non cambia (tutte le VM, come prima) — utile perché repliche e VM di test sono spesso ferme per definizione, e altrimenti affollano il report di rilievi su macchine che nessuno sta usando in quel momento.

Verificato di nuovo sul cluster di prova: `--solo-accese` passa da 13 a 10 VM controllate (le 3 spente escluse correttamente); i profili Replica e Proxy applicati a due VM reali producono rilievi coerenti.

## 2026-09-03 (3)

### `audit-nodo.py` — nuovo: la guida-carico diventa uno script di audit

Uno script Python (stdlib, nessuna dipendenza) che si collega **via SSH a un nodo Proxmox da un Mac** e confronta la configurazione rilevata con le soglie già documentate in `archivio/guida-configurazione-vm-proxmox-per-carico.md` — non re-inventa i controlli, legge la matrice a 9 profili della Parte 3 e i parametri della Parte 1 e ne fa un motore di regole.

- **Solo lettura**: `qm config`, `pvesm status`, `corosync-cfgtool -s`, `zpool list`, mai un comando che scrive
- **`--host utente@ip`**: gira dal Mac del tecnico, i comandi passano per SSH, non lascia nulla sul nodo del cliente — nato da un'osservazione dell'utente a metà lavoro, che ha cambiato l'architettura da "script da copiare sul nodo" a "strumento centrale in `sh()`, locale o remoto a seconda di un flag"
- **Chiede il profilo di carico VM per VM** (i 9 della guida: Domain Controller, File server, Application/web, Database, Appliance di rete, Log server/SIEM, Terminal/VDI, Monitoraggio, Legacy) e applica le regole non negoziabili di quel profilo, non solo i controlli generali
- **`--profili` / `--salva-profili`**: le risposte si salvano in JSON e si riusano al giro successivo, senza richiedere tutto da capo
- Severità **BLOCCANTE / attenzione / info**, stesso linguaggio del resto della documentazione; `--output` salva anche un report Markdown

**Verificato su due cluster reali**, non solo in locale: il cluster di prova (`<nodo-cluster>`, 13 VM) e un secondo ambiente indipendente (`<nodo-singolo>`, 20 VM) — inclusa una prova mirata: classificare forzatamente una VM non-database come "Database server" per confermare che scattano i 🔴 attesi (CPU type sconsigliata, ballooning non disattivato).

**Tre difetti di parsing trovati e corretti proprio grazie a quel test dal vivo — nessuno emerso dai controlli locali, perché serviva l'output reale di un nodo:**
1. La sonda di presenza (`qm --help`) usava una sintassi che la CLI di Proxmox non riconosce: esce con stato 255. La sintassi corretta è `qm help`
2. `pvesm status` ha 7 colonne (`Name Type Status Total Used Available %`), non 6: la percentuale letta era la colonna "Available (KiB)", producendo occupazioni di storage con svariati zeri in più
3. Il parsing di `corosync-cfgtool -s` assumeva un formato `link: N addr: X` su una riga sola; il formato reale è `LINK ID N` seguito da `addr = X` sulla riga successiva. La vecchia espressione non trovava mai nulla e segnalava sempre "0 anelli attivi", un falso allarme sistematico su ogni cluster sano

