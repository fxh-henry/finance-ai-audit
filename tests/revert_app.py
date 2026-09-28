# -*- coding: utf-8 -*-
path = r"D:\agent\财务报销项目\app.py"
with open(path, "r", encoding="utf-8") as f:
    c = f.read()

old = (
    "import sys  # 用于设置Python模块搜索路径\n"
    "import os  # 设置环境变量（HuggingFace镜像）\n"
    "from pathlib import Path  # 用于获取项目根目录\n"
    "\n"
    "# 把项目根目录加入Python搜索路径，确保从任何目录运行都能找到utils、audit等包\n"
    "sys.path.insert(0, str(Path(__file__).parent))\n"
    "\n"
    "# 必须在 import sentence-transformers/transformers 之前设置\n"
    "# 让云端从国内镜像下载 embedding 模型，避免直连 huggingface.co 超时\n"
    'os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")\n'
    "\n"
    "import streamlit as st  # 导入 Streamlit，后面所有界面元素都通过 st 调用"
)

new = (
    "import sys  # 用于设置Python模块搜索路径\n"
    "from pathlib import Path  # 用于获取项目根目录\n"
    "\n"
    "# 把项目根目录加入Python搜索路径，确保从任何目录运行都能找到utils、audit等包\n"
    "sys.path.insert(0, str(Path(__file__).parent))\n"
    "\n"
    "import streamlit as st  # 导入 Streamlit，后面所有界面元素都通过 st 调用"
)

if old in c:
    c = c.replace(old, new)
    with open(path, "w", encoding="utf-8") as f:
        f.write(c)
    print("OK")
else:
    print("NOT FOUND")
