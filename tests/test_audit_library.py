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


class CuratorRecordValidationTests(unittest.TestCase):
    """Regression coverage for issue #3: malformed telemetry must fail closed."""

    def _run(self, payload: str) -> tuple[int, str, str]:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            skill = root / "skills" / "a"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\nname: alpha\n---\n# a\n", encoding="utf-8"
            )
            usage = root / "usage.json"
            usage.write_text(payload, encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                status = audit_library.main(
                    ["--skills-dir", str(root / "skills"), "--usage-json", str(usage)]
                )
            return status, out.getvalue(), err.getvalue()

    def assert_rejected(self, payload: str, expected: str) -> None:
        status, out, err = self._run(payload)
        self.assertEqual(status, 1, msg=f"expected rejection, got:\n{out}")
        self.assertIn("USAGE TELEMETRY ERROR", err)
        self.assertIn(expected, err)
        # Fail closed: no partial report may be emitted.
        self.assertNotIn("total chars", out)

    def test_non_numeric_use_count_is_rejected_not_raised(self) -> None:
        self.assert_rejected(
            '[{"name": "alpha", "use_count": "unknown"}]', "use_count must be an integer"
        )

    def test_float_use_count_is_rejected(self) -> None:
        self.assert_rejected(
            '[{"name": "alpha", "use_count": 1.5}]', "use_count must be an integer"
        )

    def test_boolean_use_count_is_rejected(self) -> None:
        # bool subclasses int, so True would otherwise be reported as 1 use.
        self.assert_rejected(
            '[{"name": "alpha", "use_count": true}]', "use_count must be an integer"
        )

    def test_negative_use_count_is_rejected(self) -> None:
        self.assert_rejected(
            '[{"name": "alpha", "use_count": -5}]', "must not be negative"
        )

    def test_duplicate_names_are_rejected_not_last_wins(self) -> None:
        payload = json.dumps(
            [
                {"name": "alpha", "use_count": 1},
                {"name": "alpha", "use_count": 900},
            ]
        )
        self.assert_rejected(payload, "duplicate name 'alpha'")
        _status, out, _err = self._run(payload)
        self.assertNotIn("900", out)

    def test_control_characters_in_display_fields_are_rejected(self) -> None:
        self.assert_rejected(
            json.dumps([{"name": "alpha", "state": "act\x1b[31mive"}]),
            "state contains control characters",
        )
        self.assert_rejected(
            json.dumps([{"name": "alpha", "provenance": "x\ny"}]),
            "provenance contains control characters",
        )

    def test_oversized_display_field_is_rejected(self) -> None:
        self.assert_rejected(
            json.dumps([{"name": "alpha", "state": "x" * 201}]), "exceeds 200 characters"
        )

    def test_missing_and_malformed_names_are_rejected(self) -> None:
        self.assert_rejected('[{"use_count": 1}]', "name must be a non-empty string")
        self.assert_rejected('[{"name": "", "use_count": 1}]', "non-empty string")
        self.assert_rejected('[{"name": 42, "use_count": 1}]', "non-empty string")

    def test_non_object_record_and_non_list_payload_are_rejected(self) -> None:
        self.assert_rejected('["alpha"]', "must be an object")
        self.assert_rejected('{"name": "alpha"}', "must be a list")

    def test_valid_records_still_pass_and_preserve_unknown_fields(self) -> None:
        # Mirrors the real curator schema, including fields this script ignores.
        payload = json.dumps(
            [
                {
                    "name": "alpha",
                    "use_count": 12,
                    "state": "active",
                    "provenance": "agent",
                    "last_activity_at": "2026-08-29T00:00:00+00:00",
                    "pinned": False,
                    "patch_generation": 0,
                    "archived_at": None,
                }
            ]
        )
        status, out, err = self._run(payload)
        self.assertEqual(status, 0, msg=err)
        self.assertIn("12", out)
        self.assertIn("agent", out)
        usage = audit_library.validate_records(json.loads(payload))
        self.assertEqual(usage["alpha"]["patch_generation"], 0)
        self.assertIs(usage["alpha"]["pinned"], False)

    def test_null_use_count_and_null_display_fields_are_tolerated(self) -> None:
        # Real curator output uses null for never-used skills.
        status, out, err = self._run(
            json.dumps(
                [{"name": "alpha", "use_count": None, "last_activity_at": None}]
            )
        )
        self.assertEqual(status, 0, msg=err)
        self.assertIn("total skills: 1", out)


class ManifestFrontmatterTests(unittest.TestCase):
    """Regression coverage for issue #4: frontmatter must be well-formed."""

    SOURCE = Path("skills/example/SKILL.md")

    def parse(self, text: str) -> str:
        return audit_library.manifest_skill_name(text, self.SOURCE)

    def assert_rejected(self, text: str, expected: str) -> None:
        with self.assertRaises(ValueError) as caught:
            self.parse(text)
        self.assertIn(expected, str(caught.exception))

    def test_unterminated_frontmatter_is_rejected(self) -> None:
        # The reported case: valid name, no closing delimiter.
        self.assert_rejected(
            "---\nname: example\n# no closing delimiter\n", "unterminated frontmatter"
        )

    def test_unterminated_with_nothing_after_name_is_rejected(self) -> None:
        self.assert_rejected("---\nname: example\n", "unterminated frontmatter")

    def test_duplicate_name_fields_are_rejected(self) -> None:
        self.assert_rejected(
            "---\nname: first\nname: second\n---\n", "duplicate frontmatter name"
        )

    def test_opening_delimiter_must_be_its_own_line(self) -> None:
        # startswith("---") also matched these; a --- line does not.
        self.assert_rejected("----\nname: example\n---\n", "must start with a --- line")
        self.assert_rejected("--- yaml\nname: example\n---\n", "must start with a --- line")

    def test_name_below_the_closing_delimiter_is_not_frontmatter(self) -> None:
        self.assert_rejected("---\ntitle: x\n---\nname: notreally\n", "missing frontmatter name")

    def test_empty_and_missing_frontmatter_are_rejected(self) -> None:
        self.assert_rejected("---\n---\n# body\n", "missing frontmatter name")
        self.assert_rejected("", "must start with a --- line")
        self.assert_rejected("# just a heading\n", "must start with a --- line")

    def test_invalid_name_values_are_rejected(self) -> None:
        self.assert_rejected("---\nname: Example\n---\n", "invalid frontmatter name")
        self.assert_rejected("---\nname: has space\n---\n", "invalid frontmatter name")
        self.assert_rejected("---\nname:\n---\n", "invalid frontmatter name")

    def test_well_formed_manifests_are_accepted(self) -> None:
        self.assertEqual(self.parse("---\nname: example\n---\n# body\n"), "example")
        self.assertEqual(self.parse('---\nname: "quoted-name"\n---\n'), "quoted-name")
        self.assertEqual(self.parse("---\nname:   spaced\n---\n"), "spaced")
        # CRLF manifests are legitimate; splitlines() handles them.
        self.assertEqual(self.parse("---\r\nname: example\r\n---\r\n"), "example")
        # A --- rule in the body must not be mistaken for frontmatter.
        self.assertEqual(
            self.parse("---\nname: example\n---\n# body\n\n---\n\nmore\n"), "example"
        )

    def test_malformed_manifest_aborts_the_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            skill = root / "skills" / "bad"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("---\nname: example\n", encoding="utf-8")
            usage = root / "usage.json"
            usage.write_text('[{"name": "example", "use_count": 5}]', encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                status = audit_library.main(
                    ["--skills-dir", str(root / "skills"), "--usage-json", str(usage)]
                )

        self.assertEqual(status, 1)
        self.assertIn("MANIFEST ERROR", err.getvalue())
        self.assertIn("unterminated frontmatter", err.getvalue())
        # The unintended identity must not pick up telemetry.
        self.assertNotIn("5", out.getvalue())


if __name__ == "__main__":
    unittest.main()
