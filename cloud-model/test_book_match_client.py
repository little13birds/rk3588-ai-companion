"""Unit tests for BookMatchClient orientation fallback.

Run from cloud-model repo with: python3 -m test_book_match_client
"""
import ctypes
import json
import unittest

import cv2
import numpy as np

from book_match_client import (
    BookLookupResult,
    BookMatchClient,
    BookMatchResult,
)


class FakeDetectLib:
    def __init__(self, jpeg: bytes):
        self.jpeg = jpeg
        self.buffers = []
        self.lookup = None

    def book_detect_rectify(self, _handle, _buf, _size, _short_side):
        data = (ctypes.c_ubyte * len(self.jpeg))(*self.jpeg)
        self.buffers.append(data)

        lookup = BookLookupResult()
        lookup.num_pages = 1
        lookup.pages[0].jpeg_data = ctypes.cast(data, ctypes.POINTER(ctypes.c_ubyte))
        lookup.pages[0].jpeg_size = len(self.jpeg)
        lookup.pages[0].width = 320
        lookup.pages[0].height = 240
        self.lookup = lookup
        return ctypes.pointer(self.lookup)

    def book_lookup_free(self, _lookup_ptr):
        return None

    def book_detect_release(self, _handle):
        return None

    def book_detect_infer(self, _handle, _buf, _size):
        payload = json.dumps({
            "found": True,
            "pages": [{
                "conf": 0.92,
                "corners": {
                    "tl": [1, 2, 0.9],
                    "tr": [3, 4, 0.8],
                    "br": [5, 6, 0.7],
                    "bl": [7, 8, 0.6],
                },
            }],
        }).encode("utf-8")
        buf = ctypes.create_string_buffer(payload)
        self.buffers.append(buf)
        return ctypes.cast(buf, ctypes.c_void_p)


class FakeMatchLib:
    def __init__(self):
        self.calls = []
        self.results = []

    def book_match_query(self, _handle, data, size, _options):
        self.calls.append(ctypes.string_at(data, size))
        result = BookMatchResult()
        if len(self.calls) == 1:
            result.book = b"upright-low"
            result.page = 1
            result.text = b"low confidence text"
            result.score = 0.31
        else:
            result.book = b"rotated-high"
            result.page = 2
            result.text = b"high confidence text"
            result.score = 0.87
        self.results.append(result)
        return ctypes.pointer(self.results[-1])

    def book_match_free(self, _result_ptr):
        return None

    def book_match_release(self, _handle):
        return None


class BookMatchOrientationTests(unittest.TestCase):
    def test_query_checks_rotated_page_and_returns_best_score(self):
        image = np.zeros((24, 32, 3), dtype=np.uint8)
        image[:, :16] = (40, 80, 180)
        image[:, 16:] = (200, 220, 30)
        ok, encoded = cv2.imencode(".jpg", image)
        self.assertTrue(ok)
        jpeg = encoded.tobytes()
        fake_detect = FakeDetectLib(jpeg)
        fake_match = FakeMatchLib()

        client = BookMatchClient.__new__(BookMatchClient)
        client._handle_match = object()
        client._handle_detect = object()
        client._lib_detect = fake_detect
        client._lib_match = fake_match

        results = client.query(jpeg)

        self.assertEqual(len(fake_match.calls), 2)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["book"], "rotated-high")
        self.assertEqual(results[0]["page"], 2)
        self.assertEqual(results[0]["orientation"], "rot180")
        self.assertAlmostEqual(results[0]["orientation_scores"]["upright"], 0.31, places=3)
        self.assertAlmostEqual(results[0]["orientation_scores"]["rot180"], 0.87, places=3)

    def test_detect_metadata_returns_corner_json(self):
        image = np.zeros((24, 32, 3), dtype=np.uint8)
        ok, encoded = cv2.imencode(".jpg", image)
        self.assertTrue(ok)
        jpeg = encoded.tobytes()

        client = BookMatchClient.__new__(BookMatchClient)
        client._handle_match = object()
        client._handle_detect = object()
        client._lib_detect = FakeDetectLib(jpeg)
        client._lib_match = FakeMatchLib()
        client._libc_free = lambda _ptr: None

        metadata = client.detect_metadata(jpeg)

        self.assertTrue(metadata["found"])
        self.assertEqual(metadata["pages"][0]["corners"]["tl"], [1, 2, 0.9])


if __name__ == "__main__":
    unittest.main(verbosity=2)
