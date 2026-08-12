from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPDX_LICENSE = "AGPL-3.0-or-later"


class LicenseMetadataTests(unittest.TestCase):
    def test_repository_contains_complete_agpl_v3_license(self):
        license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")

        self.assertIn("GNU AFFERO GENERAL PUBLIC LICENSE", license_text)
        self.assertIn("Version 3, 19 November 2007", license_text)
        self.assertIn("13. Remote Network Interaction", license_text)
        self.assertIn("END OF TERMS AND CONDITIONS", license_text)

    def test_package_metadata_uses_same_spdx_license(self):
        cargo_toml = (ROOT / "rust/deckview_core/Cargo.toml").read_text(
            encoding="utf-8"
        )
        pyproject = (ROOT / "rust/deckview_core/pyproject.toml").read_text(
            encoding="utf-8"
        )

        self.assertIn(f'license = "{SPDX_LICENSE}"', cargo_toml)
        self.assertIn(f'license = "{SPDX_LICENSE}"', pyproject)

    def test_public_documentation_describes_network_copyleft(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        normalized_readme = " ".join(readme.split())

        self.assertIn(SPDX_LICENSE, readme)
        self.assertIn(
            "изменённая версия взаимодействует с пользователями по сети",
            normalized_readme,
        )


if __name__ == "__main__":
    unittest.main()
