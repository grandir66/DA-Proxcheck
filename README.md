# DA-Proxcheck

Due strumenti per chi progetta e verifica infrastrutture **Proxmox VE**, nati dal manuale operativo di Domarc e pubblicati per essere usati così come sono:

| Strumento | Cosa fa | Come si usa |
| --- | --- | --- |
| [`audit-nodo.py`](audit-nodo.py) | Audit di sola lettura di un nodo o di un intero cluster, dal computer del tecnico, confrontato con le best practice. Produce **inventario** e **report** in Markdown. | `python3 audit-nodo.py --host <ip-nodo>` |
| [`questionario/questionario-migrazione.html`](questionario/questionario-migrazione.html) | Questionario di raccolta dati per una migrazione da VMware o un'installazione nuova: una pagina sola, offline, in italiano e in inglese, che esporta il Markdown di progetto. | Aprire il file nel browser, oppure la [versione pubblicata](https://grandir66.github.io/DA-Proxcheck/questionario/questionario-migrazione.html) |

*English summary at the end of this page.*

---

## 1. Audit di nodo e cluster — `audit-nodo.py`

Gira su macOS, Linux o Windows con Python 3.7+ e il client `ssh`. **Una sola connessione SSH**, solo lettura, nessun file lasciato sui nodi.

```bash
python3 audit-nodo.py --host 192.168.40.1 --cliente "Rossi Srl" --codice C0123 --output ~/report
python3 audit-nodo.py --host 192.168.40.1 --performance      # aggiunge pveperf (fsync/s, DNS)
python3 audit-nodo.py                                        # menu: host salvati, parametri, ultimo esito
```

