"""I formati verificati su nodi reali (PVE 9.2, 2026-09-03/04). Ogni test è un
formato che, immaginato invece che letto, aveva già prodotto un parser
sbagliato: se uno di questi cambia, deve fallire il cancello, non il tecnico."""
import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("audit_nodo", Path(__file__).resolve().parents[1] / "audit-nodo.py")
an = importlib.util.module_from_spec(spec)
sys.modules["audit_nodo"] = an
spec.loader.exec_module(an)

SMART_ATA = """=== START OF READ SMART DATA SECTION ===
SMART overall-health self-assessment test result: PASSED

ID# ATTRIBUTE_NAME          FLAG     VALUE WORST THRESH TYPE      UPDATED  WHEN_FAILED RAW_VALUE
  5 Reallocated_Sector_Ct   0x0033   100   100   010    Pre-fail  Always       -       0
  9 Power_On_Hours          0x0032   076   076   000    Old_age   Always       -       21357
190 Airflow_Temperature_Cel 0x0022   060   050   040    Old_age   Always       -       40
197 Current_Pending_Sector  0x0012   100   100   000    Old_age   Always       -       0
199 UDMA_CRC_Error_Count    0x003e   200   200   000    Old_age   Always       -       3
"""

SMART_NVME = """SMART overall-health self-assessment test result: PASSED
Critical Warning:                   0x00
Temperature:                        65 Celsius
Available Spare:                    100%
Percentage Used:                    8%
Power On Hours:                     12,985
Media and Data Integrity Errors:    0
"""


def test_smart_ata_non_confonde_when_failed_con_un_guasto():
    """La colonna WHEN_FAILED contiene 'FAILED' su ogni disco sano (bug 2026-09-04)."""
    r = an.parse_smart(SMART_ATA, "/dev/sda")
    assert r["salute"] == "PASSED"
    assert r["ata_reallocated"] == 0 and r["ata_pending"] == 0
    assert r["ata_crc"] == 3 and r["power_on_hours"] == 21357 and r["temperatura"] == 40


def test_smart_nvme_formato_chiave_valore():
    r = an.parse_smart(SMART_NVME, "/dev/nvme0n1")
    assert r["nvme_critical_warning"] == "0x00" and r["nvme_percentage_used"] == 8
    assert r["power_on_hours"] == 12985 and r["temperatura"] == 65


def test_nvme_caldo_ma_sano_non_e_bloccante():
    """65 °C con Critical Warning 0x00: informativo, non bloccante (verificato su disco reale)."""
    inv_nodo = {"nodo": {"disks": [{"devpath": "/dev/nvme0n1", "model": "X", "health": "PASSED", "wearout": 92}],
                         "smart": {"/dev/nvme0n1": SMART_NVME}}}
    e = an.Esito()
    an.controlla_hardware("n1", inv_nodo, e)
    assert e.conta(an.BLOCCANTE) == 0


CFGTOOL = """Local node ID 2, transport knet
LINK ID 0 udp
	addr	= 192.168.16.1
	status:
		nodeid:          1:	connected
		nodeid:          2:	localhost
		nodeid:          3:	connected
LINK ID 1 udp
	addr	= 192.168.40.1
	status:
		nodeid:          1:	connected
		nodeid:          2:	localhost
		nodeid:          3:	disconnected
"""


def test_cfgtool_multiriga_due_anelli():
    """Il formato è multiriga: una regex su una riga sola contava sempre 0 anelli (2026-09-03)."""
    link = an.parse_cfgtool(CFGTOOL)
    assert [l["addr"] for l in link] == ["192.168.16.1", "192.168.40.1"]
    assert link[1]["nodi"]["3"] == "disconnected"


PVEPERF_ZFS = """CPU BOGOMIPS:      121369.44
REGEX/SECOND:      7314912
HD SIZE:           10885.91 GB (ZFS-LARGE)
FSYNCS/SECOND:     96.60
DNS EXT:           28.81 ms
DNS INT:           1001.23 ms (domarc.it)
"""


