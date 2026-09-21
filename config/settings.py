# -*- coding: utf-8 -*-
"""
全局配置文件

分级原则：
  - 真敏感（密钥 / Secret）：走环境变量 > Streamlit Secrets > 代码兜底
    → 防止推到 GitHub 后泄露
  - 不敏感（URL / 模型名 / 端点 / AccessKeyId）：直接硬编码
    → 不泄露任何东西，写法简单
"""
import os


def _get_secret(key, default=None):
    """
    按优先级读取一个密钥：环境变量 > Streamlit Secrets > 默认值

    为什么这样写：
      - 部署到 Streamlit Community Cloud 时，密钥在网页后台填，不进 Git
      - 本地开发时，没配 Secrets 也能直接跑（用 default 兜底）
      - 调用方无感知，仍然 from config.settings import LLM_API_KEY
    """
    # 1) 环境变量（最高优先级，命令行/容器里设的）
    env_val = os.environ.get(key)
    if env_val:
        return env_val

    # 2) Streamlit Secrets（本地 .streamlit/secrets.toml 或 Cloud 后台）
    try:
        import streamlit as st  # 延迟导入，避免非 Streamlit 场景（如纯脚本测试）报错
        if key in st.secrets:  # 密钥表里存在这个键
            return st.secrets[key]
    except Exception:
        # 不在 Streamlit 运行环境里（比如命令行直接跑 pipeline.py），跳过这一层
        pass

    # 3) 兜底默认值（本地开发用；部署时请用 Secrets/环境变量覆盖）
    return default


# ===== 大模型配置（百炼兼容模式） =====
# 不敏感：直接写死（接入地址、模型名，泄露也调不通）
LLM_BASE_URL = "https://ws-qr2d8qrkll6yl9j7.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
LLM_MODEL = "qwen3.8-max"

# 真敏感：走环境变量 / Streamlit Secrets，推 Git 不泄露
# 注意：default 留空，不硬编码任何真实密钥（GitHub Push Protection 会拦截）
LLM_API_KEY = _get_secret("LLM_API_KEY", "")

# ===== 阿里云 OCR 配置 =====
# AccessKeyId 和 AccessKeySecret 都走环境变量 / Streamlit Secrets
# 本地开发在 .streamlit/secrets.toml 里填，部署时在 Streamlit Cloud 后台填
OCR_ACCESS_KEY_ID = _get_secret("OCR_ACCESS_KEY_ID", "")
OCR_ENDPOINT = "ocr-api.cn-hangzhou.aliyuncs.com"
OCR_ACCESS_KEY_SECRET = _get_secret("OCR_ACCESS_KEY_SECRET", "")

# ===== 数据库配置 =====
DB_PATH = "database/finance_audit.db"


COMPANY_NAME ="苏州城市学院"
COMPANY_TAX_ID ="12320500MB1F99368P"
# ===== 公司信息（用于发票抬头/税号校验） =====
# COMPANY_NAME = "泗阳县成达制盖有限公司"
# COMPANY_TAX_ID = "91321323796144439H"

# ===== 临时文件目录 =====
TEMP_DIR = "temp"

# ===== 费用标准（后续可以移到数据库） =====
ACCOMMODATION_STANDARD = {
    "一线城市": {"普通员工": 400, "部门经理": 500, "总监": 600},
    "二线城市": {"普通员工": 300, "部门经理": 400, "总监": 500},
    "其他城市": {"普通员工": 200, "部门经理": 300, "总监": 400},
}

# 一线城市列表
FIRST_TIER_CITIES = ["北京", "上海", "广州", "深圳"]

# 二线城市列表（省会城市和计划单列市）
SECOND_TIER_CITIES = [
    "南京", "杭州", "成都", "武汉", "西安", "重庆", "天津",
    "苏州", "青岛", "大连", "宁波", "厦门", "长沙", "郑州",
    "济南", "合肥", "福州", "南昌", "昆明", "南宁", "贵阳",
    "太原", "石家庄", "沈阳", "长春", "哈尔滨", "兰州", "乌鲁木齐",
    "呼和浩特", "银川", "西宁", "海口", "拉萨"
]

# ===== 风控检测配置 =====
# 审批阈值列表（用于拆分报销检测和金额临界检测）
APPROVAL_THRESHOLDS = [500, 1000, 5000, 10000]
# 连号发票检测：开票日期前后多少天内的发票纳入比对
CONSECUTIVE_DAYS_RANGE = 7
# 金额临界检测：金额在阈值的多少比例范围内算临界（0.95表示95%-100%）
AMOUNT_THRESHOLD_RATIO = 0.95
# 高频报销检测：统计近多少天
HIGH_FREQUENCY_DAYS = 30
# 高频报销检测：超过部门平均值多少倍算异常
HIGH_FREQUENCY_MULTIPLIER = 2.0
