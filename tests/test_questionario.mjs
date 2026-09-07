/* Il questionario è una pagina sola con 120 domande e 30 regole di blocco, e
   finora nessun test la guardava. Qui si fissa quello che, se si rompe, si rompe
   in silenzio: una regola che punta a un campo inesistente non si accende mai e
   non lo dice a nessuno; una id sbagliata nell'elenco ESSENZIALE toglie una
   domanda dalla vista minima senza un errore; una stringa nuova senza traduzione
   fa comparire l'italiano dentro la versione inglese.
   Si esegue con:  node --test tests/                                          */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { caricaQuestionario, PAGINA } from './carica-questionario.mjs';

const q = caricaQuestionario();
const HTML = readFileSync(PAGINA, 'utf8');

const campiDOC = () => {
  const out = [];
  q.DOC.sezioni.forEach(s => (s.gruppi || []).forEach(g => (g.righe || []).forEach(c => out.push({ s, g, c }))));
  return out;
};

/* ───────────────────────── modello dati ───────────────────────── */

test('ogni id dell\'elenco ESSENZIALE esiste davvero', () => {
  assert.deepEqual(q.ESSENZIALE_IGNOTI, [],
    'id o colonne citate in ESSENZIALE che non corrispondono a niente nel documento');
});

test('nessuna id di campo è duplicata', () => {
  const visti = new Map();
  campiDOC().forEach(({ c }) => visti.set(c.id, (visti.get(c.id) || 0) + 1));
  const doppie = [...visti].filter(([, n]) => n > 1).map(([id]) => id);
  assert.deepEqual(doppie, []);
});

test('i tre livelli sono annidati: essenziale ⊂ base ⊂ completo', () => {
  const insieme = (filtro) => {
    q.filtro = filtro;
    const out = new Set();
    q.DOC.sezioni.forEach(s => {
      if (!q.visibile(s)) return;
      (s.gruppi || []).forEach(g => {
        if (!q.visibile(g)) return;
        (g.righe || []).forEach(c => { if (q.visibile(c)) out.add(c.id); });
      });
    });
    return out;
  };
  const ess = insieme('essenziale'), base = insieme('base'), tutto = insieme('completo');
  [...ess].forEach(id => assert.ok(base.has(id), `${id}: nell'essenziale ma non in base`));
  [...base].forEach(id => assert.ok(tutto.has(id), `${id}: in base ma non in completo`));
  assert.ok(ess.size < base.size && base.size < tutto.size,
    `i livelli devono restringere davvero: ${ess.size} < ${base.size} < ${tutto.size}`);
  q.filtro = 'completo';
});

test('nell\'essenziale le tabelle mostrano le colonne dichiarate, e solo quelle', () => {
  q.filtro = 'essenziale';
  Object.entries(q.ESSENZIALE_COLONNE).forEach(([tab, attese]) => {
    const viste = q.COLONNE_PER_TABELLA[tab].filter(c => q.visibile(c)).map(c => c.key);
    assert.deepEqual(viste.sort(), [...attese].sort(), `tabella ${tab}`);
  });
  q.filtro = 'completo';
});

test('nell\'essenziale ogni sezione visibile ha almeno una domanda o una tabella', () => {
  q.filtro = 'essenziale';
  q.DOC.sezioni.filter(s => q.visibile(s)).forEach(s => {
    if (s.tipo === 'decisioni') return;
    const roba = (s.gruppi || []).filter(g => q.visibile(g))
      .reduce((n, g) => n + (g.tipo === 'qa' ? g.righe.filter(c => q.visibile(c)).length : 1), 0);
    assert.ok(roba > 0, `sezione ${s.id} visibile ma vuota`);
  });
  q.filtro = 'completo';
});

/* ───────────────────────── regole di blocco ───────────────────────── */

