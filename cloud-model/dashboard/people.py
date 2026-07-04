"""Dashboard proxy helpers for the board person identity service."""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any, Callable, Dict, List, Optional


HttpGet = Callable[[str], Dict[str, Any]]
HttpPostJson = Callable[[str, Dict[str, Any]], Dict[str, Any]]
HttpPostJpeg = Callable[[str, bytes], Dict[str, Any]]


class PersonIdentityClient:
    def __init__(
        self,
        tracker_url: Optional[str] = None,
        *,
        known_confidence_threshold: float = 0.55,
        http_get: Optional[HttpGet] = None,
        http_post_json: Optional[HttpPostJson] = None,
        http_post_jpeg: Optional[HttpPostJpeg] = None,
    ):
        self.tracker_url = (tracker_url or os.environ.get("PERSON_TRACKER_URL") or "http://127.0.0.1:8102").rstrip("/")
        self.known_confidence_threshold = float(known_confidence_threshold)
        self.http_get = http_get or self._http_get
        self.http_post_json = http_post_json or self._http_post_json
        self.http_post_jpeg = http_post_jpeg or self._http_post_jpeg

    def list_people(self) -> Dict[str, Any]:
        data = self.http_get("/registry")
        people = []
        counts = data.get("embedding_counts") or {}
        for item in data.get("people") or []:
            person_id = str(item.get("person_id") or item.get("unique_name") or item.get("name") or "").strip()
            display_name = str(item.get("display_name") or item.get("name") or person_id).strip()
            unique_name = person_id or display_name
            if not unique_name:
                continue
            people.append({
                "unique_name": unique_name,
                "person_id": person_id or unique_name,
                "display_name": display_name or unique_name,
                "embedding_count": int(item.get("embedding_count") or counts.get(person_id) or counts.get(unique_name) or 0),
            })
        people.sort(key=lambda item: item["unique_name"].lower())
        return {
            "ok": bool(data.get("ok", True)),
            "people": people,
            "total_embeddings": int(data.get("total_embeddings") or sum(p["embedding_count"] for p in people)),
            "raw_ok": data.get("ok", True),
        }

    def delete_person(self, unique_name: str) -> Dict[str, Any]:
        return self.http_post_json("/registry/delete", {"unique_name": str(unique_name or "").strip()})

    def enroll_face(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.http_post_json("/enroll_face", payload)

    def capture_candidates_from_jpeg(self, jpeg_bytes: bytes, source: str = "upload") -> Dict[str, Any]:
        data = self.http_post_jpeg("/observe?include_embedding=1&include_face_crop=1", jpeg_bytes)
        known_faces, candidates = self._extract_candidates(data, source=source)
        return {
            "ok": bool(data.get("ok", True)) or bool(known_faces or candidates),
            "known_faces": known_faces,
            "candidates": candidates,
            "num_people": int(data.get("num_people") or len(data.get("people") or [])),
            "error": str(data.get("error") or ""),
        }

    def _extract_candidates(self, data: Dict[str, Any], source: str) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        known_faces: List[Dict[str, Any]] = []
        candidates: List[Dict[str, Any]] = []
        for person in data.get("people") or []:
            face = person.get("face") or {}
            if not isinstance(face, dict):
                continue
            confidence = float(person.get("identity_confidence") or face.get("confidence") or 0.0)
            unique_name = str(person.get("unique_name") or face.get("unique_name") or face.get("display_name") or "").strip()
            if unique_name and confidence >= self.known_confidence_threshold:
                known_faces.append({
                    "unique_name": unique_name,
                    "display_name": str(face.get("display_name") or unique_name),
                    "confidence": confidence,
                    "track_id": person.get("track_id"),
                    "bbox": face.get("bbox") or person.get("bbox") or [],
                })
                continue
            candidate = self._candidate_from_face(face, source=source, track_id=person.get("track_id"))
            if candidate:
                candidates.append(candidate)
        for face in data.get("faces_unassigned") or []:
            if isinstance(face, dict):
                candidate = self._candidate_from_face(face, source=source, track_id=None)
                if candidate:
                    candidates.append(candidate)
        return known_faces, candidates

    @staticmethod
    def _candidate_from_face(face: Dict[str, Any], *, source: str, track_id: Any) -> Optional[Dict[str, Any]]:
        embedding = face.get("embedding")
        if not isinstance(embedding, list) or not embedding:
            return None
        return {
            "embedding": embedding,
            "crop_jpeg_b64": str(face.get("crop_jpeg_b64") or face.get("face_jpeg_b64") or ""),
            "quality": float(face.get("quality") or face.get("confidence") or 0.0),
            "bbox": face.get("bbox") or [],
            "source": source,
            "track_id": track_id,
        }

    def _http_get(self, path: str) -> Dict[str, Any]:
        with urllib.request.urlopen(self.tracker_url + path, timeout=2.0) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _http_post_json(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.tracker_url + path,
            data=raw,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=4.0) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _http_post_jpeg(self, path: str, jpeg_bytes: bytes) -> Dict[str, Any]:
        req = urllib.request.Request(
            self.tracker_url + path,
            data=jpeg_bytes,
            headers={"Content-Type": "image/jpeg"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            return json.loads(resp.read().decode("utf-8"))
