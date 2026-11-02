"""OCR layer. Independent of the AI layer: it only turns page images into text + boxes.

Engines:
  * PaddleOCR (primary, supports both the 2.x and 3.x APIs)
  * Tesseract (optional fallback, via pytesseract)

Swap or add an engine by implementing `OCREngine.recognize()`.
"""
from __future__ import annotations

import json
import logging
import shutil
import statistics
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
from PIL import Image

from backend.config import get_settings
from backend.utils.image_utils import pil_to_bgr, preprocess_for_ocr, resize_if_needed

logger = logging.getLogger("invoice_ai.ocr")


class OCRError(Exception):
    """OCR could not run or no engine is available."""


@dataclass
class OCRLine:
    text: str
    confidence: Optional[float]  # 0..1, None if the engine gives none
    bbox: List[float]            # [x_min, y_min, x_max, y_max] in preprocessed-image pixels


@dataclass
class OCRPage:
    page: int
    lines: List[OCRLine] = field(default_factory=list)
    width: int = 0
    height: int = 0

    @property
    def text(self) -> str:
        return lines_to_text(self.lines)

    @property
    def mean_confidence(self) -> Optional[float]:
        vals = [l.confidence for l in self.lines if l.confidence is not None]
        return statistics.fmean(vals) if vals else None


@dataclass
class OCRResult:
    engine: str
    pages: List[OCRPage] = field(default_factory=list)

    @property
    def text(self) -> str:
        """All pages, with explicit page markers so the AI knows where pages start."""
        parts = []
        for p in self.pages:
            parts.append(f"=== PAGE {p.page} ===\n{p.text}")
        return "\n\n".join(parts)

    @property
    def char_count(self) -> int:
        return sum(len(l.text) for p in self.pages for l in p.lines)

    @property
    def mean_confidence(self) -> Optional[float]:
        vals = [l.confidence for p in self.pages for l in p.lines if l.confidence is not None]
        return statistics.fmean(vals) if vals else None

    @property
    def low_confidence_ratio(self) -> float:
        thr = get_settings().ocr_low_confidence
        vals = [l.confidence for p in self.pages for l in p.lines if l.confidence is not None]
        if not vals:
            return 0.0
        return sum(1 for v in vals if v < thr) / len(vals)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "engine": self.engine,
            "mean_confidence": self.mean_confidence,
            "pages": [
                {
                    "page": p.page,
                    "mean_confidence": p.mean_confidence,
                    "lines": [{"text": l.text, "confidence": l.confidence, "bbox": l.bbox} for l in p.lines],
                }
                for p in self.pages
            ],
        }


# ----------------------------------------------------------------------------- layout reconstruction

def lines_to_text(lines: List[OCRLine]) -> str:
    """Group boxes into visual rows (by vertical overlap) and join left-to-right.

    Keeping rows intact is important for tables: 'Laptop  2  50000  100000' stays on one line.
    """
    if not lines:
        return ""
    items = sorted(lines, key=lambda l: ((l.bbox[1] + l.bbox[3]) / 2, l.bbox[0]))
    rows: List[List[OCRLine]] = []
    for ln in items:
        cy = (ln.bbox[1] + ln.bbox[3]) / 2
        h = max(ln.bbox[3] - ln.bbox[1], 1)
        placed = False
        if rows:
            last = rows[-1]
            ry = statistics.fmean((l.bbox[1] + l.bbox[3]) / 2 for l in last)
            rh = statistics.fmean(max(l.bbox[3] - l.bbox[1], 1) for l in last)
            if abs(cy - ry) <= 0.5 * min(h, rh):
                last.append(ln)
                placed = True
        if not placed:
            rows.append([ln])
    out = []
    for row in rows:
        row.sort(key=lambda l: l.bbox[0])
        out.append("   ".join(l.text for l in row))
    return "\n".join(out)


# ----------------------------------------------------------------------------- engines

class OCREngine:
    name = "base"

    def available(self) -> bool:
        raise NotImplementedError

    def recognize(self, image_bgr: np.ndarray) -> List[OCRLine]:
        raise NotImplementedError


