# Projet Lumi

Starter pour un projet basé sur [Lumi](https://github.com/gerard-loic/lumi). Le code de Lumi n'est pas dans ce dépôt : il vient de l'image Docker `ghcr.io/gerard-loic/lumi`. Ce dépôt ne versionne que le spécifique du projet.

## Prérequis

- Docker avec Compose
- `make` et `openssl`
- Un Traefik sur le réseau Docker externe `traefik-network`

## Démarrer

```bash
make setup    # crée docker/.env et config/config.json, avec des secrets générés
```

Renseignez ensuite :

- dans `docker/.env` : le nom du projet et son hôte ;
- dans `config/config.json` : `app.url`, `services.api.url` et le bloc `LiteLLM` du profil.

```bash
make up
make pipeline-run pipeline=hello    # vérifie que le service répond
```

`make` seul liste les commandes disponibles.

## Structure

| Chemin | Rôle | Versionné |
|--------|------|-----------|
| `config/config.json.exemple` | Modèle de configuration | Oui |
| `config/config.json` | Configuration réelle, avec les secrets | Non |
| `config/systemprompt.md` | Prompt système du profil `default` | Oui |
| `config/pipelines/<uid>/` | Pipelines, avec leurs scripts | Oui |
| `config/tools/` | Outils MCP spécifiques | Oui |
| `config/services/` | Services spécifiques | Oui |
| `config/languages/<code>/` | Traductions spécifiques | Oui |
| `config/templates/` | Gabarits Word | Oui |
| `config/source-rag/` | Documents sources du RAG | Non |
| `storage/` | Données produites à l'exécution | Non |
| `docker/.env` | Variables de déploiement | Non |

`config/` est monté en lecture seule dans le conteneur. Après une modification, lancez `make restart`.

## Déployer en production

```bash
git clone <depot> && cd <depot>
make setup
sudo chown -R 1000:1000 storage    # le conteneur tourne avec l'UID 1000
```

Dans `docker/.env`, réglez :

```
COMPOSE_RESTART_POLICY=always
COMPOSE_TRAEFIK_ENTRYPOINT=https
```

Complétez `config/config.json`, puis lancez `make up`.

## Mettre à jour Lumi

Changez `COMPOSE_LUMI_VERSION` dans `docker/.env`, puis lancez `make up`. Choisissez une version exacte plutôt que `latest`.

## Bon à savoir

- `config.json` ne gère pas les variables d'environnement : les secrets restent dans ce fichier non versionné. Toute nouvelle clé doit aussi être ajoutée à `config.json.exemple`, sans sa valeur.
- Dans `tools_enabled`, les motifs suivent le chemin du fichier sous `lib/mcp/tools/` ou `config/tools/`. Par exemple, `context.*` active `lib/mcp/tools/context/datetime.py`.
- `authentication.service` doit désigner une clé de `services`. Le service `api` de l'exemple utilise le handler `Sylo` : remplacez-le selon votre projet.
- Dans le service `api`, `mcp` vaut `false` : passez-le à `true` si votre instance Sylo expose son serveur MCP.
