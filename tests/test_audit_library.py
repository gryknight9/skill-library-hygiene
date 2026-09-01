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


class ManifestRootIsolationTests(unittest.TestCase):
    """Regression coverage for issue #2: discovery must not escape the root."""

    def _library(self, root: Path) -> Path:
        skills = root / "skills"
        real = skills / "real"
        real.mkdir(parents=True)
        (real / "SKILL.md").write_text("---\nname: real\n---\n# real\n", encoding="utf-8")
        outside = root / "outside" / "secret"
        outside.mkdir(parents=True)
        (outside / "SKILL.md").write_text(
            "---\nname: outside-secret\n---\n# not library data\n", encoding="utf-8"
        )
        usage = root / "usage.json"
        usage.write_text(
            json.dumps(
                [
                    {"name": "real", "use_count": 3, "state": "active"},
                    {"name": "outside-secret", "use_count": 99, "state": "active"},
                ]
            ),
            encoding="utf-8",
        )
        return skills

    def _run(self, skills: Path, usage: Path) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            status = audit_library.main(
                ["--skills-dir", str(skills), "--usage-json", str(usage)]
            )
        return status, out.getvalue(), err.getvalue()

    def test_symlinked_manifest_file_escape_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            skills = self._library(root)
            link = skills / "filelink"
            link.mkdir()
            (link / "SKILL.md").symlink_to(root / "outside" / "secret" / "SKILL.md")
            status, out, err = self._run(skills, root / "usage.json")

        self.assertEqual(status, 1)
        self.assertIn("ESCAPING MANIFESTS", err)
        self.assertIn("filelink/SKILL.md", err)
        # No out-of-library identity, size, or telemetry may be reported.
        self.assertNotIn("outside-secret", out)
        self.assertNotIn("99", out)
        self.assertNotIn("total chars", out)

    def test_symlinked_parent_directory_escape_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            skills = self._library(root)
            (skills / "dirlink").symlink_to(
                root / "outside" / "secret", target_is_directory=True
            )
            manifests, escaped = audit_library.skill_files(skills)

            # rglob's traversal of symlinked directories is version-dependent;
            # assert the invariant instead: nothing outside the root survives.
            resolved_root = skills.resolve()
            for manifest in manifests:
                self.assertEqual(
                    manifest.resolve().relative_to(resolved_root).parts[0], "real"
                )
            for path in escaped:
                self.assertFalse(
                    str(path.resolve()).startswith(str(resolved_root) + os.sep)
                )

            status, out, _err = self._run(skills, root / "usage.json")

        if escaped:
            self.assertEqual(status, 1)
        else:
            self.assertEqual(status, 0)
            self.assertNotIn("outside-secret", out)

    def test_symlink_inside_the_library_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            skills = self._library(root)
            (skills / "alias").symlink_to(skills / "real", target_is_directory=True)
            status, out, err = self._run(skills, root / "usage.json")

        self.assertEqual(status, 0, msg=err)
        self.assertIn("real", out)
        self.assertNotIn("ESCAPING", err)

    def test_broken_manifest_symlink_is_not_audited(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            skills = self._library(root)
            broken = skills / "broken"
            broken.mkdir()
            (broken / "SKILL.md").symlink_to(root / "nonexistent" / "SKILL.md")
            status, _out, err = self._run(skills, root / "usage.json")

        self.assertEqual(status, 1)
        self.assertIn("broken/SKILL.md", err)


if __name__ == "__main__":
    unittest.main()
