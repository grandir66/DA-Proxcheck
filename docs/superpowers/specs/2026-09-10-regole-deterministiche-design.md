# Regole deterministiche per l'analisi di un cluster Proxmox

**Stato:** proposta, in revisione · **Data:** 2026-09-10
**Banco di prova:** cluster OpenCRM di IT&M (73226) — 3 nodi, 39 VM, 5 container,
Ceph, PVE 9.0.11. Ogni regola qui sotto è stata provata contro la raccolta vera
(`raccolta.json`, 1,5 MB) o contro gli endpoint interrogati sul cluster.

## Il principio

> **Una regola è deterministica solo se esiste un campo raccolto che la decide.**

Ogni regola nomina il proprio campo, come `e_spenta()` nomina `status`. Dove il
campo non c'è, la regola non si scrive: si estende prima la raccolta. Il
corollario è che il catalogo si legge come una tabella — campo, soglia, livello,
fonte — e non come una collezione di opinioni.

Tre conseguenze operative:

1. **Nessuna regola senza fonte.** Ogni rilievo cita la sezione del manuale che
   lo giustifica, e `strumenti/estrai-fonti.py --verifica` si rifiuta di passare
   se una citazione non ha il suo testo. Le sezioni esistono già quasi tutte.
2. **Nessuna regola che dipende dal nome.** «Si chiama `-sql`, quindi è un
   database» è una proposta, non un rilievo. I nomi decidono i *suggerimenti* di
   tipologia, mai le regole.
3. **Assenza ≠ difetto, salvo dichiararlo.** Un campo mancante perché il nodo
   non ha risposto non è un difetto del cliente. Dove l'assenza *è* il difetto
   (una VM senza job di backup) la regola lo dice esplicitamente.

## Che cosa si raccoglie in più

Cinque chiamate, tutte in sola lettura, tutte **verificate sul cluster vero** il
2026-09-10. Fra parentesi ciò che aprono.

| Chiamata | Che cosa dà |
| --- | --- |
| `/cluster/firewall/{options,rules,groups,aliases}` | l'interruttore del datacenter e le regole che valgono per tutti |
| `/nodes/<n>/firewall/{options,rules}` | l'interruttore dell'host e le sue regole |
| `/nodes/<n>/qemu/<id>/firewall/{options,rules}` | il firewall della singola VM |
| `/storage` | le **definizioni** degli storage: `nodes=`, `krbd`, `sparse`, prune |
| `/nodes/<n>/ceph/{pool,osd}` + `/nodes/<n>/ceph/cfg/raw` | pool con `size/min_size`, albero OSD, `public_network` e `cluster_network` |
| `/cluster/notifications/{endpoints,matchers}` | dove finiscono gli avvisi, e quali |

`/nodes/<n>/ceph/pools` **non esiste** (nessun handler): l'endpoint giusto è
`/nodes/<n>/ceph/pool`, al singolare. Scritto qui perché è il tipo di errore che
si scopre solo provando.

## Una categoria nuova: «Coerenza del cluster»

I rilievi che nascono dal **confronto fra nodi** non appartengono a nessun nodo.
«`vlan20` ha una rete diversa su PX-03» non è un difetto di PX-03: è un difetto
del cluster, e mettendolo sotto «Nodo PX-03» chi legge non lo collega agli altri
due. Vanno in una categoria loro, che si legge come un elenco di divergenze.

Regola di stile della categoria: **ogni rilievo mostra i valori a confronto**,
non solo l'anomalia. «PX-01 172.18.20.1 · PX-02 172.18.20.2 · PX-03 172.20.20.3»
dice da sé qual è quello fuori posto.

---

# Il catalogo

Livelli: 🔴 bloccante (fermo, perdita dati, aggiornamento impossibile) ·
🟡 attenzione (scostamento con un costo reale) · ℹ️ informativo.
La colonna **Esito su IT&M** è il risultato reale sul cluster di prova: `—`
significa che la regola non scatta, ed è un dato prezioso quanto un rilievo,
perché dimostra che non grida al lupo.

