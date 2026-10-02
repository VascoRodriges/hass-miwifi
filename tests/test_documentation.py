"""Documentation must stay aligned with the shipped API, without executing actions."""

import json
from pathlib import Path
import re
import unittest

import yaml

from custom_components.miwifi.api_map import MIWIFI_API_MAP
from custom_components.miwifi.services import SERVICES

ROOT = Path(__file__).resolve().parents[1]


class DocumentationTests(unittest.TestCase):
    def setUp(self):
        self.english = (ROOT / "README.md").read_text(encoding="utf-8")
        self.russian = (ROOT / "README.ru.md").read_text(encoding="utf-8")

    def test_versions_and_hacs_minimum_are_documented(self):
        manifest = json.loads(
            (ROOT / "custom_components/miwifi/manifest.json").read_text()
        )
        hacs = json.loads((ROOT / "hacs.json").read_text())
        for text in (self.english, self.russian):
            self.assertIn(manifest["version"], text)
            self.assertIn(hacs["homeassistant"], text)
        self.assertEqual(
            manifest["documentation"],
            "https://github.com/VascoRodriges/hass-miwifi#readme",
        )

    def test_service_table_has_exactly_the_registered_services(self):
        documented = re.findall(r"^\| `([a-z_]+)` \|", self.english, re.M)
        self.assertEqual(len(documented), len(set(documented)))
        self.assertEqual(set(documented), {name for name, _ in SERVICES})

    def test_documented_api_endpoints_exist_in_map(self):
        documented = set(
            re.findall(r"^- `((?:xq\w+|misystem)/[a-z_]+)`", self.english, re.M)
        )
        self.assertTrue(documented)
        self.assertFalse(
            documented - {action["endpoint"] for action in MIWIFI_API_MAP.values()}
        )
        self.assertIn("misystem/qos_limits", documented)

    def test_yaml_examples_pass_real_action_schemas_without_execution(self):
        handlers = dict(SERVICES)
        for language, text in (("en", self.english), ("ru", self.russian)):
            examples = re.findall(r"```yaml\n(.*?)\n```", text, re.S)
            self.assertEqual(len(examples), 3, language)
            for example in examples:
                for step in yaml.safe_load(example)["sequence"]:
                    domain, action = step["action"].split(".", 1)
                    self.assertEqual(domain, "miwifi")
                    handler = handlers[action]
                    handler.schema(step["data"])
                    if "response_variable" in step:
                        self.assertTrue(
                            getattr(handler, "supports_response", False), action
                        )
                    if action == "cleanup_stale_clients":
                        self.assertTrue(step["data"]["dry_run"])

    def test_local_markdown_links_exist(self):
        for text in (self.english, self.russian):
            for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
                if target.startswith(("http://", "https://", "#")):
                    continue
                self.assertTrue((ROOT / target.split("#", 1)[0]).is_file(), target)

    def test_original_author_and_release_branch_distinction_are_present(self):
        for text in (self.english, self.russian):
            self.assertIn("Dmitry Mamontov", text)
            self.assertIn("dmamontov/hass-miwifi", text)
            self.assertIn("v4.0.0", text)
            self.assertIn("main", text)
            self.assertIn("verified: false", text)


if __name__ == "__main__":
    unittest.main()
