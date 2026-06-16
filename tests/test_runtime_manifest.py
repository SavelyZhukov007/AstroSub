import unittest

from app.core.runtime import packages_for


class RuntimeManifestTests(unittest.TestCase):
    def test_packages_are_deduplicated_in_order(self):
        pkgs = packages_for(["asr", "emotion", "video", "emotion"])
        self.assertEqual(pkgs.count("openvino>=2024,<2027"), 1)
        self.assertEqual(pkgs[-1], "opencv-python>=4.9")

    def test_removed_feature_keys_do_not_add_packages(self):
        pkgs = packages_for(["legacy-a", "legacy-b", "legacy-c"])
        self.assertEqual(pkgs, [])


if __name__ == "__main__":
    unittest.main()
