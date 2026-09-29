#!/usr/bin/env python3
"""Dalla raccolta vSphere all'assessment di migrazione.

Legge il JSON prodotto da `raccogli-vcenter.py` e produce due cose:

1. **I rilievi sulla sorgente** — quello che va sistemato *prima* di importare.
   Sono le domande che il questionario faceva al cliente e che ora si leggono
   dall'infrastruttura: snapshot aperti, RDM, hardware virtuale vecchio, Tools
   assenti, ISO montate.
2. **Il metodo di migrazione per ogni macchina**, scelto con la tabella del
   manuale §11.5 applicata ai dati veri, con il perché dichiarato.

Vale qui il principio delle regole di Proxmox: **nessun rilievo senza il campo
che lo decide**, e nessuna procedura che dichiari verificato ciò che non ha
guardato. Quello che vive dentro il guest — BitLocker, fstab, servizi
applicativi — resta una domanda al cliente, e si chiede solo alle macchine a
cui si applica.

    python3 analizza-vcenter.py --json vcenter.json --cliente "Nome" --output ~/report
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

QUI = Path(__file__).resolve().parent


def strumento():
    """Il motore di `audit-nodo.py`: livelli, Esito, tabelle, testo delle regole.

    Si importa invece di ricopiarlo: due motori di report divergono, e il
    giorno che divergono i due documenti dello stesso cliente si contraddicono.
    Il nome ha un trattino, quindi non si importa con `import`.
    """
    # Due disposizioni diverse, entrambe legittime: nel repository questo file
    # sta in `strumenti/` e il motore nella cartella sopra; una volta pubblicato
    # nel portale stanno **affiancati** in `contenuti/script/`. Cercare in un
    # posto solo funziona in prova e fallisce in esercizio — visto il 2026-09-10,
    # con la raccolta arrivata e l'analisi no.
    f = next((c for c in (QUI / "audit-nodo.py", QUI.parent / "audit-nodo.py") if c.is_file()), None)
    if f is None:
        raise SystemExit("non trovo audit-nodo.py: dev'essere accanto a questo file o nella cartella superiore")
    spec = importlib.util.spec_from_file_location("audit_nodo", f)
    m = importlib.util.module_from_spec(spec)
    sys.modules["audit_nodo"] = m
    spec.loader.exec_module(m)
    return m


an = strumento()
BLOCCANTE, ATTENZIONE, INFO = an.BLOCCANTE, an.ATTENZIONE, an.INFO

# Un disco su `NOME-000001.vmdk` gira su un delta: la macchina ha snapshot
# aperti. L'API REST di vSphere 8 non li espone (404 su /snapshot), e questo è
# il modo di saperlo senza chiamate in più.
RE_DELTA = re.compile(r"-\d{6}\.vmdk$", re.I)

# Le macchine di servizio di vSphere Cluster Services: le crea e ricrea vCenter,
# non si migrano mai. Restano nel grezzo, spariscono dai conteggi.
RE_SERVIZIO = re.compile(r"^vCLS[-_]", re.I)

# Sotto questa versione l'hardware virtuale è più vecchio di ESXi 6.7.
HW_MINIMO = 14

GB = 1024 ** 3


def macchine(dati: dict) -> list:
    """Le macchine da migrare: quelle di servizio non contano."""
    fuori = []
    for vid, v in (dati.get("vm") or {}).items():
        i = v.get("intera") or {}
        nome = i.get("name") or (v.get("lista") or {}).get("name") or vid
        if RE_SERVIZIO.match(nome):
            continue
        fuori.append({"id": vid, "nome": nome, "host": v.get("host"), "intera": i,
                      "tools": v.get("tools") if isinstance(v.get("tools"), dict) else {},
                      "acceso": (v.get("lista") or {}).get("power_state") == "POWERED_ON"})
    return sorted(fuori, key=lambda x: x["nome"].lower())


def dischi(m: dict) -> list:
    return list(((m["intera"].get("disks")) or {}).values())


def capacita_gb(m: dict) -> int:
    return round(sum((d.get("capacity") or 0) for d in dischi(m)) / GB)


def versione_hw(m: dict) -> int | None:
    v = str(((m["intera"].get("hardware")) or {}).get("version") or "")
    n = re.search(r"(\d+)", v)
    return int(n.group(1)) if n else None


def su_delta(m: dict) -> list:
    return [(d.get("backing") or {}).get("vmdk_file", "") for d in dischi(m)
            if RE_DELTA.search((d.get("backing") or {}).get("vmdk_file", ""))]


def controlla(m: dict, esito) -> None:
    """I rilievi sulla macchina sorgente. Ognuno nomina il campo che lo decide."""
    A = f"VM {m['nome']}"
    i = m["intera"]

    delta = su_delta(m)
    if delta:
        esito.add(BLOCCANTE, A, f"Gira su delta di snapshot ({len(delta)} disco/i): vanno consolidati "
                                f"prima dell'import, o la copia rallenta enormemente.", "manuale §11.5.1")

    for d in dischi(m):
        tipo = str((d.get("backing") or {}).get("type") or "")
        if "RDM" in tipo or "RAW" in tipo:
            esito.add(BLOCCANTE, A, f"Disco in {tipo}: un RDM non si importa come gli altri, "
                                    f"va deciso caso per caso.", "manuale §11.2")

    hw = versione_hw(m)
    if hw is not None and hw < HW_MINIMO:
        esito.add(ATTENZIONE, A, f"Hardware virtuale VMX_{hw:02d}, più vecchio di ESXi 6.7: "
                                 f"driver e dispositivi datati da verificare prima di spostarla.", "manuale §11.6")

    stato_tools = str(m["tools"].get("version_status") or "")
    if stato_tools == "NOT_INSTALLED":
        esito.add(ATTENZIONE, A, "VMware Tools non installate: niente spegnimento pulito, e i driver "
                                 "VirtIO vanno messi a mano prima di spostarla.", "manuale §11.6")

    for c in ((i.get("cdroms")) or {}).values():
        if (c.get("backing") or {}).get("type") == "ISO_FILE":
            esito.add(ATTENZIONE, A, "Ha una ISO montata da datastore: va smontata, o l'import se la porta "
                                     "dietro e può fallire.", "manuale §11.6")
            break

    windows = "WINDOWS" in str(i.get("guest_OS") or "").upper()
    for a in ((i.get("scsi_adapters")) or {}).values():
        if str(a.get("type") or "").startswith("LSI") and windows:
            esito.add(ATTENZIONE, A, f"Controller {a.get('type')} su Windows: i driver VirtIO SCSI vanno "
                                     f"iniettati nel guest PRIMA di spegnerlo, o non riparte.", "manuale §11.6")
            break

    if (i.get("boot") or {}).get("type") == "EFI":
        esito.add(INFO, A, "Firmware EFI: sulla macchina di destinazione deve corrispondere (OVMF).",
                  "manuale §11.7")

    if not m["acceso"]:
        esito.add(INFO, A, "Spenta al momento della raccolta: da confermare se va migrata o dismessa.",
                  "manuale §11.2")


def metodo(m: dict) -> tuple:
    """Il metodo di §11.5 per questa macchina, e il perché.

    La scelta è deterministica e il criterio si dichiara: chi legge deve poter
    dire «no, questa la faccio diversamente» sapendo su cosa mi sono basato.
    """
    gb = capacita_gb(m)
    note = []
    if su_delta(m):
        note.append("consolidare gli snapshot prima, qualunque metodo")
    if gb >= 2000:
        return "Attach & Move disk", f"{gb} GB: oltre le due migliaia la copia intera è una finestra troppo lunga", note
    if gb >= 500:
        return "Import wizard + live-import", f"{gb} GB: la copia intera a macchina spenta durerebbe troppo", note
    return "Import wizard ESXi", f"{gb} GB: caso ordinario, prima scelta del manuale", note


# ════════════════════ il questionario, precompilato dai dati ════════════════════
# Ogni domanda a cui l'infrastruttura risponde smette di essere una domanda — e
# una risposta LETTA non può essere sbagliata per distrazione.
#
# Il confine è netto e non si sposta: **si compila solo ciò che si è visto**.
# Quello che vive dentro il guest (BitLocker, fstab, i servizi applicativi) o
# nella testa delle persone (i cluster applicativi, le licenze legate
# all'hardware, chi autorizza un fermo) resta una domanda, e si chiede.
#
# Ogni valore precompilato porta la propria origine: chi legge deve poter
# distinguere «lo abbiamo letto» da «ce l'hanno detto».

def _elenco(nomi: list, vuoto: str) -> str:
    return ", ".join(sorted(nomi)) if nomi else vuoto


def precompila(dati: dict) -> dict:
    """Le risposte che la raccolta vSphere sa dare, con la loro origine."""
    mm = macchine(dati)
    campi, origine = {}, {}

    def metti(chiave, valore, come):
        if valore not in (None, ""):
            campi[chiave] = valore
            origine[chiave] = come

    # Le chiavi e i valori seguono il MODELLO del questionario, non il buon
    # senso: un campo composto si scrive `id.sotto` (`esxi_host_numero.numero`),
    # un select Si'/No vuole proprio «Sì» e «No», e una risposta negativa deve
    # essere ESATTAMENTE «Nessuna» — il questionario riconosce il «no» con
    # /^(no|nessun[ao]?|niente)$/. «Nessuna: tutti i dischi sono VMDK» sarebbe
    # stata letta come un elenco di RDM, cioe' un blocco. Visto il 2026-09-11
    # sullo schermo, non nei test: i test provavano il file, non il modello.
    host = dati.get("host") or []
    metti("esxi_host_numero.numero", str(len(host)), f"{len(host)} host letti da vCenter")

    tot_gb = sum(capacita_gb(m) for m in mm)
    metti("vm_numero_tb.vm", str(len(mm)), "conteggio delle macchine (escluse le vCLS)")
    metti("vm_numero_tb.tb", f"{tot_gb / 1024:.1f}", "somma dei dischi dalla raccolta")

    con_delta = [m["nome"] for m in mm if su_delta(m)]
    metti("sp_snapshot", _elenco(con_delta, "Nessuna"),
          "riconosciuti dal nome del file di backing (…-000001.vmdk)")

    con_rdm = [m["nome"] for m in mm
               for d in dischi(m) if "RDM" in str((d.get("backing") or {}).get("type") or "")]
    metti("sp_rdm", _elenco(sorted(set(con_rdm)), "Nessuna"), "tipo di backing di ogni disco")

    # I datastore col nome «difficile» bloccano l'import (§11.13).
    brutti = [d.get("name", "") for d in (dati.get("datastore") or [])
              if re.search(r"[+&%#'\"]|\s{2,}", str(d.get("name") or ""))]
    metti("caratteri_speciali_vm", _elenco(brutti, "Nessuno"),
          f"{len(dati.get('datastore') or [])} datastore letti da vCenter")

    vsan = [d.get("name", "") for d in (dati.get("datastore") or [])
            if str(d.get("type") or "").upper() == "VSAN"]
    metti("feat_vsan", "Sì" if vsan else "No", "tipo dei datastore")
    if vsan:
        metti("vsan_vm", _elenco(vsan, ""), "datastore vSAN: le VM che ci stanno sopra vanno spostate prima")

    return {"campi": campi, "origine": origine,
            "raccolto_il": dati.get("raccolto_il"),
            "sorgente": dati.get("host_vcenter", "")}


# ══════════════════════════════ il piano a ondate ══════════════════════════════
# Non è un ordinamento inventato: sono i vincoli del manuale applicati
# all'inventario vero. §11.5.3 dice «mai più di 4 dischi importati
# contemporaneamente», §11.8 dice in che ordine si spostano le cose.

DISCHI_IN_PARALLELO = 4      # manuale §11.5.3, limite duro dell'API ESXi
DISCHI_PER_ONDATA = 12       # tre turni da quattro: una sessione di lavoro
GB_PROGETTO_A_SE = 2000      # oltre, la macchina si pianifica da sola

# Indizi nel NOME e nel sistema operativo. Sono **proposte**, non rilievi: la
# stessa regola dello strumento Proxmox — i nomi suggeriscono, non decidono.
# Chi legge il piano può spostare una macchina di classe, e deve poterlo fare
# sapendo perché ce l'abbiamo messa.
INDIZI_CRITICA = re.compile(r"sql|\bdb\b|oracle|postgres|maria|mongo|exch|\bdc\b|domain", re.I)
INDIZI_PROVA = re.compile(r"tmpl|templ|test|prova|demo|\blab\b|clone|\bold\b|dismes", re.I)


def classe(m: dict) -> tuple:
    """In quale gruppo va spostata: (ordine, etichetta, perché).

    Tre gruppi, nell'ordine di §11.8: prima le innocue, poi le ordinarie, i
    database e i domain controller per ultimi.
    """
    nome = m["nome"]
    if INDIZI_PROVA.search(nome):
        return (0, "prova o template", f"il nome «{nome}» dice che non è produzione")
    if not m["acceso"]:
        return (0, "spenta", "spenta al momento della raccolta: da confermare se va migrata")
    if INDIZI_CRITICA.search(nome) or "SQL" in str(m["intera"].get("guest_OS") or ""):
        return (2, "database o dominio", f"il nome «{nome}» suggerisce un servizio da spostare per ultimo")
    return (1, "ordinaria", "nessun indizio di criticità")


def ondate(mm: list) -> list:
    """Le ondate, dimensionate sui DISCHI e non sulle macchine.

    §11.5.3: mai più di quattro dischi importati insieme. Un'ondata da dodici
    dischi sono tre turni da quattro — una sessione di lavoro. Le macchine molto
    grandi escono dalle ondate e diventano un progetto a sé: metterle in fila
    con le altre significa bloccare la serata su una sola.
    """
    a_se, resto = [], []
    for m in mm:
        (a_se if capacita_gb(m) >= GB_PROGETTO_A_SE else resto).append(m)
    # Si ordina anche per ETICHETTA, non solo per gruppo: «spenta» e «prova o
    # template» sono entrambe di classe 0, e alternandole ogni cambio apriva
    # un'ondata nuova. Ventidue ondate invece di undici, tutte da due macchine.
    resto.sort(key=lambda m: (classe(m)[0], classe(m)[1], -capacita_gb(m), m["nome"].lower()))

    fuori, corrente, dischi_ora = [], [], 0
    etichetta_ora = None
    for m in resto:
        cl = classe(m)[1]
        n = max(1, len(dischi(m)))
        # Si cambia ondata quando si riempie, o quando cambia la classe: mescolare
        # una prova con un database in una sera sola vanifica l'ordine di §11.8.
        if corrente and (dischi_ora + n > DISCHI_PER_ONDATA or cl != etichetta_ora):
            fuori.append({"macchine": corrente, "dischi": dischi_ora, "classe": etichetta_ora})
            corrente, dischi_ora = [], 0
        corrente.append(m)
        dischi_ora += n
        etichetta_ora = cl
    if corrente:
        fuori.append({"macchine": corrente, "dischi": dischi_ora, "classe": etichetta_ora})
    for m in a_se:
        fuori.append({"macchine": [m], "dischi": max(1, len(dischi(m))),
                      "classe": "progetto a sé", "sola": True})
    return fuori


def _turni(dischi_totali: int) -> str:
    n = max(1, -(-dischi_totali // DISCHI_IN_PARALLELO))
    return f"{n} turno" if n == 1 else f"{n} turni"


def sezione_ondate_md(mm: list) -> list:
    o = ondate(mm)
    r = ["## Il piano a ondate", "",
         f"**{len(o)} ondate** per {len(mm)} macchine. Il dimensionamento è sui **dischi**, non sulle "
         f"macchine: il manuale (§11.5.3) pone il limite di **{DISCHI_IN_PARALLELO} dischi importati "
         f"contemporaneamente**, e superarlo blocca i client dell'API ESXi — anche gli import già in corso. "
         f"Un'ondata da {DISCHI_PER_ONDATA} dischi sono tre turni da quattro.", "",
         "L'ordine viene da §11.8: prima le innocue, poi le ordinarie, database e domini per ultimi. "
         "**La classe è una proposta**, dedotta dal nome e dal sistema operativo: spostare una macchina "
         "di classe è una decisione vostra, e il perché è dichiarato per ognuna.", ""]
    r += an._tab([(f"Ondata {i}", o_["classe"], len(o_["macchine"]), o_["dischi"],
                   f"{sum(capacita_gb(x) for x in o_['macchine'])} GB",
                   _turni(o_['dischi']))
                  for i, o_ in enumerate(o, 1)],
                 ("Ondata", "Contiene", "Macchine", "Dischi", "Da copiare", "Turni da 4"))
    for i, o_ in enumerate(o, 1):
        r.append(f"### Ondata {i} — {o_['classe']}")
        r.append("")
        if o_.get("sola"):
            m = o_["macchine"][0]
            r.append(f"**{m['nome']}** da sola: {capacita_gb(m)} GB. Una macchina di questa taglia in fila "
                     f"con le altre blocca la serata; si pianifica per sé, con la sua finestra.  ")
            r.append("")
        r += an._tab([(m["nome"], f"{capacita_gb(m)} GB", len(dischi(m)), metodo(m)[0],
                       "sì" if su_delta(m) else "—") for m in o_["macchine"]],
                     ("Macchina", "Dischi totali", "N. dischi", "Metodo", "Snapshot da consolidare"))
    return r


def scrivi(dati: dict, esito, mm: list, percorso: Path, intest: dict) -> None:
    r = an._intestazione_md("Assessment di migrazione — " + str(intest.get("Cliente") or "sorgente"), intest)
    b, a, i = esito.conta(BLOCCANTE), esito.conta(ATTENZIONE), esito.conta(INFO)
    r += ["## Riepilogo", "", f"**{b} bloccanti · {a} da valutare · {i} informativi** su {len(mm)} macchine.", ""]

    per_metodo = {}
    for m in mm:
        met, _, _ = metodo(m)
        per_metodo.setdefault(met, []).append(m)
    r += an._tab([(k, len(v), f"{sum(capacita_gb(x) for x in v)} GB") for k, v in per_metodo.items()],
                 ("Metodo (manuale §11.5)", "Macchine", "Da copiare"))

    bl = [x for x in esito.rilievi if x.livello == BLOCCANTE]
    if bl:
        r += ["### Da sistemare prima di cominciare", ""]
        r += [f"- 🔴 **{x.ambito}** — {x.messaggio} *[{x.fonte}]*" for x in bl] + [""]

    r += ["## Le macchine, una per una", ""]
    for m in mm:
        met, perche, note = metodo(m)
        suoi = [x for x in esito.rilievi if x.ambito == f"VM {m['nome']}"]
        i_ = m["intera"]
        hw = versione_hw(m)
        r.append(f"### {m['nome']}")
        r.append("")
        r += an._tab([(
            f"{(i_.get('cpu') or {}).get('count', '?')} vCPU",
            f"{round(((i_.get('memory') or {}).get('size_MiB') or 0) / 1024)} GB RAM",
            f"{capacita_gb(m)} GB su {len(dischi(m))} dischi",
            f"VMX_{hw:02d}" if hw else "?",
            (i_.get("boot") or {}).get("type", "?"),
            str(i_.get("guest_OS") or "?"),
            "accesa" if m["acceso"] else "spenta",
        )], ("CPU", "RAM", "Dischi", "Hardware", "Firmware", "Sistema", "Stato"))
        r.append(f"**Metodo proposto:** {met} — {perche}.  ")
        if note:
            r.append("**Prima:** " + "; ".join(note) + ".  ")
        r.append("")
        if suoi:
            r += an._tab([("🔴" if x.livello == BLOCCANTE else "🟡" if x.livello == ATTENZIONE else "ℹ️",
                           x.messaggio, x.fonte) for x in sorted(suoi, key=lambda y: an.ORDINE_LIV[y.livello])],
                         ("", "Rilievo", "Fonte"))
    r += sezione_ondate_md(mm)
    r += an.sezione_regole_md(esito)
    r += an._piede_md()
    percorso.write_text("\n".join(r), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", required=True, help="la raccolta di raccogli-vcenter.py")
    ap.add_argument("--cliente", default="", help="nome del cliente")
    ap.add_argument("--codice", default="", help="codice cliente")
    ap.add_argument("--output", default=".", help="cartella di uscita")
    ap.add_argument("--precompila", metavar="FILE",
                    help="scrive le risposte al questionario che i dati sanno dare")
    args = ap.parse_args()

    dati = json.loads(Path(args.json).read_text(encoding="utf-8"))
    mm = macchine(dati)
    esito = an.Esito()
    for m in mm:
        controlla(m, esito)

    servizio = len(dati.get("vm") or {}) - len(mm)
    intest = {
        "Cliente": args.cliente or "sorgente",
        "Codice cliente": args.codice or "—",
        "Sorgente": dati.get("host_vcenter", "?"),
        "Macchine nell'impianto": dati.get("vm_totali", "?"),
        "Macchine analizzate": f"{len(mm)}" + (f" (escluse {servizio} di servizio vCLS)" if servizio else ""),
        "Host ESXi": len(dati.get("host") or []),
        "Datastore": len(dati.get("datastore") or []),
        "Raccolto il": an.datetime.fromtimestamp(dati.get("raccolto_il", 0)).strftime("%Y-%m-%d %H:%M"),
    }
    uscita = Path(args.output)
    uscita.mkdir(parents=True, exist_ok=True)
    f = uscita / f"{args.codice or 'x'}_{(args.cliente or 'sorgente').replace(' ', '-')}_migrazione.md"
    scrivi(dati, esito, mm, f, intest)
    print(f"{esito.conta(BLOCCANTE)} bloccanti · {esito.conta(ATTENZIONE)} da valutare · "
          f"{esito.conta(INFO)} informativi su {len(mm)} macchine", file=sys.stderr)
    if args.precompila:
        pre = precompila(dati)
        Path(args.precompila).write_text(json.dumps(pre, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{len(pre['campi'])} risposte precompilate in {args.precompila}", file=sys.stderr)
    print(f"Assessment scritto in {f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
