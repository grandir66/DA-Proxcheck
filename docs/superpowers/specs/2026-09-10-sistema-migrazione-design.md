# Dalla verifica alla migrazione — il sistema completo

**Stato:** proposta, in revisione · **Data:** 2026-09-10
**Decisioni prese** (2026-09-10): la parte vCenter vive **dentro DA-Proxcheck**;
l'uscita è **un piano a ondate più una scheda per VM**; le proposte sono
**comandi da copiare, mai eseguiti**.

## Che cosa cambia

Oggi lo strumento risponde a una domanda: *«questo cluster Proxmox è messo
bene?»*. Il sistema completo ne risponde a tre, e le prime due esistono già a
metà:

| | Domanda | Uscita | Stato |
| --- | --- | --- | --- |
| **Rilievi** | Cosa non torna | il report di oggi | ✅ 57 regole |
| **Proposte** | Cosa impostare | i comandi da lanciare | ⚠️ le regole lo sanno, non lo scrivono |
| **Procedure** | Come si sposta la macchina X | piano a ondate + scheda per VM | ❌ da costruire |

## Il principio, di nuovo

Vale qui identico a quello delle regole: **nessuna procedura senza il dato che
la decide.** Un passo della checklist che il sistema non può verificare non
sparisce e non viene inventato: resta, marcato **da chiedere**. Un piano di
migrazione che dichiara verificato ciò che non ha guardato è peggio di nessun
piano — è l'errore che fa saltare la nottata.

---

## 1 · Il collettore vCenter

**Non si parte da zero.** `Clienti/GAO-DEBUG/gao-monitor/gao_vcenter.py` (72
righe) è un poller vCenter in esercizio: API REST (`POST /api/session`, poi
`vmware-api-session-id`), **solo GET**, nessuna dipendenza oltre `requests`.
Autenticazione, sessione e forma delle risposte sono già risolte contro un
vCenter vero. Da lì si prende il modo di connettersi; la raccolta va allargata.

Stessa filosofia di `audit-nodo.py`: **nessun agente installato, sola lettura,
la password non la vede né la salva lo strumento.**

### Che cosa si legge, e a quale domanda risponde

| Chiamata REST | Serve a |
| --- | --- |
| `/api/vcenter/vm` | l'inventario: nome, stato, vCPU, RAM |
| `/api/vcenter/vm/{vm}/hardware` | versione hardware virtuale, firmware **BIOS/EFI** |
| `/api/vcenter/vm/{vm}/hardware/disk` | numero e capacità dei dischi, tipo di backing → **rilevare RDM** |
| `/api/vcenter/vm/{vm}/hardware/ethernet` | schede, tipo (vmxnet3/e1000), MAC, rete |
| `/api/vcenter/vm/{vm}/tools` | VMware Tools: presente, versione, in esecuzione |
| `/api/vcenter/vm/{vm}/guest/identity` | sistema operativo visto dal guest |
| `/api/vcenter/vm/{vm}/guest/networking/interfaces` | IP e MAC reali (già fatto in GAO) |
| `/api/vcenter/vm/{vm}/snapshot` | **snapshot aperti**: bloccano l'import |
| `/api/vcenter/host` · `/api/vcenter/cluster` | host ESXi, versione, cluster, HA/DRS |
| `/api/vcenter/datastore` | datastore, tipo, spazio libero |
| `/api/vcenter/network` | port group e VLAN |

**Il grezzo si conserva**, come per Proxmox: il JSON della sorgente è il dato, e
si rianalizza con le regole di domani.

### La regola che vale doppio qui

§11.5.1 dice di **puntare agli host ESXi, non a vCenter**, per l'import. Il
collettore legge da vCenter — che è il posto giusto per *sapere* — ma la
procedura che genera deve nominare **l'host ESXi** su cui la VM gira in quel
momento, non il vCenter. Il dato per farlo sta in `/api/vcenter/vm`.

---

## 2 · Il questionario si accorcia

Il questionario di migrazione chiede oggi cose che vCenter sa rispondere. Ogni
domanda a cui risponde il sistema smette di essere una domanda — e una risposta
letta non può essere sbagliata per distrazione.

| Domanda di oggi | Chi risponde domani |
| --- | --- |
| Dischi RDM, independent o shared | **vCenter** (`hardware/disk`) |
| Fault Tolerance in uso | **vCenter** (`vm` + `cluster`) |
| Caratteri speciali nel nome del datastore | **vCenter** (`datastore`) |
| Credenziali sui singoli host ESXi | **vCenter** (`host`) — l'elenco; le credenziali restano una domanda |
| CPU dei nodi non identiche | **vCenter** (`host`) |
| BitLocker senza recovery key | resta una domanda: sta dentro il guest |
| Cluster applicativi | resta una domanda: è conoscenza del cliente |
| Licenze legate all'hardware | resta una domanda |
| Referente applicativo | resta una domanda |

