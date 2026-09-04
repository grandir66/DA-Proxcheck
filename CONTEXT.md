# CONTEXT.md — DA-Proxcheck

Passaggio di consegne: **leggilo per primo**, poi `CLAUDE.md` per le regole
tecniche vincolanti. Si aggiorna quando cambia architettura, moduli o stato —
**non per ogni fix**.

## 1. Cos'è e perché esiste

Audit di sola lettura di un nodo o di un intero cluster Proxmox VE, eseguito dal computer del tecnico via SSH, che confronta quanto rilevato con le best practice del manuale operativo Domarc e della guida alla configurazione VM per carico, e produce due Markdown per il cliente: inventario e rilievi. Nato il 2026-09-03 come `tool/audit-nodo.py` dentro il repo del manuale (`DA-Proxmox-Docs`), spostato qui il 2026-09-04 perché è diventato un programma con un ciclo di vita proprio.

**Non è:** un agente installato sui nodi (quello è **DA-PVE-Agent**), né un sistema di monitoraggio continuo (**Proxreporter**, DA-Zabbix): gira quando lo lancia un tecnico, non modifica nulla, non lascia file sui nodi. Non contiene le regole: le applica. Le regole vivono nel manuale.

## 2. Stato — fotografia 2026-09-04

- Un solo file, `audit-nodo.py` (versione: vedi `VERSIONE_SCRIPT` in testa al file), stdlib only, Python 3.7+ sul client e il `python3` di Debian sui nodi.
- In esercizio: verificato su due cluster reali (uno a 4 nodi con ~54 VM, 5 minuti con `--performance`; uno a nodo singolo) e un terzo raggiungibile solo via VPN. Indirizzi e accessi in `ACCESSI.md` (git-ignored, master nel vault).
- Windows: supportato per costruzione (niente ControlMaster, console UTF-8, colori via SetConsoleMode), **non ancora provato** su una macchina Windows vera.
- Stato locale del tecnico in `~/.config/audit-nodo/`: `hosts.json` (host, etichetta, cliente, codice, parametri, esito dell'ultimo audit) e `profili-<cluster>.json` (VMID → profilo). Mai la password.

## 3. Architettura in breve

- `COLLECTOR_NODO` (stringa Python nel file) → eseguito su OGNI nodo con `python3 -`: `pvesh get … --output-format json` + `smartctl`, `ip`, `lvs`, `zpool`, `timedatectl`, `corosync-cfgtool`, `ping` fra gli anelli, `pveperf` se richiesto. Restituisce `{nodo, vms, lxc, errori}`.
- `COLLECTOR_CLUSTER` → eseguito sul nodo d'ingresso: raccoglie `/cluster/*`, esegue il collector-nodo in locale e poi `ssh -o BatchMode=yes root@<ip> python3 -` verso ogni altro nodo online (il sorgente del collector-nodo viaggia dentro in base64). Restituisce `{ingresso, cluster, nodi{…}, errori}`.
- Locale: `raccogli()` → `costruisci_vms()` → `controlla_cluster/nodo/hardware/performance/generali/profilo/lxc` → `Esito` di `Rilievo(livello, ambito, messaggio, fonte)` → `stampa_report` (terminale) + `scrivi_inventario_md` + `scrivi_rilievi_md`.
- La categoria di un rilievo si ricava dall'`ambito` (`categoria_di`): cambiare il testo di un ambito può spostare un rilievo di categoria.
- `PROFILI` (7 tipologie di carico, dal 2026-09-04; prima 13) raggruppa la Parte 3 della guida-carico per regole uguali; `CONVERSIONE_PROFILI_V1` converte i file salvati con i vecchi numeri. `INDIZI_PROFILO` propone la tipologia dal nome/SO (regex in ordine di priorità: test/replica prima di tutto).

## 4. Trappole già risolte (non ripercorrerle)

- **`FAILED` in SMART** (2026-09-04): la tabella ATA ha la colonna `WHEN_FAILED` → ogni disco sano sembrava guasto → il controllo è ancorato a `self-assessment test result:`.
- **NVMe a 65 °C sano** (2026-09-04): le soglie 45/55 °C di Proxreporter sono per SATA → su NVMe la temperatura è informativa, conta `Critical Warning`.
- **`zd*` in `lsblk`/`disks/list`** sono gli zvol delle VM, non dischi → filtrati.
- **Guest agent**: un comando non supportato (MikroTik CHR) restituisce `{"error": …}` al posto della lista → guardie di tipo su tutti i risultati agent.
- **Scarto orologio di esattamente +2 h** = l'agent riporta l'ora locale, non un orologio rotto → informativo se multiplo di un'ora.
- **`pveperf` su ZFS** non misura letture e seek → "non misurato", non 0. E non si ripete su una dir che sta sul filesystem di root.
- **Interfacce `tap*`** dei guest finivano fra le "NIC fisiche" di un anello quando il bridge ha porte guest → escluse `tap/veth/fwln/fwpr/fwbr`.
- **`ControlPath` troppo lungo su macOS** (limite ~104 caratteri del socket) → risolto alla radice togliendo del tutto ControlMaster: una connessione sola non ne ha bisogno, e Windows non ce l'ha.
- **Provare l'interattività** richiede un vero pty (`sys.stdin.isatty()`), e l'harness deve aspettare il testo esatto del prompt *nuovo*, non cercarlo in un buffer cumulativo.
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

## 6. Igiene / stale noti — 2026-09-04

- `scripts/deploy.sh` del kit non serve: non c'è un runtime Linux, lo script gira sul client. Lasciato per lo standard, non usato.
- Due report di prova prodotti dal menu sono rimasti non tracciati nella cartella `tool/` del repo del manuale: da cancellare a mano, contengono dati di infrastruttura.
