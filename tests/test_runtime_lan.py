import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from urllib.request import Request, urlopen

from app import config
from app.core.lan import LanServer
from app.core.runtime import packages_for


def http_json(url, payload=None, headers=None):
    data = None
    req_headers = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        req_headers["Content-Type"] = "application/json"
    req = Request(url, data=data, headers=req_headers)
    with urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


class RuntimeManifestTests(unittest.TestCase):
    def test_gpu_replaces_cpu_onnxruntime(self):
        pkgs = packages_for(["faces", "gpu"])
        self.assertIn("onnxruntime-gpu>=1.17", pkgs)
        self.assertNotIn("onnxruntime>=1.17", pkgs)

    def test_packages_are_deduplicated_in_order(self):
        pkgs = packages_for(["faces", "faces", "video"])
        self.assertEqual(pkgs.count("insightface>=0.7.3"), 1)
        self.assertEqual(pkgs[-1], "opencv-python>=4.9")


class LanFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old_devices = config.DEVICES_PATH
        self.old_jobs = config.LAN_JOBS_PATH
        self.old_uploads = config.uploads_dir
        config.DEVICES_PATH = root / "devices.json"
        config.LAN_JOBS_PATH = root / "jobs.json"
        config.uploads_dir = lambda: root / "uploads"
        self.web = root / "web"
        self.web.mkdir()
        (self.web / "index.html").write_text("ok", encoding="utf-8")
        self.server = LanServer(self.web)
        self.server.start()
        self.base = f"http://127.0.0.1:{self.server.port}"

    def tearDown(self):
        self.server.stop()
        config.DEVICES_PATH = self.old_devices
        config.LAN_JOBS_PATH = self.old_jobs
        config.uploads_dir = self.old_uploads
        self.tmp.cleanup()

    def test_untrusted_job_requires_approval_then_accepts_chunks(self):
        device = {"id": "phone-1", "name": "Phone", "kind": "ios-web"}
        created = http_json(self.base + "/api/jobs", {
            "filename": "clip.txt",
            "size": 10,
            "options": {"subtitles": True},
            "device": device,
        })
        self.assertEqual(created["job"]["status"], "pending")

        approved = self.server.approve_job(created["job"]["id"], True)
        self.assertEqual(approved["status"], "accepted")

        raw = b"helloworld"
        digest = hashlib.sha256(raw).hexdigest()
        req = Request(
            self.base + f"/api/jobs/{created['job']['id']}/chunks",
            data=raw,
            headers={
                "X-Chunk-Index": "0",
                "X-Chunk-Sha256": digest,
                "X-Device-Id": device["id"],
            },
            method="POST",
        )
        with urlopen(req, timeout=5) as r:
            uploaded = json.loads(r.read().decode("utf-8"))
        self.assertTrue(uploaded["ok"])

        done = http_json(self.base + f"/api/jobs/{created['job']['id']}/complete", {})
        self.assertEqual(done["job"]["status"], "queued")
        self.assertEqual(Path(done["job"]["video_path"]).read_bytes(), raw)


if __name__ == "__main__":
    unittest.main()
