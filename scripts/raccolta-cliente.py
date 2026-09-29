#!/usr/bin/env python3
"""Raccolta vCenter di un cliente, e invio al portale — tutto a domande.

    raccolta-cliente                  raccolta e invio, tutto a domande
    raccolta-cliente <file.json>      invia una raccolta già fatta

Chiede il codice del cliente, l'indirizzo del vCenter e la password, fa la
raccolta (solo lettura) e, se lo confermi, la manda al portale.

LA PASSWORD NON VIENE SALVATA. Si digita a schermo spento, viaggia sullo
standard input del collettore e finisce lì: non tocca il disco, non compare
nell'elenco dei processi, non entra nella storia della shell.

Quello che invece si ricorda, per non doverlo ridigitare, è il **contorno**:
per ogni codice cliente l'indirizzo del vCenter, l'utente e la dimensione del
campione usati l'ultima volta. Stanno in ~/.config/domarc/raccolta-clienti.json.
"""
from __future__ import annotations

import getpass
import json
import re
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

QUI = Path(__file__).resolve().parent
COLLETTORE = QUI.parent / "strumenti" / "raccogli-vcenter.py"
MEMORIA = Path.home() / ".config" / "domarc" / "raccolta-clienti.json"
# Il nome pubblico, non l'IP: sull'IP il certificato è autofirmato, sul nome
# è valido. E il prefisso «/proxmox» fa parte dell'indirizzo del portale —
# senza, l'API risponde 404 (verificato il 29/09/2026).
PORTALE_PREDEFINITO = "https://survey.domarc.it/proxmox"
CODICE = re.compile(r"^PXM(-[A-Z0-9]{4}){3}$", re.I)


# ----------------------------------------------------------------- dialogo
def chiedi(domanda: str, predefinito: str = "", obbligatorio: bool = True) -> str:
    coda = f" [{predefinito}]" if predefinito else ""
    while True:
        r = input(f"{domanda}{coda}: ").strip() or predefinito
        if r or not obbligatorio:
            return r
        print("  serve un valore")


def conferma(domanda: str, predefinito: bool = False) -> bool:
    coda = "[S/n]" if predefinito else "[s/N]"
    r = input(f"{domanda} {coda}: ").strip().lower()
    if not r:
        return predefinito
    return r in ("s", "si", "sì", "y", "yes")


def memoria_leggi() -> dict:
    try:
        return json.loads(MEMORIA.read_text(encoding="utf-8"))
    except Exception:
        return {}


def memoria_scrivi(dati: dict) -> None:
    MEMORIA.parent.mkdir(parents=True, exist_ok=True)
    MEMORIA.write_text(json.dumps(dati, indent=1, ensure_ascii=False), encoding="utf-8")
    MEMORIA.chmod(0o600)


def impronta_certificato(host: str, porta: int = 443) -> str | None:
    """L'impronta SHA-256 del certificato, come la mostra un browser."""
    import hashlib
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((host, porta), timeout=8) as grezzo:
            with ctx.wrap_socket(grezzo, server_hostname=host) as tls:
                der = tls.getpeercert(binary_form=True)
    except OSError:
        return None
    grezza = hashlib.sha256(der).hexdigest().upper()
    return ":".join(grezza[i:i + 2] for i in range(0, len(grezza), 2))


def certificato_riconosciuto(portale: str, mem: dict) -> bool:
    """Come fa SSH con le host key: la prima volta si guarda, poi si ricorda.

    Il portale interno ha un certificato autofirmato, che nessuna catena di CA
    può convalidare. Disattivare la verifica e basta vorrebbe dire accettare
    qualunque certificato, per sempre. Fissare l'impronta invece dice: mi fido
    di QUESTO, e se un giorno cambia me ne accorgo.
    """
    from urllib.parse import urlparse
    u = urlparse(portale if "//" in portale else f"https://{portale}")
    if u.scheme != "https":
        return True                      # http: non c'è niente da verificare
    host, porta = u.hostname, u.port or 443

    ora = impronta_certificato(host, porta)
    if not ora:
        print(f"  non riesco a leggere il certificato di {host}:{porta}")
        return False

    conosciute = mem.setdefault("_certificati", {})
    prima = conosciute.get(f"{host}:{porta}")

    if prima == ora:
        return True
    if prima and prima != ora:
        print(f"\n  ATTENZIONE: il certificato di {host}:{porta} È CAMBIATO.")
        print(f"     prima:  {prima}")
        print(f"     adesso: {ora}")
        print("     Può essere un rinnovo legittimo, oppure qualcuno in mezzo.")
        if not conferma("     Accetto il certificato nuovo?"):
            return False
    else:
        print(f"\n  Il portale {host} ha un certificato autofirmato, mai visto prima.")
        print(f"     impronta SHA-256: {ora}")
        print("     Confrontala con quella che mostra il browser sullo stesso indirizzo.")
        if not conferma("     Me ne fido?"):
            return False

    conosciute[f"{host}:{porta}"] = ora
    memoria_scrivi(mem)
    return True


