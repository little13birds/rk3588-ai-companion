"""
book_match_client.py — ctypes Python wrapper for libbook_detect.so + libbook_match.so

Provides BookMatchClient with perspective-corrected matching:
  1. YOLO detection → perspective rectify (libbook_detect.so)
  2. CLIP matching against database (libbook_match.so)

Matches the /lookup endpoint pipeline exactly.
"""

import ctypes
import json
import os
import time
from typing import Optional, Dict, List

# ── Constants ──────────────────────────────────────────────────
BOOK_MATCH_BOOK_MAX = 128
BOOK_MATCH_TEXT_MAX = 8192
BOOK_RECTIFY_MAX_PAGES = 2
DEFAULT_SHORT_SIDE = 1000
JPEG_ROTATE_QUALITY = 95


class BookMatchResult(ctypes.Structure):
    _fields_ = [
        ("book", ctypes.c_char * BOOK_MATCH_BOOK_MAX),
        ("page", ctypes.c_int),
        ("text", ctypes.c_char * BOOK_MATCH_TEXT_MAX),
        ("score", ctypes.c_float),
    ]


class BookRectifiedPage(ctypes.Structure):
    _fields_ = [
        ("jpeg_data", ctypes.POINTER(ctypes.c_ubyte)),
        ("jpeg_size", ctypes.c_int),
        ("width", ctypes.c_int),
        ("height", ctypes.c_int),
    ]


class BookLookupResult(ctypes.Structure):
    _fields_ = [
        ("num_pages", ctypes.c_int),
        ("pages", BookRectifiedPage * BOOK_RECTIFY_MAX_PAGES),
    ]


