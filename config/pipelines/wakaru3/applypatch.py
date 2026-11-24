import copy
import json
import re

from lib.log.logger import Logger, OK, WARNING

#Application à la leçon des corrections produites par l'audit (profil wakaru-v3-review), sans nouvel appel LLM :
#l'audit renvoie une liste d'opérations au lieu de réécrire toute la leçon.
#
#Format attendu (tableau JSON, [] si aucun problème) :
#  [{"op": "replace|add|remove", "path": "words[4].pos", "problem": "...", "value": ...}, ...]
#  - replace : remplace la valeur existante à "path" ;
#  - add     : insère dans un tableau (indice de destination, ou [-] pour la fin) ou crée une clé d'objet ;
#  - remove  : supprime un élément de tableau ou une clé d'objet.
#Tous les indices désignent la leçon REÇUE par l'audit : les replace et les opérations sur les clés d'objet sont
#appliqués d'abord (ils ne décalent aucun indice), puis les ajouts / suppressions dans les tableaux, regroupés par
#tableau et résolus sur les indices d'origine.
#Une opération invalide (chemin inexistant, champ protégé...) est ignorée et journalisée, les autres sont appliquées.
#La leçon d'origine n'est pas modifiée : la leçon corrigée est écrite en texte, à valider par un bloc JsonFormat.
#
#Paramètres (clé "params" du bloc) :
#  - input  (str) : clé du contexte de la leçon (structure validée), lue seulement.
#  - patch  (str) : clé du contexte de la réponse de l'audit (texte JSON).
#  - output (str) : clé du contexte où écrire la leçon corrigée (texte JSON compact).
#  - report (str, facultatif) : clé du contexte où écrire le compte rendu (opérations appliquées / ignorées).
#Échec du bloc si la réponse de l'audit n'est pas un tableau JSON.
PROTECTED = {"schema_version", "theme.uid", "theme.level", "theme.image", "theme.image_credit", "theme.image_licence", "grammar.uid"}
OPS = ("replace", "add", "remove")
_TOKEN_RE = re.compile(r"\.?([A-Za-z_][A-Za-z0-9_]*)|\[(\d+|-)\]")
_ESCAPE_RE = re.compile(r'\\(.)', re.DOTALL)
_VALID_ESCAPES = '"\\/bfnrtu'
_APPEND = "-"


class PatchError(Exception):
    pass


def run(context, params):
    lesson = copy.deepcopy(context.resolve(params["input"]))
    operations = _parse(context.resolve(params["patch"]))

    applied, skipped = apply(lesson, operations)

    context.set(params["output"], json.dumps(lesson, ensure_ascii=False, separators=(",", ":")))
    if params.get("report"):
        lines = [f"- OK {o['op']} {o['path']} : {o.get('problem', '')}" for o in applied]
        lines += [f"- IGNORÉ {o.get('op')} {o.get('path')} : {reason}" for o, reason in skipped]
        context.set(params["report"], "\n".join(lines) or "AUCUNE OPÉRATION")
    for operation, reason in skipped:
        Logger.write(f"[Wakaru] Patch : operation ignored ({operation.get('op')} {operation.get('path')}) : {reason}", type=WARNING)
    Logger.write(f"[Wakaru] Patch : {len(applied)} operation(s) applied, {len(skipped)} ignored", type=OK)
    return True


#Réponse de l'audit vers liste d'opérations : tolère un bloc de code markdown, du texte autour du tableau et les
#échappements invalides (\') que produisent parfois les LLM
def _parse(text)->list:
    if isinstance(text, list):
        return text
    if not isinstance(text, str):
        raise PatchError("réponse de l'audit absente")
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < start:
        raise PatchError("la réponse de l'audit ne contient pas de tableau JSON")
    text = text[start:end + 1]
    try:
        operations = json.loads(text)
    except json.JSONDecodeError:
        operations = json.loads(_ESCAPE_RE.sub(lambda m: m.group(0) if m.group(1) in _VALID_ESCAPES else m.group(1), text))
    if not isinstance(operations, list):
        raise PatchError("la réponse de l'audit n'est pas un tableau JSON")
    return operations


#Applique les opérations à lesson (modifiée sur place). Renvoie (opérations appliquées, [(opération, raison)] ignorées)
def apply(lesson, operations:list):
    applied, skipped, structural = [], [], []
    for operation in operations:
        try:
            parsed = _check(operation)
        except PatchError as e:
            skipped.append((operation if isinstance(operation, dict) else {"op": None, "path": str(operation)}, str(e)))
            continue
        op, tokens = parsed
        #Les ajouts / suppressions dans un tableau sont différés : ils décaleraient les indices des autres opérations
        if op in ("add", "remove") and isinstance(tokens[-1], (int, str)) and _isIndex(tokens[-1]):
            structural.append((operation, tokens))
            continue
        try:
            _applyDirect(lesson, op, tokens, operation.get("value"))
            applied.append(operation)
        except PatchError as e:
            skipped.append((operation, str(e)))

    #Opérations de tableau regroupées par tableau, résolues sur les indices d'origine
    groups = {}
    for operation, tokens in structural:
        try:
            container = _resolve(lesson, tokens[:-1])
        except PatchError as e:
            skipped.append((operation, str(e)))
            continue
        if not isinstance(container, list):
            skipped.append((operation, "l'emplacement parent n'est pas un tableau"))
            continue
        groups.setdefault(id(container), (container, []))[1].append((operation, tokens[-1]))
    for container, items in groups.values():
        _applyArray(container, items, applied, skipped)
    return applied, skipped