## 1 · Coerenza delle interfacce di rete

Campo: `nodo.network` (da `/nodes/<n>/network`), `nodo.ip_link`.

| # | Regola | Livello | Fonte | Esito su IT&M |
| --- | --- | --- | --- | --- |
| RETE-01 | Lo stesso `vlan-id` ha reti IP diverse su nodi diversi | 🔴 | §1.3, §2.6 | **scatta**: `vlan20` 172.18.20.x su PX-01/02, **172.20.20.3** su PX-03 |
| RETE-02 | Lo stesso `vlan-id` ha reti IP diverse (secondo caso) | 🔴 | §1.3, §2.6 | **scatta**: `vlan100` 172.18.100.x, **172.20.100.3** su PX-03 |
| RETE-03 | Un bridge esiste su un nodo e non su un altro | 🟡 | §2.6 | **scatta**: `vmbr0v20` e `bond0.20` solo su PX-02 |
| RETE-04 | Una VM usa un bridge che non esiste su tutti i nodi | 🔴 | §2.6, §8.4 | — (tutte usano `vmbr_int` e `vmbr0`, presenti ovunque) |
| RETE-05 | Una VM usa un `tag` VLAN riservato a corosync o allo storage | 🟡 | §1.3, §3.3 | **scatta**: 3 VM sul tag 10 (corosync), 3 sul tag 30 (Ceph) |
| RETE-06 | Un bridge porta un IP di gestione e traffico ospite insieme | ℹ️ | §1.3 | **scatta**: `vmbr_int` fa entrambe le cose |
| RETE-07 | Un'interfaccia dichiarata non è `active` | 🟡 | §2.6 | — |
| RETE-08 | Un'interfaccia è attiva ma senza `autostart` | 🟡 | §2.6 | — (sopravvive fino al riavvio: si scopre nel momento peggiore) |
| RETE-09 | Il bridge è usato con `tag` ma non è `bridge_vlan_aware` | 🟡 | §2.6, §14.7 | **scatta**: `vmbr0` non vlan-aware, PVE crea `vmbr0v20` |

## 2 · MTU e aggregazione

Campo: `ip_link` (MTU reale), `nodo.bonding` (testo di `/proc/net/bonding/*`),
`nodo.network` (`bond_mode`, `slaves`), `ceph.cfg` per sapere dove passa lo storage.

| # | Regola | Livello | Fonte | Esito su IT&M |
| --- | --- | --- | --- | --- |
| MTU-01 | Stessa interfaccia, MTU diverso fra i nodi | 🔴 | §1.3 | — (tutto 1500 ovunque) |
| MTU-02 | La rete dello storage (Ceph `cluster_network`, NFS, iSCSI) è a 1500 | 🟡 | §1.3, §6.2 | **scatta**: `cluster_network 172.18.30.0/24` su `bond50`, MTU 1500 |
| MTU-03 | MTU incoerente lungo la catena bond → bridge → vlan | 🔴 | §2.6 | — |
| BOND-01 | `bond_mode` diverso fra i nodi per lo stesso bond | 🔴 | §1.3 | — (802.3ad su tutti e tre) |
| BOND-02 | Bond 802.3ad con un solo slave, o slave non tutti attivi | 🔴 | §1.3 | — |
| BOND-03 | `xmit_hash_policy` non `layer3+4` su un bond che porta storage | 🟡 | §1.3 | da verificare alla prima raccolta con il dato completo |
| BOND-04 | `lacp_rate` diverso fra i nodi | 🟡 | §1.3 | — |
| BOND-05 | Corosync e storage condividono lo stesso bond fisico | 🔴 | §1.3, §3.3 | **scatta**: `vlan10` (corosync) e `vlan30` (Ceph) entrambe su `bond50` |

## 3 · Coerenza degli storage

Campo: `nodo.storage` (vista per nodo) e la **definizione** da `/storage`.