def test_pveperf_su_zfs_non_inventa_letture():
    p = an.parse_pveperf(PVEPERF_ZFS)
    assert p["fsync_s"] == 96.6 and p["dns_int_ms"] == 1001.23
    assert "read_mbs" not in p and "seek_ms" not in p


def test_parse_reti_modello_e_la_chiave_del_mac():
    reti = an.parse_reti({"net0": "vmxnet3=DE:AD:BE:EF:00:01,bridge=vmbr0,tag=4,queues=4"})
    assert reti[0]["modello"] == "vmxnet3" and reti[0]["bridge"] == "vmbr0" and reti[0]["queues"] == "4"


def test_nomi_file_codice_cliente_ip():
    inv, rep = an.nomi_file_report(Path("/tmp"), "C0123", "Rossi Srl", "root@192.168.40.1")
    assert inv.name == "C0123_Rossi-Srl_192.168.40.1_inventory.md"
    assert rep.name == "C0123_Rossi-Srl_192.168.40.1_report.md"
    inv, _ = an.nomi_file_report(Path("/tmp"), "", "PX-NAS", "PX-NAS")
    assert inv.name == "PX-NAS_inventory.md"


def test_drift_di_due_ore_esatte_e_solo_informativo():
    vm = an.VM(vmid="1", nome="chr", nodo="n", config={"agent": "1", "cpu": "host", "memory": "1024"},
               status={"status": "running", "agent": 1}, agent={"ora": (1_000_000 + 7200) * 10**9, "ora_host": 1_000_000})
    inv = {"nodi": {"n": {"nodo": {}}}, "cluster": {}}
    e = an.Esito()
    an.controlla_generali(vm, inv, e)
    orologio = [r for r in e.rilievi if "Orologio" in r.messaggio]
    assert orologio and orologio[0].livello == an.INFO


def _vm(nome, ostype="l26", pretty=None):
    ag = {"osinfo": {"pretty-name": pretty}} if pretty else {}
    return an.VM(vmid="1", nome=nome, nodo="n", config={"name": nome, "ostype": ostype}, agent=ag)


def test_suggerimento_profilo_dal_nome_e_dal_so():
    assert an.suggerisci_profilo(_vm("DA-DC01")) == "1"
    assert an.suggerisci_profilo(_vm("DA-SQL12")) == "2"
    assert an.suggerisci_profilo(_vm("DA-3CX-SBC")) == "4"
    assert an.suggerisci_profilo(_vm("PX-veeam-01")) == "5"
    assert an.suggerisci_profilo(_vm("DA-RDH")) == "6"
    assert an.suggerisci_profilo(_vm("TMPL-WIN-2022")) == "7"
    assert an.suggerisci_profilo(_vm("DA-SNS-replica")) == "7"   # replica vince sull'indizio "firewall"
    assert an.suggerisci_profilo(_vm("INTEGRA3CX")) == an.NON_CLASSIFICATA   # nessun indizio: nessuna proposta


def test_conversione_profili_versione_1():
    """I 13 profili della versione 1 finiscono nei 7 di oggi; un file già in versione 2 resta com'è."""
    v1 = {"100": "4", "101": "13", "102": "12", "103": "0", "104": "9"}
    assert an.converti_profili(v1) == {"100": "2", "101": "4", "102": "5", "103": "0", "104": "7"}
    v2 = {"_versione": "2", "100": "3"}
    assert an.converti_profili(v2) == {"100": "3"}
    assert set(an.CONVERSIONE_PROFILI_V1.values()) <= set(an.PROFILI) | {an.NON_CLASSIFICATA}


# ── le regole del manuale dentro il report ──────────────────────────────────

def test_nessun_blocco_interno_nelle_regole():
    """Il manuale marca [INTERNO] valutazioni Domarc e casi di clienti. Questo
    repository è pubblico: non devono uscire, né nel testo né altrove nel file
    generato (incident 2026-06-16, credenziali in un repo)."""
    generato = Path(__file__).resolve().parents[1] / "fonti_manuale.py"
    assert "[INTERNO]" not in generato.read_text(encoding="utf-8")
    for chiave, v in an.REGOLE.items():
        assert "[INTERNO]" not in v["testo"], chiave


