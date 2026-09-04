---
badge: Linea principale
tono: ok
famiglia: 07-strumenti
ordine: 10
stato: cantiere
prossimo: provare il client su Windows e sul terzo cluster (via VPN)
---
Audit di sola lettura di un nodo o di un intero cluster Proxmox VE dal Mac/PC del tecnico, una sola connessione SSH: confronta cluster, nodi, hardware, storage, rete, performance e VM con le best practice del manuale Domarc e produce inventario e report Markdown per il cliente (`codcli_cliente_ip_*.md`). Nato dentro DA-Proxmox-Docs, poi repo proprio.

nota: i collector girano come root sui nodi del cliente — solo comandi di lettura, mai installare nulla; i report generati non vanno in git.
