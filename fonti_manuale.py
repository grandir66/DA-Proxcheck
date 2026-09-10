"""Le regole citate dall'audit, con il testo per esteso.

GENERATO da `strumenti/estrai-fonti.py` — non modificare a mano: le modifiche
si perdono alla prossima estrazione, e il testo non corrisponderebbe più al
manuale da cui viene.

Contiene SOLO i passaggi che un rilievo cita, presi dal manuale operativo
Proxmox VE di Domarc (DA-Proxmox-Docs). Serve a far bastare il report: chi
lo legge vede la regola, non un rimando a un documento che non ha.

I blocchi che il manuale marca come interni — valutazioni Domarc, casi di
clienti — non entrano qui: questo repository è pubblico. Lo verifica
`tests/test_fonti.py`, che nel testo estratto cerca quel marcatore.
"""

MANUALE = {
 "nome": "Manuale operativo Proxmox VE — Domarc",
 "versione": "1.0",
 "verificato": "2026-09-01",
 "repo": "DA-Proxmox-Docs",
 "estratto_il": "2026-09-10"
}

FONTI = {
 "§1.3": {
  "titolo": "§1.3 Le reti: la decisione che si paga di più",
  "file": "manuale/01-progettare.md",
  "riga": 36,
  "parte": "Parte 1",
  "testo": "Proxmox usa reti con funzioni diverse. **Farle convivere sullo stesso collegamento fisico è la causa più frequente di problemi gravi.**\n\n| Rete | Funzione | Priorità di separazione | Banda tipica |\n|---|---|---|---|\n| Management | GUI, SSH, API | Media | 1 Gb |\n| **Corosync** | Heartbeat tra i nodi | **Massima — rete dedicata** | 1 Gb basta |\n| Corosync secondario | Ring ridondante | Alta | 1 Gb |\n| Storage | iSCSI o NFS | Alta, se presente | 10 Gb+ |\n| **Ceph** [D] | Replica tra OSD | **Massima, e separata dallo storage** | 10 Gb, 25 Gb con NVMe |\n| Migrazione | Trasferimento della RAM tra nodi | Media | 10 Gb |\n| Backup | Traffico verso PBS | Alta | 10 Gb |\n| VM | Traffico dei guest, trunk VLAN | Alta | secondo il carico |\n\n**Perché corosync va separato.** Corosync considera inutilizzabile una rete non solo se cade, ma **se la latenza sale troppo**. Il requisito dichiarato è **sotto i 5 ms**; oltre i **10 ms** il comportamento diventa inaffidabile, tanto più quanto più nodi ci sono. Traffico di storage o di backup può saturare il link e superare la soglia: i nodi che perdono il quorum eseguono **self-fencing**, cioè si resettano. Visto da fuori sembra un cluster che si riavvia da solo.\n\nCorosync gestisce fino a **8 link** e commuta da solo: **due ring su percorsi fisici distinti** è la configurazione corretta.\n\n> Un ring corosync su un bond LACP **non** aggiunge affidabilità reale: il bond resta un unico dominio di guasto per configurazione e per switch. Due ring su interfacce separate proteggono molto di più.",
  "troncato": 1
 },
 "§1.5": {
  "titolo": "§1.5 Dimensionare CPU, RAM e dischi",
  "file": "manuale/01-progettare.md",
  "riga": 100,
  "parte": "Parte 1",
  "testo": "**CPU.** Si conta in core fisici, non in thread. L'overcommit di vCPU è normale e funziona fino a che la somma delle vCPU *attive contemporaneamente* resta vicina ai core disponibili. Il segnale di saturazione non è la percentuale di CPU: è il **pressure stall** e lo `steal time` dentro i guest.\n\n**RAM.** È la risorsa che non si può sovrassegnare senza pagarla. Il conto:\n\n```\nRAM totale = somma della RAM delle VM\n           + riserva per l'host          (4 GB, 8 GB su nodo carico)\n           + ARC di ZFS                  (vedi §4.2, default 10% con tetto 16 GB)\n           + 8 GiB per ogni OSD Ceph     [D]\n           + margine del 20%             per reggere la perdita di un nodo\n```\n\n**L'ultima riga è quella che si dimentica.** In un cluster a tre nodi che deve sopravvivere alla perdita di uno, le VM di tre nodi devono entrare in due: la RAM utile per nodo è due terzi di quella installata, non tutta.\n\n**Dischi.** Il sistema operativo va su una coppia in mirror, separata dai dati. Le regole che non cambiano:\n\n- **niente RAID hardware sotto ZFS o Ceph** — serve un HBA, o un controller in modalità IT. ZFS e Ceph devono parlare direttamente con i dischi;\n- **SSD enterprise con protezione dalla perdita di alimentazione** per tutto ciò che serve i guest: le SSD consumer collassano sulle scritture sincrone;\n- per i carichi VM, in ZFS, **mirror e RAID10** — RAIDZ solo se le prestazioni misurate bastano davvero.",
  "troncato": 0
 },
 "§1.6": {
  "titolo": "§1.6 Licenze, repository e supporto",
  "file": "manuale/01-progettare.md",
  "riga": 122,
  "parte": "Parte 1",
  "testo": "| Repository | Chi lo usa | Stabilità |\n|---|---|---|\n| `pve-enterprise` | Chi ha una sottoscrizione | Massima, consigliato in produzione |\n| `pve-no-subscription` | Senza sottoscrizione | Pacchetti più recenti, meno collaudati |\n| `pve-test` | Sviluppo | Non in produzione |\n\nTre cose che si scoprono tardi:\n\n1. **Tutti i nodi devono avere lo stesso livello di sottoscrizione.** Un cluster metà enterprise e metà no-subscription riceve versioni diverse degli stessi pacchetti, ed è una fonte di problemi difficili da diagnosticare.\n2. **La chiave di sottoscrizione è legata all'architettura.** Una chiave arm64 non è valida su x86, e viceversa.\n3. **Proxmox VE 9 usa il formato deb822** (file `.sources`, non più righe singole in `sources.list`). Su un sistema aggiornato da versioni precedenti, `apt modernize-sources` converte le voci vecchie.",
  "troncato": 0
 },
 "§10.2": {
  "titolo": "§10.2 LXC in pratica",
  "file": "manuale/10-container-template.md",
  "riga": 23,
  "parte": "Parte 10",
  "testo": "**Privilegiato o no.** Non privilegiato è il predefinito ed è la scelta giusta: l'utente `root` del container è mappato su un utente non privilegiato dell'host. Un container privilegiato è, in pratica, `root` sull'host — dalla 9.0 crearlo richiede il privilegio `Sys.Modify` proprio per questa ragione.\n\nIl prezzo del non privilegiato sono i **permessi sui mount point**: gli UID dentro e fuori non coincidono. Dalla 9.2 le opzioni `idmap` e `keepattrs` sui mount point risolvono i casi più comuni senza dover ricorrere ai container privilegiati.\n\n**Nesting** serve per systemd completo, per FUSE e per alcuni strumenti; va attivato consapevolmente, non per abitudine.\n\n**Il backup di un container** è coerente con `suspend` o `stop`; in modalità `snapshot` dipende dallo storage sottostante. Per i dati applicativi importanti dentro un container valgono le stesse considerazioni della §12.12: il dump applicativo viene prima.",
  "troncato": 0
 },
 "§11.2": {
  "titolo": "§11.2 Assessment: cosa raccogliere prima di toccare qualcosa",
  "file": "manuale/11-migrazione.md",
  "riga": 32,
  "parte": "Parte 11",
  "testo": "Nessuna migrazione parte senza questo inventario. Esportabile da vCenter con PowerCLI.\n\n| Dato | Perché serve |\n|---|---|\n| Nome, vCPU, RAM, dimensione e numero dischi | Dimensionamento e configurazione target |\n| **Firmware: BIOS o UEFI** | Determina SeaBIOS vs OVMF. **Se sbagliato la VM non trova il bootloader** |\n| **Controller disco attuale** (LSI Logic / PVSCSI / SATA) | Determina la strategia di switch a VirtIO |\n| Sistema operativo e versione | Disponibilità driver VirtIO |\n| **vTPM presente?** | Lo stato vTPM **non è migrabile** da VMware. Impatta BitLocker |\n| **BitLocker o crittografia full-disk?** | Serve sospendere/decrittare, o avere le chiavi di ripristino |\n| Configurazione di rete (IP, DNS, gateway, route, VLAN) | Il nome dell'adattatore cambierà |\n| MAC address | Per mantenere le reservation DHCP |\n| **Snapshot presenti** | Rallentano enormemente l'import: da consolidare prima |\n| Dischi RDM / independent / shared | **Non migrabili** con i metodi standard |\n| Disk su **vSAN** | **Non importabili**: spostare prima su altro datastore |\n| **Cifratura VM (storage policy)** | **Non importabile**: rimuovere la policy prima |\n| Licenze legate a hardware ID / dongle USB | Rischio di riattivazione o blocco |\n| Criticità, finestra di manutenzione, RTO/RPO | Pianificazione delle ondate |\n\n- Versione ESXi di ogni host e nome dei datastore (**caratteri speciali come `+` rompono l'import**)\n- Credenziali amministrative sugli host ESXi — **non solo su vCenter**, vedi §11.5.1\n- Banda disponibile tra ESXi e Proxmox\n\n---",
  "troncato": 0
 },
 "§11.5": {
  "titolo": "§11.5 Metodi di migrazione",
  "file": "manuale/11-migrazione.md",
  "riga": 142,
  "parte": "Parte 11",
  "testo": "| Metodo | Downtime | Quando usarlo |\n|---|---|---|\n| **Import wizard ESXi** | Medio (VM spenta durante la copia) | **Prima scelta** per la maggior parte delle VM |\n| **Import wizard + live-import** | Basso | VM grandi con servizi sensibili al downtime |\n| **Attach & Move disk** | Minimo | VM molto grandi con finestra strettissima. Più laborioso |\n| `qm importovf` (via `ovftool`) | Alto | Quando l'accesso diretto all'ESXi non è possibile |\n| `qm disk import` da share | Alto | Controllo fine sulla configurazione target |\n| **Restore da backup** (Veeam ecc.) | Variabile | Se esiste già un backup con integrazione Proxmox |\n| **Clonezilla** | Alto | Casi limite: sorgenti non VMware, dischi problematici |\n\n- **Puntare agli host ESXi, non a vCenter.** L'import via vCenter riduce le prestazioni in modo drammatico.\n- **La VM sorgente deve essere spenta** (anche con live-import).\n- **Consolidare o eliminare gli snapshot** sulla sorgente: la presenza di snapshot rallenta enormemente l'import.\n- Testato da ESXi 6.5 a 8.0.\n- Con certificati self-signed: aggiungere la CA al trust store o spuntare *Skip Certificate Verification*.\n- La scheda *Advanced* consente di scegliere storage diversi per ogni disco, modelli di NIC, e di **escludere dischi o CD-ROM** dall'import.\n\nLa VM viene avviata mentre l'import è ancora in corso: prima i dati necessari all'avvio, il resto in background. La sorgente resta comunque spenta, quindi **un downtime c'è**, solo più breve.",
  "troncato": 1
 },
 "§11.6": {
  "titolo": "§11.6 Preparazione della VM sorgente (PRE-migrazione)",
  "file": "manuale/11-migrazione.md",
  "riga": 188,
  "parte": "Parte 11",
  "testo": "**Questa è la sezione che determina se la migrazione riesce al primo colpo.** Va eseguita mentre la VM è ancora su VMware e funzionante.\n\n1. **Backup o snapshot della VM sorgente.** Mai migrare senza. È anche il piano di rollback.\n2. **Documentare la configurazione di rete**: IP, netmask, gateway, DNS, route statiche, VLAN, MAC address. L'adattatore cambierà e la configurazione andrà rifatta a mano.\n3. **Annotare il firmware**: BIOS legacy o UEFI. Da replicare esattamente sul target.\n4. **Rimuovere gli snapshot** sulla sorgente (consolidare).\n5. **Verificare l'accesso fuori banda**: password di root/Administrator note, console raggiungibile. **La rete quasi certamente non funzionerà al primo avvio.**\n6. **Crittografia full-disk con chiavi nel vTPM**: sospendere BitLocker o disabilitare la crittografia. Lo **stato vTPM non è migrabile da VMware**. Avere comunque le chiavi di ripristino a portata di mano.\n7. **Rimuovere i guest tools del vecchio hypervisor** — è molto più difficile farlo dopo.\n8. **DHCP reservation**: o si adatta la reservation al nuovo MAC, o si imposta manualmente il vecchio MAC sulla NIC target.\n9. **Spegnere la VM in modo pulito** (shutdown dal sistema operativo, non power off).\n\nL'ordine conta. Eseguire nella sequenza indicata.\n\n**Passo 1 — Rilevare il firmware.**\n\n```\nmsinfo32\n```\n\nLeggere *Modalità BIOS*: `UEFI` oppure `Legacy`. Annotare. Verificare anche lo stile partizione (GPT/MBR) in Gestione disco.\n\n**Passo 2 — Sospendere BitLocker** (se attivo), o annotare la chiave di ripristino:",
  "troncato": 1
 },
 "§11.7": {
  "titolo": "§11.7 Configurazione ottimale della VM target",
  "file": "manuale/11-migrazione.md",
  "riga": 321,
  "parte": "Parte 11",
  "testo": "Da applicare a ogni VM migrata. L'import wizard fa scelte ragionevoli ma non sempre ottimali.",
  "troncato": 1
 },
 "§12.1": {
  "titolo": "§12.1 Che cosa protegge un backup, e che cosa no",
  "file": "manuale/12-backup.md",
  "riga": 3,
  "parte": "Parte 12",
  "testo": "Un backup di macchina virtuale cattura **lo stato dei dischi in un istante**. Non cattura la coerenza transazionale di un database, non cattura la configurazione dell'host che la ospita, e non è una protezione contro il ransomware se chi cifra i dati può raggiungere anche i backup.\n\nLa strategia di backup **condiziona la scelta dello storage** ed è una decisione di progetto, non un'aggiunta finale.\n\n**Il riferimento è la regola 3-2-1-1-0:**\n\n| Cifra | Significato | Come si realizza |\n|---|---|---|\n| **3** copie | Produzione + due backup indipendenti | PBS locale + copia remota |\n| **2** supporti | Tecnologie diverse | Datastore su disco + oggetti S3, o nastro |\n| **1** fuori sede | Sopravvive all'incendio e all'allagamento | Sync job verso PBS remoto o S3 |\n| **1** immutabile | Sopravvive alle credenziali compromesse | **S3 Object Lock**, o nastro estratto |\n| **0** errori | Ripristino dimostrato, non supposto | Verify job + prova di ripristino |\n\n**L'ultima cifra è quella che manca quasi sempre.** Un backup mai verificato e mai ripristinato non è un backup: è una cartella grande.",
  "troncato": 0
 },
 "§12.6": {
  "titolo": "§12.6 Fleecing",
  "file": "manuale/12-backup.md",
  "riga": 89,
  "parte": "Parte 12",
  "testo": "Durante un backup live QEMU installa un filtro *copy-before-write*: prima che il guest sovrascriva un blocco non ancora salvato, il dato vecchio deve arrivare al target di backup, e **la scrittura del guest resta bloccata** nel frattempo.\n\nCon il **fleecing** il dato vecchio viene parcheggiato in un'immagine temporanea locale, e il guest non aspetta più il target.\n\n| | Senza fleecing | Con fleecing |\n|---|---|---|\n| Chi detta la latenza di scrittura del guest | Il **target di backup** | Lo **storage locale** |\n| Spazio aggiuntivo | Nessuno | Immagine temporanea |\n| Rischio di blocco della VM | Reale | Molto ridotto |\n\n**Da attivare su:** database, log server, application server con I/O sostenuto. **Dove:** storage **locale** con **discard** e thin provisioning o file sparsi. Meccanismo in §21.5.",
  "troncato": 0
 },
 "§12.7": {
  "titolo": "§12.7 Prune, garbage collection e verify",
  "file": "manuale/12-backup.md",
  "riga": 103,
  "parte": "Parte 12",
  "testo": "Sono tre operazioni diverse che vengono spesso confuse, e la confusione produce datastore che si riempiono senza che nessuno capisca perché.\n\n| Operazione | Che cosa fa | Libera spazio? |\n|---|---|---|\n| **Prune** | Rimuove i **riferimenti** agli snapshot da non conservare | **No** |\n| **Garbage collection** | Cancella i **chunk** che nessuno referenzia più | **Sì** |\n| **Verify** | Rilegge i chunk e ne confronta gli hash | No: dimostra che i dati sono integri |\n\n> ⚠️ **Il prune da solo non libera un byte.** Cancella l'indice, non i dati. Chi vede il datastore pieno dopo aver ridotto la retention sta aspettando una garbage collection che non è pianificata.\n\n`keep-last`, `keep-hourly`, `keep-daily`, `keep-weekly`, `keep-monthly`, `keep-yearly`. **Ogni opzione copre solo il proprio periodo e non tiene conto di quanto già conservato dalle altre**: si sommano, non si sovrappongono.\n\nRiferimento di partenza per la produzione: **7 giornalieri, 4 settimanali, 3 mensili**. Requisiti di conformità ne chiedono molti di più, e vanno chiesti al cliente prima di configurare (non dopo).\n\nLa GC lavora in due fasi: **marca** aggiornando l'`atime` di ogni chunk ancora referenziato, poi **spazza** cancellando i chunk il cui `atime` è più vecchio del taglio — **24 ore e 5 minuti** prima dell'inizio, o l'inizio del backup attivo più vecchio.\n\nQuella finestra apparentemente arbitraria dipende da `relatime`, il comportamento predefinito dei filesystem Linux, che aggiorna l'`atime` solo se è più vecchio di 24 ore. È un margine di sicurezza, non un ritardo da eliminare.",
  "troncato": 1
 },
 "§14.7": {
  "titolo": "§14.7 Passo passo — zona VLAN (lo scenario più frequente)",
  "file": "manuale/14-sdn.md",
  "riga": 99,
  "parte": "Parte 14",
  "testo": "Obiettivo: gestire dal datacenter le VLAN già esistenti sugli switch.\n\n**Prerequisito.** Su ogni nodo deve esistere un bridge **VLAN-aware** collegato alla rete fisica, per esempio `vmbr1` con *VLAN aware* spuntato.\n\n**Passo 1 — Creare la zona.**\n`Datacenter → SDN → Zones → Add → VLAN`:\n\n| Campo | Valore |\n|---|---|\n| ID | `prod` (max 8 caratteri) |\n| Bridge | `vmbr1` |\n| Nodes | I nodi su cui deve esistere |\n| MTU | Vuoto per ereditare dal bridge |\n\n**Passo 2 — Creare la VNet.**\n`VNets → Create`:\n\n| Campo | Valore |\n|---|---|\n| Name | `srv20` (max 8 caratteri) |\n| Alias | `Server produzione` |\n| Zone | `prod` |\n| Tag | `20` — il numero della VLAN |\n| VLAN Aware | Solo se le VM devono a loro volta taggare |\n\n**Passo 3 — Aggiungere la subnet** (facoltativo, serve per IPAM e DHCP).\nSelezionare la VNet → `Subnets → Create`: `172.16.20.0/24`, gateway `172.16.20.1`.\n\n**Passo 4 — Applicare.**\n`Datacenter → SDN → Apply`. **Finché non si preme Apply non cambia nulla**: le modifiche restano in stato *pending*.\n\n**Passo 5 — Usarla.** Nella scheda di rete di una VM, scegliere il bridge `srv20`. Il tag VLAN è già gestito dalla VNet.",
  "troncato": 0
 },
 "§15.2": {
  "titolo": "§15.2 Zone e direzioni",
  "file": "manuale/15-firewall.md",
  "riga": 11,
  "parte": "Parte 15",
  "testo": "**Tre zone**, cioè tre livelli a cui si scrivono regole:\n\n| Zona | Riguarda | Dove si configura |\n|---|---|---|\n| **Host** | Traffico da/verso un nodo, o instradato dal nodo | `Datacenter → Firewall` **e** `Nodo → Firewall`. Le regole del nodo **hanno la precedenza** |\n| **VM** | Traffico da/verso una VM o container | `VM → Firewall` |\n| **VNet** | Traffico che attraversa una VNet SDN | `Datacenter → SDN → VNet → Firewall` |\n\n**Tre direzioni:**\n\n| Direzione | Significato |\n|---|---|\n| **In** | Traffico che entra nella zona |\n| **Out** | Traffico che esce dalla zona |\n| **Forward** | Traffico che attraversa la zona: routing o NAT sull'host, oppure tutto ciò che transita per una VNet |\n\n> ⚠️ **Le regole Forward e quelle a livello VNet funzionano solo con il firewall basato su nftables** (`proxmox-firewall`). Con il firewall standard `pve-firewall` vengono **ignorate silenziosamente**, senza alcun errore. È la trappola più insidiosa di questa parte.",
  "troncato": 0
 },
 "§15.3": {
  "titolo": "§15.3 Il doppio interruttore",
  "file": "manuale/15-firewall.md",
  "riga": 31,
  "parte": "Parte 15",
  "testo": "Perché il firewall protegga davvero una VM servono **due abilitazioni distinte**:\n\n1. Il firewall **generale**, a livello datacenter o nodo\n2. Il flag **Firewall sulla singola scheda di rete** della VM\n\nManca uno dei due e non filtra nulla. È la causa numero uno delle segnalazioni \"il firewall non funziona\".",
  "troncato": 0
 },
 "§15.4": {
  "titolo": "§15.4 Passo passo — attivare il firewall senza chiudersi fuori",
  "file": "manuale/15-firewall.md",
  "riga": 40,
  "parte": "Parte 15",
  "testo": "Il firewall è **disattivato per default**. Attivarlo senza preparazione è il modo più rapido per perdere l'accesso al cluster.\n\n**Passo 0 — Aprire una sessione SSH verso un nodo e lasciarla aperta.** Se qualcosa va storto è l'unica via di rientro.\n\n**Passo 1 — Definire l'IPSet `management`.**\n`Datacenter → Firewall → IPSet → Create`, nome **`management`**. Aggiungere le reti da cui si amministra il cluster: la LAN dell'ufficio, la VPN, la subnet dei nodi.\n\nQuesto IPSet ha un significato speciale: con la policy in ingresso su DROP, restano comunque consentiti verso **gli host** i servizi di gestione **provenienti dagli indirizzi elencati qui**.\n\n**Passo 2 — Controllare le regole di default.** Con policy in ingresso o in uscita su DROP/REJECT restano sempre permessi:\n\n- il traffico sull'interfaccia di loopback\n- le connessioni già stabilite\n- il protocollo IGMP\n- dagli host di gestione: TCP 8006 (interfaccia web), 5900-5999 (console VNC), 3128 (proxy SPICE), 22 (SSH), 60000-60050 (migrazione)\n\n**Passo 3 — Attivare il firewall a livello datacenter.**\n`Datacenter → Firewall → Options → Firewall: Yes`.\nLasciare per ora `Input Policy: DROP` e `Output Policy: ACCEPT`.\n\n**Passo 4 — Verificare l'accesso** dall'interfaccia web e dalla sessione SSH già aperta, poi da una **nuova** sessione.\n\n**Passo 5 — Aggiungere le regole per gli altri servizi** che devono raggiungere i nodi: backup, monitoraggio, NTP, storage.\n\n**Passo 6 — Solo dopo**, abilitare il firewall sulle singole VM.",
  "troncato": 0
 },
 "§15.5": {
  "titolo": "§15.5 Le voci del menu, livello per livello",
  "file": "manuale/15-firewall.md",
  "riga": 68,
  "parte": "Parte 15",
  "testo": "| Voce | Contenuto |\n|---|---|\n| **Firewall** | Regole valide per **tutti** i nodi del cluster |\n| **Options** | Abilitazione generale, policy di input/output/forward, log |\n| **Security Group** | Gruppi di regole riutilizzabili |\n| **Alias** | Nomi simbolici per indirizzi e reti |\n| **IPSet** | Insiemi di indirizzi, incluso lo speciale `management` |\n\n| Voce | Contenuto |\n|---|---|\n| **Firewall** | Regole specifiche di quel nodo — **prevalgono** su quelle del datacenter |\n| **Options** | Policy del nodo, log, NDP, `nftables`, protezione SMURF, filtro TCP flags |\n| **Log** | Traffico registrato |\n\n| Voce | Contenuto |\n|---|---|\n| **Firewall** | Regole della VM |\n| **Options** | Abilitazione, policy, DHCP, NDP, Router Advertisement, IP Filter, MAC filter |\n| **Alias** e **IPSet** | Definizioni locali alla VM |\n| **Log** | Traffico registrato |",
  "troncato": 0
 },
 "§16.2": {
  "titolo": "§16.2 I tipi di target",
  "file": "manuale/16-notifiche.md",
  "riga": 21,
  "parte": "Parte 16",
  "testo": "| Target | Come recapita | Quando sceglierlo |\n|---|---|---|\n| **Sendmail** | Usa il binario `sendmail` del sistema, fornito da Postfix | Quando c'è già un MTA configurato sui nodi. **Ha una coda e ritenta** in caso di errore |\n| **SMTP** | Parla direttamente con un relay SMTP, senza passare dall'MTA locale | Il più semplice da configurare. ⚠️ **Nessuna coda e nessun ritentativo**: se la consegna fallisce, la notifica è persa |\n| **Gotify** | Server di notifiche self-hosted | Notifiche push senza email |\n| **Webhook** | Richiesta HTTP verso un URL configurabile | Integrazione con ntfy, Discord, Teams, Slack, o un sistema di ticketing |\n\n> **La differenza che conta tra Sendmail e SMTP** è la coda. Con SMTP, se il relay è irraggiungibile nel momento esatto in cui il backup fallisce, **nessuno viene avvisato del fallimento**. Su un sistema di allerta è un difetto serio. Sendmail via Postfix accoda e ritenta.",
  "troncato": 0
 },
 "§16.6": {
  "titolo": "§16.6 I matcher",
  "file": "manuale/16-notifiche.md",
  "riga": 108,
  "parte": "Parte 16",
  "testo": "| Regola | Cosa confronta | Esempi |\n|---|---|---|\n| `match-severity` | La gravità | `error` · `warning,error` |\n| `match-field` | I metadati | `exact:type=vzdump` · `exact:type=replication,fencing` · `regex:hostname=^pve\\d+$` |\n| `match-calendar` | L'orario di invio | `mon..fri 9:00-17:00` · `8-12` · `sun,tue..wed,fri 9-17` |\n\nOpzioni del matcher:\n\n| Opzione | Effetto |\n|---|---|\n| `mode: all` (default) | Devono corrispondere **tutte** le regole |\n| `mode: any` | Basta che ne corrisponda **una** |\n| `invert-match` | Inverte il risultato dell'intero matcher |\n| `target` | Il destinatario. Ripetibile per notificare più target |\n\nDue comportamenti da tenere a mente: **un matcher senza regole corrisponde sempre**, e **ogni target riceve al massimo una volta** la stessa notifica, anche se compare in più matcher.",
  "troncato": 0
 },
 "§16.9": {
  "titolo": "§16.9 Le notifiche dei backup",
  "file": "manuale/16-notifiche.md",
  "riga": 188,
  "parte": "Parte 16",
  "testo": "Ogni job di backup ha un'opzione `notification-mode` con due valori:\n\n| Modalità | Comportamento |\n|---|---|\n| `notification-system` | Usa il sistema di notifiche globale, con matcher e target. **È quella da usare** |\n| `legacy-sendmail` | Manda l'email direttamente all'indirizzo scritto nel job, **ignorando ogni matcher e target**. Comportamento delle versioni precedenti alla 8.1 |\n\n> La modalità legacy **potrebbe essere rimossa** in una release futura. Se sul cluster ci sono job che la usano ancora, vanno convertiti — altrimenti un giorno gli avvisi di backup smettono di arrivare senza che nessuno cambi nulla.",
  "troncato": 0
 },
 "§18.1": {
  "titolo": "§18.1 Le sette misure che valgono più di tutte le altre",
  "file": "manuale/18-sicurezza.md",
  "riga": 5,
  "parte": "Parte 18",
  "testo": "| # | Misura | Costo | Dove |\n|---|---|---|---|\n| 1 | **Il management non è raggiungibile da Internet né dalla rete degli utenti** | Nessuno, se progettato prima | §1.3 |\n| 2 | **Utenti nominali, niente `root` condiviso** | Basso | §13.7 |\n| 3 | **Secondo fattore su tutti gli amministratori** | Basso | §13.8 |\n| 4 | **Backup fuori dalla portata delle credenziali quotidiane** | Medio | §12.8 |\n| 5 | **Aggiornamenti applicati, con una cadenza dichiarata** | Medio | §19.1 |\n| 6 | **Firewall attivo con INPUT in DROP sul datacenter** | Medio, con rischio di lockout | §15.4 |\n| 7 | **Log inoltrati fuori dal nodo** | Basso | §17.5 |\n\nLe prime cinque non hanno controindicazioni. Le ultime due vanno provate prima in laboratorio.",
  "troncato": 0
 },
 "§19.1": {
  "titolo": "§19.1 Il ciclo di aggiornamento",
  "file": "manuale/19-esercizio.md",
  "riga": 3,
  "parte": "Parte 19",
  "testo": "**Un hypervisor non aggiornato è il problema più comune che si trova nei parchi esistenti**, e la ragione è quasi sempre la stessa: nessuno ha mai deciso *quando* si aggiorna, quindi non si aggiorna mai.\n\nLa decisione da prendere in fase di progetto, e da scrivere:\n\n| Cosa | Cadenza consigliata | Riavvio |\n|---|---|---|\n| Patch di sicurezza Debian | Automatiche, senza riavvio (§18.4) | No |\n| Aggiornamenti Proxmox | **Mensile o trimestrale**, in finestra pianificata | Solo se kernel o microcodice |\n| Versione maggiore (9 → 10) | Entro 6-12 mesi dall'uscita, mai il primo mese | Sì |\n| Firmware di server e array | Annuale, o quando risolve un problema noto | Sì |\n\n**Il riavvio serve solo per kernel e microcodice.** Tutto il resto si applica a caldo. Ma un nodo che non si riavvia da un anno è un nodo che sta usando un kernel vecchio di un anno, e che nessuno ha mai visto ripartire: il primo riavvio dopo un guasto è il momento peggiore per scoprire che non riparte.\n\n```bash\napt update && apt full-upgrade\npveversion -v            # cosa gira davvero adesso\n```",
  "troncato": 0
 },
 "§19.3": {
  "titolo": "§19.3 Aggiornare un cluster senza fermare le VM",
  "file": "manuale/19-esercizio.md",
  "riga": 33,
  "parte": "Parte 19",
  "testo": "**Un nodo alla volta, verificando ognuno prima di passare al successivo.** Aggiornare tutto insieme è il modo documentato di ritrovarsi con un cluster rotto e nessun nodo funzionante da cui capire cosa è successo.\n\n```bash\n# ── PRIMA: solo se c'è Ceph ────────────────────────────────\nceph osd set noout\nceph osd set norebalance\nceph osd set norecover\nceph osd set noscrub\nceph osd set nodeep-scrub\n\n# ── PER OGNI NODO, uno alla volta ──────────────────────────\nha-manager crm-command node-maintenance enable <nodo>\n# attendere che i guest si siano spostati: ha-manager status\n\napt update && apt full-upgrade\nreboot                                   # se serve\n\n# a nodo tornato: verificare PRIMA di procedere\npvecm status                             # quorato, entrambi i link\npvesm status                             # tutti gli storage online\nceph -s                                  # se presente: nessun OSD down inatteso\n\nha-manager crm-command node-maintenance disable <nodo>\n\n# ── DOPO l'ultimo nodo, se c'è Ceph ────────────────────────\nceph osd unset norecover                 # per primo: lascia recuperare\n# attendere HEALTH_OK, poi:\nceph osd unset noout\nceph osd unset norebalance\nceph osd unset noscrub\nceph osd unset nodeep-scrub\n```\n\n**Modalità di manutenzione o riavvio semplice?** Sono due comportamenti diversi ed è la confusione più frequente:\n\n| | Cosa succede ai guest |\n|---|---|\n| `node-maintenance enable` | Vengono **migrati** su altri nodi e riportati indietro alla fine |\n| `reboot` con HA attiva | Vengono **congelati** e ripartono sullo stesso nodo dopo il riavvio |",
  "troncato": 1
 },
 "§2.1": {
  "titolo": "§2.1 Prima di avviare l'installazione",
  "file": "manuale/02-installare.md",
  "riga": 3,
  "parte": "Parte 2",
  "testo": "Le cose che vanno sistemate nel firmware, perché dopo costano un riavvio o una reinstallazione.\n\n| Impostazione | Valore | Perché |\n|---|---|---|\n| **Modalità di boot** | UEFI | Secure Boot è supportato; il BIOS legacy limita le opzioni di avvio e ZFS su dischi grandi |\n| **Virtualizzazione** | Attiva (VT-x / AMD-V) | Senza, le VM non partono |\n| **IOMMU** | Attivo (VT-d / AMD-Vi) | Serve al passthrough PCI, anche se non è previsto oggi |\n| **Controller dischi** | **HBA o modalità IT** | Obbligatorio per ZFS e Ceph: devono vedere i dischi |\n| **Hyper-Threading / SMT** | Attivo | Salvo requisito di sicurezza esplicito |\n| **Profilo energetico** | Prestazioni | I profili di risparmio introducono latenza sui carichi a raffica |\n| **Watchdog** | Attivo, se disponibile | L'HA lo usa per il self-fencing (§7.3) |\n| **IPMI / iDRAC / iLO** | Configurato, su VLAN separata | È l'unico accesso quando il nodo non risponde |\n\n**L'orologio.** Un disallineamento tra i nodi rompe il cluster in modi difficili da leggere. NTP va verificato prima di creare il cluster, non dopo.",
  "troncato": 0
 },
 "§2.5": {
  "titolo": "§2.5 Primo assetto del nodo",
  "file": "manuale/02-installare.md",
  "riga": 102,
  "parte": "Parte 2",
  "testo": "Da eseguire su **ogni** nodo, prima di creare il cluster.\n\n```bash\n# 1. Repository: enterprise con sottoscrizione, altrimenti no-subscription.\n#    Tutti i nodi devono avere lo STESSO livello.\napt update && apt full-upgrade\n\n# 2. Microcodice del processore (richiede il repository non-free-firmware)\napt install intel-microcode      # oppure amd64-microcode\ngrep microcode /proc/cpuinfo | uniq\n\n# 3. Strumenti diagnostici (smartmontools e jq ci sono già)\napt install btop ncdu iotop iftop lm-sensors\n\n# 4. Ora coerente: un drift dell'orologio rompe il cluster\ntimedatectl status\n\n# 5. Risoluzione dei nomi: ogni nodo deve risolvere tutti gli altri\ncat /etc/hosts\n```\n\n**Se si usa ZFS**, limitare l'ARC: compete con la RAM delle VM. Il valore predefinito dalla 8.1 è il 10% della RAM con tetto a 16 GiB, che su un nodo con molte VM è già ragionevole ma va comunque messo per iscritto (§4.2).\n\n```ini\n# /etc/modprobe.d/zfs.conf\noptions zfs zfs_arc_max=8589934592     # 8 GiB, da tarare\n```\n\n```bash\nupdate-initramfs -u -k all\n```\n\n**Congelare i nomi delle interfacce.** Un aggiornamento del kernel può rinominare le schede di rete e lasciare il nodo isolato. Due modi:\n\n```bash\n# opzione A: fissare lo schema di denominazione\necho \"net.naming-scheme=v252\" >> /etc/kernel/cmdline\nproxmox-boot-tool refresh\n\n# opzione B: fissare i singoli nomi (aggiorna anche interfaces, firewall e SDN)\npve-network-interface-pinning generate\n```",
  "troncato": 1
 },
 "§2.6": {
  "titolo": "§2.6 La rete del nodo",
  "file": "manuale/02-installare.md",
  "riga": 149,
  "parte": "Parte 2",
  "testo": "La configurazione sta in `/etc/network/interfaces`. Le modifiche fatte dalla GUI finiscono prima in **`/etc/network/interfaces.new`**: finché non si preme *Apply Configuration* non sono attive. Da riga di comando:\n\n```bash\nifreload -a          # applica senza riavviare (ifupdown2)\n```\n\n**Le convenzioni di nome** non sono facoltative: bridge `vmbr0`…`vmbr4094`, bond `bond0`, VLAN `eno1.50`.\n\n**Bond.** Il modo va scelto insieme a chi gestisce gli switch:\n\n| Modo | Richiede sullo switch | Uso |\n|---|---|---|\n| `active-backup` | Niente | Il più sicuro quando gli switch non sono configurabili o non sono in stack |\n| `802.3ad` (LACP) | Configurazione LACP, switch in stack per la ridondanza | Aggrega banda; **non** usarlo per corosync |\n| `balance-alb` | Niente | Sconsigliato: interagisce male con i bridge |\n\n**MTU.** Se si alzano i jumbo frame per lo storage, vanno alzati **su tutto il percorso** — schede, bond, bridge, switch, array. Un solo tratto a 1500 produce prestazioni peggiori del punto di partenza e problemi intermittenti difficili da attribuire. Se si usa VXLAN, l'incapsulamento costa 50 byte (§14).",
  "troncato": 0
 },
 "§2.7": {
  "titolo": "§2.7 Certificati",
  "file": "manuale/02-installare.md",
  "riga": 169,
  "parte": "Parte 2",
  "testo": "Il certificato autofirmato che l'installer genera è sufficiente a far funzionare tutto, ma produce l'avviso del browser a ogni accesso — e abitua le persone a ignorare gli avvisi dei certificati, che è il vero danno.\n\n```bash\n# registrazione dell'account ACME (una volta per cluster)\npvenode acme account register default sistemi@example.com\n\n# per ogni nodo: aggiungere il dominio e ordinare\npvenode acme cert order\n```\n\n- **HTTP-01** richiede che il nodo sia raggiungibile dall'esterno sulla porta 80: quasi mai il caso per una rete di management.\n- **DNS-01** funziona anche su reti interne, e supporta i caratteri jolly. Va configurato un plugin DNS in *Datacenter → ACME*. È la scelta giusta nella maggior parte delle installazioni.\n\nSe l'azienda ha una CA interna, il certificato si carica in *Node → System → Certificates*, oppure:\n\n```bash\npvenode cert set --certificates /root/fullchain.pem --key /root/privkey.pem\n```",
  "troncato": 0
 },
 "§20.2": {
  "titolo": "§20.2 Le firme dei guasti",
  "file": "manuale/20-guasti.md",
  "riga": 21,
  "parte": "Parte 20",
  "testo": "| Sintomo | Causa più probabile | Verifica |\n|---|---|---|\n| Nodi che si riavviano \"da soli\" | **Self-fencing**: corosync sopra soglia di latenza | `journalctl -u corosync`, e verificare che corosync abbia rete propria |\n| `/etc/pve` in sola lettura, VM accese ma immodificabili | Quorum perso | `pvecm status` |\n| Un nodo grigio nella GUI ma raggiungibile in SSH | Corosync isolato, management ancora attivo | `corosync-cfgtool -s` |\n| **Errori su tutti gli storage, anche i locali** | Un mount fuse bloccato — tipicamente uno storage di import ESXi rimasto configurato | `pvesm status`, rimuovere lo storage di import |\n| Tutte le VM lente insieme | Storage saturo di IOPS, o riequilibrio Ceph in corso | `iostat -x`, `ceph -s` |\n| Una VM lenta, le altre no | Overcommit di vCPU, ballooning, o disco su storage sbagliato | `qm status <id> --verbose`, steal time nel guest |\n| VM che non parte dopo un riavvio | Storage non montato, o disco su un nodo diverso | `pvesm status`, `qm config <id>` |\n| Backup che rallenta tutta la produzione | Manca il fleecing, o il backup passa sulla rete di corosync | §12.6, §1.3 |\n| Backup che fallisce sempre alla stessa VM | Guest agent che non risponde al freeze | `qm agent <id> ping` |\n| Datastore PBS pieno nonostante il prune | **Garbage collection mai eseguita** | §12.7.2 |\n| VM che non migra | Storage non condiviso, o dispositivo in passthrough | `qm config <id>`, flag `shared` |\n| Rete della VM muta dopo una modifica al firewall | Il doppio interruttore, o regole Forward con `pve-firewall` | §15.3, §15.8 |\n| Il nodo non riparte dopo la sostituzione di un disco di boot | `proxmox-boot-tool` non eseguito sul disco nuovo | §19.5 |\n| Spazio che non torna mai dopo aver cancellato dati | Catena UNMAP interrotta | §4.6 |",
  "troncato": 1
 },
 "§3": {
  "titolo": "Parte 3 — Cluster e quorum",
  "file": "manuale/03-cluster.md",
  "riga": 1,
  "parte": "Parte 3",
  "testo": "Le sezioni di questa parte del manuale:\n\n- **§3.1 Che cosa fa davvero il cluster**\n- **§3.2 Creare il cluster e aggiungere nodi**\n- **§3.3 Link ridondanti: come si verificano**\n- **§3.4 Quando si perde il quorum**\n- **§3.5 Due nodi: il QDevice**\n- **§3.6 Oltre i tre nodi**\n- **§3.7 Rimuovere un nodo, reinserirlo, ripartire senza quorum**",
  "troncato": 0
 },
 "§3.3": {
  "titolo": "§3.3 Link ridondanti: come si verificano",
  "file": "manuale/03-cluster.md",
  "riga": 35,
  "parte": "Parte 3",
  "testo": "Configurare due link non serve se non si controlla che siano davvero due percorsi.\n\n```bash\ncorosync-cfgtool -s      # deve mostrare entrambi i ring, stato OK\npvecm status             # Quorate: Yes, e il numero di voti atteso\n```\n\n**La prova che conta** è staccare fisicamente il primo link e verificare che il cluster resti quorato. Va fatta in fase di collaudo, non durante il primo guasto reale.",
  "troncato": 0
 },
 "§3.4": {
  "titolo": "§3.4 Quando si perde il quorum",
  "file": "manuale/03-cluster.md",
  "riga": 46,
  "parte": "Parte 3",
  "testo": "| Sintomo | Causa tipica |\n|---|---|\n| `/etc/pve` in sola lettura, VM accese ma immodificabili | Quorum perso, il nodo è in minoranza |\n| Nodi che si riavviano da soli, apparentemente a caso | **Self-fencing**: HA attiva e latenza corosync oltre soglia |\n| Un nodo \"grigio\" nella GUI ma raggiungibile via SSH | Corosync isolato, rete di management ancora viva |\n\nIl secondo caso è il più frequente e il più frainteso: non è un problema hardware, è **corosync che condivide il collegamento con storage o backup** (§1.3).",
  "troncato": 0
 },
 "§3.5": {
  "titolo": "§3.5 Due nodi: il QDevice",
  "file": "manuale/03-cluster.md",
  "riga": 56,
  "parte": "Parte 3",
  "testo": "Il QDevice è un servizio esterno che fornisce un voto. Non ospita VM, non tocca lo storage, non conosce le configurazioni: dice solo \"io ci sono\", e la sua presenza rompe la parità.\n\n```bash\n# sul dispositivo esterno (NAS, VM altrove, piccolo sistema dedicato)\napt install corosync-qnetd\n\n# su ogni nodo del cluster\napt install corosync-qdevice\n\n# da un nodo qualsiasi\npvecm qdevice setup <ip-del-qdevice>\npvecm status          # devono comparire 3 voti attesi\n```\n\n**Dove metterlo.** Non su uno dei due nodi — sarebbe inutile. Non su una VM ospitata dal cluster stesso, per la stessa ragione. Va bene un NAS, un piccolo sistema fisico, o una VM su un'infrastruttura diversa. Deve raggiungere entrambi i nodi in **UDP 5405**.\n\n**Cosa succede se cade il QDevice.** Il cluster resta a due voti su due: continua a funzionare finché entrambi i nodi sono vivi, ma torna a essere vulnerabile alla parità. Il QDevice va quindi monitorato come un componente dell'infrastruttura, non trattato come un accessorio.\n\n> ⚠️ **Il QDevice è pensato per cluster con numero PARI di nodi.** L'algoritmo predefinito `ffsplit` presuppone la parità. Su un cluster con numero dispari Proxmox rifiuta l'aggiunta e richiede `--force`, passando automaticamente all'algoritmo `lms` (last-man-standing), che dà al QDevice un peso diverso. *Comportamento verificato sul forum Proxmox il 2026-09-01; la documentazione ufficiale non lo descrive nel dettaglio.*\n\nPer rimuoverlo:\n\n```bash\npvecm qdevice remove\n```\n\n**Il QDevice va rimosso prima di togliere un nodo dal cluster** (§3.7).",
  "troncato": 0
 },
 "§3.7": {
  "titolo": "§3.7 Rimuovere un nodo, reinserirlo, ripartire senza quorum",
  "file": "manuale/03-cluster.md",
  "riga": 99,
  "parte": "Parte 3",
  "testo": "L'ordine è vincolante e il terzo passo è quello che si salta.\n\n```bash\n# 1. migrare via tutte le VM e i container, rimuovere i job di replica\n# 2. rimuovere il QDevice, se presente\npvecm qdevice remove\n# 3. distruggere gli OSD e i servizi Ceph sul nodo, se presente   [D]\n# 4. SPEGNERE il nodo, e assicurarsi che non si riaccenda com'è\n# 5. da un altro nodo\npvecm delnode <nome-nodo>\n```\n\n> ⚠️ **Il nodo va spento prima della rimozione, e non deve riaccendersi con la sua configurazione attuale.** Un nodo rimosso che torna online crede ancora di far parte del cluster: è la ricetta dello split-brain.\n\n**Un nodo rimosso non si reinserisce così com'è.** Va reinstallato da zero. Riusare il vecchio nome è possibile, ma solo dopo aver ripulito i residui sugli altri nodi.\n\nSituazione: due nodi su tre sono morti e il terzo è in sola lettura. Serve rimettere in servizio quello che resta.\n\n```bash\npvecm expected 1        # abbassa i voti attesi: il nodo torna scrivibile\n```\n\n> ⚠️ **È una forzatura, non una riparazione.** Va usata solo quando si è *certi* che gli altri nodi siano spenti. Se uno di essi è vivo e isolato, si è appena creato uno split-brain con due cluster che credono di essere quello buono. Quando i nodi rientrano, il valore torna automaticamente corretto.\n\nOperazione sconsigliata ma a volte necessaria: separare un nodo dal cluster senza reinstallarlo.",
  "troncato": 1
 },
 "§4.1": {
  "titolo": "§4.1 Come Proxmox vede lo storage",
  "file": "manuale/04-storage.md",
  "riga": 5,
  "parte": "Parte 4",
  "testo": "Uno storage in Proxmox è definito da tre attributi che decidono tutto il comportamento:\n\n- **il tipo** — determina cosa sa fare;\n- **il contenuto** (`content`) — cosa gli è permesso ospitare: `images` (dischi delle VM), `rootdir` (container), `iso`, `vztmpl`, `backup`, `snippets`, `import`;\n- **la condivisione** (`shared`) — se tutti i nodi vedono lo *stesso* contenuto.\n\n> ⚠️ **Il flag `shared` non rende condiviso uno storage: dichiara che lo è.** Marcare come condiviso uno storage locale presente su ogni nodo con lo stesso nome è un errore che si paga alla prima migrazione, quando Proxmox non copia il disco perché crede che sia già dall'altra parte.\n\n| Tipo | Condiviso | Livello | Snapshot | Nota decisiva |\n|---|---|---|---|---|\n| **Directory** | No | File | Solo con qcow2 | Semplice, va bene per ISO e backup |\n| **LVM** | Possibile | Blocco | ⚠️ volume-chain, **tech preview**, solo VM | Parte 5 |\n| **LVM-thin** | No | Blocco | ✅ | Ottimo su nodo singolo, non condivisibile |\n| **ZFS locale** | No | Entrambi | ✅ nativi | §4.2 |\n| **NFS** | ✅ | File | Solo con qcow2 | §4.3 |\n| **CIFS/SMB** | ✅ | File | Solo con qcow2 | Preferire NFS dove c'è scelta |\n| **iSCSI** | ✅ | Blocco | Tramite LVM sopra | Parte 5 |\n| **Ceph RBD** | ✅ | Blocco | ✅ nativi | Parte 6 |\n| **CephFS** | ✅ | File | ✅ | Utile per ISO e template condivisi |\n| **PBS** | ✅ | — | — | Solo `backup` |\n| **BTRFS** | No | File | ✅ | **Tech preview**: non in produzione |\n\n**GlusterFS non esiste più**: il supporto è stato rimosso in Proxmox VE 9.",
  "troncato": 1
 },
 "§4.2": {
  "titolo": "§4.2 ZFS locale",
  "file": "manuale/04-storage.md",
  "riga": 35,
  "parte": "Parte 4",
  "testo": "Tre decisioni non si correggono senza distruggere e ricreare il pool:\n\n| Decisione | Regola | Se sbagliata |\n|---|---|---|\n| **Livello di RAID** | **mirror o RAID10** per i carichi VM. RAIDZ solo se le prestazioni misurate bastano. dRAID da 10-15 dischi in su | Prestazioni casuali insufficienti, e non si converte |\n| **`ashift`** | 12 (4 KB) di default; mai *sotto* il settore fisico reale del disco | Amplificazione della scrittura per tutta la vita del pool |\n| **Special device** | Deve avere **la stessa ridondanza del pool** | È un punto singolo di guasto **dell'intero pool**, e non si può togliere |\n\n**Perché mirror e non RAIDZ.** Un vdev RAIDZ ha le IOPS casuali di *un solo disco*, per quanti dischi contenga. Su un fileserver che scrive sequenzialmente non si nota; su un database o su venti VM che leggono contemporaneamente sì, e molto.\n\nZFS usa la RAM libera come cache di lettura. Su un hypervisor questa RAM la vogliono le VM.\n\n**Dalla 8.1 il valore predefinito è il 10% della memoria con tetto a 16 GiB** — prima era il 50%, ed è la ragione per cui le guide più vecchie insistono tanto su questo punto. Il default attuale è ragionevole, ma su un nodo molto carico va comunque fissato esplicitamente:\n\n```ini\n# /etc/modprobe.d/zfs.conf\noptions zfs zfs_arc_max=8589934592     # 8 GiB\n```\n\n```bash\nupdate-initramfs -u -k all      # e riavvio, se la root è su ZFS\n```\n\nSu sistemi con più di 256 GiB di RAM conviene fissare anche `zfs_arc_min`.\n\n```bash\nzfs set compression=lz4 <pool>        # praticamente gratis, quasi sempre conveniente\n```",
  "troncato": 1
 },
 "§4.5": {
  "titolo": "§4.5 Replica ZFS tra nodi",
  "file": "manuale/04-storage.md",
  "riga": 123,
  "parte": "Parte 4",
  "testo": "È il modo di avere alta affidabilità senza storage condiviso: ZFS copia periodicamente le differenze del dataset su un altro nodo.\n\n```bash\npvesr create-local-job 100-0 pve2 --schedule \"*/5\"    # ogni 5 minuti\npvesr status\npvesr list\n```\n\n| Caratteristica | Valore |\n|---|---|\n| Storage supportato | **Solo ZFS locale.** Nient'altro |\n| Intervallo | Da 1 minuto a 1 settimana |\n| Ritentativo dopo un errore | Ogni 30 minuti |\n| Destinazioni per job | **Una sola** |\n| RPO | Uguale all'intervallo configurato |\n\n**Che cosa succede davvero quando il nodo di origine muore.** I dati sono sull'altro nodo, aggiornati all'ultima replica riuscita. Se la VM è gestita in HA, riparte automaticamente **perdendo il lavoro dall'ultima replica**. Se non lo è, serve spostare a mano la configurazione e avviarla.\n\n> ⚠️ **La replica non è alta affidabilità sincrona, ed è la confusione più costosa che si possa fare in fase di vendita.** Con intervallo di 5 minuti si possono perdere 5 minuti di transazioni. Su un fileserver è spesso accettabile; su un gestionale o un database non lo è, e va detto prima, per iscritto, con il numero dentro.\n\n**Su cosa non usarla:** database transazionali, code di messaggi, qualunque cosa dove la coerenza di cinque minuti fa più danno di un fermo di venti.",
  "troncato": 0
 },
 "§4.6": {
  "titolo": "§4.6 Lo spazio: dove si finisce pieni",
  "file": "manuale/04-storage.md",
  "riga": 147,
  "parte": "Parte 4",
  "testo": "Il thin provisioning permette di assegnare ai guest più spazio di quello che esiste. Funziona finché nessuno lo usa davvero.\n\n**La catena che porta al riempimento**, in ordine:\n\n1. i dischi dei guest sono thin, e crescono man mano che vengono scritti;\n2. **i dati cancellati dentro il guest non liberano spazio** sullo storage, a meno che il TRIM/UNMAP arrivi fino in fondo;\n3. gli snapshot trattengono i blocchi vecchi e nessuno li rimuove;\n4. i backup locali si accumulano sullo stesso volume.\n\n**Perché la catena UNMAP funzioni servono tre cose insieme:**\n\n| Anello | Cosa serve |\n|---|---|\n| Guest | `discard`/TRIM attivo — su Linux `fstrim.timer`, su Windows è il comportamento predefinito |\n| Disco virtuale | Opzione **Discard** attiva e bus **SCSI** con controller VirtIO SCSI |\n| Storage | Deve supportare la riallocazione: SSD, LVM-thin, ZFS, Ceph, SAN con SCSI UNMAP |\n\nSe manca un anello, lo spazio non torna mai. Su LVM condiviso è il calcolo della §1.4.1.\n\n**Wipe Removed Volumes** (`saferemove`), dalla 9.1, usa `blkdiscard` e quindi SCSI UNMAP: su una SAN che lo supporta, cancellare un disco restituisce davvero lo spazio all'array.",
  "troncato": 0
 },
 "§6.2": {
  "titolo": "§6.2 I minimi veri",
  "file": "manuale/06-ceph.md",
  "riga": 21,
  "parte": "Parte 6",
  "testo": "| Risorsa | Minimo | Consigliato | Nota |\n|---|---|---|---|\n| Nodi | 3 | **5** | Server il più possibile identici |\n| Monitor (MON) | 3 | 3 | Oltre i 3 non servono su cluster piccoli e medi |\n| Manager (MGR) | 1 | 2+ | Il secondo dà ridondanza alla gestione |\n| OSD totali | — | **12**, distribuiti in modo uniforme | Meno OSD = ricostruzioni più lente e più rischiose |\n| **CPU** | 1 core per servizio Ceph | 4-6 thread per OSD **NVMe** | Un nodo con 1 MON + 1 MGR + 6 OSD ne prenota circa 8 |\n| **RAM** | 4 GiB per OSD | **8 GiB per OSD** | Il margine serve durante il riequilibrio, non a regime |\n| Rete cluster | 10 Gb dedicati | **25 Gb** con NVMe | Separata dalla rete pubblica Ceph |\n| Rete pubblica Ceph | 10 Gb | 10 Gb+ | Il traffico verso i client |\n| Corosync | 1 Gb | 1 Gb **separato** | Mai insieme a Ceph |\n\n**Sui dischi le regole sono due e non hanno eccezioni:** niente RAID hardware — servono HBA, perché Ceph gestisce da sé la ridondanza e un controller in mezzo gli nasconde la realtà — e dimensioni e numero di OSD **bilanciati tra i nodi**, perché la distribuzione dei dati segue la capacità.\n\n**Su cluster piccoli usare SSD**: la ricostruzione dopo un guasto è la finestra di rischio, e sui dischi rotanti dura ore.",
  "troncato": 0
 },
 "§6.4": {
  "titolo": "§6.4 Monitor, manager, OSD",
  "file": "manuale/06-ceph.md",
  "riga": 51,
  "parte": "Parte 6",
  "testo": "```bash\n# su tre nodi diversi\npveceph mon create\npveceph mgr create\n\n# per ogni disco dedicato a Ceph\npveceph osd create /dev/sdX\n\n# con DB e WAL su un dispositivo veloce separato\npveceph osd create /dev/sdX -db_dev /dev/nvme0n1\n```\n\n**I MON vanno su tre nodi diversi**, altrimenti non c'è quorum di monitor. È un quorum indipendente da quello di corosync, e va tenuto distinto in testa: si può perdere il quorum Ceph mantenendo quello Proxmox, e viceversa.\n\n**Cifratura degli OSD**: la casella *Encryption* alla creazione. Va decisa ora, non si aggiunge dopo.\n\n```bash\nceph -s                 # lo stato di riferimento: deve essere HEALTH_OK\nceph osd tree           # come sono distribuiti gli OSD\nceph df                 # spazio reale e per pool\n```",
  "troncato": 0
 },
 "§6.5": {
  "titolo": "§6.5 Pool e placement group",
  "file": "manuale/06-ceph.md",
  "riga": 75,
  "parte": "Parte 6",
  "testo": "```bash\npveceph pool create <nome> --add_storages\n```\n\n**I valori predefiniti sono quelli giusti: 128 PG, `size 3`, `min_size 2`.**\n\n| Parametro | Significato |\n|---|---|\n| `size 3` | Ogni oggetto esiste in 3 copie |\n| `min_size 2` | Con meno di 2 copie disponibili, il pool smette di accettare I/O |\n\n> ⚠️ **Non impostare mai `min_size 1`.** Permette la scrittura quando esiste una sola copia: basta che quel disco muoia prima della replica e il dato non esiste più da nessuna parte. È il modo documentato di perdere dati con Ceph.\n\n**Il PG autoscaler** regola da solo il numero di placement group. Va lasciato attivo, e gli si dà un aiuto dichiarando la dimensione attesa del pool (*Target Size* o *Target Ratio*) quando è nota: evita un riequilibrio completo quando il pool cresce.\n\n**Erasure coding** (`--erasure-coding k=2,m=1`) recupera capacità a scapito di prestazioni e complessità. Su cluster piccoli non conviene. Il flag `allow_ec_optimizations` **è irreversibile**: per tornare indietro si ricrea il pool.\n\n**CephFS** serve quando si vuole uno storage condiviso a file — ISO, template, snippet — senza aggiungere un NAS:\n\n```bash\npveceph fs create --pg_num 32 --add-storage\n```",
  "troncato": 0
 },
 "§6.6": {
  "titolo": "§6.6 Manutenzione",
  "file": "manuale/06-ceph.md",
  "riga": 100,
  "parte": "Parte 6",
  "testo": "Quando un OSD sparisce, Ceph inizia a ricopiare i dati altrove. Per un riavvio di dieci minuti è lavoro inutile e rischioso: si sospende.\n\n```bash\nceph osd set noout\nceph osd set norebalance\nceph osd set norecover\nceph osd set noscrub\nceph osd set nodeep-scrub\n\n# ... manutenzione e riavvio del nodo, un nodo alla volta ...\n\nceph osd unset norecover        # per primo: lascia recuperare\n# poi, verificato che torna HEALTH_OK\nceph osd unset noout\nceph osd unset norebalance\nceph osd unset noscrub\nceph osd unset nodeep-scrub\n```\n\nLa procedura completa di aggiornamento di un cluster con Ceph è nella §19.3.\n\n```bash\nceph osd out <id>               # 1. escludilo dalla distribuzione\nceph -s                         # 2. ASPETTA che il riequilibrio finisca\nceph osd ok-to-stop <id>        # 3. conferma che si può fermare\npveceph stop --service osd.<id>\npveceph osd destroy <id> --cleanup\n```\n\nIl passo 2 è quello che si salta: fermare l'OSD prima che il riequilibrio sia completo riduce le copie disponibili proprio mentre il cluster è già degradato.\n\nDalla 9.2 **Ceph Tentacle (20.2)** è il default per le installazioni nuove; un cluster esistente **non passa da solo** — Proxmox allinea i nodi nuovi alla release già in uso dal cluster, non li forza avanti.\n\nIl percorso da Reef è **vincolato**, e va letto due volte prima di iniziare:",
  "troncato": 1
 },
 "§7.1": {
  "titolo": "§7.1 Che cosa promette l'HA, e che cosa no",
  "file": "manuale/07-ha.md",
  "riga": 3,
  "parte": "Parte 7",
  "testo": "**L'HA di Proxmox riavvia le macchine virtuali su un altro nodo.** Non le sposta accese, non conserva la memoria, non evita l'interruzione. Una VM su un nodo che muore viene riaccesa altrove: per il sistema operativo dentro è stato un blackout, con tutto quello che comporta — filesystem da controllare, transazioni interrotte, sessioni cadute.\n\n| Serve | Strumento |\n|---|---|\n| Il servizio torna da solo dopo un guasto hardware, in qualche minuto | **HA** |\n| Il servizio non si interrompe mai | Ridondanza **dentro** il guest: due DC, cluster di database, due bilanciatori |\n| Spostare una VM senza interruzione per manutenzione | **Migrazione a caldo**, che è una funzione diversa e non richiede HA |\n\n**La distinzione va messa per iscritto nell'offerta.** \"Alta affidabilità\" letta da un cliente significa \"non si ferma mai\"; quello che si consegna è \"riparte da sola in tre minuti\". Sono due prodotti diversi.",
  "troncato": 0
 },
 "§7.2": {
  "titolo": "§7.2 Requisiti",
  "file": "manuale/07-ha.md",
  "riga": 15,
  "parte": "Parte 7",
  "testo": "| Requisito | Perché |\n|---|---|\n| **Almeno 3 nodi** | Serve un quorum affidabile. Con 2 nodi + QDevice si può, con le riserve della §7.8 |\n| **Storage condiviso, oppure replica ZFS** | Il nodo che riavvia la VM deve poterne leggere il disco |\n| **Watchdog funzionante** | Senza fencing l'HA non può ripartire in sicurezza |\n| Rete corosync dedicata | Un falso positivo di corosync diventa un riavvio di nodi (§1.3) |\n| Hardware ridondato | L'HA copre il guasto del nodo, non quello dell'unico switch |",
  "troncato": 0
 },
 "§7.4": {
  "titolo": "§7.4 Risorse, stati e tentativi",
  "file": "manuale/07-ha.md",
  "riga": 44,
  "parte": "Parte 7",
  "testo": "```bash\nha-manager add vm:100 --state started\nha-manager status\nha-manager set vm:100 --state stopped\nha-manager remove vm:100\n```\n\nLa configurazione sta in `/etc/pve/ha/resources.cfg`, replicata su tutti i nodi.\n\n| Stato | Significato |\n|---|---|\n| `started` | Deve essere in esecuzione: se cade, l'HA la riavvia |\n| `stopped` | Deve restare ferma: l'HA la tiene ferma |\n| `disabled` | Ferma e ignorata |\n| `ignored` | L'HA non se ne occupa, ma la risorsa resta in elenco |\n| `error` | Tutti i tentativi sono falliti: richiede intervento |\n\n**Due parametri, entrambi con valore predefinito 1:**\n\n- `max_restart` — quante volte riprovare ad avviare la risorsa **sullo stesso nodo**;\n- `max_relocate` — quante volte provare a spostarla **su un altro nodo**.\n\n**Uscire dallo stato `error` richiede un passaggio esplicito**, ed è progettato così perché nessuno riavvii in ciclo una VM rotta:\n\n```bash\nha-manager set vm:100 --state disabled     # 1. disabilita\n# 2. ripara la causa\nha-manager set vm:100 --state started      # 3. riabilita\n```",
  "troncato": 0
 },
 "§7.5": {
  "titolo": "§7.5 Le regole HA",
  "file": "manuale/07-ha.md",
  "riga": 76,
  "parte": "Parte 7",
  "testo": "Dalla versione 9.0 i **gruppi HA sono sostituiti dalle regole**, che vivono in `/etc/pve/ha/rules.cfg`. Chi arriva da configurazioni più vecchie trova i gruppi migrati automaticamente, ma la logica da usare da qui in avanti è questa.\n\n**Regole di affinità con i nodi** — dove una risorsa può o deve girare:\n\n```bash\nha-manager rules add node-affinity solo-nodi-licenziati \\\n  --resources vm:100,vm:101 --nodes pve1,pve2 --strict 1\n```\n\n- `--strict 1`: la risorsa gira **solo** su quei nodi. Se non sono disponibili, resta ferma.\n- `--strict 0` (predefinito): sono una preferenza; in emergenza la risorsa va altrove.\n\nI nodi possono avere priorità diverse, e la risorsa torna sul nodo preferito quando ridiventa disponibile.\n\n**Regole di affinità tra risorse** — quali VM devono stare insieme e quali separate:\n\n```bash\n# tenerle insieme: applicativo e suo database\nha-manager rules add resource-affinity app-e-db \\\n  --resources vm:200,vm:201 --affinity positive\n\n# tenerle separate: i due domain controller\nha-manager rules add resource-affinity dc-separati \\\n  --resources vm:10,vm:11 --affinity negative\n```\n\n**L'affinità negativa è quella che salva davvero.** Due domain controller, due nodi di un cluster applicativo o due bilanciatori che finiscono sullo stesso nodo annullano la ridondanza che il cliente ha pagato — e nessuno se ne accorge finché quel nodo non muore.\n\n**L'affinità positiva ha un caso d'uso preciso:** VM che comunicano tanto tra loro, o vincoli di licenza che legano un software a un insieme di socket fisici.",
  "troncato": 0
 },
 "§8.3": {
  "titolo": "§8.3 Disco",
  "file": "manuale/08-parametri-vm.md",
  "riga": 102,
  "parte": "Parte 8",
  "testo": "**`VirtIO SCSI single` + bus `SCSI`** è la configurazione di riferimento: un controller per disco, presupposto per gli **IO thread**.\n\n`VirtIO Block` è più vecchio, valido ma senza le funzionalità SCSI. `SATA`/`IDE` solo per compatibilità o fase transitoria di migrazione.\n\nDelega l'I/O di quel disco a un thread dedicato invece che al thread principale di QEMU. **Da attivare praticamente sempre** con `VirtIO SCSI single`, e obbligatorio per `aio=native`.\n\n| Modalità | Page cache host | Sicurezza al crash dell'host | Uso |\n|---|---|---|---|\n| **`none`** (*No cache*) | Bypassata | ✅ Sicura | **Default consigliato**; obbligatoria per `aio=native` |\n| `writeback` | Usata in scrittura | ⚠️ Perdita dati senza UPS/BBU | Solo con alimentazione protetta e consapevolezza |\n| `writethrough` | Usata in lettura | ✅ Sicura | Scritture lente, raramente utile |\n| `directsync` | Bypassata, write-through | ✅ La più sicura | Durabilità massima |\n| `unsafe` | Ignora i flush del guest | ❌ **Pericolosa** | Solo installazioni usa-e-getta |\n\nCon `none` il guest riceve la conferma quando il blocco raggiunge la coda di scrittura dello storage fisico, ignorando la page cache dell'host.\n\n| Valore | Quando |\n|---|---|\n| **`io_uring`** (default) | **File-based (qcow2, NFS, directory), ZFS, LVM-thin, sopra RAID software.** Con `native` qui l'I/O **può bloccarsi** |\n| `native` | **Solo** blocco raw non bufferizzato con `cache=none` **e IO thread attivo** |\n| `threads` | Fallback |",
  "troncato": 1
 },
 "§8.4": {
  "titolo": "§8.4 Rete",
  "file": "manuale/08-parametri-vm.md",
  "riga": 153,
  "parte": "Parte 8",
  "testo": "**VirtIO (paravirtualized)** salvo assenza di driver. `e1000`/`rtl8139` solo per sistemi legacy.\n\nPermette al guest di elaborare pacchetti su più vCPU. Va impostato **pari al numero di vCPU**, ma la documentazione raccomanda di attivarlo **solo su VM con molte connessioni in ingresso**: router, reverse proxy, server HTTP molto trafficati. Altrove aumenta soltanto il carico CPU.\n\n> ⚠️ **Non basta impostarlo lato Proxmox: va abilitato anche dentro il guest.**\n>\n> Linux: `ethtool -L ens18 combined <numero_vCPU>`\n>\n> Windows: Gestione dispositivi → scheda di rete → Proprietà → Avanzate → **Receive Side Scaling** su *Enabled*, poi **Maximum number of RSS Queues** pari al numero di vCPU.\n\n> ⚠️ **Su tutte le interfacce, non solo sulla prima.**\n\n> ⚠️ **Le code attive sono limitate dal numero di vCPU:** `queues` e `cores` vanno alzati insieme.\n\nMeccanismo completo e caso misurato in §21.3.\n\n| Parametro | Note |\n|---|---|\n| **MTU** | Solo su VirtIO. Vuoto o `mtu=1` eredita dal bridge. Coerente **su tutto il percorso**, guest incluso |\n| **Firewall** | Il firewall per-VM inserisce bridge aggiuntivi (`fwbr`). Su VM ad alto traffico, se il filtraggio avviene altrove, conviene disattivarlo |\n| **Rate limit** | Tetto di banda per interfaccia |\n| **VLAN tag** | Preferibile al tagging dentro il guest, salvo appliance che gestiscono trunk |",
  "troncato": 0
 },
 "§8.5": {
  "titolo": "§8.5 Sistema",
  "file": "manuale/08-parametri-vm.md",
  "riga": 182,
  "parte": "Parte 8",
  "testo": "| Parametro | Raccomandazione |\n|---|---|\n| **BIOS** | `OVMF (UEFI)` per SO moderni; `SeaBIOS` per legacy. Con OVMF serve l'**EFI Disk** |\n| **Machine** | `q35` per SO moderni; `i440fx` per legacy. **≥ 10** se lo storage usa `snapshot-as-volume-chain`; **`11.0+pve2`** per Windows recenti su host Intel con VBS |\n| **QEMU Guest Agent** | Attivare sempre: IP e RAM nella GUI, spegnimento pulito, **freeze del filesystem nei backup** |\n| `agent: freeze-fs` | Default attivo. Controlla freeze/thaw durante backup, clonazioni, replication e snapshot |\n| `agent: fstrim_cloned_disks` | TRIM dopo clonazione e migrazione: recupera spazio su storage thin |\n| **vmgenid** | Assegnato automaticamente dalla 5.2. **Non rimuoverlo** — vedi §9.1 e §21.6 |\n| **VGA** | `std` di default; `qxl` solo con SPICE |\n| `tablet` | Attivo di default. Disattivabile su molte VM solo-console per risparmiare context switch |\n| `rng0` | Entropia dall'host. Utile su VM con molta crittografia |",
  "troncato": 0
 },
 "§8.6": {
  "titolo": "§8.6 Ciclo di vita",
  "file": "manuale/08-parametri-vm.md",
  "riga": 196,
  "parte": "Parte 8",
  "testo": "| Parametro | Uso |\n|---|---|\n| `onboot` | Avvio automatico all'accensione del nodo |\n| `startup: order=N,up=X,down=Y` | Ordine di avvio (lo spegnimento segue l'ordine inverso) con ritardo in secondi |\n| `protection` | Impedisce cancellazione di VM e dischi. **Attivare su tutte le VM critiche** |\n| `tags` / pool | Organizzazione, filtri, permessi |\n| HA + regole di affinità | Da PVE 9: affinità e anti-affinità per nodo e per risorsa |\n\n| Ordine | Categoria | `up` |\n|---|---|---|\n| 1 | Domain controller, DNS, appliance di rete | 60 |\n| 2 | Database | 120 |\n| 3 | Application server | 60 |\n| 4 | Web, frontend, reverse proxy | 30 |\n| 9 | Test, sviluppo | 0 |\n\n---",
  "troncato": 0
 },
 "§9.1": {
  "titolo": "§9.1 Domain Controller (Active Directory)",
  "file": "manuale/09-profili-carico.md",
  "riga": 6,
  "parte": "Parte 9",
  "testo": "VM piccola, regole più stringenti dell'intero parco: qui gli errori causano **corruzione del dominio**, non lentezza.\n\n| Parametro | Valore |\n|---|---|\n| vCPU | 2 (4 su domini grandi) — 1 socket |\n| RAM | 4–8 GB, **fissa**: *Minimum memory* = *Memory* |\n| CPU type | `x86-64-v2-AES` — la migrabilità conta più delle prestazioni |\n| NUMA | No |\n| Disco | Uno, 60–100 GB, SCSI + VirtIO SCSI single, `iothread=1`, `cache=none`, `discard=on` |\n| Rete | VirtIO, **multiqueue non necessario** |\n| Guest agent | Attivo, `freeze-fs=1` |\n| `onboot` | Sì, `startup: order=1,up=60` |\n| `protection` | **Attiva** |\n| HA | Sì, con **anti-affinità** rispetto agli altri DC |\n\n1. **Almeno due domain controller, su nodi fisici diversi.** Nessuna configurazione della VM sostituisce la ridondanza applicativa di AD.\n2. **Non usare il rollback di snapshot come strategia di ripristino.** Provoca **USN rollback**: la replica del dominio si corrompe in silenzio (§21.6).\n3. **vmgenid è la rete di protezione, non la soluzione.** Proxmox lo assegna dalla 5.2 e lo cambia al rollback; Windows Server 2012+ lo rileva e attiva le contromisure Microsoft. Richiede: Windows ≥ 2012, vmgenid presente, backup coerenti con guest agent e VSS funzionante.\n4. **Per il ripristino usare gli strumenti nativi Windows**, consapevoli dell'USN rollback.\n5. **Sincronizzazione oraria:** senza VMware Tools non c'è più il sync con l'hypervisor. Configurare `w32time` con il PDC emulator come sorgente autorevole — un drift rompe Kerberos.",
  "troncato": 0
 },
 "§9.2": {
  "titolo": "§9.2 File server",
  "file": "manuale/09-profili-carico.md",
  "riga": 31,
  "parte": "Parte 9",
  "testo": "| Parametro | Valore |\n|---|---|\n| vCPU | 2–4 |\n| RAM | Generosa (8–32 GB) |\n| Ballooning | **Attivo**, con *Minimum memory* alto |\n| Shares | Alto |\n| CPU type | `x86-64-v2-AES` |\n| Disco SO | 60–80 GB, SCSI, `iothread=1`, `cache=none`, `discard=on` |\n| **Disco dati** | Separato, `iothread=1`, `cache=none`, `discard=on`, `ssd=1` se flash |\n| Rete | VirtIO, multiqueue solo con molti client concorrenti |\n| Guest agent | Attivo con `freeze-fs=1` |\n| `protection` | Attiva |\n\n- **Separare sempre il disco dati dal disco di sistema**: politiche di backup e throttling distinte, espansione più semplice.\n- `backup=0` su dischi dati enormi **solo** con backup applicativo verificato. La decisione va scritta.\n- Con backing ZFS, `lz4` lavora bene; evitare la doppia compressione host + guest.\n- Il `discard` è particolarmente importante: un file server cancella e riscrive continuamente.\n- Se non deve essere membro di dominio, un container LXC consuma molto meno.",
  "troncato": 0
 },
 "§9.3": {
  "titolo": "§9.3 Application server e web server",
  "file": "manuale/09-profili-carico.md",
  "riga": 52,
  "parte": "Parte 9",
  "testo": "| Parametro | Valore |\n|---|---|\n| vCPU | 2–8 |\n| RAM | Secondo l'applicazione |\n| Ballooning | **Attivo** — salvo Java con heap fissa |\n| CPU type | `x86-64-v2-AES`, o `host` se TLS terminato qui |\n| `cpuunits` | Alzare sulla produzione se convive con test |\n| Disco | 40–80 GB, SCSI, `iothread=1`, `cache=none`, `discard=on` |\n| Rete | VirtIO. **Multiqueue = vCPU** se reverse proxy o molte connessioni |\n| Guest agent | Attivo |\n| `startup` | `order=3` |\n\n- È il caso d'uso **esplicitamente citato dalla documentazione** per il multiqueue. Ricordare la configurazione lato guest.\n- **Java**: memoria fissa e ballooning disattivato. La JVM non restituisce memoria, e il balloon che tenta di riprenderla porta a swap e comportamenti erratici.\n- Preferire **più VM piccole a una grande**: si scala meglio, si aggiorna a rotazione, si sfrutta l'anti-affinità.",
  "troncato": 0
 },
 "§9.4": {
  "titolo": "§9.4 Database server",
  "file": "manuale/09-profili-carico.md",
  "riga": 70,
  "parte": "Parte 9",
  "testo": "Il profilo più esigente e quello dove i default fanno più danni.\n\n| Parametro | Valore |\n|---|---|\n| vCPU | Dimensionate, **senza overcommit** (1:1) |\n| RAM | **Fissa**. Ballooning **disabilitato davvero** (`balloon: 0`) |\n| CPU type | `host` se cluster omogeneo, altrimenti `x86-64-v3` |\n| **NUMA** | **Attivo**, socket = nodi NUMA dell'host |\n| Hugepages | Da valutare con misura |\n| **Disco SO** | Separato |\n| **Disco dati** | Separato, `iothread=1`, **`cache=none`**, `discard=on`, `ssd=1` |\n| **Disco log/WAL/redo** | Separato dai dati, stessi parametri |\n| **Disco temp/tempdb** | Separato, valutare `backup=0` |\n| AIO | `native` **solo** se blocco raw + `cache=none` + iothread; altrimenti `io_uring` |\n| Rete | VirtIO, multiqueue di norma non necessario |\n| Guest agent | Attivo, `freeze-fs=1` |\n| `startup` | `order=2,up=120` |\n| `protection` | Attiva |",
  "troncato": 1
 },
 "§9.5": {
  "titolo": "§9.5 Appliance di rete e firewall virtuali",
  "file": "manuale/09-profili-carico.md",
  "riga": 100,
  "parte": "Parte 9",
  "testo": "Qui l'errore tipico non è nella CPU o nel disco: è nella rete, ed è caro.\n\n| Parametro | Valore |\n|---|---|\n| vCPU | Secondo il tier di licenza dell'appliance — **non oltre** |\n| RAM | **Fissa**, ballooning disabilitato (`balloon: 0`) |\n| CPU type | `host` (AES-NI e istruzioni crittografiche) |\n| Disco | Piccolo, SCSI, `iothread=1`, `cache=none` |\n| **Rete** | VirtIO, **multiqueue su TUTTE le interfacce** |\n| **Firewall PVE** | **Disattivato** sulle interfacce |\n| MTU | Coerente end-to-end |\n| `startup` | `order=1` |\n| `protection` | Attiva |\n\n1. **Multiqueue su ogni interfaccia.** Caso misurato: firewall con `queues=4` solo su `net0` e coda singola sulle gambe VLAN → **3,3 Gb/s in routing contro 34,4 Gb/s in L2** sullo stesso fabric, con QEMU al 254–300% di CPU su 2 vCPU. Non un limite di banda: la saturazione di un singolo percorso di elaborazione.\n2. **Le code attive sono limitate dalle vCPU.** Nello stesso caso, l'interfaccia con `queues=4` ne attivava 2 — esattamente il numero di vCPU. **`queues` e `cores` insieme.**\n3. **Verificare il tier di licenza** prima di aggiungere risorse: molti appliance hanno limiti di vCPU e RAM definiti dal modello, dichiarati nel datasheet e non applicati dal file di licenza. Superarli non dà prestazioni ed esce dal supporto.\n\n**Inoltre:** le prestazioni dichiarate dai vendor sono quasi sempre misurate con **SR-IOV o passthrough**, non con VirtIO su bridge Linux. Gran parte del divario tra datasheet e realtà si spiega così. Se servono davvero quei numeri, la strada è PCI passthrough o SR-IOV, al prezzo della migrazione live.",
  "troncato": 0
 },
 "§9.6": {
  "titolo": "§9.6 Log server, SIEM, piattaforme di raccolta eventi",
  "file": "manuale/09-profili-carico.md",
  "riga": 124,
  "parte": "Parte 9",
  "testo": "| Parametro | Valore |\n|---|---|\n| vCPU | 4–8 |\n| RAM | Alta. **Fissa, ballooning disabilitato** se c'è una JVM |\n| CPU type | `x86-64-v2-AES` |\n| Disco SO | Separato, piccolo |\n| **Disco dati** | Grande e separato, `iothread=1`, `cache=none`, `discard=on` |\n| **Throttling** | `mbps_wr` + `mbps_wr_max` con burst |\n| Rete | VirtIO, multiqueue se riceve da centinaia di sorgenti |\n| Guest agent | Attivo |\n| `startup` | `order=4` |\n\n- **È il vicino rumoroso per eccellenza.** Il throttling con burst lo contiene senza degradarne il funzionamento.\n- **La compressione conviene molto**: i log comprimono spesso 5–10×. Con backing ZFS, `zstd` sul dataset dedicato è tra gli interventi con il miglior rapporto beneficio/sforzo.\n- **Backup**: spesso ha senso `backup=0` sul disco dati con ridondanza applicativa, altrimenti si fa backup di terabyte già ridondati. Da documentare.\n- **Definire la retention prima** della messa in produzione, non a storage pieno.\n- JVM: heap **non oltre** circa metà della RAM della VM — il resto serve alla page cache del sistema operativo.",
  "troncato": 0
 },
 "§9.7": {
  "titolo": "§9.7 Terminal server, VDI, desktop remoti",
  "file": "manuale/09-profili-carico.md",
  "riga": 144,
  "parte": "Parte 9",
  "testo": "| Parametro | Valore |\n|---|---|\n| vCPU | 4–16, overcommit accettabile |\n| RAM | Alta, **ballooning attivo** |\n| CPU type | `host` (⚠️ verificare machine version se Windows recente su host Intel con VBS) |\n| Disco | SCSI, `iothread=1`, `cache=none`, `discard=on`, `ssd=1` |\n| Rete | VirtIO |\n| VGA | `std`; `qxl` solo con SPICE |\n| KSM sull'host | Utile: molte VM con lo stesso SO deduplicano bene |\n\nÈ lo scenario in cui il ballooning rende di più, perché il consumo varia molto con il numero di utenti connessi.",
  "troncato": 0
 },
 "§9.8": {
  "titolo": "§9.8 Monitoraggio (Zabbix, Prometheus, Grafana, NMS)",
  "file": "manuale/09-profili-carico.md",
  "riga": 158,
  "parte": "Parte 9",
  "testo": "Comportamento **ibrido**: frontend da application server, backend da database con moltissime scritture piccole.\n\n- Applicare il **profilo database** al disco e alla memoria del componente di storage delle metriche\n- Disco dati separato, `discard=on`, throttling se convive con la produzione\n- La retention delle metriche va dimensionata prima: è la causa più frequente di riempimento imprevisto\n- Ballooning disattivato con JVM o TSDB con cache propria",
  "troncato": 0
 },
 "§9.9": {
  "titolo": "§9.9 Sistemi legacy e appliance senza driver VirtIO",
  "file": "manuale/09-profili-carico.md",
  "riga": 167,
  "parte": "Parte 9",
  "testo": "| Parametro | Valore |\n|---|---|\n| Machine | `i440fx` |\n| BIOS | `SeaBIOS` |\n| Disco | `SATA` (o `IDE` per SO molto vecchi) |\n| Rete | `e1000` o `rtl8139` |\n| Ballooning | Disabilitato |\n| Guest agent | Non disponibile |\n\n**Non forzare VirtIO dove non esistono driver.** Meglio una VM legacy che funziona di una ottimizzata che non si avvia. Per Windows molto vecchi esistono versioni storiche della ISO virtio-win con i driver rimossi da quelle recenti.\n\nVanno **isolate a livello di rete** e trattate come debito tecnico con una data di scadenza.\n\n---",
  "troncato": 1
 },
 "§A.8": {
  "titolo": "§A.8 LVM, multipath e SAN",
  "file": "manuale/90-appendici.md",
  "riga": 246,
  "parte": "",
  "testo": "```bash\n# ── inventario\npvs -o pv_name,vg_name,pv_size,pv_free,pv_uuid\nvgs -o vg_name,pv_count,lv_count,vg_size,vg_free\nlvs -o lv_name,vg_name,lv_size,data_percent,metadata_percent\nlvs -a                                   # mostra anche i volumi interni del thin pool\npvdisplay ; vgdisplay ; lvdisplay <vg>/<lv>\n\n# ── multipath\nmultipath -ll                            # percorsi e loro stato\nmultipath -r                             # ricarica le mappe\nmultipathd show config | head -40        # i parametri realmente in uso\nmultipathd show paths\n/lib/udev/scsi_id -g -u -d /dev/sdX      # WWID di un disco\n```\n\n**Estendere una LUN condivisa.** L'ordine è vincolante e saltare il terzo passo è l'errore più comune (§5.9):\n\n```bash\n# 1. estendere la LUN sull'array\n# 2. su OGNI nodo: rileggere la geometria\niscsiadm -m node -R                      # iSCSI\necho 1 > /sys/block/sdX/device/rescan    # SAS/FC\n# 3. su OGNI nodo: aggiornare la mappa multipath   ← IL PASSO CHE SI SALTA\nmultipathd resize map <WWID>\n# 4. su UN SOLO nodo: estendere il physical volume\npvresize /dev/mapper/<WWID>\nvgs                                      # il VG deve essere cresciuto\n```\n\n```bash\n# ── thin pool: sorvegliare i metadati, non solo i dati\nlvs -o lv_name,data_percent,metadata_percent <vg>\nlvextend --poolmetadatasize +1G <vg>/<thinpool>\nlvextend -l +100%FREE <vg>/<thinpool>\n\n# ── autoactivation su storage condiviso (§5.7.4)\nlvchange --setautoactivation n <vg>/<lv>\nvgchange --setautoactivation n <vg>\n```",
  "troncato": 1
 },
 "§8.1 › Overcommit di vCPU": {
  "titolo": "§8.1 CPU › Overcommit di vCPU",
  "file": "manuale/08-parametri-vm.md",
  "riga": 45,
  "parte": "Parte 8",
  "testo": "Sovrallocare è normale, ma va misurato: il sintomo di eccesso è lo **steal time** nel guest. Rapporti tipici 3:1 o 4:1 su carichi generici, **1:1 su database e appliance di rete**.",
  "troncato": 0
 },
 "§8.1 › Priorità e limiti": {
  "titolo": "§8.1 CPU › Priorità e limiti",
  "file": "manuale/08-parametri-vm.md",
  "riga": 37,
  "parte": "Parte 8",
  "testo": "| Parametro | Cosa fa | Uso tipico |\n|---|---|---|\n| `cpuunits` | Peso relativo nello scheduler (default 100 su cgroup v2). Una VM a 200 ottiene il doppio di banda CPU di una a 100 | Prioritizzare la produzione **quando l'host è in contesa** |\n| `cpulimit` | Tetto assoluto di tempo CPU. `0` = nessun limite | Contenere VM che possono impazzire. Impostandolo pari al numero di vCPU si garantisce che non superino mai la propria allocazione |\n| `affinity` | Pinning su core fisici specifici (`0,5,8-11`) | Solo casi estremi di latenza. **Non è una misura di sicurezza**, e i processi di I/O non sono coperti |",
  "troncato": 0
 },
 "§8.1 › Socket e core": {
  "titolo": "§8.1 CPU › Socket e core",
  "file": "manuale/08-parametri-vm.md",
  "riga": 21,
  "parte": "Parte 8",
  "testo": "**Regola: 1 socket, N core.** Semplifica il licensing di Windows Server e SQL Server, ed è gestito meglio dalla maggior parte dei sistemi operativi.\n\nEccezione: attivando NUMA, la documentazione raccomanda **socket pari al numero di nodi NUMA dell'host**.",
  "troncato": 0
 },
 "§8.1 › Tipo di CPU": {
  "titolo": "§8.1 CPU › Tipo di CPU",
  "file": "manuale/08-parametri-vm.md",
  "riga": 8,
  "parte": "Parte 8",
  "testo": "| Valore | Quando | Effetto |\n|---|---|---|\n| `host` | Tutti i nodi hanno **CPU identica** | Espone tutte le istruzioni del processore fisico (AES-NI, AVX-512…). Prestazioni migliori |\n| `x86-64-v2-AES` | Cluster misto, hardware dal 2010 circa | Compromesso sicuro, ampiamente compatibile |\n| `x86-64-v3` | Cluster misto ma hardware recente (Haswell+) | Include AVX2 |\n| `kvm64` (default storico) | Da evitare | Set di istruzioni minimo, prestazioni penalizzate |\n\n> ⚠️ **`host` impedisce la migrazione live verso un nodo con CPU diversa.** In un cluster che potrebbe crescere con hardware differente, usare un modello generico. Il guadagno di `host` è reale ma tipicamente inferiore al costo operativo di perdere la migrazione live — salvo carichi che sfruttano istruzioni specifiche (crittografia, compressione, database).\n\n> ⚠️ **Su host Intel con Windows 11/2022/2025 e VBS attiva**, `host` richiede machine version `11.0+pve2` o successiva: vedi il problema noto qui sopra.",
  "troncato": 0
 },
 "§8.2 › Ballooning — il malinteso più diffuso": {
  "titolo": "§8.2 Memoria › Ballooning — il malinteso più diffuso",
  "file": "manuale/08-parametri-vm.md",
  "riga": 51,
  "parte": "Parte 8",
  "testo": "La documentazione è letterale: *\"il driver balloon è abilitato per default, a meno che non sia esplicitamente disabilitato impostando il valore a zero\"*.\n\n| Configurazione | Palloncino | Statistiche | Cosa vedi nella GUI |\n|---|---|---|---|\n| *Minimum memory* **<** *Memory* | Attivo | ✅ | Uso reale della RAM |\n| *Minimum memory* **=** *Memory* | Presente ma fermo | ✅ | **Uso reale della RAM** |\n| `balloon: 0` | **Assente** | ❌ | **Sempre 100%** |\n\n> **Errore comune:** impostare `balloon: 0` credendo di \"fissare la memoria mantenendo le statistiche\". Per quello serve `min = max`. Con `balloon: 0` si perde il reporting e la VM appare per sempre al 100% di RAM. Meccanismo spiegato in §21.2.",
  "troncato": 0
 },
 "§8.2 › Shares, hugepages, KSM": {
  "titolo": "§8.2 Memoria › Shares, hugepages, KSM",
  "file": "manuale/08-parametri-vm.md",
  "riga": 96,
  "parte": "Parte 8",
  "testo": "- **Shares**: con l'allocazione automatica definisce quanta RAM libera dell'host ogni VM può prendere. Utile perché in un host con più VM in ballooning sia il database a ricevere la memoria in eccesso, non l'ambiente di test.\n- **Hugepages**: riducono i TLB miss. Utili su VM grandi e statiche (database, JVM). Costo: memoria riservata in anticipo, host meno flessibile. Attivare solo con misura prima/dopo.\n- **KSM**: deduplica pagine identiche tra VM. Molto efficace con tante VM dello stesso SO (VDI, farm web). Da valutare rispetto agli attacchi side-channel tra VM.",
  "troncato": 0
 },
 "§8.3 › AIO — la scelta che dipende dallo storage": {
  "titolo": "§8.3 Disco › AIO — la scelta che dipende dallo storage",
  "file": "manuale/08-parametri-vm.md",
  "riga": 126,
  "parte": "Parte 8",
  "testo": "| Valore | Quando |\n|---|---|\n| **`io_uring`** (default) | **File-based (qcow2, NFS, directory), ZFS, LVM-thin, sopra RAID software.** Con `native` qui l'I/O **può bloccarsi** |\n| `native` | **Solo** blocco raw non bufferizzato con `cache=none` **e IO thread attivo** |\n| `threads` | Fallback |\n\nMeccanismo del blocco spiegato in §21.4. **Lasciare `io_uring`** salvo le tre condizioni insieme e una misura che dimostri il guadagno.",
  "troncato": 0
 },
 "§8.3 › Altri parametri disco": {
  "titolo": "§8.3 Disco › Altri parametri disco",
  "file": "manuale/08-parametri-vm.md",
  "riga": 136,
  "parte": "Parte 8",
  "testo": "| Parametro | Effetto | Quando |\n|---|---|---|\n| `discard=on` | Propaga TRIM/UNMAP | **Sempre** con thin provisioning |\n| `ssd=1` | Presenta il disco come SSD | Backing flash: il guest attiva TRIM e disabilita la deframmentazione |\n| `detect_zeroes` | Ottimizza le scritture di zeri | Utile con thin provisioning |\n| `backup=0` | Esclude il disco dal backup VM | Dischi dati enormi con backup applicativo, dischi scratch |\n| `replicate=0` | Esclude dalla replication ZFS | Dischi non replicabili |\n| `iops`, `mbps`, `bps_rd/wr` + varianti `_max` | Throttling per disco, con burst | **Isolare un vicino rumoroso** su storage condiviso |\n\nIl throttling è sottoutilizzato: un log server che satura la SAN penalizza tutte le altre VM. Un tetto con burst risolve senza degradare il funzionamento normale.",
  "troncato": 0
 },
 "§8.3 › Cache mode": {
  "titolo": "§8.3 Disco › Cache mode",
  "file": "manuale/08-parametri-vm.md",
  "riga": 114,
  "parte": "Parte 8",
  "testo": "| Modalità | Page cache host | Sicurezza al crash dell'host | Uso |\n|---|---|---|---|\n| **`none`** (*No cache*) | Bypassata | ✅ Sicura | **Default consigliato**; obbligatoria per `aio=native` |\n| `writeback` | Usata in scrittura | ⚠️ Perdita dati senza UPS/BBU | Solo con alimentazione protetta e consapevolezza |\n| `writethrough` | Usata in lettura | ✅ Sicura | Scritture lente, raramente utile |\n| `directsync` | Bypassata, write-through | ✅ La più sicura | Durabilità massima |\n| `unsafe` | Ignora i flush del guest | ❌ **Pericolosa** | Solo installazioni usa-e-getta |\n\nCon `none` il guest riceve la conferma quando il blocco raggiunge la coda di scrittura dello storage fisico, ignorando la page cache dell'host.",
  "troncato": 0
 },
 "§8.3 › Controller e bus": {
  "titolo": "§8.3 Disco › Controller e bus",
  "file": "manuale/08-parametri-vm.md",
  "riga": 104,
  "parte": "Parte 8",
  "testo": "**`VirtIO SCSI single` + bus `SCSI`** è la configurazione di riferimento: un controller per disco, presupposto per gli **IO thread**.\n\n`VirtIO Block` è più vecchio, valido ma senza le funzionalità SCSI. `SATA`/`IDE` solo per compatibilità o fase transitoria di migrazione.",
  "troncato": 0
 },
 "§8.3 › IO thread": {
  "titolo": "§8.3 Disco › IO thread",
  "file": "manuale/08-parametri-vm.md",
  "riga": 110,
  "parte": "Parte 8",
  "testo": "Delega l'I/O di quel disco a un thread dedicato invece che al thread principale di QEMU. **Da attivare praticamente sempre** con `VirtIO SCSI single`, e obbligatorio per `aio=native`.",
  "troncato": 0
 },
 "§8.4 › Multiqueue": {
  "titolo": "§8.4 Rete › Multiqueue",
  "file": "manuale/08-parametri-vm.md",
  "riga": 157,
  "parte": "Parte 8",
  "testo": "Permette al guest di elaborare pacchetti su più vCPU. Va impostato **pari al numero di vCPU**, ma la documentazione raccomanda di attivarlo **solo su VM con molte connessioni in ingresso**: router, reverse proxy, server HTTP molto trafficati. Altrove aumenta soltanto il carico CPU.\n\n> ⚠️ **Non basta impostarlo lato Proxmox: va abilitato anche dentro il guest.**\n>\n> Linux: `ethtool -L ens18 combined <numero_vCPU>`\n>\n> Windows: Gestione dispositivi → scheda di rete → Proprietà → Avanzate → **Receive Side Scaling** su *Enabled*, poi **Maximum number of RSS Queues** pari al numero di vCPU.\n\n> ⚠️ **Su tutte le interfacce, non solo sulla prima.**\n\n> ⚠️ **Le code attive sono limitate dal numero di vCPU:** `queues` e `cores` vanno alzati insieme.\n\nMeccanismo completo e caso misurato in §21.3.",
  "troncato": 0
 },
 "Proxreporter hardware_monitor.py": {
  "titolo": "Soglie hardware di Proxreporter",
  "file": "",
  "riga": 0,
  "parte": "",
  "origine": "Proxreporter, il tool di reporting Domarc in produzione — modulo hardware_monitor.py",
  "testo": "Le soglie su SMART, ECC e RAID sono quelle che Proxreporter applica da anni sul parco Domarc, non valori scelti qui: salute SMART diversa da PASSED, settori riallocati o pending, errori ECC non corretti, array mdadm degradato.\n\nDue distinzioni pagate sul campo: la tabella degli attributi ATA ha una colonna `WHEN_FAILED`, che contiene la parola FAILED su ogni disco sano — la salute si legge dalla frase «self-assessment test result», non cercando una sottostringa. E le soglie di temperatura 45/55 °C valgono per i dischi SATA: un NVMe lavora normalmente più caldo, quindi su NVMe la temperatura è informativa e il segnale autorevole è il bit Critical Warning del firmware.",
  "troncato": 0
 },
 "API disks/list": {
  "titolo": "Usura dei dischi dall'API di Proxmox",
  "file": "",
  "riga": 0,
  "parte": "",
  "origine": "Proxmox VE, endpoint /nodes/<nodo>/disks/list",
  "testo": "L'API riporta `wearout` come **vita residua** in percentuale: 92 significa che il disco ha consumato l'8% della propria resistenza in scrittura. L'audit mostra l'usura (100 − wearout) perché è il numero che si guarda quando si decide una sostituzione. Verificato contro `smartctl`: wearout 92 corrisponde a «Percentage Used: 8%».",
  "troncato": 0
 },
 "blockstat": {
  "titolo": "Latenze di I/O dalle statistiche QEMU",
  "file": "",
  "riga": 0,
  "parte": "",
  "origine": "Proxmox VE, /nodes/<nodo>/qemu/<vmid>/status/current, campo blockstat",
  "testo": "QEMU conta, per ogni disco virtuale, operazioni e tempo totale dall'avvio della VM. Dividendo il tempo per il numero di operazioni si ottiene la latenza media di lettura, scrittura e flush. È una media dall'accensione, non una misura istantanea: dice se lo storage è lento in generale, non se lo è stato cinque minuti fa.\n\nIl flush è il segnale più parlante: sotto i 5 ms su SSD, oltre i 20 ms indica uno storage saturo o senza cache protetta. Le operazioni fallite (`failed_*_operations`) non sono mai normali.",
  "troncato": 0
 },
 "rrddata": {
  "titolo": "Andamento dell'ultima ora (RRD)",
  "file": "",
  "riga": 0,
  "parte": "",
  "origine": "Proxmox VE, /nodes/<nodo>/rrddata e /qemu/<vmid>/rrddata, timeframe hour",
  "testo": "Proxmox conserva in un database circolare CPU, I/O wait, memoria, rete e le metriche di pressione del kernel (PSI). L'audit ne legge l'ultima ora e ne riporta media e massimo: servono a distinguere un valore alto adesso da un carico che è alto sempre.\n\nLa pressione PSI misura il tempo in cui almeno un processo ha aspettato una risorsa. Una PSI I/O che si tiene sopra il 10% dice che il collo di bottiglia è lo storage, anche quando la CPU sembra tranquilla.",
  "troncato": 0
 },
 "guest agent": {
  "titolo": "Dati letti dentro il guest",
  "file": "",
  "riga": 0,
  "parte": "",
  "origine": "QEMU Guest Agent, comandi get-osinfo / get-fsinfo / network-get-interfaces / get-time",
  "testo": "Con l'agent attivo l'hypervisor può chiedere al sistema operativo ospite il proprio nome, l'occupazione dei filesystem, gli indirizzi IP e l'ora. Sono le uniche informazioni che dall'esterno non si vedono: un disco virtuale pieno all'80% può contenere un filesystem pieno al 99%.\n\nUn comando non supportato dal guest (per esempio su appliance non standard) risponde con un errore, non con un dato: l'audit lo tratta come informazione mancante.",
  "troncato": 0
 },
 "pveperf": {
  "titolo": "Misure di pveperf",
  "file": "",
  "riga": 0,
  "parte": "",
  "origine": "Proxmox VE, comando pveperf — soglie indicative dal forum Proxmox e dall'esperienza Domarc",
  "testo": "`pveperf` misura la CPU, la lettura sequenziale, il tempo di seek e soprattutto i **fsync al secondo**: quante scritture sincronizzate su disco il sistema regge. È il numero che conta per macchine virtuali e database, perché ogni transazione ne chiede una.\n\nValori indicativi: sotto 200 il sistema è inadatto a VM con database; fra 200 e 1000 è accettabile per carichi generici; sopra 1000 è buono. Non sono soglie ufficiali Proxmox: sono i valori con cui si ragiona in campo, e vanno letti insieme al tipo di storage.\n\n`pveperf` misura anche la risoluzione DNS: un DNS interno che risponde in più di mezzo secondo rallenta login, interfaccia e ogni comando del nodo, ed è quasi sempre un resolver sbagliato o irraggiungibile.",
  "troncato": 0
 }
}
