# -*- coding: utf-8 -*-
"""
Сервис FaceID — съёмка и разбор лица пользователя.

Пользователь записывает короткое видео своего лица (фронтальная камера в UI),
кадры приходят сюда как base64-JPEG. На каждом кадре:
  - InsightFace (buffalo_l) -> 512-мерный эмбеддинг, пол и возраст
    (модель genderage), 106 опорных точек (landmark_2d_106);
  - MediaPipe FaceMesh -> 468 точек (если установлен) для плотной сетки.
Всё сводится в JSON: покадровые координаты точек, агрегированные пол/возраст,
качество. Затем по этим данным локальная модель Qwen делает вывод, который
можно обсудить в чате.

Это локальная замена связки OpenVINO age/gender: используется ArcFace +
genderage из InsightFace, что даёт сопоставимое или лучшее качество офлайн.
"""
from __future__ import annotations

import base64
import json
import time
import uuid
from typing import Callable, List, Optional

from .. import config


ACTION_TITLES = {
    "front": "смотреть прямо",
    "turn_left": "повернуть голову влево",
    "turn_right": "повернуть голову вправо",
    "chin_up": "поднять подбородок",
    "chin_down": "опустить подбородок",
    "smile": "улыбнуться",
}


def _decode(b64: str):
    import cv2
    import numpy as np

    if "," in b64:
        b64 = b64.split(",", 1)[1]
    raw = base64.b64decode(b64)
    arr = np.frombuffer(raw, dtype=np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def _payload(item):
    if isinstance(item, dict):
        return (
            item.get("image") or item.get("data") or item.get("b64") or "",
            item.get("action") or "",
            item.get("prompt") or ACTION_TITLES.get(item.get("action") or "", ""),
        )
    return item, "", ""


def _pose_metrics(face) -> dict:
    import numpy as np

    kps = getattr(face, "kps", None)
    if kps is None:
        return {}
    pts = np.asarray(kps, dtype=np.float32)
    if pts.shape[0] < 5:
        return {}

    left_eye, right_eye, nose, left_mouth, right_mouth = pts[:5]
    eye_mid = (left_eye + right_eye) / 2
    mouth_mid = (left_mouth + right_mouth) / 2
    eye_dist = float(np.linalg.norm(right_eye - left_eye)) + 1e-6
    face_height = float(np.linalg.norm(mouth_mid - eye_mid)) + 1e-6
    return {
        "turn": float((nose[0] - eye_mid[0]) / eye_dist),
        "chin": float((nose[1] - eye_mid[1]) / face_height),
        "smile": float(np.linalg.norm(right_mouth - left_mouth) / eye_dist),
    }


def _median(rows, key: str, action: str) -> Optional[float]:
    import numpy as np

    vals = [r["pose"][key] for r in rows
            if r.get("action") == action and r.get("pose") and key in r["pose"]]
    if not vals:
        return None
    return float(np.median(vals))


def _validate_actions(frames_data: list) -> dict:
    actions = {f.get("action") for f in frames_data if f.get("action")}
    if not actions:
        return {"ok": True, "checks": []}

    checks = []

    def add(action: str, ok: bool, score: float = 0.0):
        checks.append({
            "action": action,
            "title": ACTION_TITLES.get(action, action),
            "ok": bool(ok),
            "score": round(float(score or 0.0), 3),
        })

    front_turn = _median(frames_data, "turn", "front")
    front_chin = _median(frames_data, "chin", "front")
    front_smile = _median(frames_data, "smile", "front")

    if "front" in actions:
        add("front", front_turn is not None and abs(front_turn) < 0.34,
            abs(front_turn or 0.0))

    left_turn = _median(frames_data, "turn", "turn_left")
    right_turn = _median(frames_data, "turn", "turn_right")
    if {"turn_left", "turn_right"} & actions:
        if left_turn is not None and right_turn is not None:
            diff = abs(left_turn - right_turn)
            ok = diff >= 0.12
            if "turn_left" in actions:
                add("turn_left", ok, diff)
            if "turn_right" in actions:
                add("turn_right", ok, diff)
        else:
            if "turn_left" in actions:
                score = abs((left_turn or 0.0) - (front_turn or 0.0))
                add("turn_left", left_turn is not None and score >= 0.08, score)
            if "turn_right" in actions:
                score = abs((right_turn or 0.0) - (front_turn or 0.0))
                add("turn_right", right_turn is not None and score >= 0.08, score)

    up_chin = _median(frames_data, "chin", "chin_up")
    down_chin = _median(frames_data, "chin", "chin_down")
    if {"chin_up", "chin_down"} & actions:
        if up_chin is not None and down_chin is not None:
            diff = abs(up_chin - down_chin)
            ok = diff >= 0.06
            if "chin_up" in actions:
                add("chin_up", ok, diff)
            if "chin_down" in actions:
                add("chin_down", ok, diff)
        else:
            if "chin_up" in actions:
                score = abs((up_chin or 0.0) - (front_chin or 0.0))
                add("chin_up", up_chin is not None and score >= 0.04, score)
            if "chin_down" in actions:
                score = abs((down_chin or 0.0) - (front_chin or 0.0))
                add("chin_down", down_chin is not None and score >= 0.04, score)

    smile = _median(frames_data, "smile", "smile")
    if "smile" in actions:
        baseline = front_smile
        if baseline is None:
            neutral = [f["pose"]["smile"] for f in frames_data
                       if f.get("pose") and f.get("action") != "smile"]
            baseline = min(neutral) if neutral else None
        gain = (smile - baseline) if smile is not None and baseline is not None else 0.0
        add("smile", smile is not None and baseline is not None and gain >= max(0.04, baseline * 0.04), gain)

    failed = [c for c in checks if not c["ok"]]
    return {
        "ok": not failed,
        "checks": checks,
        "failed": failed,
    }


class FaceIDService:
    def __init__(self, device="cpu"):
        self.device = device
        self._app = None
        self._mesh = None

    def _ensure(self):
        if self._app is not None:
            return
        from insightface.app import FaceAnalysis
        ctx = 0 if self.device == "cuda" else -1
        app = FaceAnalysis(name="buffalo_l")  # detection + recognition + genderage + landmark
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
        try:
            import mediapipe as mp
            self._mesh = mp.solutions.face_mesh.FaceMesh(
                static_image_mode=True, max_num_faces=1,
                refine_landmarks=True, min_detection_confidence=0.5)
        except Exception:
            self._mesh = False  # недоступно

    # ------------------------------------------------------------------ #
    def enroll(self, frames_b64: List[str], label: str = "",
               on_progress: Optional[Callable[[float, str], None]] = None) -> dict:
        self._ensure()
        self._ensure_mesh()
        import cv2
        import numpy as np

        uid = uuid.uuid4().hex[:12]
        frames_data = []
        embeddings = []
        ages, genders = [], []
        best_score, best_jpg = -1.0, None

        total = max(1, len(frames_b64))
        for idx, item in enumerate(frames_b64):
            b64, action, prompt = _payload(item)
            if not b64:
                continue
            img = _decode(b64)
            if img is None:
                continue
            faces = self._app.get(img)
            if not faces:
                continue
            f = max(faces, key=lambda x: (x.bbox[2] - x.bbox[0]) * (x.bbox[3] - x.bbox[1]))
            pose = _pose_metrics(f)
            emb = getattr(f, "normed_embedding", None)
            if emb is not None:
                embeddings.append(np.asarray(emb, dtype=np.float32))
            age = getattr(f, "age", None)
            gender = getattr(f, "gender", None)  # 1=муж, 0=жен (insightface)
            if age is not None:
                ages.append(float(age))
            if gender is not None:
                genders.append(int(gender))

            lm106 = getattr(f, "landmark_2d_106", None)
            pts106 = ([[round(float(x), 1), round(float(y), 1)] for x, y in lm106.tolist()]
                      if lm106 is not None else [])

            pts468 = []
            if self._mesh:
                res = self._mesh.process(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
                if res.multi_face_landmarks:
                    h, w = img.shape[:2]
                    pts468 = [[round(p.x * w, 1), round(p.y * h, 1)]
                              for p in res.multi_face_landmarks[0].landmark]

            frames_data.append({
                "frame": idx,
                "bbox": [int(v) for v in f.bbox.tolist()],
                "det_score": round(float(getattr(f, "det_score", 0.0)), 3),
                "landmarks_106": pts106,
                "landmarks_468": pts468,
                "age": age, "gender": ("муж" if gender == 1 else "жен") if gender is not None else None,
                "action": action,
                "prompt": prompt,
                "pose": pose,
            })

            score = float(getattr(f, "det_score", 0.0))
            if score > best_score:
                best_score = score
                ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 88])
                if ok:
                    best_jpg = buf

            if on_progress:
                on_progress(min(0.95, (idx + 1) / total), f"Кадр {idx + 1} из {total}")

        if not embeddings:
            if on_progress:
                on_progress(1.0, "Лицо не распознано")
            return {"ok": False, "error": "Не удалось обнаружить лицо. Освещение/ракурс."}

        if on_progress:
            on_progress(0.96, "Проверка подсказок")
        validation = _validate_actions(frames_data)
        if not validation["ok"]:
            missed = ", ".join(c["title"] for c in validation.get("failed", []))
            if on_progress:
                on_progress(1.0, "Подсказки не выполнены")
            return {
                "ok": False,
                "error": "Повторите запись: не удалось подтвердить действия — " + missed,
                "validation": validation,
            }

        mean_emb = np.mean(np.vstack(embeddings), axis=0)
        mean_emb = mean_emb / (np.linalg.norm(mean_emb) + 1e-9)
        age_val = round(float(np.median(ages)), 1) if ages else None
        gender_val = ("муж" if np.mean(genders) >= 0.5 else "жен") if genders else None

        # превью
        thumb_path = None
        if best_jpg is not None:
            thumb_path = str(config.faceid_dir() / f"{uid}.jpg")
            best_jpg.tofile(thumb_path)

        dataset = {
            "uid": uid,
            "label": label or "Моё лицо",
            "created": time.time(),
            "frames_total": len(frames_data),
            "embedding": mean_emb.tolist(),
            "age": age_val,
            "gender": gender_val,
            "quality": round(float(best_score), 3),
            "thumb": thumb_path,
            "frames": frames_data,
            "validation": validation,
        }
        path = config.faceid_dir() / f"{uid}.json"
        path.write_text(json.dumps(dataset, ensure_ascii=False), encoding="utf-8")

        if on_progress:
            on_progress(1.0, "Готово")

        # компактная сводка без покадровых точек — для UI и для Qwen
        return {
            "ok": True, "uid": uid, "path": str(path),
            "label": dataset["label"], "age": age_val, "gender": gender_val,
            "frames_total": len(frames_data), "quality": dataset["quality"],
            "thumb": thumb_path,
            "embedding": mean_emb.tolist(),
            "validation": validation,
        }

    @staticmethod
    def list_enrollments() -> list:
        out = []
        for p in config.faceid_dir().glob("*.json"):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                out.append({
                    "uid": d["uid"], "label": d.get("label", "—"),
                    "age": d.get("age"), "gender": d.get("gender"),
                    "frames_total": d.get("frames_total", 0),
                    "quality": d.get("quality"), "thumb": d.get("thumb"),
                    "created": d.get("created", 0),
                })
            except Exception:
                continue
        return sorted(out, key=lambda x: -x["created"])

    @staticmethod
    def load(uid: str) -> dict:
        return json.loads((config.faceid_dir() / f"{uid}.json").read_text(encoding="utf-8"))

    @staticmethod
    def delete(uid: str) -> bool:
        j = config.faceid_dir() / f"{uid}.json"
        t = config.faceid_dir() / f"{uid}.jpg"
        ok = j.exists()
        for f in (j, t):
            if f.exists():
                f.unlink()
        return ok