| # | Regola | Livello | Fonte | Esito su IT&M |
| --- | --- | --- | --- | --- |
| STOR-01 | Uno storage è attivo su alcuni nodi e non su altri | 🔴 | §4.1 | — (PBS, local, ZFS, CEPH ovunque) |
| STOR-02 | Lo stesso storage dichiara `content` diversi fra i nodi | 🟡 | §4.1 | — |
| STOR-03 | Uno storage è dichiarato `shared` ma non è raggiungibile ovunque | 🔴 | §4.1 | — |
| STOR-04 | La restrizione `nodes=` esclude un nodo del cluster | 🟡 | §4.1 | — (`nodes: PX-01,PX-03,PX-02`, cioè tutti) |
| STOR-05 | Una VM ha dischi su storage **locale** in un cluster | 🔴 | §4.1, §7.2 | da calcolare: blocca migrazione e HA |
| STOR-06 | Uno storage locale (`dir`) dichiara `images`/`rootdir` | 🟡 | §4.1 | **scatta**: `local` li dichiara su tutti e tre |
| STOR-07 | Spazio libero sotto il 15% | 🟡 | §4.6 | da calcolare sui valori raccolti |
| STOR-08 | ZFS: `zfs_arc_max` diverso fra i nodi con RAM uguale | 🟡 | §4.2 | da confrontare |

## 4 · Firewall

Campo: `/cluster/firewall/options` e `rules`, `/nodes/<n>/firewall/options` e
`rules`, il firewall per VM, più `pve_firewall` (stato del demone).

| # | Regola | Livello | Fonte | Esito su IT&M |
| --- | --- | --- | --- | --- |
| FW-01 | Firewall del datacenter acceso, **firewall dell'host spento** | 🔴 | §15.3 | **scatta su tutti e tre**: datacenter `enable:1`, host `enable:0` |
| FW-02 | Esistono regole a livello cluster che non si applicano da nessuna parte | 🔴 | §15.3, §15.5 | **scatta**: 4 regole definite, nessun host che le applica |
| FW-03 | `pve-firewall status` dice `pending changes` | 🟡 | §15.4 | **scatta su PX-02 e PX-03** |
| FW-04 | Interruttore dell'host diverso fra i nodi | 🔴 | §15.3 | — (spento ovunque: coerente, e sbagliato) |
| FW-05 | Policy in ingresso non `DROP` con firewall acceso | 🟡 | §15.2 | da leggere in `options` |
| FW-06 | Nessuna regola consente la gestione (8006/22) prima di chiudere | 🔴 | §15.4 | regola anti-lockout: si controlla **prima** di suggerire di accendere |
| FW-07 | VM con firewall acceso sull'interfaccia e nessuna regola | 🟡 | §15.7 | — |
| FW-08 | Appliance di rete con firewall PVE acceso sull'interfaccia | ℹ️ | §9.5 | già presente oggi, resta |

## 5 · Rete di migrazione e replica

Campo: `/cluster/options` (chiave `migration`), `corosync_conf`, `network`.

| # | Regola | Livello | Fonte | Esito su IT&M |
| --- | --- | --- | --- | --- |
| MIGR-01 | Nessuna rete di migrazione dichiarata: si usa quella del cluster | 🔴 | §1.3, §3.3 | **scatta**: `migration` assente, si migra su `vlan10` = corosync |
| MIGR-02 | La rete di migrazione coincide con quella di corosync | 🔴 | §1.3, §3.3 | **scatta**, per conseguenza della precedente |
| MIGR-03 | La rete di migrazione coincide con quella dello storage | 🟡 | §1.3 | **scatta**: `bond50` porta anche Ceph |
| MIGR-04 | `migration: type=insecure` su una rete non dedicata | 🔴 | §1.3 | — (non dichiarato) |
| MIGR-05 | La rete di replica ZFS non è dichiarata separata | 🟡 | §4.5 | **scatta** |

## 6 · Coerenza fra host e cluster

Campo: `status.pveversion`, `status.current-kernel`, `apt_update`, `apt_repos`,
`subscription`, `timedatectl`, `services`, `zfs_arc_max`, `status.cpuinfo`,
`status.memory`.

