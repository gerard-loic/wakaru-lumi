COMPOSE := docker compose --env-file docker/.env -f docker/docker-compose.yml

setup: ## Crée docker/.env et config/config.json (secrets générés) s'ils n'existent pas
	@test -f docker/.env || cp docker/.env.exemple docker/.env
	@test -f config/config.json || sed \
		-e "s/__JWT_SECRET__/$$(openssl rand -hex 32)/" \
		-e "s/__ADMIN_PASSWORD__/$$(openssl rand -hex 16)/" \
		config/config.json.exemple > config/config.json

up: ## Récupère l'image et démarre le service
	@$(COMPOSE) pull
	@$(COMPOSE) up -d

down: ## Arrête et supprime le conteneur
	@$(COMPOSE) down

restart: ## Redémarre le service (nécessaire après un changement dans config/)
	@$(COMPOSE) restart

logs: ## Affiche les logs en continu
	@$(COMPOSE) logs -f

bash: ## Ouvre un shell dans le conteneur
	@$(COMPOSE) exec lumi /bin/bash

pipeline-run: export PIPELINE_PAYLOAD := $(payload)
pipeline-run: ## Lance un pipeline à trigger Cli : make pipeline-run pipeline=<uid> [payload='{...}'] [args='--no-wait']
	@test -n "$(pipeline)" || { echo "Usage : make pipeline-run pipeline=<uid> [payload='{...}'] [args='--no-wait --timeout 600']"; exit 2; }
	@$(COMPOSE) exec -T lumi python cli.py pipeline "$(pipeline)" --payload "$${PIPELINE_PAYLOAD:-{\}}" $(args)

help: ## Affiche les commandes disponibles
	@grep -hE '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

.PHONY: setup up down restart logs bash pipeline-run help