def _isIndex(token)->bool:
    return isinstance(token, int) or token == _APPEND


def _check(operation):
    if not isinstance(operation, dict):
        raise PatchError("opération qui n'est pas un objet")
    op, path = operation.get("op"), operation.get("path")
    if op not in OPS:
        raise PatchError(f"opération inconnue {op!r}")
    if not isinstance(path, str) or not path.strip():
        raise PatchError("chemin absent")
    tokens = _tokens(path.strip())
    if _protected(tokens):
        raise PatchError("champ protégé")
    if op != "remove" and "value" not in operation:
        raise PatchError("valeur absente")
    if op != "add" and tokens[-1] == _APPEND:
        raise PatchError("[-] n'est valable que pour add")
    return op, tokens


#"words[4].composition[0].type" -> ["words", 4, "composition", 0, "type"] ; "[-]" -> "-"
def _tokens(path:str)->list:
    tokens, pos = [], 0
    for m in _TOKEN_RE.finditer(path):
        if m.start() != pos:
            raise PatchError(f"chemin invalide {path!r}")
        if m.group(1) is not None:
            if m.start() == 0 and path.startswith("."):
                raise PatchError(f"chemin invalide {path!r}")
            tokens.append(m.group(1))
        else:
            tokens.append(_APPEND if m.group(2) == _APPEND else int(m.group(2)))
        pos = m.end()
    if pos != len(path) or not tokens or not isinstance(tokens[0], str):
        raise PatchError(f"chemin invalide {path!r}")
    if _APPEND in tokens[:-1]:
        raise PatchError(f"[-] uniquement en fin de chemin {path!r}")
    return tokens


#Champs protégés, et tout ce qu'ils contiennent ; remplacer un de leurs parents (ex. "theme") est aussi refusé
def _protected(tokens:list)->bool:
    keys = [t for t in tokens if isinstance(t, str) and t != _APPEND]
    dotted = ".".join(keys) if len(keys) == len(tokens) else None
    for field in PROTECTED:
        if dotted is not None and (dotted == field or dotted.startswith(field + ".") or field.startswith(dotted + ".")):
            return True
    return False


def _resolve(lesson, tokens:list):
    node = lesson
    for token in tokens:
        if isinstance(token, int):
            if not isinstance(node, list) or not 0 <= token < len(node):
                raise PatchError("indice inexistant")
        elif not isinstance(node, dict) or token not in node:
            raise PatchError(f"clé {token!r} inexistante")
        node = node[token]
    return node


#replace (toute cible), add / remove d'une clé d'objet
def _applyDirect(lesson, op:str, tokens:list, value):
    if len(tokens) == 1 and op != "replace":
        raise PatchError("ajout ou suppression d'une section entière interdit")
    parent = _resolve(lesson, tokens[:-1])
    key = tokens[-1]
    if op == "replace":
        _resolve(parent, [key])
        parent[key] = value
    elif not isinstance(parent, dict):
        raise PatchError("l'emplacement parent n'est pas un objet")
    elif op == "add":
        if key in parent:
            raise PatchError(f"clé {key!r} déjà présente (utiliser replace)")
        parent[key] = value
    else:
        if key not in parent:
            raise PatchError(f"clé {key!r} inexistante")
        del parent[key]


#Reconstruit le tableau : chaque élément d'origine précédé des insertions à son indice, sauf s'il est supprimé ;
#les ajouts en fin ([-] ou indice = longueur) en dernier
def _applyArray(container:list, items:list, applied:list, skipped:list):
    size = len(container)
    removed, inserts, appends = set(), {}, []
    for operation, index in items:
        op = operation["op"]
        if op == "remove":
            if index == _APPEND or not 0 <= index < size:
                skipped.append((operation, "indice inexistant"))
            elif index in removed:
                skipped.append((operation, "élément déjà supprimé"))
            else:
                removed.add(index)
                applied.append(operation)
        elif index == _APPEND or index == size:
            appends.append(operation["value"])
            applied.append(operation)
        elif 0 <= index < size:
            inserts.setdefault(index, []).append(operation["value"])
            applied.append(operation)
        else:
            skipped.append((operation, "indice inexistant"))
    rebuilt = []
    for i, element in enumerate(container):
        rebuilt.extend(inserts.get(i, []))
        if i not in removed:
            rebuilt.append(element)
    rebuilt.extend(appends)
    container[:] = rebuilt
