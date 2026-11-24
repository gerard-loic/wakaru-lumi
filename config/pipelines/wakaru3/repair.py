import json
import re
from pathlib import Path

from lib.log.logger import Logger, OK, WARNING

#Réparation, sans LLM, des valeurs à dictionnaire invalides de la leçon générée, avant sa validation par le schéma
#(bloc JsonFormat) : une seule valeur hors liste ferait sinon rejeter toute la génération.
#Les listes de valeurs autorisées sont lues dans lesson.schema.json (pas de copie à maintenir).
#Pour chaque champ à dictionnaire, dans l'ordre :
#  1. normalisation (casse, espaces, tirets : "Verb-Godan" -> verb_godan) et synonymes courants ;
#  2. règle propre au champ (taught_form : forme en -ます -> polite, autre forme japonaise -> dictionary) ;
#  3. valeur toujours invalide : clé retirée si elle est facultative, laissée sinon (le schéma la rejettera).
#Chaque réparation est consignée et transmise à l'audit, pour vérification.
#Si le texte n'est pas du JSON lisible, il est laissé tel quel : le bloc JsonFormat suivant signalera l'erreur.
#
#Paramètres (clé "params" du bloc) :
#  - input   (str) : clé du contexte du texte JSON de la leçon, réécrit (JSON compact) si une réparation a eu lieu.
#  - repairs (str) : clé du contexte où écrire la liste des réparations (texte, "AUCUNE" si aucune).
SCHEMA_PATH = Path(__file__).with_name("lesson.schema.json")
#Champs à dictionnaire : chemin dans la leçon ([] = chaque élément d'un tableau), chemin dans le schéma, facultatif
FIELDS = [
    ("words[].role", False),
    ("words[].taught_form", True),
    ("words[].pos", False),
    ("words[].composition[].type", False),
    ("grammar.category", False),
    ("sentences[].tokens[].role", True),
    ("exercises[].category", False),
    ("exercises[].type", False),
]
SYNONYMS = {
    "i_adj": "i_adjective", "adjective_i": "i_adjective", "i_adjectif": "i_adjective",
    "na_adj": "na_adjective", "adjective_na": "na_adjective", "na_adjectif": "na_adjective",
    "godan": "verb_godan", "ichidan": "verb_ichidan", "irregular": "verb_irregular",
    "verb_group1": "verb_godan", "verb_group2": "verb_ichidan", "verb_group3": "verb_irregular",
    "number": "numeral", "particule": "particle", "nom": "noun", "adverbe": "adverb",
    "onyomi": "on", "kunyomi": "kun", "on_yomi": "on", "kun_yomi": "kun", "okurigana": "kana",
    "hiragana": "kana", "katakana": "kana",
    "dictionary_form": "dictionary", "plain": "dictionary", "masu": "polite", "masu_form": "polite",
    "fill_in": "fill", "fill_in_the_blank": "fill", "blank": "fill",
}
POLITE_RE = re.compile(r"(ます|ません|ました|ませんでした|ましょう)$")
JAPANESE_RE = re.compile(r"[぀-ヿ一-鿿]")
_ESCAPE_RE = re.compile(r'\\(.)', re.DOTALL)
_VALID_ESCAPES = '"\\/bfnrtu'


def run(context, params):
    context.set(params["repairs"], "AUCUNE")
    lesson = _parse(context.resolve(params["input"]))
    if not isinstance(lesson, dict):
        return True

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    repairs = []
    for path, optional in FIELDS:
        allowed = _enum(schema, path)
        if not allowed:
            continue
        for location, parent, key in _targets(lesson, path):
            _repair(parent, key, location, allowed, optional, repairs)

    if repairs:
        context.set(params["input"], json.dumps(lesson, ensure_ascii=False, separators=(",", ":")))
        context.set(params["repairs"], "\n".join(f"- {r}" for r in repairs))
        Logger.write(f"[Wakaru] Repair : {len(repairs)} value(s) repaired : {' '.join(repairs)}", type=WARNING)
    else:
        Logger.write("[Wakaru] Repair : nothing to repair", type=OK)
    return True


#Même tolérance que JsonFormat (échappements invalides), plus un éventuel texte ou bloc markdown autour de l'objet
def _parse(text):
    if not isinstance(text, str):
        return None
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        return None
    text = text[start:end + 1]
    for candidate in (text, _ESCAPE_RE.sub(lambda m: m.group(0) if m.group(1) in _VALID_ESCAPES else m.group(1), text)):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


#Valeurs autorisées d'un champ, lues dans le schéma ("words[].pos" -> properties.words.items.properties.pos.enum)
def _enum(schema, path:str):
    node = schema
    for part in path.split("."):
        name, is_array = part.removesuffix("[]"), part.endswith("[]")
        node = node.get("properties", {}).get(name, {})
        if is_array:
            node = node.get("items", {})
    return node.get("enum")


#(emplacement lisible, objet parent, clé) de chaque occurrence présente du champ
def _targets(lesson, path:str):
    parts = path.split(".")
    nodes = [("", lesson)]
    for part in parts[:-1]:
        name, is_array = part.removesuffix("[]"), part.endswith("[]")
        next_nodes = []
        for location, node in nodes:
            value = node.get(name) if isinstance(node, dict) else None
            prefix = f"{location}.{name}" if location else name
            if is_array and isinstance(value, list):
                next_nodes += [(f"{prefix}[{i}]", v) for i, v in enumerate(value) if isinstance(v, dict)]
            elif not is_array and isinstance(value, dict):
                next_nodes.append((prefix, value))
        nodes = next_nodes
    key = parts[-1]
    return [(f"{location}.{key}" if location else key, node, key) for location, node in nodes if key in node]


def _repair(parent:dict, key:str, location:str, allowed:list, optional:bool, repairs:list):
    value = parent[key]
    if value in allowed:
        return
    fixed = None
    if isinstance(value, str):
        normalized = re.sub(r"[\s\-]+", "_", value.strip().lower())
        normalized = SYNONYMS.get(normalized, normalized)
        if normalized in allowed:
            fixed = normalized
        elif key == "taught_form" and JAPANESE_RE.search(value):
            fixed = "polite" if POLITE_RE.search(value.strip()) else "dictionary"
    if fixed is None and len(allowed) == 1:
        fixed = allowed[0]

    if fixed is not None:
        parent[key] = fixed
        repairs.append(f"{location} : valeur « {value} » hors dictionnaire remplacée par « {fixed} » (réparation automatique, à vérifier).")
    elif optional:
        del parent[key]
        repairs.append(f"{location} : valeur « {value} » hors dictionnaire retirée (clé facultative ; à remettre avec une valeur valide si nécessaire : {', '.join(allowed)}).")
    else:
        Logger.write(f"[Wakaru] Repair : {location} : unrepairable value {value!r}", type=WARNING)
