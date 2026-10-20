"""Which folder the agent works in, and switching it by voice.

The agent runs in one directory (assistant_dir). Building an application by
talking means moving between a handful of them, so the last few are kept, the
tray lists them, and a spoken command switches without a click:

  "proje snapcode'a geç", "snapcode projesini aç", "switch to project snapcode"
  "yeni proje hava durumu oluştur", "create a new project weather app"

The name is matched against the remembered folders and every folder directly
under the project roots (the desktop, Documents, source\\repos and ~/projects
unless set), loosely: speech models spell "SnapCode" as "snap code" as often as
not, and Turkish letters come back folded or not depending on the model.
"""

import difflib
import os
import re
import unicodedata

MAX_RECENT = 8

# Turkish suffixes ride on the name ("snapcode'a", "snapcode'ya", "projesine"),
# so the patterns take the name up to an optional apostrophe-suffix.
_SUFFIX = r"(?:['’](?:y?[ae]|n[ae]|y?[ıiuü]|n?[ıiuü]n?))?"
_SWITCH = [
    re.compile(p, re.IGNORECASE) for p in (
        rf"^(?:proje(?:yi|ye|si)?|klasör(?:e|ü)?)\s+(?P<name>.+?){_SUFFIX}\s+"
        r"(?:geç|gec|aç|ac|değiştir|degistir|yap|seç|sec)\b",
        rf"^(?P<name>.+?)\s+(?:projesi(?:ne|ni)?|klasörü(?:ne|nü)?){_SUFFIX}\s+"
        r"(?:geç|gec|aç|ac|seç|sec)\b",
        r"^(?:switch|change|go)\s+to\s+(?:the\s+)?(?:project|folder)\s+(?P<name>.+)$",
        r"^(?:open|use)\s+(?:the\s+)?(?:project|folder)\s+(?P<name>.+)$",
    )
]
_CREATE = [
    re.compile(p, re.IGNORECASE) for p in (
        r"^yeni\s+(?:bir\s+)?proje\s+(?P<name>.+?)\s+(?:oluştur|olustur|aç|ac|başlat|baslat)\b",
        r"^(?P<name>.+?)\s+(?:adında|adlı|isimli)\s+(?:yeni\s+)?(?:bir\s+)?proje\s+"
        r"(?:oluştur|olustur|aç|ac|başlat|baslat)\b",
        r"^(?:create|start|make)\s+(?:a\s+)?new\s+project\s+(?:called\s+|named\s+)?(?P<name>.+)$",
    )
]


def default_roots():
    home = os.path.expanduser("~")
    return [os.path.join(home, "Desktop"), os.path.join(home, "Documents"),
            os.path.join(home, "source", "repos"), os.path.join(home, "projects")]


def roots(conf):
    listed = [line.strip() for line in conf["projects_roots"].splitlines()]
    chosen = [os.path.expanduser(p) for p in listed if p] or default_roots()
    return [p for p in chosen if os.path.isdir(p)]


def recent(conf):
    """The remembered folders that still exist, newest first."""
    stored = conf["assistant_recent_dirs"]
    if not isinstance(stored, list):
        return []
    return [p for p in stored if isinstance(p, str) and os.path.isdir(p)]


def remember(conf, path):
    path = os.path.normpath(os.path.expanduser(path))
    rest = [p for p in recent(conf) if os.path.normcase(p) != os.path.normcase(path)]
    conf["assistant_recent_dirs"] = [path] + rest[:MAX_RECENT - 1]


def use(conf, path):
    """Make `path` the agent's folder and remember it; the caller saves."""
    path = os.path.normpath(os.path.expanduser(path))
    conf["assistant_dir"] = path
    remember(conf, path)
    return path


def current(conf):
    wanted = conf["assistant_dir"].strip()
    return os.path.expanduser(wanted) if wanted else ""


def label(path):
    """A folder's own name, for menus and spoken answers."""
    return os.path.basename(os.path.normpath(path)) or path


def _fold(text):
    text = text.lower().replace("ı", "i")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", text)


def candidates(conf):
    """Every folder a spoken name may mean, remembered ones first."""
    seen, out = set(), []
    for path in recent(conf):
        key = os.path.normcase(path)
        if key not in seen:
            seen.add(key)
            out.append(path)
    for root in roots(conf):
        try:
            entries = sorted(os.scandir(root), key=lambda e: e.name.lower())
        except OSError:
            continue
        for entry in entries:
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            key = os.path.normcase(entry.path)
            if key not in seen:
                seen.add(key)
                out.append(entry.path)
    return out


def find(conf, spoken):
    """The folder a spoken name most likely means, or ""."""
    wanted = _fold(spoken)
    if not wanted:
        return ""
    best, best_score = "", 0.0
    for path in candidates(conf):
        name = _fold(os.path.basename(path))
        if not name:
            continue
        if name == wanted:
            return path
        score = difflib.SequenceMatcher(None, wanted, name).ratio()
        if wanted in name or name in wanted:
            score = max(score, 0.85)
        if score > best_score:
            best, best_score = path, score
    return best if best_score >= 0.75 else ""


def _clean(text):
    return text.strip().strip(".!?,;:").strip()


def parse(text):
    """("switch" | "create", spoken name) for a project command, or None."""
    text = _clean(text)
    for kind, patterns in (("switch", _SWITCH), ("create", _CREATE)):
        for pattern in patterns:
            found = pattern.match(text)
            if found:
                name = _clean(found.group("name"))
                name = re.sub(r"^(?:the|a)\s+", "", name, flags=re.IGNORECASE)
                if name:
                    return kind, name
    return None


def folder_name(spoken):
    """A folder name made from a spoken one: "Hava Durumu" -> "hava-durumu"."""
    text = spoken.lower().replace("ı", "i")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def create(conf, spoken):
    """A new folder under the first project root, made the agent's folder."""
    name = folder_name(spoken)
    if not name:
        raise ValueError(spoken)
    base = (roots(conf) or [os.path.expanduser("~")])[0]
    path = os.path.join(base, name)
    os.makedirs(path, exist_ok=True)
    return use(conf, path)
