SHELL := /bin/bash
.DEFAULT_GOAL := help
-include ./docker/.env
include ./docker/vendor/basecmd.mk
