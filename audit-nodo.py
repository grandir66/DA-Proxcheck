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
  2. In locale: tabella delle VM (tutto il cluster) con assegnazione dei
     profili di carico, confronto con le regole, report.

SOLO LETTURA: pvesh get, smartctl -H/-A, cat, ip, lvs, zpool, timedatectl,
ping fra gli anelli corosync; con --performance anche pveperf (scrive e
rimuove un file temporaneo per il test fsync).

FONTI DELLE SOGLIE (citate in ogni rilievo)
    guida-configurazione-vm-proxmox-per-carico.md   parametri e profili VM
    manuale/02 installare · 03 cluster · 04-06 storage · 12 backup · App. A
    Proxreporter hardware_monitor.py                SMART, ECC, RAID (soglie del
                                                    tool Domarc in produzione)
    pveperf / forum Proxmox                         fsync/s: valori indicativi
Dove nessuna fonte prescrive un valore giusto in assoluto, il dato è
riportato come informazione, non come rilievo.
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

VERSIONE_SCRIPT = "2.0"
WINDOWS = os.name == "nt"

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


@dataclass
class Esito:
    rilievi: list = field(default_factory=list)

    def add(self, livello, ambito, messaggio, fonte=""):
        self.rilievi.append(Rilievo(livello, ambito, messaggio, fonte))

    def conta(self, livello):
        return sum(1 for r in self.rilievi if r.livello == livello)


# ────────────────────────────── profili di carico (guida-carico Parte 3) ──────────────────────────────

