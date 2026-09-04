#!/usr/bin/env python3
"""Estrae dal manuale operativo Proxmox VE di Domarc i passaggi che l'audit
cita, e li scrive in `fonti_manuale.py`. Così il report riporta la regola per
esteso: chi lo legge non deve avere il manuale sottomano.

    python3 strumenti/estrai-fonti.py                     # rigenera fonti_manuale.py
    python3 strumenti/estrai-fonti.py --verifica          # il generato è ancora allineato?
    python3 strumenti/estrai-fonti.py --controlla         # ogni citazione dell'audit risolve?

Il manuale si cerca in `~/Progetti/manuali/proxmox` (repo DA-Proxmox-Docs),
oppure dove dice `--manuale` / la variabile `DA_PROXMOX_DOCS`.

DUE REGOLE CHE NON SI TOCCANO

1. **Non si copia il manuale intero.** Qui finiscono solo i passaggi citati da
   un rilievo: sono le regole che questo strumento applica, e senza il loro
   testo un report dice "vedi §8.3" a chi il manuale non ce l'ha. Il manuale
   resta il documento di Domarc e vive nel suo repository.
2. **I blocchi `[INTERNO]` non escono mai.** Nel manuale marcano valutazioni
   Domarc, casi di clienti e riferimenti a progetti interni; questo repository
   è pubblico. Il paragrafo che li contiene viene scartato, e
   `tests/test_fonti.py` verifica che nel generato non ne resti traccia.

Il file generato non si modifica a mano: porta file e riga di origine, e
`--verifica` fallisce se il manuale è cambiato sotto (il cancello lo esegue
quando il manuale è presente, lo salta quando non c'è).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

QUI = Path(__file__).resolve().parent.parent
GENERATO = QUI / "fonti_manuale.py"
AUDIT = QUI / "audit-nodo.py"
MANUALE_DEFAULT = Path(os.environ.get("DA_PROXMOX_DOCS", Path.home() / "Progetti" / "manuali" / "proxmox"))

MAX_CARATTERI = 1600          # oltre, si taglia a fine paragrafo: è una citazione, non un capitolo
# un'ancora è `§8.3` o `§8.3 › Cache mode`: il titolo della sottosezione può
# contenere virgole («Shares, hugepages, KSM»), quindi si ferma solo alla
# chiusura della stringa (virgolette, apice, backtick, fine riga) o su un
# altro §. Lo stesso regex sta in audit-nodo.py: le due copie vanno tenute
# uguali, e `tests/test_parser.py` verifica il comportamento di quella.
RE_ANCORA = re.compile(r"§[0-9A-Z]+(?:\.[0-9]+)?(?:\s*›\s*[^\"'`§\n]+)?")
RE_PARTE = re.compile(r"^#\s+(?:PARTE|APPENDIC[EI])\s*([0-9A-Z]*)\s*(?:—\s*(.*))?$")
RE_SEZIONE = re.compile(r"^##\s+([0-9]+\.[0-9]+|[A-Z]\.[0-9]+|[0-9]+\.[A-Z])\s+(.*)$")
RE_SOTTO = re.compile(r"^###\s+(.*)$")
RE_SCENARI = re.compile(r"\s*\*\*\[[A-D ]+\]\*\*\s*$")

# Fonti che non stanno nel manuale: la ragione della soglia si scrive qui, una
# volta, invece di lasciare al lettore una sigla senza spiegazione.
FONTI_ESTERNE = {
    "Proxreporter hardware_monitor.py": {
        "titolo": "Soglie hardware di Proxreporter",
        "origine": "Proxreporter, il tool di reporting Domarc in produzione — modulo hardware_monitor.py",
        "testo": "Le soglie su SMART, ECC e RAID sono quelle che Proxreporter applica da anni sul parco "
                 "Domarc, non valori scelti qui: salute SMART diversa da PASSED, settori riallocati o "
                 "pending, errori ECC non corretti, array mdadm degradato.\n\n"
                 "Due distinzioni pagate sul campo: la tabella degli attributi ATA ha una colonna "
                 "`WHEN_FAILED`, che contiene la parola FAILED su ogni disco sano — la salute si legge "
                 "dalla frase «self-assessment test result», non cercando una sottostringa. E le soglie "
                 "di temperatura 45/55 °C valgono per i dischi SATA: un NVMe lavora normalmente più "
                 "caldo, quindi su NVMe la temperatura è informativa e il segnale autorevole è il bit "
                 "Critical Warning del firmware.",
    },
    "API disks/list": {
        "titolo": "Usura dei dischi dall'API di Proxmox",
        "origine": "Proxmox VE, endpoint /nodes/<nodo>/disks/list",
        "testo": "L'API riporta `wearout` come **vita residua** in percentuale: 92 significa che il disco "
                 "ha consumato l'8% della propria resistenza in scrittura. L'audit mostra l'usura "
                 "(100 − wearout) perché è il numero che si guarda quando si decide una sostituzione. "
                 "Verificato contro `smartctl`: wearout 92 corrisponde a «Percentage Used: 8%».",
    },
    "blockstat": {
        "titolo": "Latenze di I/O dalle statistiche QEMU",
        "origine": "Proxmox VE, /nodes/<nodo>/qemu/<vmid>/status/current, campo blockstat",
        "testo": "QEMU conta, per ogni disco virtuale, operazioni e tempo totale dall'avvio della VM. "
                 "Dividendo il tempo per il numero di operazioni si ottiene la latenza media di lettura, "
                 "scrittura e flush. È una media dall'accensione, non una misura istantanea: dice se lo "
                 "storage è lento in generale, non se lo è stato cinque minuti fa.\n\n"
                 "Il flush è il segnale più parlante: sotto i 5 ms su SSD, oltre i 20 ms indica uno "
                 "storage saturo o senza cache protetta. Le operazioni fallite (`failed_*_operations`) "
                 "non sono mai normali.",
    },
    "rrddata": {
        "titolo": "Andamento dell'ultima ora (RRD)",
        "origine": "Proxmox VE, /nodes/<nodo>/rrddata e /qemu/<vmid>/rrddata, timeframe hour",
        "testo": "Proxmox conserva in un database circolare CPU, I/O wait, memoria, rete e le metriche di "
                 "pressione del kernel (PSI). L'audit ne legge l'ultima ora e ne riporta media e massimo: "
                 "servono a distinguere un valore alto adesso da un carico che è alto sempre.\n\n"
                 "La pressione PSI misura il tempo in cui almeno un processo ha aspettato una risorsa. "
                 "Una PSI I/O che si tiene sopra il 10% dice che il collo di bottiglia è lo storage, "
                 "anche quando la CPU sembra tranquilla.",
    },
    "guest agent": {
        "titolo": "Dati letti dentro il guest",
        "origine": "QEMU Guest Agent, comandi get-osinfo / get-fsinfo / network-get-interfaces / get-time",
        "testo": "Con l'agent attivo l'hypervisor può chiedere al sistema operativo ospite il proprio "
                 "nome, l'occupazione dei filesystem, gli indirizzi IP e l'ora. Sono le uniche "
                 "informazioni che dall'esterno non si vedono: un disco virtuale pieno all'80% può "
                 "contenere un filesystem pieno al 99%.\n\n"
                 "Un comando non supportato dal guest (per esempio su appliance non standard) risponde "
                 "con un errore, non con un dato: l'audit lo tratta come informazione mancante.",
    },
    "pveperf": {
        "titolo": "Misure di pveperf",
        "origine": "Proxmox VE, comando pveperf — soglie indicative dal forum Proxmox e dall'esperienza Domarc",
        "testo": "`pveperf` misura la CPU, la lettura sequenziale, il tempo di seek e soprattutto i "
                 "**fsync al secondo**: quante scritture sincronizzate su disco il sistema regge. È il "
                 "numero che conta per macchine virtuali e database, perché ogni transazione ne chiede "
                 "una.\n\n"
                 "Valori indicativi: sotto 200 il sistema è inadatto a VM con database; fra 200 e 1000 è "
                 "accettabile per carichi generici; sopra 1000 è buono. Non sono soglie ufficiali "
                 "Proxmox: sono i valori con cui si ragiona in campo, e vanno letti insieme al tipo di "
                 "storage.\n\n"
                 "`pveperf` misura anche la risoluzione DNS: un DNS interno che risponde in più di mezzo "
                 "secondo rallenta login, interfaccia e ogni comando del nodo, ed è quasi sempre un "
                 "resolver sbagliato o irraggiungibile.",
    },
}


def pulisci_titolo(t: str) -> str:
    return RE_SCENARI.sub("", t).strip()


def paragrafi(righe: list) -> list:
    """Blocchi separati da riga vuota, tenendo insieme i blocchi di codice."""
    blocchi, corrente, in_codice = [], [], False
    for r in righe:
        if r.strip().startswith("```"):
            in_codice = not in_codice
            corrente.append(r)
            continue
        if not r.strip() and not in_codice:
            if corrente:
                blocchi.append(corrente); corrente = []
        else:
            corrente.append(r)
    if corrente:
        blocchi.append(corrente)
    return blocchi


def testo_pulito(righe: list) -> tuple:
    """Testo citabile: via i blocchi [INTERNO], via le note di rimando interne,
    taglio a fine paragrafo se troppo lungo. Torna (testo, troncato)."""
    fuori = []
    for blocco in paragrafi(righe):
        unito = "\n".join(blocco)
        if "[INTERNO]" in unito:
            continue                      # valutazione interna: non esce da qui
        fuori.append(unito.rstrip())
    testo, troncato = "", False
    for blocco in fuori:
        if testo and len(testo) + len(blocco) + 2 > MAX_CARATTERI:
            troncato = True
            break
        testo = (testo + "\n\n" + blocco) if testo else blocco
        if len(testo) >= MAX_CARATTERI:
            troncato = True
            break
    return testo.strip(), troncato


def leggi_manuale(radice: Path) -> tuple:
    """Legge manuale/*.md e torna (sezioni, parti). Struttura del manuale:
    `# PARTE 8 — titolo`, `## 8.3 Disco`, `### Cache mode`."""
    cartella = radice / "manuale"
    if not cartella.is_dir():
        raise SystemExit(f"Non trovo il manuale in {cartella}. Indicare il repo con --manuale o DA_PROXMOX_DOCS.")
    sezioni, parti = {}, {}
    for f in sorted(cartella.glob("*.md")):
        rel = f"manuale/{f.name}"
        parte_id = None
        sez = sotto = None
        for n, riga in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            m = RE_PARTE.match(riga)
            if m:
                parte_id = m.group(1) or f.stem.split("-")[0].lstrip("0")
                parti[parte_id] = {"titolo": pulisci_titolo(m.group(2) or ""), "file": rel, "riga": n, "righe": []}
                sez = sotto = None
                continue
            m = RE_SEZIONE.match(riga)
            if m:
                sez = {"numero": m.group(1), "titolo": pulisci_titolo(m.group(2)), "file": rel, "riga": n,
                       "parte": parte_id, "righe": [], "sotto": {}}
                sezioni[m.group(1)] = sez
                sotto = None
                continue
            m = RE_SOTTO.match(riga)
            if m and sez is not None:
                sotto = {"titolo": pulisci_titolo(m.group(1)), "file": rel, "riga": n, "righe": []}
                sez["sotto"][sotto["titolo"]] = sotto
                continue
            if sotto is not None:
                sotto["righe"].append(riga)
                sez["righe"].append(riga)
            elif sez is not None:
                sez["righe"].append(riga)
            elif parte_id is not None:
                parti[parte_id]["righe"].append(riga)
    return sezioni, parti


def versione_manuale(radice: Path) -> dict:
    """Edizione e data di verifica, dal manuale stesso: un'affermazione tecnica
    senza data, fra un anno, è indistinguibile da un'affermazione sbagliata."""
    d = {"nome": "Manuale operativo Proxmox VE — Domarc", "versione": "?", "verificato": "?", "repo": "DA-Proxmox-Docs"}
    uso = radice / "manuale" / "00-uso.md"
    if uso.is_file():
        testo = uso.read_text(encoding="utf-8")
        m = re.search(r"##\s*0\.2\s*Versioni di riferimento\s*—\s*verificate il\s*(\S+)", testo)
        if m:
            d["verificato"] = m.group(1)
        m = re.search(r"\|\s*Proxmox VE\s*\|\s*([^|]+?)\s*\|", testo)
        if m:
            d["prodotti"] = "Proxmox VE " + m.group(1).strip()
    readme = radice / "README.md"
    if readme.is_file():
        m = re.search(r"\*\*Manuale operativo Proxmox VE\*\*[^|]*\|\s*\*\*([0-9.]+)\*\*", readme.read_text(encoding="utf-8"))
        if m:
            d["versione"] = m.group(1)
    return d


def ancore_citate(codice: str) -> list:
    """Le ancore che l'audit cita davvero, dedotte dal suo codice."""
    trovate = {a.strip().rstrip(",;.") for a in RE_ANCORA.findall(codice)}
    return sorted(trovate, key=lambda a: (len(a.split("›")), a))


def risolvi(ancora: str, sezioni: dict, parti: dict) -> dict:
    """`§8.3 › Cache mode` → la sottosezione; `§8.3` → la sezione; `§8` → la parte."""
    corpo = ancora.lstrip("§").strip()
    sotto_titolo = None
    if "›" in corpo:
        corpo, sotto_titolo = [x.strip() for x in corpo.split("›", 1)]
    sez = sezioni.get(corpo)
    if sez and sotto_titolo:
        s = sez["sotto"].get(sotto_titolo)
        if not s:
            vicini = ", ".join(sez["sotto"]) or "(nessuna sottosezione)"
            raise KeyError(f"{ancora}: sottosezione inesistente. In §{corpo} ci sono: {vicini}")
        testo, troncato = testo_pulito(s["righe"])
        return {"titolo": f"§{corpo} {sez['titolo']} › {s['titolo']}", "file": s["file"], "riga": s["riga"],
                "parte": f"Parte {sez['parte']}" if sez.get("parte") else "", "testo": testo, "troncato": int(troncato)}
    if sez:
        testo, troncato = testo_pulito(sez["righe"])
        return {"titolo": f"§{corpo} {sez['titolo']}", "file": sez["file"], "riga": sez["riga"],
                "parte": f"Parte {sez['parte']}" if sez.get("parte") else "", "testo": testo, "troncato": int(troncato)}
    parte = parti.get(corpo)
    if parte:
        righe = list(parte["righe"])
        if not "".join(righe).strip():        # parte senza introduzione: si elencano le sue sezioni
            righe = [f"- **§{n} {s['titolo']}**" for n, s in sezioni.items() if s.get("parte") == corpo]
            righe = ["Le sezioni di questa parte del manuale:", ""] + righe
        testo, troncato = testo_pulito(righe)
        return {"titolo": f"Parte {corpo} — {parte['titolo']}", "file": parte["file"], "riga": parte["riga"],
                "parte": f"Parte {corpo}", "testo": testo, "troncato": int(troncato)}
    raise KeyError(f"{ancora}: non esiste nel manuale (né come sezione né come parte)")


def genera(radice: Path) -> str:
    sezioni, parti = leggi_manuale(radice)
    codice = AUDIT.read_text(encoding="utf-8")
    fonti, errori = {}, []
    for ancora in ancore_citate(codice):
        try:
            fonti[ancora] = risolvi(ancora, sezioni, parti)
        except KeyError as e:
            errori.append(str(e))
    if errori:
        raise SystemExit("Citazioni che non risolvono nel manuale:\n  - " + "\n  - ".join(errori))
    for chiave, v in FONTI_ESTERNE.items():
        fonti[chiave] = {"titolo": v["titolo"], "file": "", "riga": 0, "parte": "",
                         "origine": v["origine"], "testo": v["testo"], "troncato": 0}
    manuale = versione_manuale(radice)
    manuale["estratto_il"] = date.today().isoformat()
    intestazione = f'''"""Le regole citate dall'audit, con il testo per esteso.

GENERATO da `strumenti/estrai-fonti.py` — non modificare a mano: le modifiche
si perdono alla prossima estrazione, e il testo non corrisponderebbe più al
manuale da cui viene.

Contiene SOLO i passaggi che un rilievo cita, presi dal manuale operativo
Proxmox VE di Domarc ({manuale["repo"]}). Serve a far bastare il report: chi
lo legge vede la regola, non un rimando a un documento che non ha.

I blocchi che il manuale marca come interni — valutazioni Domarc, casi di
clienti — non entrano qui: questo repository è pubblico. Lo verifica
`tests/test_fonti.py`, che nel testo estratto cerca quel marcatore.
"""
'''
    corpo = "MANUALE = " + json.dumps(manuale, ensure_ascii=False, indent=1) + "\n\n"
    corpo += "FONTI = " + json.dumps(fonti, ensure_ascii=False, indent=1) + "\n"
    return intestazione + "\n" + corpo


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manuale", type=Path, default=MANUALE_DEFAULT, help="repo del manuale (DA-Proxmox-Docs)")
    ap.add_argument("--verifica", action="store_true", help="non scrive: fallisce se il generato non è allineato al manuale")
    ap.add_argument("--controlla", action="store_true", help="non legge il manuale: verifica che ogni citazione dell'audit abbia un testo")
    args = ap.parse_args()

    if args.controlla:
        try:
            sys.path.insert(0, str(QUI))
            from fonti_manuale import FONTI
        except ImportError:
            print("fonti_manuale.py assente: il report citerà le regole senza riportarle.", file=sys.stderr)
            return 1
        mancanti = [a for a in ancore_citate(AUDIT.read_text(encoding="utf-8")) if a not in FONTI]
        if mancanti:
            print("Citazioni senza testo (rigenerare con estrai-fonti.py):\n  - " + "\n  - ".join(mancanti), file=sys.stderr)
            return 1
        print(f"  {len(FONTI)} regole con il testo, tutte le citazioni risolvono")
        return 0

    nuovo = genera(args.manuale)
    if args.verifica:
        vecchio = GENERATO.read_text(encoding="utf-8") if GENERATO.is_file() else ""
        # la data di estrazione cambia ogni giorno e non è una differenza di contenuto
        norm = lambda t: re.sub(r'"estratto_il": "[^"]*"', "", t)
        if norm(nuovo) != norm(vecchio):
            print("fonti_manuale.py non è allineato al manuale: rigenerare con estrai-fonti.py", file=sys.stderr)
            return 1
        print("  fonti_manuale.py allineato al manuale")
        return 0
    GENERATO.write_text(nuovo, encoding="utf-8")
    n_manuale = sum(1 for k in json.loads(nuovo.split("FONTI = ", 1)[1]) if k.startswith("§"))
    print(f"Scritto {GENERATO.name}: {n_manuale} passaggi dal manuale + {len(FONTI_ESTERNE)} fonti esterne")
    return 0


if __name__ == "__main__":
    sys.exit(main())
