/* Carica lo script incorporato in questionario/questionario-migrazione.html dentro
   un DOM finto e ne restituisce le strutture interne. Serve a poter provare il
   modello dati e le regole di blocco senza un browser: il questionario è una
   pagina sola e finora nessun test la guardava. */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const RADICE = dirname(dirname(fileURLToPath(import.meta.url)));
export const PAGINA = join(RADICE, 'questionario', 'questionario-migrazione.html');

function nodoFinto(id = '') {
  const n = {
    id, value: '', textContent: '', innerHTML: '', hidden: false, type: 'text',
    dataset: {}, style: {}, parentElement: null, firstElementChild: null,
    classList: { add(){}, remove(){}, toggle(){}, contains(){ return false; } },
    setAttribute(){}, getAttribute(){ return null; }, addEventListener(){},
    removeEventListener(){}, replaceWith(){}, scrollIntoView(){}, focus(){},
    closest(){ return null; }, querySelector(){ return null; }, querySelectorAll(){ return []; },
    appendChild(){}, remove(){}, click(){},
  };
  return n;
}

export function caricaQuestionario() {
  const html = readFileSync(PAGINA, 'utf8');
  const m = html.match(/<script>\n([\s\S]*)\n<\/script>/);
  if (!m) throw new Error('script incorporato non trovato nella pagina');

  const document = {
    getElementById: (id) => nodoFinto(id),
    createElement: () => nodoFinto(),
    querySelector: () => null,
    querySelectorAll: () => [],
    body: nodoFinto('body'),
    addEventListener(){},
  };
  const magazzino = new Map();
  const localStorage = {
    getItem: (k) => (magazzino.has(k) ? magazzino.get(k) : null),
    setItem: (k, v) => magazzino.set(k, String(v)),
  };
  class IntersectionObserver { observe(){} disconnect(){} }
  const CSS = { escape: (s) => String(s).replace(/["\\]/g, '\\$&') };

  const coda = `
  ;return {
    DOC, DECISIONI, TRADUZIONI, LIVELLI,
    ESSENZIALE, ESSENZIALE_COLONNE, ESSENZIALE_GRUPPI, ESSENZIALE_SEZIONI,
    ESSENZIALE_IGNOTI, COLONNE_PER_TABELLA, REGOLE_BLOCCO,
    valutaBlocchi, visibile, livelloDi, campiRossiVisibili, esportaMarkdown,
    trovaCampo, t, testoRegola,
    FONTI: (typeof FONTI !== 'undefined' ? FONTI : {}),
    MANUALE: (typeof MANUALE !== 'undefined' ? MANUALE : {}),
    mdInHtml, ancoraId, fontiCitate, regoleApplicateMarkdown,
    get filtro(){ return FILTRO; },
    set filtro(f){ FILTRO = f; },
    get lingua(){ return LINGUA; },
    set lingua(l){ LINGUA = l; },
    prova(campi, extra){
      STATO = Object.assign(statoBase(), extra || {});
      STATO.campi = Object.assign({}, campi);
      return valutaBlocchi();
    },
  };`;

  const fabbrica = new Function('document', 'localStorage', 'IntersectionObserver', 'CSS', m[1] + coda);
  return fabbrica(document, localStorage, IntersectionObserver, CSS);
}
