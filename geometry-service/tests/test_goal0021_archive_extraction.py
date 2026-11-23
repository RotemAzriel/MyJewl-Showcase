#!/usr/bin/env python3
"""Goal 0021 -- the Compress-Archive backslash gotcha, fixed at the extraction point.

PowerShell writes Windows separators inside the zip, so ``zipfile`` extracted a
whole bundle tree as flat files with literal backslashes in their names. It cost
manual renaming twice (Goal 0014, Goal 0018). ``_safe_extract`` now normalises,
and these tests hold it there -- including the containment check that must not be
weakened by the normalisation.
"""

from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from scripts.goal0011_azure_master_run import (  # noqa: E402
    _safe_extract,
    normalize_archive_member,
)


def _write_archive(path: pathlib.Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)


class NormalizeMemberTests(unittest.TestCase):
    def test_backslashes_become_directories(self):
        self.assertEqual(
            normalize_archive_member("job-name\\part.stl"), "job-name/part.stl"
        )

    def test_deep_windows_paths_are_handled(self):
        self.assertEqual(
            normalize_archive_member("a\\b\\c\\part.stl"), "a/b/c/part.stl"
        )

    def test_posix_members_are_left_alone(self):
        self.assertEqual(normalize_archive_member("a/b/part.stl"), "a/b/part.stl")

    def test_drive_prefixes_are_stripped(self):
        self.assertEqual(normalize_archive_member("C:\\work\\part.stl"), "work/part.stl")

    def test_leading_separators_are_stripped(self):
        self.assertEqual(normalize_archive_member("/abs/part.stl"), "abs/part.stl")
        self.assertEqual(normalize_archive_member("\\abs\\part.stl"), "abs/part.stl")


class SafeExtractTests(unittest.TestCase):
    def test_a_powershell_archive_extracts_as_a_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            archive = root / "transfer.zip"
            _write_archive(
                archive,
                {
                    "goal0018-castable-master-control\\shank.stl": b"solid shank\n",
                    "goal0018-castable-master-control\\prong_01.stl": b"solid prong\n",
                    "goal0018-castable-master-large\\shank.stl": b"solid large\n",
                },
            )
            destination = root / "received"
            _safe_extract(archive, destination)

            control = destination / "goal0018-castable-master-control"
            self.assertTrue(control.is_dir())
            self.assertEqual(
                sorted(p.name for p in control.iterdir()),
                ["prong_01.stl", "shank.stl"],
            )
            self.assertEqual(
                (control / "shank.stl").read_bytes(), b"solid shank\n"
            )
            # The defect this fixes: no flat file with a backslash in its name.
            self.assertEqual(
                [p.name for p in destination.iterdir() if "\\" in p.name], []
            )

    def test_a_wellformed_archive_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            archive = root / "transfer.zip"
            _write_archive(archive, {"bundle/part.stl": b"solid\n", "top.json": b"{}"})
            destination = root / "received"
            _safe_extract(archive, destination)
            self.assertEqual(
                (destination / "bundle" / "part.stl").read_bytes(), b"solid\n"
            )
            self.assertEqual((destination / "top.json").read_bytes(), b"{}")

    def test_traversal_is_still_refused_after_normalisation(self):
        """The containment check must survive the separator rewrite."""
        for hostile in ("..\\escape.stl", "../escape.stl", "a\\..\\..\\escape.stl"):
            with self.subTest(member=hostile):
                with tempfile.TemporaryDirectory() as tmp:
                    root = pathlib.Path(tmp)
                    archive = root / "transfer.zip"
                    _write_archive(archive, {hostile: b"nope\n"})
                    with self.assertRaises(RuntimeError):
                        _safe_extract(archive, root / "received")
                    self.assertFalse((root / "escape.stl").exists())

    def test_directory_entries_do_not_become_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            archive = root / "transfer.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("bundle\\", b"")
                handle.writestr("bundle\\part.stl", b"solid\n")
            destination = root / "received"
            _safe_extract(archive, destination)
            self.assertTrue((destination / "bundle").is_dir())
            self.assertTrue((destination / "bundle" / "part.stl").is_file())


class SharedByEveryRunnerTests(unittest.TestCase):
    """The Goal 0014 and Goal 0018 runners must use this helper, not their own."""

    def test_the_later_runners_import_the_fixed_helper(self):
        from scripts import goal0014_azure_master_run, goal0018_azure_castable_run

        self.assertIs(goal0014_azure_master_run._safe_extract, _safe_extract)
        self.assertIs(goal0018_azure_castable_run._safe_extract, _safe_extract)


if __name__ == "__main__":
    unittest.main()
