from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


SCRIPT = (
    Path(__file__).parents[1]
    / "skills"
    / "skill-library-hygiene"
    / "scripts"
    / "audit_library.py"
)
SPEC = importlib.util.spec_from_file_location("audit_library", SCRIPT)
assert SPEC and SPEC.loader
audit_library = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit_library)


class AuditLibraryTests(unittest.TestCase):
    def test_explicit_profile_is_passed_to_curator(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            skills = Path(tempdir) / "profile" / "skills"
            usage_json = Path(tempdir) / "usage.json"
            usage_json.write_text("[]", encoding="utf-8")
            with patch.object(audit_library.subprocess, "run") as run:
                run.return_value.returncode = 0
                run.return_value.stdout = "[]"
                run.return_value.stderr = ""
                audit_library.load_usage(None, skills.parent)
                self.assertEqual(
                    run.call_args.kwargs["env"]["HERMES_HOME"], str(skills.parent)
                )

    def test_escapes_curator_error_output(self) -> None:
        with patch.object(audit_library.subprocess, "run") as run:
            run.return_value.returncode = 1
            run.return_value.stdout = ""
            run.return_value.stderr = "\x1b[31mcurator failed"
            with self.assertRaisesRegex(RuntimeError, r"\\x1b\[31m"):
                audit_library.load_usage(None)

    def test_rejects_non_integer_usage_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            usage_json = root / "usage.json"
            usage_json.write_text(
                json.dumps([{"name": "example", "use_count": "many"}]),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                audit_library.load_usage(usage_json)

    def test_rejects_malformed_usage_records(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            usage_json = Path(tempdir) / "usage.json"
            usage_json.write_text(json.dumps([{"use_count": 1}]), encoding="utf-8")

            with self.assertRaises(ValueError):
                audit_library.load_usage(usage_json)

    def test_rejects_non_scalar_optional_usage_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            usage_json = Path(tempdir) / "usage.json"
            usage_json.write_text(
                json.dumps([{"name": "example", "state": {}}]), encoding="utf-8"
            )

            with self.assertRaises(ValueError):
                audit_library.load_usage(usage_json)

    def test_rejects_control_characters_in_optional_usage_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            usage_json = Path(tempdir) / "usage.json"
            usage_json.write_text(
                json.dumps([{"name": "example", "state": "active\u001b[31m"}]),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                audit_library.load_usage(usage_json)

    def test_rejects_control_characters_in_usage_name(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            usage_json = Path(tempdir) / "usage.json"
            usage_json.write_text(
                json.dumps([{"name": "example\u0000", "use_count": "bad"}]),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                audit_library.load_usage(usage_json)

    def test_rejects_c1_control_characters_in_optional_usage_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            usage_json = Path(tempdir) / "usage.json"
            usage_json.write_text(
                json.dumps([{"name": "example", "state": "active\u009b"}]),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                audit_library.load_usage(usage_json)

    def test_rejects_unicode_format_controls_in_optional_usage_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            usage_json = Path(tempdir) / "usage.json"
            usage_json.write_text(
                json.dumps([{"name": "example", "state": "active\u202e"}]),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                audit_library.load_usage(usage_json)

    def test_rejects_invalid_usage_name_grammar(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            usage_json = Path(tempdir) / "usage.json"
            usage_json.write_text(
                json.dumps([{"name": "example skill", "use_count": 1}]),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                audit_library.load_usage(usage_json)

    def test_rejects_unicode_line_separators_in_optional_usage_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            usage_json = Path(tempdir) / "usage.json"
            usage_json.write_text(
                json.dumps([{"name": "example", "state": "active\u2028"}]),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                audit_library.load_usage(usage_json)

    def test_escapes_control_characters_in_relative_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            skills = root / "skills"
            skill = skills / "bad\x1bname"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\nname: example\n---\n# Example\n", encoding="utf-8"
            )
            usage_json = root / "usage.json"
            usage_json.write_text("[]", encoding="utf-8")

            output = io.StringIO()
            with redirect_stdout(output):
                status = audit_library.main(
                    ["--skills-dir", str(skills), "--usage-json", str(usage_json)]
                )

        self.assertEqual(status, 0)
        self.assertNotIn("\x1b", output.getvalue())
        self.assertIn(r"bad\x1bname", output.getvalue())

    def test_rejects_duplicate_manifest_names(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            skills = root / "skills"
            for path in (skills / "one", skills / "two"):
                path.mkdir(parents=True)
                (path / "SKILL.md").write_text(
                    "---\nname: duplicate\n---\n# Skill\n", encoding="utf-8"
                )
            usage_json = root / "usage.json"
            usage_json.write_text("[]", encoding="utf-8")

            self.assertEqual(
                audit_library.main(
                    ["--skills-dir", str(skills), "--usage-json", str(usage_json)]
                ),
                1,
            )

    def test_rejects_unterminated_frontmatter(self) -> None:
        with self.assertRaises(ValueError):
            audit_library.manifest_skill_name(
                "---\nname: example\n# body\n", Path("SKILL.md")
            )

    def test_rejects_mismatched_manifest_quotes(self) -> None:
        with self.assertRaises(ValueError):
            audit_library.manifest_skill_name(
                "---\nname: 'example\"\n---\n# body\n", Path("SKILL.md")
            )

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