class PaddleEngine(OCREngine):
    name = "paddleocr"
    _instance: Any = None
    _failed: Optional[str] = None

    def available(self) -> bool:
        try:
            import paddleocr  # noqa: F401
            return True
        except Exception:
            return False

    def _get(self) -> Any:
        if PaddleEngine._instance is None:
            from paddleocr import PaddleOCR

            lang = get_settings().ocr_lang
            try:  # PaddleOCR 3.x
                kwargs = {
                    "lang": lang,
                    "enable_mkldnn": False,
                    "use_doc_orientation_classify": False,
                    "use_doc_unwarping": False,
                    "use_textline_orientation": False,
                }
                if get_settings().paddle_det_model:
                    kwargs["text_detection_model_name"] = get_settings().paddle_det_model
                if get_settings().paddle_rec_model:
                    kwargs["text_recognition_model_name"] = get_settings().paddle_rec_model
                PaddleEngine._instance = PaddleOCR(**kwargs)
            except TypeError:  # PaddleOCR 2.x
                PaddleEngine._instance = PaddleOCR(lang=lang, use_angle_cls=True, show_log=False)
        return PaddleEngine._instance

    @staticmethod
    def _result_dict(res: Any) -> Dict[str, Any]:
        """Normalize PaddleOCR 3.x Result objects/dicts across minor API versions."""
        data: Any = res
        if not isinstance(data, dict):
            data = getattr(res, "json", None)
            if callable(data):
                data = data()
            if isinstance(data, str):
                data = json.loads(data)
        if not isinstance(data, dict):
            raise RuntimeError("PaddleOCR returned an unsupported result object.")
        if isinstance(data.get("res"), dict):
            data = data["res"]
        return data

    @staticmethod
    def _box_from_item(poly: Any, rec_box: Any = None) -> List[float]:
        # PaddleOCR 3.x normally provides rec_polys; rec_boxes is a reliable
        # rectangular fallback and is explicitly part of the 3.x result API.
        if rec_box is not None:
            b = np.asarray(rec_box).reshape(-1)
            if b.size >= 4:
                return [float(b[0]), float(b[1]), float(b[2]), float(b[3])]
        pts = np.asarray(poly).reshape(-1, 2)
        return [float(pts[:, 0].min()), float(pts[:, 1].min()),
                float(pts[:, 0].max()), float(pts[:, 1].max())]

    def recognize(self, image_bgr: np.ndarray) -> List[OCRLine]:
        ocr = self._get()
        lines: List[OCRLine] = []
        if hasattr(ocr, "predict"):  # PaddleOCR 3.x
            for res in ocr.predict(image_bgr):
                data = self._result_dict(res)
                texts = data.get("rec_texts")
                texts = [] if texts is None else list(texts)
                scores = data.get("rec_scores")
                scores = [] if scores is None else list(scores)
                polys = data.get("rec_polys")
                if polys is None:
                    polys = data.get("dt_polys")
                polys = [] if polys is None else list(polys)
                boxes = data.get("rec_boxes")
                if boxes is None:
                    boxes = []
                else:
                    boxes = list(boxes)
                # Keep all three arrays aligned even when a PaddleOCR minor
                # version omits one of them.
                count = len(texts)
                for i in range(count):
                    text = str(texts[i]).strip()
                    if not text:
                        continue
                    score = float(scores[i]) if i < len(scores) else None
                    poly = polys[i] if i < len(polys) else None
                    box = boxes[i] if i < len(boxes) else None
                    if poly is None and box is None:
                        continue
                    bbox = self._box_from_item(poly, box)
                    lines.append(OCRLine(text, score, bbox))
        else:  # PaddleOCR 2.x
            result = ocr.ocr(image_bgr, cls=True)
            for page in result or []:
                for box, (text, score) in page or []:
                    pts = np.array(box)
                    lines.append(OCRLine(str(text), float(score),
                                         [float(pts[:, 0].min()), float(pts[:, 1].min()),
                                          float(pts[:, 0].max()), float(pts[:, 1].max())]))

        if not lines:
            raise RuntimeError("PaddleOCR completed but detected no readable text.")
        return lines



