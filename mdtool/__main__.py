"""``python -m mdtool``：命令行入口，委托 :mod:`mdtool.cli.main`。"""

import sys

from mdtool.cli.main import main

if __name__ == "__main__":
    sys.exit(main())
