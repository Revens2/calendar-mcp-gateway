#!/usr/bin/env python3
"""Validation de la chaine MCP Calendar a travers la passerelle (127.0.0.1:8790).

Lit le jeton statique dans /opt/calendar-mcp/calendar.env (jamais affiche), ouvre une
session MCP, puis execute des appels reels. Les contenus prives du calendrier ne sont
pas affiches : uniquement des compteurs et l'evenement de test cree ici.
Usage : sudo python3 chemin/du/depot/deploy/val_calendar.py [--create]
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone

BASE = "http://127.0.0.1:8790/mcp"
ENV = "/opt/calendar-mcp/calendar.env"

FUSEAU = "Europe/Paris"


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
    requete = urllib.request.Request(BASE, data=corps, headers=entetes)
    with urllib.request.urlopen(requete, timeout=90) as reponse:
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


def main() -> None:
    creer = "--create" in sys.argv
    demain = (datetime.now(timezone.utc) + timedelta(days=1)).date()
    iso_j = demain.isoformat()

    session, r = appeler(None, 1, "initialize", {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "val-calendar", "version": "1"},
    })
    assert "result" in r, texte(r)
    print("initialize        : OK")

    session, r = appeler(session, 2, "tools/list", {})
    noms = [t["name"] for t in r["result"]["tools"]]
    print("tools/list        :", ", ".join(noms))

    session, r = appeler(session, 3, "tools/call", {
        "name": "list-calendars", "arguments": {},
    })
    t = texte(r)
    if t.startswith("ERREUR") or r.get("error"):
        print("list-calendars    : ECHEC ->", t[:300])
    else:
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
    if t.startswith("ERREUR") or r.get("error"):
        print("list-events       : ECHEC ->", t[:300])
    else:
        n = compter_dans(t)
        print(f"list-events       : OK ({n} evenement(s) le {iso_j} sur primary)")

    if not creer:
        return
    # ---- Test d'ecriture (evenement jetable, demain 10h00-10h15 Europe/Paris) ----
    session, r = appeler(session, 5, "tools/call", {
        "name": "create-event",
        "arguments": {
            "calendarId": "primary",
            "summary": "TEST-MCP-CALENDAR",
            "start": f"{iso_j}T10:00:00",
            "end": f"{iso_j}T10:15:00",
            "timeZone": FUSEAU,
            "description": "test automatique MCP Calendar (validation passerelle)",
            "allowDuplicates": True,
        },
    })
    t = texte(r)
    if r.get("error") or t.startswith("ERREUR"):
        print("create-event      : ECHEC ->", t[:400])
        sys.exit(1)
    print("create-event      : OK ->", t[:300])
    m = re.search(r'"id"\s*:\s*"([A-Za-z0-9_-]+)"', t)
    if not m:
        print("  (id non trouve dans la reponse; abandon de la mise a jour)")
        sys.exit(1)
    identifiant = m.group(1)
    session, r = appeler(session, 6, "tools/call", {
        "name": "update-event",
        "arguments": {
            "calendarId": "primary",
            "eventId": identifiant,
            "summary": "TEST-MCP-CALENDAR-VALIDE",
        },
    })
    t = texte(r)
    if r.get("error") or t.startswith("ERREUR"):
        print("update-event      : ECHEC ->", t[:400])
        sys.exit(1)
    m2 = re.search(r'"summary"\s*:\s*"([^"]*)"', t)
    print("update-event      : OK ->", m2.group(1) if m2 else t[:300])
    session, r = appeler(session, 7, "tools/call", {
        "name": "get-event",
        "arguments": {"calendarId": "primary", "eventId": identifiant},
    })
    t = texte(r)
    if r.get("error") or t.startswith("ERREUR"):
        print("get-event         : ECHEC ->", t[:300])
    else:
        m3 = re.search(r'"summary"\s*:\s*"([^"]*)"', t)
        print("get-event         : OK ->", m3.group(1) if m3 else t[:200].replace("\n", " "))


if __name__ == "__main__":
    main()