Produce `C0123_Rossi-Srl_192.168.40.1_inventory.md` (cosa c'è) e `C0123_Rossi-Srl_192.168.40.1_report.md` (cosa non torna, con la fonte di ogni regola). Nome e codice del cliente, se non passati, vengono chiesti.

### Come funziona

1. Un collector Python (solo libreria standard) viene inviato al nodo d'ingresso ed eseguito una volta: interroga l'API locale (`pvesh … --output-format json`) e pochi comandi di sola lettura. Se il nodo è in cluster, ripete la raccolta sugli altri nodi con la fiducia SSH interna che Proxmox distribuisce da sé. Restituisce un unico JSON.
2. In locale: tabella di tutte le VM del cluster con la **tipologia di carico** proposta dal nome e dal sistema operativo (asterisco): INVIO accetta, un VMID la cambia. Sette tipologie: domain controller/DNS, database, applicativo/web/monitoraggio, rete (firewall, proxy, load balancer), dati (file server, log/SIEM, backup), terminal server/VDI, test/legacy/replica. Ogni tipologia aggiunge le sue regole ai controlli generali.

Le classificazioni delle VM si salvano da sole in `~/.config/audit-nodo/profili-<cluster>.json`; gli host e i parametri in `hosts.json`. **La password non viene mai vista né salvata dallo script**: la chiede `ssh` sul terminale.

### Cosa controlla

| Area | Esempi |
| --- | --- |
| Cluster / corosync | quorum e voti, due nodi senza QDevice, anelli sulle stesse NIC fisiche, latenza misurata fra i nodi, HA, job di backup e guest scoperti, replica, Ceph |
| Nodo | sottoscrizione e repository, aggiornamenti e riavvio pendente, servizi, certificati, RAM e swap, overcommit, storage, ZFS (`zpool status -x`), LVM-thin, orologio, bond degradati o con un solo slave |
| Hardware | SMART ATA e NVMe, usura, temperatura, ECC, RAID mdadm |
| Performance | rrddata dell'ultima ora (CPU, iowait, PSI, swap, ARC), `pveperf` opzionale |
| VM | CPU type, socket/NUMA, ballooning, bus e cache dei dischi, iothread, discard, VirtIO, multiqueue, agent, snapshot vecchi, modifiche pendenti, filesystem e orologio del guest, latenze di flush, copertura backup, profilo di carico |
| Container | privilegiati, protection |

Ogni rilievo ha tre livelli (🔴 bloccante · 🟡 da valutare · ℹ️ informativo) e cita la regola che applica. **Il report riporta in fondo il testo di ogni regola citata**, nella sezione «Le regole applicate»: per capire un rilievo non serve avere il manuale sottomano. I passaggi stanno in `fonti_manuale.py`, generato da `strumenti/estrai-fonti.py` a partire dal manuale operativo Domarc; le soglie che il manuale non copre (SMART, fsync/s, latenze di I/O) hanno lì la propria spiegazione. Dove nessuna fonte prescrive un valore assoluto, il dato è informativo.

### Opzioni

`--solo-questo-nodo` non estende al cluster · `--solo-accese` salta le VM spente · `--solo-nodo` salta le VM · `--json dati.json` salva i dati grezzi · `--da-json dati.json` rianalizza senza riconnettersi · `--breve` solo riepilogo · `--no-color`.

---

## 2. Questionario di migrazione — `questionario/`

Una pagina HTML autonoma (nessun server, nessuna dipendenza online oltre ai font) da compilare con il cliente prima di una migrazione da VMware o di un'installazione nuova. Undici sezioni: referenti e vincoli, ambiente VMware, inventario VM, rete esistente, reti del cluster, storage, hardware dei nodi, backup, sicurezza, operatività, decisioni da chiudere.

- **Base / Completo**: la versione base è per installazioni da 2-3 nodi; la completa aggiunge inventario host, SAS diretto, dipendenze fra servizi, conformità estesa.
- **Italiano / English** con un pulsante; l'esportazione segue la lingua scelta.
- I campi **🔴 bloccanti** vanno compilati prima dell'esportazione (o con "da verificare"): sono le domande che, scoperte a lavori avviati, costano un rifacimento o un acquisto sbagliato.
- Un valore per casella: numeri con **default proposto** (tratteggiati), scelte chiuse, sotto-campi separati (versione e indirizzo, porte e velocità), tabella degli **indirizzi IP per nodo**, totale della capacità calcolato.
- **Bozza** salvata in automatico nel browser e scaricabile in JSON; **esportazione in Markdown** con lo stesso ordine del questionario cartaceo; script PowerCLI per l'inventario automatico delle VM.

---

## Sviluppo

```bash
python3 -m venv .venv && .venv/bin/pip install pytest ruff   # una volta per macchina
bash scripts/controlla.sh                                    # compila, collector, fonti, ruff, pytest
python3 strumenti/estrai-fonti.py                            # rigenera il testo delle regole dal manuale
```

`fonti_manuale.py` è **generato**: contiene i passaggi del manuale che i rilievi citano, con file e riga di origine, e non si modifica a mano. Serve il repo del manuale (`DA_PROXMOX_DOCS`, default `~/Progetti/manuali/proxmox`) solo per rigenerarlo: per usare lo strumento no.

Le soglie dell'audit vengono dal manuale operativo Proxmox di Domarc e dalla guida alla configurazione VM per carico; le soglie hardware da Proxreporter, il tool di reporting Domarc, solo la parte di lettura. `CONTEXT.md` è il passaggio di consegne; `CHANGELOG.md` racconta cosa è cambiato e perché.

---

## English summary

**DA-Proxcheck** ships two tools for Proxmox VE projects:

- **`audit-nodo.py`** — read-only audit of a node or a whole cluster from the engineer's laptop (macOS, Linux, Windows; Python 3.7+ and `ssh`). One SSH connection: a stdlib-only collector runs on the entry node, queries the local API and, in a cluster, repeats the collection on the other nodes through the cluster's own SSH trust. It compares cluster/corosync, node, hardware, storage, network, performance, VM and container settings with best practices and writes two Markdown files: an **inventory** and a **findings report** (🔴 blocking · 🟡 review · ℹ️ info). Each finding cites the rule it applies, and the report **quotes the full text of every cited rule** in a closing section, so it stands on its own without the manual. Passwords are never seen or stored; VM workload types (7, proposed automatically from the VM name and guest OS, always confirmable) are remembered per cluster.
- **`questionario/questionario-migrazione.html`** — a single-file, offline **migration questionnaire** (VMware → Proxmox or greenfield), Italian and English, Basic/Full scope, blocking fields, per-node IP table, defaults, auto-saved draft and **Markdown export**. Published at <https://grandir66.github.io/DA-Proxcheck/questionario/questionario-migrazione.html>.

Messages, labels and documentation are in Italian; the questionnaire itself is bilingual.

## Licenza

© Domarc Srl. Pubblicato per consultazione e uso; nessuna licenza esplicita è ancora stata scelta — chi vuole riusarlo in un proprio prodotto scriva prima.
