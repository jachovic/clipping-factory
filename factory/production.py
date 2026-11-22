"""Local production engine: source ingest -> transcription -> moment detection -> 9:16 render with captions.
Crayo (factory.crayo) is the preferred editor when configured; this engine is the always-available fallback,
so Crayo stays replaceable."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from .config import settings
from .models import Campaign, Clip, SourceAsset

FONT = os.environ.get("CAPTION_FONT", "Arial" if os.name == "nt" else "DejaVu Sans")


# ------------------------------------------------------------------ helpers
def run(cmd: list[str], timeout: int = 1800) -> subprocess.CompletedProcess:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {p.stderr[-800:]}")
    return p


def probe(path: str | Path) -> dict[str, Any]:
    out = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]).stdout
    return json.loads(out)


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------------------- ingest
def ingest_source(s: Session, path: str | Path, rights: str, campaign_id: int | None = None,
                  origin_url: str = "") -> SourceAsset:
    """Register a source file. `rights` is mandatory: who granted use, and where (e.g. campaign brief URL)."""
    if not rights or len(rights.strip()) < 10:
        raise ValueError("A usage-rights note is required for every source file (e.g. 'Provided in campaign "
                         "brief <url> for clipping').")
    p = Path(path).resolve()
    info = probe(p)
    digest = sha256_file(p)
    existing = s.query(SourceAsset).filter_by(sha256=digest, campaign_id=campaign_id).first()
    if existing:
        return existing
    src = SourceAsset(campaign_id=campaign_id, path=str(p), origin_url=origin_url, rights=rights.strip(),
                      sha256=digest, duration_s=float(info["format"].get("duration", 0)))
    s.add(src)
    s.flush()
    return src


# ------------------------------------------------------------ transcription
_model = None


def transcribe(s: Session, src: SourceAsset, language: str | None = None) -> dict[str, Any]:
    if src.transcript.get("words"):
        return src.transcript
    global _model
    from faster_whisper import WhisperModel
    if _model is None:
        os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
        _model = WhisperModel(settings.whisper_model, device="cpu", compute_type="int8",
                              download_root=str(settings.data_dir / "models"))
    # decode with our own ffmpeg (16 kHz mono float32) instead of faster-whisper's PyAV path
    import numpy as np
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", src.path, "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    audio = np.frombuffer(pcm, dtype=np.float32)
    segments, info = _model.transcribe(audio, language=language or None, word_timestamps=True,
                                       vad_filter=True, beam_size=1)
    words, segs = [], []
    for seg in segments:
        segs.append({"start": seg.start, "end": seg.end, "text": seg.text.strip()})
        for w in seg.words or []:
            words.append({"w": w.word.strip(), "s": round(w.start, 3), "e": round(w.end, 3),
                          "p": round(w.probability, 3)})
    src.transcript = {"language": info.language, "words": words, "segments": segs, "engine": f"faster-whisper/{settings.whisper_model}"}
    return src.transcript


# --------------------------------------------------------- moment detection
@dataclass
class Moment:
    start: float
    end: float
    text: str
    words: list[dict[str, Any]]
    score: float = 0.0
    breakdown: dict[str, float] = field(default_factory=dict)
    hook: str = ""


_SENT_END = re.compile(r"[.!?…]$")
_BAD_START = {"and", "but", "so", "or", "because", "which", "that", "it", "he", "she", "they", "this", "um", "uh"}
_FILLERS = {"um", "uh", "like", "you know", "kind of", "sort of", "basically"}
CURIOSITY = {"why", "how", "secret", "nobody", "truth", "actually", "what if", "never", "mistake", "reason"}
EMOTION = {"crazy", "insane", "love", "hate", "scared", "amazing", "unbelievable", "angry", "shocked", "incredible",
           "best", "worst", "wow", "omg"}
SURPRISE = {"suddenly", "turns out", "realized", "but then", "plot twist", "didn't expect", "shocked", "wait"}
VALUE = {"tip", "learn", "step", "how to", "mistake", "rule", "strategy", "money", "here's", "lesson", "trick", "%"}
BLOCKLIST = {"kill yourself", "kys"}  # extend via ComplianceRule


def sentences(words: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    out, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        gap = (words[i + 1]["s"] - w["e"]) if i + 1 < len(words) else 9
        if _SENT_END.search(w["w"]) or gap > 1.2:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def _count(text: str, vocab: set[str]) -> int:
    return sum(text.count(v) for v in vocab)


def score_moment(m: Moment, campaign_terms: set[str], energy: float) -> None:
    t = m.text.lower()
    first = " ".join(w["w"] for w in m.words[:14]).lower()
    dur = m.end - m.start
    n = max(1, len(m.words))
    wps = n / max(dur, 1)
    b = {
        "hook": 10 * min(1.0, (("?" in first) * 0.4 + (_count(first, CURIOSITY | EMOTION | SURPRISE) * 0.3)
                               + (bool(re.search(r"\d", first)) * 0.2) + 0.2)),
        "curiosity": 10 * min(1.0, _count(t, CURIOSITY) / 3 + t.count("?") * 0.15),
        "emotion": 10 * min(1.0, _count(t, EMOTION) / 3 + t.count("!") * 0.15 + energy * 0.5),
        "surprise": 10 * min(1.0, _count(t, SURPRISE) / 2),
        "value": 10 * min(1.0, _count(t, VALUE) / 3 + 0.2),
        "clarity": 10 * max(0.0, 1 - abs(wps - 2.7) / 2.7) * (sum(w["p"] for w in m.words) / n),
        "retention": 10 * max(0.0, 1 - _count(t, _FILLERS) / max(1, n / 20)) * min(1.0, dur / 20),
        "context": 10 * (0.4 if m.words[0]["w"].lower().strip(",.") in _BAD_START else 1.0)
                       * (1.0 if _SENT_END.search(m.words[-1]["w"]) else 0.5),
        "relevance": 10 * (min(1.0, sum(term in t for term in campaign_terms) / 2) if campaign_terms else 0.6),
        "compliance": 0.0 if any(b in t for b in BLOCKLIST) else 10.0,
    }
    m.breakdown = {k: round(v, 1) for k, v in b.items()}
    m.score = round(sum(b.values()), 1) if b["compliance"] > 0 else 0.0
    # hook line = first sentence, trimmed
    first_sentence = re.split(r"(?<=[.!?])\s", m.text, maxsplit=1)[0]
    m.hook = first_sentence if len(first_sentence) <= 90 else first_sentence[:88].rsplit(" ", 1)[0] + "…"


def audio_energy_profile(path: str, duration: float, step: float = 1.0) -> list[float]:
    """Per-second loudness (0..1) via ffmpeg astats; used as a cheap emphasis signal."""
    try:
        out = run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-af",
                   f"asetnsamples=n={int(16000 * step)},aresample=16000,astats=metadata=1:reset=1,"
                   "ametadata=print:key=lavfi.astats.Overall.RMS_level", "-f", "null", "-"]).stderr
    except RuntimeError:
        return []
    vals = [float(x) for x in re.findall(r"RMS_level=(-?[\d.]+|-inf)", out.replace("-inf", "-120"))]
    if not vals:
        return []
    lo, hi = min(vals), max(vals)
    return [(v - lo) / (hi - lo) if hi > lo else 0.0 for v in vals]


def detect_moments(src: SourceAsset, campaign: Campaign | None, n: int = 3, min_len: float = 15,
                   max_len: float = 59) -> list[Moment]:
    words = src.transcript.get("words") or []
    if not words:
        return []
    if campaign is not None:
        min_len = max(min_len, campaign.min_duration_s or 0)
        max_len = min(max_len, campaign.max_duration_s or max_len)
    sents = sentences(words)
    energy = audio_energy_profile(src.path, src.duration_s or 0)
    terms = set()
    if campaign is not None:
        terms = {w for w in re.findall(r"[a-z]{4,}", f"{campaign.name} {campaign.brand}".lower())}
    cands: list[Moment] = []
    for i in range(len(sents)):
        acc: list[dict[str, Any]] = []
        for j in range(i, len(sents)):
            acc = acc + sents[j]
            dur = acc[-1]["e"] - acc[0]["s"]
            if dur > max_len:
                break
            if dur >= min_len:
                m = Moment(start=max(0.0, acc[0]["s"] - 0.15), end=acc[-1]["e"] + 0.35,
                           text=" ".join(w["w"] for w in acc), words=list(acc))
                e = energy[int(m.start):int(m.end) + 1] if energy else []
                score_moment(m, terms, (sum(e) / len(e)) if e else 0.3)
                cands.append(m)
    cands.sort(key=lambda m: m.score, reverse=True)
    chosen: list[Moment] = []
    for m in cands:  # non-overlapping greedy pick
        if m.score <= 0:
            continue
        if all(m.end <= c.start or m.start >= c.end for c in chosen):
            chosen.append(m)
        if len(chosen) >= n:
            break
    return chosen


# ---------------------------------------------------------------- rendering
def _ass_time(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def build_ass(words: list[dict[str, Any]], offset: float, hook: str | None, style: dict[str, Any]) -> str:
    """Word-group captions (karaoke-style highlight of the active word) in the lower-middle safe zone."""
    fs = style.get("font_size", 74)
    margin_v = style.get("margin_v", 560)          # px from bottom on a 1920-high canvas (~lower third)
    hl = style.get("highlight", "&H0000E5FF")      # BGR: yellow
    head = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 0\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
            "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
            "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            f"Style: Cap,{FONT},{fs},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,6,2,2,80,80,{margin_v},1\n"
            f"Style: Hook,{FONT},62,&H00FFFFFF,&H00FFFFFF,&H00000000,&HB4000000,1,0,0,0,100,100,0,0,3,4,0,8,70,70,260,1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    lines = []
    if hook:
        lines.append(f"Dialogue: 1,{_ass_time(0)},{_ass_time(2.8)},Hook,,0,0,0,,{_esc(hook.upper())}")
    group = style.get("words_per_caption", 3)
    groups, cur = [], []
    for w in words:  # max `group` words, and never run across a sentence end
        cur.append(w)
        if len(cur) >= group or _SENT_END.search(w["w"]) or w["w"].endswith(","):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    for g in groups:
        for k, w in enumerate(g):
            st = w["s"] - offset
            en = (g[k + 1]["s"] if k + 1 < len(g) else g[-1]["e"]) - offset
            txt = " ".join((f"{{\\c{hl}}}{_esc(x['w'].upper())}{{\\c&H00FFFFFF&}}" if x is w else _esc(x["w"].upper()))
                           for x in g)
            lines.append(f"Dialogue: 0,{_ass_time(st)},{_ass_time(max(en, st + 0.05))},Cap,,0,0,0,,{txt}")
    return head + "\n".join(lines) + "\n"


def _esc(t: str) -> str:
    return t.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def subject_center_x(path: str, start: float, end: float, w: int, h: int, samples: int = 10) -> float | None:
    """Horizontal position (0..1) of the main speaker's face across the moment, or None when there is no single
    stable face (no face, several people, or the face moves a lot) -> caller falls back to the safe blurred fit."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    det = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    sw = 640
    sh = int(round(h * sw / w / 2) * 2)
    xs, multi = [], 0
    for k in range(samples):
        t = start + (end - start) * (k + 0.5) / samples
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1",
                              "-vf", f"scale={sw}:{sh},format=gray", "-f", "rawvideo", "-"], capture_output=True).stdout
        if len(raw) != sw * sh:
            continue
        frame = np.frombuffer(raw, dtype=np.uint8).reshape(sh, sw)
        faces = det.detectMultiScale(frame, scaleFactor=1.1, minNeighbors=6, minSize=(max(24, sh // 12),) * 2)
        if len(faces) == 0:
            continue
        faces = sorted(faces, key=lambda f: f[2] * f[3], reverse=True)
        if len(faces) > 1 and faces[1][2] * faces[1][3] > 0.5 * faces[0][2] * faces[0][3]:
            multi += 1          # two similar-sized faces: a conversation -> don't crop one person out
        x, y, fw, fh = faces[0]
        xs.append((x + fw / 2) / sw)
    if len(xs) < max(3, samples * 0.6) or multi > samples * 0.3:
        return None
    xs.sort()
    med = xs[len(xs) // 2]
    spread = xs[int(len(xs) * 0.9) - 1] - xs[int(len(xs) * 0.1)]
    return med if spread < 0.2 else None


def render_clip(src: SourceAsset, m: Moment, out_path: Path, *, hook: bool = True,
                style: dict[str, Any] | None = None) -> Path:
    """Render a 1080x1920 H.264/AAC clip. Wide footage is fitted on a blurred fill (no subject cropped off);
    near-vertical footage is scaled to fill."""
    style = style or {}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ass = out_path.with_suffix(".ass")
    ass.write_text(build_ass(m.words, m.start, m.hook if hook else None, style))
    v = next((st for st in probe(src.path)["streams"] if st["codec_type"] == "video"), None)
    if v is None:
        raise RuntimeError("source has no video stream")
    w, h = int(v["width"]), int(v["height"])
    framing = style.get("framing", os.environ.get("CLIP_FRAMING", "auto"))   # auto | crop | fit
    cx = subject_center_x(src.path, m.start, m.end, w, h) if (w / h > 0.7 and framing != "fit") else None
    m.breakdown["framing"] = "face_crop" if cx is not None else ("fit" if w / h > 0.7 else "fill")
    if cx is not None:  # one stable speaker -> full-screen 9:16 crop centred on the face
        cw = int(h * 9 / 16) // 2 * 2
        x = int(min(max(cx * w - cw / 2, 0), w - cw))
        vf = f"crop={cw}:{h}:{x}:0,scale=1080:1920,setsar=1,ass={_ff_path(ass)},fps=30,format=yuv420p"
    elif w / h > 0.7:  # landscape/square without a single speaker -> blurred background + fitted foreground
        vf = ("split=2[bg][fg];[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
              "boxblur=24:2,eq=brightness=-0.08[bgb];[fg]scale=1080:-2[fgs];[bgb][fgs]overlay=(W-w)/2:(H-h)/2-120,"
              f"ass={_ff_path(ass)},fps=30,format=yuv420p")
    else:
        vf = f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,ass={_ff_path(ass)},fps=30,format=yuv420p"
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{m.start:.3f}", "-to", f"{m.end:.3f}",
         "-i", src.path, "-vf", vf, "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-c:v", "libx264", "-preset", "medium",
         "-crf", "20", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart", str(out_path)])
    return out_path


def _ff_path(p: Path) -> str:
    return str(p).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def visual_fingerprint(path: str | Path, frames: int = 8) -> str:
    """Tiny perceptual fingerprint: 8 frames downscaled to 8x8 grey, thresholded at their mean."""
    dur = float(probe(path)["format"]["duration"])
    bits = []
    for k in range(frames):
        t = dur * (k + 0.5) / frames
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1",
                              "-vf", "scale=8:8,format=gray", "-f", "rawvideo", "-"], capture_output=True).stdout
        if len(raw) == 64:
            avg = sum(raw) / 64
            bits.append("".join("1" if b > avg else "0" for b in raw))
    return hex(int("".join(bits), 2))[2:] if bits else ""


def fingerprint_distance(a: str, b: str) -> float:
    if not a or not b:
        return 1.0
    x, y = int(a, 16), int(b, 16)
    nbits = max(x.bit_length(), y.bit_length(), 1)
    return bin(x ^ y).count("1") / nbits


# --------------------------------------------------------------- orchestration
def produce_local(s: Session, src: SourceAsset, campaign: Campaign | None, n: int = 2,
                  variant: str = "v1", style: dict[str, Any] | None = None) -> list[Clip]:
    transcribe(s, src, language=(campaign.language if campaign and campaign.language else None))
    from . import ai_rank
    if ai_rank.enabled():
        candidates = detect_moments(src, campaign, n=ai_rank.MAX_CANDIDATES)
        moments = ai_rank.rerank(s, campaign, candidates, n)
    else:
        moments = detect_moments(src, campaign, n=n)
    clips = []
    for idx, m in enumerate(moments, 1):
        clip = Clip(campaign_id=campaign.id if campaign else None, source_id=src.id, engine="local",
                    start_s=m.start, end_s=m.end, moment_score=m.score, moment_breakdown=m.breakdown,
                    hook=m.hook, text=m.text, variant=variant, status="in_production")
        s.add(clip)
        s.flush()
        out = settings.data_dir / "clips" / f"c{clip.campaign_id or 0}" / f"clip{clip.id}_{variant}.mp4"
        try:
            render_clip(src, m, out, style=style)
            clip.path = str(out)
            clip.content_hash = visual_fingerprint(out)
            clip.status = "rendered"
            clip.qc_report = {"words": m.words}  # kept for caption QC; replaced by the QC report
        except Exception as e:  # noqa: BLE001
            clip.status = "failed"
            clip.qc_report = {"error": str(e)[:500]}
        clips.append(clip)
    return clips


def seconds(x: float) -> str:
    return f"{int(x // 60)}:{x % 60:04.1f}"


__all__ = ["ingest_source", "transcribe", "detect_moments", "render_clip", "produce_local", "probe",
           "visual_fingerprint", "fingerprint_distance", "Moment"]
