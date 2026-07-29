import unittest

from diffasaurus import __release_label__, __version__


class VersionTests(unittest.TestCase):
    def test_preview_version_and_release_label_agree(self):
        base, release_candidate = __version__.split("rc", 1)
        self.assertEqual(__release_label__, f"{base}-preview.{release_candidate}")


if __name__ == "__main__":
    unittest.main()