test('ogni regola ha id unica e punta a una sezione e a un campo che esistono', () => {
  const idCampi = new Set(campiDOC().map(({ c }) => c.id));
  const idSezioni = new Set(q.DOC.sezioni.map(s => s.id));
  const viste = new Set();
  q.REGOLE_BLOCCO.forEach(r => {
    assert.ok(!viste.has(r.id), `regola duplicata: ${r.id}`);
    viste.add(r.id);
    assert.ok(idSezioni.has(r.sez), `regola ${r.id}: sezione ${r.sez} inesistente`);
    if (r.campo !== null) assert.ok(idCampi.has(r.campo), `regola ${r.id}: campo ${r.campo} inesistente`);
    assert.ok(typeof r.quando === 'function', `regola ${r.id}: manca quando()`);
  });
});

test('ogni testo delle regole ha la sua traduzione inglese', () => {
  const mancanti = [];
  q.REGOLE_BLOCCO.forEach(r => {
    ['titolo', 'effetto', 'sblocco'].forEach(k => {
      q.lingua = 'it'; const it = q.testoRegola(r, k);
      q.lingua = 'en'; const en = q.testoRegola(r, k);
      if (it === en) mancanti.push(`${r.id}.${k}`);
    });
  });
  q.lingua = 'it';
  assert.deepEqual(mancanti, [], 'testi che in inglese restano in italiano');
});

