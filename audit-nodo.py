#!/usr/bin/env python3
"""Audit di un nodo o di un intero cluster Proxmox VE: raccoglie quanto più
possibile da host, cluster e VM e lo confronta con le best practice del
manuale operativo Domarc. Report a terminale; con --output un Markdown
completo (inventario, performance, rilievi); con --json i dati grezzi.

USO DAL CLIENT (macOS, Linux o Windows con Python 3.7+ e il client ssh)

    python3 audit-nodo.py --host 192.168.40.1                 # root@ di default; chiede nome e codice cliente
    python3 audit-nodo.py --host 192.168.40.1 --cliente "Rossi Srl" --codice C0123 --output ~/report
    python3 audit-nodo.py --host 192.168.40.1 --performance   # + pveperf
    python3 audit-nodo.py --host 192.168.40.1 --solo-questo-nodo
    python3 audit-nodo.py                                     # menu (host salvati)

    # con il portale dei clienti: le tipologie delle VM si assegnano da lì,
    # e qui non viene chiesto niente
    python3 audit-nodo.py --host 192.168.40.1 \\
        --invia https://survey.domarc.it/proxmox --codice-portale PXM-XXXX-XXXX-XXXX

I RILIEVI riguardano le VM ACCESE. Una macchina spenta non ha un carico da
confrontare con la propria tipologia, e i template fermi riempirebbero il
report di scostamenti che nessuno agirà; l'INVENTARIO invece le elenca tutte,
con il loro stato. Per estendere i rilievi anche alle spente: --con-spente.
(Da non confondere con --solo-accese, che agisce prima: quelle VM non vengono
proprio interrogate, e la raccolta non le contiene.)

Produce sempre DUE file Markdown nella cartella di --output (default: quella
corrente), con il nome composto da codice cliente, nome cliente e indirizzo:
    C0123_Rossi-Srl_192.168.40.1_inventory.md   cosa c'è (cluster, nodi, hardware, VM)
    C0123_Rossi-Srl_192.168.40.1_report.md      cosa non torna rispetto alle best practice

UNA sola connessione SSH: se la chiave non basta, ssh chiede la password
una volta sola, sul terminale — questo script non la vede né la salva.

SE IL NODO È IN UN CLUSTER, per default vengono indagati TUTTI i nodi e
tutte le VM: il nodo d'ingresso raggiunge gli altri con la fiducia SSH
interna del cluster (chiavi che Proxmox stesso distribuisce). Un nodo non
raggiungibile viene segnalato, non blocca il resto.

COME FUNZIONA

  1. Un collector (Python, solo stdlib) è inviato al nodo d'ingresso ed
     eseguito una volta: interroga l'API locale (pvesh, JSON) e pochi
     comandi di sola lettura; se in cluster, ripete la stessa raccolta
     sugli altri nodi; restituisce un unico JSON. Nessun file resta sui nodi.
  2. In locale: tabella delle VM (tutto il cluster) con la tipologia di
     carico proposta dal nome e dal SO (asterisco) — INVIO accetta, un VMID
     cambia — poi confronto con le regole della tipologia e report. Sette
     tipologie: domain controller, database, applicativo, rete, dati,
     terminal server, test/legacy/replica.

SOLO LETTURA: pvesh get, smartctl -H/-A, cat, ip, lvs, zpool, timedatectl,
ping fra gli anelli corosync; con --performance anche pveperf (scrive e
rimuove un file temporaneo per il test fsync).

FONTI DELLE SOGLIE

Ogni rilievo cita la regola che applica (`manuale §8.3 › Cache mode`), e il
report ne riporta il TESTO in fondo, nella sezione «Le regole applicate»: chi
legge non deve avere il manuale sottomano. I passaggi stanno in
`fonti_manuale.py`, generato da `strumenti/estrai-fonti.py` a partire dal
manuale operativo Proxmox VE di Domarc; le soglie che il manuale non copre
(SMART da Proxreporter, fsync/s di pveperf, latenze da blockstat) hanno lì la
propria spiegazione. Dove nessuna fonte prescrive un valore giusto in
assoluto, il dato è riportato come informazione, non come rilievo.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

VERSIONE_SCRIPT = "2.1"
WINDOWS = os.name == "nt"

# Le regole citate, con il testo per esteso: `fonti_manuale.py` è generato da
# `strumenti/estrai-fonti.py` a partire dal manuale operativo Domarc. Se manca
# (per esempio perché è stato copiato solo questo file) il report cita le
# regole senza riportarle: si perde comodità, non correttezza.
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from fonti_manuale import FONTI as REGOLE, MANUALE as EDIZIONE_MANUALE
except ImportError:
    REGOLE, EDIZIONE_MANUALE = {}, {}

# ────────────────────────────── console: UTF-8 e colori anche su Windows ──────────────────────────────

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def _abilita_vt_windows() -> bool:
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-11)
        modo = ctypes.c_uint()
        if k.GetConsoleMode(h, ctypes.byref(modo)) == 0:
            return False
        return k.SetConsoleMode(h, modo.value | 0x0004) != 0
    except Exception:
        return False


USA_COLORI = sys.stdout.isatty() and not os.environ.get("NO_COLOR") and (not WINDOWS or _abilita_vt_windows())


def c(testo: str, codice: str) -> str:
    return f"\033[{codice}m{testo}\033[0m" if USA_COLORI else testo


ROSSO, GIALLO, VERDE, BLU, GRIGIO, GRASSETTO = "31", "33", "32", "36", "90", "1"

# ────────────────────────────── connessione ──────────────────────────────

HOST_REMOTO = None


def host_con_utente_default(host: str) -> str:
    """Senza utente esplicito si usa root: è l'account di amministrazione
    di un nodo Proxmox nella quasi totalità dei casi reali."""
    return host if "@" in host else f"root@{host}"


def opzioni_ssh() -> list:
    # Niente BatchMode: la password, se serve, la chiede ssh stesso sul
    # terminale. Niente ControlMaster: non esiste su Windows, e con una
    # sola connessione non serve. accept-new solo dove il client lo
    # conosce (l'OpenSSH di Windows 10 originale è un 7.7 che non lo ha).
    o = ["-o", "ConnectTimeout=15"]
    if not WINDOWS:
        o += ["-o", "StrictHostKeyChecking=accept-new"]
    return o


def esegui_collector(script: str, timeout: int = 1800) -> str:
    """Una connessione sola: `ssh host python3 -` con il collector su
    stdin; stderr resta al terminale così l'avanzamento si vede dal vivo
    (e anche il prompt della password o della chiave dell'host)."""
    argv = (["ssh", *opzioni_ssh(), HOST_REMOTO, "python3 -"] if HOST_REMOTO else [sys.executable, "-"])
    try:
        r = subprocess.run(argv, input=script, stdout=subprocess.PIPE, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return r.stdout
    except FileNotFoundError:
        print("Il comando 'ssh' non è disponibile su questo client.", file=sys.stderr)
    except subprocess.TimeoutExpired:
        print("Il collector non ha risposto entro il tempo massimo.", file=sys.stderr)
    except OSError as e:
        print(f"Errore nell'esecuzione del collector: {e}", file=sys.stderr)
    return ""


# ────────────────────────────── collector: nodo ──────────────────────────────
# Eseguito su OGNI nodo (quello d'ingresso direttamente, gli altri via ssh
# interno al cluster). Solo lettura; ogni voce protetta: un comando assente
# produce None e una riga in "errori", mai un'interruzione.

COLLECTOR_NODO = r'''
import json, subprocess, sys, os, re, glob, time
SOLO_ACCESE = __SOLO_ACCESE__
PERFORMANCE = __PERFORMANCE__
MAX_VM = __MAX_VM__
out = {"nodo": {}, "vms": {}, "lxc": {}, "errori": []}

def prog(msg):
    sys.stderr.write("  [%s] %s\n" % (out["nodo"].get("hostname", "?"), msg)); sys.stderr.flush()

def run(cmd, timeout=25):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else ""
    except Exception as e:
        out["errori"].append("%s: %s" % (cmd[:60], e)); return ""

def api(path, extra="", timeout=25):
    txt = run("pvesh get %s %s --output-format json 2>/dev/null" % (path, extra), timeout)
    if not txt.strip():
        return None
    try:
        return json.loads(txt)
    except Exception:
        out["errori"].append("json non valido da " + path); return None

N = run("hostname").strip()
nodo = out["nodo"]; nodo["hostname"] = N
prog("stato, rete, storage, dischi")
nodo["status"] = api("/nodes/%s/status" % N)
if nodo["status"] and "cpuinfo" in nodo["status"]:
    nodo["status"]["cpuinfo"].pop("flags", None)
nodo["network"] = api("/nodes/%s/network" % N)
nodo["disks"] = api("/nodes/%s/disks/list" % N)
nodo["storage"] = api("/nodes/%s/storage" % N)
nodo["subscription"] = api("/nodes/%s/subscription" % N)
nodo["apt_update"] = api("/nodes/%s/apt/update" % N)
nodo["apt_repos"] = api("/nodes/%s/apt/repositories" % N)
nodo["services"] = api("/nodes/%s/services" % N)
nodo["certificati"] = api("/nodes/%s/certificates/info" % N)
for ce in (nodo["certificati"] or []):
    ce.pop("pem", None)
nodo["replication"] = api("/nodes/%s/replication" % N)
# Il firewall dell'HOST: e' il secondo dei due interruttori (manuale 15.3).
# Quello del datacenter sta a livello cluster; acceso uno e spento l'altro,
# le regole non si applicano e `pve-firewall status` dice comunque
# "enabled/running". Va letto dai due lati, o non si vede.
nodo["fw_options"] = api("/nodes/%s/firewall/options" % N)
nodo["fw_rules"] = api("/nodes/%s/firewall/rules" % N)
# Ceph: solo se questo nodo lo ha. Un cluster senza Ceph deve restare
# analizzabile, quindi si prova e si tira dritto.
if os.path.exists("/etc/pve/ceph.conf"):
    nodo["ceph_pool"] = api("/nodes/%s/ceph/pool" % N)
    nodo["ceph_osd"] = api("/nodes/%s/ceph/osd" % N)
    nodo["ceph_cfg"] = run("cat /etc/pve/ceph.conf")
nodo["rrd"] = api("/nodes/%s/rrddata" % N, "--timeframe hour")
nodo["timedatectl"] = run("timedatectl")
nodo["ip_addr"] = run("ip -o -4 addr")
nodo["ip_link"] = run("ip -o link")
nodo["bonding"] = {}
for f in glob.glob("/proc/net/bonding/*"):
    try:
        nodo["bonding"][os.path.basename(f)] = open(f).read()
    except Exception:
        pass
nodo["lvs"] = run("lvs --noheadings -o lv_name,vg_name,lv_size,data_percent,metadata_percent")
nodo["zpool_list"] = run("zpool list -H -o name,size,alloc,free,capacity,health,frag")
nodo["zpool_status_x"] = run("zpool status -x")
nodo["zfs_arc_max"] = run("cat /sys/module/zfs/parameters/zfs_arc_max").strip()
nodo["mdstat"] = run("cat /proc/mdstat")
nodo["edac"] = {}
for mc in run("ls /sys/devices/system/edac/mc/ 2>/dev/null").split():
    if mc.startswith("mc"):
        nodo["edac"][mc] = {"ce": run("cat /sys/devices/system/edac/mc/%s/ce_count" % mc).strip(),
                            "ue": run("cat /sys/devices/system/edac/mc/%s/ue_count" % mc).strip()}
nodo["kernel_boot"] = run("ls -1 /boot/vmlinuz-* 2>/dev/null")
nodo["pve_firewall"] = run("pve-firewall status")
nodo["multipath"] = run("multipath -ll 2>/dev/null")
nodo["pvecm_status"] = run("pvecm status 2>/dev/null")
nodo["corosync_conf"] = run("cat /etc/pve/corosync.conf 2>/dev/null")
nodo["corosync_cfgtool"] = run("corosync-cfgtool -s 2>/dev/null")
nodo["smart"] = {}
for d in (nodo["disks"] or []):
    dev = d.get("devpath", "")
    if dev and not os.path.basename(dev).startswith("zd"):
        nodo["smart"][dev] = run("smartctl -H -A %s 2>/dev/null" % dev, 30)

nodo["ping_ring"] = {}
miei = set(re.findall(r"inet (\S+)/", nodo["ip_addr"]))
altri = sorted(set(re.findall(r"ring\d+_addr:\s*(\S+)", nodo["corosync_conf"])) - miei)
if altri:
    prog("latenza verso %d indirizzi corosync" % len(altri))
for ip in altri:
    o = run("ping -c 3 -i 0.2 -W 1 -q %s" % ip, 10)
    m = re.search(r"= ([\d.]+)/([\d.]+)/([\d.]+)/", o)
    p = re.search(r"(\d+)% packet loss", o)
    nodo["ping_ring"][ip] = {"avg_ms": float(m.group(2)) if m else None,
                             "max_ms": float(m.group(3)) if m else None,
                             "loss": int(p.group(1)) if p else None}

if PERFORMANCE:
    prog("pveperf su / (10-20 s)")
    nodo["pveperf"] = {"/": run("pveperf / 2>&1", 120)}
    n = 0
    for s in (nodo["storage"] or []):
        if n >= 2 or not s.get("active") or s.get("shared"):
            continue
        p = None
        d = api("/storage/%s" % s["storage"])
        if s.get("type") == "dir" and d:
            p = d.get("path")
        elif s.get("type") == "zfspool" and d and d.get("pool"):
            p = run("zfs get -H -o value mountpoint %s" % d["pool"]).strip()
        if p and os.path.isdir(p) and p != "/" and os.stat(p).st_dev != os.stat("/").st_dev:
            prog("pveperf su %s (%s)" % (p, s["storage"]))
            nodo["pveperf"][p] = run("pveperf %s 2>&1" % p, 120); n += 1

vms = sorted(api("/nodes/%s/qemu" % N) or [], key=lambda x: x["vmid"])
tot = len(vms)
for i, v in enumerate(vms, 1):
    vmid = str(v["vmid"])
    if SOLO_ACCESE and v.get("status") != "running":
        continue
    if MAX_VM and i > MAX_VM:
        break
    prog("VM %s %s (%d/%d)" % (vmid, v.get("name", ""), i, tot))
    d = {"lista": v, "nodo": N}
    d["config"] = api("/nodes/%s/qemu/%s/config" % (N, vmid))
    d["status"] = api("/nodes/%s/qemu/%s/status/current" % (N, vmid))
    d["snapshot"] = [s for s in (api("/nodes/%s/qemu/%s/snapshot" % (N, vmid)) or []) if s.get("name") != "current"]
    d["fw_options"] = api("/nodes/%s/qemu/%s/firewall/options" % (N, vmid))
    d["pending"] = [p for p in (api("/nodes/%s/qemu/%s/pending" % (N, vmid)) or []) if "pending" in p or "delete" in p]
    d["rrd"] = api("/nodes/%s/qemu/%s/rrddata" % (N, vmid), "--timeframe hour")
    d["agent"] = {}
    if d["status"] and d["status"].get("status") == "running" and d["status"].get("agent"):
        for k, ep in (("osinfo", "get-osinfo"), ("fsinfo", "get-fsinfo"),
                      ("interfacce", "network-get-interfaces"), ("ora", "get-time")):
            r = api("/nodes/%s/qemu/%s/agent/%s" % (N, vmid, ep), "", 10)
            res = r.get("result") if isinstance(r, dict) else None
            d["agent"][k] = None if isinstance(res, dict) and "error" in res else res
        d["agent"]["ora_host"] = int(time.time())
    out["vms"][vmid] = d

for ct in (api("/nodes/%s/lxc" % N) or []):
    ctid = str(ct["vmid"])
    if SOLO_ACCESE and ct.get("status") != "running":
        continue
    out["lxc"][ctid] = {"lista": ct, "nodo": N,
                        "config": api("/nodes/%s/lxc/%s/config" % (N, ctid)),
                        "status": api("/nodes/%s/lxc/%s/status/current" % (N, ctid))}

print(json.dumps(out))
'''

# ────────────────────────────── collector: cluster (orchestratore sul nodo d'ingresso) ──────────────────────────────

COLLECTOR_CLUSTER = r'''
import json, subprocess, sys, base64, re
TUTTO_CLUSTER = __TUTTO_CLUSTER__
SRC = base64.b64decode("__SRC_B64__").decode()
out = {"versione": "__VER__", "ingresso": None, "cluster": {}, "nodi": {}, "errori": []}

def prog(msg):
    sys.stderr.write("  [cluster] " + msg + "\n"); sys.stderr.flush()

def run(cmd, timeout=25):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else ""
    except Exception as e:
        out["errori"].append("%s: %s" % (cmd[:60], e)); return ""

def api(path, extra="", timeout=25):
    txt = run("pvesh get %s %s --output-format json 2>/dev/null" % (path, extra), timeout)
    try:
        return json.loads(txt) if txt.strip() else None
    except Exception:
        return None

def raccogli_nodo(argv, etichetta):
    try:
        r = subprocess.run(argv, input=SRC, stdout=subprocess.PIPE, text=True, timeout=1500)
        d = json.loads(r.stdout) if r.stdout.strip() else None
        if not d:
            out["errori"].append("%s: nessun dato (%s)" % (etichetta, "codice %d" % r.returncode))
        return d
    except Exception as e:
        out["errori"].append("%s: %s" % (etichetta, e)); return None

N = run("hostname").strip()
out["ingresso"] = N
cl = out["cluster"]
prog("informazioni di cluster")
cl["status"] = api("/cluster/status")
cl["resources"] = api("/cluster/resources")
cl["ha_status"] = api("/cluster/ha/status/current")
cl["ha_resources"] = api("/cluster/ha/resources")
cl["ha_rules"] = api("/cluster/ha/rules")
cl["backup"] = api("/cluster/backup")
cl["not_backed_up"] = api("/cluster/backup-info/not-backed-up")
cl["replication"] = api("/cluster/replication")
cl["options"] = api("/cluster/options")
cl["sdn_zones"] = api("/cluster/sdn/zones")
# Il primo dei due interruttori del firewall, e le regole che valgono per tutti.
cl["fw_options"] = api("/cluster/firewall/options")
cl["fw_rules"] = api("/cluster/firewall/rules")
cl["fw_groups"] = api("/cluster/firewall/groups")
# Le DEFINIZIONI degli storage: la vista per nodo dice cosa e' attivo, questa
# dice com'e' dichiarato (nodes=, shared, prune).
cl["storage_def"] = api("/storage")
# Dove finiscono gli avvisi. Un backup fallito che non avvisa nessuno e' un
# backup che non c'e'.
cl["notif_endpoints"] = api("/cluster/notifications/endpoints")
cl["notif_matchers"] = api("/cluster/notifications/matchers")
cl["ceph"] = api("/cluster/ceph/status", "", 15)

d = raccogli_nodo([sys.executable, "-"], N)
if d:
    out["nodi"][N] = d

if TUTTO_CLUSTER and cl["status"]:
    for n in cl["status"]:
        if n.get("type") != "node" or n.get("local") or not n.get("online"):
            continue
        nome, ip = n.get("name"), n.get("ip")
        prog("nodo %s (%s) via ssh interno al cluster" % (nome, ip))
        d = raccogli_nodo(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                           "-o", "StrictHostKeyChecking=accept-new", "root@%s" % ip, "python3 -"], nome)
        if d:
            out["nodi"][d["nodo"].get("hostname", nome)] = d
    for n in cl["status"]:
        if n.get("type") == "node" and not n.get("online"):
            out["errori"].append("nodo %s OFFLINE: non raccolto" % n.get("name"))

print(json.dumps(out))
'''


def raccogli(solo_accese: bool, performance: bool, tutto_cluster: bool, max_vm: int = 0) -> dict:
    nodo_src = (COLLECTOR_NODO
                .replace("__SOLO_ACCESE__", "True" if solo_accese else "False")
                .replace("__PERFORMANCE__", "True" if performance else "False")
                .replace("__MAX_VM__", str(int(max_vm))))
    script = (COLLECTOR_CLUSTER
              .replace("__TUTTO_CLUSTER__", "True" if tutto_cluster else "False")
              .replace("__SRC_B64__", base64.b64encode(nodo_src.encode()).decode())
              .replace("__VER__", VERSIONE_SCRIPT))
    print(c("Raccolta dati in corso: una connessione, un collector sul nodo d'ingresso…", GRIGIO), file=sys.stderr)
    txt = esegui_collector(script)
    if not txt.strip():
        return {}
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        print("Il collector ha restituito un output non JSON:\n" + txt[:600], file=sys.stderr)
        return {}


# ────────────────────────────── parsing ──────────────────────────────

def normalizza_config(cfg) -> dict:
    """L'API restituisce numeri come int (cores: 8, balloon: 0): tutto a
    stringa, come da `qm config`, così i parser hanno un solo formato."""
    return {k: str(v) for k, v in (cfg or {}).items() if k != "digest"}


def parse_dischi(cfg: dict) -> list:
    dischi = []
    for chiave, valore in cfg.items():
        if re.match(r"^(scsi|virtio|sata|ide|efidisk|tpmstate)\d+$", chiave):
            p = {"bus": chiave}
            parti = valore.split(",")
            p["volume"] = parti[0] if parti else ""
            for x in parti[1:]:
                if "=" in x:
                    k, v = x.split("=", 1); p[k] = v
            dischi.append(p)
    return dischi


def dischi_dati(cfg: dict) -> list:
    """Esclude cdrom, cloudinit, efidisk, tpmstate."""
    return [d for d in parse_dischi(cfg)
            if d.get("media") != "cdrom" and d.get("volume") not in ("none", "")
            and not d["bus"].startswith(("efidisk", "tpmstate"))]


_RE_MAC = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")


def parse_reti(cfg: dict) -> list:
    """netN è 'virtio=MAC,bridge=vmbr0,tag=4': il modello è la CHIAVE del
    campo il cui valore è un MAC (verificato su VM reali)."""
    reti = []
    for chiave, valore in cfg.items():
        if re.match(r"^net\d+$", chiave):
            p = {"iface": chiave}
            for x in valore.split(","):
                if "=" not in x:
                    continue
                k, v = x.split("=", 1)
                if _RE_MAC.match(v):
                    p["modello"], p["mac"] = k, v
                else:
                    p[k] = v
            reti.append(p)
    return reti


def vcpu_di(cfg: dict) -> int:
    return int(cfg.get("cores", "1") or 1) * int(cfg.get("sockets", "1") or 1)


def ram_gb_di(cfg: dict) -> float:
    return int(cfg.get("memory", "0") or 0) / 1024


_RE_SIZE = re.compile(r"^([\d.]+)([KMGT])$")
_GB = {"K": 1 / 1024**2, "M": 1 / 1024, "G": 1, "T": 1024}


def disco_gb_di(cfg: dict) -> float:
    tot = 0.0
    for d in dischi_dati(cfg):
        m = _RE_SIZE.match(d.get("size", ""))
        if m:
            tot += float(m.group(1)) * _GB[m.group(2)]
    return tot


def stato_ballooning(cfg: dict) -> str:
    mem = int(cfg.get("memory", "0") or 0)
    b = cfg.get("balloon")
    if b in (None, ""):
        return "attivo_default"
    b = int(b)
    return "assente" if b == 0 else ("presente_fermo" if b == mem else ("attivo" if b < mem else "sconosciuto"))


_RE_ATA_ATTR = re.compile(r"^\s*(\d+)\s+(\S+)\s+\S+\s+\d+\s+\d+\s+\d+\s+\S+\s+\S+\s+\S+\s+(\d+)")


def parse_smart(out: str, device: str) -> dict:
    """Due formati (verificati su dischi reali): tabella ATA e chiave:valore
    NVMe. Salute ancorata alla frase esatta — la tabella ATA ha la colonna
    WHEN_FAILED, che contiene "FAILED" su ogni disco sano."""
    if not out:
        return {}
    m = re.search(r"self-assessment test result:\s*(\S+)", out)
    r = {"salute": m.group(1) if m else "sconosciuta"}
    if "/nvme" in device:
        for k, rx, conv in (("nvme_critical_warning", r"Critical Warning:\s*(0x[0-9a-fA-F]+)", str),
                            ("nvme_percentage_used", r"Percentage Used:\s*(\d+)%", int),
                            ("nvme_media_errors", r"Media and Data Integrity Errors:\s*(\d+)", int),
                            ("nvme_spare", r"Available Spare:\s*(\d+)%", int),
                            ("power_on_hours", r"Power On Hours:\s*([\d,]+)", lambda s: int(s.replace(",", ""))),
                            ("temperatura", r"^Temperature:\s*(\d+)", int)):
            mm = re.search(rx, out, re.M)
            if mm:
                r[k] = conv(mm.group(1))
    else:
        attrs = {}
        for riga in out.splitlines():
            mm = _RE_ATA_ATTR.match(riga)
            if mm:
                attrs[mm.group(2)] = int(mm.group(3))
        r["ata_reallocated"] = attrs.get("Reallocated_Sector_Ct")
        r["ata_pending"] = attrs.get("Current_Pending_Sector")
        r["ata_uncorrectable"] = attrs.get("Offline_Uncorrectable")
        r["ata_crc"] = attrs.get("UDMA_CRC_Error_Count")
        r["power_on_hours"] = attrs.get("Power_On_Hours")
        t = attrs.get("Temperature_Celsius", attrs.get("Airflow_Temperature_Cel"))
        if t is not None:
            r["temperatura"] = t
    return r


def parse_corosync_conf(testo: str) -> dict:
    nodi, in_node, cur = [], False, {}
    for riga in testo.splitlines():
        s = riga.strip()
        if s.startswith("node {"):
            in_node, cur = True, {}
        elif in_node and s == "}":
            nodi.append(cur); in_node = False
        elif in_node and ":" in s:
            k, v = s.split(":", 1); cur[k.strip()] = v.strip()
    d = {"nodi": nodi}
    for k in ("cluster_name", "config_version", "link_mode", "transport", "token", "secauth"):
        m = re.search(r"^\s*%s:\s*(\S+)" % k, testo, re.M)
        if m:
            d[k] = m.group(1)
    d["qdevice"] = "device {" in testo
    d["n_link"] = len(re.findall(r"linknumber:\s*\d+", testo))
    return d


def parse_pvecm(testo: str) -> dict:
    d = {}
    for k, rx in (("quorate", r"Quorate:\s*(\S+)"), ("expected", r"Expected votes:\s*(\d+)"),
                  ("total", r"Total votes:\s*(\d+)"), ("quorum", r"Quorum:\s*(\d+)"),
                  ("nodes", r"Nodes:\s*(\d+)"), ("transport", r"Transport:\s*(\S+)"),
                  ("qdevice_votes", r"Qdevice votes:\s*(\d+)")):
        m = re.search(rx, testo)
        if m:
            d[k] = m.group(1)
    return d


def parse_cfgtool(testo: str) -> list:
    """LINK ID N, poi 'addr = X' e 'nodeid: N: connected' (formato verificato)."""
    link, cur = [], None
    for riga in testo.splitlines():
        m = re.match(r"^LINK ID (\d+)", riga)
        if m:
            cur = {"id": int(m.group(1)), "addr": None, "nodi": {}}; link.append(cur); continue
        if cur is None:
            continue
        m = re.search(r"addr\s*=\s*(\S+)", riga)
        if m:
            cur["addr"] = m.group(1); continue
        m = re.search(r"nodeid:\s*(\d+):\s*(\S+)", riga)
        if m:
            cur["nodi"][m.group(1)] = m.group(2)
    return link


def parse_pveperf(testo: str) -> dict:
    d = {}
    for k, rx in (("bogomips", r"CPU BOGOMIPS:\s*([\d.]+)"), ("regex_s", r"REGEX/SECOND:\s*([\d.]+)"),
                  ("read_mbs", r"BUFFERED READS:\s*([\d.]+)"), ("seek_ms", r"AVERAGE SEEK TIME:\s*([\d.]+)"),
                  ("fsync_s", r"FSYNCS/SECOND:\s*([\d.]+)"), ("dns_ext_ms", r"DNS EXT:\s*([\d.]+)"),
                  ("dns_int_ms", r"DNS INT:\s*([\d.]+)")):
        m = re.search(rx, testo)
        if m:
            d[k] = float(m.group(1))
    return d


# un'ancora è `§8.3` o `§8.3 › Cache mode`; il titolo di una sottosezione può
# contenere virgole («Shares, hugepages, KSM»), quindi si chiude solo a fine
# stringa o su un altro §
_RE_ANCORA = re.compile(r"§[0-9A-Z]+(?:\.[0-9]+)?(?:\s*›\s*[^\"'`§\n]+)?")


def ancore_di(fonte: str) -> list:
    """Le regole citate da un rilievo. Una citazione può nominarne più d'una
    («manuale §9.2, §9.6, §12.1»), e le fonti che non sono il manuale (pveperf,
    blockstat, Proxreporter) hanno il proprio testo sotto il loro nome."""
    if not fonte:
        return []
    trovate = [a.strip().rstrip(",;.") for a in _RE_ANCORA.findall(fonte)]
    if not trovate and fonte in REGOLE:
        trovate = [fonte]
    return [a for a in trovate if a in REGOLE]


def ordine_regola(ancora: str):
    """Prima il manuale in ordine di sezione, poi le fonti che manuale non sono."""
    if not ancora.startswith("§"):
        return (2, 0, 0, ancora)
    corpo = ancora.lstrip("§").split("›")[0].strip()
    parte, _, sez = corpo.partition(".")
    try:
        return (0, int(parte), int(sez or 0), ancora)
    except ValueError:                       # appendice: §A.8
        return (1, 0, int(sez or 0), ancora)


def media(campioni, k):
    v = [x[k] for x in (campioni or []) if isinstance(x, dict) and isinstance(x.get(k), (int, float))]
    return (sum(v) / len(v)) if v else None


def massimo(campioni, k):
    v = [x[k] for x in (campioni or []) if isinstance(x, dict) and isinstance(x.get(k), (int, float))]
    return max(v) if v else None


def gb(n) -> str:
    try:
        return f"{float(n) / 1024**3:.1f} GB"
    except (TypeError, ValueError):
        return "—"


def durata(sec) -> str:
    try:
        sec = int(sec)
    except (TypeError, ValueError):
        return "—"
    g, r = divmod(sec, 86400); h, r = divmod(r, 3600)
    return f"{g}g {h}h" if g else f"{h}h {r // 60}m"


def pct(n, tot):
    try:
        return 100.0 * float(n) / float(tot) if float(tot) else None
    except (TypeError, ValueError):
        return None


def mappa_rete(nodo: dict) -> dict:
    """ip → interfaccia; interfaccia → genitore (vlan@parent, slave→master);
    bond/bridge → slave. Per sapere su quali NIC fisiche passa un anello."""
    ip2if, parent, slaves = {}, {}, {}
    for riga in (nodo.get("ip_addr") or "").splitlines():
        m = re.match(r"^\d+:\s+(\S+)\s+inet\s+(\S+)/", riga)
        if m:
            ip2if[m.group(2)] = m.group(1)
    for riga in (nodo.get("ip_link") or "").splitlines():
        m = re.match(r"^\d+:\s+([^:@]+)(?:@(\S+))?:\s.*", riga)
        if not m:
            continue
        nome, at = m.group(1), m.group(2)
        if at and at != "NONE":
            parent[nome] = at
        mm = re.search(r"\bmaster (\S+)", riga)
        if mm:
            slaves.setdefault(mm.group(1), []).append(nome)
    for bond, testo in (nodo.get("bonding") or {}).items():
        slaves[bond] = re.findall(r"Slave Interface:\s*(\S+)", testo)
    return {"ip2if": ip2if, "parent": parent, "slaves": slaves}


def nic_fisiche(iface: str, topo: dict, visti=None) -> set:
    visti = visti or set()
    if iface in visti:
        return set()
    visti.add(iface)
    if iface in topo["parent"]:
        return nic_fisiche(topo["parent"][iface], topo, visti)
    if iface in topo["slaves"]:
        s = set()
        for x in topo["slaves"][iface]:
            if not x.startswith(("tap", "veth", "fwln", "fwpr", "fwbr")):  # porte dei guest, non NIC
                s |= nic_fisiche(x, topo, visti)
        return s
    return {iface}


# ────────────────────────────── rilievi ──────────────────────────────

BLOCCANTE, ATTENZIONE, INFO = "BLOCCANTE", "ATTENZIONE", "INFO"
SIMBOLO = {BLOCCANTE: "🔴", ATTENZIONE: "🟡", INFO: "ℹ️ "}
ORDINE_LIV = {BLOCCANTE: 0, ATTENZIONE: 1, INFO: 2}


@dataclass
class Rilievo:
    livello: str
    ambito: str
    messaggio: str
    fonte: str = ""
    # Il comando che chiude QUESTO rilievo, se esiste. Non tutti ne hanno uno:
    # «VLAN 20 ha reti diverse fra i nodi» non ha un comando, ha una decisione.
    # Dove non c'è si lascia vuoto invece di inventarne uno approssimativo.
    comando: str = ""


@dataclass
class Esito:
    rilievi: list = field(default_factory=list)

    def add(self, livello, ambito, messaggio, fonte="", comando=""):
        self.rilievi.append(Rilievo(livello, ambito, messaggio, fonte, comando))

    def agibili(self) -> list:
        """I rilievi che hanno un comando. È la differenza fra un elenco di cose
        che non vanno e una lista di cose da fare."""
        return [r for r in self.rilievi if r.comando]

    def conta(self, livello):
        return sum(1 for r in self.rilievi if r.livello == livello)


# ────────────────────────────── tipologie di carico (manuale, Parte 9) ──────────────────────────────

PROFILI = {
    "1": {"nome": "Domain controller / DNS", "vcpu_max": 4, "balloon": "min_eq_max",
          "cpu_type_evita": {"kvm64"}, "numa": False, "cache": "none", "multiqueue": "no",
          "protection": True, "ha": True, "fonte": "manuale §9.1",
          "extra": ["Almeno un secondo domain controller su un altro nodo fisico.",
                    "Mai il rollback di snapshot come ripristino (USN rollback)."]},
    "2": {"nome": "Database", "vcpu_max": None, "balloon": "disabilitato",
          "cpu_type_evita": {"kvm64", "x86-64-v2-AES"}, "numa": True, "cache": "none", "multiqueue": "no",
          "protection": True, "ha": True, "dischi_min": 2, "fonte": "manuale §9.4",
          "extra": ["Dischi dati e log/WAL su dischi virtuali separati.",
                    "aio=native SOLO con raw + cache=none + iothread; altrimenti io_uring."]},
    "3": {"nome": "Applicativo / web / monitoraggio", "vcpu_max": 8, "balloon": "attivo_salvo_java",
          "cpu_type_evita": {"kvm64"}, "numa": False, "cache": "none", "multiqueue": "no",
          "protection": False, "fonte": "manuale §9.3, §9.8",
          "extra": ["Con JVM (Zabbix Java gateway, Elastic, Tomcat) il ballooning va valutato: la JVM non restituisce memoria."]},
    "4": {"nome": "Rete: firewall, proxy, load balancer", "vcpu_max": None, "balloon": "disabilitato",
          "cpu_type_evita": set(), "cpu_type_richiede": "host", "numa": False, "cache": "none",
          "multiqueue": "tutte_le_nic", "protection": True, "fonte": "manuale §9.5, §9.3",
          "extra": ["Firewall PVE per-interfaccia off se il filtraggio avviene nell'appliance stessa."]},
    "5": {"nome": "Dati: file server, log/SIEM, backup server", "vcpu_max": None, "balloon": "attivo_min_alto",
          "cpu_type_evita": {"kvm64"}, "numa": False, "cache": "none", "multiqueue": "molti_client",
          "protection": True, "dischi_min": 2, "fonte": "manuale §9.2, §9.6, §12.1",
          "extra": ["Disco dati separato dal disco di sistema (backup e throttling distinti).",
                    "Valutare un limite di throughput (mbps_wr) sul disco dati: è il vicino rumoroso tipico.",
                    "Se è un backup server: datastore su storage con checksum (ZFS) e backup=0 sul disco del datastore."]},
    "6": {"nome": "Terminal server / VDI", "vcpu_max": None, "balloon": "attivo", "cpu_type_evita": set(),
          "cpu_type_richiede": "host", "numa": None, "cache": "none", "multiqueue": "no",
          "protection": False, "fonte": "manuale §9.7"},
    "7": {"nome": "Test, legacy, replica", "vcpu_max": None, "cpu_type_evita": set(), "numa": False,
          "cache": None, "multiqueue": "no", "protection": False, "onboot_no": True, "legacy": True,
          "fonte": "manuale §8.6, §9.9, §4.5",
          "extra": ["Test: non condividere rete/VLAN con la produzione se il traffico non è filtrato.",
                    "Legacy senza VirtIO: isolare a livello di rete, è debito tecnico con una scadenza.",
                    "Replica/DR: mai onboot insieme alla primaria (conflitto IP/hostname); verificare che la schedulazione pvesr esista."]},
}
NON_CLASSIFICATA = "0"
# I 13 profili della versione 1 (uno per paragrafo del manuale, Parte 9) → i 7 di oggi, raggruppati per regole uguali.
CONVERSIONE_PROFILI_V1 = {"1": "1", "2": "5", "3": "3", "4": "2", "5": "4", "6": "5", "7": "6", "8": "3",
                          "9": "7", "10": "7", "11": "7", "12": "5", "13": "4", "0": "0"}
VERSIONE_PROFILI = "2"

# parole nel nome della VM (o nel SO visto dall'agent) → tipologia proposta; ordine = priorità
INDIZI_PROFILO = [
    ("7", r"test|tmpl|template|replica|repl\b|\bdev\b|\blab\b|prova|demo|\bold\b|legacy|eve-ng|gns3"),
    ("1", r"\b(dc|ad|dns|addc|pdc|domain)\b|\bdc\d|-dc\b|dc0\d"),
    ("2", r"sql|\bdb\b|database|postgres|\bpg\b|oracle|mysql|maria|mongo"),
    ("4", r"\bfw\b|firewall|pfsense|opnsense|fortigate|sophos|utm|proxy|haproxy|traefik|\blb\b|\bsbc\b|vpn|wireguard|netbird|"
          r"\bsns\b|stormshield|\bchr\b|routeros|mikrotik|omada|unifi|ubnt|router"),
    ("5", r"file|\bfs\b|\bnas\b|synology|\bdsm\b|truenas|share|\bftp\b|pbs|veeam|backup|\blog|graylog|siem|wazuh|elastic|syslog|nakivo"),
    ("6", r"\brds\b|\bts\b|terminal|\bvdi\b|\brdh\b|remoteapp|citrix|rdsh"),
    ("3", r"zabbix|grafana|prtg|observium|opmanager|nagios|icinga|monitor|checkmk|smtp|mail|esva|dmarc|\bweb\b|\bwww\b|\bapp\b|nginx|apache|iis|tomcat"),
]


def suggerisci_profilo(vm) -> str:
    """Proposta dal nome della VM e dal SO dell'agent. Torna NON_CLASSIFICATA se
    nessun indizio: meglio nessuna proposta che una sbagliata."""
    testo = " ".join(x for x in (vm.nome, (vm.config or {}).get("name", ""), so_di(vm)) if x).lower()
    for pid, rx in INDIZI_PROFILO:
        if re.search(rx, testo):
            return pid
    return NON_CLASSIFICATA


def converti_profili(noti: dict) -> dict:
    """File di profili della versione 1 (13 tipologie) → versione 2 (7)."""
    if noti.get("_versione") == VERSIONE_PROFILI:
        return {k: v for k, v in noti.items() if not k.startswith("_")}
    return {k: CONVERSIONE_PROFILI_V1.get(str(v), NON_CLASSIFICATA) for k, v in noti.items() if not k.startswith("_")}


@dataclass
class VM:
    vmid: str
    nome: str
    nodo: str
    config: dict
    status: dict = field(default_factory=dict)
    snapshot: list = field(default_factory=list)
    pending: list = field(default_factory=list)
    agent: dict = field(default_factory=dict)
    rrd: list = field(default_factory=list)
    lista: dict = field(default_factory=dict)

    @property
    def running(self):
        return (self.status or {}).get("status") == "running"


def costruisci_vms(inv: dict) -> list:
    vms = []
    for nome_nodo, blocco in (inv.get("nodi") or {}).items():
        for vmid, d in (blocco.get("vms") or {}).items():
            cfg = normalizza_config(d.get("config"))
            vms.append(VM(vmid=vmid, nome=cfg.get("name", f"vmid-{vmid}"), nodo=nome_nodo, config=cfg,
                          status=d.get("status") or {}, snapshot=d.get("snapshot") or [],
                          pending=d.get("pending") or [], agent=d.get("agent") or {},
                          rrd=d.get("rrd") or [], lista=d.get("lista") or {}))
    return sorted(vms, key=lambda v: int(v.vmid))


def nodo_ingresso(inv: dict) -> dict:
    return ((inv.get("nodi") or {}).get(inv.get("ingresso")) or {}).get("nodo") or {}


def in_cluster(inv: dict) -> bool:
    return any(x.get("type") == "cluster" for x in (inv.get("cluster", {}).get("status") or []))


# ────────────────────────────── controlli: cluster e corosync ──────────────────────────────

def controlla_cluster(inv: dict, esito: Esito):
    cl = inv.get("cluster", {})
    stato = cl.get("status") or []
    testa = next((x for x in stato if x.get("type") == "cluster"), None)
    nodi = [x for x in stato if x.get("type") == "node"]
    if not testa:
        esito.add(INFO, "Cluster", "Nodo non in cluster (host singolo): controlli di quorum e corosync saltati.")
        return
    A = f"Cluster {testa.get('name')}"
    F = "manuale §3"
    if not testa.get("quorate"):
        esito.add(BLOCCANTE, A, "Cluster NON quorato: le VM in HA non partono e /etc/pve è in sola lettura.", "manuale §3.4")
    offline = [n["name"] for n in nodi if not n.get("online")]
    if offline:
        esito.add(BLOCCANTE, A, f"Nodi offline: {', '.join(offline)}.", F)
    raccolti = set((inv.get("nodi") or {}).keys())
    mancanti = [n["name"] for n in nodi if n.get("online") and n["name"] not in raccolti]
    if mancanti:
        esito.add(ATTENZIONE, A, f"Nodi online ma non raccolti (ssh interno al cluster fallito): {', '.join(mancanti)}. "
                  "Verificare la fiducia SSH fra i nodi (pvecm updatecerts).", "manuale §3.7")

    n0 = nodo_ingresso(inv)
    conf = parse_corosync_conf(n0.get("corosync_conf") or "")
    pv = parse_pvecm(n0.get("pvecm_status") or "")
    n_nodi = len(conf.get("nodi") or nodi)
    qdev = conf.get("qdevice") or bool(pv.get("qdevice_votes"))
    if n_nodi == 2 and not qdev:
        esito.add(BLOCCANTE, A, "Due nodi senza QDevice: la caduta di un nodo blocca anche l'altro. Serve un terzo voto.", "manuale §3.5")
    elif n_nodi % 2 == 0 and not qdev:
        esito.add(INFO, A, f"{n_nodi} nodi (pari) senza QDevice: perdere metà dei nodi toglie il quorum.", "manuale §3.5")
    if pv.get("expected") and pv.get("total") and pv["expected"] != pv["total"]:
        esito.add(ATTENZIONE, A, f"Voti attesi {pv['expected']}, totali {pv['total']}: un nodo manca o è stato forzato 'pvecm expected'.", "manuale §3.7")

    for nome, blocco in (inv.get("nodi") or {}).items():
        nd = blocco.get("nodo") or {}
        B = f"{A} — anelli su {nome}"
        link = parse_cfgtool(nd.get("corosync_cfgtool") or "")
        n_link = len(link) or conf.get("n_link", 0)
        if n_link < 2:
            esito.add(ATTENZIONE, B, f"{n_link} anello/i: ne servono almeno due su percorsi fisici distinti.", "manuale §3.3")
        for l in link:
            nc = [nid for nid, st in l["nodi"].items() if st not in ("connected", "localhost")]
            if nc:
                esito.add(BLOCCANTE, B, f"Anello {l['id']} ({l['addr']}): nodi non connessi {', '.join(nc)}.", "manuale §3.3")
        topo = mappa_rete(nd)
        percorsi = {}
        mgmt_subnets = {x.get("ip", "").rsplit(".", 1)[0] for x in nodi if x.get("ip")}
        for l in link:
            if not l.get("addr"):
                continue
            iface = topo["ip2if"].get(l["addr"])
            if not iface:
                continue
            percorsi[l["id"]] = nic_fisiche(iface, topo)
            bond = iface if iface in topo["slaves"] and iface.startswith("bond") else topo["parent"].get(iface, "")
            if bond.startswith("bond"):
                esito.add(INFO, B, f"Anello {l['id']} ({l['addr']}) su {iface} → {bond} ({', '.join(topo['slaves'].get(bond, []))}): "
                          "un anello su un bond è un unico dominio di guasto per configurazione e per switch.", "manuale §1.3")
            if l["id"] != 0 and l["addr"].rsplit(".", 1)[0] in mgmt_subnets:
                esito.add(INFO, B, f"Anello {l['id']} ({l['addr']}) sulla rete di management: ok come secondario.", "manuale §1.3")
        ids = list(percorsi)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                comuni = percorsi[ids[i]] & percorsi[ids[j]]
                if comuni:
                    esito.add(ATTENZIONE, B, f"Gli anelli {ids[i]} e {ids[j]} passano dalle stesse NIC fisiche "
                              f"({', '.join(sorted(comuni))}): ridondanza solo logica.", "manuale §3.3, §1.3")
        addrs = [l["addr"] for l in link if l.get("addr")]
        if len(addrs) >= 2 and len({a.rsplit(".", 1)[0] for a in addrs}) == 1:
            esito.add(ATTENZIONE, B, "Tutti gli anelli nella stessa subnet.", "manuale §3.3")
        for ip, p in (nd.get("ping_ring") or {}).items():
            if p.get("loss"):
                esito.add(BLOCCANTE, B, f"Perdita pacchetti {p['loss']}% verso {ip}.", "manuale §3.3")
            elif p.get("avg_ms") is not None and p["avg_ms"] > 5:
                esito.add(BLOCCANTE, B, f"Latenza {p['avg_ms']:.1f} ms verso {ip}: corosync tratta una rete lenta come caduta.", "manuale §3.3")
            elif p.get("avg_ms") is not None and p["avg_ms"] > 2:
                esito.add(ATTENZIONE, B, f"Latenza {p['avg_ms']:.1f} ms verso {ip} (indicativo: <2 ms su LAN).", "manuale §3.3")

    ha_res = cl.get("ha_resources") or []
    if ha_res and n_nodi < 3 and not qdev:
        esito.add(BLOCCANTE, A, f"{len(ha_res)} risorse HA su {n_nodi} nodi senza QDevice.", "manuale §7.2")
    if not ha_res and n_nodi >= 3:
        esito.add(INFO, A, "Nessuna risorsa in HA: con ≥3 nodi l'alta affidabilità è disponibile ma non usata.", "manuale §7.1")
    for r in (cl.get("ha_status") or []):
        if r.get("type") == "service" and str(r.get("state", "")).startswith("error"):
            esito.add(BLOCCANTE, A, f"Risorsa HA {r.get('sid')} in stato {r.get('state')}.", "manuale §7.4")

    jobs = cl.get("backup") or []
    if not jobs:
        esito.add(BLOCCANTE, A, "Nessun job di backup configurato nel cluster.", "manuale §12.1")
    for j in jobs:
        if not j.get("enabled", 1):
            esito.add(ATTENZIONE, A, f"Job di backup {j.get('id')} disabilitato.", "manuale §12.1")
        if not (j.get("fleecing") or {}).get("enabled"):
            esito.add(INFO, A, f"Job {j.get('id')} senza fleecing: durante il backup le scritture dei guest aspettano il target.", "manuale §12.6")
        if (j.get("prune-backups") or {}).get("keep-all"):
            esito.add(INFO, A, f"Job {j.get('id')}: keep-all, nessuna retention.", "manuale §12.7")
    nbu = cl.get("not_backed_up") or []
    if nbu:
        esito.add(ATTENZIONE, A, f"{len(nbu)} guest senza alcun job di backup: " +
                  ", ".join(f"{x.get('vmid')} {x.get('name', '')}".strip() for x in nbu[:12]) + (" …" if len(nbu) > 12 else "") + ".", "manuale §12.1")
    for r in (cl.get("replication") or []):
        if r.get("error"):
            esito.add(BLOCCANTE, A, f"Replica {r.get('id')}: errore — {str(r.get('error'))[:120]}", "manuale §4.5")
    ceph = cl.get("ceph")
    if isinstance(ceph, dict) and (ceph.get("health") or {}).get("status") not in (None, "HEALTH_OK"):
        h = ceph["health"]["status"]
        esito.add(BLOCCANTE if h == "HEALTH_ERR" else ATTENZIONE, A, f"Ceph: {h}.", "manuale §6.6")


# ────────────────────────────── controlli: nodo ──────────────────────────────

SERVIZI_CORE = ("pve-cluster", "pvedaemon", "pveproxy", "pvestatd")


def controlla_nodo(nome: str, blocco: dict, inv: dict, esito: Esito):
    nodo = blocco.get("nodo") or {}
    st = nodo.get("status") or {}
    A = f"Nodo {nome}"
    cluster = in_cluster(inv)

    sub = nodo.get("subscription") or {}
    std = {s.get("handle"): s.get("status") for s in (nodo.get("apt_repos") or {}).get("standard-repos", [])}
    ent, nosub, test = std.get("enterprise") == 1, std.get("no-subscription") == 1, std.get("test") == 1
    attiva = sub.get("status") == "active"
    if ent and not attiva:
        esito.add(BLOCCANTE, A, f"Repository enterprise attivo ma sottoscrizione '{sub.get('status', 'assente')}': "
                  "apt riceve 401, il nodo NON si aggiorna.", "manuale §1.6, §2.1")
    if not attiva and not nosub and not ent:
        esito.add(BLOCCANTE, A, "Nessun repository Proxmox attivo: il nodo non riceve aggiornamenti.", "manuale §2.1")
    if not attiva and nosub:
        esito.add(ATTENZIONE, A, "Repository no-subscription in produzione: l'enterprise riceve solo aggiornamenti ampiamente testati.", "manuale §1.6")
    if test:
        esito.add(ATTENZIONE, A, "Repository pvetest attivo su un nodo di produzione.", "manuale §1.6")
    if attiva and sub.get("nextduedate"):
        try:
            gg = (datetime.strptime(sub["nextduedate"], "%Y-%m-%d") - datetime.now()).days
            if gg < 30:
                esito.add(ATTENZIONE, A, f"Sottoscrizione in scadenza tra {gg} giorni.", "manuale §1.6")
        except ValueError:
            pass
    upd = nodo.get("apt_update") or []
    if upd:
        imp = [u["Package"] for u in upd if re.match(r"(pve-manager|proxmox-kernel|pve-kernel|qemu-server|pve-qemu-kvm|corosync|zfs)", u.get("Package", ""))]
        esito.add(ATTENZIONE, A, f"{len(upd)} aggiornamenti disponibili non installati" + (f" (tra cui {', '.join(imp[:5])})" if imp else "") + ".", "manuale §19.1")
    running = ((st.get("current-kernel") or {}).get("release") or "")
    ker = sorted(re.findall(r"vmlinuz-(\S+)", nodo.get("kernel_boot") or ""))
    if running and ker and running in ker and ker[-1] != running:
        esito.add(ATTENZIONE, A, f"Kernel installato più recente ({ker[-1]}) di quello in esecuzione ({running}): riavvio pendente.", "manuale §19.1")
    srv = {s.get("name"): s for s in (nodo.get("services") or [])}
    for n in list(SERVIZI_CORE) + (["corosync"] if cluster else []):
        s = srv.get(n)
        if s and (s.get("state") != "running" or s.get("unit-state") not in ("enabled", "static", "indirect")):
            esito.add(BLOCCANTE, A, f"Servizio {n}: {s.get('state')}/{s.get('unit-state')}.", "manuale §20.2")
    for ce in (nodo.get("certificati") or []):
        if ce.get("notafter"):
            gg = (ce["notafter"] - time.time()) / 86400
            if gg < 0:
                esito.add(BLOCCANTE, A, f"Certificato {ce.get('filename')} SCADUTO.", "manuale §2.7")
            elif gg < 30:
                esito.add(ATTENZIONE, A, f"Certificato {ce.get('filename')} scade tra {gg:.0f} giorni.", "manuale §2.7")
    mem, sw, rf = st.get("memory") or {}, st.get("swap") or {}, st.get("rootfs") or {}
    p = pct(mem.get("used"), mem.get("total"))
    if p is not None and p > 90:
        esito.add(ATTENZIONE, A, f"RAM host al {p:.0f}%: poco margine per ballooning, ARC e picchi.", "manuale §1.5")
    p = pct(sw.get("used"), sw.get("total"))
    if p is not None and p > 25:
        esito.add(ATTENZIONE, A, f"Swap dell'host al {p:.0f}%: l'hypervisor pagina, ne risentono tutte le VM.", "manuale §1.5")
    p = pct(rf.get("used"), rf.get("total"))
    if p is not None and p >= 85:
        esito.add(BLOCCANTE if p >= 95 else ATTENZIONE, A, f"Filesystem di root al {p:.0f}%.", "manuale §4.6")
    if (st.get("uptime") or 0) > 365 * 86400:
        esito.add(INFO, A, f"Uptime {durata(st.get('uptime'))}: oltre un anno senza riavvio.", "manuale §19.1")
    cpus = (st.get("cpuinfo") or {}).get("cpus")
    vms = [v for v in costruisci_vms(inv) if v.nodo == nome and v.running]
    vcpu_tot = sum(vcpu_di(v.config) for v in vms)
    mem_tot = sum(int(v.config.get("memory", 0) or 0) for v in vms) * 1024**2
    if cpus and vcpu_tot:
        r = vcpu_tot / cpus
        esito.add(ATTENZIONE if r > 4 else INFO, A, f"vCPU delle VM accese: {vcpu_tot} su {cpus} CPU logiche "
                  f"({r:.1f}:1; tipico 3-4:1, 1:1 per database e appliance).", "manuale §8.1 › Overcommit di vCPU")
    if mem.get("total") and mem_tot > mem["total"]:
        esito.add(ATTENZIONE, A, f"RAM assegnata alle VM accese ({gb(mem_tot)}) supera quella dell'host ({gb(mem['total'])}): overcommit reale.", "manuale §1.5")
    for s in (nodo.get("storage") or []):
        S = f"{A} — storage {s.get('storage')}"
        if s.get("enabled") and not s.get("active"):
            esito.add(BLOCCANTE, S, "Storage abilitato ma NON attivo.", "manuale §4.1")
        p = pct(s.get("used"), s.get("total"))
        if p is not None and p >= 85:
            esito.add(BLOCCANTE if p >= 95 else ATTENZIONE, S, f"Occupazione al {p:.0f}%.", "manuale §4.6")
    for riga in (nodo.get("zpool_list") or "").splitlines():
        f = riga.split("\t")
        if len(f) >= 6:
            Z = f"{A} — pool ZFS {f[0]}"
            if f[5] != "ONLINE":
                esito.add(BLOCCANTE, Z, f"Stato {f[5]} (atteso ONLINE).", "manuale §4.2")
            cap = f[4].rstrip("%")
            if cap.isdigit() and int(cap) >= 80:
                esito.add(ATTENZIONE, Z, f"Pool al {cap}%: ZFS rallenta oltre l'80%.", "manuale §4.6")
            if len(f) >= 7 and f[6].rstrip("%").isdigit() and int(f[6].rstrip("%")) >= 50:
                esito.add(INFO, Z, f"Frammentazione {f[6]}.", "manuale §4.2")
    zx = (nodo.get("zpool_status_x") or "").strip()
    if zx and "all pools are healthy" not in zx:
        for blocco in re.split(r"\n(?=\s*pool:)", zx):
            pool = re.search(r"pool:\s*(\S+)", blocco)
            stato = re.search(r"status:\s*(.+?)(?:\n\s*action:|\Z)", blocco, re.S)
            testo = re.sub(r"\s+", " ", stato.group(1)).strip() if stato else re.sub(r"\s+", " ", blocco)[:160]
            grave = re.search(r"corrupt|DEGRADED|FAULTED|UNAVAIL|unrecoverable", blocco, re.I)
            esito.add(BLOCCANTE if grave else ATTENZIONE, f"{A} — pool ZFS {pool.group(1) if pool else '?'}",
                      f"zpool status: {testo} Controllare `zpool status -v` e l'elenco dei file/zvol colpiti.", "manuale §4.2")
    if nodo.get("zpool_list") and not (nodo.get("zfs_arc_max") or "").strip("0"):
        esito.add(INFO, f"{A} — ZFS ARC", "zfs_arc_max non impostato: l'ARC cresce fino a metà della RAM e compete con le VM.", "manuale §4.2")
    for riga in (nodo.get("lvs") or "").splitlines():
        f = riga.split()
        if len(f) == 5:
            try:
                mp = float(f[4])
            except ValueError:
                continue
            if mp >= 80:
                esito.add(BLOCCANTE if mp >= 95 else ATTENZIONE, f"{A} — LVM-thin {f[1]}/{f[0]}", f"Metadati al {mp:.1f}%: il pool diventa di sola lettura quando finiscono.", "manuale §A.8")
    if (nodo.get("multipath") or "").strip():
        esito.add(INFO, f"{A} — multipath", "multipath attivo: i PV vanno su /dev/mapper/<WWID>, mai su /dev/sdX.", "manuale §A.8")
    td = nodo.get("timedatectl") or ""
    if re.search(r"System clock synchronized:\s*no", td):
        esito.add(BLOCCANTE, f"{A} — orario", "Orologio NON sincronizzato: rompe il cluster e Kerberos.", "manuale §2.5")
    elif re.search(r"NTP service:\s*inactive", td):
        esito.add(ATTENZIONE, f"{A} — orario", "Servizio NTP inattivo.", "manuale §2.5")
    for bond, testo in (nodo.get("bonding") or {}).items():
        slave = re.findall(r"Slave Interface:\s*(\S+)\nMII Status:\s*(\S+)", testo)
        giu = [s for s, stt in slave if stt != "up"]
        if giu:
            esito.add(BLOCCANTE, f"{A} — rete {bond}", f"Slave {', '.join(giu)} DOWN: bond degradato.", "manuale §2.6")
        if len(slave) == 1:
            esito.add(ATTENZIONE, f"{A} — rete {bond}", f"Bond con un solo slave ({slave[0][0]}): nessuna ridondanza, il bond è solo nominale.", "manuale §2.6")
    fw = re.search(r"Status:\s*(\S+)", nodo.get("pve_firewall") or "")
    if fw:
        esito.add(INFO, f"{A} — firewall PVE", f"Stato {fw.group(1)}: va attivo se il filtraggio non avviene altrove.", "manuale §15.3")
    bi = st.get("boot-info") or {}
    if bi:
        esito.add(INFO, A, f"Boot {str(bi.get('mode', '?')).upper()}, Secure Boot {'attivo' if bi.get('secureboot') else 'disattivo'}.", "manuale §2.1")
    if (st.get("ksm") or {}).get("shared"):
        esito.add(INFO, A, f"KSM attivo: {gb(st['ksm']['shared'])} di pagine condivise.", "manuale §8.2 › Shares, hugepages, KSM")


# ────────────────────────────── controlli: hardware ──────────────────────────────

def controlla_hardware(nome: str, blocco: dict, esito: Esito):
    nodo = blocco.get("nodo") or {}
    F = "Proxreporter hardware_monitor.py"
    per_dev = {d.get("devpath"): d for d in (nodo.get("disks") or [])}
    for dev, testo in (nodo.get("smart") or {}).items():
        s = parse_smart(testo, dev)
        info = per_dev.get(dev, {})
        A = f"Nodo {nome} — disco {os.path.basename(dev)} ({info.get('model', '?')})"
        if not s:
            continue
        if s.get("salute") == "FAILED" or info.get("health") not in (None, "PASSED", "OK", "UNKNOWN"):
            esito.add(BLOCCANTE, A, f"Salute SMART {s.get('salute')} / API {info.get('health')}: rischio di guasto imminente.", F)
        w = info.get("wearout")
        if isinstance(w, (int, float)) and w >= 0 and 100 - w >= 80:
            esito.add(BLOCCANTE if 100 - w >= 90 else ATTENZIONE, A, f"Usura {100 - w}% (vita residua {w}%).", "API disks/list")
        if s.get("nvme_critical_warning") not in (None, "0x00"):
            esito.add(BLOCCANTE, A, f"Critical Warning NVMe {s['nvme_critical_warning']} (atteso 0x00).", F)
        if s.get("nvme_media_errors"):
            esito.add(BLOCCANTE, A, f"{s['nvme_media_errors']} errori di integrità dati NVMe.", F)
        if s.get("nvme_spare") is not None and s["nvme_spare"] < 20:
            esito.add(ATTENZIONE, A, f"Spare NVMe disponibile {s['nvme_spare']}%.", F)
        for k, sb, txt in (("ata_reallocated", 10, "settori riallocati"), ("ata_pending", 10**9, "settori pending"),
                           ("ata_uncorrectable", 1, "settori non correggibili")):
            if s.get(k):
                esito.add(BLOCCANTE if s[k] >= sb else ATTENZIONE, A, f"{s[k]} {txt} (SMART).", F)
        if s.get("ata_crc"):
            esito.add(ATTENZIONE, A, f"{s['ata_crc']} errori CRC UDMA: di solito cavo o backplane.", F)
        t = s.get("temperatura")
        if t is not None and "/nvme" not in dev and t >= 45:
            # per NVMe il segnale autorevole è Critical Warning: verificato su disco reale a 65°C sano
            esito.add(BLOCCANTE if t >= 55 else ATTENZIONE, A, f"Temperatura {t}°C.", F)
    ce = sum(int(v["ce"]) for v in (nodo.get("edac") or {}).values() if str(v.get("ce", "")).isdigit())
    ue = sum(int(v["ue"]) for v in (nodo.get("edac") or {}).values() if str(v.get("ue", "")).isdigit())
    if ue:
        esito.add(BLOCCANTE, f"Nodo {nome} — memoria ECC", f"{ue} errori ECC NON corretti.", F)
    elif ce >= 10:
        esito.add(ATTENZIONE, f"Nodo {nome} — memoria ECC", f"{ce} errori ECC corretti: da tenere d'occhio.", F)
    md = nodo.get("mdstat") or ""
    for m in re.finditer(r"^(md\d+)\s*:\s*(\w+)", md, re.M):
        if m.group(2) != "active":
            esito.add(BLOCCANTE, f"Nodo {nome} — RAID {m.group(1)}", f"Stato {m.group(2)}.", F)
    for m in re.finditer(r"\[([U_]+)\]", md):
        if "_" in m.group(1):
            esito.add(BLOCCANTE, f"Nodo {nome} — RAID mdadm", f"{m.group(1).count('_')} disco/hi degradato/i [{m.group(1)}].", F)


# ────────────────────────────── controlli: performance ──────────────────────────────

def controlla_performance(nome: str, blocco: dict, esito: Esito):
    nodo = blocco.get("nodo") or {}
    rrd = nodo.get("rrd") or []
    A = f"Performance — nodo {nome} (ultima ora)"
    F = "rrddata"
    cpu = media(rrd, "cpu")
    if cpu is not None:
        esito.add(ATTENZIONE if cpu > 0.8 else INFO, A, f"CPU media {cpu*100:.0f}% (max {(massimo(rrd, 'cpu') or 0)*100:.0f}%).", F)
    iow = media(rrd, "iowait")
    if iow is not None and iow > 0.10:
        esito.add(ATTENZIONE, A, f"I/O wait medio {iow*100:.0f}%: lo storage è il collo di bottiglia.", F)
    for k, n in (("pressureiosome", "I/O"), ("pressurememorysome", "memoria"), ("pressurecpusome", "CPU")):
        v = media(rrd, k)
        if v is not None and v > 10:
            esito.add(ATTENZIONE, A, f"Pressione {n} (PSI some) media {v:.0f}%: processi in attesa di quella risorsa.", F)
    la, mc = media(rrd, "loadavg"), massimo(rrd, "maxcpu")
    if la is not None and mc and la / mc > 1:
        esito.add(ATTENZIONE, A, f"Load average medio {la:.1f} su {mc:.0f} CPU: coda di esecuzione satura.", F)
    for path, testo in (nodo.get("pveperf") or {}).items():
        p = parse_pveperf(testo)
        B = f"Performance — pveperf {nome}:{path}"
        f = p.get("fsync_s")
        if f is not None:
            giud = "scarso, sotto il minimo consigliato per VM e database" if f < 200 else ("accettabile" if f < 1000 else "buono")
            esito.add(ATTENZIONE if f < 200 else INFO, B, f"FSYNC/s {f:.0f} — {giud} (indicativo: <200 scarso · 200-1000 accettabile · >1000 buono).", "pveperf")
        if path == "/" and p.get("dns_int_ms") is not None and p["dns_int_ms"] > 500:
            esito.add(ATTENZIONE, f"Performance — nodo {nome}", f"DNS interno {p['dns_int_ms']:.0f} ms: rallenta login, GUI e ogni "
                      "risoluzione dal nodo — di norma un resolver sbagliato o irraggiungibile.", "pveperf")
        if path == "/" and p.get("dns_ext_ms") is not None and p["dns_ext_ms"] > 500:
            esito.add(INFO, f"Performance — nodo {nome}", f"DNS esterno {p['dns_ext_ms']:.0f} ms.", "pveperf")


# ────────────────────────────── controlli: VM ──────────────────────────────

def latenze_blockstat(status: dict) -> dict:
    out = {}
    for disco, b in ((status or {}).get("blockstat") or {}).items():
        if not isinstance(b, dict):
            continue
        wr, fl, rd = b.get("wr_operations") or 0, b.get("flush_operations") or 0, b.get("rd_operations") or 0
        out[disco] = {"wr_ms": (b.get("wr_total_time_ns", 0) / wr / 1e6) if wr else None,
                      "rd_ms": (b.get("rd_total_time_ns", 0) / rd / 1e6) if rd else None,
                      "flush_ms": (b.get("flush_total_time_ns", 0) / fl / 1e6) if fl else None,
                      "failed": b.get("failed_rd_operations", 0) + b.get("failed_wr_operations", 0) + b.get("failed_flush_operations", 0)}
    return out


def ambito_vm(vm: VM, inv: dict) -> str:
    multi = len(inv.get("nodi") or {}) > 1
    return f"VM {vm.vmid} ({vm.nome})" + (f" @{vm.nodo}" if multi else "")


def _qm_disco(vmid: str, bus: str, cfg: dict, **aggiunte) -> str:
    """Il comando che riscrive UNA riga di disco aggiungendoci un parametro.

    `qm set` sostituisce l'intero valore, quindi va ripetuto per intero quello
    che c'è già: mandare solo `--scsi0 iothread=1` cancellerebbe il disco dalla
    configurazione. Si riparte dal valore grezzo e ci si aggiunge in coda.
    """
    grezzo = str(cfg.get(bus) or "").strip()
    if not grezzo:
        return ""
    pezzi = [p for p in grezzo.split(",") if p and p.split("=")[0] not in aggiunte]
    pezzi += [f"{k}={v}" for k, v in aggiunte.items()]
    return f"qm set {vmid} --{bus} {','.join(pezzi)}"


def controlla_generali(vm: VM, inv: dict, esito: Esito):
    cfg, st = vm.config, vm.status or {}
    A = ambito_vm(vm, inv)
    G = "manuale"
    host_cpu = (((inv.get("nodi") or {}).get(vm.nodo) or {}).get("nodo") or {}).get("status", {}).get("cpuinfo") or {}
    cpu_tipo = (cfg.get("cpu") or "kvm64").split(",")[0]
    if cpu_tipo == "kvm64":
        esito.add(ATTENZIONE, A, "CPU type kvm64 (default): set di istruzioni minimo. Valutare almeno x86-64-v2-AES.", f"{G} §8.1 › Tipo di CPU",
                  comando=f"qm set {vm.vmid} --cpu x86-64-v2-AES")
    if cfg.get("sockets", "1") not in ("", "1") and cfg.get("numa", "0") != "1":
        esito.add(ATTENZIONE, A, f"{cfg['sockets']} socket senza NUMA: la regola è 1 socket, N core.", f"{G} §8.1 › Socket e core")
    if cfg.get("cpulimit") not in (None, "", "0"):
        esito.add(INFO, A, f"cpulimit={cfg['cpulimit']}: tetto assoluto di CPU.", f"{G} §8.1 › Priorità e limiti")
    if stato_ballooning(cfg) == "assente":
        esito.add(ATTENZIONE, A, "balloon: 0 — driver assente E reporting RAM perso (sempre 100% in GUI). "
                  "Per RAM fissa con statistiche: Minimum memory = Memory.", f"{G} §8.2 › Ballooning — il malinteso più diffuso",
                  comando=f"qm set {vm.vmid} --balloon {cfg.get('memory', '')}".rstrip())
    if cfg.get("hugepages") not in (None, ""):
        esito.add(INFO, A, f"hugepages={cfg['hugepages']}.", f"{G} §8.2 › Shares, hugepages, KSM")
    ha_scsi = False
    for d in dischi_dati(cfg):
        bus, cache, aio, ioth, disc = d["bus"], d.get("cache", ""), d.get("aio", ""), d.get("iothread", "0"), d.get("discard", "")
        ha_scsi |= bus.startswith("scsi")
        if bus.startswith(("sata", "ide")):
            esito.add(ATTENZIONE, A, f"Disco {bus}: bus SATA/IDE — solo per compatibilità o migrazione; il riferimento è SCSI + VirtIO SCSI single.", f"{G} §8.3 › Controller e bus")
        if cache in ("writeback", "unsafe"):
            esito.add(BLOCCANTE if cache == "unsafe" else ATTENZIONE, A, f"Disco {bus}: cache={cache} — un crash dell'host può corrompere i dati recenti senza UPS/BBU.", f"{G} §8.3 › Cache mode")
        if aio == "native" and (cache != "none" or ioth in ("0", "")):
            esito.add(BLOCCANTE, A, f"Disco {bus}: aio=native senza cache=none+iothread=1 — l'I/O può bloccarsi.", f"{G} §8.3 › AIO — la scelta che dipende dallo storage")
        # I due parametri che mancano più spesso si scrivono in UN comando solo.
        # `qm set` riscrive l'intera riga del disco: due comandi separati, lanciati
        # in sequenza, si cancellano a vicenda — il secondo riporterebbe la riga
        # senza quello che ha appena messo il primo.
        manca_disc = disc not in ("on", "1") and bus.startswith(("scsi", "virtio"))
        manca_ioth = bus.startswith("scsi") and ioth in ("0", "")
        aggiunte = {}
        if manca_disc:
            aggiunte["discard"] = "on"
        if manca_ioth:
            aggiunte["iothread"] = "1"
        insieme = _qm_disco(vm.vmid, bus, cfg, **aggiunte) if aggiunte else ""
        if manca_disc:
            esito.add(ATTENZIONE, A, f"Disco {bus}: discard non attivo — lo spazio liberato nel guest non torna allo storage thin.", f"{G} §8.3 › Altri parametri disco",
                      comando=insieme)
        if manca_ioth:
            esito.add(ATTENZIONE, A, f"Disco {bus}: iothread non attivo.", f"{G} §8.3 › IO thread",
                      comando=insieme)
    scsihw = cfg.get("scsihw", "")
    if ha_scsi and scsihw and scsihw != "virtio-scsi-single":
        esito.add(ATTENZIONE, A, f"scsihw={scsihw}: il riferimento è virtio-scsi-single (presupposto per gli IO thread).", f"{G} §8.3 › IO thread",
                  comando=f"qm set {vm.vmid} --scsihw virtio-scsi-single")
    vcpu = vcpu_di(cfg)
    for r in parse_reti(cfg):
        mq = r.get("queues")
        if mq and mq.isdigit() and int(mq) > vcpu:
            esito.add(ATTENZIONE, A, f"{r['iface']}: multiqueue={mq} > {vcpu} vCPU.", f"{G} §8.4 › Multiqueue")
        if r.get("modello") and not r["modello"].startswith("virtio"):
            esito.add(ATTENZIONE, A, f"{r['iface']}: modello '{r['modello']}', non VirtIO — solo per sistemi legacy.", f"{G} §8.4")
    agent = cfg.get("agent", "")
    if not agent or agent.startswith("0"):
        esito.add(ATTENZIONE, A, "QEMU Guest Agent non attivo: niente spegnimento pulito, freeze del filesystem nei backup, IP in GUI.", f"{G} §8.5",
                  comando=f"qm set {vm.vmid} --agent enabled=1  # poi installare il pacchetto NEL guest")
    elif vm.running and not st.get("agent"):
        esito.add(ATTENZIONE, A, "Agent abilitato ma NON risponde nel guest: i backup non fanno il freeze del filesystem.", f"{G} §8.5")
    ostype = cfg.get("ostype", "")
    if ostype.startswith("win") and host_cpu.get("vendor") == "GenuineIntel" and cpu_tipo == "host":
        rm = st.get("running-machine") or cfg.get("machine", "")
        if re.search(r"11\.0", rm or "") and "pve2" not in rm:
            esito.add(ATTENZIONE, A, f"Windows con CPU host su Intel e machine {rm}: blocchi intermittenti noti con VBS (Bugzilla #7825) — serve 11.0+pve2.", f"{G} §8.1 › Tipo di CPU")
    if ostype in ("win11", "win10") and cfg.get("bios", "seabios") != "ovmf":
        esito.add(INFO, A, "Windows recente con SeaBIOS: OVMF (UEFI) è il riferimento.", f"{G} §8.5")
    if cfg.get("protection", "0") != "1" and cfg.get("onboot", "0") == "1":
        esito.add(INFO, A, "protection non attiva su una VM ad avvio automatico.", f"{G} §8.6",
                  comando=f"qm set {vm.vmid} --protection 1")
    for s in vm.snapshot:
        eta = (time.time() - s.get("snaptime", time.time())) / 86400
        if eta > 30:
            esito.add(ATTENZIONE, A, f"Snapshot '{s.get('name')}' di {eta:.0f} giorni: non è un backup, "
                      "e degrada le prestazioni finché resta.", f"{G} §12.1")
    if vm.pending:
        esito.add(INFO, A, f"{len(vm.pending)} modifiche in attesa di riavvio: " + ", ".join(str(p.get('key')) for p in vm.pending[:6]) + ".", "manuale §8.6")
    if vm.running:
        cpu_avg = media(vm.rrd, "cpu")
        if cpu_avg is not None and cpu_avg > 0.8:
            esito.add(ATTENZIONE, A, f"CPU media nell'ultima ora {cpu_avg*100:.0f}% su {vcpu} vCPU: sottodimensionata o in loop.", f"{G} §8.1 › Overcommit di vCPU")
        for k, n in (("pressurecpusome", "CPU"), ("pressureiosome", "I/O"), ("pressurememorysome", "memoria")):
            v = media(vm.rrd, k)
            if v is not None and v > 20:
                esito.add(ATTENZIONE, A, f"Pressione {n} (PSI) media {v:.0f}%.", "rrddata")
        for disco, l in latenze_blockstat(st).items():
            if l["failed"]:
                esito.add(BLOCCANTE, A, f"Disco {disco}: {l['failed']} operazioni I/O fallite dall'avvio.", "blockstat")
            if l["flush_ms"] is not None and l["flush_ms"] > 20:
                esito.add(ATTENZIONE, A, f"Disco {disco}: latenza media dei flush {l['flush_ms']:.1f} ms (indicativo: <5 ms su SSD).", "blockstat")
        ag = vm.agent or {}
        for fs in (ag.get("fsinfo") if isinstance(ag.get("fsinfo"), list) else []):
            if not isinstance(fs, dict):
                continue
            tot, used = fs.get("total-bytes"), fs.get("used-bytes")
            p = pct(used, tot)
            if p is not None and tot and tot > 1024**3 and p >= 90:
                esito.add(ATTENZIONE, A, f"Filesystem {fs.get('mountpoint')} nel guest al {p:.0f}% ({gb(used)}/{gb(tot)}).", "guest agent")
        if isinstance(ag.get("ora"), (int, float)) and ag.get("ora_host"):
            drift = ag["ora"] / 1e9 - ag["ora_host"]
            resto = abs(drift) % 3600
            if abs(drift) > 30 and min(resto, 3600 - resto) < 60 and abs(drift) >= 3600:
                esito.add(INFO, A, f"Orologio del guest a {drift/3600:+.0f} h esatte dall'host: quasi certamente l'agent riporta l'ora locale, non un orologio sbagliato.", "manuale §2.5")
            elif abs(drift) > 30:
                esito.add(ATTENZIONE, A, f"Orologio del guest sfasato di {drift:+.0f} s rispetto all'host.", "manuale §2.5")
        oi = ag.get("osinfo") if isinstance(ag.get("osinfo"), dict) else {}
        if oi.get("id") and ostype:
            if ostype.startswith("win") != ("windows" in (oi.get("id", "") + oi.get("name", "")).lower()):
                esito.add(INFO, A, f"ostype '{ostype}' ma il guest è {oi.get('pretty-name') or oi.get('name')}.", f"{G} §8.5")
        for it in (ag.get("interfacce") if isinstance(ag.get("interfacce"), list) else []):
            stt = it.get("statistics") or {} if isinstance(it, dict) else {}
            if stt.get("rx-errs") or stt.get("tx-errs"):
                esito.add(INFO, A, f"{it.get('name')} nel guest: errori rx/tx {stt.get('rx-errs', 0)}/{stt.get('tx-errs', 0)}.", "guest agent")
    elif cfg.get("onboot") == "1":
        esito.add(INFO, A, "Spenta ma con onboot=1: ripartirà al prossimo riavvio del nodo.", f"{G} §8.6")
    nbu = {str(x.get("vmid")) for x in (inv.get("cluster", {}).get("not_backed_up") or [])}
    if vm.vmid in nbu:
        esito.add(ATTENZIONE, A, "Non inclusa in nessun job di backup.", "manuale §12.1")


def controlla_profilo(vm: VM, pid: str, inv: dict, esito: Esito):
    if pid == NON_CLASSIFICATA or pid not in PROFILI:
        return
    p, cfg = PROFILI[pid], vm.config
    A = ambito_vm(vm, inv) + f" — profilo {p['nome']}"
    F = p["fonte"]
    vcpu = vcpu_di(cfg)
    if p.get("vcpu_max") and vcpu > p["vcpu_max"] * 2:
        esito.add(ATTENZIONE, A, f"{vcpu} vCPU: molto oltre l'indicazione tipica (~{p['vcpu_max']}).", F)
    cpu_tipo = (cfg.get("cpu") or "kvm64").split(",")[0]
    if cpu_tipo in p.get("cpu_type_evita", set()):
        esito.add(BLOCCANTE, A, f"CPU type '{cpu_tipo}' sconsigliata per questo profilo.", F,
                  comando=f"qm set {vm.vmid} --cpu {p.get('cpu_type_richiede') or 'x86-64-v3'}")
    if p.get("cpu_type_richiede") and cpu_tipo != p["cpu_type_richiede"]:
        esito.add(ATTENZIONE, A, f"Raccomandato CPU type '{p['cpu_type_richiede']}'; rilevato '{cpu_tipo}'.", F,
                  comando=f"qm set {vm.vmid} --cpu {p['cpu_type_richiede']}")
    sb = stato_ballooning(cfg)
    if p.get("balloon") == "disabilitato" and sb != "assente":
        esito.add(BLOCCANTE, A, "Ballooning da disattivare (balloon: 0) per questo carico.", F,
                  comando=f"qm set {vm.vmid} --balloon 0")
    if p.get("balloon") == "min_eq_max" and sb != "presente_fermo":
        esito.add(ATTENZIONE, A, "RAM fissa (Minimum memory = Memory) mantenendo le statistiche.", F)
    if p.get("numa") is True and cfg.get("numa", "0") != "1":
        esito.add(ATTENZIONE, A, "NUMA non attivo: raccomandato per questo profilo.", F,
                  comando=f"qm set {vm.vmid} --numa 1")
    if p.get("protection") and cfg.get("protection", "0") != "1":
        esito.add(ATTENZIONE, A, "protection non attiva: raccomandata per la criticità del servizio.", F,
                  comando=f"qm set {vm.vmid} --protection 1")
    if p.get("onboot_no") and cfg.get("onboot") == "1":
        esito.add(ATTENZIONE, A, "onboot attivo su una VM di test.", F,
                  comando=f"qm set {vm.vmid} --onboot 0")
    if p.get("dischi_min") and len(dischi_dati(cfg)) < p["dischi_min"]:
        esito.add(ATTENZIONE, A, "Un solo disco: il profilo prevede sistema e dati separati.", F)
    if p.get("cache") == "none":
        for d in dischi_dati(cfg):
            if d.get("cache", "none") not in ("none", ""):
                esito.add(BLOCCANTE, A, f"Disco {d['bus']}: cache={d['cache']}, il profilo richiede none.", F)
    if p.get("legacy"):
        for d in dischi_dati(cfg):
            if d["bus"].startswith(("sata", "ide")):
                esito.add(INFO, A, f"Disco {d['bus']} su bus SATA/IDE: tollerato per questa tipologia, ma è la prima cosa da cambiare se la VM diventa di produzione.", F)
    if p.get("multiqueue") == "tutte_le_nic":
        reti = parse_reti(cfg)
        senza = [r["iface"] for r in reti if not (r.get("queues") or "").isdigit()]
        diverse = [r["iface"] for r in reti if (r.get("queues") or "").isdigit() and int(r["queues"]) != vcpu]
        if senza:
            esito.add(ATTENZIONE, A, f"Multiqueue non impostato su {', '.join(senza)}: va su TUTTE le interfacce (misurato: 3,3 vs 34,4 Gb/s).", F)
        if diverse:
            esito.add(ATTENZIONE, A, f"Multiqueue diverso dalle {vcpu} vCPU su {', '.join(diverse)}.", F)
    if p.get("ha") and in_cluster(inv) and not ((vm.status or {}).get("ha") or {}).get("managed"):
        esito.add(INFO, A, "Non gestita in HA: raccomandata per questo profilo (con anti-affinità fra repliche applicative).", F)
    for e in p.get("extra", []):
        esito.add(INFO, A, e, F)


def controlla_lxc(ctid: str, d: dict, inv: dict, esito: Esito):
    cfg = normalizza_config(d.get("config"))
    A = f"CT {ctid} ({cfg.get('hostname', '')})" + (f" @{d.get('nodo')}" if len(inv.get('nodi') or {}) > 1 else "")
    if cfg.get("unprivileged", "0") != "1":
        esito.add(ATTENZIONE, A, "Container privilegiato: un'evasione compromette l'host. Preferire unprivileged.", "manuale §10.2")
    if cfg.get("protection", "0") != "1" and cfg.get("onboot") == "1":
        esito.add(INFO, A, "protection non attiva su un container ad avvio automatico.", "manuale §10.2")


# ────────────────────────────── tabella VM e assegnazione ──────────────────────────────

def _acc(t: str, n: int) -> str:
    return t if len(t) <= n else t[:n - 1] + "…"


def so_di(vm: VM) -> str:
    oi = (vm.agent or {}).get("osinfo")
    return (oi.get("pretty-name") if isinstance(oi, dict) else None) or vm.config.get("ostype", "—")


def stampa_tabella_vm(vms: list, asseg: dict, multi: bool):
    inte = ("VMID", "Nodo", "Nome", "Stato", "vCPU", "RAM", "Disco", "Rete", "SO (agent)", "Profilo") if multi else \
           ("VMID", "Nome", "Stato", "vCPU", "RAM", "Disco", "Rete", "SO (agent)", "Profilo")
    righe = []
    for v in vms:
        pid = asseg.get(v.vmid, NON_CLASSIFICATA)
        if pid == NON_CLASSIFICATA:
            sug = suggerisci_profilo(v)
            prof = f"{PROFILI[sug]['nome']} *" if sug != NON_CLASSIFICATA else "—"
        else:
            prof = PROFILI.get(pid, {}).get("nome", f"id '{pid}'?")
        stato = (v.status or {}).get("status") or v.lista.get("status") or "?"
        r = [v.vmid, _acc(v.nome, 22), stato, vcpu_di(v.config), f"{ram_gb_di(v.config):.0f}", f"{disco_gb_di(v.config):.0f}",
             _acc(",".join(x.get("bridge", "?") for x in parse_reti(v.config)) or "—", 14), _acc(so_di(v), 22), prof]
        if multi:
            r.insert(1, _acc(v.nodo, 12))
        righe.append(tuple(r))
    larg = [max(len(str(inte[i])), *(len(str(r[i])) for r in righe)) if righe else len(inte[i]) for i in range(len(inte))]
    sep = "  "
    riga = lambda vals: sep.join(str(v).ljust(larg[i]) for i, v in enumerate(vals))
    print("\n" + c(riga(inte), GRASSETTO))
    print(sep.join("-" * l for l in larg))
    for r in righe:
        s = riga(r)
        print(s if "running" in r else c(s, GRIGIO))


def chiedi_profilo_per(vm: VM) -> str:
    sug = suggerisci_profilo(vm)
    print(f"\n── VM {vm.vmid} — {vm.nome} ({so_di(vm)}) " + "─" * max(1, 30 - len(vm.nome)))
    for k, v in PROFILI.items():
        print(f"  {k}) {v['nome']}" + ("   ← proposta" if k == sug else ""))
    print(f"  {NON_CLASSIFICATA}) Non classificare")
    while True:
        s = input(f"  Tipologia [{sug}]: ").strip() or sug
        if s in PROFILI or s == NON_CLASSIFICATA:
            return s
        print("  Valore non valido.")


def assegna_profili_da_tabella(vms: list, noti: dict, multi: bool) -> dict:
    asseg = dict(noti)
    ids = {v.vmid for v in vms}
    while True:
        stampa_tabella_vm(vms, asseg, multi)
        proposte = sum(1 for v in vms if asseg.get(v.vmid, NON_CLASSIFICATA) == NON_CLASSIFICATA and suggerisci_profilo(v) != NON_CLASSIFICATA)
        print("\nVMID da cambiare · 't' chiedi una per una quelle senza profilo · INVIO accetta le proposte (*)"
              + (f" — {proposte} proposte dal nome/SO" if proposte else ""))
        s = input("Scelta: ").strip()
        if s == "":
            for v in vms:
                if asseg.get(v.vmid, NON_CLASSIFICATA) == NON_CLASSIFICATA:
                    asseg[v.vmid] = suggerisci_profilo(v)
            return asseg
        if s == "t":
            for v in vms:
                if asseg.get(v.vmid, NON_CLASSIFICATA) == NON_CLASSIFICATA:
                    asseg[v.vmid] = chiedi_profilo_per(v)
            continue
        if s in ids:
            asseg[s] = chiedi_profilo_per(next(v for v in vms if v.vmid == s))
        else:
            print(f"  '{s}' non è un VMID di questa scansione.")


# ══════════════════════════════ coerenza del cluster ══════════════════════════════
# Le regole di questa parte non guardano un nodo: guardano la DIFFERENZA fra i
# nodi. Sono deterministiche nel senso stretto — ognuna nomina il campo che la
# decide — e nascono dal catalogo in docs/superpowers/specs/2026-09-10.
#
# Regola di stile: ogni rilievo mostra i VALORI a confronto, non solo
# l'anomalia. «PX-01 172.18.20.1 · PX-03 172.20.20.3» dice da sé qual è quello
# fuori posto; «vlan20 incoerente» costringe chi legge ad andare a cercare.

RE_MTU = re.compile(r"^\d+:\s+([^:@]+)[:@].*?\bmtu\s+(\d+)", re.M)


def mtu_di(blocco: dict) -> dict:
    """Il MTU davvero applicato, da `ip link`. Non quello dichiarato: un MTU
    scritto in /etc/network/interfaces e mai applicato non protegge nessuno."""
    fuori = {}
    for nome, valore in RE_MTU.findall((blocco.get("nodo") or {}).get("ip_link") or ""):
        nome = nome.strip()
        if not nome.startswith(("lo", "tap", "fwbr", "fwln", "fwpr", "veth")):
            fuori[nome] = int(valore)
    return fuori


def reti_di(blocco: dict) -> dict:
    """Le interfacce dichiarate, indicizzate per nome."""
    return {i.get("iface"): i for i in ((blocco.get("nodo") or {}).get("network") or []) if i.get("iface")}


def _sotto(rete: dict, iface: str, visti=None) -> set:
    """I dispositivi su cui poggia un'interfaccia, fino ai fisici.

    vlan30 → vmbr_int → bond50 → enp129s0f0np0, enp129s0f1np1. Serve a
    rispondere alla sola domanda che conta: due reti diverse passano sullo
    stesso rame?
    """
    visti = visti if visti is not None else set()
    if not iface or iface in visti:
        return set()
    visti.add(iface)
    voce = rete.get(iface) or {}
    giu = set()
    for chiave in ("vlan-raw-device", "bridge_ports", "slaves"):
        for pezzo in str(voce.get(chiave) or "").split():
            giu.add(pezzo)
            giu |= _sotto(rete, pezzo, visti)
    return giu


def iface_con_ip(rete: dict, ip: str) -> str:
    """Quale interfaccia porta questo indirizzo. Confronto sulla rete /24 e non
    sull'indirizzo esatto: l'IP di corosync di un nodo sta su quel nodo, ma
    stiamo cercando la rete, non l'host."""
    if not ip:
        return ""
    prefisso = ip.rsplit(".", 1)[0] + "."
    for nome, voce in rete.items():
        cidr = str(voce.get("cidr") or "")
        if cidr.startswith(prefisso):
            return nome
    return ""


def _valori_per_nodo(inv: dict, estrai) -> dict:
    """{valore: [nodi]} — la forma in cui una divergenza si racconta da sé."""
    per = {}
    for nome, blocco in (inv.get("nodi") or {}).items():
        try:
            v = estrai(nome, blocco)
        except Exception:  # noqa: BLE001 — un nodo che non risponde non è un difetto del cliente
            v = None
        if v is not None:
            per.setdefault(str(v), []).append(nome)
    return per


def _elenca(per: dict, limite: int = 4) -> str:
    voci = [f"{', '.join(sorted(n))}: {v}" for v, n in sorted(per.items(), key=lambda x: -len(x[1]))]
    return " · ".join(voci[:limite]) + (" · …" if len(voci) > limite else "")


def controlla_coerenza_rete(inv: dict, esito: Esito):
    """RETE-01/02/03, MTU-01, BOND-01/04 — quello che diverge fra i nodi."""
    nodi = inv.get("nodi") or {}
    if len(nodi) < 2:
        return
    A = "Coerenza — rete"
    reti = {n: reti_di(b) for n, b in nodi.items()}
    mtus = {n: mtu_di(b) for n, b in nodi.items()}

    # RETE-01/02: stessa VLAN, reti IP diverse. È il difetto che rompe la
    # migrazione senza avvisare: la VM parte, e si trova su un'altra rete.
    per_vlan = {}
    for nodo, rete in reti.items():
        for nome, voce in rete.items():
            vid = voce.get("vlan-id")
            cidr = voce.get("cidr")
            if vid and cidr:
                per_vlan.setdefault(str(vid), {}).setdefault(_rete_di(cidr), []).append(f"{nodo} {cidr}")
    for vid, reti_viste in sorted(per_vlan.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 0):
        if len(reti_viste) > 1:
            dettaglio = " · ".join(f"{', '.join(v)}" for v in reti_viste.values())
            esito.add(BLOCCANTE, A, f"VLAN {vid}: reti IP diverse fra i nodi — {dettaglio}.", "manuale §1.3, §2.6")

    # RETE-03: un bridge o un bond che esiste solo su alcuni nodi.
    for genere, etichetta in (("bridge", "bridge"), ("bond", "bond")):
        presenza = {}
        for nodo, rete in reti.items():
            for nome, voce in rete.items():
                if voce.get("type") == genere:
                    presenza.setdefault(nome, []).append(nodo)
        for nome, dove in sorted(presenza.items()):
            mancano = sorted(set(nodi) - set(dove))
            if mancano:
                esito.add(ATTENZIONE, A, f"{etichetta} {nome} esiste su {', '.join(sorted(dove))} ma non su {', '.join(mancano)}.",
                          "manuale §2.6")

    # MTU-01: stessa interfaccia, MTU diverso. Un MTU asimmetrico non si
    # manifesta subito: passa il ping e si perdono i pacchetti grandi.
    nomi = {i for m in mtus.values() for i in m}
    for iface in sorted(nomi):
        per = {}
        for nodo, m in mtus.items():
            if iface in m:
                per.setdefault(str(m[iface]), []).append(nodo)
        if len(per) > 1:
            esito.add(BLOCCANTE, A, f"{iface}: MTU diverso fra i nodi — {_elenca(per)}.", "manuale §1.3")

    # BOND-01/04: modo e cadenza LACP dello stesso bond.
    for chiave, etichetta, liv in (("bond_mode", "modo", BLOCCANTE), ("lacp_rate", "lacp_rate", ATTENZIONE)):
        per_bond = {}
        for nodo, rete in reti.items():
            for nome, voce in rete.items():
                if voce.get("type") == "bond" and voce.get(chiave):
                    per_bond.setdefault(nome, {}).setdefault(str(voce[chiave]), []).append(nodo)
        for nome, per in sorted(per_bond.items()):
            if len(per) > 1:
                esito.add(liv, A, f"bond {nome}: {etichetta} diverso fra i nodi — {_elenca(per)}.", "manuale §1.3")


def _rete_di(cidr: str) -> str:
    """«172.18.20.3/24» → «172.18.20.0/24». Confronto per rete, non per host."""
    try:
        indirizzo, bit = cidr.split("/")
        parti = [int(x) for x in indirizzo.split(".")]
        maschera = (0xFFFFFFFF << (32 - int(bit))) & 0xFFFFFFFF
        num = (parti[0] << 24) | (parti[1] << 16) | (parti[2] << 8) | parti[3]
        num &= maschera
        return f"{(num >> 24) & 255}.{(num >> 16) & 255}.{(num >> 8) & 255}.{num & 255}/{bit}"
    except (ValueError, IndexError):
        return cidr


def controlla_rete_nodo(nome: str, blocco: dict, inv: dict, esito: Esito):
    """RETE-06/07/08/09, MTU-02/03, BOND-02/03 — quello che si vede su un nodo solo."""
    multi = len(inv.get("nodi") or {}) > 1
    A = f"Rete — nodo {nome}" if multi else "Rete del nodo"
    rete = reti_di(blocco)
    mtu = mtu_di(blocco)

    for iface, voce in sorted(rete.items()):
        tipo = voce.get("type")
        # RETE-07/08: dichiarata e non attiva, o attiva e non dichiarata all'avvio.
        if not voce.get("active") and voce.get("autostart"):
            esito.add(ATTENZIONE, A, f"{iface}: dichiarata all'avvio ma non attiva.", "manuale §2.6")
        if voce.get("active") and not voce.get("autostart") and tipo in ("bridge", "bond", "vlan"):
            esito.add(ATTENZIONE, A, f"{iface}: attiva ma senza autostart — sparisce al primo riavvio.", "manuale §2.6")
        # MTU-03: la catena deve avere un MTU non decrescente verso il basso.
        mio = mtu.get(iface)
        if mio:
            for sotto in _sotto(rete, iface):
                giu = mtu.get(sotto)
                if giu and giu < mio:
                    esito.add(BLOCCANTE, A, f"{iface} ha MTU {mio} ma poggia su {sotto} che è a {giu}: i pacchetti grandi si perdono.",
                              "manuale §2.6")
        # BOND-02: un'aggregazione con un solo membro non aggrega niente.
        if tipo == "bond":
            membri = str(voce.get("slaves") or "").split()
            if len(membri) < 2:
                esito.add(BLOCCANTE, A, f"bond {iface}: {len(membri)} membro/i — un'aggregazione con un solo cavo non è ridondante.",
                          "manuale §1.3")
        # RETE-09: bridge non vlan-aware usato con tag (PVE crea bridge dinamici).
        if tipo == "bridge" and not voce.get("bridge_vlan_aware"):
            if any(f"{iface}v" in x for x in rete):
                esito.add(ATTENZIONE, A, f"{iface} non è vlan-aware ma viene usato con dei tag: PVE crea bridge dinamici per ogni VLAN.",
                          "manuale §2.6, §14.7")
        # RETE-06: lo stesso bridge porta la gestione e il traffico ospite.
        if tipo == "bridge" and voce.get("cidr") and _guest_sul_bridge(inv, iface):
            esito.add(INFO, A, f"{iface} porta un indirizzo dell'host e il traffico dei guest: gestione e ospiti sullo stesso segmento.",
                      "manuale §1.3")


def _guest_sul_bridge(inv: dict, bridge: str) -> int:
    n = 0
    for blocco in (inv.get("nodi") or {}).values():
        for v in (blocco.get("vms") or {}).values():
            cfg = normalizza_config(v.get("config"))
            for k, val in cfg.items():
                if k.startswith("net") and f"bridge={bridge}" in str(val):
                    n += 1
    return n


def reti_di_servizio(inv: dict) -> dict:
    """Le reti che il cluster usa per sé: corosync e storage.

    Non sono «una VLAN come le altre»: un guest attestato lì, o una migrazione
    che ci passa sopra, competono con il battito del cluster.
    """
    fuori = {}
    ing = nodo_ingresso(inv)
    for ip in re.findall(r"ring\d+_addr:\s*(\d+\.\d+\.\d+\.\d+)", ing.get("corosync_conf") or ""):
        fuori.setdefault(ip.rsplit(".", 1)[0], "corosync")
    ceph = (inv.get("cluster") or {}).get("ceph") or {}
    for m in ((ceph.get("monmap") or {}).get("mons") or []):
        ind = str(m.get("public_addr") or "").split(":")[0]
        if ind.count(".") == 3:
            fuori.setdefault(ind.rsplit(".", 1)[0], "storage Ceph")
    return fuori


def controlla_reti_di_servizio(inv: dict, esito: Esito):
    """BOND-05 e RETE-05: chi altro passa sul rame del cluster."""
    servizio = reti_di_servizio(inv)
    if not servizio:
        return
    A = "Cluster — reti di servizio"
    ing_nome = inv.get("ingresso")
    blocco = (inv.get("nodi") or {}).get(ing_nome) or {}
    rete = reti_di(blocco)

    # BOND-05: corosync e storage sullo stesso dispositivo fisico.
    dove = {}
    for prefisso, a_che_serve in servizio.items():
        iface = iface_con_ip(rete, prefisso + ".1") or iface_con_ip(rete, prefisso + ".0")
        if iface:
            dove[a_che_serve] = (iface, _sotto(rete, iface) | {iface})
    if len(dove) > 1:
        nomi = list(dove)
        for i in range(len(nomi)):
            for j in range(i + 1, len(nomi)):
                a, b = dove[nomi[i]], dove[nomi[j]]
                comuni = {x for x in (a[1] & b[1]) if x.startswith(("bond", "en", "eth"))}
                if comuni:
                    esito.add(BLOCCANTE, A,
                              f"{nomi[i]} ({a[0]}) e {nomi[j]} ({b[0]}) passano sullo stesso rame: {', '.join(sorted(comuni))}. "
                              f"Il traffico dello storage compete con il battito del cluster.",
                              "manuale §1.3, §3.3")

    # RETE-05: un guest attestato su una VLAN di servizio.
    vlan_servizio = {}
    for prefisso, a_che_serve in servizio.items():
        iface = iface_con_ip(rete, prefisso + ".1")
        vid = (rete.get(iface) or {}).get("vlan-id")
        if vid:
            vlan_servizio[str(vid)] = a_che_serve
    if vlan_servizio:
        multi = len(inv.get("nodi") or {}) > 1
        for nome, b in (inv.get("nodi") or {}).items():
            for vmid, v in sorted((b.get("vms") or {}).items(), key=lambda x: int(x[0]) if x[0].isdigit() else 0):
                cfg = normalizza_config(v.get("config"))
                for k, val in cfg.items():
                    if not k.startswith("net"):
                        continue
                    m = re.search(r"tag=(\d+)", str(val))
                    if m and m.group(1) in vlan_servizio:
                        AV = f"VM {vmid} ({cfg.get('name', '')})" + (f" @{nome}" if multi else "")
                        esito.add(ATTENZIONE, AV,
                                  f"{k} sul tag VLAN {m.group(1)}, che è la rete di {vlan_servizio[m.group(1)]} del cluster.",
                                  "manuale §1.3, §3.3")


def controlla_migrazione(inv: dict, esito: Esito):
    """MIGR-01/02/03/04/05 — dove passa una migrazione a caldo.

    È la famiglia con il rilievo più grave del catalogo, e la meno visibile: non
    c'è niente di rotto da guardare finché non si migra una macchina grossa e il
    cluster perde il quorum.
    """
    nodi = inv.get("nodi") or {}
    if len(nodi) < 2:
        return
    A = "Cluster — migrazione"
    opzioni = (inv.get("cluster") or {}).get("options") or {}
    dichiarata = str(opzioni.get("migration") or "")
    servizio = reti_di_servizio(inv)

    if not dichiarata:
        quale = ", ".join(sorted(p + ".0" for p, s in servizio.items() if s == "corosync")) or "quella del cluster"
        esito.add(BLOCCANTE, A,
                  f"Nessuna rete di migrazione dichiarata: la migrazione a caldo usa la rete del cluster ({quale}), "
                  f"cioè la stessa di corosync.", "manuale §1.3, §3.3")
        return

    m = re.search(r"network=([0-9./]+)", dichiarata)
    if m:
        prefisso = m.group(1).split("/")[0].rsplit(".", 1)[0]
        a_che_serve = servizio.get(prefisso)
        if a_che_serve:
            liv = BLOCCANTE if a_che_serve == "corosync" else ATTENZIONE
            esito.add(liv, A, f"La rete di migrazione ({m.group(1)}) è la stessa di {a_che_serve}.", "manuale §1.3, §3.3")
    if "type=insecure" in dichiarata and not m:
        esito.add(BLOCCANTE, A, "Migrazione «insecure» senza una rete dedicata dichiarata: i dati viaggiano in chiaro sulla rete comune.",
                  "manuale §1.3")

    # MIGR-05: la replica ZFS non ha una rete sua e segue quella del cluster.
    if ((inv.get("cluster") or {}).get("replication") or []) and not m:
        esito.add(ATTENZIONE, A, "Ci sono job di replica ma nessuna rete dedicata: la replica passa sulla rete del cluster.",
                  "manuale §4.5")


def controlla_coerenza_host(inv: dict, esito: Esito):
    """COER-01..07 — gli host devono somigliarsi. Dove non si somigliano, la
    manutenzione di uno non vale per gli altri."""
    nodi = inv.get("nodi") or {}
    if len(nodi) < 2:
        return
    A = "Coerenza — host"

    per = _valori_per_nodo(inv, lambda n, b: ((b.get("nodo") or {}).get("status") or {}).get("pveversion"))
    if len(per) > 1:
        esito.add(BLOCCANTE, A, f"Versione di Proxmox diversa fra i nodi — {_elenca(per)}.", "manuale §19.3")

    per = _valori_per_nodo(inv, lambda n, b: ((b.get("nodo") or {}).get("status") or {}).get("current-kernel", {}).get("release"))
    if len(per) > 1:
        esito.add(ATTENZIONE, A, f"Kernel in esecuzione diverso fra i nodi — {_elenca(per)}. "
                                 f"Un nodo non è stato riavviato dopo l'aggiornamento.", "manuale §19.3")

    conti = _valori_per_nodo(inv, lambda n, b: len((b.get("nodo") or {}).get("apt_update") or []))
    numeri = [int(x) for x in conti]
    if numeri and max(numeri) - min(numeri) > 20:
        esito.add(ATTENZIONE, A, f"Aggiornamenti pendenti molto diversi fra i nodi — {_elenca(conti)}.", "manuale §19.1")

    def repo_attivi(_n, b):
        righe = []
        for f in ((b.get("nodo") or {}).get("apt_repos") or {}).get("files") or []:
            for r in f.get("repositories") or []:
                if r.get("Enabled") is not False:
                    righe += [f"{u} {' '.join(r.get('Suites') or [])}" for u in (r.get("URIs") or [])]
        return " | ".join(sorted(set(righe))) or None
    per = _valori_per_nodo(inv, repo_attivi)
    if len(per) > 1:
        esito.add(BLOCCANTE, A, f"Repository configurati diversi fra i nodi: {len(per)} combinazioni. "
                                f"Gli aggiornamenti non porteranno gli stessi pacchetti.", "manuale §1.6")

    per = _valori_per_nodo(inv, lambda n, b: ((b.get("nodo") or {}).get("subscription") or {}).get("level") or "nessuna")
    if len(per) > 1:
        esito.add(ATTENZIONE, A, f"Livello di subscription diverso fra i nodi — {_elenca(per)}.", "manuale §1.6")

    def fuso(_n, b):
        m = re.search(r"Time zone:\s*(\S+)", (b.get("nodo") or {}).get("timedatectl") or "")
        return m.group(1) if m else None
    per = _valori_per_nodo(inv, fuso)
    if len(per) > 1:
        esito.add(BLOCCANTE, A, f"Fuso orario diverso fra i nodi — {_elenca(per)}. I log non si confrontano più.",
                  "manuale §2.5")

    def risorse(_n, b):
        st = (b.get("nodo") or {}).get("status") or {}
        return f"{(st.get('cpuinfo') or {}).get('cpus')} vCPU, {round(((st.get('memory') or {}).get('total') or 0) / 1e9)} GB"
    per = _valori_per_nodo(inv, risorse)
    if len(per) > 1:
        esito.add(INFO, A, f"Nodi di taglia diversa — {_elenca(per)}. In HA il più piccolo deve reggere il carico degli altri.",
                  "manuale §1.5, §7.2")

    # STOR-01/02/04: gli storage devono essere gli stessi ovunque.
    per_storage = {}
    for nome, b in nodi.items():
        for s in ((b.get("nodo") or {}).get("storage") or []):
            per_storage.setdefault(s.get("storage"), {})[nome] = s
    for nome, dove in sorted(per_storage.items()):
        mancano = sorted(set(nodi) - set(dove))
        if mancano:
            liv = BLOCCANTE if any(s.get("shared") for s in dove.values()) else ATTENZIONE
            esito.add(liv, "Coerenza — storage", f"Lo storage «{nome}» non c'è su {', '.join(mancano)} "
                                                 f"(c'è su {', '.join(sorted(dove))}).", "manuale §4.1")
        contenuti = {n: ",".join(sorted((s.get("content") or "").split(","))) for n, s in dove.items()}
        distinti = {}
        for n, c in contenuti.items():
            distinti.setdefault(c, []).append(n)
        if len(distinti) > 1:
            esito.add(ATTENZIONE, "Coerenza — storage", f"Lo storage «{nome}» dichiara contenuti diversi fra i nodi — {_elenca(distinti)}.",
                      "manuale §4.1")


RE_ORA_UTC = re.compile(r"Universal time:\s+\w+\s+(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2}):(\d{2})")


def istante_raccolta(inv: dict):
    """Quando è stata fatta la raccolta, in secondi. **Non** «adesso».

    Una raccolta si rianalizza mesi dopo — è il motivo per cui si conserva il
    grezzo. Ogni regola che misura un'età deve partire da qui, o rianalizzando
    un JSON di tre ore prima dichiara in ritardo sette job che erano in orario
    (visto il 2026-09-10, sette falsi positivi in un colpo).

    Torna None se non si sa: una regola che misura il tempo senza sapere che ora
    era non deve scattare affatto.
    """
    if inv.get("raccolto_il"):
        return float(inv["raccolto_il"])
    m = RE_ORA_UTC.search(nodo_ingresso(inv).get("timedatectl") or "")
    if not m:
        return None
    import calendar
    return calendar.timegm((int(m.group(1)), int(m.group(2)), int(m.group(3)),
                            int(m.group(4)), int(m.group(5)), int(m.group(6)), 0, 0, 0))


def cadenza_replica(job: dict) -> int:
    """Ogni quanto dovrebbe girare, in secondi. Senza schedule vale il valore
    predefinito di PVE, che è un quarto d'ora."""
    s = str(job.get("schedule") or "").strip()
    m = re.fullmatch(r"\*/(\d+)", s)
    if m:
        return max(60, int(m.group(1)) * 60)
    if re.fullmatch(r"\*/\d+:\d+", s) or re.fullmatch(r"\d+:\d+", s):
        m2 = re.search(r"(\d+):", s)
        return max(3600, int(m2.group(1)) * 3600) if m2 else 3600
    return 15 * 60


def controlla_replica_ha(inv: dict, esito: Esito):
    """HA-01..09 — la replica c'è, l'HA la accende, e i due devono guardarsi."""
    cl = inv.get("cluster") or {}
    repliche = cl.get("replication") or []
    risorse = cl.get("ha_resources") or []
    regole = cl.get("ha_rules") or []
    stato = cl.get("ha_status") or []
    A = "Cluster — replica e HA"
    if not repliche and not risorse:
        return

    in_ha = {str(r.get("sid", "")).split(":")[-1] for r in risorse}
    replicati = {str(r.get("guest")) for r in repliche}
    soli = sorted(replicati - in_ha, key=lambda x: int(x) if x.isdigit() else 0)
    if soli and risorse:
        esito.add(ATTENZIONE, A, f"{len(soli)} guest replicati ma non in HA ({', '.join(soli)}): la copia c'è, "
                                 f"ma in caso di guasto non la accende nessuno.", "manuale §4.5, §7.1")

    if risorse and not regole:
        esito.add(ATTENZIONE, A, "Nessuna regola HA definita: una risorsa può ripartire su un nodo dove la sua replica non arriva.",
                  "manuale §7.5")

    senza = [r.get("id") for r in repliche if not r.get("schedule")]
    if senza:
        esito.add(INFO, A, f"{len(senza)} job di replica senza schedule esplicito: vale il valore predefinito (*/15).",
                  "manuale §4.5")

    quando = istante_raccolta(inv)
    cadenze = {str(r.get("id")): cadenza_replica(r) for r in repliche}
    for nome, b in (inv.get("nodi") or {}).items():
        for r in ((b.get("nodo") or {}).get("replication") or []):
            if (r.get("fail_count") or 0) > 0 or r.get("error"):
                motivo = r.get("error") or f"{r.get('fail_count')} tentativi falliti"
                esito.add(BLOCCANTE, A, f"Job di replica {r.get('id')} in errore su {nome}: {motivo}.",
                          "manuale §4.5")
            ultimo = r.get("last_sync") or 0
            atteso = cadenze.get(str(r.get("id")), 15 * 60)
            # Senza l'istante della raccolta la regola tace: meglio muta che bugiarda.
            if quando and ultimo and quando - ultimo > max(3 * atteso, 3600):
                ore = (quando - ultimo) / 3600
                esito.add(BLOCCANTE, A, f"Job di replica {r.get('id')}: al momento della verifica l'ultima "
                                        f"sincronizzazione risaliva a {ore:.0f} ore prima, contro una cadenza "
                                        f"di {atteso // 60} minuti.", "manuale §4.5")

    if risorse and len(inv.get("nodi") or {}) < 3:
        qd = "qdevice" in (nodo_ingresso(inv).get("corosync_conf") or "").lower()
        if not qd:
            esito.add(BLOCCANTE, A, "HA attivo con meno di tre nodi e senza QDevice: al primo guasto non c'è quorum "
                                    "e l'HA non riparte niente.", "manuale §7.2, §3.5")

    for s in stato:
        if s.get("type") == "lrm" and not any(x in str(s.get("status", "")) for x in ("idle", "active")):
            esito.add(ATTENZIONE, A, f"LRM di {s.get('node')} in stato inatteso: {s.get('status')}.", "manuale §7.4")


def controlla_firewall(inv: dict, esito: Esito):
    """FW-01..06 — i due interruttori, e le regole che non si applicano.

    Il firewall di Proxmox ha DUE interruttori (manuale §15.3): uno al
    datacenter e uno per host. Acceso il primo e spento il secondo, le regole
    definite non filtrano niente — e `pve-firewall status` continua a
    rispondere «enabled/running», quindi a occhio sembra tutto acceso. È il
    difetto che si trova più spesso, e non si vede da un lato solo.
    """
    cl = inv.get("cluster") or {}
    nodi = inv.get("nodi") or {}
    opz_cl = cl.get("fw_options")
    per_nodo = {n: (b.get("nodo") or {}).get("fw_options") for n, b in nodi.items()}
    if opz_cl is None and not any(isinstance(x, dict) for x in per_nodo.values()):
        return  # raccolta più vecchia dell'introduzione di queste chiamate
    A = "Cluster — firewall"
    # L'interruttore del datacenter può non essere leggibile: su PX-NAS
    # `pvesh get /cluster/firewall/options` fallisce con un errore di Perl
    # (2026-09-10). In quel caso si tace SU QUELL'INTERRUTTORE e si continua
    # con quello che si sa: metà del dato è meglio di nessuna regola.
    dc_noto = isinstance(opz_cl, dict)
    acceso_dc = dc_noto and str(opz_cl.get("enable", "")) == "1"
    regole_cl = cl.get("fw_rules") or []

    spenti = []
    for nome, b in nodi.items():
        opz = (b.get("nodo") or {}).get("fw_options")
        if isinstance(opz, dict) and str(opz.get("enable", "")) != "1":
            spenti.append(nome)

    # Firewall spento dappertutto: è la sesta delle sette misure del manuale.
    # Attenzione e non bloccante — un nodo dietro un perimetro può legittimamente
    # non usarlo, ma la scelta va vista, non subita.
    noti = [n for n, o in per_nodo.items() if isinstance(o, dict)]
    if noti and len(spenti) == len(noti) and not acceso_dc:
        dove = "su questo host" if len(noti) == 1 else f"su tutti i {len(noti)} nodi"
        esito.add(ATTENZIONE, A, f"Firewall di Proxmox spento {dove}"
                                 + ("" if dc_noto else " (l'interruttore del datacenter non è leggibile su questa versione)")
                                 + ": nessun filtro sul traffico verso il management.", "manuale §18.1, §15.4")
    if acceso_dc and spenti:
        esito.add(BLOCCANTE, A,
                  f"Firewall acceso al datacenter ma SPENTO sull'host di {', '.join(sorted(spenti))}: "
                  f"le regole non filtrano niente. `pve-firewall status` dice comunque «enabled/running».",
                  "manuale §15.3")
    if regole_cl and dc_noto and len(spenti) == len(nodi) and nodi:
        esito.add(BLOCCANTE, A,
                  f"{len(regole_cl)} regole definite a livello di cluster e nessun host che le applica: "
                  f"esistono sulla carta e non in esercizio.", "manuale §15.3, §15.5")
    if dc_noto and not acceso_dc and any(str(((b.get('nodo') or {}).get('fw_options') or {}).get('enable', '')) == '1'
                                         for b in nodi.values()):
        esito.add(BLOCCANTE, A, "Firewall acceso su un host ma spento al datacenter: l'interruttore generale vince.",
                  "manuale §15.3")

    # FW-04: lo stesso interruttore deve stare nella stessa posizione ovunque.
    per = _valori_per_nodo(inv, lambda n, b: ("acceso" if str(((b.get("nodo") or {}).get("fw_options") or {}).get("enable", "")) == "1"
                                              else "spento") if isinstance((b.get("nodo") or {}).get("fw_options"), dict) else None)
    if len(per) > 1:
        esito.add(BLOCCANTE, "Coerenza — firewall", f"Firewall dell'host in stati diversi fra i nodi — {_elenca(per)}.",
                  "manuale §15.3")

    for nome, b in nodi.items():
        n = b.get("nodo") or {}
        stato = (n.get("pve_firewall") or "")
        if "pending" in stato:
            esito.add(ATTENZIONE, f"Rete — nodo {nome}" if len(nodi) > 1 else "Rete del nodo",
                      "Il firewall ha modifiche in attesa di essere applicate (`pending changes`).", "manuale §15.4")
        opz = n.get("fw_options")
        if not isinstance(opz, dict) or str(opz.get("enable", "")) != "1":
            continue
        if str(opz.get("policy_in", "DROP")).upper() not in ("DROP", "REJECT"):
            esito.add(ATTENZIONE, A, f"Nodo {nome}: politica in ingresso «{opz.get('policy_in')}» con il firewall acceso.",
                      "manuale §15.2")
        # FW-06: prima di chiudere, controllare che resti aperta la porta da cui si entra.
        porte = set()
        for r in list(regole_cl) + list(n.get("fw_rules") or []):
            if str(r.get("action", "")).upper() == "ACCEPT":
                porte |= {p.strip() for p in str(r.get("dport") or "").replace(":", ",").split(",") if p.strip()}
        if not ({"8006", "22"} & porte) and str(opz.get("policy_in", "DROP")).upper() == "DROP":
            esito.add(BLOCCANTE, A, f"Nodo {nome}: firewall acceso, politica DROP e nessuna regola che ammetta "
                                    f"8006 o 22. Accendendolo ci si chiude fuori.", "manuale §15.4")


def controlla_ceph_dettaglio(inv: dict, esito: Esito):
    """CEPH-01..05, 07 — i pool, i monitor, le reti e i flag dimenticati."""
    ing = nodo_ingresso(inv)
    pool = ing.get("ceph_pool")
    if pool is None:
        return
    A = "Cluster — Ceph"
    ceph = (inv.get("cluster") or {}).get("ceph") or {}

    for p in (pool or []):
        nome = p.get("pool_name") or p.get("pool")
        size, minimo = p.get("size"), p.get("min_size")
        if size is not None and int(size) < 3:
            esito.add(BLOCCANTE, A, f"Pool «{nome}»: size {size}. Sotto tre copie un guasto durante un ripristino "
                                    f"perde i dati.", "manuale §6.5")
        if minimo is not None and int(minimo) < 2:
            esito.add(BLOCCANTE, A, f"Pool «{nome}»: min_size {minimo}. Con min_size 1 si continua a scrivere su "
                                    f"una copia sola: è perdita di dati che aspetta.", "manuale §6.5")
        aut = p.get("autoscale_status") or {}
        if aut.get("pg_num_final") and p.get("pg_num") and int(aut["pg_num_final"]) != int(p["pg_num"]):
            esito.add(INFO, A, f"Pool «{nome}»: {p['pg_num']} PG contro i {aut['pg_num_final']} suggeriti "
                               f"dall'autoscaler.", "manuale §6.5")

    mons = ((ceph.get("monmap") or {}).get("mons") or [])
    if mons:
        if len(mons) < 3 or len(mons) % 2 == 0:
            esito.add(BLOCCANTE, A, f"{len(mons)} monitor Ceph: ne servono almeno tre, in numero dispari, "
                                    f"o il quorum di Ceph non regge un guasto.", "manuale §6.4")
    flag = str((ceph.get("osdmap") or {}).get("osdmap", {}).get("flags") or (ceph.get("osdmap") or {}).get("flags") or "")
    for brutto in ("noout", "norebalance", "norecover", "nobackfill"):
        if brutto in flag:
            esito.add(BLOCCANTE, A, f"Flag «{brutto}» attivo: di norma si mette durante una manutenzione e si toglie "
                                    f"dopo. Lasciato acceso, Ceph non ripara più da solo.", "manuale §6.6")

    cfg = ing.get("ceph_cfg") or ""
    pub = re.search(r"public_network\s*=\s*(\S+)", cfg)
    clu = re.search(r"cluster_network\s*=\s*(\S+)", cfg)
    if pub and clu and pub.group(1) == clu.group(1):
        esito.add(ATTENZIONE, A, f"public_network e cluster_network coincidono ({pub.group(1)}): la replica fra OSD "
                                 f"compete con il traffico dei client.", "manuale §6.2")

    # CEPH-04: OSD per nodo. Uno squilibrio sposta il carico e lo spazio utile.
    per_nodo = {}
    for nome, b in (inv.get("nodi") or {}).items():
        albero = (b.get("nodo") or {}).get("ceph_osd")
        if isinstance(albero, dict):
            per_nodo[nome] = _conta_osd(albero.get("root"), nome)
    valori = [v for v in per_nodo.values() if v]
    if len(valori) > 1 and max(valori) > min(valori) * 1.5:
        dettaglio = " · ".join(f"{n}: {v}" for n, v in sorted(per_nodo.items()))
        esito.add(ATTENZIONE, A, f"OSD sbilanciati fra i nodi — {dettaglio}.", "manuale §6.4")


def _conta_osd(albero, nodo: str) -> int:
    """Quanti OSD stanno sotto questo host nell'albero CRUSH."""
    if not isinstance(albero, dict):
        return 0
    if albero.get("type") == "host" and albero.get("name") == nodo:
        return sum(1 for c in (albero.get("children") or []) if isinstance(c, dict) and c.get("type") == "osd")
    return sum(_conta_osd(c, nodo) for c in (albero.get("children") or []))


def controlla_storage_dettaglio(inv: dict, esito: Esito):
    """STOR-04/05/06/08 — le caratteristiche, non solo la presenza."""
    cl = inv.get("cluster") or {}
    nodi = inv.get("nodi") or {}
    A = "Coerenza — storage"

    for d in (cl.get("storage_def") or []):
        nome = d.get("storage")
        ristretti = str(d.get("nodes") or "")
        if ristretti and nodi:
            esclusi = sorted(set(nodi) - {x.strip() for x in ristretti.split(",")})
            if esclusi:
                esito.add(ATTENZIONE, A, f"Lo storage «{nome}» è dichiarato solo per {ristretti}: "
                                         f"su {', '.join(esclusi)} non esiste.", "manuale §4.1")
        if d.get("type") == "dir" and any(x in str(d.get("content") or "") for x in ("images", "rootdir")) and len(nodi) > 1:
            esito.add(ATTENZIONE, "Storage", f"Lo storage locale «{nome}» accetta dischi di macchine: quelle macchine "
                                             f"non migrano e non vanno in HA.", "manuale §4.1")

    # STOR-05: dischi su storage locale in un cluster.
    if len(nodi) > 1:
        locali = {d.get("storage") for d in (cl.get("storage_def") or []) if not d.get("shared")}
        multi = True
        for nome, b in nodi.items():
            for vmid, v in sorted((b.get("vms") or {}).items(), key=lambda x: int(x[0]) if x[0].isdigit() else 0):
                cfg = normalizza_config(v.get("config"))
                usati = set()
                for k, val in cfg.items():
                    if re.match(r"(scsi|virtio|sata|ide)\d+$", k):
                        s = str(val).split(":")[0]
                        if s in locali:
                            usati.add(s)
                if usati:
                    AV = f"VM {vmid} ({cfg.get('name', '')})" + (f" @{nome}" if multi else "")
                    esito.add(ATTENZIONE, AV, f"Dischi su storage locale ({', '.join(sorted(usati))}): "
                                              f"la macchina non migra a caldo e non può andare in HA.",
                              "manuale §4.1, §7.2")

    # STOR-08: la cache ZFS sui nodi di pari taglia.
    per = _valori_per_nodo(inv, lambda n, b: (b.get("nodo") or {}).get("zfs_arc_max") or None)
    ram = _valori_per_nodo(inv, lambda n, b: round((((b.get("nodo") or {}).get("status") or {}).get("memory") or {}).get("total", 0) / 1e9))
    if len(per) > 1 and len(ram) == 1:
        esito.add(ATTENZIONE, A, f"Nodi con la stessa RAM ma zfs_arc_max diverso — {_elenca(per)}.", "manuale §4.2")


def controlla_notifiche(inv: dict, esito: Esito):
    """BKP-03/04/05/06 — chi viene avvisato quando qualcosa non va."""
    cl = inv.get("cluster") or {}
    endpoint = cl.get("notif_endpoints")
    if endpoint is None:
        return
    A = "Cluster — notifiche"
    matcher = cl.get("notif_matchers") or []

    for j in (cl.get("backup") or []):
        if str(j.get("notification-mode") or "") == "legacy-sendmail":
            esito.add(ATTENZIONE, A, f"Il job di backup {j.get('id')} usa il canale vecchio (legacy-sendmail): "
                                     f"non passa dai target e dai matcher configurati.", "manuale §16.9")
        deposito = str(j.get("storage") or "")
        locali = {d.get("storage") for d in (cl.get("storage_def") or []) if not d.get("shared")}
        if deposito and deposito in locali and len(inv.get("nodi") or {}) > 1:
            esito.add(BLOCCANTE, A, f"Il job di backup {j.get('id')} scrive su «{deposito}», che è locale a un nodo: "
                                    f"se si perde quel nodo si perdono i backup con lui.", "manuale §12.1")

    if not matcher:
        esito.add(BLOCCANTE, A, "Nessun matcher di notifica: gli avvisi non vengono instradati da nessuna parte, "
                                "e un backup fallito non lo sa nessuno.", "manuale §16.6")
    nomi = {str(e.get("name")) for e in (endpoint or [])}
    if nomi and nomi <= {"mail-to-root", "sendmail"}:
        esito.add(ATTENZIONE, A, "L'unico recapito configurato è la posta di root sul nodo: se non esce dal server, "
                                 "l'avviso resta lì.", "manuale §16.2")


# ══════════════════════════ confronto fra due verifiche ══════════════════════════
# «Due bloccanti a settembre e due a ottobre» possono essere quattro problemi
# diversi. Per dire che cosa è stato CHIUSO serve riconoscere lo stesso rilievo
# in due raccolte diverse, e un rilievo oggi è testo.

# I numeri dentro un messaggio sono la MISURA, non l'identità: «latenza 7,9 ms»
# e «latenza 8,1 ms» verso lo stesso nodo sono lo stesso rilievo, misurato due
# volte. Senza questa normalizzazione ogni verifica direbbe che il precedente è
# stato chiuso e ne è comparso uno nuovo — cioè non direbbe niente.
# Gli INDIRIZZI si tengono, il resto dei numeri no: «latenza verso 172.18.10.2»
# e «verso 172.18.10.3» sono due rilievi diversi, non lo stesso misurato due
# volte. Azzerando anche gli indirizzi si fondevano in uno (visto subito, sulla
# raccolta vera: 212 rilievi diventavano 200 impronte).
# La regola, in una riga: **un numero attaccato a una parola è un NOME, un
# numero isolato è una MISURA.** `scsi0`, `net1`, `vlan20`, `bond50` e gli
# indirizzi identificano l'oggetto e si tengono; «7,9 ms» e «166 aggiornamenti»
# sono quello che si è misurato oggi e si azzerano. Senza la seconda alternativa
# i due dischi della stessa macchina diventavano lo stesso rilievo.
RE_MISURA = re.compile(r"(\d+\.\d+\.\d+\.\d+)|([A-Za-z_]+\d+)|(\d+[.,]?\d*)")


def _senza_misure(testo: str) -> str:
    return RE_MISURA.sub(lambda m: m.group(1) or m.group(2) or "N", testo)


def impronta(r) -> tuple:
    """L'identità di un rilievo attraverso il tempo: (ambito, fonte, forma).

    Non è a prova di riscrittura: se domani si cambia il TESTO di una regola, i
    rilievi vecchi risultano chiusi e i nuovi comparsi. È il prezzo di non avere
    un codice per regola, ed è dichiarato qui perché chi riscrive un messaggio
    sappia che sta toccando anche lo storico. Un `codice` esplicito, dove c'è,
    vince su tutto.
    """
    codice = getattr(r, "codice", "")
    if codice:
        return (r.ambito, codice)
    return (r.ambito, r.fonte, _senza_misure(r.messaggio))


def rilievi_json(esito: Esito) -> list:
    """I rilievi come dato, non come documento. Il Markdown è per le persone;
    questo serve a confrontare due verifiche."""
    return [{"livello": r.livello, "ambito": r.ambito, "messaggio": r.messaggio,
             "fonte": r.fonte, "comando": r.comando,
             "impronta": "|".join(impronta(r))} for r in esito.rilievi]


def confronta(prima: list, dopo: list) -> dict:
    """Che cosa è cambiato fra due verifiche dello stesso impianto.

    Torna i rilievi CHIUSI (c'erano e non ci sono più), RIMASTI e NUOVI. È la
    sola forma che risponde alla domanda vera — «il lavoro fatto è servito?» —
    perché il conteggio da solo non distingue un problema chiuso da uno
    sostituito.
    """
    def per_impronta(righe):
        return {x.get("impronta"): x for x in righe if x.get("impronta")}
    a, b = per_impronta(prima), per_impronta(dopo)
    return {
        "chiusi": [a[k] for k in a if k not in b],
        "rimasti": [b[k] for k in b if k in a],
        "nuovi": [b[k] for k in b if k not in a],
    }


# ────────────────────────────── report ──────────────────────────────

ORDINE_CATEGORIE = ["Coerenza del cluster", "Cluster / corosync", "Nodo", "Hardware", "Storage", "Rete",
                    "Performance", "VM — profilo di carico", "VM — parametri", "Container"]


def categoria_di(ambito: str) -> str:
    # I rilievi che nascono dal CONFRONTO fra nodi non appartengono a nessun
    # nodo: «vlan20 ha una rete diversa su PX-03» è un difetto del cluster, e
    # sotto «Nodo PX-03» chi legge non lo collega agli altri due.
    if ambito.startswith("Coerenza"):
        return "Coerenza del cluster"
    if ambito.startswith("Cluster"):
        return "Cluster / corosync"
    if ambito.startswith("VM "):
        return "VM — profilo di carico" if " — profilo " in ambito else "VM — parametri"
    if ambito.startswith("CT "):
        return "Container"
    if ambito.startswith("Performance"):
        return "Performance"
    if any(s in ambito for s in ("disco", "ECC", "RAID")):
        return "Hardware"
    if any(s in ambito for s in ("storage", "ZFS", "multipath", "LVM")):
        return "Storage"
    if any(s in ambito for s in ("rete", "firewall", "orario")):
        return "Rete"
    return "Nodo"


def raggruppa(rilievi):
    g = {}
    for r in rilievi:
        g.setdefault(categoria_di(r.ambito), []).append(r)
    return {k: g[k] for k in ORDINE_CATEGORIE if k in g}


def colora_liv(liv, testo):
    return c(testo, {BLOCCANTE: ROSSO, ATTENZIONE: GIALLO, INFO: GRIGIO}[liv])


def stampa_report(esito: Esito, intest: dict, breve=False):
    L = 90
    print("\n" + c("=" * L, BLU))
    print(c("AUDIT PROXMOX VE — CONFRONTO CON LE BEST PRACTICE DOMARC", GRASSETTO))
    print(c("=" * L, BLU))
    for k, v in intest.items():
        print(f"  {k}: {v}")
    print(c("=" * L, BLU))
    per = raggruppa(esito.rilievi)
    b, a, i = esito.conta(BLOCCANTE), esito.conta(ATTENZIONE), esito.conta(INFO)
    print(f"\n{c('RIEPILOGO', GRASSETTO)}   {colora_liv(BLOCCANTE, f'🔴 {b} bloccanti')} · "
          f"{colora_liv(ATTENZIONE, f'🟡 {a} da valutare')} · {colora_liv(INFO, f'ℹ️  {i} informativi')}")
    for cat, rl in per.items():
        bb = sum(1 for r in rl if r.livello == BLOCCANTE); aa = sum(1 for r in rl if r.livello == ATTENZIONE)
        ii = len(rl) - bb - aa
        print(f"  {cat:<26} " + " · ".join(x for x in (f"🔴 {bb}" if bb else "", f"🟡 {aa}" if aa else "", f"ℹ️  {ii}" if ii else "") if x))
    bl = [r for r in esito.rilievi if r.livello == BLOCCANTE]
    if bl:
        print("\n" + c("DA RISOLVERE PRIMA DI TUTTO", ROSSO))
        for r in bl[:20]:
            print(f"  🔴 {r.ambito}: {r.messaggio}")
    if breve:
        return
    for cat, rl in per.items():
        print(f"\n\n{c('■ ' + cat.upper(), GRASSETTO)}")
        print(c("=" * L, BLU))
        per_amb = {}
        for r in rl:
            per_amb.setdefault(r.ambito, []).append(r)
        for amb, rr in per_amb.items():
            print(f"\n{c(amb, GRASSETTO)}")
            for r in sorted(rr, key=lambda x: ORDINE_LIV[x.livello]):
                print(f"  {SIMBOLO[r.livello]} {r.messaggio}" + (c(f"  [{r.fonte}]", GRIGIO) if r.fonte else ""))
    print("\n" + c("=" * L, BLU))
    print(f"Totale: {b} bloccanti · {a} da valutare · {i} informativi")
    print(c("=" * L, BLU))


def _tab(righe: list, inte: tuple) -> list:
    out = ["", "| " + " | ".join(inte) + " |", "|" + "---|" * len(inte)]
    for r in righe:
        out.append("| " + " | ".join(str(x).replace("|", "/") for x in r) + " |")
    return out + [""]


def sezione_cluster_md(inv: dict) -> list:
    cl = inv.get("cluster", {})
    stato = cl.get("status") or []
    testa = next((x for x in stato if x.get("type") == "cluster"), None)
    r = ["## Cluster e corosync", ""]
    if not testa:
        return r + ["Nodo singolo, non in cluster.", ""]
    n0 = nodo_ingresso(inv)
    pv = parse_pvecm(n0.get("pvecm_status") or "")
    conf = parse_corosync_conf(n0.get("corosync_conf") or "")
    r.append(f"**Nome:** {testa.get('name')} · **Nodi:** {testa.get('nodes')} · **Quorato:** {'sì' if testa.get('quorate') else 'NO'} · "
             f"**Voti:** {pv.get('total', '?')}/{pv.get('expected', '?')} (quorum {pv.get('quorum', '?')}) · "
             f"**Trasporto:** {pv.get('transport', conf.get('transport', '?'))} · **QDevice:** {'sì' if conf.get('qdevice') else 'no'} · "
             f"**link_mode:** {conf.get('link_mode', '?')} · **config_version:** {conf.get('config_version', '?')}  ")
    r.append("")
    raccolti = set((inv.get("nodi") or {}).keys())
    r += _tab([(n.get("name"), n.get("nodeid"), n.get("ip"), "online" if n.get("online") else "OFFLINE",
               n.get("level") or "—", "sì" if n.get("name") in raccolti else "NO") for n in stato if n.get("type") == "node"],
              ("Nodo", "ID", "IP", "Stato", "Sub.", "Raccolto"))
    r.append("### Anelli corosync (per nodo)")
    r.append("")
    righe = []
    for nome, blocco in (inv.get("nodi") or {}).items():
        nd = blocco.get("nodo") or {}
        topo = mappa_rete(nd)
        for l in parse_cfgtool(nd.get("corosync_cfgtool") or ""):
            iface = topo["ip2if"].get(l.get("addr"), "?")
            nic = ", ".join(sorted(nic_fisiche(iface, topo))) if iface != "?" else "?"
            conn = sum(1 for v in l["nodi"].values() if v in ("connected", "localhost"))
            righe.append((nome, l["id"], l.get("addr"), iface, nic, f"{conn}/{len(l['nodi'])}"))
    r += _tab(righe, ("Nodo", "Link", "Indirizzo", "Interfaccia", "NIC fisiche", "Connessi"))
    righe = []
    for nome, blocco in (inv.get("nodi") or {}).items():
        for ip, p in ((blocco.get("nodo") or {}).get("ping_ring") or {}).items():
            righe.append((nome, ip, f"{p['avg_ms']:.2f}" if p.get("avg_ms") is not None else "—",
                          f"{p['max_ms']:.2f}" if p.get("max_ms") is not None else "—", f"{p.get('loss', '—')}%"))
    if righe:
        r.append("### Latenza fra gli anelli (ping da ogni nodo)")
        r.append("")
        r += _tab(righe, ("Da", "Verso", "Media ms", "Max ms", "Perdita"))
    ha = cl.get("ha_resources") or []
    r.append(f"**Risorse HA:** {len(ha)}" + (" — " + ", ".join(f"{x.get('sid')} ({x.get('state')})" for x in ha[:20]) if ha else "") + "  ")
    jobs = cl.get("backup") or []
    r.append(f"**Job di backup:** {len(jobs)}  ")
    if jobs:
        r += _tab([(j.get("id"), j.get("schedule"), j.get("storage"), j.get("mode"), "tutti" if j.get("all") else (j.get("vmid") or "—"),
                    j.get("exclude") or "—", "sì" if (j.get("fleecing") or {}).get("enabled") else "no", "sì" if j.get("enabled", 1) else "NO")
                   for j in jobs], ("Job", "Orario", "Storage", "Modo", "Guest", "Esclusi", "Fleecing", "Attivo"))
    nbu = cl.get("not_backed_up") or []
    r.append(f"**Guest senza backup:** {len(nbu)}" + (" — " + ", ".join(f"{x.get('vmid')} {x.get('name', '')}" for x in nbu) if nbu else "") + "  ")
    r.append(f"**Job di replica:** {len(cl.get('replication') or [])} · **Zone SDN:** {len(cl.get('sdn_zones') or [])}  ")
    res = cl.get("resources") or []
    qemu = [x for x in res if x.get("type") == "qemu"]
    lxc = [x for x in res if x.get("type") == "lxc"]
    r.append(f"**Guest nel cluster:** {len(qemu)} VM ({sum(1 for x in qemu if x.get('status') == 'running')} accese), "
             f"{len(lxc)} CT ({sum(1 for x in lxc if x.get('status') == 'running')} accesi)  ")
    r.append("")
    return r


def sezione_nodo_md(nome: str, blocco: dict) -> list:
    nodo = blocco.get("nodo") or {}
    st = nodo.get("status") or {}
    ci = st.get("cpuinfo") or {}
    mem, sw, rf = st.get("memory") or {}, st.get("swap") or {}, st.get("rootfs") or {}
    sub = nodo.get("subscription") or {}
    r = [f"## Nodo {nome}", ""]
    r.append(f"**PVE:** {st.get('pveversion', '?')} · **Kernel:** {(st.get('current-kernel') or {}).get('release', '?')} · "
             f"**Uptime:** {durata(st.get('uptime'))} · **Boot:** {(st.get('boot-info') or {}).get('mode', '?')} (Secure Boot {'on' if (st.get('boot-info') or {}).get('secureboot') else 'off'})  ")
    r.append(f"**CPU:** {ci.get('model', '?')} — {ci.get('sockets', '?')} socket, {ci.get('cores', '?')} core, {ci.get('cpus', '?')} thread, {ci.get('mhz', '?')} MHz  ")
    r.append(f"**RAM:** {gb(mem.get('used'))} / {gb(mem.get('total'))} ({pct(mem.get('used'), mem.get('total')) or 0:.0f}%) · "
             f"**Swap:** {gb(sw.get('used'))} / {gb(sw.get('total'))} · **Root:** {gb(rf.get('used'))} / {gb(rf.get('total'))} · "
             f"**Load:** {', '.join(st.get('loadavg') or [])} · **KSM:** {gb((st.get('ksm') or {}).get('shared'))}  ")
    r.append(f"**Sottoscrizione:** {sub.get('status', 'assente')} {sub.get('level', '')} — {sub.get('productname', '')} (scade {sub.get('nextduedate', '—')})  ")
    std = [s.get("handle") for s in (nodo.get("apt_repos") or {}).get("standard-repos", []) if s.get("status") == 1]
    r.append(f"**Repository attivi:** {', '.join(std) or '—'} · **Aggiornamenti disponibili:** {len(nodo.get('apt_update') or [])}  ")
    td = nodo.get("timedatectl") or ""
    m1 = re.search(r"System clock synchronized:\s*(\S+)", td); m2 = re.search(r"NTP service:\s*(\S+)", td)
    fwm = re.search(r"Status:\s*(\S+)", nodo.get("pve_firewall") or "")
    r.append(f"**Orologio sincronizzato:** {m1.group(1) if m1 else '—'} (NTP {m2.group(1) if m2 else '—'}) · "
             f"**Firewall PVE:** {fwm.group(1) if fwm else '—'}  ")
    ko = [s.get("name") for s in (nodo.get("services") or []) if s.get("state") != "running" and s.get("unit-state") not in ("masked", "not-found")]
    r.append(f"**Servizi non in esecuzione:** {', '.join(ko) or 'nessuno'}  ")
    r.append("")
    r.append("### Storage")
    r.append("")
    r += _tab([(s.get("storage"), s.get("type"), s.get("content"), gb(s.get("total")), gb(s.get("used")),
               f"{pct(s.get('used'), s.get('total')) or 0:.0f}%", "sì" if s.get("shared") else "no",
               "attivo" if s.get("active") else "NON attivo") for s in (nodo.get("storage") or [])],
              ("Nome", "Tipo", "Contenuto", "Totale", "Usato", "%", "Condiviso", "Stato"))
    zp = [x.split("\t") for x in (nodo.get("zpool_list") or "").splitlines()]
    if zp:
        r.append("### Pool ZFS")
        r.append("")
        r += _tab([tuple(x[:7]) for x in zp if len(x) >= 6], ("Pool", "Size", "Alloc", "Free", "Cap", "Health", "Frag"))
        r.append(f"ARC max: {gb(nodo.get('zfs_arc_max')) if (nodo.get('zfs_arc_max') or '').strip('0') else 'automatico (metà RAM)'}  ")
        r.append("")
    lv = [x.split() for x in (nodo.get("lvs") or "").splitlines() if len(x.split()) == 5]
    if lv:
        r.append("### LVM-thin")
        r.append("")
        r += _tab([(f"{x[1]}/{x[0]}", x[2], x[3], x[4]) for x in lv], ("VG/LV", "Size", "Data%", "Meta%"))
    r.append("### Rete")
    r.append("")
    topo = mappa_rete(nodo)
    righe = []
    for it in (nodo.get("network") or []):
        if it.get("type") in ("eth", "bond", "bridge", "vlan"):
            extra = ""
            if it.get("type") == "bond":
                extra = f"{it.get('bond_mode', '')} [{it.get('slaves', '')}]"
            elif it.get("type") == "bridge":
                extra = f"porte: {it.get('bridge_ports', '—')}" + (" · VLAN-aware" if it.get("bridge_vlan_aware") else "")
            elif it.get("type") == "vlan":
                extra = f"su {it.get('vlan-raw-device', topo['parent'].get(it.get('iface'), '?'))}"
            righe.append((it.get("iface"), it.get("type"), it.get("cidr") or "—", it.get("mtu") or "—",
                          "attiva" if it.get("active") else "no", extra))
    r += _tab(righe, ("Interfaccia", "Tipo", "Indirizzo", "MTU", "Attiva", "Dettagli"))
    for b, testo in (nodo.get("bonding") or {}).items():
        modo = re.search(r"Bonding Mode:\s*(.+)", testo)
        sl = re.findall(r"Slave Interface:\s*(\S+)\nMII Status:\s*(\S+)", testo)
        r.append(f"**{b}:** {modo.group(1) if modo else '?'} — " + ", ".join(f"{s} {st_}" for s, st_ in sl) + "  ")
    r.append("")
    r.append("### Hardware")
    r.append("")
    righe = []
    for d in (nodo.get("disks") or []):
        s = parse_smart((nodo.get("smart") or {}).get(d.get("devpath"), ""), d.get("devpath", ""))
        w = d.get("wearout")
        usura = f"{100 - w}%" if isinstance(w, (int, float)) and w >= 0 else "—"
        temp = f"{s.get('temperatura')}°C" if s.get("temperatura") is not None else "—"
        ore = s.get("power_on_hours")
        det = []
        if s.get("nvme_percentage_used") is not None:
            det.append(f"used {s['nvme_percentage_used']}%")
        if s.get("nvme_critical_warning"):
            det.append(f"CW {s['nvme_critical_warning']}")
        for k, lab in (("ata_reallocated", "realloc"), ("ata_pending", "pending"), ("ata_uncorrectable", "uncorr"), ("ata_crc", "crc")):
            if s.get(k):
                det.append(f"{lab} {s[k]}")
        righe.append((os.path.basename(d.get("devpath", "")), d.get("model", "—"), d.get("type", "—"), gb(d.get("size")),
                      d.get("health", "—"), s.get("salute", "—"), usura, temp, f"{ore:,}" if isinstance(ore, int) else "—",
                      d.get("used") or "—", ", ".join(det) or "—"))
    r += _tab(righe, ("Disco", "Modello", "Tipo", "Size", "Health API", "SMART", "Usura", "Temp", "Ore", "Uso", "Dettagli"))
    ce = sum(int(v["ce"]) for v in (nodo.get("edac") or {}).values() if str(v.get("ce", "")).isdigit())
    ue = sum(int(v["ue"]) for v in (nodo.get("edac") or {}).values() if str(v.get("ue", "")).isdigit())
    raid = "presente" if re.search(r"^md\d+", nodo.get("mdstat") or "", re.M) else "nessun array"
    ecc = "non rilevabile" if not nodo.get("edac") else f"{ce} corretti, {ue} non corretti"
    r.append(f"**ECC:** {ecc} · **RAID mdadm:** {raid}  ")
    r.append("")
    rrd = nodo.get("rrd") or []
    if rrd:
        mc = massimo(rrd, "maxcpu") or 1
        r.append("### Performance — ultima ora (rrddata)")
        r.append("")
        r += _tab([("CPU", f"{(media(rrd, 'cpu') or 0)*100:.0f}%", f"{(massimo(rrd, 'cpu') or 0)*100:.0f}%"),
                   ("I/O wait", f"{(media(rrd, 'iowait') or 0)*100:.1f}%", f"{(massimo(rrd, 'iowait') or 0)*100:.1f}%"),
                   ("Load / CPU", f"{(media(rrd, 'loadavg') or 0)/mc:.2f}", f"{(massimo(rrd, 'loadavg') or 0)/mc:.2f}"),
                   ("RAM usata", gb(media(rrd, "memused")), gb(massimo(rrd, "memused"))),
                   ("Swap usata", gb(media(rrd, "swapused")), gb(massimo(rrd, "swapused"))),
                   ("ARC ZFS", gb(media(rrd, "arcsize")), gb(massimo(rrd, "arcsize"))),
                   ("PSI I/O some", f"{media(rrd, 'pressureiosome') or 0:.1f}%", f"{massimo(rrd, 'pressureiosome') or 0:.1f}%"),
                   ("PSI mem some", f"{media(rrd, 'pressurememorysome') or 0:.1f}%", f"{massimo(rrd, 'pressurememorysome') or 0:.1f}%"),
                   ("Rete in / out", f"{(media(rrd, 'netin') or 0)/1e6:.2f} / {(media(rrd, 'netout') or 0)/1e6:.2f} MB/s", "")],
                  ("Metrica", "Media", "Max"))
    for path, testo in (nodo.get("pveperf") or {}).items():
        p = parse_pveperf(testo)
        r.append(f"### pveperf su `{path}`")
        r.append("")
        v = lambda k, fmt, suf="", p=p: (format(p[k], fmt) + suf) if p.get(k) is not None else "— (non misurato)"
        r += _tab([("CPU BOGOMIPS", v("bogomips", ",.0f")), ("REGEX/SECOND", v("regex_s", ",.0f")),
                   ("BUFFERED READS", v("read_mbs", ",.0f", " MB/s")), ("AVERAGE SEEK TIME", v("seek_ms", ".2f", " ms")),
                   ("FSYNCS/SECOND", v("fsync_s", ",.0f") + "  (indicativo: <200 scarso · 200-1000 accettabile · >1000 buono)"),
                   ("DNS EXT / INT", v("dns_ext_ms", ".0f", " ms") + " / " + v("dns_int_ms", ".0f", " ms"))], ("Voce", "Valore"))
    return r


def sezione_vm_md(vms: list, asseg: dict, esito: Esito, multi: bool, con_rilievi: bool = True) -> list:
    r = ["## Inventario delle VM", ""]
    righe = []
    for v in vms:
        cfg = v.config
        pid = asseg.get(v.vmid, NON_CLASSIFICATA)
        prof = "—" if pid == NON_CLASSIFICATA else PROFILI.get(pid, {}).get("nome", pid)
        reti = ", ".join(f"{x['iface']}:{x.get('modello', '?')}@{x.get('bridge', '?')}" + (f" vlan{x['tag']}" if x.get("tag") else "") +
                         (f" mq{x['queues']}" if x.get("queues") else "") for x in parse_reti(cfg)) or "—"
        b = {"assente": "0", "attivo": "min<max", "presente_fermo": "min=max", "attivo_default": "default"}.get(stato_ballooning(cfg), "?")
        righe.append((v.vmid, v.nodo, v.nome, (v.status or {}).get("status") or v.lista.get("status", "?"), prof, so_di(v),
                      f"{cfg.get('sockets', 1)}×{cfg.get('cores', 1)}", (cfg.get("cpu") or "kvm64").split(",")[0],
                      f"{ram_gb_di(cfg):.0f}", b, cfg.get("bios", "seabios"), cfg.get("machine", "i440fx"),
                      cfg.get("scsihw", "—"), len(dischi_dati(cfg)), f"{disco_gb_di(cfg):.0f}", reti,
                      "sì" if str(cfg.get("agent", "")).startswith(("1", "enabled")) else "no",
                      "sì" if cfg.get("protection") == "1" else "no", "sì" if cfg.get("onboot") == "1" else "no",
                      "sì" if ((v.status or {}).get("ha") or {}).get("managed") else "no",
                      len(v.snapshot), durata((v.status or {}).get("uptime")) if v.running else "—"))
    r += _tab(righe, ("VMID", "Nodo", "Nome", "Stato", "Profilo", "SO", "Sock×core", "CPU type", "RAM GB", "Balloon", "BIOS", "Machine",
                      "scsihw", "Dischi", "GB", "Rete", "Agent", "Prot.", "Onboot", "HA", "Snap", "Uptime"))
    righe = []
    for vm in vms:
        if not vm.running:
            continue
        lat = latenze_blockstat(vm.status)
        fl = max((l["flush_ms"] or 0) for l in lat.values()) if lat else 0
        wr = max((l["wr_ms"] or 0) for l in lat.values()) if lat else 0
        righe.append((vm.vmid, _acc(vm.nome, 22), f"{(media(vm.rrd, 'cpu') or 0)*100:.0f}%", f"{(massimo(vm.rrd, 'cpu') or 0)*100:.0f}%",
                      f"{pct(media(vm.rrd, 'mem'), massimo(vm.rrd, 'maxmem')) or 0:.0f}%",
                      f"{(media(vm.rrd, 'diskread') or 0)/1e6:.2f} / {(media(vm.rrd, 'diskwrite') or 0)/1e6:.2f}",
                      f"{(media(vm.rrd, 'netin') or 0)/1e6:.2f} / {(media(vm.rrd, 'netout') or 0)/1e6:.2f}",
                      f"{wr:.1f} / {fl:.1f}", f"{media(vm.rrd, 'pressureiosome') or 0:.0f}%"))
    if righe:
        r.append("### VM accese — ultima ora")
        r.append("")
        r += _tab(righe, ("VMID", "Nome", "CPU med", "CPU max", "RAM", "Disco R/W MB/s", "Rete in/out MB/s", "Lat. write/flush ms", "PSI I/O"))
    r.append("### Dettaglio per VM")
    r.append("")
    per_vm = {}
    for x in esito.rilievi:
        m = re.match(r"^VM (\d+) ", x.ambito)
        if m:
            per_vm.setdefault(m.group(1), []).append(x)
    for v in vms:
        cfg = v.config
        r.append(f"#### VM {v.vmid} — {v.nome}" + (f" (nodo {v.nodo})" if multi else ""))
        r.append("")
        dd = [(d["bus"], d.get("volume", "").split(":")[0], d.get("size", "—"), d.get("cache", "none"), d.get("aio", "io_uring"),
               d.get("iothread", "0"), d.get("discard", "off"), d.get("ssd", "0"), d.get("backup", "1")) for d in dischi_dati(cfg)]
        if dd:
            r += _tab(dd, ("Disco", "Storage", "Size", "Cache", "AIO", "IOthread", "Discard", "SSD", "Backup"))
        ag = v.agent or {}
        fsr = [(f.get("mountpoint"), f.get("type"), gb(f.get("total-bytes")), gb(f.get("used-bytes")),
                f"{pct(f.get('used-bytes'), f.get('total-bytes')) or 0:.0f}%") for f in (ag.get("fsinfo") if isinstance(ag.get("fsinfo"), list) else [])
               if isinstance(f, dict) and (f.get("total-bytes") or 0) > 1024**3]
        if fsr:
            r += _tab(fsr, ("Mount (guest)", "FS", "Totale", "Usato", "%"))
        ips = []
        for it in (ag.get("interfacce") if isinstance(ag.get("interfacce"), list) else []):
            if not isinstance(it, dict) or it.get("name") == "lo":
                continue
            v4 = [a["ip-address"] for a in (it.get("ip-addresses") or []) if a.get("ip-address-type") == "ipv4" and not a["ip-address"].startswith("127.")]
            if v4:
                ips.append(f"{it.get('name')}: {', '.join(v4)}")
        if ips:
            r.append("**IP nel guest:** " + " · ".join(ips) + "  ")
            r.append("")
        rl = per_vm.get(v.vmid, [])
        if not con_rilievi:
            if not dd and not fsr and not ips:
                r.append("Nessun dettaglio aggiuntivo.")
                r.append("")
            continue
        if rl:
            r += _tab([("🔴" if x.livello == BLOCCANTE else "🟡" if x.livello == ATTENZIONE else "ℹ️", x.messaggio, x.fonte)
                       for x in sorted(rl, key=lambda x: ORDINE_LIV[x.livello])], ("", "Rilievo", "Fonte"))
        else:
            r.append("Nessun rilievo.")
            r.append("")
    return r


def e_spenta(vm) -> bool:
    """Spenta secondo Proxmox — è il campo Stato dell'inventario, non una
    deduzione nostra.

    Se lo stato **manca** (raccolta parziale, nodo che non ha risposto) la VM
    NON è considerata spenta: resta nei rilievi. Meglio un rilievo di troppo
    che una macchina sparita in silenzio da un report.
    """
    st = (vm.status or {}).get("status") or (vm.lista or {}).get("status")
    return bool(st) and st != "running"


def sezione_rilievi_vm_md(vms: list, asseg: dict, esito: Esito, multi: bool) -> list:
    """Nel file dei rilievi: una scheda per VM con i soli rilievi, e il profilo assegnato."""
    per_vm = {}
    for x in esito.rilievi:
        m = re.match(r"^VM (\d+) ", x.ambito)
        if m:
            per_vm.setdefault(m.group(1), []).append(x)
    r = ["## Rilievi per VM", ""]
    senza = []
    for v in vms:
        rl = per_vm.get(v.vmid, [])
        if not rl:
            senza.append(f"{v.vmid} {v.nome}")
            continue
        pid = asseg.get(v.vmid, NON_CLASSIFICATA)
        prof = PROFILI.get(pid, {}).get("nome") if pid != NON_CLASSIFICATA else "non classificata"
        r.append(f"### VM {v.vmid} — {v.nome}" + (f" (nodo {v.nodo})" if multi else "") + f" · profilo: {prof}")
        r += _tab([("🔴" if x.livello == BLOCCANTE else "🟡" if x.livello == ATTENZIONE else "ℹ️", x.messaggio, x.fonte)
                   for x in sorted(rl, key=lambda x: ORDINE_LIV[x.livello])], ("", "Rilievo", "Fonte"))
    if senza:
        r.append("**VM senza rilievi:** " + ", ".join(senza) + "  ")
        r.append("")
    return r


def sezione_lxc_md(inv: dict) -> list:
    righe = []
    for nome, blocco in (inv.get("nodi") or {}).items():
        for ctid, d in sorted((blocco.get("lxc") or {}).items(), key=lambda x: int(x[0])):
            cfg = normalizza_config(d.get("config"))
            righe.append((ctid, nome, cfg.get("hostname", "—"), (d.get("status") or {}).get("status", "?"), cfg.get("cores", "—"),
                          f"{int(cfg.get('memory', 0) or 0)/1024:.1f}", cfg.get("rootfs", "—").split(",")[0],
                          "sì" if cfg.get("unprivileged") == "1" else "NO", "sì" if cfg.get("onboot") == "1" else "no", cfg.get("features", "—")))
    if not righe:
        return []
    return ["## Container LXC", ""] + _tab(righe, ("CTID", "Nodo", "Hostname", "Stato", "Core", "RAM GB", "Rootfs", "Unprivileged", "Onboot", "Features"))


def _intestazione_md(titolo: str, intest: dict) -> list:
    r = [f"# {titolo}", ""]
    for k, v in intest.items():
        r.append(f"**{k}:** {v}  ")
    return r + [""]


def _piede_md() -> list:
    return ["---", "", f"*Generato da `audit-nodo.py` {VERSIONE_SCRIPT}. Solo lettura: nessuna configurazione è stata modificata.*"]


def scrivi_inventario_md(path: Path, inv: dict, intest: dict, vms: list, asseg: dict):
    """Il file di INVENTARIO: cosa c'è. Nessun giudizio, solo dati."""
    multi = len(inv.get("nodi") or {}) > 1
    r = _intestazione_md("Inventario Proxmox VE — " + str(intest.get("Cliente") or intest.get("Cluster")), intest)
    r += sezione_cluster_md(inv)
    for nome, blocco in (inv.get("nodi") or {}).items():
        r += sezione_nodo_md(nome, blocco)
    if vms:
        r += sezione_vm_md(vms, asseg, Esito(), multi, con_rilievi=False)
    r += sezione_lxc_md(inv)
    errori = list(inv.get("errori") or [])
    for nome, blocco in (inv.get("nodi") or {}).items():
        errori += [f"{nome}: {e}" for e in (blocco.get("errori") or [])]
    if errori:
        r += ["## Dati non raccolti", ""] + [f"- {e}" for e in errori] + [""]
    r += _piede_md()
    path.write_text("\n".join(r), encoding="utf-8")


def scrivi_rilievi_md(path: Path, esito: Esito, inv: dict, intest: dict, vms: list, asseg: dict):
    """Il file dei RILIEVI: cosa non torna rispetto alle best practice, e perché."""
    multi = len(inv.get("nodi") or {}) > 1
    r = _intestazione_md("Rilievi Proxmox VE — " + str(intest.get("Cliente") or intest.get("Cluster")), intest)
    b, a, i = esito.conta(BLOCCANTE), esito.conta(ATTENZIONE), esito.conta(INFO)
    r += ["## Riepilogo esecutivo", "", f"**{b} bloccanti · {a} da valutare · {i} informativi**", ""]
    per = raggruppa(esito.rilievi)
    r += _tab([(cat, sum(1 for x in rl if x.livello == BLOCCANTE), sum(1 for x in rl if x.livello == ATTENZIONE),
               sum(1 for x in rl if x.livello == INFO)) for cat, rl in per.items()], ("Categoria", "Bloccanti", "Da valutare", "Info"))
    bl = [x for x in esito.rilievi if x.livello == BLOCCANTE]
    if bl:
        r += ["### Da risolvere prima di tutto", ""]
        r += [f"- 🔴 **{x.ambito}** — {x.messaggio} *[{x.fonte}]*" for x in bl] + [""]
    r += ["## Legenda", "", "- 🔴 **Bloccante**: rischio concreto di fermo, perdita dati o mancato aggiornamento; da trattare per primo.",
          "- 🟡 **Da valutare**: scostamento dalla best practice con un costo reale (prestazioni, ripristino, sicurezza).",
          "- ℹ️ **Informativo**: dato utile a chi decide, nessuna azione obbligata.",
          "- La colonna *Fonte* cita il paragrafo da cui viene la regola. **Il testo di ogni regola citata è in fondo al report**, "
          "nella sezione «Le regole applicate»: per capire un rilievo non serve avere il manuale sottomano.", ""]
    r += ["## Rilievi su cluster, nodi, hardware, storage, rete e performance", ""]
    for cat, rl in per.items():
        if cat.startswith("VM") or cat == "Container":
            continue
        r += [f"### {cat}"]
        r += _tab([("🔴 BLOCCANTE" if x.livello == BLOCCANTE else "🟡 attenzione" if x.livello == ATTENZIONE else "ℹ️ info",
                    x.ambito, x.messaggio, x.fonte) for x in sorted(rl, key=lambda y: (y.ambito, ORDINE_LIV[y.livello]))],
                  ("Livello", "Ambito", "Rilievo", "Fonte"))
    if vms:
        r += sezione_rilievi_vm_md(vms, asseg, esito, multi)
    ct = per.get("Container") or []
    if ct:
        r += ["## Rilievi sui container", ""]
        r += _tab([("🔴 BLOCCANTE" if x.livello == BLOCCANTE else "🟡 attenzione" if x.livello == ATTENZIONE else "ℹ️ info",
                    x.ambito, x.messaggio, x.fonte) for x in ct], ("Livello", "Ambito", "Rilievo", "Fonte"))
    if getattr(ARGOMENTI, "prontezza", False):
        r += sezione_prontezza_md(esito)
    r += sezione_comandi_md(esito)
    r += sezione_regole_md(esito)
    r += _piede_md()
    path.write_text("\n".join(r), encoding="utf-8")


RE_QM = re.compile(r"^qm set (\d+) --(\S+)")


def _bersaglio(comando: str):
    """Che cosa tocca questo comando: (macchina, parametro).

    Serve a non emettere due comandi che si disfano a vicenda — `--cpu host` e
    `--cpu x86-64-v2-AES` sulla stessa VM, o due righe dello stesso disco.
    Visto il 2026-09-10 appena i comandi sono nati: la regola di profilo e
    quella generale prescrivevano due CPU diverse per la stessa macchina.
    """
    m = RE_QM.match(comando.strip())
    return (m.group(1), m.group(2)) if m else (None, comando.strip())


def _macchina(ambito: str) -> str:
    """«VM 100 (nome) @PX-03 — profilo Rete: …» → «VM 100 (nome) @PX-03».

    I rilievi di profilo e quelli generali hanno ambiti diversi per la STESSA
    macchina: senza questo, i comandi di una VM finiscono in due blocchi
    separati e chi legge ne applica metà.
    """
    return ambito.split(" — profilo ")[0].strip()


# ══════════════════════ prontezza alla migrazione (§11.4) ══════════════════════
# «Questo cluster è pronto a ricevere?» non è una domanda diversa da «questo
# cluster è sano?»: sono gli STESSI rilievi, letti nell'ordine in cui §11.4 li
# chiede. Non si scrivono regole nuove, si raggruppano quelle che ci sono —
# altrimenti due elenchi della stessa cosa finiscono per divergere.
PRONTEZZA = [
    ("Rete", "§11.4.1", (
        "reti di servizio", "migrazione", "anelli", "Coerenza — rete", "Rete —", "Rete del nodo")),
    ("Sistema", "§11.4.2", (
        "Coerenza — host", "orario", "Coerenza — firewall")),
    ("Storage", "§11.4.1", (
        "Coerenza — storage", "storage", "ZFS")),
    ("Backup", "§11.4.3", (
        "Cluster — notifiche",)),
]


ARGOMENTI = None   # gli argomenti della riga di comando, per chi scrive il report


def sezione_prontezza_md(esito: Esito) -> list:
    """Il cluster visto come DESTINAZIONE di una migrazione.

    §11.4 elenca che cosa completare prima della prima migrazione, e quasi ogni
    voce corrisponde a una regola che già applichiamo. Qui si dice, per ciascuna
    area, se è a posto — e se non lo è, che cosa manca.
    """
    righe, dettagli = [], []
    for area, fonte, indizi in PRONTEZZA:
        suoi = [x for x in esito.rilievi
                if x.livello != INFO and any(s.lower() in x.ambito.lower() for s in indizi)]
        b = sum(1 for x in suoi if x.livello == BLOCCANTE)
        giudizio = "da sistemare" if b else ("da valutare" if suoi else "a posto")
        righe.append((area, fonte, giudizio, b, len(suoi) - b))
        if suoi:
            dettagli.append((area, suoi))
    if not righe:
        return []
    r = ["## Prontezza alla migrazione", "",
         "Il manuale (§11.4) elenca che cosa completare **prima della prima migrazione**. "
         "Non sono controlli diversi da quelli del resto del report: sono gli stessi rilievi, "
         "letti rispondendo a un'altra domanda — non «questo cluster è sano» ma "
         "«questo cluster è pronto a ricevere».", ""]
    r += _tab(righe, ("Area", "Manuale", "Giudizio", "Bloccanti", "Da valutare"))
    for area, suoi in dettagli:
        peggio = [x for x in suoi if x.livello == BLOCCANTE] or suoi
        r.append(f"**{area}** — " + "; ".join(x.messaggio.rstrip(".") for x in peggio[:3]) +
                 ("; …" if len(peggio) > 3 else "") + ".  ")
    r.append("")
    return r


def sezione_comandi_md(esito: Esito) -> list:
    """I comandi che chiudono i rilievi agibili, raggruppati per macchina.

    È la differenza fra un elenco di cose che non vanno e una lista di cose da
    fare. Non tutti i rilievi ci finiscono, ed è voluto: «VLAN 20 ha reti diverse
    fra i nodi» non ha un comando, ha una decisione. Quelli che ne hanno uno lo
    mostrano; per gli altri non se ne inventa uno approssimativo.
    """
    agibili = esito.agibili()
    if not agibili:
        return []
    per = {}
    for x in agibili:
        per.setdefault(_macchina(x.ambito), []).append(x)

    r = ["## Cosa fare — i comandi", "",
         "Sono riportati qui sotto, raggruppati per macchina.", "",
         "> ⚠️ **Nessuno di questi comandi è stato eseguito.** Lo strumento legge e basta. "
         "Vanno letti prima di lanciarli: alcuni richiedono la macchina spenta, e il commento "
         "in coda dice perché ciascuno serve.", ""]
    quanti = 0
    corpo = []
    def _ordine(voce):
        # Prima le macchine che hanno un bloccante, poi per VMID crescente.
        # Niente walrus: lo strumento gira anche su Python 3.7.
        nome, righe = voce
        m = re.match(r"^VM (\d+)", nome)
        return (min(ORDINE_LIV[x.livello] for x in righe), int(m.group(1)) if m else 0, nome)

    for nome, righe in sorted(per.items(), key=_ordine):
        # Un solo comando per parametro: vince quello del rilievo più grave, che
        # è anche il più specifico (la regola di profilo batte quella generale).
        # A parità di gravità vince la regola di PROFILO: sa che cosa fa la
        # macchina, e la generica no. Su un firewall il manuale vuole `--cpu
        # host`, non `x86-64-v2-AES` — e senza questo criterio vinceva la
        # generica solo perché viene valutata prima.
        scelti, visti = [], set()
        for x in sorted(righe, key=lambda y: (ORDINE_LIV[y.livello],
                                              0 if " — profilo " in y.ambito else 1)):
            b = _bersaglio(x.comando)
            if b in visti:
                continue
            visti.add(b)
            scelti.append(x)
        if not scelti:
            continue
        corpo.append(f"### {nome}")
        corpo.append("")
        corpo.append("```bash")
        for x in scelti:
            quanti += 1
            segno = "🔴" if x.livello == BLOCCANTE else "🟡" if x.livello == ATTENZIONE else "ℹ️"
            base, _, gia = x.comando.partition("  #")
            nota = (gia.strip() + " · " if gia.strip() else "") + x.messaggio.split(".")[0]
            corpo.append(f"{base.strip():<52} # {segno} {nota}")
        corpo.append("```")
        corpo.append("")
    r[2] = f"**{quanti} comandi** chiudono {len(agibili)} dei {len(esito.rilievi)} rilievi, raggruppati per macchina."
    return r + corpo


def sezione_regole_md(esito: Esito) -> list:
    """Le regole citate nel report, con il testo per esteso: il report deve
    bastare a sé stesso, senza il manuale sottomano. Compaiono solo le regole
    che un rilievo ha davvero citato, e ognuna porta il file e la riga da cui
    viene, così chi ha il manuale può risalire e chi non ce l'ha legge qui."""
    citate = []
    for x in esito.rilievi:
        for a in ancore_di(x.fonte):
            if a not in citate:
                citate.append(a)
    if not citate:
        if esito.rilievi and not REGOLE:
            return ["## Le regole applicate", "",
                    "*Il testo delle regole non è disponibile in questa copia dello strumento "
                    "(manca `fonti_manuale.py`): ogni rilievo riporta comunque il paragrafo del manuale che lo motiva.*", ""]
        return []
    ed = EDIZIONE_MANUALE or {}
    r = ["## Le regole applicate", "",
         f"Il testo delle regole citate dai rilievi, così il report basta a sé stesso. Dal "
         f"**{ed.get('nome', 'manuale operativo Proxmox VE — Domarc')}**"
         + (f", edizione {ed['versione']}" if ed.get("versione") not in (None, "?") else "")
         + (f", verificata il {ed['verificato']}" if ed.get("verificato") not in (None, "?") else "") + ".", ""]
    for a in sorted(citate, key=ordine_regola):
        v = REGOLE[a]
        r.append(f"### {'manuale ' if a.startswith('§') else ''}{a}")
        r.append("")
        origine = v.get("origine") or (f"`{v['file']}`" + (f", riga {v['riga']}" if v.get("riga") else ""))
        r.append(f"**{v['titolo']}** — {origine}  ")
        r.append("")
        r.append(v["testo"])
        if v.get("troncato"):
            r.append("")
            r.append("*(il paragrafo prosegue nel manuale)*")
        r.append("")
    return r


def _slug(t: str) -> str:
    t = re.sub(r"[^A-Za-z0-9]+", "-", (t or "").strip()).strip("-")
    return t or "x"


def nomi_file_report(cartella: Path, codice: str, nome: str, host: str) -> tuple:
    """codcli_nomecli_IP_inventory.md e codcli_nomecli_IP_report.md (il codice
    si omette se non c'è)."""
    ip = re.sub(r"[^A-Za-z0-9.]+", "-", (host or "").split("@")[-1]).strip("-.")  # l'IP tiene i punti
    parti = [x for x in (_slug(codice) if codice else "", _slug(nome), ip) if x]
    parti = [x for i, x in enumerate(parti) if i == 0 or x != parti[i - 1]]  # niente PX-NAS_PX-NAS
    base = "_".join(parti)
    return cartella / f"{base}_inventory.md", cartella / f"{base}_report.md"


# ────────────────────────────── stato locale: host e profili ──────────────────────────────

CONFIG_DIR = Path.home() / ".config" / "audit-nodo"
CONFIG_HOST = CONFIG_DIR / "hosts.json"


def percorso_profili_default(chiave: str) -> Path:
    """Un file di profili per cluster (o per host, se singolo): solo VMID →
    tipologia, mai una password — questo script non la vede mai."""
    sicuro = re.sub(r"[^A-Za-z0-9.-]+", "-", chiave).strip("-") or "host"
    return CONFIG_DIR / f"profili-{sicuro}.json"


CAMPI_VUOTI_HOST = {"etichetta": None, "host": "", "cliente": None, "codice": None, "solo_accese": False,
                    "solo_nodo": False, "solo_questo_nodo": False, "performance": False, "profili": None,
                    "output": None, "ultimo_audit": None}


def carica_host_salvati() -> list:
    if not CONFIG_HOST.is_file():
        return []
    try:
        return json.loads(CONFIG_HOST.read_text(encoding="utf-8")).get("hosts", [])
    except (json.JSONDecodeError, OSError):
        print(f"ATTENZIONE: {CONFIG_HOST} non leggibile, ignorato.", file=sys.stderr)
        return []


def salva_host_salvati(hosts: list):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_HOST.write_text(json.dumps({"hosts": hosts}, indent=2, ensure_ascii=False), encoding="utf-8")


def descrivi_voce(v: dict) -> str:
    parti = [v["host"]]
    if v.get("cliente"):
        parti.append((f"{v['codice']} " if v.get("codice") else "") + v["cliente"])
    for k, lab in (("solo_accese", "solo accese"), ("solo_nodo", "solo nodo"), ("solo_questo_nodo", "solo questo nodo"), ("performance", "performance")):
        if v.get(k):
            parti.append(lab)
    if v.get("output"):
        parti.append(f"output={v['output']}")
    u = v.get("ultimo_audit")
    if u:
        parti.append(f"ultimo {u.get('data', '?')}: 🔴{u.get('b', 0)} 🟡{u.get('a', 0)}")
    return " · ".join(parti)


def chiedi_indice(hosts: list, azione: str):
    for i, v in enumerate(hosts, 1):
        print(f"  {i}) {v.get('etichetta') or v['host']}")
    s = input(f"Quale {azione}? (numero, vuoto per annullare): ").strip()
    return int(s) - 1 if s.isdigit() and 1 <= int(s) <= len(hosts) else None


def avvisa_se_manca_utente(host: str):
    if host and "@" not in host:
        print(f"  (nota: senza utente verrà usato 'root@{host}')")


def crea_host_interattivo():
    host = input("Host/indirizzo SSH (utente@ip o alias di ~/.ssh/config — vuoto per annullare): ").strip()
    if not host:
        return None
    avvisa_se_manca_utente(host)
    et = input("Etichetta (invio per usare l'indirizzo): ").strip()
    v = dict(CAMPI_VUOTI_HOST); v["host"] = host; v["etichetta"] = et or None
    v["cliente"] = input("Nome cliente: ").strip() or None
    v["codice"] = input("Codice cliente (INVIO se non c'è): ").strip() or None
    return v


def menu_profili_vm(percorso: Path):
    noti = {}
    if percorso.is_file():
        try:
            noti = converti_profili(json.loads(percorso.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            print(f"ATTENZIONE: {percorso} non è un JSON leggibile.", file=sys.stderr)
    salva = lambda: percorso.write_text(json.dumps({"_versione": VERSIONE_PROFILI, **noti}, indent=2, ensure_ascii=False), encoding="utf-8")
    while True:
        print(f"\nProfili VM salvati in {percorso}:")
        if not noti:
            print("  (vuoto)")
        for vmid, pid in sorted(noti.items(), key=lambda x: (len(x[0]), x[0])):
            print(f"  VM {vmid:<8} {PROFILI.get(pid, {}).get('nome', 'non classificata' if pid == NON_CLASSIFICATA else f'id {pid}?')}")
        print("\n  c) Cambia/aggiungi   r) Rimuovi   m) Torna")
        s = input("Scelta: ").strip().lower()
        if s == "m":
            return
        if s == "c":
            vmid = input("  VMID: ").strip()
            if not vmid.isdigit():
                print("  VMID non valido."); continue
            for k, v in PROFILI.items():
                print(f"    {k:>2}) {v['nome']}")
            print(f"     {NON_CLASSIFICATA}) Non classificata")
            pid = input("  Profilo: ").strip()
            if pid in PROFILI or pid == NON_CLASSIFICATA:
                noti[vmid] = pid
                percorso.parent.mkdir(parents=True, exist_ok=True)
                salva()
                print("  Salvato.")
        elif s == "r":
            vmid = input("  VMID da rimuovere: ").strip()
            if vmid in noti:
                del noti[vmid]
                salva()
                print("  Rimosso.")
        else:
            print("  Scelta non valida.")


def menu_parametri(voce: dict, hosts: list, indice):
    voce = {**CAMPI_VUOTI_HOST, **voce}
    while True:
        u = voce.get("ultimo_audit") or {}
        prof = Path(voce["profili"]) if voce.get("profili") else (Path(u["profili"]) if u.get("profili") else
               (percorso_profili_default(host_con_utente_default(voce["host"])) if voce["host"] else None))
        print("\n" + "-" * 70)
        print(c(f"Parametri per {voce.get('etichetta') or voce['host']}", GRASSETTO))
        print("-" * 70)
        print(f"  1) Host/indirizzo SSH ............ {voce['host']}")
        print(f"  2) Etichetta ..................... {voce.get('etichetta') or '(nessuna)'}")
        cli = voce.get("cliente") or "(chiesto all'avvio)"
        print(f"  3) Cliente (nome / codice) ....... {cli} / {voce.get('codice') or '—'}")
        print(f"  4) Solo VM accese ................ {'sì' if voce.get('solo_accese') else 'no'}")
        print(f"  5) Solo controlli nodo (no VM) ... {'sì' if voce.get('solo_nodo') else 'no'}")
        print(f"  6) Solo questo nodo (no cluster) . {'sì' if voce.get('solo_questo_nodo') else 'no'}")
        print(f"  7) Test di performance (pveperf) . {'sì' if voce.get('performance') else 'no'}")
        print(f"  8) Cartella dei report ........... {voce.get('output') or '(cartella corrente)'}")
        print(f"  9) File profili .................. {voce.get('profili') or '(automatico per cluster/host)'}")
        if prof and prof.is_file():
            print(f" 10) Vedi/modifica i profili VM già assegnati ({prof.name})")
        if u:
            print(f"\n  Ultimo audit: {u.get('data')} — 🔴 {u.get('b', 0)} · 🟡 {u.get('a', 0)} · ℹ️ {u.get('i', 0)}" +
                  (f" — report {u.get('output')}" if u.get("output") else ""))
        print("\n  a) Avvia l'audit   s) Salva parametri   m) Torna al menu host")
        s = input("Scelta: ").strip().lower()
        if s == "m":
            return
        elif s == "1":
            n = input(f"  Nuovo valore [{voce['host']}]: ").strip(); voce["host"] = n or voce["host"]; avvisa_se_manca_utente(voce["host"])
        elif s == "2":
            voce["etichetta"] = input("  Etichetta: ").strip() or None
        elif s == "3":
            voce["cliente"] = input("  Nome cliente: ").strip() or None
            voce["codice"] = input("  Codice cliente (vuoto se non c'è): ").strip() or None
        elif s in ("4", "5", "6", "7"):
            k = {"4": "solo_accese", "5": "solo_nodo", "6": "solo_questo_nodo", "7": "performance"}[s]
            voce[k] = not voce.get(k)
        elif s == "8":
            voce["output"] = input("  Cartella dei report (vuoto = cartella corrente): ").strip() or None
        elif s == "9":
            voce["profili"] = input("  Percorso file profili (vuoto = automatico): ").strip() or None
        elif s == "10" and prof and prof.is_file():
            menu_profili_vm(prof)
        elif s == "s":
            if not voce["host"]:
                print("  Manca l'host."); continue
            if indice is not None:
                hosts[indice] = voce
            else:
                hosts.append(voce); indice = len(hosts) - 1
            salva_host_salvati(hosts); print(f"  Salvato in {CONFIG_HOST}.")
        elif s == "a":
            if not voce["host"]:
                print("  Manca l'host."); continue
            ns = SimpleNamespace(host=voce["host"], cliente=voce.get("cliente"), codice=voce.get("codice"),
                                 profili=Path(voce["profili"]) if voce.get("profili") else None,
                                 salva_profili=None, output=Path(voce["output"]) if voce.get("output") else None, json=None, da_json=None,
                                 solo_nodo=voce.get("solo_nodo", False), solo_accese=voce.get("solo_accese", False),
                                 solo_questo_nodo=voce.get("solo_questo_nodo", False),
                                 performance=voce.get("performance", False), breve=False, max_vm=0)
            ris = esegui(ns)
            if ris:
                voce["ultimo_audit"] = ris
                if indice is not None:
                    hosts[indice] = voce
                else:
                    hosts.append(voce); indice = len(hosts) - 1
                salva_host_salvati(hosts)
            input("\nPremi INVIO per tornare al menu...")
        else:
            print("  Scelta non valida.")


def menu_principale():
    while True:
        hosts = carica_host_salvati()
        print("\n" + c("=" * 70, BLU))
        print(c("AUDIT-NODO — menu", GRASSETTO))
        print(c("=" * 70, BLU))
        if hosts:
            print("\nHost salvati:")
            for i, v in enumerate(hosts, 1):
                print(f"  {i}) {(v.get('etichetta') or v['host']):<22} {descrivi_voce(v)}")
        else:
            print("\nNessun host salvato ancora.")
        print("\n  n) Nuovo host" + ("   e) Modifica   d) Rimuovi" if hosts else "") + "   q) Esci")
        s = input("Scelta: ").strip().lower()
        if s == "q":
            return
        elif s == "n":
            v = crea_host_interattivo()
            if v:
                menu_parametri(v, hosts, None)
        elif s == "e" and hosts:
            i = chiedi_indice(hosts, "modificare")
            if i is not None:
                menu_parametri(hosts[i], hosts, i)
        elif s == "d" and hosts:
            i = chiedi_indice(hosts, "rimuovere")
            if i is not None:
                rimosso = hosts.pop(i); salva_host_salvati(hosts); print(f"Rimosso: {rimosso.get('etichetta') or rimosso['host']}")
        elif s.isdigit() and hosts and 1 <= int(s) <= len(hosts):
            menu_parametri(hosts[int(s) - 1], hosts, int(s) - 1)
        else:
            print("Scelta non valida.")


# ────────────────────────────── esecuzione ──────────────────────────────

def chiedi_cliente(args) -> tuple:
    """Nome e codice del cliente: dagli argomenti, altrimenti chiesti qui se
    c'è un terminale. Servono per il nome dei file e l'intestazione."""
    nome = (getattr(args, "cliente", None) or "").strip()
    codice = (getattr(args, "codice", None) or "").strip()
    if not nome and sys.stdin.isatty():
        nome = input("Nome cliente: ").strip()
        if not codice:
            codice = input("Codice cliente (INVIO se non c'è): ").strip()
    return nome, codice


def esegui(args):
    global HOST_REMOTO
    HOST_REMOTO = host_con_utente_default(args.host) if args.host else None
    nome_cliente, codice_cliente = chiedi_cliente(args)
    if HOST_REMOTO and HOST_REMOTO != args.host:
        print(c(f"Nessun utente specificato: uso '{HOST_REMOTO}'.", GRIGIO), file=sys.stderr)
    t0 = time.time()
    if getattr(args, "da_json", None):
        # Un percorso sbagliato o un file monco non devono dare una traccia di
        # stack a chi sta davanti a un cluster: si dice cosa non va e si esce.
        try:
            inv = json.loads(Path(args.da_json).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"Non riesco a leggere {args.da_json}: {e}", file=sys.stderr)
            return None
    else:
        inv = raccogli(solo_accese=args.solo_accese, performance=args.performance,
                       tutto_cluster=not getattr(args, "solo_questo_nodo", False), max_vm=getattr(args, "max_vm", 0))
    if not inv or not inv.get("nodi"):
        print("Nessun dato raccolto: connessione fallita, o la destinazione non è un nodo Proxmox VE con python3 e pvesh.", file=sys.stderr)
        return None
    # L'istante della raccolta viaggia col dato: le regole che misurano un'età
    # devono sapere che ora era allora, non che ora è adesso. Si timbra SOLO
    # quando si raccoglie davvero: timbrarlo anche qui vorrebbe dire scrivere
    # «adesso» su un JSON letto da disco, cioè esattamente l'errore che questo
    # campo esiste per evitare (visto il 2026-09-10: sette job dichiarati in
    # ritardo di tre ore perché la raccolta aveva tre ore).
    if not getattr(args, "da_json", None):
        inv.setdefault("raccolto_il", int(time.time()))
    if args.json:
        Path(args.json).write_text(json.dumps(inv, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"Dati grezzi salvati in {args.json}", file=sys.stderr)
    if getattr(args, "invia", None):
        if not getattr(args, "codice_portale", None):
            print("Con --invia serve anche --codice-portale (quello che vi ha dato Domarc).", file=sys.stderr)
        else:
            invia_al_portale(inv, args.invia, args.codice_portale,
                             cliente=args.cliente or "", codice_cliente=args.codice or "")

    cluster_nome = next((x.get("name") for x in (inv.get("cluster", {}).get("status") or []) if x.get("type") == "cluster"), None)
    multi = len(inv.get("nodi") or {}) > 1
    if not nome_cliente:
        nome_cliente = cluster_nome or inv.get("ingresso") or "cliente"
    esito = Esito()
    controlla_cluster(inv, esito)
    for nome, blocco in inv["nodi"].items():
        controlla_nodo(nome, blocco, inv, esito)
        controlla_hardware(nome, blocco, esito)
        controlla_performance(nome, blocco, esito)

    chiave = cluster_nome or inv.get("ingresso") or "host"   # il cluster, o l'hostname del nodo: mai l'indirizzo, che cambia
    auto = percorso_profili_default(chiave)
    lettura = args.profili or auto
    noti = {}
    if lettura.is_file():
        try:
            grezzi = json.loads(lettura.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            grezzi = {}
        noti = converti_profili(grezzi)
        if grezzi and grezzi.get("_versione") != VERSIONE_PROFILI:
            print(c("Profili salvati con le 13 tipologie della versione 1: convertiti alle 7 attuali.", GRIGIO), file=sys.stderr)
        if noti:
            print(c(f"Profili già noti ({len(noti)} VM), da {lettura}", GRIGIO), file=sys.stderr)

    vms = costruisci_vms(inv) if not args.solo_nodo else []
    asseg = dict(noti)
    # Le domande si fanno solo se c'è qualcuno che può rispondere E nessuno
    # gestisce già le tipologie altrove. Con --invia le gestisce il portale:
    # chiederle qui vorrebbe dire raccogliere due volte la stessa decisione e
    # non sapere quale delle due vale.
    chiedere = (vms and sys.stdin.isatty()
                and not getattr(args, "senza_domande", False)
                and not getattr(args, "invia", None))
    if chiedere:
        asseg = assegna_profili_da_tabella(vms, noti, multi)
    # I RILIEVI si fanno sulle macchine ACCESE. Una spenta non ha un carico da
    # confrontare con la propria tipologia, e nove template fermi riempiono il
    # report di rilievi che nessuno agirà. Restano nell'INVENTARIO, che è il
    # censimento di cosa c'è: --con-spente le rimette anche fra i rilievi.
    spente = [v for v in vms if e_spenta(v)]
    vms_rilievi = vms if args.con_spente else [v for v in vms if not e_spenta(v)]
    for v in vms:
        # La tipologia si assegna a TUTTE, anche alle spente: si accendono, e la
        # scelta dev'essere già lì. Senza terminale (o per VM nuove) vale la
        # proposta automatica: meglio i controlli del profilo probabile che nessuno.
        if asseg.get(v.vmid, NON_CLASSIFICATA) == NON_CLASSIFICATA:
            asseg[v.vmid] = suggerisci_profilo(v)
    for v in vms_rilievi:
        controlla_generali(v, inv, esito)
        controlla_profilo(v, asseg.get(v.vmid, NON_CLASSIFICATA), inv, esito)
    # Le regole che guardano il CLUSTER invece del singolo nodo. Valgono sempre,
    # anche con --con-spente o senza: non parlano di macchine, parlano di come
    # sono messi i nodi fra loro.
    controlla_coerenza_rete(inv, esito)
    controlla_coerenza_host(inv, esito)
    controlla_reti_di_servizio(inv, esito)
    controlla_migrazione(inv, esito)
    controlla_replica_ha(inv, esito)
    controlla_firewall(inv, esito)
    controlla_ceph_dettaglio(inv, esito)
    controlla_storage_dettaglio(inv, esito)
    controlla_notifiche(inv, esito)
    for nome, blocco in inv["nodi"].items():
        controlla_rete_nodo(nome, blocco, inv, esito)
    if not args.solo_nodo:
        for nome, blocco in inv["nodi"].items():
            for ctid, d in (blocco.get("lxc") or {}).items():
                if not args.con_spente and ((d.get("lista") or {}).get("status") or "running") != "running":
                    continue
                controlla_lxc(ctid, d, inv, esito)
    if vms:
        scrittura = args.salva_profili or auto
        scrittura.parent.mkdir(parents=True, exist_ok=True)
        scrittura.write_text(json.dumps({"_versione": VERSIONE_PROFILI, **asseg}, indent=2, ensure_ascii=False), encoding="utf-8")
        print(c(f"Profili VM salvati in {scrittura}", GRIGIO), file=sys.stderr)

    n_vm_cluster = sum(1 for x in (inv.get("cluster", {}).get("resources") or []) if x.get("type") == "qemu")
    intest = {
        "Cluster": cluster_nome or "nessuno (host singolo)",
        "Nodi raccolti": ", ".join(inv["nodi"].keys()),
        "Nodo d'ingresso": inv.get("ingresso"),
        "Versione": (nodo_ingresso(inv).get("status") or {}).get("pveversion", "?"),
        "Rilevato": f"da JSON salvato ({args.da_json})" if getattr(args, "da_json", None) else (f"da remoto via SSH ({HOST_REMOTO})" if HOST_REMOTO else "in locale sul nodo"),
        "Data": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "VM analizzate": (f"{len(vms)}" + (f" su {n_vm_cluster} nel cluster" if n_vm_cluster else "") +
                          (" (solo accese)" if args.solo_accese else "")) if not args.solo_nodo else "0 (--solo-nodo)",
        # Chi legge deve sapere di cosa NON si parla: un report che tace su
        # quattordici macchine escluse mente per omissione.
        "Rilievi su": ("tutte, accese e spente" if args.con_spente else
                       (f"le {len(vms_rilievi)} accese ({len(spente)} spente escluse)" if spente
                        else "tutte (nessuna spenta)")) if not args.solo_nodo else "—",
        "Durata raccolta": f"{time.time() - t0:.0f} s",
    }
    if not multi:
        intest["Nodo"] = inv.get("ingresso")
    intest = {"Cliente": nome_cliente, "Codice cliente": codice_cliente or "—", **intest}
    stampa_report(esito, intest, breve=args.breve)
    cartella = Path(args.output) if args.output else Path.cwd()
    cartella.mkdir(parents=True, exist_ok=True)
    f_inv, f_rep = nomi_file_report(cartella, codice_cliente, nome_cliente, args.host or inv.get("ingresso") or "locale")
    scrivi_inventario_md(f_inv, inv, intest, vms, asseg)
    global ARGOMENTI
    ARGOMENTI = args
    scrivi_rilievi_md(f_rep, esito, inv, intest, vms_rilievi, asseg)
    if getattr(args, "rilievi_json", None):
        Path(args.rilievi_json).write_text(
            json.dumps({"data": intest["Data"], "rilievi": rilievi_json(esito)}, ensure_ascii=False, indent=1),
            encoding="utf-8")
    print(f"\nInventario salvato in {f_inv}\nRilievi salvati in   {f_rep}")
    return {"data": intest["Data"], "b": esito.conta(BLOCCANTE), "a": esito.conta(ATTENZIONE), "i": esito.conta(INFO),
            "output": str(f_rep), "profili": str(auto)}


def invia_al_portale(inv: dict, portale: str, codice: str,
                     cliente: str = "", codice_cliente: str = "") -> bool:
    """Manda la raccolta GREZZA al portale dei clienti, che la archivia e ne
    produce il report da sé.

    Si manda il grezzo e non i due documenti per una ragione precisa: il
    portale rifà l'analisi con le regole aggiornate, e la stessa raccolta
    riletta fra un anno dice quello che sappiamo allora, non quello che
    sapevamo il giorno della verifica. I documenti li genera lui, con questo
    stesso strumento.
    """
    import urllib.error
    import urllib.parse
    import urllib.request
    indirizzo = portale.rstrip("/") + "/api/scansione"
    corpo = json.dumps(inv, ensure_ascii=False).encode()
    print(c(f"Invio della raccolta a {indirizzo} ({len(corpo)/1024:.0f} kB)…", GRIGIO), file=sys.stderr)
    testate = {"Content-Type": "application/json", "X-Codice": codice.strip().upper()}
    # Per chi si sta raccogliendo. Il portale lo usa SOLO se il codice non ha
    # già un cliente suo — con un codice di cliente vince il codice, altrimenti
    # basterebbe dichiararsi qualcun altro per intestargli una verifica.
    # Le intestazioni HTTP sono ASCII: un nome con accenti o «&» si cifra.
    if cliente.strip():
        testate["X-Cliente"] = urllib.parse.quote(cliente.strip())
    if codice_cliente.strip():
        testate["X-Codice-Cliente"] = urllib.parse.quote(codice_cliente.strip())
    req = urllib.request.Request(indirizzo, data=corpo, method="POST", headers=testate)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            esito = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        motivo = {401: "codice non valido o revocato", 413: "raccolta troppo grande",
                  400: "il portale non ha riconosciuto il formato"}.get(e.code, e.reason)
        print(c(f"Invio non riuscito ({e.code}): {motivo}", ROSSO), file=sys.stderr)
        return False
    except OSError as e:
        print(c(f"Invio non riuscito: {e}", ROSSO), file=sys.stderr)
        return False
    if esito.get("errore"):
        print(c(f"Raccolta archiviata, ma il portale non l'ha analizzata: {esito['errore']}", GIALLO), file=sys.stderr)
    else:
        print(c(f"Archiviata: {esito.get('bloccanti', '?')} bloccanti, "
                f"{esito.get('attenzioni', '?')} da valutare.", VERDE), file=sys.stderr)
    if esito.get("pagina"):
        print(f"Il report è consultabile su {esito['pagina']}", file=sys.stderr)
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", metavar="[utente@]host", help="nodo d'ingresso via SSH (default utente root)")
    ap.add_argument("--solo-questo-nodo", action="store_true", help="non estendere la raccolta agli altri nodi del cluster")
    ap.add_argument("--solo-nodo", action="store_true", help="salta le VM")
    ap.add_argument("--solo-accese", action="store_true", help="salta le VM spente (in RACCOLTA: non vengono proprio interrogate)")
    ap.add_argument("--rilievi-json", metavar="FILE", dest="rilievi_json",
                    help="scrive i rilievi anche come dato, per confrontare due verifiche nel tempo")
    ap.add_argument("--prontezza", action="store_true",
                    help="aggiunge la lettura «pronto a ricevere una migrazione?» (manuale 11.4); "
                         "ha senso solo se c'e' una sorgente da cui migrare")
    ap.add_argument("--con-spente", action="store_true",
                    help="rilievi anche sulle VM spente (default: solo sulle accese; l'inventario le elenca sempre)")
    ap.add_argument("--performance", action="store_true", help="esegue anche pveperf (test fsync: scrive un file temporaneo)")
    ap.add_argument("--cliente", metavar="NOME", help="nome del cliente (chiesto all'avvio se manca e c'è un terminale)")
    ap.add_argument("--codice", metavar="CODCLI", help="codice del cliente, se esiste")
    ap.add_argument("--output", type=Path, metavar="CARTELLA",
                    help="cartella dove scrivere <codcli>_<cliente>_<ip>_inventory.md e _report.md (default: cartella corrente)")
    ap.add_argument("--json", type=Path, help="salva i dati grezzi raccolti in JSON")
    ap.add_argument("--da-json", type=Path, help="non raccoglie: analizza un JSON salvato con --json")
    ap.add_argument("--invia", metavar="URL", help="manda la raccolta al portale clienti "
                    "(es. https://survey.domarc.it/proxmox), che la archivia e ne pubblica il report")
    # NON «--codice»: quello esiste già ed è il codice CLIENTE che finisce nel
    # nome dei file. Due opzioni con lo stesso nome fanno fallire argparse
    # all'avvio, e sarebbe un guasto scoperto dal tecnico davanti al cluster.
    ap.add_argument("--codice-portale", metavar="PXM-…", dest="codice_portale",
                    help="il codice di accesso al portale, per --invia")
    ap.add_argument("--profili", type=Path, help="file JSON {vmid: profilo} esplicito (default: automatico per cluster/host)")
    ap.add_argument("--salva-profili", type=Path, help="dove salvare le classificazioni (default: automatico)")
    ap.add_argument("--senza-domande", action="store_true", dest="senza_domande",
                    help="non chiede le tipologie a terminale: usa quelle salvate e, per le altre, "
                         "la proposta dal nome. Implicito con --invia, dove le tipologie le gestisce il portale")
    ap.add_argument("--breve", action="store_true", help="a terminale solo riepilogo e bloccanti")
    ap.add_argument("--max-vm", type=int, default=0, help="limita il numero di VM per nodo (prove)")
    ap.add_argument("--menu", action="store_true", help="apre il menu interattivo")
    ap.add_argument("--no-color", action="store_true", help="senza colori ANSI")
    args = ap.parse_args()
    global USA_COLORI
    if args.no_color:
        USA_COLORI = False
    if args.menu or (not args.host and not args.da_json and sys.stdin.isatty() and shutil.which("pvesh") is None):
        menu_principale(); return
    if not args.host and not args.da_json and shutil.which("pvesh") is None:
        print("Questa macchina non è un nodo Proxmox (manca pvesh): indicare il nodo con --host, o aprire il menu con --menu.", file=sys.stderr)
        sys.exit(2)
    esegui(args)


if __name__ == "__main__":
    try:
        main()
    except (EOFError, KeyboardInterrupt):
        print("\nInterrotto.")
        sys.exit(130)
