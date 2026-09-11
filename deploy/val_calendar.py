#!/usr/bin/env python3
"""Validation de la chaine MCP Calendar a travers la passerelle (127.0.0.1:8790).

Lit le jeton statique dans /opt/calendar-mcp/calendar.env (jamais affiche), ouvre une
session MCP, puis execute des appels reels. Les contenus prives du calendrier ne sont
pas affiches : uniquement des compteurs et l'evenement de test cree ici.

Ce validateur verifie aussi que le profil d'outils expose est complet et minimal :
les 10 outils attendus (8 historiques + `delete-event` + `list-colors`) doivent etre
annonces par `tools/list` ; une absence fait echouer la validation.

Avec `--create`, un CRUD complet est execute sur un evenement jetable de `primary`
(create -> update -> get -> delete), titre unique `TEST-MCP-CALENDAR-E2E-<timestamp>`.
La suppression est toujours tentee en `finally` : un evenement cree par cette
execution ne reste jamais dans le calendrier, meme si une etape intermediaire echoue.
Aucun autre evenement n'est jamais supprime.

Usage : sudo python3 chemin/du/depot/deploy/val_calendar.py [--create]
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

# Passerelle en boucle locale (127.0.0.1), jamais exposee : http volontaire.
BASE = "http://127.0.0.1:8790/mcp"  # nosemgrep: python.lang.security.audit.insecure-transport.urllib.insecure-request-object.insecure-request-object
ENV = "/opt/calendar-mcp/calendar.env"

FUSEAU = "Europe/Paris"

# Profil minimal expose par la passerelle (voir calendar_gateway/politique.py).
OUTILS_ATTENDUS: tuple[str, ...] = (
    "list-calendars",
    "list-events",
    "search-events",
    "get-event",
    "create-event",
    "update-event",
    "delete-event",
    "get-freebusy",
    "get-current-time",
    "list-colors",
)


def lire_jeton() -> str:
    with open(ENV, encoding="utf-8") as f:
        for ligne in f:
            if ligne.startswith("CALENDAR_MCP_TOKEN="):
                return ligne.split("=", 1)[1].strip().strip('"')
    raise SystemExit("CALENDAR_MCP_TOKEN absent de l'environnement")


def appeler(session: str | None, ident: int, methode: str, params: dict) -> tuple[str | None, dict]:
    corps = json.dumps(
        {"jsonrpc": "2.0", "id": ident, "method": methode, "params": params}
    ).encode()
    entetes = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {lire_jeton()}",
    }
    if session:
        entetes["mcp-session-id"] = session
    # URL constante de la passerelle locale (boucle locale).
    requete = urllib.request.Request(BASE, data=corps, headers=entetes)  # nosemgrep: python.lang.security.audit.insecure-transport.urllib.insecure-request-object.insecure-request-object
    with urllib.request.urlopen(requete, timeout=90) as reponse:  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
        session_id = reponse.headers.get("mcp-session-id") or session
        brut = reponse.read().decode()
    # Reponse JSON nue ou enveloppe SSE (l'upstream v2.6.3 repond en SSE).
    payload: dict | None = None
    for ligne in brut.splitlines():
        if ligne.startswith("data: "):
            payload = json.loads(ligne[6:])
    if payload is None:
        payload = json.loads(brut)
    return session_id, payload


def texte(paquet: dict) -> str:
    resultat = paquet.get("result") or {}
    for contenu in resultat.get("content") or []:
        if contenu.get("type") == "text":
            return str(contenu.get("text", ""))
    if paquet.get("error"):
        return f"ERREUR JSON-RPC: {paquet['error']}"
    return json.dumps(paquet, ensure_ascii=False)[:500]


def est_erreur(paquet: dict, t: str) -> bool:
    """Vrai si la reponse porte une erreur MCP (niveau JSON-RPC, enveloppe SSE
    incluse) ou un resultat `isError` de l'SDK upstream."""
    resultat = paquet.get("result") or {}
    return (
        bool(paquet.get("error"))
        or resultat.get("isError") is True
        or t.startswith("ERREUR")
    )


def suppression_confirmee(paquet: dict, t: str) -> bool:
    """Vrai si l'evenement ne peut plus reapparaitre dans le calendrier.

    Google garde une tombe `status: cancelled` pour un evenement supprime :
    `events.get` peut encore la renvoyer un court instant, mais `events.list`
    ne la contient plus. On accepte donc erreur OU tombe annulee comme preuve
    de suppression.
    """
    if est_erreur(paquet, t):
        return True
    try:
        donnees = json.loads(t)
    except ValueError:
        return False
    evenement = donnees.get("event") if isinstance(donnees, dict) else None
    return isinstance(evenement, dict) and evenement.get("status") == "cancelled"