class BookMatchClient:
    """Perspective-corrected book matching client."""

    def __init__(self, db_dir: str, model_dir: str,
                 detect_model: str = None, lib_dir: str = None):
        self.db_dir = db_dir
        self.model_dir = model_dir
        self._handle_match = None
        self._handle_detect = None
        self._libc_free = ctypes.CDLL(None).free
        self._libc_free.argtypes = [ctypes.c_void_p]

        if lib_dir is None:
            lib_dir = os.path.expanduser("~/book_detect/build")

        # ── Load libbook_match.so ──────────────────────────
        match_lib = os.path.join(lib_dir, "libbook_match.so")
        self._lib_match = ctypes.CDLL(match_lib)
        self._lib_match.book_match_init.argtypes = [
            ctypes.c_char_p, ctypes.c_char_p]
        self._lib_match.book_match_init.restype = ctypes.c_void_p
        self._lib_match.book_match_query.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ubyte), ctypes.c_int,
            ctypes.c_char_p]
        self._lib_match.book_match_query.restype = ctypes.POINTER(BookMatchResult)
        self._lib_match.book_match_free.argtypes = [ctypes.POINTER(BookMatchResult)]
        self._lib_match.book_match_free.restype = None
        self._lib_match.book_match_release.argtypes = [ctypes.c_void_p]
        self._lib_match.book_match_release.restype = None
        self._lib_match.book_match_cache_exists.argtypes = [ctypes.c_char_p]
        self._lib_match.book_match_cache_exists.restype = ctypes.c_int

        # ── Load libbook_detect.so ─────────────────────────
        detect_lib = os.path.join(lib_dir, "libbook_detect.so")
        if detect_model is None:
            detect_model = os.path.expanduser(
                "~/book_detect/model/best_hybrid_v9.rknn")
        books_dir = os.path.join(db_dir, "books")
        try:
            book_count = len([
                name for name in os.listdir(books_dir)
                if name.endswith(".json")
            ])
        except OSError:
            book_count = -1
        print(
            "[BookMatch] paths db={} exists={} books={} models={} exists={} cache={}".format(
                db_dir,
                os.path.isdir(db_dir),
                book_count,
                model_dir,
                os.path.isdir(model_dir),
                os.path.join(db_dir, "cache.bin"),
            ),
            flush=True,
        )
        print(
            "[BookMatch] libs match={} detect={} detect_model={} exists={}".format(
                match_lib,
                detect_lib,
                detect_model,
                os.path.exists(detect_model),
            ),
            flush=True,
        )
        self._lib_detect = ctypes.CDLL(detect_lib)
        self._lib_detect.book_detect_init.argtypes = [ctypes.c_char_p]
        self._lib_detect.book_detect_init.restype = ctypes.c_void_p
        self._lib_detect.book_detect_release.argtypes = [ctypes.c_void_p]
        self._lib_detect.book_detect_release.restype = None
        self._lib_detect.book_detect_infer.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ubyte), ctypes.c_int]
        self._lib_detect.book_detect_infer.restype = ctypes.c_void_p
        self._lib_detect.book_detect_rectify.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ubyte), ctypes.c_int, ctypes.c_int]
        self._lib_detect.book_detect_rectify.restype = ctypes.POINTER(BookLookupResult)
        self._lib_detect.book_lookup_free.argtypes = [ctypes.POINTER(BookLookupResult)]
        self._lib_detect.book_lookup_free.restype = None

        # ── Init book_match ──────────────────────────────────
        cache_path = os.path.join(db_dir, "cache.bin")
        cache_exists = self._lib_match.book_match_cache_exists(
            cache_path.encode()) == 1
        label = "Loading cached database" if cache_exists else "Building index (~80s)"
        print(f"[BookMatch] {label}...", flush=True)
        t0 = time.time()
        self._handle_match = self._lib_match.book_match_init(
            db_dir.encode(), model_dir.encode())
        elapsed = time.time() - t0
        if not self._handle_match:
            raise RuntimeError("book_match_init failed")
        print(f"[BookMatch] Ready in {elapsed:.1f}s", flush=True)

        # ── Init book_detect ─────────────────────────────────
        print(f"[BookDetect] Loading YOLO model...", flush=True)
        self._handle_detect = self._lib_detect.book_detect_init(
            detect_model.encode())
        if not self._handle_detect:
            raise RuntimeError("book_detect_init failed")
        print(f"[BookDetect] Ready", flush=True)

    def query(self, jpeg_bytes: bytes) -> List[Dict]:
        """
        Query the database with a camera frame.

        Pipeline: YOLO detect → perspective rectify → CLIP match.
        Returns up to 2 results (one per detected page in a spread).
        Each result: {"book", "page", "text", "score"} or None if no match.
        Returns empty list if no pages detected.
        """
        t0 = time.time()
        if not self._handle_match or not self._handle_detect or not jpeg_bytes:
            print(
                "[BookMatch] query_skip handle_match={} handle_detect={} jpeg_bytes={}".format(
                    bool(self._handle_match),
                    bool(self._handle_detect),
                    len(jpeg_bytes or b""),
                ),
                flush=True,
            )
            return []
        print(
            "[BookMatch] query_start jpeg_bytes={} short_side={}".format(
                len(jpeg_bytes),
                DEFAULT_SHORT_SIDE,
            ),
            flush=True,
        )

        # Step 1: YOLO detection + perspective rectify
        buf = (ctypes.c_ubyte * len(jpeg_bytes))(*jpeg_bytes)
        lr_ptr = self._lib_detect.book_detect_rectify(
            self._handle_detect, buf, len(jpeg_bytes), DEFAULT_SHORT_SIDE)

        if not lr_ptr:
            print(
                "[BookMatch] rectify_none elapsed={:.2f}s".format(time.time() - t0),
                flush=True,
            )
            return []

        lr = lr_ptr.contents
        results = []
        page_count = max(0, min(int(lr.num_pages), BOOK_RECTIFY_MAX_PAGES))
        print(
            "[BookMatch] rectify_done raw_pages={} used_pages={}".format(
                lr.num_pages,
                page_count,
            ),
            flush=True,
        )

        try:
            # Step 2: CLIP match each rectified page
            for i in range(page_count):
                page = lr.pages[i]
                if page.jpeg_size <= 0 or not page.jpeg_data:
                    print(
                        "[BookMatch] page_skip index={} jpeg_size={} has_data={}".format(
                            i,
                            page.jpeg_size,
                            bool(page.jpeg_data),
                        ),
                        flush=True,
                    )
                    continue

                # Extract JPEG bytes from pointer
                jpg = ctypes.string_at(page.jpeg_data, page.jpeg_size)

                upright = self._query_match_candidate(
                    jpg,
                    index=i,
                    orientation="upright",
                    rectified_size=(page.width, page.height),
                )
                rotated_jpg = self._rotate_jpeg_180(jpg)
                rotated = None
                if rotated_jpg:
                    rotated = self._query_match_candidate(
                        rotated_jpg,
                        index=i,
                        orientation="rot180",
                        rectified_size=(page.width, page.height),
                    )

                best = self._choose_orientation_result(upright, rotated)
                if best:
                    results.append(best)
                    print(
                        "[BookMatch] orientation_select index={} selected={} upright={} rot180={}".format(
                            i,
                            best.get("orientation", ""),
                            self._score_text(upright),
                            self._score_text(rotated),
                        ),
                        flush=True,
                    )
                else:
                    results.append(None)
        finally:
            self._lib_detect.book_lookup_free(lr_ptr)

        print(
            "[BookMatch] query_end results={} elapsed={:.2f}s".format(
                len(results),
                time.time() - t0,
            ),
            flush=True,
        )
        return results

    def detect_metadata(self, jpeg_bytes: bytes) -> Dict:
        """Return raw book corner detection metadata for dashboard/debug records."""
        if not self._handle_detect or not jpeg_bytes:
            return {"found": False, "num_pages": 0}
        size = len(jpeg_bytes)
        ptr = self._lib_detect.book_detect_infer(
            self._handle_detect,
            (ctypes.c_ubyte * size)(*jpeg_bytes),
            size,
        )
        if not ptr:
            return {"found": False, "num_pages": 0}
        try:
            raw = ctypes.string_at(ptr).decode("utf-8", errors="replace")
            data = json.loads(raw)
            return data if isinstance(data, dict) else {"found": False, "num_pages": 0}
        except Exception as exc:
            print(
                "[BookDetect] metadata_parse_failed error_type={} error={}".format(
                    type(exc).__name__,
                    exc,
                ),
                flush=True,
            )
            return {"found": False, "num_pages": 0}
        finally:
            free_fn = getattr(self, "_libc_free", None)
            if free_fn:
                free_fn(ptr)

    def _query_match_candidate(
        self,
        jpg: bytes,
        *,
        index: int,
        orientation: str,
        rectified_size: tuple[int, int],
    ) -> Optional[Dict]:
        size = len(jpg)
        rptr = self._lib_match.book_match_query(
            self._handle_match,
            (ctypes.c_ubyte * size)(*jpg),
            size,
            None,
        )
        width, height = rectified_size
        if not rptr:
            print(
                "[BookMatch] candidate index={} orientation={} no_match rectified={}x{} jpeg_bytes={}".format(
                    index,
                    orientation,
                    width,
                    height,
                    size,
                ),
                flush=True,
            )
            return None

        try:
            r = rptr.contents
            result = {
                "book": r.book.decode("utf-8", errors="replace"),
                "page": r.page,
                "text": r.text.decode("utf-8", errors="replace"),
                "score": float(r.score),
                "orientation": orientation,
            }
            print(
                "[BookMatch] candidate index={} orientation={} rectified={}x{} jpeg_bytes={} book={} page={} score={:.3f} text_chars={}".format(
                    index,
                    orientation,
                    width,
                    height,
                    size,
                    result["book"],
                    result["page"],
                    result["score"],
                    len(result["text"] or ""),
                ),
                flush=True,
            )
            return result
        finally:
            self._lib_match.book_match_free(rptr)

    @staticmethod
    def _rotate_jpeg_180(jpg: bytes) -> Optional[bytes]:
        try:
            import cv2
            import numpy as np

            arr = np.frombuffer(jpg, dtype=np.uint8)
            image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if image is None:
                print("[BookMatch] rotate_skip reason=decode_failed", flush=True)
                return None
            rotated = cv2.rotate(image, cv2.ROTATE_180)
            ok, encoded = cv2.imencode(
                ".jpg",
                rotated,
                [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_ROTATE_QUALITY],
            )
            if not ok:
                print("[BookMatch] rotate_skip reason=encode_failed", flush=True)
                return None
            return encoded.tobytes()
        except Exception as exc:
            print(
                "[BookMatch] rotate_skip reason={} error={}".format(
                    type(exc).__name__,
                    exc,
                ),
                flush=True,
            )
            return None

    @classmethod
    def _choose_orientation_result(
        cls,
        upright: Optional[Dict],
        rotated: Optional[Dict],
    ) -> Optional[Dict]:
        candidates = [item for item in (upright, rotated) if item]
        if not candidates:
            return None
        best = max(candidates, key=cls._orientation_rank)
        result = dict(best)
        result["orientation_scores"] = {
            "upright": cls._score_value(upright),
            "rot180": cls._score_value(rotated),
        }
        return result

    @staticmethod
    def _orientation_rank(result: Dict) -> tuple[int, float]:
        text_ok = 1 if (result.get("text") or "").strip() else 0
        return text_ok, BookMatchClient._score_value(result) or 0.0

    @staticmethod
    def _score_value(result: Optional[Dict]) -> Optional[float]:
        if not result:
            return None
        try:
            return float(result.get("score", 0.0))
        except (TypeError, ValueError):
            return 0.0

    @classmethod
    def _score_text(cls, result: Optional[Dict]) -> str:
        score = cls._score_value(result)
        return "none" if score is None else "{:.3f}".format(score)

    def release(self):
        if self._handle_match:
            self._lib_match.book_match_release(self._handle_match)
            self._handle_match = None
        if self._handle_detect:
            self._lib_detect.book_detect_release(self._handle_detect)
            self._handle_detect = None

    def __del__(self):
        self.release()
