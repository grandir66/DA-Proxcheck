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
    f = QUI.parent / "audit-nodo.py"
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
    r += an.sezione_regole_md(esito)
    r += an._piede_md()
    percorso.write_text("\n".join(r), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", required=True, help="la raccolta di raccogli-vcenter.py")
    ap.add_argument("--cliente", default="", help="nome del cliente")
    ap.add_argument("--codice", default="", help="codice cliente")
    ap.add_argument("--output", default=".", help="cartella di uscita")
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
    print(f"Assessment scritto in {f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
