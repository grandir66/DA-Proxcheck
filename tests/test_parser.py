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
    codice = (Path(__file__).resolve().parents[1] / "audit-nodo.py").read_text(encoding="utf-8")
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
