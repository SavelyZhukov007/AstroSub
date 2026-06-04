# -*- coding: utf-8 -*-
"""
Распознавание и идентификация лиц.

  1. InsightFace (buffalo_l) даёт 512-мерный ArcFace-эмбеддинг на лицо.
  2. Эмбеддинги кластеризуются агломеративно по косинусной дистанции ->
     устойчивый person_id. Один человек = один кластер.
  3. Средний эмбеддинг кластера сверяется с постоянной галереей (facedb):
     если лицо уже знакомо — подставляется сохранённое имя (узнавание между
     сессиями). По желанию новые лица запоминаются.
  4. MediaPipe FaceMesh добавляет 468 точек для оверлея.
"""
from __future__ import annotations

import uuid
from typing import Callable, List, Optional

from . import facedb, media


class FaceEngine:
    def __init__(self, sample_fps=0.5, cluster_threshold=0.55,
                 match_threshold=0.42, remember=True, device="cpu"):
        self.sample_fps = sample_fps
        self.cluster_threshold = cluster_threshold
        self.match_threshold = match_threshold
        self.remember = remember
        self.device = device
        self._app = None
        self._mesh = None

    # --- ленивая инициализация моделей -----------------------------------
    def _ensure_detector(self):
        if self._app is not None:
            return
        from insightface.app import FaceAnalysis
        ctx = 0 if self.device == "cuda" else -1
        app = FaceAnalysis(name="buffalo_l")
        try:
            app.prepare(ctx_id=ctx, det_size=(640, 640))
        except Exception:
            if ctx == -1:
                raise
            self.device = "cpu"
            app.prepare(ctx_id=-1, det_size=(640, 640))
        self._app = app

    def _ensure_mesh(self):
        if self._mesh is not None:
            return
        import mediapipe as mp
        self._mesh = mp.solutions.face_mesh.FaceMesh(
            static_image_mode=True, max_num_faces=6,
            refine_landmarks=True, min_detection_confidence=0.5)

    # --- основной проход --------------------------------------------------
    def analyze(self, video_path: str,
                on_progress: Optional[Callable[[float, str], None]] = None) -> dict:
        import numpy as np

        self._ensure_detector()
        duration = media.probe_duration(video_path) or 1.0

        detections = []
        for t, frame in media.iter_frames(video_path, fps=self.sample_fps):
            faces = self._app.get(frame)
            for f in faces:
                emb = getattr(f, "normed_embedding", None)
                if emb is None:
                    continue
                detections.append({
                    "t": round(float(t), 2),
                    "bbox": [int(v) for v in f.bbox.tolist()],
                    "emb": np.asarray(emb, dtype=np.float32),
                    "det_score": float(getattr(f, "det_score", 1.0)),
                    "age": getattr(f, "age", None),
                    "gender": getattr(f, "gender", None),
                })
            if on_progress and duration:
                on_progress(min(0.95, t / duration), "Анализ кадров")

        if not detections:
            if on_progress:
                on_progress(1.0, "Лица не найдены")
            return {"persons": [], "timeline": []}

        labels = self._cluster([d["emb"] for d in detections])
        persons = self._build_persons(detections, labels, video_path)
        # карта: внутренний номер кластера -> стабильный person id (0..N)
        order = {p["_cluster"]: i for i, p in enumerate(persons)}
        timeline = [{"t": d["t"], "person": order.get(int(lbl), 0), "bbox": d["bbox"]}
                    for d, lbl in zip(detections, labels)]
        for i, p in enumerate(persons):
            p["id"] = i
            p.pop("_cluster", None)

        if on_progress:
            on_progress(1.0, f"Найдено лиц: {len(persons)}")
        return {"persons": persons, "timeline": timeline}

    # --- кластеризация эмбеддингов ---------------------------------------
    def _cluster(self, embeddings: List[np.ndarray]) -> np.ndarray:
        import numpy as np

        X = np.vstack(embeddings)
        X = X / (np.linalg.norm(X, axis=1, keepdims=True) + 1e-9)
        if len(X) == 1:
            return np.array([0])
        from sklearn.cluster import AgglomerativeClustering
        clusterer = AgglomerativeClustering(
            n_clusters=None, metric="cosine", linkage="average",
            distance_threshold=self.cluster_threshold)
        return clusterer.fit_predict(X)

    def _build_persons(self, detections, labels, video_path) -> list:
        import os
        import tempfile
        import numpy as np

        groups = {}
        for d, lbl in zip(detections, labels):
            lbl = int(lbl)
            g = groups.setdefault(lbl, {
                "_cluster": lbl, "count": 0, "first_t": d["t"], "last_t": d["t"],
                "best_score": -1, "best_t": d["t"], "thumb": None,
                "embs": [], "ages": [], "genders": [],
            })
            g["count"] += 1
            g["first_t"] = min(g["first_t"], d["t"])
            g["last_t"] = max(g["last_t"], d["t"])
            g["embs"].append(d["emb"])
            if d.get("age") is not None:
                g["ages"].append(float(d["age"]))
            if d.get("gender") is not None:
                g["genders"].append(int(d["gender"]))
            if d["det_score"] > g["best_score"]:
                g["best_score"] = d["det_score"]
                g["best_t"] = d["t"]

        persons = []
        for g in groups.values():
            mean = np.mean(np.vstack(g["embs"]), axis=0)
            mean = mean / (np.linalg.norm(mean) + 1e-9)
            # узнаём по галерее
            known = facedb.match(mean, self.match_threshold)
            if known:
                uid, label = known["uid"], known["label"]
            else:
                uid = uuid.uuid4().hex[:12]
                label = f"Спикер {len(persons) + 1}"

            thumb = os.path.join(tempfile.gettempdir(), f"submind_face_{uid}.jpg")
            if media.grab_thumbnail(video_path, g["best_t"], thumb, width=200):
                g["thumb"] = thumb

            person = {
                "id": 0, "_cluster": g["_cluster"], "uid": uid, "label": label,
                "known": bool(known), "count": g["count"],
                "first_t": g["first_t"], "last_t": g["last_t"],
                "thumb": g["thumb"], "embedding": mean.tolist(),
                "age": round(float(np.median(g["ages"])), 0) if g["ages"] else None,
                "gender": ("муж" if np.mean(g["genders"]) >= 0.5 else "жен") if g["genders"] else None,
            }
            persons.append(person)
            # запоминаем нового спикера в галерее
            if self.remember:
                facedb.remember(uid, label, mean, g["thumb"])

        return sorted(persons, key=lambda x: -x["count"])

    # --- 468 точек для одного кадра -------------------------------------
    def landmarks_468(self, video_path: str, ts: float) -> list:
        self._ensure_mesh()
        import cv2
        cap = cv2.VideoCapture(video_path)
        cap.set(cv2.CAP_PROP_POS_MSEC, ts * 1000)
        ok, frame = cap.read()
        cap.release()
        if not ok:
            return []
        h, w = frame.shape[:2]
        res = self._mesh.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        out = []
        if res.multi_face_landmarks:
            for face in res.multi_face_landmarks:
                out.append([[round(p.x * w, 1), round(p.y * h, 1)] for p in face.landmark])
        return out
