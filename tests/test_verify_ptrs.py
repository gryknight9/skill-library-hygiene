from __future__ import annotations

import importlib.util
import io
import os
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
    / "verify_ptrs.py"
)
SPEC = importlib.util.spec_from_file_location("verify_ptrs", SCRIPT)
assert SPEC and SPEC.loader
verify_ptrs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify_ptrs)


class VerifyPointersTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.library = self.root / "skills"
        self.skill = self.library / "example"
        self.skill.mkdir(parents=True)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def write_skill(self, *pointers: str) -> None:
        body = "---\nname: example\n---\n# Example\n" + "\n".join(pointers) + "\n"
        (self.skill / "SKILL.md").write_text(body, encoding="utf-8")

    def invoke_checker(self) -> tuple[int, str]:
        output = io.StringIO()
        with redirect_stdout(output):
            status = verify_ptrs.main([str(self.skill), str(self.library)])
        return status, output.getvalue()

    def test_default_library_root_uses_hermes_home(self) -> None:
        profile_home = self.root / "profile"
        with patch.dict(os.environ, {"HERMES_HOME": str(profile_home)}):
            self.assertEqual(
                verify_ptrs.default_library_root(), profile_home / "skills"
            )

    def test_existing_local_pointer_passes(self) -> None:
        (self.skill / "references").mkdir()
        (self.skill / "references" / "topic.md").write_text("ok", encoding="utf-8")
        self.write_skill("references/topic.md")

        status, output = self.invoke_checker()

        self.assertEqual(status, 0)
        self.assertIn("all reference pointers resolve safely", output)

    def test_missing_local_pointer_fails(self) -> None:
        self.write_skill("references/missing.md")

        status, output = self.invoke_checker()

        self.assertEqual(status, 1)
        self.assertIn("LOCAL MISSING OR UNSAFE", output)
        self.assertIn("references/missing.md", output)

    def test_cross_skill_pointer_requires_skill_manifest(self) -> None:
        target = self.library / "other-skill" / "references"
        target.mkdir(parents=True)
        (target / "topic.md").write_text("ok", encoding="utf-8")
        self.write_skill("other-skill/references/topic.md")

        status, output = self.invoke_checker()

        self.assertEqual(status, 1)
        self.assertIn("target is not an installed skill", output)

    def test_cross_skill_pointer_requires_matching_manifest_name(self) -> None:
        target = self.library / "other-skill" / "references"
        target.mkdir(parents=True)
        (target.parent / "SKILL.md").write_text(
            "---\nname: different-skill\n---\n# Other\n", encoding="utf-8"
        )
        (target / "topic.md").write_text("ok", encoding="utf-8")
        self.write_skill("other-skill/references/topic.md")

        status, output = self.invoke_checker()

        self.assertEqual(status, 1)
        self.assertIn("manifest name mismatch", output)

    def test_cross_skill_pointer_rejects_invalid_manifest(self) -> None:
        target = self.library / "other-skill" / "references"
        target.mkdir(parents=True)
        (target.parent / "SKILL.md").write_text(
            "---\nname: other-skill\n# missing close\n", encoding="utf-8"
        )
        (target / "topic.md").write_text("ok", encoding="utf-8")
        self.write_skill("other-skill/references/topic.md")

        status, output = self.invoke_checker()

        self.assertEqual(status, 1)
        self.assertIn("invalid skill manifest", output)

    def test_cross_skill_pointer_rejects_mismatched_manifest_quotes(self) -> None:
        target = self.library / "other-skill" / "references"
        target.mkdir(parents=True)
        (target.parent / "SKILL.md").write_text(
            "---\nname: 'other-skill\"\n---\n# Other\n", encoding="utf-8"
        )
        (target / "topic.md").write_text("ok", encoding="utf-8")
        self.write_skill("other-skill/references/topic.md")

        status, output = self.invoke_checker()

        self.assertEqual(status, 1)
        self.assertIn("invalid skill manifest", output)

    def test_cross_skill_pointer_rejects_invalid_utf8_manifest(self) -> None:
        target = self.library / "other-skill" / "references"
        target.mkdir(parents=True)
        (target.parent / "SKILL.md").write_bytes(b"\xff")
        (target / "topic.md").write_text("ok", encoding="utf-8")
        self.write_skill("other-skill/references/topic.md")

        status, output = self.invoke_checker()

        self.assertEqual(status, 1)
        self.assertIn("invalid skill manifest", output)

    def test_cross_skill_pointer_rejects_symlinked_skill_root(self) -> None:
        outside = self.root / "outside-skill"
        outside.mkdir()
        (outside / "SKILL.md").write_text(
            "---\nname: other-skill\n---\n# Outside\n", encoding="utf-8"
        )
        (outside / "references").mkdir()
        (outside / "references" / "topic.md").write_text("outside", encoding="utf-8")
        (self.library / "other-skill").symlink_to(outside, target_is_directory=True)
        self.write_skill("other-skill/references/topic.md")

        status, output = self.invoke_checker()

        self.assertEqual(status, 1)
        self.assertIn("target is not an installed skill", output)

    def test_rejects_symlink_escape(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "topic.md").write_text("secret", encoding="utf-8")
        (self.skill / "references").mkdir()
        (self.skill / "references" / "escape.md").symlink_to(outside / "topic.md")
        self.write_skill("references/escape.md")

        status, output = self.invoke_checker()

        self.assertEqual(status, 1)
        self.assertIn("unsafe path", output)

    def test_existing_cross_skill_pointer_passes(self) -> None:
        target = self.library / "other-skill" / "references"
        target.mkdir(parents=True)
        (target.parent / "SKILL.md").write_text(
            "---\nname: other-skill\n---\n# Other\n", encoding="utf-8"
        )
        (target / "topic.md").write_text("ok", encoding="utf-8")
        self.write_skill("other-skill/references/topic.md")

        status, output = self.invoke_checker()

        self.assertEqual(status, 0)
        self.assertIn("all reference pointers resolve safely", output)

    def test_missing_cross_skill_pointer_fails(self) -> None:
        self.write_skill("other-skill/references/missing.md")

        status, output = self.invoke_checker()

        self.assertEqual(status, 1)
        self.assertIn("CROSS-SKILL MISSING OR UNSAFE", output)
        self.assertIn("other-skill/references/missing.md", output)

    def test_valid_pointer_in_prose_passes(self) -> None:
        (self.skill / "references").mkdir()
        (self.skill / "references" / "topic.md").write_text("ok", encoding="utf-8")
        self.write_skill("See references/topic.md before continuing.")

        status, output = self.invoke_checker()

        self.assertEqual(status, 0)
        self.assertIn("all reference pointers resolve safely", output)

    def test_whitespace_pointer_candidate_fails_closed(self) -> None:
        self.write_skill("references/my topic.md")

        status, output = self.invoke_checker()

        self.assertEqual(status, 1)
        self.assertIn("references/my topic.md", output)

    def test_malformed_suffix_after_md_fails_closed(self) -> None:
        (self.skill / "references").mkdir()
        (self.skill / "references" / "topic.md").write_text("ok", encoding="utf-8")
        for suffix in ("/child", "~backup", "?child", ":child", "+child"):
            with self.subTest(suffix=suffix):
                self.write_skill(f"references/topic.md{suffix}")

                status, output = self.invoke_checker()

                self.assertEqual(status, 1)
                self.assertIn(f"references/topic.md{suffix}", output)

    def test_absolute_pointer_forms_are_rejected(self) -> None:
        self.write_skill(
            "/tmp/references/secret.md",
            "/other-skill/references/secret.md",
        )

        status, output = self.invoke_checker()

        self.assertEqual(status, 1)
        self.assertIn("UNSAFE ABSOLUTE POINTERS", output)
        self.assertIn("/tmp/references/secret.md", output)
        self.assertIn("/other-skill/references/secret.md", output)

    def test_path_traversal_is_rejected(self) -> None:
        outside = self.root / "outside.md"
        outside.write_text("must not resolve", encoding="utf-8")
        self.write_skill("references/../../outside.md")

        status, output = self.invoke_checker()

        self.assertEqual(status, 1)
        self.assertIn("unsafe path", output)

    def test_pointer_control_text_is_escaped(self) -> None:
        self.write_skill("references/bad\x1b\u009b\u202e\u2028.md.bak")

        status, output = self.invoke_checker()

        self.assertEqual(status, 1)
        self.assertNotIn("\x1b", output)
        self.assertNotIn("\u009b", output)
        self.assertNotIn("\u202e", output)
        self.assertNotIn("\u2028", output)
        self.assertIn(r"bad\x1b\x9b\x202e\x2028.md.bak", output)

    def test_missing_skill_file_fails(self) -> None:
        (self.skill / "SKILL.md").unlink(missing_ok=True)

        status, output = self.invoke_checker()

        self.assertEqual(status, 1)
        self.assertIn("MISSING SKILL.md", output)

    def test_invalid_utf8_skill_file_is_a_controlled_error(self) -> None:
        (self.skill / "SKILL.md").write_bytes(b"\xff")

        with self.assertRaises(OSError):
            verify_ptrs.verify(self.skill, self.library)
    def test_symlink_loop_is_a_controlled_error(self) -> None:
        loop_a = self.root / "loop-a"
        loop_b = self.root / "loop-b"
        loop_a.symlink_to(loop_b, target_is_directory=True)
        loop_b.symlink_to(loop_a, target_is_directory=True)
        self.write_skill("other-skill/references/topic.md")

        output = io.StringIO()
        with redirect_stdout(output):
            status = verify_ptrs.main([str(self.skill), str(loop_a)])

        self.assertEqual(status, 1)
        self.assertIn("UNREADABLE SKILL.md", output.getvalue())

    def test_missing_control_path_is_escaped(self) -> None:
        missing = self.root / "missing\x1bskill"
        output = io.StringIO()
        with redirect_stdout(output):
            status = verify_ptrs.main([str(missing), str(self.library)])

        self.assertEqual(status, 1)
        self.assertNotIn("\x1b", output.getvalue())
        self.assertIn(r"missing\x1bskill", output.getvalue())

    def test_missing_format_control_path_is_escaped(self) -> None:
        missing = self.root / "missing\u202eskill"
        output = io.StringIO()
        with redirect_stdout(output):
            status = verify_ptrs.main([str(missing), str(self.library)])

        self.assertEqual(status, 1)
        self.assertNotIn("\u202e", output.getvalue())
        self.assertIn(r"missing\x202eskill", output.getvalue())


if __name__ == "__main__":
    unittest.main()