def test_ogni_citazione_ha_il_suo_testo():
    """Un rilievo che cita «manuale §8.3 › Cache mode» deve trovare quel
    paragrafo: senza, il report rimanda a un documento che il lettore non ha."""
    radice = Path(__file__).resolve().parents[1]
    fonti_di_regole = [radice / "audit-nodo.py", radice / "strumenti" / "analizza-vcenter.py"]
    codice = "\n".join(f.read_text(encoding="utf-8") for f in fonti_di_regole if f.is_file())
    citate = {a.strip().rstrip(",;.") for a in an._RE_ANCORA.findall(codice)}
    mancanti = sorted(a for a in citate if a not in an.REGOLE)
    assert not mancanti, f"citazioni senza testo (rigenerare con strumenti/estrai-fonti.py): {mancanti}"


def test_le_regole_hanno_testo_e_origine():
    for chiave, v in an.REGOLE.items():
        assert len(v["testo"].strip()) > 80, f"{chiave}: testo troppo corto"
        assert v.get("origine") or v.get("file"), f"{chiave}: senza origine"


def test_una_citazione_puo_nominare_piu_regole():
    assert an.ancore_di("manuale §9.2, §9.6, §12.1") == ["§9.2", "§9.6", "§12.1"]
    # il titolo di una sottosezione contiene virgole: non va spezzato
    assert an.ancore_di("manuale §8.2 › Shares, hugepages, KSM") == ["§8.2 › Shares, hugepages, KSM"]
    assert an.ancore_di("blockstat") == ["blockstat"]
    assert an.ancore_di("") == []


def test_appendice_riporta_il_testo_della_regola_citata():
    """È il punto di tutto: il report deve bastare a sé stesso."""
    e = an.Esito()
    e.add(an.BLOCCANTE, "VM 1 (prova)", "cache=unsafe", "manuale §8.3 › Cache mode")
    md = "\n".join(an.sezione_regole_md(e))
    assert "## Le regole applicate" in md
    assert "### manuale §8.3 › Cache mode" in md
    assert an.REGOLE["§8.3 › Cache mode"]["testo"].splitlines()[0] in md
    # una regola non citata non compare
    assert "§9.4" not in md


def test_invio_al_portale_e_codice_distinto_dal_codice_cliente():
    """`--codice` è il codice CLIENTE (finisce nel nome dei file), `--codice-portale`
    è la chiave d'accesso al portale. Averli chiamati uguale faceva fallire
    argparse all'avvio: un guasto che il tecnico avrebbe scoperto davanti al
    cluster (2026-09-09)."""
    import subprocess
    import sys
    from pathlib import Path
    aiuto = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / "audit-nodo.py"), "--help"],
                           capture_output=True, text=True, timeout=60)
    assert aiuto.returncode == 0, aiuto.stderr
    assert "--codice CODCLI" in aiuto.stdout
    assert "--codice-portale" in aiuto.stdout
    assert "--invia" in aiuto.stdout


def test_le_domande_a_terminale_sono_opzionali():
    """Una sola versione dello strumento, non due: `--senza-domande` la rende
    silenziosa, e `--invia` lo implica perché le tipologie le gestisce il
    portale — chiederle anche qui raccoglierebbe due volte la stessa decisione
    senza sapere quale vale (2026-09-09)."""
    import subprocess
    import sys
    from pathlib import Path
    aiuto = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / "audit-nodo.py"), "--help"],
                           capture_output=True, text=True, timeout=60)
    assert "--senza-domande" in aiuto.stdout
    sorgente = (Path(__file__).resolve().parents[1] / "audit-nodo.py").read_text(encoding="utf-8")
    assert 'not getattr(args, "invia", None)' in sorgente, "l'invio deve spegnere le domande"


# ── rilievi solo sulle macchine accese ──────────────────────────────────────

class _VMFinta:
    def __init__(self, status=None, lista=None):
        self.status, self.lista = status, lista