PROFILI = {
    "1": {"nome": "Domain Controller (Active Directory)", "vcpu_max": 4, "balloon": "min_eq_max",
          "cpu_type_evita": {"kvm64"}, "numa": False, "cache": "none", "multiqueue": "no",
          "protection": True, "ha": True, "fonte": "guida-carico §2.1",
          "extra": ["Almeno un secondo domain controller su un altro nodo fisico.",
                    "Mai il rollback di snapshot come ripristino (USN rollback)."]},
    "2": {"nome": "File server", "vcpu_max": 4, "balloon": "attivo_min_alto", "cpu_type_evita": {"kvm64"},
          "numa": False, "cache": "none", "multiqueue": "molti_client", "protection": True,
          "dischi_min": 2, "fonte": "guida-carico §2.2",
          "extra": ["Disco dati separato dal disco di sistema (backup e throttling distinti)."]},
    "3": {"nome": "Application server / web server", "vcpu_max": 8, "balloon": "attivo_salvo_java",
          "cpu_type_evita": {"kvm64"}, "numa": False, "cache": "none", "multiqueue": "se_reverse_proxy",
          "protection": False, "fonte": "guida-carico §2.3"},
    "4": {"nome": "Database server", "vcpu_max": None, "balloon": "disabilitato",
          "cpu_type_evita": {"kvm64", "x86-64-v2-AES"}, "numa": True, "cache": "none", "multiqueue": "no",
          "protection": True, "ha": True, "dischi_min": 2, "fonte": "guida-carico §2.4",
          "extra": ["Dischi dati e log/WAL su dischi virtuali separati.",
                    "aio=native SOLO con raw + cache=none + iothread; altrimenti io_uring."]},
    "5": {"nome": "Appliance di rete / firewall virtuale", "vcpu_max": None, "balloon": "disabilitato",
          "cpu_type_evita": set(), "cpu_type_richiede": "host", "numa": False, "cache": "none",
          "multiqueue": "tutte_le_nic", "protection": True, "fonte": "guida-carico §2.5",
          "extra": ["Firewall PVE per-interfaccia off se il filtraggio avviene nell'appliance."]},
    "6": {"nome": "Log server / SIEM / raccolta eventi", "vcpu_max": None, "balloon": "dipende_jvm",
          "cpu_type_evita": {"kvm64"}, "numa": False, "cache": "none", "multiqueue": "se_molte_sorgenti",
          "protection": False, "dischi_min": 2, "fonte": "guida-carico §2.6",
          "extra": ["Valutare un limite di throughput (mbps_wr) sul disco dati: vicino rumoroso tipico."]},
    "7": {"nome": "Terminal server / VDI", "vcpu_max": None, "balloon": "attivo", "cpu_type_evita": set(),
          "cpu_type_richiede": "host", "numa": None, "cache": "none", "multiqueue": "no",
          "protection": False, "fonte": "guida-carico §2.7"},
    "8": {"nome": "Monitoraggio (Zabbix/Prometheus/Grafana)", "vcpu_max": None, "balloon": "dipende_jvm",
          "cpu_type_evita": {"kvm64"}, "numa": False, "cache": "none", "multiqueue": "no",
          "protection": False, "fonte": "guida-carico §2.8"},
    "9": {"nome": "Sistema legacy / appliance senza VirtIO", "vcpu_max": 2, "balloon": "disabilitato",
          "cpu_type_evita": set(), "numa": False, "cache": None, "multiqueue": "no", "protection": False,
          "legacy": True, "fonte": "guida-carico §2.9",
          "extra": ["Isolare a livello di rete: debito tecnico con una data di scadenza."]},
    "10": {"nome": "Replica (VM di replica/DR)", "vcpu_max": None, "cpu_type_evita": {"kvm64"},
           "numa": False, "cache": "none", "multiqueue": "no", "protection": True, "fonte": "manuale §4.5",
           "extra": ["Non deve avere onboot insieme alla primaria (conflitto IP/hostname).",
                     "Verificare che la schedulazione di replica (pvesr) esista e l'RPO sia quello concordato."]},
    "11": {"nome": "Test / sviluppo", "vcpu_max": None, "cpu_type_evita": set(), "numa": False,
           "cache": "none", "multiqueue": "no", "protection": False, "onboot_no": True,
           "fonte": "guida-carico §1.6",
           "extra": ["Non condividere rete/VLAN con la produzione se il test comporta traffico non filtrato."]},
    "12": {"nome": "Backup server (PBS o Veeam)", "vcpu_max": None, "cpu_type_evita": {"kvm64"},
           "numa": False, "cache": "none", "multiqueue": "no", "protection": True, "fonte": "manuale §12",
           "extra": ["PBS: datastore su storage con checksum (ZFS). Veeam: serve uno storage file-level nel cluster.",
                     "Valutare backup=0 sul disco del datastore/repository."]},
    "13": {"nome": "Proxy / reverse proxy / load balancer", "vcpu_max": 4, "cpu_type_evita": {"kvm64"},
           "numa": False, "cache": "none", "multiqueue": "tutte_le_nic", "protection": True,
           "fonte": "guida-carico §2.3 (reverse proxy)",
           "extra": ["Se il filtraggio avviene nel proxy, valutare il firewall PVE off sull'interfaccia."]},
}
NON_CLASSIFICATA = "0"


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
                          "un anello su un bond è un unico dominio di guasto per configurazione e per switch.", "manuale §5.2")
            if l["id"] != 0 and l["addr"].rsplit(".", 1)[0] in mgmt_subnets:
                esito.add(INFO, B, f"Anello {l['id']} ({l['addr']}) sulla rete di management: ok come secondario.", "manuale §5.1")
        ids = list(percorsi)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                comuni = percorsi[ids[i]] & percorsi[ids[j]]
                if comuni:
                    esito.add(ATTENZIONE, B, f"Gli anelli {ids[i]} e {ids[j]} passano dalle stesse NIC fisiche "
                              f"({', '.join(sorted(comuni))}): ridondanza solo logica.", "manuale §3.3, §5.2")
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
        esito.add(BLOCCANTE, A, f"{len(ha_res)} risorse HA su {n_nodi} nodi senza QDevice.", "manuale §7.1")
    if not ha_res and n_nodi >= 3:
        esito.add(INFO, A, "Nessuna risorsa in HA: con ≥3 nodi l'alta affidabilità è disponibile ma non usata.", "manuale §7")
    for r in (cl.get("ha_status") or []):
        if r.get("type") == "service" and str(r.get("state", "")).startswith("error"):
            esito.add(BLOCCANTE, A, f"Risorsa HA {r.get('sid')} in stato {r.get('state')}.", "manuale §7.7")

    jobs = cl.get("backup") or []
    if not jobs:
        esito.add(BLOCCANTE, A, "Nessun job di backup configurato nel cluster.", "manuale §12")
    for j in jobs:
        if not j.get("enabled", 1):
            esito.add(ATTENZIONE, A, f"Job di backup {j.get('id')} disabilitato.", "manuale §12")
        if not (j.get("fleecing") or {}).get("enabled"):
            esito.add(INFO, A, f"Job {j.get('id')} senza fleecing: durante il backup le scritture dei guest aspettano il target.", "manuale §12.7")
        if (j.get("prune-backups") or {}).get("keep-all"):
            esito.add(INFO, A, f"Job {j.get('id')}: keep-all, nessuna retention.", "manuale §12")
    nbu = cl.get("not_backed_up") or []
    if nbu:
        esito.add(ATTENZIONE, A, f"{len(nbu)} guest senza alcun job di backup: " +
                  ", ".join(f"{x.get('vmid')} {x.get('name', '')}".strip() for x in nbu[:12]) + (" …" if len(nbu) > 12 else "") + ".", "manuale §12")
    for r in (cl.get("replication") or []):
        if r.get("error"):
            esito.add(BLOCCANTE, A, f"Replica {r.get('id')}: errore — {str(r.get('error'))[:120]}", "manuale §4.5")
    ceph = cl.get("ceph")
    if isinstance(ceph, dict) and (ceph.get("health") or {}).get("status") not in (None, "HEALTH_OK"):
        h = ceph["health"]["status"]
        esito.add(BLOCCANTE if h == "HEALTH_ERR" else ATTENZIONE, A, f"Ceph: {h}.", "manuale §6.7")


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
        esito.add(ATTENZIONE, A, f"{len(upd)} aggiornamenti disponibili non installati" + (f" (tra cui {', '.join(imp[:5])})" if imp else "") + ".", "manuale §19")
    running = ((st.get("current-kernel") or {}).get("release") or "")
    ker = sorted(re.findall(r"vmlinuz-(\S+)", nodo.get("kernel_boot") or ""))
    if running and ker and running in ker and ker[-1] != running:
        esito.add(ATTENZIONE, A, f"Kernel installato più recente ({ker[-1]}) di quello in esecuzione ({running}): riavvio pendente.", "manuale §19")
    srv = {s.get("name"): s for s in (nodo.get("services") or [])}
    for n in list(SERVIZI_CORE) + (["corosync"] if cluster else []):
        s = srv.get(n)
        if s and (s.get("state") != "running" or s.get("unit-state") not in ("enabled", "static", "indirect")):
            esito.add(BLOCCANTE, A, f"Servizio {n}: {s.get('state')}/{s.get('unit-state')}.", "manuale §20")
    for ce in (nodo.get("certificati") or []):
        if ce.get("notafter"):
            gg = (ce["notafter"] - time.time()) / 86400
            if gg < 0:
                esito.add(BLOCCANTE, A, f"Certificato {ce.get('filename')} SCADUTO.", "manuale §13")
            elif gg < 30:
                esito.add(ATTENZIONE, A, f"Certificato {ce.get('filename')} scade tra {gg:.0f} giorni.", "manuale §13")
    mem, sw, rf = st.get("memory") or {}, st.get("swap") or {}, st.get("rootfs") or {}
    p = pct(mem.get("used"), mem.get("total"))
    if p is not None and p > 90:
        esito.add(ATTENZIONE, A, f"RAM host al {p:.0f}%: poco margine per ballooning, ARC e picchi.", "guida-carico §1.2")
    p = pct(sw.get("used"), sw.get("total"))
    if p is not None and p > 25:
        esito.add(ATTENZIONE, A, f"Swap dell'host al {p:.0f}%: l'hypervisor pagina, ne risentono tutte le VM.", "guida-carico §1.2")
    p = pct(rf.get("used"), rf.get("total"))
    if p is not None and p >= 85:
        esito.add(BLOCCANTE if p >= 95 else ATTENZIONE, A, f"Filesystem di root al {p:.0f}%.", "manuale §4.6")
    if (st.get("uptime") or 0) > 365 * 86400:
        esito.add(INFO, A, f"Uptime {durata(st.get('uptime'))}: oltre un anno senza riavvio.", "manuale §19")
    cpus = (st.get("cpuinfo") or {}).get("cpus")
    vms = [v for v in costruisci_vms(inv) if v.nodo == nome and v.running]
    vcpu_tot = sum(vcpu_di(v.config) for v in vms)
    mem_tot = sum(int(v.config.get("memory", 0) or 0) for v in vms) * 1024**2
    if cpus and vcpu_tot:
        r = vcpu_tot / cpus
        esito.add(ATTENZIONE if r > 4 else INFO, A, f"vCPU delle VM accese: {vcpu_tot} su {cpus} CPU logiche ({r:.1f}:1; tipico 3-4:1, 1:1 per database e appliance).", "guida-carico §1.1")
    if mem.get("total") and mem_tot > mem["total"]:
        esito.add(ATTENZIONE, A, f"RAM assegnata alle VM accese ({gb(mem_tot)}) supera quella dell'host ({gb(mem['total'])}): overcommit reale.", "guida-carico §1.2")
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
                esito.add(BLOCCANTE if mp >= 95 else ATTENZIONE, f"{A} — LVM-thin {f[1]}/{f[0]}", f"Metadati al {mp:.1f}%: il pool diventa di sola lettura quando finiscono.", "manuale App. A.8")
    if (nodo.get("multipath") or "").strip():
        esito.add(INFO, f"{A} — multipath", "multipath attivo: i PV vanno su /dev/mapper/<WWID>, mai su /dev/sdX.", "manuale App. A.8")
    td = nodo.get("timedatectl") or ""
    if re.search(r"System clock synchronized:\s*no", td):
        esito.add(BLOCCANTE, f"{A} — orario", "Orologio NON sincronizzato: rompe il cluster e Kerberos.", "manuale §2.1")
    elif re.search(r"NTP service:\s*inactive", td):
        esito.add(ATTENZIONE, f"{A} — orario", "Servizio NTP inattivo.", "manuale §2.1")
    for bond, testo in (nodo.get("bonding") or {}).items():
        slave = re.findall(r"Slave Interface:\s*(\S+)\nMII Status:\s*(\S+)", testo)
        giu = [s for s, stt in slave if stt != "up"]
        if giu:
            esito.add(BLOCCANTE, f"{A} — rete {bond}", f"Slave {', '.join(giu)} DOWN: bond degradato.", "manuale §5")
        if len(slave) == 1:
            esito.add(ATTENZIONE, f"{A} — rete {bond}", f"Bond con un solo slave ({slave[0][0]}): nessuna ridondanza, il bond è solo nominale.", "manuale §5.2")
    fw = re.search(r"Status:\s*(\S+)", nodo.get("pve_firewall") or "")
    if fw:
        esito.add(INFO, f"{A} — firewall PVE", f"Stato {fw.group(1)}: va attivo se il filtraggio non avviene altrove.", "manuale §15")
    bi = st.get("boot-info") or {}
    if bi:
        esito.add(INFO, A, f"Boot {str(bi.get('mode', '?')).upper()}, Secure Boot {'attivo' if bi.get('secureboot') else 'disattivo'}.", "manuale §2.2")
    if (st.get("ksm") or {}).get("shared"):
        esito.add(INFO, A, f"KSM attivo: {gb(st['ksm']['shared'])} di pagine condivise.", "guida-carico §1.2")


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
    F = "rrddata / guida-carico §1"
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
            esito.add(ATTENZIONE if f < 200 else INFO, B, f"FSYNC/s {f:.0f} — {giud} (indicativo: <200 scarso · 200-1000 accettabile · >1000 buono).", "pveperf / forum Proxmox")
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


