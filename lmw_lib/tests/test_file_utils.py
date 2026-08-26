"""file_utils 测试"""

import pytest
from pathlib import Path
from lmw_lib.file_utils import (
    read_text, write_text, file_size, find_files, safe_join,
)


class TestReadWrite:
    def test_roundtrip(self, tmp_path):
        p = tmp_path / "test.txt"
        write_text(p, "hello world")
        assert read_text(p) == "hello world"

    def test_creates_parent_dirs(self, tmp_path):
        p = tmp_path / "a" / "b" / "c.txt"
        write_text(p, "deep")
        assert read_text(p) == "deep"


class TestFileSize:
    def test_size(self, tmp_path):
        p = tmp_path / "size.txt"
        write_text(p, "12345")
        assert file_size(p) == 5


class TestFindFiles:
    def test_find(self, tmp_path):
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.py").write_text("b")
        files = find_files(tmp_path, "*.txt")
        assert len(files) == 1
        assert files[0].name == "a.txt"

    def test_not_a_dir(self):
        with pytest.raises(NotADirectoryError):
            find_files("/nonexistent_dir_xyz")


class TestSafeJoin:
    def test_normal(self, tmp_path):
        result = safe_join(tmp_path, "sub", "file.txt")
        assert result.name == "file.txt"

    def test_path_traversal_raises(self, tmp_path):
        with pytest.raises(ValueError):
            safe_join(tmp_path, "..", "etc", "passwd")