def test_spenta_e_quello_che_dice_proxmox():
    """Il filtro è il campo Stato, non una deduzione: acceso è `running`."""
    assert an.e_spenta(_VMFinta(status={"status": "stopped"}))
    assert not an.e_spenta(_VMFinta(status={"status": "running"}))
    # lo stato può arrivare dall'elenco invece che dallo stato puntuale
    assert an.e_spenta(_VMFinta(status={}, lista={"status": "stopped"}))


def test_stato_sconosciuto_non_e_spenta():
    """Se lo stato manca — nodo che non ha risposto, raccolta parziale — la VM
    resta nei rilievi: meglio un rilievo di troppo che una macchina sparita in
    silenzio dal report di un cliente."""
    assert not an.e_spenta(_VMFinta(status=None, lista=None))
    assert not an.e_spenta(_VMFinta(status={}, lista={}))


# ── coerenza del cluster ────────────────────────────────────────────────────

def _nodo(rete=None, ip_link="", **extra):
    return {"nodo": {"network": rete or [], "ip_link": ip_link, **extra}, "vms": {}, "lxc": {}}


def test_vlan_con_reti_diverse_e_bloccante():
    """Il difetto trovato su OpenCRM il 2026-09-10: la stessa VLAN con un'altra
    rete su un nodo. Non si vede da nessun nodo preso da solo, e rompe la
    migrazione senza avvisare — la VM parte e si trova altrove."""
    inv = {"nodi": {
        "A": _nodo([{"iface": "vlan20", "type": "vlan", "vlan-id": "20", "cidr": "172.18.20.1/24"}]),
        "B": _nodo([{"iface": "vlan20", "type": "vlan", "vlan-id": "20", "cidr": "172.20.20.2/24"}]),
    }}
    e = an.Esito()
    an.controlla_coerenza_rete(inv, e)
    b = [r for r in e.rilievi if r.livello == an.BLOCCANTE and "VLAN 20" in r.messaggio]
    assert len(b) == 1
    # il rilievo mostra i valori a confronto, non solo l'anomalia
    assert "172.18.20.1/24" in b[0].messaggio and "172.20.20.2/24" in b[0].messaggio


def test_stessa_vlan_stessa_rete_non_e_un_rilievo():
    inv = {"nodi": {
        "A": _nodo([{"iface": "vlan20", "type": "vlan", "vlan-id": "20", "cidr": "172.18.20.1/24"}]),
        "B": _nodo([{"iface": "vlan20", "type": "vlan", "vlan-id": "20", "cidr": "172.18.20.2/24"}]),
    }}
    e = an.Esito()
    an.controlla_coerenza_rete(inv, e)
    assert not [r for r in e.rilievi if "VLAN 20" in r.messaggio]


def test_un_nodo_solo_non_genera_coerenza():
    """Le regole di coerenza confrontano: con un host solo non c'è niente da
    confrontare e devono tacere, non inventare."""
    inv = {"nodi": {"A": _nodo([{"iface": "vlan20", "type": "vlan", "vlan-id": "20", "cidr": "10.0.0.1/24"}])}}
    e = an.Esito()
    an.controlla_coerenza_rete(inv, e)
    an.controlla_coerenza_host(inv, e)
    assert e.rilievi == []


def test_catena_fisica_arriva_agli_slave():
    rete = {
        "vlan30": {"iface": "vlan30", "type": "vlan", "vlan-raw-device": "vmbr_int"},
        "vmbr_int": {"iface": "vmbr_int", "type": "bridge", "bridge_ports": "bond50"},
        "bond50": {"iface": "bond50", "type": "bond", "slaves": "eth0 eth1"},
    }
    assert an._sotto(rete, "vlan30") == {"vmbr_int", "bond50", "eth0", "eth1"}