**Il criterio è netto**: ciò che si vede dall'infrastruttura lo legge il
sistema; ciò che vive dentro il guest o nella testa delle persone resta una
domanda. E le domande che restano si fanno **solo sulle VM a cui si applicano**
— BitLocker si chiede sulle macchine Windows, non su tutte.

---

## 3 · La scheda per VM

È la checklist §11.14 **istanziata**, e ogni voce ha uno di tre stati:

- **✅ verificato** — il dato c'è e va bene. *«Firmware: UEFI»*
- **⚠️ da fare** — il dato c'è e dice che manca qualcosa. *«3 snapshot aperti dal 14 giugno: consolidare prima dell'import (§11.5.1)»*
- **❓ da chiedere** — il sistema non può saperlo. *«BitLocker: sospeso? (§11.6)»*
- **➖ non applicabile** — non riguarda questa macchina. Le voci Windows su una VM Linux spariscono invece di restare vuote.

La scheda porta in testa **il metodo scelto** dalla tabella §11.5, con il
perché. La scelta è deterministica:

| Condizione sulla VM | Metodo |
| --- | --- |
| caso ordinario | **Import wizard ESXi** (prima scelta) |
| grande e sensibile al downtime | Import wizard + **live-import**, con l'avvertenza di §11.5.2 |
| finestra strettissima, molto grande | **Attach & Move disk** |
| host ESXi non raggiungibile direttamente | `qm importovf` via `ovftool` |
| esiste già un backup con integrazione Proxmox | **Restore da backup** |
| sorgente non VMware, o dischi problematici | Clonezilla |

I criteri numerici (che cosa è «grande», che cosa è «finestra stretta») vengono
dai dati e dalle risposte del questionario, e **si dichiarano nella scheda**:
«scelto live-import perché 1,8 TB su una finestra di 2 ore».

---

## 4 · Il piano a ondate

Non è un ordinamento inventato: sono i vincoli del manuale applicati
all'inventario vero.

- **§11.5.3 — mai più di 4 dischi in import contemporaneo.** È il vincolo che
  dimensiona ogni ondata: quattro VM da un disco, o una VM da quattro. Il piano
  conta i dischi, non le macchine.
- **§11.8 — le ondate**: prima le innocue (test, template, macchine senza
  dipendenze), poi gli applicativi, i database per ultimi.
- **I cluster applicativi si spostano insieme** — o restano in piedi durante lo
  spostamento — e questo il sistema non lo deduce: viene dal questionario.
- **I domain controller non si spostano prima del proprio secondario**
  (§9.1, §21.6 sull'USN rollback).

Ogni ondata dichiara: quali macchine, quanti dischi in totale, la finestra
stimata, e **che cosa deve essere vero prima di cominciare**.

---

## 5 · Le proposte diventano comandi

Ogni rilievo agibile produce il comando esatto, mai eseguito:

```
VM 111 (Stormshield-EVA-VirtIO) @PX-01
  qm set 111 --balloon 0                 # profilo Rete: la RAM non si riprende a caldo (§9.5)
  qm set 111 --scsihw virtio-scsi-single # presupposto degli IO thread (§8.3)
```

**Non tutti i rilievi sono agibili**, e la distinzione va tenuta: «VLAN 20 ha
reti diverse fra i nodi» non ha un comando — ha una decisione. Quelli che hanno
un comando lo mostrano; gli altri restano rilievi, e non si finge il contrario.

Lo strumento **resta in sola lettura**. È la proprietà che oggi lo rende
accettabile sull'impianto di un cliente, e non si spende per comodità.

---

## Cosa il sistema NON fa

- **Non esegue niente**, né su Proxmox né su vSphere. Nessuna scrittura, nessun
  import lanciato, nessuna VM toccata.
- **Non entra nei guest.** Tutto ciò che sta dentro la macchina — BitLocker,
  fstab, servizi applicativi — resta una domanda al cliente.
- **Non decide le ondate da solo** dove servono conoscenze che non ha: propone
  un ordine e dichiara su cosa si è basato.

## Ordine dei lavori

1. **Le proposte come comandi** — nessuna raccolta nuova, valore immediato sul
   lavoro di oggi, e mette a punto il formato prima che serva alle procedure.
2. **Il collettore vCenter** — connessione (dal modello GAO), inventario, e il
   grezzo conservato. Si collauda su un vCenter vero prima di scriverci sopra
   qualunque regola.
3. **Le regole di pre-migrazione** sulla sorgente: snapshot aperti, RDM, Fault
   Tolerance, hardware virtuale vecchio, VMware Tools assenti. Sono la parte che
   *toglie domande al questionario*.
4. **La scheda per VM** — la checklist istanziata con i quattro stati.
5. **Il piano a ondate** — con i vincoli di §11.5.3 e §11.8.
6. **Il portale**: la sorgente accanto alla destinazione, e il piano scaricabile.

Ogni lotto chiude con `scripts/controlla.sh` verde e con una prova su dati veri
— per i lotti 2 e 3 serve un vCenter di prova: senza, non si dichiara fatto.