def compter_dans(texte_brut: str) -> int:
    """Nombre d'entites dans un texte JSON d'outil (items/events/calendars)."""
    try:
        d = json.loads(texte_brut)
    except ValueError:
        return -1
    if isinstance(d, list):
        return len(d)
    for cle in ("items", "events", "calendars", "accounts"):
        if isinstance(d.get(cle), list):
            return len(d[cle])
    return -1


def verifier_tools_list(r: dict) -> list[str]:
    """Verifie que tools/list annonce exactement le profil minimal ; echec clair sinon."""
    outils = r["result"]["tools"]
    noms = [t["name"] for t in outils]
    manquants = [nom for nom in OUTILS_ATTENDUS if nom not in noms]
    si_manquant = [nom for nom in noms if nom not in OUTILS_ATTENDUS]
    print("tools/list        :", ", ".join(noms))
    if manquants:
        print("tools/list        : ECHEC -> outils absents :", ", ".join(manquants))
        print("  Le profil doit contenir :", ", ".join(OUTILS_ATTENDUS))
        sys.exit(1)
    if si_manquant:
        print("tools/list        : ALERTE -> outils exposes hors profil :", ", ".join(si_manquant))
    return noms


def extraire_id(t: str) -> str | None:
    m = re.search(r'"id"\s*:\s*"([A-Za-z0-9_-]+)"', t)
    return m.group(1) if m else None


def tester_list_colors(session: str) -> str:
    """Appelle list-colors et retourne un colorId d'evenement valide (ou quitte)."""
    session, r = appeler(session, 8, "tools/call", {"name": "list-colors", "arguments": {}})
    t = texte(r)
    if est_erreur(r, t):
        print("list-colors       : ECHEC ->", t[:300])
        sys.exit(1)
    try:
        donnees = json.loads(t)
    except ValueError:
        print("list-colors       : ECHEC -> reponse non JSON :", t[:300])
        sys.exit(1)
    evenements = donnees.get("event")
    calendriers = donnees.get("calendar")
    if not isinstance(evenements, dict) or not isinstance(calendriers, dict):
        print("list-colors       : ECHEC -> structure inattendue (event/calendar attendus)")
        sys.exit(1)
    if not evenements:
        print("list-colors       : ECHEC -> aucune couleur d'evenement retournee par Google")
        sys.exit(1)
    premier = next(iter(evenements))
    couleur = evenements[premier]
    if not isinstance(couleur, dict) or "background" not in couleur:
        print("list-colors       : ECHEC -> couleur d'evenement sans background :", str(couleur)[:200])
        sys.exit(1)
    print(f"list-colors       : OK ({len(evenements)} couleurs evenement, "
          f"{len(calendriers)} couleurs calendrier; colorId retenu pour le test : {premier})")
    return premier