def test_mtu_ignora_le_interfacce_effimere():
    """`tap*` e `fwbr*` compaiono e spariscono con le VM: confrontarle fra i
    nodi produrrebbe divergenze che non significano niente."""
    testo = ("1: lo: <LOOPBACK> mtu 65536 qdisc noqueue\n"
             "2: bond50: <BROADCAST> mtu 9000 qdisc noqueue\n"
             "3: tap100i0: <BROADCAST> mtu 1500 qdisc pfifo\n")
    assert an.mtu_di({"nodo": {"ip_link": testo}}) == {"bond50": 9000}


def test_eta_della_replica_si_misura_dalla_raccolta_non_da_adesso():
    """L'incidente del 2026-09-10: rianalizzando un JSON di tre ore prima, sette
    job in orario risultavano in ritardo di tre ore. Un'età si misura dall'ora
    che era ALLORA."""
    quando = 1789033958
    inv = {"raccolto_il": quando,
           "cluster": {"replication": [{"id": "1-0", "guest": 1}], "ha_resources": []},
           "nodi": {"A": {"nodo": {"replication": [{"id": "1-0", "last_sync": quando - 458, "fail_count": 0}]},
                          "vms": {}, "lxc": {}}}}
    e = an.Esito()
    an.controlla_replica_ha(inv, e)
    assert not [r for r in e.rilievi if "sincronizzazione" in r.messaggio]
    # e con un ritardo vero il rilievo compare
    inv["nodi"]["A"]["nodo"]["replication"][0]["last_sync"] = quando - 6 * 3600
    e2 = an.Esito()
    an.controlla_replica_ha(inv, e2)
    assert [r for r in e2.rilievi if r.livello == an.BLOCCANTE and "sincronizzazione" in r.messaggio]


def test_senza_istante_di_raccolta_la_regola_tace():
    """Meglio muta che bugiarda: una regola che misura il tempo senza sapere che
    ora era non deve scattare."""
    inv = {"cluster": {"replication": [{"id": "1-0"}], "ha_resources": []},
           "nodi": {"A": {"nodo": {"replication": [{"id": "1-0", "last_sync": 1}], "timedatectl": ""},
                          "vms": {}, "lxc": {}}}}
    assert an.istante_raccolta(inv) is None
    e = an.Esito()
    an.controlla_replica_ha(inv, e)
    assert not [r for r in e.rilievi if "sincronizzazione" in r.messaggio]


def test_migrazione_non_dichiarata_e_bloccante():
    inv = {"ingresso": "A", "cluster": {"options": {}, "replication": []},
           "nodi": {"A": _nodo(corosync_conf="node { ring0_addr: 10.9.0.1 }"), "B": _nodo()}}
    e = an.Esito()
    an.controlla_migrazione(inv, e)
    assert [r for r in e.rilievi if r.livello == an.BLOCCANTE and "rete di migrazione" in r.messaggio]


def test_bond_con_un_solo_membro_non_e_ridondante():
    inv = {"nodi": {"A": _nodo([{"iface": "bond0", "type": "bond", "slaves": "eth0"}])}}
    e = an.Esito()
    an.controlla_rete_nodo("A", inv["nodi"]["A"], inv, e)
    assert [r for r in e.rilievi if r.livello == an.BLOCCANTE and "bond0" in r.messaggio]


def test_categoria_coerenza_e_prima_nel_report():
    """I confronti fra nodi non appartengono a nessun nodo: vanno letti insieme,
    e per primi."""
    assert an.categoria_di("Coerenza — rete") == "Coerenza del cluster"
    assert an.ORDINE_CATEGORIE[0] == "Coerenza del cluster"


# ── firewall: i due interruttori ────────────────────────────────────────────

def _cluster(fw_options=None, fw_rules=None, **extra):
    return {"ingresso": "A", "cluster": {"fw_options": fw_options, "fw_rules": fw_rules or [], **extra},
            "nodi": {}}