def raggiungibile(host: str, porta: int = 443, attesa: float = 5) -> bool:
    try:
        with socket.create_connection((host, porta), timeout=attesa):
            return True
    except OSError:
        return False


# --------------------------------------------------------------- riepilogo
def riepilogo(percorso: Path) -> dict:
    """Legge il JSON e dice se la raccolta è buona, prima di mandarla via."""
    d = json.loads(percorso.read_text(encoding="utf-8"))
    vm = d.get("vm") or {}
    host_toccati = {v.get("host") for v in vm.values() if v.get("host")}

    senza_firmware, con_snapshot, tools_assenti = [], [], []
    for v in vm.values():
        intera = v.get("intera") or {}
        nome = (v.get("lista") or {}).get("name") or "?"

        tipo = ((intera.get("boot") or {}).get("type") or "").strip()
        if not tipo:
            senza_firmware.append(nome)

        for disco in (intera.get("disks") or {}).values():
            if not isinstance(disco, dict):
                continue
            file_vmdk = ((disco.get("backing") or {}).get("vmdk_file") or "")
            if re.search(r"-\d{6}\.vmdk", file_vmdk):
                con_snapshot.append(nome)
                break

        stato = ((v.get("tools") or {}) or {}).get("version_status") or ""
        if stato in ("", "NOT_INSTALLED"):
            tools_assenti.append(nome)

    return {
        "raccolte": len(vm),
        "inventario": d.get("vm_totali", 0),
        "host_toccati": len(host_toccati),
        "host_totali": len(d.get("host") or []),
        "datastore": len(d.get("datastore") or []),
        "reti": len(d.get("rete") or []),
        "senza_firmware": senza_firmware,
        "con_snapshot": con_snapshot,
        "tools_assenti": tools_assenti,
        "errori": d.get("errori") or [],
        "durata": d.get("durata_s"),
        "chiamate": d.get("chiamate"),
        "kb": percorso.stat().st_size // 1024,
    }


def stampa_riepilogo(r: dict) -> None:
    print()
    print(f"  macchine raccolte   {r['raccolte']} su {r['inventario']} in inventario")
    print(f"  host                {r['host_toccati']} toccati su {r['host_totali']}")
    print(f"  datastore · reti    {r['datastore']} · {r['reti']}")
    print(f"  tempo               {r['durata']} s, {r['chiamate']} chiamate, {r['kb']} kB")
    if r["senza_firmware"]:
        print(f"\n  ATTENZIONE: {len(r['senza_firmware'])} macchine senza firmware letto")
        print(f"     {', '.join(r['senza_firmware'][:5])}{'…' if len(r['senza_firmware']) > 5 else ''}")
        print("     di solito vuol dire che l'utenza non vede tutto, o l'API risponde diversamente")
    if r["con_snapshot"]:
        print(f"\n  {len(r['con_snapshot'])} macchine girano su uno SNAPSHOT aperto")
        print(f"     {', '.join(r['con_snapshot'][:5])}{'…' if len(r['con_snapshot']) > 5 else ''}")
        print("     vanno consolidate prima di qualsiasi migrazione")
    if r["tools_assenti"]:
        print(f"\n  {len(r['tools_assenti'])} macchine senza VMware Tools")
    if r["errori"]:
        print(f"\n  {len(r['errori'])} errori durante la raccolta:")
        for e in r["errori"][:5]:
            print(f"     {e}")


# ------------------------------------------------------------------ main
def invia_file(percorso: Path) -> int:
    """Manda al portale una raccolta già fatta, senza ripassare da vCenter.

    Serve quando l'invio è fallito — certificato, rete, codice — e rifare la
    raccolta vorrebbe dire un'altra tornata di chiamate all'API di vSphere.
    """
    if not percorso.exists():
        print(f"non trovo {percorso}", file=sys.stderr)
        return 2

    mem = memoria_leggi()
    r = riepilogo(percorso)
    print(f"\nRaccolta già fatta: {percorso}")
    stampa_riepilogo(r)

    # il codice si indovina dal nome del file, se c'è
    trovato = re.search(r"(PXM(?:-[A-Z0-9]{4}){3})", percorso.name, re.I)
    codice = chiedi("\nCodice cliente (PXM-…)",
                    (trovato.group(1).upper() if trovato else mem.get("_ultimo", ""))).upper()
    noto = mem.get(codice, {})
    nome_cliente = chiedi("Nome del cliente (facoltativo)", noto.get("cliente", ""), obbligatorio=False)
    portale = chiedi("Portale", noto.get("portale", PORTALE_PREDEFINITO))

    print(f"\n  Sto per inviare {r['raccolte']} macchine di «{nome_cliente or codice}»")
    print(f"  a {portale}, sotto il codice {codice}.")
    if not conferma("  Confermi l'invio?"):
        print("\n  Non inviato.")
        return 0

    if not certificato_riconosciuto(portale, mem):
        print("\n  Invio annullato.")
        return 1

    import importlib.util
    spec = importlib.util.spec_from_file_location("collettore", COLLETTORE)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    ok = modulo.invia_al_portale(json.loads(percorso.read_text(encoding="utf-8")),
                                 portale, codice, nome_cliente, "", verifica=False)
    if ok:
        mem.setdefault(codice, {}).update({"portale": portale, "cliente": nome_cliente})
        memoria_scrivi(mem)
        print("\n  Inviato.")
        return 0
    print("\n  L'invio non è andato a buon fine.")
    return 1


