from lib.services.servicemanager import ServiceManager
from lib.log.logger import Logger, OK

#Préchargement, avant la génération, des thèmes, kanji et mots les plus récents de Wakaru (API REST Sylo), injectés
#dans le prompt de l'agent sous forme compacte. Remplace les appels MCP ext__wakaru__list_* que le LLM faisait
#systématiquement : un aller-retour LLM de moins, et des listes réduites à l'essentiel (pas de JSON complet).
#Tri par id décroissant (les plus récents d'abord). Sylo plafonne "limit" à 200 (max_page_size).
#Une erreur d'API lève une exception : échec du bloc, pas de leçon générée sur des données incomplètes.
#
#Paramètres (clé "params" du bloc) :
#  - service      (str, défaut "wakaru") : nom du service Sylo.
#  - themes_limit (int, défaut 50)       : nombre de thèmes chargés.
#  - kanji_limit  (int, défaut 100)      : nombre de kanji chargés.
#  - words_limit  (int, défaut 200)      : nombre de mots chargés.
#  - grammar_limit (int, défaut 200)     : nombre de règles de grammaire chargées.
#  - themes / kanji / words / grammar (str) : clés du contexte où écrire chaque liste (texte).
#  - theme_types  (list[str], optionnel) : types de thème, imposés à tour de rôle (cf. _theme_type).
#  - theme_type   (str, optionnel)       : clé du contexte où écrire le type de thème imposé.
def run(context, params):
    service = ServiceManager.get(params.get("service", "wakaru"))

    themes, themes_total = _list_with_total(service, "themes", params.get("themes_limit", 50))
    kanji = _list(service, "kanjis", params.get("kanji_limit", 100))
    words = _list(service, "words", params.get("words_limit", 200))
    grammar = _list(service, "grammar_rules", params.get("grammar_limit", 200))

    #Thèmes : un par ligne (nom seul, la description serait trop coûteuse)
    context.set(params["themes"], "\n".join(f"- {t['name']}" for t in themes if t.get("name")) or "AUCUN")
    #Kanji : caractères seuls
    context.set(params["kanji"], "、".join(k["char"] for k in kanji if k.get("char")) or "AUCUN")
    #Mots : écriture(lecture), lecture omise quand elle est identique à l'écriture (mot en kana)
    context.set(params["words"], "、".join(_word(w) for w in words if w.get("writing")) or "AUCUN")
    #Règles de grammaire : uid (unique côté API, un doublon fait échouer l'import de la leçon) et nom
    context.set(params["grammar"], "\n".join(f"- {g['uid']} : {g.get('name', '')}" for g in grammar if g.get("uid")) or "AUCUNE")

    #Type de thème imposé : laissé au LLM, le choix retombait toujours sur les mêmes types (saison, plats)
    theme_types = params.get("theme_types") or []
    if theme_types and params.get("theme_type"):
        theme_type = _theme_type(theme_types, themes_total)
        context.set(params["theme_type"], theme_type)
        Logger.write(f"[Wakaru] Preload : type de theme impose = {theme_type}", type=OK)

    Logger.write(f"[Wakaru] Preload : {len(themes)} theme(s), {len(kanji)} kanji, {len(words)} word(s), {len(grammar)} grammar rule(s)", type=OK)
    return True


def _list(service, endpoint:str, limit)->list:
    return service.list(endpoint, order="id.DESC", limit=int(limit)) or []


#Rotation sur le nombre total de thèmes enregistrés dans Wakaru : le type avance d'un cran à chaque leçon
#importée (une génération en échec ne fait pas sauter de type), sans état local à maintenir.
def _theme_type(types:list, total:int)->str:
    return types[total % len(types)]


#Liste + nombre total d'éléments (meta.total), repli sur la taille de la liste si l'API ne le fournit pas
def _list_with_total(service, endpoint:str, limit)->tuple[list, int]:
    response = service.list(endpoint, order="id.DESC", limit=int(limit), with_meta=True) or {}
    items = response.get("data") or []
    total = (response.get("meta") or {}).get("total")
    return items, int(total) if total is not None else len(items)


def _word(w:dict)->str:
    writing, reading = w["writing"], w.get("reading") or ""
    return writing if not reading or reading == writing else f"{writing}({reading})"
