---
badge: Linea principale
tono: ok
famiglia: 07-strumenti
ordine: 10
stato: cantiere
prossimo: collaudare l'assessment su un secondo vCenter e portare le regole 13b (sicurezza) con i sette comandi in piu'
---
Audit di sola lettura di un nodo o di un cluster Proxmox VE dal PC del tecnico, con una sola connessione SSH: confronta cluster, nodi, hardware, storage, rete e VM con le best practice del manuale Domarc, e produce inventario e report per il cliente col testo delle regole citate in fondo — il report basta a sé stesso. Nello stesso repo il **questionario di migrazione**: pagina sola, offline, IT/EN, tre livelli, e un **Esito** che giudica le risposte invece della loro presenza.

L'area clienti che li pubblica è **DA-Proxweb** (`survey.domarc.it/proxmox`).

nota: nel questionario «campo bloccante» (obbliga a rispondere) e «parametro che blocca» (giudica la risposta) sono meccanismi distinti, da non fondere; il testo del manuale dentro la pagina è generato da `estrai-fonti.py` come `fonti_manuale.py`, non si tocca a mano. I collector girano come root sui nodi del cliente — solo comandi di lettura, mai installare nulla. `fonti_manuale.py` è generato dal manuale (DA-Proxmox-Docs) e non si modifica a mano; i blocchi [INTERNO] del manuale non escono, il repo è pubblico.
