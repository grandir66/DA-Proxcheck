#!/usr/bin/env python3
"""Audit di un cluster Proxmox e invio al portale — tutto a domande.

    audit-cliente

Il gemello di `raccolta-cliente.py`, dall'altra parte della migrazione: quello
guarda il vCenter da cui si parte, questo il cluster dove si è arrivati.
Stesse domande, stessa memoria — un cliente ha entrambi.

    audit-cliente --prontezza     «questo cluster è pronto a ricevere?»,
                                  da usare PRIMA di migrare sul cluster nuovo

NON CHIEDE LA PASSWORD: l'audit si collega al nodo con `ssh`, quindi vale la
tua chiave. Se il nodo la vuole comunque, la chiede ssh stesso, e non passa
da qui.

C'È GIÀ UNA VIA SENZA QUESTO SCRIPT. Il portale genera `avvia-proxmox.sh` già
configurato con codice e indirizzo: il cliente lo scarica dalla sua pagina
«Script» e lancia quello. Questo comando serve a noi, che facciamo l'audit di
clienti diversi dallo stesso Mac e non vogliamo passare ogni volta dal portale.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

QUI = Path(__file__).resolve().parent
sys.path.insert(0, str(QUI))
from comune import (PORTALE_PREDEFINITO, chiedi, chiedi_codice,  # noqa: E402
                    conferma, memoria_leggi, memoria_scrivi, raggiungibile)

AUDIT = QUI.parent / "audit-nodo.py"
GRAVITA = {"critico": "CRITICI", "alto": "ALTI", "medio": "MEDI", "basso": "BASSI",
           "CRITICO": "CRITICI", "ALTO": "ALTI", "MEDIO": "MEDI", "BASSO": "BASSI"}


def riepilogo_rilievi(percorso: Path) -> dict:
    """Quanti rilievi, per gravità e per ambito: il report è per le persone,
    questo serve a decidere se mandarlo."""
    try:
        d = json.loads(percorso.read_text(encoding="utf-8"))
    except Exception:
        return {}
    righe = d.get("rilievi") or []
    per_livello, per_ambito = {}, {}
    for r in righe:
        liv = GRAVITA.get(str(r.get("livello", "")), str(r.get("livello", "?")))
        per_livello[liv] = per_livello.get(liv, 0) + 1
        amb = r.get("ambito") or "—"
        per_ambito[amb] = per_ambito.get(amb, 0) + 1
    return {"totale": len(righe), "per_livello": per_livello, "per_ambito": per_ambito,
            "data": d.get("data"), "primi": righe[:4]}


def main(argv: list[str]) -> int:
    if not AUDIT.exists():
        print(f"non trovo {AUDIT}", file=sys.stderr)
        return 2

    prontezza = "--prontezza" in argv
    performance = "--performance" in argv

    print("\nAudit di un cluster Proxmox VE")
    if prontezza:
        print("Modalità «prontezza»: il cluster è pronto a ricevere le macchine?")
    print("La connessione usa ssh e la tua chiave: nessuna password passa da qui.\n")

    mem = memoria_leggi()
    codice = chiedi_codice(mem)
    noto = mem.get(codice, {})

    nodo = chiedi("Nodo Proxmox (utente@indirizzo)", noto.get("nodo", ""))
    if "@" not in nodo:
        nodo = f"root@{nodo}"
        print(f"  uso {nodo}")
    nome_cliente = chiedi("Nome del cliente (facoltativo)", noto.get("cliente", ""), obbligatorio=False)

    solo_host = nodo.split("@", 1)[1]
    print(f"\n  verifico che {solo_host}:22 risponda…", end=" ", flush=True)
    if raggiungibile(solo_host, 22):
        print("sì")
    else:
        print("NO")
        print("  Il nodo non risponde in SSH da questa macchina: controlla la VPN.")
        if not conferma("  Provo lo stesso?"):
            return 1

    quando = datetime.now().strftime("%Y%m%d-%H%M")
    cartella = Path(tempfile.gettempdir()) / f"audit-{codice}-{quando}"
    rilievi = cartella.with_suffix(".rilievi.json")

    comando = [sys.executable, str(AUDIT), "--host", nodo,
               "--output", str(cartella), "--rilievi-json", str(rilievi)]
    if nome_cliente:
        comando += ["--cliente", nome_cliente]
    if prontezza:
        comando.append("--prontezza")
    if performance:
        comando.append("--performance")

    print("\n  audit in corso (solo lettura)…\n")
    esito = subprocess.run(comando)
    if esito.returncode != 0:
        print("\n  L'audit non è riuscito. Niente è stato inviato.", file=sys.stderr)
        return esito.returncode

    mem[codice] = {**noto, "nodo": nodo, "cliente": nome_cliente,
                   "ultimo_audit": datetime.now().isoformat(timespec="seconds")}
    mem["_ultimo"] = codice
    memoria_scrivi(mem)

    r = riepilogo_rilievi(rilievi)
    if r:
        print(f"\n  {r['totale']} rilievi")
        for liv in ("CRITICI", "ALTI", "MEDI", "BASSI"):
            if r["per_livello"].get(liv):
                print(f"     {liv:9} {r['per_livello'][liv]}")
        if r["per_ambito"]:
            primi = sorted(r["per_ambito"].items(), key=lambda x: -x[1])[:4]
            print(f"     ambiti:   {', '.join(f'{a} ({n})' for a, n in primi)}")
    print(f"\n  report: {cartella}")

    print()
    portale = chiedi("Portale", noto.get("portale", PORTALE_PREDEFINITO))
    print(f"\n  Sto per inviare l'audit di «{nome_cliente or codice}»")
    print(f"  a {portale}, sotto il codice {codice}.")
    if not conferma("  Confermi l'invio?"):
        print(f"\n  Non inviato. Il report resta in {cartella}")
        return 0

    # Si rilancia l'audit con --invia invece di riusare la raccolta: l'audit
    # non sa (ancora) inviare un risultato già prodotto, e rifarlo costa una
    # manciata di comandi ssh in sola lettura.
    print("\n  invio…\n")
    comando_invio = [sys.executable, str(AUDIT), "--host", nodo,
                     "--invia", portale, "--codice-portale", codice]
    if nome_cliente:
        comando_invio += ["--cliente", nome_cliente]
    esito = subprocess.run(comando_invio)
    if esito.returncode == 0:
        mem[codice]["portale"] = portale
        memoria_scrivi(mem)
        print(f"\n  Inviato. Il report resta in {cartella}")
        return 0
    print("\n  L'invio non è andato a buon fine.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print("\n  interrotto")
        raise SystemExit(130)
