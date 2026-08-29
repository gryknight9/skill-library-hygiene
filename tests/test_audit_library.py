from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "skills" / "skill-library-hygiene" / "scripts" / "audit_library.py"
SPEC = importlib.util.spec_from_file_location("audit_library", SCRIPT)
assert SPEC and SPEC.loader
audit_library = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit_library)


class AuditLibraryTests(unittest.TestCase):
    def test_joins_manifest_sizes_to_curator_usage_and_skips_hidden_trees(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            skills = root / "skills"
            skill = skills / "devops" / "example"
            skill.mkdir(parents=True)
            manifest = "---\nname: canonical-example\n---\n# Example\n"
            (skill / "SKILL.md").write_text(manifest, encoding="utf-8")
            archived = skills / ".archive" / "old"
            archived.mkdir(parents=True)
            (archived / "SKILL.md").write_text("ignored", encoding="utf-8")
            usage_json = root / "usage.json"
            usage_json.write_text(
                json.dumps(
                    [
                        {
                            "name": "canonical-example",
                            "use_count": 7,
                            "last_activity_at": "2026-08-29T00:00:00+00:00",
                            "state": "active",
                            "provenance": "agent",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            output = io.StringIO()
            with redirect_stdout(output):
                status = audit_library.main(
                    ["--skills-dir", str(skills), "--usage-json", str(usage_json)]
                )

        self.assertEqual(status, 0)
        rendered = output.getvalue()
        self.assertIn("devops/example", rendered)
        self.assertIn("  7", rendered)
        self.assertNotIn(".archive", rendered)
        self.assertIn("total skills: 1", rendered)


if __name__ == "__main__":
    unittest.main()
