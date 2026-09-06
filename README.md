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

## Contenu

```
calendar_gateway/     passerelle ASGI (app, auth/oauth, consentement, proxy upstream)
tests/                suite pytest (test_gateway.py)
deploy/
  install-gateway.sh               installation (compte dédié, venv, env, unité systemd)
  calendar-mcp-gateway.service     unité systemd durcie (boucle locale stricte)
  nginx-calendar-mcp.conf          vhost public (TLS, OAuth + /mcp uniquement)
  docker-compose.upstream.example.yml   exemple d'exécution de l'upstream (boucle locale)
  creer-phrase-calendar-mcp.sh     pose l'empreinte PBKDF2 de la phrase de consentement
  val_calendar.py                  validation de bout en bout via la passerelle
```

## Déploiement

1. Exécuter l'upstream (voir le dépôt upstream ; `deploy/docker-compose.upstream.example.yml`
   = exemple en boucle locale stricte, credentials Google hors Git en 0600).
2. `sudo bash deploy/install-gateway.sh` (depuis la racine du dépôt) : crée le compte
   `calendar-app`, installe le code + venv dans `/opt/calendar-mcp`, génère
   `calendar.env` (0600) et active l'unité systemd.
3. Poser la phrase de consentement : `sudo bash deploy/creer-phrase-calendar-mcp.sh`.
4. Configurer nginx (`deploy/nginx-calendar-mcp.conf`) avec un domaine + certificat TLS.

Variables d'environnement : voir `.env.example` (aucune valeur réelle versionnée).

## Test

```bash
pytest            # unités de la passerelle
sudo python3 deploy/val_calendar.py --create   # validation de bout en bout (sur le serveur)
```
