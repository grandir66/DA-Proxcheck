"""Le parti che raccolta-cliente e audit-cliente hanno in comune.

Stanno qui e non copiate in due file perché la memoria dei clienti è la
stessa: un cliente ha un vCenter da cui si parte e un cluster Proxmox dove
si arriva, e sono due righe dello stesso quaderno.
"""
from __future__ import annotations

import json
import re
import socket
from pathlib import Path

MEMORIA = Path.home() / ".config" / "domarc" / "raccolta-clienti.json"
PORTALE_PREDEFINITO = "https://survey.domarc.it/proxmox"
CODICE = re.compile(r"^PXM(-[A-Z0-9]{4}){3}$", re.I)


def cartella_raccolte() -> Path:
    """Dove finiscono i file: una sottocartella di DOVE SI LANCIA, non /tmp.

    In /tmp si perdono al riavvio, e quando servono — per caricarli a mano dal
    portale — bisogna ricordarsi il percorso. Qui restano accanto a dove si sta
    lavorando, e si trovano da soli.
    """
    d = Path.cwd() / "raccolte"
    d.mkdir(exist_ok=True)
    return d


def _leggi(invito: str) -> str:
    """input() che non lascia una traccia di stack se lo standard input finisce.

    Succede quando si lancia lo strumento da uno script o con una pipe: non è
    un errore da programmatore, è una domanda senza risposta, e si esce dicendolo.
    """
    try:
        return input(invito)
    except EOFError:
        print("\n  nessuna risposta (standard input finito): mi fermo.")
        raise SystemExit(130)


def chiedi(domanda: str, predefinito: str = "", obbligatorio: bool = True) -> str:
    coda = f" [{predefinito}]" if predefinito else ""
    while True:
        r = _leggi(f"{domanda}{coda}: ").strip() or predefinito
        if r or not obbligatorio:
            return r
        print("  serve un valore")


def conferma(domanda: str, predefinito: bool = False) -> bool:
    r = _leggi(f"{domanda} {'[S/n]' if predefinito else '[s/N]'}: ").strip().lower()
    return predefinito if not r else r in ("s", "si", "sì", "y", "yes")


def memoria_leggi() -> dict:
    try:
        return json.loads(MEMORIA.read_text(encoding="utf-8"))
    except Exception:
        return {}


def memoria_scrivi(dati: dict) -> None:
    MEMORIA.parent.mkdir(parents=True, exist_ok=True)
    MEMORIA.write_text(json.dumps(dati, indent=1, ensure_ascii=False), encoding="utf-8")
    MEMORIA.chmod(0o600)


def raggiungibile(host: str, porta: int = 443, attesa: float = 5) -> bool:
    try:
        with socket.create_connection((host, porta), timeout=attesa):
            return True
    except OSError:
        return False


def chiedi_codice(mem: dict, quale: str = "_ultimo") -> str:
    codice = chiedi("Codice cliente (PXM-…)", mem.get(quale, "")).upper()
    if not CODICE.match(codice):
        print(f"  «{codice}» non ha la forma PXM-XXXX-XXXX-XXXX.")
        if not conferma("  Procedo lo stesso?"):
            raise SystemExit(1)
    return codice
