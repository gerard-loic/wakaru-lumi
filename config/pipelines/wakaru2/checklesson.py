import importlib.util
import json
import re
from pathlib import Path

from lib.log.logger import Logger, WARNING

#Contrôle de la leçon (schéma 2.0) par dictionnaire (KANJIDIC2 / JMdict, cf. jdict.py), sans LLM.
#  - Ce qui est certain est corrigé directement : traits, radical, grade, Unicode et niveau JLPT du kanji,
#    lectures inexistantes du kanji, type on/kun, écriture (katakana/hiragana) et rendaku des segments de
#    "composition".
#  - Le reste est rapporté sous forme de liste de problèmes, transmise à l'audit : mots introuvables dans le
#    dictionnaire, kanji non autorisés, composition ou tokens incohérents, japonais dans les traductions,
#    exercices mal construits.
#
#Paramètres (clé "params" du bloc) :
#  - input     (str) : clé du contexte de la leçon (structure validée), mise à jour.
#  - input_txt (str) : clé du contexte du texte JSON de la leçon, mis à jour (utilisé par les prompts suivants).
#  - issues    (str) : clé du contexte où écrire la liste des problèmes (texte, "AUCUN" si aucun).
KANJI_RE = re.compile(r"[一-鿿]")
JAPANESE_RE = re.compile(r"[぀-ヿ一-鿿]")
PUNCTUATION_RE = re.compile(r"^[。、！？!?.,「」『』（）()…・ー〜~\s]+$")
#Liste de référence des prompts wakaru-v2 : kanji autorisés (avec celui de la leçon) hors "writing" des mots
N5_KANJI = set("一九七十人二入八下口三山子女小上千川大土円火月五手水中天日木六右左四出生白本目立気休耳先年百名花見車足男雨学金空校万午今少父分友外古半母北会行西多毎何言社来国長店東後食前南高時書魚週間道買新電話語読聞安飲駅")
#Ancien niveau JLPT (KANJIDIC2, 4 à 1) vers le niveau actuel, quand la correspondance est univoque
JLPT_OLD_TO_NEW = {"4": "N5", "3": "N4"}
#Formes -ます vers forme du dictionnaire (verbes du groupe 1) : dernière syllabe de la base en -i vers -u
GODAN = dict(zip("いきぎしちにびみり", "うくぐすつぬぶむる"))
#Rendaku : syllabe initiale voisée vers sa forme non voisée
DEVOICE = dict(zip("がぎぐげござじずぜぞだぢづでどばびぶべぼぱぴぷぺぽ", "かきくけこさしすせそたちつてとはひふへほはひふへほ"))
#Catégories grammaticales absentes ou mal couvertes par JMdict : pas de recherche dans le dictionnaire
NO_LOOKUP_POS = {"particle", "copula", "expression"}
VOCABULARY_COUNT = 10


