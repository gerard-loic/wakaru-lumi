import json
from pathlib import Path

from jsonschema import validators

from lib.log.logger import Logger, WARNING

#Décrit pourquoi le texte d'une leçon a été rejeté par JsonFormat, qui ne l'écrit que dans les logs, pour que
#l'agent de réparation sache quoi corriger.
#Échoue si le texte n'est pas une leçon, par exemple {"error" : ...} quand un outil est indisponible (cf. prompt) :
#il n'y a alors rien à réparer, et une réparation inventerait une leçon.
#
#Paramètres (clé "params" du bloc) :
#  - input  (str) : clé du contexte du texte JSON de la leçon.
#  - output (str) : clé du contexte où écrire la description de l'erreur.
EXCERPT_LENGTH = 100
MAX_SCHEMA_ERRORS = 20


def run(context, params):
    text = context.resolve(params["input"])
    if '"schema_version"' not in text:
        Logger.write(f"[Wakaru] LLM response is not a lesson, nothing to repair : {text[:200]}", type=WARNING)
        return False
    try:
        error = _describeSchemaErrors(json.loads(text))
    except json.JSONDecodeError as e:
        error = _describeSyntaxError(text, e)
    context.set(params["output"], error)
    Logger.write(f"[Wakaru] Invalid lesson, repair requested : {error}", type=WARNING)
    return True


def _describeSyntaxError(text:str, e:json.JSONDecodeError)->str:
    before = text[max(0, e.pos - EXCERPT_LENGTH):e.pos]
    after = text[e.pos:e.pos + EXCERPT_LENGTH]
    return f"JSON invalide, ligne {e.lineno}, colonne {e.colno} : {e.msg}.\nL'analyse s'arrête à l'endroit marqué <<ICI>> : {before}<<ICI>>{after}"


def _describeSchemaErrors(value)->str:
    schema = json.loads(Path(__file__).with_name("lesson.schema.json").read_text(encoding="utf-8"))
    errors = sorted(validators.validator_for(schema)(schema).iter_errors(value), key=lambda e: e.json_path)
    lines = [f"- {e.json_path} : {e.message[:200]}" for e in errors[:MAX_SCHEMA_ERRORS]]
    if len(errors) > MAX_SCHEMA_ERRORS:
        lines.append(f"- ... et {len(errors) - MAX_SCHEMA_ERRORS} autre(s) erreur(s).")
    return "Le JSON ne respecte pas le schéma de la leçon :\n" + "\n".join(lines)
