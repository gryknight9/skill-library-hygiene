from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from typing import Any
from unittest import mock


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


class TelemetryProfileScopeTests(unittest.TestCase):
    """Regression coverage for issue #1: telemetry must follow --skills-dir."""

    def _profile(self, root: Path, name: str) -> Path:
        home = root / "profiles" / name
        skill = home / "skills" / "shared" / "collide"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: collide\n---\n# Collide\n", encoding="utf-8"
        )
        return home

    def test_curator_subprocess_receives_selected_profile_home(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            target = self._profile(root, "noc")
            ambient = self._profile(root, "nous")
            captured: dict[str, Any] = {}

            def fake_run(cmd, **kwargs):
                captured["cmd"] = cmd
                captured["env"] = kwargs["env"]
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout=json.dumps(
                        [{"name": "collide", "use_count": 42, "state": "active"}]
                    ),
                    stderr="",
                )

            env = dict(os.environ, HERMES_HOME=str(ambient))
            output = io.StringIO()
            with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(
                audit_library.subprocess, "run", fake_run
            ), redirect_stdout(output):
                status = audit_library.main(
                    ["--skills-dir", str(target / "skills")]
                )

        self.assertEqual(status, 0)
        self.assertEqual(captured["cmd"], ["hermes", "curator", "usage", "--json"])
        # The subprocess must see the AUDITED profile, not the ambient one.
        self.assertEqual(
            Path(captured["env"]["HERMES_HOME"]).resolve(), target.resolve()
        )
        self.assertNotEqual(
            Path(captured["env"]["HERMES_HOME"]).resolve(), ambient.resolve()
        )
        rendered = output.getvalue()
        self.assertIn(" 42 ", rendered)
        self.assertIn(str(target.resolve()), rendered)

    def test_contradictory_hermes_home_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            target = self._profile(root, "noc")
            other = self._profile(root, "nous")
            err = io.StringIO()
            with redirect_stderr(err):
                status = audit_library.main(
                    [
                        "--skills-dir",
                        str(target / "skills"),
                        "--hermes-home",
                        str(other),
                    ]
                )
        self.assertEqual(status, 1)
        self.assertIn("ambiguous profile", err.getvalue())

    def test_underivable_skills_dir_requires_explicit_home(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            odd = root / "not-a-profile-layout"
            odd.mkdir()
            err = io.StringIO()
            with redirect_stderr(err):
                status = audit_library.main(["--skills-dir", str(odd)])
        self.assertEqual(status, 1)
        self.assertIn("cannot derive a Hermes profile", err.getvalue())

    def test_usage_json_and_hermes_home_are_mutually_exclusive(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            target = self._profile(root, "noc")
            usage_json = root / "usage.json"
            usage_json.write_text("[]", encoding="utf-8")
            err = io.StringIO()
            with redirect_stderr(err):
                status = audit_library.main(
                    [
                        "--skills-dir",
                        str(target / "skills"),
                        "--hermes-home",
                        str(target),
                        "--usage-json",
                        str(usage_json),
                    ]
                )
        self.assertEqual(status, 1)
        self.assertIn("mutually exclusive", err.getvalue())


if __name__ == "__main__":
    unittest.main()