def test_datacenter_acceso_e_host_spento_e_bloccante():
    """Il difetto trovato su OpenCRM il 2026-09-10, e il piu' frequente in
    assoluto: l'interruttore del datacenter e' acceso, quello degli host no, e
    `pve-firewall status` risponde comunque «enabled/running». Non si vede
    guardando un lato solo."""
    inv = _cluster({"enable": 1}, [{"action": "ACCEPT", "dport": "22"}])
    inv["nodi"] = {"A": {"nodo": {"fw_options": {"enable": 0}}, "vms": {}, "lxc": {}},
                   "B": {"nodo": {"fw_options": {"enable": 0}}, "vms": {}, "lxc": {}}}
    e = an.Esito()
    an.controlla_firewall(inv, e)
    b = [r for r in e.rilievi if r.livello == an.BLOCCANTE]
    assert any("SPENTO sull'host" in r.messaggio for r in b)
    assert any("nessun host che le applica" in r.messaggio for r in b)


def test_firewall_coerente_e_acceso_ovunque_non_e_un_rilievo():
    inv = _cluster({"enable": 1}, [{"action": "ACCEPT", "dport": "8006"}])
    inv["nodi"] = {"A": {"nodo": {"fw_options": {"enable": 1, "policy_in": "DROP"}}, "vms": {}, "lxc": {}}}
    e = an.Esito()
    an.controlla_firewall(inv, e)
    assert not [r for r in e.rilievi if r.livello == an.BLOCCANTE]


def test_accendere_il_firewall_senza_lasciarsi_una_porta_e_bloccante():
    """Anti-lockout: politica DROP e nessuna regola che ammetta 8006 o 22
    significa chiudersi fuori dal proprio nodo."""
    inv = _cluster({"enable": 1}, [{"action": "ACCEPT", "dport": "443"}])
    inv["nodi"] = {"A": {"nodo": {"fw_options": {"enable": 1, "policy_in": "DROP"}}, "vms": {}, "lxc": {}}}
    e = an.Esito()
    an.controlla_firewall(inv, e)
    assert [r for r in e.rilievi if r.livello == an.BLOCCANTE and "chiude fuori" in r.messaggio]


def test_raccolta_vecchia_senza_firewall_non_genera_niente():
    """Le raccolte fatte prima che queste chiamate esistessero non devono
    diventare cluster «senza firewall»: l'assenza del dato non e' un difetto."""
    inv = {"ingresso": "A", "cluster": {}, "nodi": {"A": {"nodo": {}, "vms": {}, "lxc": {}}}}
    e = an.Esito()
    an.controlla_firewall(inv, e)
    an.controlla_ceph_dettaglio(inv, e)
    an.controlla_notifiche(inv, e)
    assert e.rilievi == []


# ── Ceph ────────────────────────────────────────────────────────────────────

def test_pool_con_una_copia_sola_e_perdita_di_dati():
    inv = {"ingresso": "A",
           "nodi": {"A": {"nodo": {"ceph_pool": [{"pool_name": "vm", "size": 2, "min_size": 1}]}, "vms": {}, "lxc": {}}},
           "cluster": {"ceph": {}}}
    e = an.Esito()
    an.controlla_ceph_dettaglio(inv, e)
    messaggi = " ".join(r.messaggio for r in e.rilievi if r.livello == an.BLOCCANTE)
    assert "size 2" in messaggi and "min_size 1" in messaggi


def test_flag_noout_dimenticato_e_bloccante():
    """Si mette durante una manutenzione e si toglie dopo. Lasciato acceso,
    Ceph non ripara piu' da solo e nessuno se ne accorge finche' non serve."""
    inv = {"ingresso": "A",
           "nodi": {"A": {"nodo": {"ceph_pool": []}, "vms": {}, "lxc": {}}},
           "cluster": {"ceph": {"osdmap": {"flags": "sortbitwise,noout"}}}}
    e = an.Esito()
    an.controlla_ceph_dettaglio(inv, e)
    assert [r for r in e.rilievi if r.livello == an.BLOCCANTE and "noout" in r.messaggio]


def test_due_monitor_non_fanno_quorum():
    inv = {"ingresso": "A",
           "nodi": {"A": {"nodo": {"ceph_pool": []}, "vms": {}, "lxc": {}}},
           "cluster": {"ceph": {"monmap": {"mons": [{"public_addr": "10.0.0.1:6789/0"},
                                                    {"public_addr": "10.0.0.2:6789/0"}]}}}}
    e = an.Esito()
    an.controlla_ceph_dettaglio(inv, e)
    assert [r for r in e.rilievi if r.livello == an.BLOCCANTE and "monitor" in r.messaggio]