| # | Regola | Livello | Fonte | Esito su IT&M |
| --- | --- | --- | --- | --- |
| COER-01 | Versione di `pve-manager` diversa fra i nodi | 🔴 | §19.3 | — (9.0.11 ovunque) |
| COER-02 | **Kernel in esecuzione** diverso fra i nodi | 🟡 | §19.3 | **scatta**: 6.17.13-2 su PX-01, 6.14.11-4 su PX-02 e PX-03 |
| COER-03 | Numero di aggiornamenti pendenti molto diverso fra i nodi | 🟡 | §19.1 | **scatta**: 166 / 111 / 111 |
| COER-04 | Repository configurati diversi fra i nodi | 🔴 | §1.6 | da confrontare |
| COER-05 | Repository enterprise senza subscription valida | 🔴 | §1.6 | da leggere in `subscription` |
| COER-06 | Livello di subscription diverso fra i nodi | 🟡 | §1.6 | da confrontare |
| COER-07 | Fuso orario diverso fra i nodi | 🔴 | §2.5 | — |
| COER-08 | Orologio non sincronizzato (NTP inattivo o non in sync) | 🔴 | §2.5, §3.4 | da leggere in `timedatectl` + `services` |
| COER-09 | Un servizio PVE essenziale non è attivo su un nodo | 🔴 | §20.2 | dato già raccolto, oggi non guardato |
| COER-10 | CPU o RAM molto sbilanciate fra i nodi | ℹ️ | §1.5, §7.2 | rilevante per l'HA: il nodo piccolo non regge il carico degli altri |
| COER-11 | Certificato in scadenza entro 30 giorni | 🟡 | §2.7 | dato già raccolto, oggi non guardato |

## 7 · Replica e alta affidabilità

Campo: `cluster.replication`, `nodo.replication` (stato), `cluster.ha_resources`,
`cluster.ha_rules`, `cluster.ha_status`.

| # | Regola | Livello | Fonte | Esito su IT&M |
| --- | --- | --- | --- | --- |
| HA-01 | Guest replicati ma **non** in HA: la copia c'è, nessuno la accende | 🟡 | §4.5, §7.1 | **scatta**: 7 guest replicati, 1 solo in HA (`ct:221`) |
| HA-02 | Risorsa in HA su storage locale senza replica verso gli altri nodi | 🔴 | §7.2 | da calcolare |
| HA-03 | Nessuna regola HA definita: la risorsa può accendersi ovunque | 🟡 | §7.5 | **scatta**: `ha_rules` vuoto |
| HA-04 | Job di replica **senza schedule esplicito** | ℹ️ | §4.5 | **scatta**: 6 job su 7 (vale il default `*/15`) |
| HA-05 | Job di replica con `fail_count` > 0 o ultimo tentativo fallito | 🔴 | §4.5 | — (`fail_count: 0`) |
| HA-06 | Ultima sincronizzazione più vecchia di tre volte lo schedule | 🔴 | §4.5 | da calcolare su `last_sync` |
| HA-07 | HA attivo con meno di tre nodi e senza QDevice | 🔴 | §7.2, §3.5 | — (tre nodi) |
| HA-08 | LRM in stato diverso da `idle`/`active` | 🟡 | §7.4 | — |
| HA-09 | Replica e HA puntano a nodi diversi | 🔴 | §7.5, §4.5 | da incrociare |

## 8 · Ceph *(nuovo, richiede la raccolta aggiuntiva)*

Campo: `/nodes/<n>/ceph/pool`, `/nodes/<n>/ceph/osd`, `ceph/cfg/raw`, `cluster.ceph`.