def _jdict():
    spec = importlib.util.spec_from_file_location("wakaru2_jdict", Path(__file__).with_name("jdict.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.load()


def toHiragana(text:str)->str:
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in text)


def toKatakana(text:str)->str:
    return "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in text)


def compact(text:str)->str:
    return re.sub(r"\s+", "", text)


def run(context, params):
    lesson = context.resolve(params["input"])
    index = _jdict()
    issues = []
    _checkKanji(lesson, index, issues)
    _checkWords(lesson, index, issues)
    _checkSentences(lesson, issues)
    _checkKanjiUsage(lesson, issues)
    _checkTranslations(lesson, issues)
    _checkExercises(lesson, issues)

    context.set(params["input"], lesson)
    context.set(params["input_txt"], json.dumps(lesson, ensure_ascii=False, indent=2))
    context.set(params["issues"], "\n".join(f"- {issue}" for issue in issues) if issues else "AUCUN")
    if issues:
        Logger.write(f"[Wakaru] Dictionary check : {len(issues)} issue(s) : {' '.join(issues)}", type=WARNING)
    return True


def _checkKanji(lesson, index, issues):
    kanji = lesson["kanji"]
    char = kanji["char"]
    if char not in N5_KANJI:
        issues.append(f"kanji.char : « {char} » n'est pas dans la liste des kanji N5.")
    info = index["kanji"].get(char)
    if info is None:
        issues.append(f"kanji.char : « {char} » est introuvable dans KANJIDIC2.")
        return
    kanji["strokes"] = info["strokes"]
    kanji["unicode"] = f"U+{ord(char):04X}"
    if info["radical"]:
        kanji["radical"] = info["radical"]
    if info["grade"]:
        kanji["grade"] = int(info["grade"])
    if info["jlpt_old"] in JLPT_OLD_TO_NEW:
        kanji["jlpt"] = JLPT_OLD_TO_NEW[info["jlpt_old"]]
    kanji["readings"]["on"] = _keepKnown(kanji["readings"]["on"], info["on"], toKatakana)
    kanji["readings"]["kun"] = _keepKnown(kanji["readings"]["kun"], info["kun"], toHiragana)
    if not kanji["readings"]["on"] and not kanji["readings"]["kun"]:
        issues.append(f"kanji.readings : aucune lecture connue de {char} (on : {'、'.join(info['on'])} ; kun : {'、'.join(info['kun'])}).")


#Ne garde que les lectures connues du dictionnaire, écrites sous la forme du dictionnaire (ex : た.べる)
def _keepKnown(readings:list, known:list, script)->list:
    by_key = {k.replace(".", "").replace("-", ""): k for k in known}
    kept = []
    for reading in readings:
        key = script(reading).replace(".", "").replace("-", "")
        if key in by_key and by_key[key] not in kept:
            kept.append(by_key[key])
    return kept


#Formes candidates du dictionnaire pour un mot (verbes en -ます, mots suivis de です)
def _lemmas(word:str)->list:
    word = compact(word)
    candidates = [word]
    if word.endswith("です"):
        candidates.append(word[:-2])
    suffix = next((s for s in ("ませんでした", "ました", "ません", "ます") if word.endswith(s)), None)
    if suffix:
        stem = word[:-len(suffix)]
        candidates.append(stem + "る")
        if stem and stem[-1] in GODAN:
            candidates.append(stem[:-1] + GODAN[stem[-1]])
        if stem in ("き", "来"):
            candidates.append(stem + "る" if stem == "来" else "くる")
        if stem.endswith("し"):
            candidates += [stem[:-1] + "する", stem[:-1]]
    return candidates


def _checkWords(lesson, index, issues):
    char = lesson["kanji"]["char"]
    seen = set()
    for i, word in enumerate(lesson["words"]):
        location = f"words[{i}]"
        writing, reading = compact(word["writing"]), toHiragana(compact(word["reading"]))
        if writing in seen:
            issues.append(f"{location} : « {word['writing']} » figure plusieurs fois dans \"words\" ; garde une seule entrée.")
        seen.add(writing)

        if word["pos"] not in NO_LOOKUP_POS:
            if len(_lemmas(writing)) > 1:
                issues.append(f"{location}.writing : « {word['writing']} » n'est pas la forme du dictionnaire (pas de -ます ni de です collé).")
            pairs = list(zip(_lemmas(writing), _lemmas(reading)))
            found = [(w, r) for w, r in pairs if w in index["words"]]
            if not found:
                issues.append(f"{location}.writing : « {word['writing']} » est introuvable dans le dictionnaire JMdict (mot inexistant ou mal orthographié ?).")
            elif not any(r in {toHiragana(k) for k in index["words"][w]} for w, r in found):
                readings = index["words"][found[0][0]]
                issues.append(f"{location}.reading : « {word['reading']} » n'est pas une lecture de « {word['writing']} » (lectures connues : {'、'.join(sorted(readings))}).")

        if word["role"] == "kanji_example":
            if char not in writing:
                issues.append(f"{location} : le mot « {word['writing']} » (rôle kanji_example) ne contient pas le kanji de la leçon {char}.")
            others = sorted({c for c in KANJI_RE.findall(writing) if c not in N5_KANJI and c != char})
            if others:
                issues.append(f"{location} : « {word['writing']} » (rôle kanji_example) contient un kanji hors N5 ({'、'.join(others)}) : retire ce mot.")

        if "composition" in word:
            if not KANJI_RE.search(writing) and "々" not in writing:
                issues.append(f"{location}.composition : « {word['writing']} » est écrit entièrement en kana, \"composition\" doit être absente.")
            else:
                _checkComposition(word, writing, reading, index, location, issues)

    vocabulary = sum(1 for word in lesson["words"] if word["role"] == "vocabulary")
    if vocabulary != VOCABULARY_COUNT:
        issues.append(f"words : {vocabulary} mot(s) de rôle \"vocabulary\" au lieu de {VOCABULARY_COUNT}.")


def _checkComposition(word, writing, reading, index, location, issues):
    composition = word["composition"]
    texts = "".join(part["text"] for part in composition)
    if texts != writing:
        issues.append(f"{location}.composition : la concaténation des segments ({texts}) ne redonne pas « {word['writing']} » ; décris chaque segment du mot, dans l'ordre.")
        return
    for j, part in enumerate(composition):
        if part["type"] in ("on", "kun") and len(part["text"]) == 1 and KANJI_RE.match(part["text"]):
            _checkSegment(part, index["kanji"].get(part["text"]), f"{location}.composition[{j}]", issues)
    readings = "".join(toHiragana(part["reading"]) for part in composition)
    if readings != reading:
        issues.append(f"{location}.composition : les lectures des segments ({'、'.join(p['reading'] for p in composition)}) ne redonnent pas la lecture du mot « {word['reading']} » ; indique la lecture de chaque segment dans CE mot.")


#Lecture d'un kanji dans un mot : type on (katakana) ou kun (hiragana), cohérent avec KANJIDIC2.
#Tolère la gémination finale (がく → がっ) ; le rendaku (きゅう → ぎゅう) est reporté dans "rendaku".
def _checkSegment(part, info, location, issues):
    if info is None:
        return
    reading = toHiragana(part["reading"])
    on = {toHiragana(r) for r in info["on"]}
    kun = {r.split(".")[0].replace("-", "") for r in info["kun"]}

    def match(candidates):
        devoiced = DEVOICE.get(reading[:1], reading[:1]) + reading[1:]
        for variant, voiced in ((reading, False), (devoiced, True)):
            if variant == reading and voiced:
                continue
            if any(variant == c or (variant.endswith("っ") and c.startswith(variant[:-1]) and len(c) == len(variant)) for c in candidates):
                return voiced
        return None

    kind = part["type"]
    voiced = match(on if kind == "on" else kun)
    if voiced is None:
        other = "kun" if kind == "on" else "on"
        voiced = match(on if other == "on" else kun)
        if voiced is None:
            issues.append(f"{location} : « {part['reading']} » n'est pas une lecture connue de {part['text']} (on : {'、'.join(info['on'])} ; kun : {'、'.join(info['kun'])}).")
            return
        kind = other
    part["type"] = kind
    part["reading"] = toKatakana(reading) if kind == "on" else reading
    if voiced:
        part["rendaku"] = True
    else:
        part.pop("rendaku", None)


#Identifiant d'un mot : écriture et lecture, ex. 紅葉:もみじ
def wordUid(word)->str:
    return f"{compact(word['writing'])}:{toHiragana(compact(word['reading']))}"


#Mots de "words" auxquels correspond un token, par ordre de priorité : écriture, lecture, puis lemme
def _tokenWords(token, words)->list:
    text, lemma = compact(token["text"]), compact(token.get("lemma", ""))
    for form in (text, lemma):
        if not form:
            continue
        for key in ("writing", "reading"):
            found = [w for w in words if toHiragana(compact(w[key])) == toHiragana(form)]
            if found:
                return found
    return []


def _checkSentences(lesson, issues):
    for i, sentence in enumerate(lesson["sentences"]):
        tokens = "".join(compact(token["text"]) for token in sentence["tokens"])
        if tokens != compact(sentence["text"]):
            issues.append(f"sentences[{i}].tokens : la concaténation des tokens ({tokens}) ne redonne pas la phrase « {sentence['text']} ».")
        for j, token in enumerate(sentence["tokens"]):
            text = compact(token["text"])
            if PUNCTUATION_RE.match(text):
                token.pop("word_uid", None)
                continue
            if KANJI_RE.search(text) and "reading" not in token:
                issues.append(f"sentences[{i}].tokens[{j}] : le token « {token['text']} » contient un kanji mais n'a pas de \"reading\".")
            words = _tokenWords(token, lesson["words"])
            reading = toHiragana(compact(token.get("reading", "")))
            words = [w for w in words if toHiragana(compact(w["reading"])) == reading] or words
            uids = list(dict.fromkeys(wordUid(w) for w in words))
            if not uids:
                token.pop("word_uid", None)
                issues.append(f"sentences[{i}].tokens[{j}] : le token « {token['text']} » ne correspond à aucun mot de \"words\" (ajoute le mot, rôle \"support\" si nécessaire, ou corrige \"lemma\").")
            elif len(uids) == 1:
                token["word_uid"] = uids[0]
            elif token.get("word_uid") not in uids:
                token.pop("word_uid", None)
                issues.append(f"sentences[{i}].tokens[{j}] : le token « {token['text']} » correspond à plusieurs mots de \"words\" ({'、'.join(uids)}) ; écris-le en kanji avec \"reading\" ou précise \"lemma\".")


#Kanji autorisés hors "writing" des mots : kanji de la leçon et liste N5
def _checkKanjiUsage(lesson, issues):
    allowed = N5_KANJI | {lesson["kanji"]["char"]}
    texts = [(f"sentences[{i}].text", s["text"]) for i, s in enumerate(lesson["sentences"])]
    texts += [(f"words[{i}].examples[{j}].text", e["text"])
              for i, w in enumerate(lesson["words"]) for j, e in enumerate(w.get("examples", []))]
    for i, exercise in enumerate(lesson["exercises"]):
        texts += [(f"exercises[{i}].choices", " ".join(exercise.get("choices", [])))]
        texts += [(f"exercises[{i}].items[{j}]", item["prompt"] + " " + " ".join(a for b in item["blanks"] for a in b["answers"]))
                  for j, item in enumerate(exercise["items"])]
    for location, text in texts:
        forbidden = sorted({c for c in KANJI_RE.findall(text) if c not in allowed})
        if forbidden:
            issues.append(f"{location} : kanji hors N5 {'、'.join(forbidden)} (seuls le kanji de la leçon et les kanji N5 sont autorisés) : écris ces mots en kana.")


def _checkTranslations(lesson, issues):
    texts = [(f"words[{i}].translations", t) for i, w in enumerate(lesson["words"]) for t in w["translations"]]
    texts += [(f"words[{i}].examples[{j}].translation", e["translation"])
              for i, w in enumerate(lesson["words"]) for j, e in enumerate(w.get("examples", []))]
    texts += [(f"sentences[{i}].translation", s["translation"]) for i, s in enumerate(lesson["sentences"])]
    texts += [(f"kanji.translations", t) for t in lesson["kanji"]["translations"]]
    texts += [(f"exercises[{i}].items[{j}].translation", item["translation"])
              for i, e in enumerate(lesson["exercises"]) for j, item in enumerate(e["items"])]
    for location, text in texts:
        if JAPANESE_RE.search(text):
            issues.append(f"{location} : la traduction « {text} » contient du japonais ; elle doit être entièrement en français.")


#Items : un seul trou {{1}}, réponses présentes dans "choices", phrase complétée égale à une phrase de "sentences"
def _checkExercises(lesson, issues):
    sentences = {compact(s["text"]) for s in lesson["sentences"]}
    for i, exercise in enumerate(lesson["exercises"]):
        choices = exercise.get("choices", [])
        answers = []
        for j, item in enumerate(exercise["items"]):
            location = f"exercises[{i}].items[{j}]"
            holes = re.findall(r"\{\{([0-9]+)\}\}", item["prompt"])
            keys = [b["key"] for b in item["blanks"]]
            if holes != ["1"] or keys != [1]:
                issues.append(f"{location} : le \"prompt\" doit contenir exactement un trou {{{{1}}}} et \"blanks\" une seule entrée de \"key\" 1.")
                continue
            item_answers = item["blanks"][0]["answers"]
            answers.append(item_answers[0])
            missing = [a for a in item_answers if choices and a not in choices]
            if missing:
                issues.append(f"{location}.blanks[0].answers : {'、'.join(missing)} absent(s) de \"choices\".")
            completed = compact(item["prompt"].replace("{{1}}", item_answers[0]))
            if completed not in sentences:
                issues.append(f"{location} : la phrase complétée « {item['prompt'].replace('{{1}}', item_answers[0])} » ne correspond à aucune phrase de \"sentences\".")
        if answers and len(set(answers)) < 3:
            issues.append(f"exercises[{i}] : les réponses ne couvrent que {len(set(answers))} valeur(s) différente(s) ({'、'.join(sorted(set(answers)))}) ; il en faut au moins 3.")
