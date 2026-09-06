# calendar-mcp-gateway

Passerelle d'authentification pour un serveur MCP **Google Calendar** (upstream :
[`nspady/google-calendar-mcp`](https://github.com/nspady/google-calendar-mcp), non inclus ici).

Elle n'est **pas** un serveur MCP de plein exercice : elle valide le jeton d'accès
(OAuth colocalisé ou Bearer statique) puis relaie tel quel le trafic Streamable HTTP
vers l'upstream (`127.0.0.1:3000`). Le flux d'autorisation Google initial reste celui
de l'upstream (serveur d'auth local 3500-3505, une seule fois).

- `/authorize`, `/token`, `/register`, `/revoke`, `/consentement` — serveur
  d'autorisation OAuth colocalisé (RFC 8414/9728) + page de consentement à phrase de passe.
- `/mcp` — middleware d'authentification puis proxy transparent vers l'upstream.
- Tout le reste répond 404.

## Rôle métier

Ce MCP est la **sortie opérationnelle du planner IA** : il lit, cherche, crée, modifie
et supprime les blocs de planning sur le calendrier principal (`primary`). Les règles
de catégorisation/couleurs vivent dans le RAG (convention), pas dans ce serveur.

## Profil d'outils

Deux couches indépendantes doivent être alignées pour qu'un outil soit réellement exposé :

1. **Upstream** (`ENABLED_TOOLS`, voir `deploy/calendar-mcp-upstream.env.example`) :
   filtre les outils enregistrés par le conteneur `nspady/google-calendar-mcp`.
2. **Passerelle** (`calendar_gateway/politique.py`) : chaque outil est classé
   lecture / écriture / admin. Non classé ⇒ jamais annoncé, jamais exécutable
   (fail-closed), même si l'upstream l'expose.

Profil minimal voulu (10 outils, lecture + écriture sur `primary`) :

```
list-calendars  list-events  search-events  get-event  get-freebusy  get-current-time  list-colors   # lecture
create-event    update-event delete-event                                                             # écriture
```

`list-colors` renvoie les couleurs réellement disponibles côté Google ; la passerelle
n'embarque aucune palette. `delete-event` est exposé tel quel (schéma officiel upstream) :
le planner supprime ses propres blocs sans confirmation supplémentaire.

Reste **hors profil** (jamais annoncé ni exécutable via la passerelle) : `create-events`
(bulk), `respond-to-event` et `manage-accounts` (administration des comptes, refusée
même avec `calendar:ecriture`).

## Contenu

```
calendar_gateway/     passerelle ASGI (app, auth/oauth, politique, consentement, proxy upstream)
tests/                suite pytest (test_gateway.py, test_oauth_magasin.py)
deploy/
  install-gateway.sh               installation (compte dédié, venv, env, unité systemd)
  calendar-mcp-gateway.service     unité systemd durcie (boucle locale stricte)
  nginx-calendar-mcp.conf          vhost public (TLS, OAuth + /mcp uniquement)
  docker-compose.upstream.example.yml        exemple d'exécution de l'upstream (boucle locale)
  calendar-mcp-upstream.env.example          template `.env` upstream (profil ENABLED_TOOLS, aucun secret)
  creer-phrase-calendar-mcp.sh     pose l'empreinte PBKDF2 de la phrase de consentement
  val_calendar.py                  validation de bout en bout via la passerelle
```

## Déploiement

1. Exécuter l'upstream (voir le dépôt upstream ; `deploy/docker-compose.upstream.example.yml`
   = exemple en boucle locale stricte). Copier `deploy/calendar-mcp-upstream.env.example`
   vers le `.env` du compose upstream : aucun secret (les credentials Google sont dans
   `gcp-oauth.keys.json`, 0600, hors Git), mais le profil `ENABLED_TOOLS` y est versionné.
2. `sudo bash deploy/install-gateway.sh` (depuis la racine du dépôt) : crée le compte
   `calendar-app`, installe le code + venv dans `/opt/calendar-mcp`, génère
   `calendar.env` (0600) et active l'unité systemd.
3. Poser la phrase de consentement : `sudo bash deploy/creer-phrase-calendar-mcp.sh`.
4. Configurer nginx (`deploy/nginx-calendar-mcp.conf`) avec un domaine + certificat TLS.

Variables d'environnement : voir `.env.example` (aucune valeur réelle versionnée).

### Mise à jour d'un serveur existant

`install-gateway.sh` régénère `calendar.env` (jeton statique, issuer, hash de
consentement) : sur une installation déjà en service, préférer une mise à jour
minimale — recopier `calendar_gateway/` vers `/opt/calendar-mcp/` puis
`systemctl restart calendar-mcp-gateway` — afin de préserver l'état OAuth et la
configuration réelle.

## Test

```bash
pytest            # unités de la passerelle
sudo python3 deploy/val_calendar.py --create   # validation de bout en bout (sur le serveur)
```

`val_calendar.py` vérifie que `tools/list` annonce bien les 10 outils du profil
(absence de `delete-event` ou `list-colors` ⇒ échec clair), appelle réellement
`list-colors`, puis avec `--create` déroule un CRUD complet (create → update →
get → delete) sur un événement jetable de `primary` (`TEST-MCP-CALENDAR-E2E-<timestamp>`),
supprimé en `finally` même en cas d'échec intermédiaire. Il ne supprime jamais un
événement qu'il n'a pas lui-même créé.

## Description recommandée du connecteur (ChatGPT)

La description affichée par ChatGPT pour un connecteur MCP est définie dans la
configuration du connecteur côté ChatGPT, pas par ce serveur. Texte recommandé :

> MCP Google Calendar custom auto-hébergé, connecté à Google Calendar API. Utilisé
> comme calendrier opérationnel du planner IA de Titou, notamment depuis les tâches
> planifiées. Permet de lire, rechercher, créer, modifier et supprimer les blocs de
> planning sur le calendrier principal (`primary`). Pour les couleurs, utiliser
> `list-colors`; les règles métier de catégorisation restent dans le RAG.