| # | Regola | Livello | Fonte | Esito su IT&M |
| --- | --- | --- | --- | --- |
| CEPH-01 | Pool con `size < 3` o `min_size < 2` | 🔴 | §6.5 | da leggere: un 2/1 è perdita di dati che aspetta |
| CEPH-02 | Monitor in numero pari, o meno di tre | 🔴 | §6.4 | da contare in `monmap` |
| CEPH-03 | `cluster_network` non separata da `public_network` | 🟡 | §6.2 | **da verificare**: separate come reti, stesso bond fisico |
| CEPH-04 | OSD sbilanciati fra i nodi | 🟡 | §6.4 | da contare nell'albero |
| CEPH-05 | Flag `noout`/`norebalance` lasciato acceso | 🔴 | §6.6 | da leggere in `osdmap.flags` |
| CEPH-06 | `HEALTH_WARN` o `HEALTH_ERR` | 🔴 | §6.6 | — (`HEALTH_OK`) |
| CEPH-07 | Numero di PG lontano da quello suggerito dall'autoscaler | ℹ️ | §6.5 | da leggere in `autoscale_status` |

## 9 · Backup e notifiche

Campo: `cluster.backup`, `cluster.not_backed_up`, `/cluster/notifications/*`.

| # | Regola | Livello | Fonte | Esito su IT&M |
| --- | --- | --- | --- | --- |
| BKP-01 | Guest senza alcun job di backup | 🟡 | §12.1 | **scatta**: VM 101 `Mint` (già presente oggi) |
| BKP-02 | Job con `keep-all`, nessuna retention | ℹ️ | §12.7 | **scatta** (già presente oggi) |
| BKP-03 | Job che scrive su storage **locale** al nodo | 🔴 | §12.1 | — (scrive su PBS) |
| BKP-04 | `notification-mode: legacy-sendmail` | 🟡 | §16.9 | **scatta**: il job usa il canale vecchio |
| BKP-05 | Nessun target di notifica configurato oltre a `mail-to-root` | 🟡 | §16.2 | da leggere negli endpoint |
| BKP-06 | Nessun matcher copre i fallimenti di backup | 🔴 | §16.6 | un backup fallito che non avvisa nessuno è un backup che non c'è |

## 10 · Ciclo di vita delle macchine

| # | Regola | Livello | Fonte | Esito su IT&M |
| --- | --- | --- | --- | --- |
| VITA-01 | Snapshot più vecchio di 30 giorni | 🟡 | §8.6, §4.7 | da calcolare |
| VITA-02 | Più di tre snapshot sulla stessa macchina | 🟡 | §8.6 | da contare |
| VITA-03 | Macchina spenta da oltre 90 giorni e mai avviata | ℹ️ | §8.6 | richiede `uptime`/`lastseen`: **da verificare se il dato basta** |
| VITA-04 | Kernel installato più recente di quello in esecuzione | 🟡 | §19.1 | già presente oggi |

---

## Cosa NON entra, e perché

- **Regole che dipendono dal nome della macchina.** Restano suggerimenti di
  tipologia. Un rilievo che nasce da una regex sul nome non è deterministico.
- **Soglie di prestazione assolute** (IOPS, latenza «giusta»). Dipendono dal
  carico e dall'hardware; restano informative, com'è oggi per `pveperf`.
- **VITA-03** se il dato non c'è: si dichiara e si lascia fuori, invece di
  dedurla da qualcosa che non la dice.
- **Regole sulle VM spente**: il filtro esistente vale anche per queste. Le
  regole di *coerenza del cluster* invece riguardano i nodi, e valgono sempre.

## Ordine di realizzazione

1. **Coerenza del cluster** (RETE, MTU/BOND, COER) — nessuna raccolta nuova,
   trova subito otto rilievi su questo cluster, e introduce la categoria.
2. **Migrazione e HA** (MIGR, HA) — nessuna raccolta nuova, è la famiglia con il
   rilievo più grave (MIGR-01/02).
3. **Raccolta aggiuntiva** — le cinque chiamate, con il collector che non si
   ferma se una non risponde: un cluster senza Ceph deve restare analizzabile.
4. **Firewall** (FW) — la famiglia che dipende di più dalla raccolta nuova, e
   che qui trova un difetto vero su tutti e tre i nodi.
5. **Ceph, backup e notifiche, ciclo di vita.**