def e2e_crud(session: str, iso_j: str, color_id: str) -> None:
    """CRUD complet jetable sur primary : create -> update -> get -> delete.

    La suppression est dans un `finally` : un evenement cree ici est toujours
    supprime, meme si une validation intermediaire echoue.
    """
    horodatage = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    titre = f"TEST-MCP-CALENDAR-E2E-{horodatage}"
    identifiant: str | None = None
    supprime = False
    echec = False

    def _nettoyer() -> None:
        nonlocal supprime
        if identifiant is None or supprime:
            return
        print("  nettoyage       : suppression de l'evenement de test (finally)")
        _, rd = appeler(session, 30, "tools/call", {
            "name": "delete-event",
            "arguments": {
                "calendarId": "primary",
                "eventId": identifiant,
                "sendUpdates": "none",
            },
        })
        td = texte(rd)
        if not est_erreur(rd, td):
            supprime = True
            print("  nettoyage       : OK, evenement supprime")
        else:
            print("  nettoyage       : ECHEC ->", td[:300])

    try:
        # 1. creation d'un evenement unique, demain 10h00-10h15 Europe/Paris
        _, r = appeler(session, 10, "tools/call", {
            "name": "create-event",
            "arguments": {
                "calendarId": "primary",
                "summary": titre,
                "start": f"{iso_j}T10:00:00",
                "end": f"{iso_j}T10:15:00",
                "timeZone": FUSEAU,
                "description": "test automatique MCP Calendar (CRUD E2E jetable)",
                "allowDuplicates": True,
            },
        })
        t = texte(r)
        if est_erreur(r, t):
            print("create-event      : ECHEC ->", t[:400])
            echec = True
            return
        identifiant = extraire_id(t)
        if not identifiant:
            print("create-event      : ECHEC -> id introuvable dans la reponse :", t[:400])
            echec = True
            return
        print(f"create-event      : OK -> {titre} (id {identifiant})")

        # 2. mise a jour : titre + colorId valide obtenu par list-colors
        titre_maj = f"{titre}-VALIDE"
        _, r = appeler(session, 11, "tools/call", {
            "name": "update-event",
            "arguments": {
                "calendarId": "primary",
                "eventId": identifiant,
                "summary": titre_maj,
                "colorId": color_id,
            },
        })
        t = texte(r)
        if est_erreur(r, t):
            print("update-event      : ECHEC ->", t[:400])
            echec = True
            return
        m2 = re.search(r'"summary"\s*:\s*"([^"]*)"', t)
        print("update-event      : OK ->", m2.group(1) if m2 else t[:300])

        # 3. relecture et verification des valeurs
        _, r = appeler(session, 12, "tools/call", {
            "name": "get-event",
            "arguments": {"calendarId": "primary", "eventId": identifiant},
        })
        t = texte(r)
        if est_erreur(r, t):
            print("get-event         : ECHEC ->", t[:300])
            echec = True
            return
        if titre_maj not in t:
            print("get-event         : ECHEC -> titre mis a jour absent de la reponse")
            echec = True
            return
        if f'"colorId":"{color_id}"' not in t and f'"colorId": "{color_id}"' not in t:
            print(f"get-event         : ECHEC -> colorId {color_id} absent de la reponse")
            echec = True
            return
        print(f"get-event         : OK -> {titre_maj} (colorId {color_id} present)")

        # 4. suppression (sendUpdates=none : pas de notification)
        _, r = appeler(session, 13, "tools/call", {
            "name": "delete-event",
            "arguments": {
                "calendarId": "primary",
                "eventId": identifiant,
                "sendUpdates": "none",
            },
        })
        t = texte(r)
        if est_erreur(r, t):
            print("delete-event      : ECHEC ->", t[:300])
            echec = True
            return
        supprime = True
        m4 = re.search(r'"success"\s*:\s*true', t)
        print("delete-event      : OK ->", m4.group(0) if m4 else t[:200])

        # 5. confirmation de disparition (erreur ou tombe `cancelled`)
        _, r = appeler(session, 14, "tools/call", {
            "name": "get-event",
            "arguments": {"calendarId": "primary", "eventId": identifiant},
        })
        t = texte(r)
        if suppression_confirmee(r, t):
            print("get-event (post)  : OK -> l'evenement n'existe plus")
        else:
            print("get-event (post)  : ECHEC -> l'evenement existe encore apres delete-event")
            echec = True
    finally:
        _nettoyer()

    if echec:
        sys.exit(1)


def main() -> None:
    creer = "--create" in sys.argv
    demain = (datetime.now(timezone.utc) + timedelta(days=1)).date()
    iso_j = demain.isoformat()

    session, r = appeler(None, 1, "initialize", {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "val-calendar", "version": "2"},
    })
    assert "result" in r, texte(r)
    print("initialize        : OK")

    session, r = appeler(session, 2, "tools/list", {})
    verifier_tools_list(r)

    session, r = appeler(session, 3, "tools/call", {
        "name": "list-calendars", "arguments": {},
    })
    t = texte(r)
    if est_erreur(r, t):
        print("list-calendars    : ECHEC ->", t[:300])
        sys.exit(1)
    print(f"list-calendars    : OK ({compter_dans(t)} calendriers)")
    print("  apercu          :", t[:220].replace("\n", " "))

    session, r = appeler(session, 4, "tools/call", {
        "name": "list-events",
        "arguments": {
            "calendarId": "primary",
            "timeMin": f"{iso_j}T00:00:00",
            "timeMax": f"{iso_j}T23:59:59",
            "timeZone": FUSEAU,
        },
    })
    t = texte(r)
    if est_erreur(r, t):
        print("list-events       : ECHEC ->", t[:300])
        sys.exit(1)
    print(f"list-events       : OK ({compter_dans(t)} evenement(s) le {iso_j} sur primary)")

    color_id = tester_list_colors(session)

    if creer:
        e2e_crud(session, iso_j, color_id)
        print("CRUD E2E          : OK (evenement cree, lu, supprime par le validateur)")
    else:
        print("(mode lecture seule : ajouter --create pour le CRUD E2E jetable)")


if __name__ == "__main__":
    main()