# ── notifiche ───────────────────────────────────────────────────────────────

def test_senza_matcher_un_backup_fallito_non_lo_sa_nessuno():
    inv = {"ingresso": "A", "nodi": {},
           "cluster": {"notif_endpoints": [{"name": "smtp"}], "notif_matchers": [], "backup": []}}
    e = an.Esito()
    an.controlla_notifiche(inv, e)
    assert [r for r in e.rilievi if r.livello == an.BLOCCANTE and "matcher" in r.messaggio]


def test_backup_su_storage_locale_in_un_cluster_e_bloccante():
    """Se si perde il nodo si perdono i backup con lui."""
    inv = {"ingresso": "A",
           "nodi": {"A": {"nodo": {}, "vms": {}, "lxc": {}}, "B": {"nodo": {}, "vms": {}, "lxc": {}}},
           "cluster": {"notif_endpoints": [], "notif_matchers": [{"name": "x"}],
                       "storage_def": [{"storage": "local", "shared": 0}],
                       "backup": [{"id": "j1", "storage": "local"}]}}
    e = an.Esito()
    an.controlla_notifiche(inv, e)
    assert [r for r in e.rilievi if r.livello == an.BLOCCANTE and "locale a un nodo" in r.messaggio]


# ── i comandi proposti ──────────────────────────────────────────────────────
# Il rischio di questa parte non e' sbagliare un comando: e' emetterne due che
# si disfano a vicenda. `qm set` riscrive l'intero valore, quindi il secondo
# cancella quello che ha appena messo il primo.

def test_due_comandi_sullo_stesso_parametro_diventano_uno():
    """Visto il 2026-09-10 appena i comandi sono nati: la regola di profilo
    voleva `--cpu host`, quella generale `--cpu x86-64-v2-AES`, sulla stessa
    macchina. Lanciati in fila, il secondo disfa il primo."""
    e = an.Esito()
    e.add(an.ATTENZIONE, "VM 100 (fw) @A — profilo Rete", "Raccomandato CPU type 'host'.", "manuale §9.5",
          comando="qm set 100 --cpu host")
    e.add(an.ATTENZIONE, "VM 100 (fw) @A", "CPU type kvm64.", "manuale §8.1",
          comando="qm set 100 --cpu x86-64-v2-AES")
    testo = "\n".join(an.sezione_comandi_md(e))
    assert testo.count("qm set 100 --cpu") == 1
    # e vince quella che sa cosa fa la macchina
    assert "--cpu host" in testo and "x86-64-v2-AES" not in testo


def test_i_comandi_di_una_macchina_stanno_in_un_blocco_solo():
    """I rilievi di profilo e quelli generali hanno ambiti diversi per la stessa
    VM: in due blocchi separati chi legge ne applica meta'."""
    e = an.Esito()
    e.add(an.BLOCCANTE, "VM 100 (fw) @A — profilo Rete", "Ballooning.", "manuale §9.5", comando="qm set 100 --balloon 0")
    e.add(an.ATTENZIONE, "VM 100 (fw) @A", "Protection.", "manuale §8.6", comando="qm set 100 --protection 1")
    testo = "\n".join(an.sezione_comandi_md(e))
    assert testo.count("### VM 100") == 1


def test_un_disco_riceve_un_comando_solo_con_tutti_i_parametri():
    """`qm set --scsi0` riscrive la riga intera: discard e iothread vanno
    aggiunti insieme, o il secondo comando cancella il primo."""
    cfg = {"scsi0": "local:100/vm-100-disk-0.qcow2,size=8G"}
    c = an._qm_disco("100", "scsi0", cfg, discard="on", iothread="1")
    assert c == "qm set 100 --scsi0 local:100/vm-100-disk-0.qcow2,size=8G,discard=on,iothread=1"
    # il valore che c'era resta: non si manda solo il parametro nuovo
    assert "vm-100-disk-0.qcow2" in c