Ogni lotto chiude con `scripts/controlla.sh` verde e con la rianalisi della
raccolta di IT&M, che è la prova sul campo: i rilievi attesi compaiono, quelli
segnati `—` restano assenti.

## Conteggio atteso

Sul cluster di prova le regole nuove producono **circa 20 rilievi**, di cui
5-6 bloccanti che oggi non compaiono da nessuna parte — il firewall degli host
spento, la migrazione su corosync, le VLAN divergenti. Nessuno di essi riguarda
una singola macchina: sono tutti difetti dell'impianto.

---

# Parte seconda — quello che si vede

Le regole dicono cosa non torna. Restano due cose da mostrare, e nessuna delle
due è un rilievo: **la topologia di rete** e **la configurazione letta**.

## 11 · La mappa della rete di ogni nodo

Non si riprogetta: la rappresentazione esiste già in **DA-PXREPL**
(`~/Progetti/DA-PXREPL`, `frontend/src/views/Nodes.vue`, la sezione
`topology-bridges-list`), e va ricostruita qui con gli stessi elementi. Si
disegna con i dati che **già raccogliamo** — `nodo.network` per bridge, bond e
VLAN, la config di ogni VM per il collegamento — senza chiamate nuove.

Un riquadro per bridge, e dentro, dall'alto in basso:

1. **Il bridge**: nome e CIDR, oppure «switch L2» se non ha indirizzo.
2. **L'uplink**: le porte, come pastiglie. Una porta `bond*` porta accanto il
   **modo** in chiaro (802.3ad, active-backup…) e sotto i propri **slave**, che
   è il punto in cui si vede se l'aggregazione è davvero doppia.
3. **Le VLAN** attestate su quel bridge, con l'indirizzo per nodo.
4. **I guest collegati**, ciascuno con il proprio **tag VLAN** a badge; e chi
   non ne ha, senza badge — l'assenza è essa stessa un'informazione.

Sotto, ripiegate, le interfacce che non sono bridge.

**Perché sta nell'inventario e non nei rilievi**: è una fotografia, non un
giudizio. Serve a chi deve capire com'è fatto un cluster che non ha costruito
lui — ed è il documento su cui si discute con il cliente. I difetti che quella
mappa rende evidenti (una VLAN con reti diverse, un bond con uno slave solo,
una VM sulla VLAN di corosync) li dicono le regole della famiglia 1 e 2, con il
loro livello e la loro fonte.

Due aggiunte rispetto a DA-PXREPL, che qui hanno senso e là no:

- **La mappa è del cluster, non del nodo**: i tre riquadri stanno affiancati e
  le divergenze si vedono a colpo d'occhio, che è esattamente ciò che la
  categoria «Coerenza del cluster» dice a parole.
- **Le VLAN di servizio sono marcate**: quella di corosync e quella dello
  storage portano un segno, perché un guest attestato lì è un problema e
  altrimenti sembra un tag come gli altri.

## 12 · La configurazione letta, resa leggibile

Oggi il portale rende **solo i rilievi**. L'inventario esiste, è archiviato e si
scarica come Markdown, ma a schermo non lo vede nessuno: chi apre la verifica
trova l'elenco dei difetti e nessun modo di rispondere alla domanda «ma com'è
fatto, questo cluster?».

- **Una linguetta «Configurazione»** accanto a «Rilievi» e «Tipologie», che rende
  l'inventario con lo stesso motore Markdown già in uso — quindi con le
  citazioni del manuale già collegate ai capitoli.
- **In testa, la mappa della rete** del punto 11; sotto, le sezioni che
  l'inventario ha già: cluster, nodi, hardware e dischi, storage, VM.
- **Un indice laterale** delle sezioni: l'inventario di questo cluster è di
  40 KB e senza indice non si naviga.
- Il file scaricabile **resta**: un Markdown mandato per posta non ha un portale
  dietro. A schermo vince la pagina, nel file vince il testo — è la stessa
  regola già adottata per le citazioni del manuale.

