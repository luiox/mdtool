"""``python -m mdtool.cli``：与 ``python -m mdtool`` 等价的命令行入口。"""

import sys

from mdtool.cli.main import main

if __name__ == "__main__":
    sys.exit(main())
