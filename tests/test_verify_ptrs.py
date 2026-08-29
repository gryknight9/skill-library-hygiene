from __future__ import annotations

import importlib.util
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).parents[1] / "skills" / "skill-library-hygiene" / "scripts" / "verify_ptrs.py"
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
            self.assertEqual(verify_ptrs.default_library_root(), profile_home / "skills")

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

    def test_existing_cross_skill_pointer_passes(self) -> None:
        target = self.library / "other-skill" / "references"
        target.mkdir(parents=True)
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

    def test_missing_skill_file_fails(self) -> None:
        (self.skill / "SKILL.md").unlink(missing_ok=True)

        status, output = self.invoke_checker()

        self.assertEqual(status, 1)
        self.assertIn("MISSING SKILL.md", output)


if __name__ == "__main__":
    unittest.main()
