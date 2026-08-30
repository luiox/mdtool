"""``python -m markdown_util`` 入口：委托给 :mod:`cli`。

项目模块是平铺导入（``from server.meta_db import MetaDB``）而非包内
相对导入，这里先把本目录挂进 sys.path 再引 cli，与 tests 的做法一致。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