def test_un_parametro_gia_presente_non_si_duplica():
    cfg = {"scsi0": "local:100/vm.qcow2,discard=ignore,size=8G"}
    c = an._qm_disco("100", "scsi0", cfg, discard="on")
    assert c.count("discard=") == 1 and "discard=on" in c


def test_un_rilievo_senza_comando_non_ne_riceve_uno_finto():
    """«VLAN 20 ha reti diverse fra i nodi» non ha un comando, ha una
    decisione. Non si inventa."""
    e = an.Esito()
    e.add(an.BLOCCANTE, "Coerenza — rete", "VLAN 20: reti IP diverse fra i nodi.", "manuale §1.3")
    assert e.agibili() == []
    assert an.sezione_comandi_md(e) == []


# ── assessment di migrazione ────────────────────────────────────────────────

def _carica_migrazione():
    import importlib.util as iu
    p = Path(__file__).resolve().parents[1] / "strumenti" / "analizza-vcenter.py"
    s = iu.spec_from_file_location("analizza_vcenter", p)
    m = iu.module_from_spec(s)
    sys.modules["analizza_vcenter"] = m
    s.loader.exec_module(m)
    return m


def test_un_disco_su_delta_e_uno_snapshot_aperto():
    """L'API REST di vSphere 8 non espone gli snapshot (404 su /snapshot,
    verificato su vCenter 8.0.3). Si riconoscono dal nome del backing, ed e'
    la cosa che conta: 11.5.1 dice di consolidare prima dell'import."""
    mig = _carica_migrazione()
    m = {"nome": "X", "acceso": True, "tools": {},
         "intera": {"disks": {"2000": {"backing": {"vmdk_file": "[DS] X/X-000001.vmdk"}, "capacity": 10 * 1024**3}}}}
    assert mig.su_delta(m)
    e = mig.an.Esito()
    mig.controlla(m, e)
    assert [r for r in e.rilievi if r.livello == mig.BLOCCANTE and "delta" in r.messaggio]


def test_un_disco_normale_non_e_uno_snapshot():
    """`X_1.vmdk` e `X.vmdk` sono dischi veri: solo il suffisso a sei cifre e'
    un delta. Confondere i due direbbe a meta' impianto di consolidare niente."""
    mig = _carica_migrazione()
    for f in ("[DS] X/X.vmdk", "[DS] X/X_1.vmdk", "[DS] X/X_12.vmdk"):
        m = {"nome": "X", "acceso": True, "tools": {},
             "intera": {"disks": {"2000": {"backing": {"vmdk_file": f}, "capacity": 1}}}}
        assert not mig.su_delta(m), f


def test_le_macchine_di_servizio_non_si_migrano():
    """vCLS-* le crea e ricrea vCenter: restano nel grezzo, spariscono dai
    conteggi e dalle schede."""
    mig = _carica_migrazione()
    dati = {"vm": {"a": {"intera": {"name": "vCLS-4c4c"}, "lista": {}},
                   "b": {"intera": {"name": "SRV-VERO"}, "lista": {}}}}
    assert [m["nome"] for m in mig.macchine(dati)] == ["SRV-VERO"]


def test_il_metodo_dipende_dalla_dimensione_e_lo_dichiara():
    """Chi legge deve poter dire «no, questa la faccio diversamente» sapendo su
    cosa ci si e' basati."""
    mig = _carica_migrazione()
    def vm(gb):
        return {"nome": "X", "acceso": True, "tools": {},
                "intera": {"disks": {"2000": {"backing": {"vmdk_file": "[DS] X/X.vmdk"},
                                              "capacity": gb * 1024**3}}}}
    assert mig.metodo(vm(100))[0] == "Import wizard ESXi"
    assert "live-import" in mig.metodo(vm(800))[0]
    assert mig.metodo(vm(2500))[0] == "Attach & Move disk"
    for gb in (100, 800, 2500):
        assert str(gb) in mig.metodo(vm(gb))[1]   # il perche' porta il numero