class TesseractEngine(OCREngine):
    name = "tesseract"

    def _setup(self):
        import pytesseract

        cmd = get_settings().tesseract_cmd
        if cmd:
            pytesseract.pytesseract.tesseract_cmd = cmd
        return pytesseract

    def available(self) -> bool:
        try:
            pt = self._setup()
            if get_settings().tesseract_cmd:
                return shutil.which(get_settings().tesseract_cmd) is not None or __import__("os").path.exists(
                    get_settings().tesseract_cmd
                )
            pt.get_tesseract_version()
            return True
        except Exception:
            return False

    def recognize(self, image_bgr: np.ndarray) -> List[OCRLine]:
        pt = self._setup()
        lang = {"en": "eng"}.get(get_settings().ocr_lang, get_settings().ocr_lang)
        # PSM 6 = assume a uniform block of text; works well for invoices with tables.
        d = pt.image_to_data(Image.fromarray(image_bgr[:, :, ::-1]), lang=lang, config="--psm 6",
                             output_type=pt.Output.DICT)
        # Merge words that sit on the same tesseract line into one box, to cut noise.
        grouped: Dict[tuple, List[int]] = {}
        for i, txt in enumerate(d["text"]):
            if txt and txt.strip() and float(d["conf"][i]) >= 0:
                grouped.setdefault((d["block_num"][i], d["par_num"][i], d["line_num"][i]), []).append(i)
        lines: List[OCRLine] = []
        for idxs in grouped.values():
            # split a line into segments wherever there is a wide gap (table columns)
            idxs.sort(key=lambda i: d["left"][i])
            seg: List[int] = [idxs[0]]
            segments = [seg]
            for i in idxs[1:]:
                prev = seg[-1]
                gap = d["left"][i] - (d["left"][prev] + d["width"][prev])
                if gap > 2.5 * max(d["height"][i], 8):
                    seg = [i]
                    segments.append(seg)
                else:
                    seg.append(i)
            for s_ in segments:
                x0 = min(d["left"][i] for i in s_)
                y0 = min(d["top"][i] for i in s_)
                x1 = max(d["left"][i] + d["width"][i] for i in s_)
                y1 = max(d["top"][i] + d["height"][i] for i in s_)
                conf = statistics.fmean(float(d["conf"][i]) for i in s_) / 100.0
                lines.append(OCRLine(" ".join(d["text"][i].strip() for i in s_), conf,
                                     [float(x0), float(y0), float(x1), float(y1)]))
        return lines


_ENGINES = {"paddle": PaddleEngine, "paddleocr": PaddleEngine, "tesseract": TesseractEngine}


def _engine_order() -> List[OCREngine]:
    choice = get_settings().ocr_engine
    if choice in ("paddle", "paddleocr"):
        return [PaddleEngine()]
    if choice == "tesseract":
        return [TesseractEngine()]
    return [PaddleEngine(), TesseractEngine()]  # auto: Paddle first, Tesseract as fallback


def ocr_status() -> Dict[str, Any]:
    installed = {e.name: e.available() for e in (PaddleEngine(), TesseractEngine())}
    active = next((e.name for e in _engine_order() if installed.get(e.name)), None)
    return {"available": active is not None, "engines": installed, "active": active}


def run_ocr(pages: List[Image.Image], cancel_check=None, progress_callback=None) -> OCRResult:
    """OCR image pages. Raw images are tried first; preprocessing is a fallback only.

    This avoids the old behaviour of running every page through OCR twice, which
    was a major source of latency.
    """
    if progress_callback:
        # Publish an initial OCR event before importing/initialising PaddleOCR. Its first
        # model setup can take noticeably longer than the subsequent page recognition.
        progress_callback(0, max(len(pages), 1), 0)
    engines = [e for e in _engine_order() if e.available()]
    if not engines:
        raise OCRError("No OCR engine is available. Install PaddleOCR/PaddlePaddle or configure Tesseract.")

    last_error: Optional[Exception] = None
    for engine in engines:
        try:
            result = OCRResult(engine=engine.name)
            for n, page_img in enumerate(pages, start=1):
                if cancel_check and cancel_check(): raise RuntimeError("__CANCELLED__")
                raw = resize_if_needed(page_img)
                arr = pil_to_bgr(raw)
                try:
                    lines = engine.recognize(arr)
                except Exception as raw_exc:
                    if not get_settings().ocr_preprocess_fallback:
                        raise
                    logger.warning("OCR engine %s failed on raw image; trying light preprocessing", engine.name)
                    lines = engine.recognize(preprocess_for_ocr(page_img))
                    last_error = raw_exc
                if cancel_check and cancel_check(): raise RuntimeError("__CANCELLED__")
                result.pages.append(OCRPage(page=n, lines=lines, width=raw.width, height=raw.height))
                if progress_callback:
                    progress_callback(n, len(pages), result.char_count)
            if result.char_count >= 15:
                logger.info("OCR engine %s succeeded: %d chars, mean confidence=%s", engine.name, result.char_count, result.mean_confidence)
                return result
            raise RuntimeError("OCR completed but returned almost no readable text.")
        except RuntimeError as exc:
            if str(exc) == "__CANCELLED__": raise
            logger.exception("OCR engine %s failed", engine.name)
            last_error = exc
        except Exception as exc:
            logger.exception("OCR engine %s failed", engine.name)
            last_error = exc

    raise OCRError("Text recognition failed. The OCR engine could not read the document.") from last_error