test('ogni stringa passata a t() ha la sua traduzione', () => {
  const script = HTML.match(/<script>\n([\s\S]*)\n<\/script>/)[1];
  const letterali = [...script.matchAll(/\bt\((['"])((?:\\.|(?!\1)[^\\])*)\1\)/g)]
    .map(x => x[2].replace(/\\'/g, "'").replace(/\\"/g, '"'));
  const uniche = [...new Set(letterali)];
  assert.ok(uniche.length > 50, 'estrazione delle stringhe fallita: rivedere il regex');
  assert.deepEqual(uniche.filter(k => !(k in q.TRADUZIONI)), [],
    'stringhe che in inglese resterebbero in italiano');
});

test('ogni stringa dell\'interfaccia ha la sua traduzione', () => {
  const chiavi = [...HTML.matchAll(/data-i18n(?:-html)?="([^"]+)"/g)]
    .map(x => x[1].replace(/&quot;/g, '"').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>'));
  assert.ok(chiavi.length >= 18);
  assert.deepEqual(chiavi.filter(k => !(k in q.TRADUZIONI)), []);
});

/* Ogni riga: la regola, le risposte, l'esito atteso. Sono i casi che descrivono
   che cosa il questionario considera bloccante — si leggono come una specifica. */
const CASI = [
  // vSAN, nomi datastore, cifratura: le tre condizioni che l'import non supera
  ['vsan', { vsan_vm: 'srv-app01, srv-db02' }, 'blocco'],
  ['vsan', { vsan_vm: 'nessuna' }, false],
  ['vsan', { feat_vsan: 'Sì' }, 'blocco'],
  ['vsan', { vsan_vm: 'da verificare' }, 'verifica'],
  ['datastore_nome', { caratteri_speciali_vm: 'DS+PROD01' }, 'blocco'],
  ['datastore_nome', { caratteri_speciali_vm: 'no' }, false],
  ['cifratura', { cifratura_policy_vm: 'srv-hr01' }, 'blocco'],

  // accessi e via di ritorno
  ['credenziali_esxi', { esxi_credenziali: 'No' }, 'blocco'],
  ['credenziali_esxi', { esxi_credenziali: 'Da verificare' }, 'verifica'],
  ['credenziali_esxi', { esxi_credenziali: 'Sì' }, false],
  ['host_liberabile', { host_riuso: 'Sì', host_liberabile: 'No' }, 'blocco'],
  ['host_liberabile', { host_riuso: 'No', host_liberabile: 'No' }, false],
  ['rollback_spazio', { rollback_spazio: 'No' }, 'blocco'],
  ['rollback_zero', { rollback_durata: '0' }, 'verifica'],
  ['rollback_zero', { rollback_durata: '21' }, false],
  ['ref_applicativo', {}, 'verifica'],
  ['ref_applicativo', { ref_applicativo: 'Anna Bianchi — ufficio IT' }, false],

  // le VM che non si spostano come le altre
  ['bitlocker_key', { sp_bitlocker: 'srv-fin01' }, 'blocco'],
  ['bitlocker_key', { sp_bitlocker: 'srv-fin01', sp_recovery_key: 'tutte, in AD' }, false],
  ['bitlocker_key', { sp_bitlocker: 'nessuna' }, false],
  ['rdm', { sp_rdm: 'srv-sql01 (quorum)' }, 'blocco'],
  ['cluster_app', { sp_cluster_app: 'SQL Always On su srv-sql01/02' }, 'blocco'],
  ['licenze_hw', { sp_licenze_hw: 'gestionale con dongle USB' }, 'blocco'],
  ['snapshot', { sp_snapshot: 'srv-web01 da marzo' }, 'verifica'],
  ['ft', { feat_ft: 'Sì' }, 'verifica'],

  // rete
  ['nic_una', { reti_interfacce: '1' }, 'blocco'],
  ['nic_una', { reti_interfacce: '2' }, false],
  ['nic_poche', { reti_interfacce: '2' }, 'verifica'],
  ['nic_poche', { reti_interfacce: '4' }, false],
  ['switch_porte', { 'switch_porte_libere.porte': '1' }, 'blocco'],
  ['switch_porte', { 'switch_porte_libere.porte': '3' }, 'verifica'],
  ['switch_porte', { 'switch_porte_libere.porte': '6' }, false],
  ['ntp', {}, 'verifica'],
  ['ntp', { ntp: 'da verificare' }, 'verifica'],
  ['ntp', { ntp: '192.168.10.1 (DC)' }, false],

  // storage
  ['thin', { storage_thin: 'No' }, 'blocco'],
  ['unmap', { storage_unmap: 'Da verificare' }, 'verifica'],
  ['zfs_raid', { zfs_hba: 'PERC H730 RAID hardware' }, 'blocco'],
  ['zfs_raid', { zfs_hba: 'HBA330' }, false],
  ['zfs_raid', { zfs_hba: 'PERC H330 in modalità IT, RAID disattivato' }, false],
  ['zfs_rpo', { storage_tipo: 'ZFS locale', zfs_rpo: 'No' }, 'blocco'],
  ['zfs_rpo', { storage_tipo: 'NFS', zfs_rpo: 'No' }, false],
  ['capacita', { cap_occupato: '10', cap_crescita: '5', cap_snapshot: '4', 'storage_capacita.libera': '12' }, 'blocco'],
  ['capacita', { cap_occupato: '10', cap_crescita: '5', cap_snapshot: '4', 'storage_capacita.libera': '30' }, false],
  ['sas', { storage_tipo: 'SAS direct-attached' }, 'verifica'],
  ['sas', { storage_tipo: 'SAS direct-attached', sas_porte: '2', sas_server: '3' }, 'blocco'],
  ['sas', { storage_tipo: 'SAS direct-attached', sas_porte: '2', sas_server: '3', sas_switch: 'Sì' }, false],
  ['sas', { storage_tipo: 'SAS direct-attached', sas_porte: '4', sas_server: '3' }, false],
  ['sas', { storage_tipo: 'ZFS locale' }, false],

  // nodi, quorum, backup, repository
  ['qdevice', { cluster_nodi_num: '2' }, 'blocco'],
  ['qdevice', { cluster_nodi_num: '2', cluster_qdevice: 'NAS di sala, sempre acceso' }, false],
  ['qdevice', { cluster_nodi_num: '3' }, false],
  ['cpu_diverse', { nodi_cpu_identiche: 'No' }, 'verifica'],
  ['cpu_diverse', { nodi_cpu_identiche: 'Sì' }, false],
  ['veeam', { veeam_previsto: 'Sì', veeam_filelevel: 'No' }, 'blocco'],
  ['veeam', { veeam_previsto: 'No', veeam_filelevel: 'No' }, false],
  ['dump_db', { pbs_dump_db: 'no' }, 'verifica'],
  ['dump_db', { pbs_dump_db: 'pg_dump notturno su NAS' }, false],
  ['dump_db', {}, false],
  ['repo', { sic_repo: 'No' }, 'blocco'],
  ['repo', { sic_repo: 'Sì' }, false],
];

function esitoDi(campi, idRegola, extra) {
  const { blocchi, verifiche } = q.prova(campi, extra);
  if (blocchi.some(r => r.id === idRegola)) return 'blocco';
  if (verifiche.some(r => r.id === idRegola)) return 'verifica';
  return false;
}

test('le regole si accendono e si spengono sulle risposte attese', () => {
  const idRegole = new Set(q.REGOLE_BLOCCO.map(r => r.id));
  CASI.forEach(([id, campi, atteso]) => {
    assert.ok(idRegole.has(id), `caso su una regola inesistente: ${id}`);
    assert.equal(esitoDi(campi, id), atteso,
      `regola ${id} con ${JSON.stringify(campi)}`);
  });
});

test('ogni regola è coperta da almeno un caso che la accende', () => {
  const accese = new Set(CASI.filter(([, , a]) => a !== false).map(([id]) => id));
  const scoperte = q.REGOLE_BLOCCO.map(r => r.id).filter(id => !accese.has(id) && id !== 'ip_corosync');
  assert.deepEqual(scoperte, [], 'regole senza un caso che le accende');
});

test('gli indirizzi di management e corosync mancanti si segnalano, presenti no', () => {
  const nodo = (n, pieno) => ({
    id: `n${n}`, numero: n,
    ip_mgmt: pieno ? `192.168.10.1${n}/24` : '',
    ip_coro1: pieno ? `10.10.1.1${n}/24` : '',
    ip_coro2: pieno ? `10.10.2.1${n}/24` : '',
  });
  assert.equal(esitoDi({}, 'ip_corosync', { nodi: [nodo(1, false), nodo(2, false)] }), 'verifica');
  assert.equal(esitoDi({}, 'ip_corosync', { nodi: [nodo(1, true), nodo(2, true)] }), false);
});

/* ───────────────────────── il manuale dentro la pagina ───────────────────────── */

test('ogni regola cita il manuale, e la citazione ha il suo testo', () => {
  const senzaFonte = q.REGOLE_BLOCCO.filter(r => !r.fonte).map(r => r.id);
  assert.deepEqual(senzaFonte, [], 'regole senza citazione del manuale');
  const rotte = [];
  q.REGOLE_BLOCCO.forEach(r => {
    assert.ok(r.fonte.startsWith('§'), `regola ${r.id}: la fonte non è un'ancora del manuale`);
    const f = q.FONTI[r.fonte];
    if (!f || !f.testo || !f.testo.trim()) rotte.push(`${r.id} → ${r.fonte}`);
  });
  assert.deepEqual(rotte, [], 'citazioni senza testo: rigenerare con strumenti/estrai-fonti.py');
});

test('ogni fonte porta la propria origine nel manuale', () => {
  Object.entries(q.FONTI).forEach(([a, f]) => {
    assert.ok(f.titolo && f.titolo.trim(), `${a}: senza titolo`);
    assert.match(f.file, /^manuale\/.+\.md$/, `${a}: origine non tracciata`);
    assert.ok(f.riga > 0, `${a}: senza riga di origine`);
  });
  assert.ok(q.MANUALE.nome, "manca l'edizione del manuale");
});

test('nella pagina non finisce nulla di marcato interno — è pubblicata', () => {
  assert.doesNotMatch(HTML, /\[INTERNO\]/,
    'un blocco interno del manuale è finito nella pagina: la pagina sta su GitHub Pages');
});

test('ancore diverse danno id diversi (il collegamento non sbaglia bersaglio)', () => {
  const ancore = [...new Set(q.REGOLE_BLOCCO.map(r => r.fonte))];
  const id = ancore.map(q.ancoraId);
  assert.equal(new Set(id).size, ancore.length, 'due citazioni diverse finiscono sullo stesso id');
});

test('il Markdown del manuale si rende: tabelle, grassetto, codice', () => {
  const h = q.mdInHtml('| Trappola | Sintomo |\n|---|---|\n| **vTPM** | Richiesta chiave |\n\nTesto con `codice`.');
  assert.match(h, /<table>/);
  assert.match(h, /<th>Trappola<\/th>/);
  assert.match(h, /<strong>vTPM<\/strong>/);
  assert.match(h, /<code>codice<\/code>/);
  assert.doesNotMatch(h, /\|---\|/, 'la riga separatrice della tabella non deve comparire');
  // §11.13 è una tabella: se il renderer la sbaglia, la regola più citata è illeggibile
  const trappole = q.FONTI['§11.13'];
  if (trappole) assert.match(q.mdInHtml(trappole.testo), /<table>/);
});

test('un passaggio citato compare una volta sola, anche se lo citano più regole', () => {
  const perAncora = {};
  q.REGOLE_BLOCCO.forEach(r => { (perAncora[r.fonte] ??= []).push(r.id); });
  const condivise = Object.entries(perAncora).filter(([, v]) => v.length > 1);
  assert.ok(condivise.length, 'atteso almeno un passaggio citato da più regole');
  const [ancora, regole] = condivise[0];
  const accese = q.REGOLE_BLOCCO.filter(r => regole.includes(r.id));
  assert.equal(q.fontiCitate(accese).length, 1, `${ancora}: ripetuto una volta per regola`);
});

test('le regole applicate finiscono nel Markdown, col testo e con l\'origine', () => {
  q.prova({ storage_thin: 'No', veeam_previsto: 'Sì', veeam_filelevel: 'No' });
  const righe = q.regoleApplicateMarkdown().join('\n');
  assert.match(righe, /# Le regole applicate/);
  assert.match(righe, /Il conto degli snapshot su LVM/);
  assert.match(righe, /vincolo Veeam/);
  assert.match(righe, /storage file-level/);                 // il testo vero del manuale
  assert.match(righe, /manuale\/0?1-progettare\.md:\d+/);    // l'origine
});

test('senza regole accese non si stampa una sezione vuota di regole', () => {
  q.prova({ ref_applicativo: 'Anna Bianchi', ntp: '10.0.0.1' },
          { nodi: [{ id: 'n1', ip_mgmt: '1.1.1.1/24', ip_coro1: '2.2.2.1/24', ip_coro2: '3.3.3.1/24' }] });
  assert.deepEqual(q.regoleApplicateMarkdown(), []);
});

/* ───────────────────────── esportazione ───────────────────────── */

test('l\'esito compare in testa al Markdown, con i blocchi trovati', () => {
  q.filtro = 'essenziale';
  q.prova({ storage_thin: 'No', veeam_previsto: 'Sì', veeam_filelevel: 'No' });
  const md = q.esportaMarkdown();
  const testa = md.slice(0, md.indexOf('# SEZIONE'));
  assert.match(testa, /# Esito/);
  assert.match(testa, /Storage senza thin provisioning/);
  assert.match(testa, /Veeam senza storage file-level/);
  assert.match(testa, /🔴/);
  q.filtro = 'completo';
});

test('il Markdown dell\'essenziale non contiene le domande dei livelli più estesi', () => {
  q.filtro = 'essenziale';
  q.prova({});
  const md = q.esportaMarkdown();
  assert.match(md, /versione essenziale/);
  assert.match(md, /Lo storage supporta il thin provisioning/);        // essenziale
  assert.doesNotMatch(md, /Convenzione di denominazione/);             // §10, completo
  assert.doesNotMatch(md, /Esiste uno switch SAS/);                    // §6.3, completo
  q.filtro = 'completo';
});

test('un filtro sconosciuto (bozza vecchia) non lascia la pagina vuota', () => {
  q.filtro = 'ridotto-2024';
  const visibili = q.DOC.sezioni.filter(s => q.visibile(s)).length;
  assert.equal(visibili, q.DOC.sezioni.length, 'un filtro ignoto deve comportarsi come «completo»');
  q.filtro = 'completo';
});
