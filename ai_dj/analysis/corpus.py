"""Corpus scan: discover WAV/MP3 files and derive track identities (D8).

Identity model (from DECISIONS.md D8):

* ``recording_id`` identifies one specific recording. It is derived from the
  SHA-256 of the file's bytes, so two byte-identical files share a
  ``recording_id`` and are reported as duplicates. Two different encodes of the
  same take get different ids (fingerprint-based dedup is out of P0 scope).
* ``work_id`` identifies the musical work (the song). Many performers can cover
  the same song -> one ``work_id``, many ``recording_id``\\ s.

``work_id`` derivation heuristic (deterministic, documented, no guessing):

1.  Take the filename stem (directories are never used — the metadata comes
    from the file's own name).
2.  Transliterate to ASCII: NFKD decomposition, drop combining marks, and map
    Vietnamese ``đ``/``Đ`` to ``d`` (NFKD leaves it intact).
3.  Remove parenthesised/bracketed groups ``(...)`` ``[...]`` — in this corpus
    they hold cover/artist/composer markers, e.g. ``(Cover Duy)``,
    ``(DUY-COVER)``, ``(Sáng tác - Hoàng Duy)``, and dedup markers ``(1)``.
4.  Tokenise on non-alphanumeric characters, then per token repeatedly strip a
    glued format suffix (``wav``/``mp3``) so ``"Duywav" -> "duy"``.
5.  Drop stopword tokens that describe the *recording*, not the *work*:
    cover markers (``cover``), performer tags seen across the corpus
    (``duy``), genre tags (``bolero``), master markers (``goc`` = gốc,
    ``mixdown``, ``original``, ``version``), and composer prose
    (``sang``, ``tac`` — "sáng tác").
6.  If every token was dropped, fall back to the full token list (minus format
    tokens) — never merge an unknown title into another work.
7.  ``work_id = "work-" + sha1(normalized_title)[:12]`` and the human-readable
    ``normalized_title`` is stored for audit.

Consequences, deliberately conservative: spelling variants that survive the
fold (different artist names left in the title, extra prose) produce distinct
``work_id``\\ s — we never merge works we are unsure about.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

import soundfile as sf

AUDIO_EXTENSIONS = {".wav", ".mp3"}

# Tokens that describe a recording's provenance, not the underlying work.
# Removing them groups "ĐOẠN TUYỆT - COVER -DUY" and "ĐOẠN TUYỆT (DUY COVER)".
_STOPWORDS = {
    "cover",
    "duy",
    "bolero",
    "goc",  # "gốc" — the original/master take
    "mixdown",
    "original",
    "version",
    "sang",  # "sáng tác" — composer credit
    "tac",
    "ok",
}

_FORMAT_TOKENS = {"wav", "mp3"}

_PARENS_RE = re.compile(r"[([{][^)\]}]*[)\]}]")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class TrackRef:
    """One discovered audio file with derived identities."""

    path: str
    relpath: str
    title: str
    recording_id: str
    work_id: str
    normalized_title: str
    format: str
    size_bytes: int
    sha256: str
    duration_s: float | None = None
    status: str = "ok"
    error: str | None = None
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        if not d["extra"]:
            del d["extra"]
        return d


def _fold_vietnamese(text: str) -> str:
    """ASCII-fold Vietnamese text (NFKD strips accents; map đ/Đ which it misses)."""
    text = text.replace("đ", "d").replace("Đ", "D")
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def normalize_work_title(stem: str) -> str:
    """Reduce a filename stem to a canonical work title (see module docstring)."""
    folded = _fold_vietnamese(stem).lower()
    folded = _PARENS_RE.sub(" ", folded)
    tokens = [t for t in _NON_ALNUM_RE.split(folded) if t]

    def strip_format(tok: str) -> str:
        while len(tok) > 3 and tok.endswith(("wav", "mp3")):
            tok = tok[:-3]
        return tok

    tokens = [strip_format(t) for t in tokens]
    tokens = [t for t in tokens if t and t not in _FORMAT_TOKENS]
    kept = [t for t in tokens if t not in _STOPWORDS]
    if not kept:
        kept = tokens
    return " ".join(kept)


def work_id_for_title(normalized_title: str) -> str:
    """Deterministic work id: sha1 of the normalized title."""
    digest = hashlib.sha1(normalized_title.encode("utf-8")).hexdigest()
    return "work-" + digest[:12]


def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    """SHA-256 of a file's bytes, streamed in 1 MiB chunks."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def probe_duration_s(path: Path) -> float | None:
    """Best-effort duration probe via soundfile; None when undecodable."""
    try:
        info = sf.info(str(path))
        if info.samplerate and info.frames:
            return round(info.frames / float(info.samplerate), 3)
    except Exception:
        return None
    return None


def scan_corpus(root: str | Path) -> list[TrackRef]:
    """Recursively discover WAV/MP3 files under ``root``; return sorted TrackRefs.

    Files that fail to hash or probe are still listed with ``status="error"`` so
    one corrupt file cannot sink the whole corpus.
    """
    root = Path(root)
    refs: list[TrackRef] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in AUDIO_EXTENSIONS:
            continue
        stem = path.stem
        normalized = normalize_work_title(stem)
        try:
            digest = sha256_file(path)
            size = path.stat().st_size
            status, error = "ok", None
        except OSError as exc:
            digest, size = "", 0
            status, error = "error", f"{type(exc).__name__}: {exc}"
        refs.append(
            TrackRef(
                path=str(path),
                relpath=str(path.relative_to(root)),
                title=stem,
                recording_id="rec-" + digest[:16] if digest else "rec-unreadable",
                work_id=work_id_for_title(normalized),
                normalized_title=normalized,
                format=path.suffix.lower().lstrip("."),
                size_bytes=size,
                sha256=digest,
                duration_s=probe_duration_s(path) if status == "ok" else None,
                status=status,
                error=error,
            )
        )
    # Stable order independent of filesystem enumeration quirks.
    refs.sort(key=lambda t: _fold_vietnamese(t.relpath).lower())
    return refs


def duplicate_report(refs: list[TrackRef]) -> dict:
    """Group recordings by identical content hash and works by work_id."""
    by_recording: dict[str, list[str]] = {}
    by_work: dict[str, list[str]] = {}
    for t in refs:
        by_recording.setdefault(t.recording_id, []).append(t.relpath)
        by_work.setdefault(t.work_id, []).append(t.relpath)
    return {
        "duplicate_recordings": {k: sorted(v) for k, v in by_recording.items() if len(v) > 1},
        "works_with_multiple_recordings": {k: sorted(v) for k, v in by_work.items() if len(v) > 1},
        "file_count": len(refs),
        "recording_count": len(by_recording),
        "work_count": len(by_work),
    }