def controlla_generali(vm: VM, inv: dict, esito: Esito):
    cfg, st = vm.config, vm.status or {}
    A = ambito_vm(vm, inv)
    G = "guida-carico"
    host_cpu = (((inv.get("nodi") or {}).get(vm.nodo) or {}).get("nodo") or {}).get("status", {}).get("cpuinfo") or {}
    cpu_tipo = (cfg.get("cpu") or "kvm64").split(",")[0]
    if cpu_tipo == "kvm64":
        esito.add(ATTENZIONE, A, "CPU type kvm64 (default): set di istruzioni minimo. Valutare almeno x86-64-v2-AES.", f"{G} §1.1")
    if cfg.get("sockets", "1") not in ("", "1") and cfg.get("numa", "0") != "1":
        esito.add(ATTENZIONE, A, f"{cfg['sockets']} socket senza NUMA: la regola è 1 socket, N core.", f"{G} §1.1")
    if cfg.get("cpulimit") not in (None, "", "0"):
        esito.add(INFO, A, f"cpulimit={cfg['cpulimit']}: tetto assoluto di CPU.", f"{G} §1.1")
    if stato_ballooning(cfg) == "assente":
        esito.add(ATTENZIONE, A, "balloon: 0 — driver assente E reporting RAM perso (sempre 100% in GUI). Per RAM fissa con statistiche: Minimum memory = Memory.", f"{G} §1.2")
    if cfg.get("hugepages") not in (None, ""):
        esito.add(INFO, A, f"hugepages={cfg['hugepages']}.", f"{G} §1.2")
    ha_scsi = False
    for d in dischi_dati(cfg):
        bus, cache, aio, ioth, disc = d["bus"], d.get("cache", ""), d.get("aio", ""), d.get("iothread", "0"), d.get("discard", "")
        ha_scsi |= bus.startswith("scsi")
        if bus.startswith(("sata", "ide")):
            esito.add(ATTENZIONE, A, f"Disco {bus}: bus SATA/IDE — solo per compatibilità o migrazione; il riferimento è SCSI + VirtIO SCSI single.", f"{G} §1.3")
        if cache in ("writeback", "unsafe"):
            esito.add(BLOCCANTE if cache == "unsafe" else ATTENZIONE, A, f"Disco {bus}: cache={cache} — un crash dell'host può corrompere i dati recenti senza UPS/BBU.", f"{G} §1.3")
        if aio == "native" and (cache != "none" or ioth in ("0", "")):
            esito.add(BLOCCANTE, A, f"Disco {bus}: aio=native senza cache=none+iothread=1 — l'I/O può bloccarsi.", f"{G} §1.3, §7.4")
        if disc not in ("on", "1") and bus.startswith(("scsi", "virtio")):
            esito.add(ATTENZIONE, A, f"Disco {bus}: discard non attivo — lo spazio liberato nel guest non torna allo storage thin.", f"{G} §1.3")
        if bus.startswith("scsi") and ioth in ("0", ""):
            esito.add(ATTENZIONE, A, f"Disco {bus}: iothread non attivo.", f"{G} §1.3")
    scsihw = cfg.get("scsihw", "")
    if ha_scsi and scsihw and scsihw != "virtio-scsi-single":
        esito.add(ATTENZIONE, A, f"scsihw={scsihw}: il riferimento è virtio-scsi-single (presupposto per gli IO thread).", f"{G} §1.3")
    vcpu = vcpu_di(cfg)
    for r in parse_reti(cfg):
        mq = r.get("queues")
        if mq and mq.isdigit() and int(mq) > vcpu:
            esito.add(ATTENZIONE, A, f"{r['iface']}: multiqueue={mq} > {vcpu} vCPU.", f"{G} §1.4")
        if r.get("modello") and not r["modello"].startswith("virtio"):
            esito.add(ATTENZIONE, A, f"{r['iface']}: modello '{r['modello']}', non VirtIO — solo per sistemi legacy.", f"{G} §1.4")
    agent = cfg.get("agent", "")
    if not agent or agent.startswith("0"):
        esito.add(ATTENZIONE, A, "QEMU Guest Agent non attivo: niente spegnimento pulito, freeze del filesystem nei backup, IP in GUI.", f"{G} §1.5")
    elif vm.running and not st.get("agent"):
        esito.add(ATTENZIONE, A, "Agent abilitato ma NON risponde nel guest: i backup non fanno il freeze del filesystem.", f"{G} §1.5")
    ostype = cfg.get("ostype", "")
    if ostype.startswith("win") and host_cpu.get("vendor") == "GenuineIntel" and cpu_tipo == "host":
        rm = st.get("running-machine") or cfg.get("machine", "")
        if re.search(r"11\.0", rm or "") and "pve2" not in rm:
            esito.add(ATTENZIONE, A, f"Windows con CPU host su Intel e machine {rm}: blocchi intermittenti noti con VBS (Bugzilla #7825) — serve 11.0+pve2.", f"{G} problema noto 9.2")
    if ostype in ("win11", "win10") and cfg.get("bios", "seabios") != "ovmf":
        esito.add(INFO, A, "Windows recente con SeaBIOS: OVMF (UEFI) è il riferimento.", f"{G} §1.5")
    if cfg.get("protection", "0") != "1" and cfg.get("onboot", "0") == "1":
        esito.add(INFO, A, "protection non attiva su una VM ad avvio automatico.", f"{G} §1.6")
    for s in vm.snapshot:
        eta = (time.time() - s.get("snaptime", time.time())) / 86400
        if eta > 30:
            esito.add(ATTENZIONE, A, f"Snapshot '{s.get('name')}' di {eta:.0f} giorni: non è un backup, e degrada le prestazioni finché resta.", f"{G} §1.3, manuale §12")
    if vm.pending:
        esito.add(INFO, A, f"{len(vm.pending)} modifiche in attesa di riavvio: " + ", ".join(str(p.get('key')) for p in vm.pending[:6]) + ".", "manuale §8")
    if vm.running:
        cpu_avg = media(vm.rrd, "cpu")
        if cpu_avg is not None and cpu_avg > 0.8:
            esito.add(ATTENZIONE, A, f"CPU media nell'ultima ora {cpu_avg*100:.0f}% su {vcpu} vCPU: sottodimensionata o in loop.", f"{G} §1.1")
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
                esito.add(INFO, A, f"Orologio del guest a {drift/3600:+.0f} h esatte dall'host: quasi certamente l'agent riporta l'ora locale, non un orologio sbagliato.", "manuale §2.1")
            elif abs(drift) > 30:
                esito.add(ATTENZIONE, A, f"Orologio del guest sfasato di {drift:+.0f} s rispetto all'host.", "manuale §2.1")
        oi = ag.get("osinfo") if isinstance(ag.get("osinfo"), dict) else {}
        if oi.get("id") and ostype:
            if ostype.startswith("win") != ("windows" in (oi.get("id", "") + oi.get("name", "")).lower()):
                esito.add(INFO, A, f"ostype '{ostype}' ma il guest è {oi.get('pretty-name') or oi.get('name')}.", f"{G} §1.5")
        for it in (ag.get("interfacce") if isinstance(ag.get("interfacce"), list) else []):
            stt = it.get("statistics") or {} if isinstance(it, dict) else {}
            if stt.get("rx-errs") or stt.get("tx-errs"):
                esito.add(INFO, A, f"{it.get('name')} nel guest: errori rx/tx {stt.get('rx-errs', 0)}/{stt.get('tx-errs', 0)}.", "guest agent")
    elif cfg.get("onboot") == "1":
        esito.add(INFO, A, "Spenta ma con onboot=1: ripartirà al prossimo riavvio del nodo.", f"{G} §1.6")
    nbu = {str(x.get("vmid")) for x in (inv.get("cluster", {}).get("not_backed_up") or [])}
    if vm.vmid in nbu:
        esito.add(ATTENZIONE, A, "Non inclusa in nessun job di backup.", "manuale §12")


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
        esito.add(BLOCCANTE, A, f"CPU type '{cpu_tipo}' sconsigliata per questo profilo.", F)
    if p.get("cpu_type_richiede") and cpu_tipo != p["cpu_type_richiede"]:
        esito.add(ATTENZIONE, A, f"Raccomandato CPU type '{p['cpu_type_richiede']}'; rilevato '{cpu_tipo}'.", F)
    sb = stato_ballooning(cfg)
    if p.get("balloon") == "disabilitato" and sb != "assente":
        esito.add(BLOCCANTE, A, "Ballooning da disattivare (balloon: 0) per questo carico.", F)
    if p.get("balloon") == "min_eq_max" and sb != "presente_fermo":
        esito.add(ATTENZIONE, A, "RAM fissa (Minimum memory = Memory) mantenendo le statistiche.", F)
    if p.get("numa") is True and cfg.get("numa", "0") != "1":
        esito.add(ATTENZIONE, A, "NUMA non attivo: raccomandato per questo profilo.", F)
    if p.get("protection") and cfg.get("protection", "0") != "1":
        esito.add(ATTENZIONE, A, "protection non attiva: raccomandata per la criticità del servizio.", F)
    if p.get("onboot_no") and cfg.get("onboot") == "1":
        esito.add(ATTENZIONE, A, "onboot attivo su una VM di test.", F)
    if p.get("dischi_min") and len(dischi_dati(cfg)) < p["dischi_min"]:
        esito.add(ATTENZIONE, A, "Un solo disco: il profilo prevede sistema e dati separati.", F)
    if p.get("cache") == "none":
        for d in dischi_dati(cfg):
            if d.get("cache", "none") not in ("none", ""):
                esito.add(BLOCCANTE, A, f"Disco {d['bus']}: cache={d['cache']}, il profilo richiede none.", F)
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
        esito.add(INFO, A, "protection non attiva su un container ad avvio automatico.", "manuale §10")


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
        prof = "—" if pid == NON_CLASSIFICATA else PROFILI.get(pid, {}).get("nome", f"id '{pid}'?")
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
    print(f"\n── VM {vm.vmid} — {vm.nome} " + "─" * max(1, 40 - len(vm.nome)))
    for k, v in PROFILI.items():
        print(f"  {k:>2}) {v['nome']}")
    print(f"   {NON_CLASSIFICATA}) Non classificare")
    while True:
        s = input("  Tipologia: ").strip()
        if s in PROFILI or s == NON_CLASSIFICATA:
            return s
        print("  Valore non valido.")


