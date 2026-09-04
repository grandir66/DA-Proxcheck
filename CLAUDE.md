# CLAUDE.md — DA-Proxcheck

DA-Proxcheck: audit di sola lettura di un nodo o cluster Proxmox VE dal computer del tecnico, contro le best practice del manuale Domarc; produce inventario e report Markdown per il cliente.
**Non è:** un agente sui nodi (DA-PVE-Agent), né monitoraggio continuo (Proxreporter, DA-Zabbix). Non contiene le regole: le applica. Le regole vivono nel manuale (**DA-Proxmox-Docs**, `~/Progetti/manuali/proxmox`).

## Pointer

- Contesto (leggere per primo): [CONTEXT.md](CONTEXT.md)
- Storia per umani: [CHANGELOG.md](CHANGELOG.md)
- Accessi/device (git-ignored): `ACCESSI.md` — master nel vault. I cluster di prova sono elencati in `CONTEXT.md` §2
- Non esistono `.claude/skills/` né `.claude/rules/` in questo repo. ADR: [docs/adr/](docs/adr/)

## Stack vincolante

Python **3.7+ sul client**, **solo stdlib** (il tecnico non installa nulla; su Windows nemmeno `pip`). Sui nodi il `python3` di Debian 13. Il client `ssh` di sistema.
**Trappole:** niente `ControlMaster`/`ControlPath` (Windows non li ha) · niente `StrictHostKeyChecking=accept-new` su Windows (OpenSSH 7.7 non lo conosce) · `print` con emoji su console `cp1252` crasha: stdout è riconfigurato a UTF-8 in testa al file · i collector sono **stringhe** dentro il file: la loro sintassi si verifica con `scripts/controlla.sh`, non dall'import.

## Comandi essenziali

```bash
python3 audit-nodo.py --host <nodo-di-prova> --cliente Prova --output /tmp/p   # giro vero (indirizzi in ACCESSI.md)
python3 audit-nodo.py --da-json dati.json --cliente Prova --output /tmp/p   # rianalisi senza rete
bash scripts/controlla.sh                                                   # cancello pre-«fatto»
```

Non c'è runtime Linux né deploy: lo script si distribuisce copiando il file (o `git pull`) sul computer del tecnico.

## Zone del codice: dove un errore costa caro

| Zona | Regola | Dove |
| --- | --- | --- |
| 🔴 Rossa | solo con decisione esplicita + prova su un nodo reale | `COLLECTOR_NODO` e `COLLECTOR_CLUSTER` (girano come root sui nodi del cliente: **solo comandi di lettura**; `pveperf` è l'unica eccezione, opt-in) · `esegui_collector` (l'unica connessione SSH) |
| 🟡 Gialla | leggere prima la fonte della soglia | `controlla_*` e `PROFILI`: ogni soglia cita `guida-carico`, `manuale` o `Proxreporter`; una soglia senza fonte non si aggiunge |
| 🟢 Verde | liberi | report Markdown, menu, tabella, test |

## Regole anti-regressione

1. **Sola lettura sui nodi.** Nessun comando che scrive configurazione, nessun `apt install`. Se un dato richiede di installare qualcosa, il dato non si raccoglie. (Decisione 2026-09-03: è un audit dal cliente, non manutenzione.)
2. **La password non entra mai nel processo Python.** La chiede `ssh` sul tty. Su disco solo `hosts.json` (host, cliente, parametri) e `profili-<cluster>.json`. (Richiesta esplicita 2026-09-04: "non la password".)
3. **Ogni rilievo cita la fonte** (`fonte=` in `Esito.add`). Senza fonte è al massimo `INFO`.
4. **I formati dei comandi si verificano su un nodo reale prima di scrivere il parser.** `qm --help` esce 255, `pvesm status` ha 7 colonne, `corosync-cfgtool -s` è multiriga, SMART ha due formati: ogni parser sbagliato di questo repo nasceva da un formato immaginato. Test: `tests/test_parser.py` fissa i formati verificati.
5. **Report di prova mai in git**: `*_inventory.md`, `*_report.md`, `*.json` sono ignorati; contengono dati di infrastrutture di clienti.

## Quando cito un altro progetto

> Consuma le regole di **DA-Proxmox-Docs** (`~/Progetti/manuali/proxmox`; cosa fa e stato in `MAPPA-PROGETTI.html`, logica in `CONTEXT.md`) e le soglie hardware di **Proxreporter** (`~/Progetti/Proxreporter`, solo `hardware_monitor.py`, parte di lettura).

## Loop di apprendimento (fine sessione)

- Errore 2+ volte → regola qui sopra + test in `tests/` · decisione → ADR · fatto nuovo → `CONTEXT.md` · stato/trappola → `docs/scheda-mappa.md` poi `python3 ~/Progetti/genera_mappa.py`.
