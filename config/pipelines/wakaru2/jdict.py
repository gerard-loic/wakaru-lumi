import gzip
import pickle
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

#Dictionnaires japonais de l'EDRDG (licence CC BY-SA 4.0, https://www.edrdg.org/edrdg/licence.html) :
#  - KANJIDIC2 : informations sur les kanji (traits, radical, grade, ancien niveau JLPT, lectures) ;
#  - JMdict_e  : mots (écritures en kanji et lectures en kana).
#Les fichiers sont téléchargés une fois, puis réduits en un index compact (pickle) dans INDEX_PATH,
#reconstruit uniquement s'il est absent.
KANJIDIC_URL = "http://www.edrdg.org/kanjidic/kanjidic2.xml.gz"
JMDICT_URL = "http://ftp.edrdg.org/pub/Nihongo/JMdict_e.gz"
INDEX_PATH = Path("storage/data/jdict/jdict.pickle")
#Radicaux classiques (Kangxi) : le n-ième radical est le caractère U+2F00 + n - 1, normalisé en NFKC
_KANGXI_BASE = 0x2F00


def load()->dict:
    if not INDEX_PATH.is_file():
        build()
    with INDEX_PATH.open("rb") as f:
        return pickle.load(f)


#Index produit :
#  - kanji : {caractère: {"strokes", "grade", "jlpt_old", "radical", "on", "kun"}}
#  - words : {écriture: set(lectures en kana)}, les mots écrits en kana sont indexés par leur lecture elle-même
def build():
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    index = {"kanji": _buildKanji(_download(KANJIDIC_URL)), "words": _buildWords(_download(JMDICT_URL))}
    tmp = INDEX_PATH.with_suffix(".tmp")
    with tmp.open("wb") as f:
        pickle.dump(index, f)
    tmp.replace(INDEX_PATH)


def _download(url:str)->bytes:
    with urllib.request.urlopen(url, timeout=120) as response:
        return gzip.decompress(response.read())


def _buildKanji(data:bytes)->dict:
    kanji = {}
    for character in ET.fromstring(data).iter("character"):
        misc = character.find("misc")
        radical = character.find("radical/rad_value[@rad_type='classical']")
        readings = character.findall("reading_meaning/rmgroup/reading")
        kanji[character.findtext("literal")] = {
            "strokes": int(misc.findtext("stroke_count")),
            "grade": misc.findtext("grade"),
            "jlpt_old": misc.findtext("jlpt"),
            "radical": unicodedata.normalize("NFKC", chr(_KANGXI_BASE + int(radical.text) - 1)) if radical is not None else None,
            "on": [r.text for r in readings if r.get("r_type") == "ja_on"],
            "kun": [r.text for r in readings if r.get("r_type") == "ja_kun"],
        }
    return kanji


def _buildWords(data:bytes)->dict:
    words = {}
    #JMdict déclare des entités DTD (&v5r; ...) qu'ElementTree ne résout pas : on les neutralise
    data = data.split(b"]>", 1)[1] if data.lstrip().startswith(b"<?xml") or b"<!DOCTYPE" in data[:2000] else data
    data = data.replace(b"&", b"")
    for entry in ET.fromstring(data).iter("entry"):
        readings = {r.text for r in entry.iter("reb")}
        for reading in readings:
            words.setdefault(reading, set()).add(reading)
        for writing in entry.iter("keb"):
            words.setdefault(writing.text, set()).update(readings)
    return words
