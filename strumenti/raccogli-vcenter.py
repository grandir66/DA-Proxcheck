#!/usr/bin/env python3
"""Raccolta da vCenter — SOLO LETTURA, per preparare una migrazione a Proxmox.

Legge da vSphere quello che serve a dire, per ogni macchina, **come si sposta**:
firmware, dischi e il loro tipo di backing, schede di rete, VMware Tools,
snapshot aperti, e su quale host ESXi gira in questo momento.

DA DOVE SI LANCIA
    Dalla macchina che ha la credenziale, non dal portatile: il file della
    password non si sposta. Il collettore scrive un JSON, e il JSON si porta via.

    python3 raccogli-vcenter.py --host 172.16.1.80 --credenziale ~/gao/vcenter.pass \\
        --campione 30 --host-minimi 3 --json /tmp/vcenter.json

PERCHE' E' LENTO DI PROPOSITO
    L'API di vSphere ha un limite basso di connessioni: superarlo blocca i
    client per una trentina di secondi — anche quelli gia' in corso (manuale
    §11.5.3). Le chiamate sono quindi **sequenziali**, con una pausa fra una VM
    e l'altra. Su un inventario grande si comincia con un campione.

IL CAMPIONE
    `--campione N --host-minimi H` prende N macchine distribuite **a rotazione
    fra gli host**, non le prime N dell'inventario: le prime N stanno quasi
    sempre sullo stesso host, e le differenze fra host — versione di ESXi,
    datastore, reti — sono meta' di quello che serve sapere per una migrazione.

GLI SNAPSHOT
    L'API REST di vSphere 8 **non li espone**: `/api/vcenter/vm/<id>/snapshot`
    risponde 404 (verificato su vCenter 8.0.3). Si riconoscono comunque dal
    nome del file di backing: un disco su `NOME-000001.vmdk` e' una macchina
    che gira su un delta. E' la cosa che conta per la migrazione — §11.5.1 dice
    di consolidare prima dell'import — e si legge senza chiamate in piu'.

SOLO GET. Nessuna scrittura, nessuna VM toccata, nessuno snapshot creato.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

try:
    import requests
    import urllib3
    urllib3.disable_warnings()
except ImportError:  # pragma: no cover
    print("serve il pacchetto requests: apt install python3-requests", file=sys.stderr)
    raise SystemExit(2)

PAUSA = 0.15   # fra una chiamata e l'altra: gentilezza verso l'API, non prudenza eccessiva


class VCenter:
    """Una sessione vSphere. Il token muore alla fine, sempre."""

    def __init__(self, host: str, utente: str, password: str, timeout: int = 30):
        self.base = f"https://{host}"
        self.t = timeout
        self.s = requests.Session()
        self.s.verify = False       # certificati autofirmati sono la norma su vCenter
        r = self.s.post(f"{self.base}/api/session", auth=(utente, password), timeout=timeout)
        r.raise_for_status()
        tok = r.json()
        self.s.headers["vmware-api-session-id"] = tok if isinstance(tok, str) else tok.get("value")
        self.chiamate = 0

    def get(self, percorso: str, **parametri):
        """Una GET. Torna None invece di alzare: un endpoint che non c'e' su
        questa versione non deve fermare la raccolta di tutto il resto."""
        self.chiamate += 1
        time.sleep(PAUSA)
        try:
            r = self.s.get(f"{self.base}{percorso}", params=parametri or None, timeout=self.t)
        except requests.RequestException as e:
            return {"_errore": str(e)[:200]}
        if r.status_code != 200:
            return {"_errore": f"HTTP {r.status_code}"} if r.status_code != 404 else None
        try:
            d = r.json()
        except ValueError:
            return None
        return d.get("value") if isinstance(d, dict) and "value" in d else d

    def chiudi(self):
        try:
            self.s.delete(f"{self.base}/api/session", timeout=10)
        except requests.RequestException:
            pass


def avanzamento(testo: str):
    print(f"  {testo}", file=sys.stderr, flush=True)


def campiona(per_host: dict, quante: int, host_minimi: int) -> list:
    """N macchine distribuite a rotazione fra gli host.

    Prende una VM per host a giro finche' non arriva a N: cosi' ogni host
    contribuisce, e con pochi host popolati si arriva comunque a N. Se gli host
    con macchine sono meno di `host_minimi`, lo dice e prosegue: e' un fatto
    dell'impianto, non un errore del comando.
    """
    ordine = [h for h in per_host if per_host[h]]
    if len(ordine) < host_minimi:
        avanzamento(f"attenzione: solo {len(ordine)} host con macchine, ne erano chiesti {host_minimi}")
    scelte, giro = [], 0
    while len(scelte) < quante:
        aggiunte = 0
        for h in ordine:
            if giro < len(per_host[h]) and len(scelte) < quante:
                scelte.append((h, per_host[h][giro]))
                aggiunte += 1
        if not aggiunte:
            break
        giro += 1
    return scelte


def raccogli(vc: VCenter, quante: int, host_minimi: int) -> dict:
    t0 = time.time()
    fuori = {"versione_strumento": "1.0", "raccolto_il": int(time.time()),
             "sorgente": "vcenter", "host_vcenter": vc.base, "errori": []}

    avanzamento("host, cluster, datastore, reti")
    host = vc.get("/api/vcenter/host") or []
    fuori["host"] = host
    fuori["cluster"] = vc.get("/api/vcenter/cluster") or []
    fuori["datastore"] = vc.get("/api/vcenter/datastore") or []
    fuori["rete"] = vc.get("/api/vcenter/network") or []

    # Le VM per host: si chiedono filtrate, cosi' il campione per host e' gratis.
    per_host = {}
    for h in host:
        hid = h.get("host")
        vms = vc.get("/api/vcenter/vm", **{"hosts": hid}) or []
        per_host[hid] = [v for v in vms if isinstance(v, dict)]
        avanzamento(f"host {h.get('name', hid)}: {len(per_host[hid])} macchine")
    fuori["vm_per_host"] = {h: len(v) for h, v in per_host.items()}
    totale = sum(len(v) for v in per_host.values())
    fuori["vm_totali"] = totale

    scelte = campiona(per_host, quante, host_minimi) if quante else \
        [(h, v) for h, vs in per_host.items() for v in vs]
    avanzamento(f"campione: {len(scelte)} macchine su {totale}, da {len({h for h, _ in scelte})} host")

    dettagli = {}
    for i, (hid, v) in enumerate(scelte, 1):
        vid = v.get("vm")
        avanzamento(f"[{i}/{len(scelte)}] {v.get('name', vid)}")
        # UNA chiamata per la macchina intera: `/api/vcenter/vm/{id}` torna
        # dischi con capacità e backing, schede con rete e MAC, boot, CPU e
        # memoria tutti insieme. Chiedere i pezzi separatamente costava nove
        # chiamate invece di una, e per giunta le liste dei dischi tornavano
        # solo gli identificativi (verificato sul campo il 2026-09-10).
        d = {"lista": v, "host": hid, "intera": vc.get(f"/api/vcenter/vm/{vid}")}
        # Queste due no: stanno fuori dalla macchina, e sulle VM spente
        # rispondono 503 — che è un'informazione, non un errore.
        d["tools"] = vc.get(f"/api/vcenter/vm/{vid}/tools")
        if (v.get("power_state") or "") == "POWERED_ON":
            d["identita"] = vc.get(f"/api/vcenter/vm/{vid}/guest/identity")
            d["reti_guest"] = vc.get(f"/api/vcenter/vm/{vid}/guest/networking/interfaces")
        dettagli[vid] = d
    fuori["vm"] = dettagli
    fuori["durata_s"] = round(time.time() - t0, 1)
    fuori["chiamate"] = vc.chiamate
    return fuori


def invia_al_portale(dati: dict, portale: str, codice: str, cliente: str = "", codice_cliente: str = "") -> bool:
    """Manda la raccolta grezza al portale, come fa `audit-nodo.py --invia`.

    Una via sola per due strumenti: il portale archivia il grezzo e ne produce
    l'assessment da sé, con le regole di quel giorno. Le stesse intestazioni,
    così chi legge il codice di uno riconosce l'altro.
    """
    import urllib.parse
    indirizzo = portale.rstrip("/") + "/api/scansione"
    corpo = json.dumps(dati, ensure_ascii=False).encode()
    print(f"Invio della raccolta a {indirizzo} ({len(corpo)/1024:.0f} kB)…", file=sys.stderr)
    testate = {"Content-Type": "application/json", "X-Codice": codice.strip().upper()}
    if cliente.strip():
        testate["X-Cliente"] = urllib.parse.quote(cliente.strip())
    if codice_cliente.strip():
        testate["X-Codice-Cliente"] = urllib.parse.quote(codice_cliente.strip())
    try:
        r = requests.post(indirizzo, data=corpo, headers=testate, timeout=180, verify=True)
    except requests.RequestException as e:
        print(f"Invio non riuscito: {e}", file=sys.stderr)
        return False
    if r.status_code != 200:
        motivo = {401: "codice non valido o revocato", 413: "raccolta troppo grande",
                  400: "il portale non ha riconosciuto il formato"}.get(r.status_code, r.reason)
        print(f"Invio non riuscito ({r.status_code}): {motivo}", file=sys.stderr)
        return False
    esito = r.json()
    if esito.get("errore"):
        print(f"Archiviata, ma il portale non l'ha analizzata: {esito['errore']}", file=sys.stderr)
    if esito.get("pagina"):
        print(f"Il risultato è consultabile su {esito['pagina']}", file=sys.stderr)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", required=True, help="indirizzo del vCenter")
    ap.add_argument("--credenziale", required=True,
                    help="file con «utente:password», permessi 600. MAI la password sulla riga di comando: "
                         "finirebbe nella storia della shell e nell'elenco dei processi")
    ap.add_argument("--campione", type=int, default=30, help="quante macchine (0 = tutte)")
    ap.add_argument("--host-minimi", type=int, default=3, dest="host_minimi",
                    help="da quanti host ESXi almeno")
    ap.add_argument("--json", required=True, help="dove scrivere la raccolta")
    ap.add_argument("--invia", metavar="URL", help="manda la raccolta al portale dei clienti")
    ap.add_argument("--codice-portale", metavar="PXM-…", dest="codice_portale",
                    help="il codice di accesso al portale (con --invia)")
    ap.add_argument("--cliente", default="", help="nome del cliente, dichiarato al portale")
    ap.add_argument("--codice-cliente", default="", dest="codice_cliente",
                    help="codice cliente: è la chiave con cui il portale raggruppa le acquisizioni")
    args = ap.parse_args()

    testo = Path(args.credenziale).expanduser().read_text(encoding="utf-8").strip()
    if ":" not in testo:
        print("il file della credenziale deve contenere «utente:password»", file=sys.stderr)
        return 2
    utente, password = testo.split(":", 1)

    print(f"vCenter {args.host} — raccolta in sola lettura", file=sys.stderr)
    try:
        vc = VCenter(args.host, utente, password)
    except requests.RequestException as e:
        print(f"accesso non riuscito: {e}", file=sys.stderr)
        return 1
    try:
        dati = raccogli(vc, args.campione, args.host_minimi)
    finally:
        vc.chiudi()

    Path(args.json).write_text(json.dumps(dati, indent=1, ensure_ascii=False), encoding="utf-8")
    if args.invia:
        if not args.codice_portale:
            print("Con --invia serve anche --codice-portale.", file=sys.stderr)
        else:
            invia_al_portale(dati, args.invia, args.codice_portale, args.cliente, args.codice_cliente)
    print(f"\n{len(dati.get('vm') or {})} macchine · {dati['chiamate']} chiamate · {dati['durata_s']} s"
          f"\nScritto in {args.json}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