def main() -> int:
    if not COLLETTORE.exists():
        print(f"non trovo il collettore: {COLLETTORE}", file=sys.stderr)
        return 2

    # «raccolta-cliente <file.json>» invia una raccolta già fatta
    argomenti = [a for a in sys.argv[1:] if not a.startswith("-")]
    if argomenti:
        return invia_file(Path(argomenti[0]).expanduser())

    print("\nRaccolta vCenter per il portale clienti")
    print("La password non viene salvata da nessuna parte.\n")

    mem = memoria_leggi()
    ultimo = mem.get("_ultimo", "")

    codice = chiedi("Codice cliente (PXM-…)", ultimo).upper()
    if not CODICE.match(codice):
        print(f"  «{codice}» non ha la forma PXM-XXXX-XXXX-XXXX.")
        if not conferma("  Procedo lo stesso?"):
            return 1
    noto = mem.get(codice, {})

    host = chiedi("vCenter (indirizzo o nome)", noto.get("host", ""))
    utente = chiedi("Utente", noto.get("utente", "administrator@vsphere.local"))
    campione = chiedi("Quante macchine (0 = tutte)", str(noto.get("campione", 12)))
    host_minimi = chiedi("Da quanti host almeno", str(noto.get("host_minimi", 3)))
    nome_cliente = chiedi("Nome del cliente (facoltativo)", noto.get("cliente", ""), obbligatorio=False)

    # La raggiungibilità si prova PRIMA di far digitare la password: se il
    # vCenter non risponde, aver scritto la password non serve a niente.
    print(f"\n  verifico che {host}:443 risponda…", end=" ", flush=True)
    if raggiungibile(host):
        print("sì")
    else:
        print("NO")
        print("  Il vCenter non risponde sulla 443 da questa macchina.")
        print("  Controlla la VPN, e che tu stia sulla macchina che ha la rotta.")
        if not conferma("  Provo lo stesso?"):
            return 1

    password = getpass.getpass(f"Password di {utente}: ")
    if not password:
        print("  password vuota: mi fermo")
        return 1

    fuori = Path(f"/tmp/raccolta-{codice}-{datetime.now():%Y%m%d-%H%M}.json")
    comando = [sys.executable, str(COLLETTORE),
               "--host", host, "--credenziale", "-",
               "--campione", campione, "--host-minimi", host_minimi,
               "--json", str(fuori)]

    print(f"\n  raccolta in corso (solo lettura)…\n")
    t0 = time.time()
    esito = subprocess.run(comando, input=f"{utente}:{password}\n", text=True)
    del password                       # e non se ne parla più
    if esito.returncode != 0 or not fuori.exists():
        print("\n  La raccolta non è riuscita. Niente è stato inviato.", file=sys.stderr)
        return esito.returncode or 1

    # il contorno si ricorda, la password no
    mem[codice] = {"host": host, "utente": utente, "campione": int(campione or 0),
                   "host_minimi": int(host_minimi or 0), "cliente": nome_cliente,
                   "ultima_raccolta": datetime.now().isoformat(timespec="seconds")}
    mem["_ultimo"] = codice
    memoria_scrivi(mem)

    r = riepilogo(fuori)
    stampa_riepilogo(r)
    print(f"\n  JSON: {fuori}  ({round(time.time() - t0)} s in tutto)")

    # ------------------------------------------------------------- invio
    print()
    if r["raccolte"] == 0:
        print("  Nessuna macchina raccolta: non c'è niente da inviare.")
        return 1

    portale = chiedi("Portale", noto.get("portale", PORTALE_PREDEFINITO))
    print(f"\n  Sto per inviare {r['raccolte']} macchine di «{nome_cliente or codice}»")
    print(f"  a {portale}, sotto il codice {codice}.")
    if not conferma("  Confermi l'invio?"):
        print(f"\n  Non inviato. Il JSON resta in {fuori}")
        return 0

    # L'invio riusa la funzione del collettore invece di rifare le chiamate a
    # vCenter: la raccolta è già su disco, mandarla via è un POST e basta.
    if not certificato_riconosciuto(portale, mem):
        print(f"\n  Invio annullato. Il JSON resta in {fuori}")
        return 1

    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("collettore", COLLETTORE)
        modulo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(modulo)
        ok = modulo.invia_al_portale(
            json.loads(fuori.read_text(encoding="utf-8")),
            portale, codice, nome_cliente, "", verifica=False)
    except Exception as e:
        print(f"  Invio non riuscito: {e}", file=sys.stderr)
        ok = False

    if ok:
        mem[codice]["portale"] = portale
        memoria_scrivi(mem)
        print(f"\n  Inviato. Il JSON resta in {fuori} finché non lo cancelli.")
    else:
        print(f"\n  L'invio non è andato a buon fine. Il JSON è salvo in {fuori}")
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n  interrotto")
        raise SystemExit(130)
