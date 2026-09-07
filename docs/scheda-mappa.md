---
badge: Linea principale
tono: ok
famiglia: 07-strumenti
ordine: 10
stato: cantiere
prossimo: provare il client su Windows e sul terzo cluster (via VPN); il questionario essenziale su un cliente vero
---
Audit di sola lettura di un nodo o di un intero cluster Proxmox VE dal Mac/PC del tecnico, una sola connessione SSH: confronta cluster, nodi, hardware, storage, rete, performance e VM con le best practice del manuale Domarc e produce inventario e report Markdown per il cliente, col testo delle regole citate riportato in fondo — il report basta a sé stesso. Nello stesso repo il **questionario di migrazione**: pagina sola, offline, IT/EN, tre livelli annidati (Essenziale · Base · Completo) e un **Esito** che giudica le risposte, non la loro presenza: trenta regole dicono quali fermano la migrazione, cosa serve per toglierle, e riportano il testo del manuale che le motiva.

nota: nel questionario «campo bloccante» (obbliga a rispondere) e «parametro che blocca» (giudica la risposta) sono meccanismi distinti, da non fondere; il testo del manuale dentro la pagina è generato da `estrai-fonti.py` come `fonti_manuale.py`, non si tocca a mano. I collector girano come root sui nodi del cliente — solo comandi di lettura, mai installare nulla. `fonti_manuale.py` è generato dal manuale (DA-Proxmox-Docs) e non si modifica a mano; i blocchi [INTERNO] del manuale non escono, il repo è pubblico.
