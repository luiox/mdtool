"""cli 纯逻辑测试：媒体命名规划、URL 拼装、路径展开。

命名规则与 server/media_server.py 的 _make_name 同语义（图片 image- 前缀、
时间戳 + 3 位序号 + 扩展名兜底、冲突跳进），这里是脚本化导入的复刻，
改一处命名规则必须同步另一处。
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cli import collect_files, media_url, timestamped_names

NOW = datetime(2026, 8, 30, 9, 30, 0)


class TestTimestampedNames:
    def test_image_prefix_and_seq(self):
        out = timestamped_names(["a.png", "b.jpg"], set(), asset=False, now=NOW)
        assert out == [
            ("a.png", "image-20260830093000001.png"),
            ("b.jpg", "image-20260830093000002.jpg"),
        ]

    def test_asset_no_prefix(self):
        out = timestamped_names(["手册.pdf"], set(), asset=True, now=NOW)
        assert out == [("手册.pdf", "20260830093000001.pdf")]

    def test_ext_fallback_png(self):
        out = timestamped_names(["noext"], set(), asset=False, now=NOW)
        assert out[0][1].endswith(".png")

    def test_collision_skips_seq(self):
        existing = {"image-20260830093000001.png", "image-20260830093000002.png"}
        out = timestamped_names(["a.png"], existing, asset=False, now=NOW)
        assert out[0][1] == "image-20260830093000003.png"

    def test_batch_no_self_collision(self):
        out = timestamped_names([f"{i}.png" for i in range(5)], set(), asset=False, now=NOW)
        names = [n for _, n in out]
        assert len(set(names)) == 5

    def test_strip_fragment_and_query(self):
        out = timestamped_names(["pic.png#x?y=1"], set(), asset=False, now=NOW)
        assert out[0][1].endswith(".png")


class TestMediaUrl:
    def test_image_url_default_host(self):
        assert media_url("image-1.png", asset=False, host="127.0.0.1", port=8765) \
            == "http://127.0.0.1:8765/images/image-1.png"

    def test_asset_url(self):
        assert media_url("20260516162257001.zip", asset=True, host="192.168.1.5", port=9000) \
            == "http://192.168.1.5:9000/assets/20260516162257001.zip"


class TestCollectFiles:
    def test_dir_recursive_and_hidden_skip(self, tmp_path):
        (tmp_path / "sub").mkdir()
        (tmp_path / "a.png").write_bytes(b"a")
        (tmp_path / "sub" / "b.png").write_bytes(b"b")
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "c.png").write_bytes(b"c")
        out = collect_files([str(tmp_path)])
        assert [p.name for p in out] == ["a.png", "b.png"]

    def test_missing_raises(self):
        try:
            collect_files(["no/such/path"])
            raise AssertionError("should raise")
        except FileNotFoundError:
            pass