def assegna_profili_da_tabella(vms: list, noti: dict, multi: bool) -> dict:
    asseg = dict(noti)
    ids = {v.vmid for v in vms}
    while True:
        stampa_tabella_vm(vms, asseg, multi)
        print("\nVMID da classificare · 't' tutte quelle senza profilo · INVIO per procedere")
        s = input("Scelta: ").strip()
        if s == "":
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


# ────────────────────────────── report ──────────────────────────────

ORDINE_CATEGORIE = ["Cluster / corosync", "Nodo", "Hardware", "Storage", "Rete", "Performance",
                    "VM — profilo di carico", "VM — parametri", "Container"]


def categoria_di(ambito: str) -> str:
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
        r += ["### Da risolvere prima di tutto", ""] + [f"- 🔴 **{x.ambito}** — {x.messaggio} *[{x.fonte}]*" for x in bl] + [""]
    r += ["## Legenda", "", "- 🔴 **Bloccante**: rischio concreto di fermo, perdita dati o mancato aggiornamento; da trattare per primo.",
          "- 🟡 **Da valutare**: scostamento dalla best practice con un costo reale (prestazioni, ripristino, sicurezza).",
          "- ℹ️ **Informativo**: dato utile a chi decide, nessuna azione obbligata.",
          "- La colonna *Fonte* cita il documento e il paragrafo da cui viene la regola.", ""]
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
    r += _piede_md()
    path.write_text("\n".join(r), encoding="utf-8")


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
            noti = json.loads(percorso.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"ATTENZIONE: {percorso} non è un JSON leggibile.", file=sys.stderr)
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
                percorso.write_text(json.dumps(noti, indent=2, ensure_ascii=False), encoding="utf-8")
                print("  Salvato.")
        elif s == "r":
            vmid = input("  VMID da rimuovere: ").strip()
            if vmid in noti:
                del noti[vmid]
                percorso.write_text(json.dumps(noti, indent=2, ensure_ascii=False), encoding="utf-8")
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
        inv = json.loads(Path(args.da_json).read_text(encoding="utf-8"))
    else:
        inv = raccogli(solo_accese=args.solo_accese, performance=args.performance,
                       tutto_cluster=not getattr(args, "solo_questo_nodo", False), max_vm=getattr(args, "max_vm", 0))
    if not inv or not inv.get("nodi"):
        print("Nessun dato raccolto: connessione fallita, o la destinazione non è un nodo Proxmox VE con python3 e pvesh.", file=sys.stderr)
        return None
    if args.json:
        Path(args.json).write_text(json.dumps(inv, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"Dati grezzi salvati in {args.json}", file=sys.stderr)

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

    chiave = cluster_nome or HOST_REMOTO or inv.get("ingresso") or "host"
    auto = percorso_profili_default(chiave)
    lettura = args.profili or auto
    noti = {}
    if lettura.is_file():
        try:
            noti = json.loads(lettura.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            noti = {}
        if noti:
            print(c(f"Profili già noti ({len(noti)} VM), da {lettura}", GRIGIO), file=sys.stderr)

    vms = costruisci_vms(inv) if not args.solo_nodo else []
    asseg = dict(noti)
    if vms and sys.stdin.isatty():
        asseg = assegna_profili_da_tabella(vms, noti, multi)
    for v in vms:
        asseg.setdefault(v.vmid, NON_CLASSIFICATA)
        controlla_generali(v, inv, esito)
        controlla_profilo(v, asseg.get(v.vmid, NON_CLASSIFICATA), inv, esito)
    if not args.solo_nodo:
        for nome, blocco in inv["nodi"].items():
            for ctid, d in (blocco.get("lxc") or {}).items():
                controlla_lxc(ctid, d, inv, esito)
    if vms:
        scrittura = args.salva_profili or auto
        scrittura.parent.mkdir(parents=True, exist_ok=True)
        scrittura.write_text(json.dumps(asseg, indent=2, ensure_ascii=False), encoding="utf-8")
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
    scrivi_rilievi_md(f_rep, esito, inv, intest, vms, asseg)
    print(f"\nInventario salvato in {f_inv}\nRilievi salvati in   {f_rep}")
    return {"data": intest["Data"], "b": esito.conta(BLOCCANTE), "a": esito.conta(ATTENZIONE), "i": esito.conta(INFO),
            "output": str(f_rep), "profili": str(auto)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", metavar="[utente@]host", help="nodo d'ingresso via SSH (default utente root)")
    ap.add_argument("--solo-questo-nodo", action="store_true", help="non estendere la raccolta agli altri nodi del cluster")
    ap.add_argument("--solo-nodo", action="store_true", help="salta le VM")
    ap.add_argument("--solo-accese", action="store_true", help="salta le VM spente")
    ap.add_argument("--performance", action="store_true", help="esegue anche pveperf (test fsync: scrive un file temporaneo)")
    ap.add_argument("--cliente", metavar="NOME", help="nome del cliente (chiesto all'avvio se manca e c'è un terminale)")
    ap.add_argument("--codice", metavar="CODCLI", help="codice del cliente, se esiste")
    ap.add_argument("--output", type=Path, metavar="CARTELLA",
                    help="cartella dove scrivere <codcli>_<cliente>_<ip>_inventory.md e _report.md (default: cartella corrente)")
    ap.add_argument("--json", type=Path, help="salva i dati grezzi raccolti in JSON")
    ap.add_argument("--da-json", type=Path, help="non raccoglie: analizza un JSON salvato con --json")
    ap.add_argument("--profili", type=Path, help="file JSON {vmid: profilo} esplicito (default: automatico per cluster/host)")
    ap.add_argument("--salva-profili", type=Path, help="dove salvare le classificazioni (default: automatico)")
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