**Perché conta più di quanto sembri**: l'inventario è il documento che
sopravvive alla consegna. I rilievi si chiudono, la configurazione resta, ed è
quella che si rilegge fra sei mesi quando qualcosa cambia. Renderla solo come
file da scaricare la rende invisibile.

---

# Parte terza — 13 · Sicurezza

*Numerata 13 e non 11 perché 11 e 12 sono già prese: si aggiunge, non si
rinumera. È la stessa regola dei capitoli del manuale, per la stessa ragione —
i codici finiscono nei report già consegnati.*

## Da dove viene

La fonte più utile trovata è la **[Proxmox Hardening Guide di
HomeSecExplorer](https://github.com/HomeSecExplorer/Proxmox-Hardening-Guide)**
per PVE 9, costruita sul CIS Debian 13 Benchmark: è l'unica che per ogni
controllo dà **file, parametro e valore atteso**, quindi è traducibile in regole
deterministiche senza interpretazione. La documentazione ufficiale conferma le
regole di rete che avevamo già scritto, quasi con le stesse parole:

> «Corosync is sensitive to latency jitters… It's especially important **not to
> use a shared network for corosync and storage**»
> — [Separate Cluster Network](https://pve.proxmox.com/wiki/Separate_Cluster_Network)

Il manuale Domarc ha già la **Parte 18 «Sicurezza»**, con le sette misure di
§18.1. Il modulo nuovo si aggancia lì: le regole citano §18.x e §15.x, e le
sottosezioni che mancano si **aggiungono** in coda alla parte 18.

## ⚠️ La trappola da non ripetere

`/nodes/<n>/services` restituisce **un elenco fisso di servizi gestiti da PVE**
— `chrony`, `corosync`, `pveproxy`, `sshd`, `postfix`… — e **non conterrà mai**
`fail2ban`, `auditd` o `rsyslog` configurato per l'inoltro. Dedurne l'assenza
significherebbe scrivere a un cliente che non ha fail2ban quando magari ce l'ha
installato e attivo.

**Regola generale**: l'assenza da un elenco che non potrebbe contenerlo non è
un'assenza. Ogni controllo qui sotto dichiara **il comando che lo decide**, e
dove quel comando non c'è la regola non si scrive.

## 13a · Decidibili con i dati che già raccogliamo

| # | Regola | Campo che decide | Livello | Fonte | Domarc | IT&M | PX-NAS |
| --- | --- | --- | --- | --- | --- | --- | --- |
| SEC-01 | Secure Boot disattivo | `status.boot-info.secureboot` | ℹ️ | §18.1 | — (attivo) | **scatta** | **scatta** |
| SEC-02 | Interfaccia servita con certificato autofirmato | `certificati[].issuer` | 🟡 | §2.7 | **scatta** | **scatta** | **scatta** |
| SEC-03 | KSM attivo: deduplica memoria **fra macchine diverse** | `status.ksm.shared > 0` | 🟡 | §18.1, §8.2 | **scatta** (15,4 GB condivisi) | — | — |
| SEC-04 | corosync senza `secauth`: il traffico di cluster non è autenticato | `corosync_conf` | 🔴 | §3.2 | — (`on`) | — (`on`) | n/a |
| SEC-05 | Ceph senza `cephx` | `ceph_cfg auth_*_required` | 🔴 | §6.3 | n/a | — (tutti `cephx`) | n/a |
| SEC-06 | Ceph **non cifrato in transito** (`ms_encrypt` assente) | `ceph_cfg` | 🟡 | §6.3 | n/a | **scatta** | n/a |
| SEC-07 | FORWARD non in DROP con firewall acceso | `fw_options.policy_forward` | 🟡 | §15.2 | da leggere | da leggere | da leggere |
| SEC-08 | Firewall acceso e **UDP 5405-5412 non ammessi** sull'anello corosync: accendendolo si spacca il cluster | `fw_rules` + rete di corosync | 🔴 | §15.4, §3.3 | da calcolare | da calcolare | n/a |
| SEC-09 | `ksmtuned` attivo dove KSM dovrebbe restare spento | `services` | ℹ️ | §18.1 | da leggere | da leggere | da leggere |

## 13b · Richiedono un comando in più (tutti in sola lettura)

Sette comandi, nessuna scrittura. Ognuno serve più di una regola.

| Comando | Regole che apre |
| --- | --- |
| `sshd -T` | SEC-10, SEC-11, SEC-12 |
| `systemctl is-active fail2ban` + `fail2ban-client status` | SEC-13 |
| `pvesh get /access/users` e `/access/users/<id>/token` | SEC-14, SEC-15, SEC-16 |
| `auditctl -l` | SEC-17 |
| `cat /etc/rsyslog.d/*.conf` | SEC-18 |
| `cat /etc/apt/apt.conf.d/20auto-upgrades` | SEC-19 |
| `dpkg -l intel-microcode amd64-microcode` · `cat /sys/kernel/security/lockdown` · `findmnt /var/lib/vz` | SEC-20, SEC-21, SEC-22 |

| # | Regola | Livello | Fonte |
| --- | --- | --- | --- |
| SEC-10 | `PermitRootLogin yes`: root entra con la password da qualunque rete | 🔴 | §18.2 |
| SEC-11 | `PasswordAuthentication yes`: SSH accetta password | 🔴 | §18.2, §18.3 |
| SEC-12 | Forwarding SSH abilitato globalmente | 🟡 | §18.2 |
| SEC-13 | fail2ban assente, o jail `proxmox` non attivo sulla 8006 | 🟡 | §18.3 |
| SEC-14 | Nessun utente nominale: si amministra tutti come `root@pam` | 🟡 | §13.7, §18.1 |
| SEC-15 | Amministratori **senza secondo fattore** | 🔴 | §13.8, §13.9, §18.1 |
| SEC-16 | Token API **senza scadenza**, o con privilegi pieni | 🟡 | §13.12 |
| SEC-17 | Nessuna regola auditd su `/etc/pve`: chi cambia la configurazione non lascia traccia | 🟡 | §18.5 |
| SEC-18 | Log non inoltrati fuori dal nodo: chi entra può cancellarli | 🟡 | §17.5, §18.1 |
| SEC-19 | Aggiornamenti di sicurezza non automatici | 🟡 | §18.4, §19.1 |
| SEC-20 | Microcodice CPU non installato | 🟡 | §18.1 |
| SEC-21 | Kernel lockdown non attivo con Secure Boot acceso | ℹ️ | §18.1 |
| SEC-22 | `/var/lib/vz` non separato, o senza `nodev,nosuid` | ℹ️ | §2.2 |

## Due regole che NON scrivo, e perché

- **Cifratura del disco (LUKS)**: si decide all'installazione e non si cambia
  dopo. Dirlo a chi ha già l'impianto in esercizio è un rilievo che non si può
  agire — sta nel questionario di migrazione, non nel report.
- **«Il management non è raggiungibile da Internet»** — la prima delle sette
  misure. Da dentro il nodo non è verificabile: si vedono gli indirizzi, non chi
  ci arriva. Un rilievo che afferma di sapere una cosa che non può sapere è
  peggio di nessun rilievo.

## Cosa aggiungere al manuale

Le regole di 13a citano sezioni che esistono già. Di 13b ne mancano tre, da
**aggiungere in coda alla Parte 18** senza toccare i numeri esistenti:

- **§18.9 — Il secondo fattore, in pratica**: chi deve averlo, come si impone a
  un realm, cosa fare quando qualcuno si chiude fuori.
- **§18.10 — Token API: scopo, scadenza, rotazione**: il pezzo che serve
  a SEC-16 e che oggi §13.12 tratta come procedura, non come regola.
- **§18.11 — La linea di base di un nodo nuovo**: la lista che si applica a un
  nodo appena entrato nel cluster, perché — dice la guida CIS — è il punto in
  cui la configurazione diverge senza che nessuno se ne accorga.
