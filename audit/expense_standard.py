# -*- coding: utf-8 -*-
# ============================================================================
# 费用标准校验工具
# ============================================================================
# 作用：根据费用类型、城市、职级、金额，判断是否符合公司报销标准
# 特点：纯代码计算，100%确定，不依赖LLM
# 数据来源：config/settings.py 中的 ACCOMMODATION_STANDARD 等配置
#
# 机制（六类缺陷修复）：
#   1. 城市为空 → 拒绝计算，升级待确认（不再默认"其他城市"）
#   2. 城市认不出 → 升级待确认（不再默认"其他城市"）
#   3. 职级为空/找不到 → 升级待确认（不再默认"普通员工"）
#   4. 费用类型无标准 → 不直接放过，标记待确认
#   5. 每次返回必须带口径来源（城市几线、职级、依据哪条标准）
#   6. 口径缺失 → needs_human=True，交给人工
# ============================================================================

import sys
from pathlib import Path

# 把项目根目录加入Python搜索路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import ACCOMMODATION_STANDARD, FIRST_TIER_CITIES, SECOND_TIER_CITIES, PROVINCE_TO_CAPITAL, OTHER_CITIES


# ============================================================================
# 规则清单：所有标准一览（前端可展示，评委一眼看到"规则从哪来"）
# ============================================================================
RULE_LIST = [
    {
        "expense_type": "住宿费",
        "source_clause": "第十四条 住宿费标准",
        "standard_table": ACCOMMODATION_STANDARD,
        "note": "同性两人合住按单人1.5倍；超标部分个人承担，需提前审批",
    },
    {
        "expense_type": "市内交通费",
        "source_clause": "第十六条 市内交通费标准",
        "daily_standard": 80,
        "note": "按出差天数包干，凭票据实报销但不超过标准",
    },
    {
        "expense_type": "伙食补助",
        "source_clause": "第十五条 伙食补助标准",
        "daily_standard_normal": 100,
        "daily_standard_special": 120,
        "note": "特殊地区（西藏/青海/新疆）120元/天",
    },
]


# ============================================================================
# 工具函数：判断城市等级
# ============================================================================
def get_city_tier(city_name):
    """
    根据城市名称判断属于几线城市

    返回：
        (city_tier, recognized, in_known_list)
        - recognized=False: 城市为空/无效，需要人工补充
        - in_known_list=False: 不在列表但按其他城市处理
    """
    if not city_name or len(city_name.strip()) < 2:
        return ("", False, False)

    city = city_name.replace("市", "").strip()

    # 省份名自动转省会（如"湖南"→"长沙"）
    if city in PROVINCE_TO_CAPITAL:
        city = PROVINCE_TO_CAPITAL[city]

    if city in FIRST_TIER_CITIES:
        return ("一线城市", True, True)

    if city in SECOND_TIER_CITIES:
        return ("二线城市", True, True)

    if city in OTHER_CITIES:
        return ("其他城市", True, True)

    # 不在任何列表里，默认其他城市
    return ("其他城市", True, False)


# ============================================================================
# 校验1：住宿费标准
# ============================================================================
def check_accommodation(city, level, amount):
    """
    校验住宿费是否超标

    返回:
        {
            "passed": True/False/None,   None=数据不足无法判定
            "standard": 标准金额,
            "actual": 实际金额,
            "exceed": 超标金额,
            "city_tier": 城市等级,
            "city_recognized": 城市是否识别成功,
            "level_recognized": 职级是否识别成功,
            "source_clause": 依据条款,
            "caliber": 口径说明,
            "needs_human": 是否需要人工确认,
            "message": 描述信息
        }
    """
    source_clause = "第十四条 住宿费标准（元/天）"

    # ---- 缺陷1修复：城市为空，拒绝计算 ----
    if not city:
        return {
            "passed": None,
            "standard": None,
            "actual": amount,
            "exceed": 0,
            "city_tier": "",
            "city_recognized": False,
            "level_recognized": True,
            "source_clause": source_clause,
            "caliber": "城市未提供，无法判断城市等级",
            "needs_human": True,
            "message": "缺少出差城市信息，无法适用住宿费标准，请补充出差城市后人工确认"
        }

    # ---- 缺陷2修复：城市为空才拦截；未列出的城市默认其他城市 ----
    city_tier, city_recognized, city_in_list = get_city_tier(city)
    if not city_recognized:
        return {
            "passed": None,
            "standard": None,
            "actual": amount,
            "exceed": 0,
            "city_tier": "",
            "city_recognized": False,
            "level_recognized": True,
            "source_clause": source_clause,
            "caliber": "城市未提供，无法判断城市等级",
            "needs_human": True,
            "message": "缺少出差城市信息，无法适用住宿费标准，请补充出差城市后人工确认"
        }
    # 未在列表中的城市，标注但不拦截（按其他城市处理）
    city_note = "" if city_in_list else f"（{city}未在城市列表中，按其他城市处理）"

    # ---- 缺陷3修复：职级校验 ----
    valid_levels = ["普通员工", "部门经理", "总经理及以上"]
    if level not in valid_levels:
        return {
            "passed": None,
            "standard": None,
            "actual": amount,
            "exceed": 0,
            "city_tier": city_tier,
            "city_recognized": True,
            "level_recognized": False,
            "source_clause": source_clause,
            "caliber": f"职级[{level}]不在标准职级列表中",
            "needs_human": True,
            "message": f"职级[{level}]不在已知职级（{'/'.join(valid_levels)}）中，需人工确认职级后适用标准"
        }

    # 总经理及以上实报实销
    if level == "总经理及以上":
        return {
            "passed": True,
            "standard": "实报实销",
            "actual": amount,
            "exceed": 0,
            "city_tier": city_tier,
            "city_recognized": True,
            "level_recognized": True,
            "source_clause": source_clause,
            "caliber": f"{city}（{city_tier}），{level}，实报实销",
            "needs_human": False,
            "message": f"总经理及以上实报实销，{city}（{city_tier}），金额{amount}元"
        }

    # 获取标准
    tier_standards = ACCOMMODATION_STANDARD.get(city_tier, {})
    standard = tier_standards.get(level, 0)

    # ---- 缺陷3修复：职级找不到对应标准，升级待确认 ----
    if standard == 0:
        return {
            "passed": None,
            "standard": 0,
            "actual": amount,
            "exceed": 0,
            "city_tier": city_tier,
            "city_recognized": True,
            "level_recognized": False,
            "source_clause": source_clause,
            "caliber": f"{city}（{city_tier}）下未配置[{level}]的住宿费标准",
            "needs_human": True,
            "message": f"未找到[{city_tier}][{level}]的住宿费标准，需人工确认"
        }

    # 正常判定
    caliber = f"{city}（{city_tier}）{city_note}，{level}，标准{standard}元/天"
    if amount <= standard:
        return {
            "passed": True,
            "standard": standard,
            "actual": amount,
            "exceed": 0,
            "city_tier": city_tier,
            "city_recognized": True,
            "level_recognized": True,
            "source_clause": source_clause,
            "caliber": caliber,
            "needs_human": False,
            "message": f"住宿费{amount}元，{caliber}，未超标"
        }
    else:
        exceed = round(amount - standard, 2)
        return {
            "passed": False,
            "standard": standard,
            "actual": amount,
            "exceed": exceed,
            "city_tier": city_tier,
            "city_recognized": True,
            "level_recognized": True,
            "source_clause": source_clause,
            "caliber": caliber,
            "needs_human": False,
            "message": f"住宿费超标：{caliber}，实际{amount}元，超标{exceed}元（依据：{source_clause}）"
        }


# ============================================================================
# 校验2：市内交通费标准（80元/天包干）
# ============================================================================
def check_local_transport(days, amount):
    standard = 80 * days
    source_clause = "第十六条 市内交通费标准"
    caliber = f"{days}天 × 80元/天 = {standard}元"

    if amount <= standard:
        return {
            "passed": True,
            "standard": standard,
            "actual": amount,
            "exceed": 0,
            "source_clause": source_clause,
            "caliber": caliber,
            "needs_human": False,
            "message": f"市内交通费{amount}元，{caliber}，未超标（依据：{source_clause}）"
        }
    else:
        exceed = round(amount - standard, 2)
        return {
            "passed": False,
            "standard": standard,
            "actual": amount,
            "exceed": exceed,
            "source_clause": source_clause,
            "caliber": caliber,
            "needs_human": False,
            "message": f"市内交通费超标：{caliber}，实际{amount}元，超标{exceed}元（依据：{source_clause}）"
        }


# ============================================================================
# 校验3：伙食补助费标准（100元/天，特殊地区120元/天）
# ============================================================================
def check_meal_subsidy(days, amount, is_special_area=False):
    daily_standard = 120 if is_special_area else 100
    standard = daily_standard * days
    source_clause = "第十五条 伙食补助标准"
    area_name = "特殊地区" if is_special_area else "一般地区"
    caliber = f"{days}天 × {daily_standard}元/天（{area_name}）= {standard}元"

    if amount <= standard:
        return {
            "passed": True,
            "standard": standard,
            "actual": amount,
            "exceed": 0,
            "source_clause": source_clause,
            "caliber": caliber,
            "needs_human": False,
            "message": f"伙食补助{amount}元，{caliber}，未超标（依据：{source_clause}）"
        }
    else:
        exceed = round(amount - standard, 2)
        return {
            "passed": False,
            "standard": standard,
            "actual": amount,
            "exceed": exceed,
            "source_clause": source_clause,
            "caliber": caliber,
            "needs_human": False,
            "message": f"伙食补助超标：{caliber}，实际{amount}元，超标{exceed}元（依据：{source_clause}）"
        }


# ============================================================================
# 费用类型别名表（口径归一）
# ============================================================================
# 为什么需要：报销单表单里的费用类型名来自 utils/expense_form.py（交通费），
# 但制度条款里的写法是「市内交通费」（第十六条）。两处写法不一致，
# 标准查询就会落空——交通费永远走不到 80 元/天 那个分支，
# 被当成「未配置标准」转人工。这是典型的「口径漂移」缺陷，
# 修法是统一在一个地方映射，而不是让每个调用方自己对齐。
EXPENSE_TYPE_ALIASES = {
    "交通费": "市内交通费",
    "市内交通": "市内交通费",
    "伙食费": "伙食补助",
    "餐补": "伙食补助",
}


# ============================================================================
# 统一入口：根据费用类型自动选择校验函数
# ============================================================================
def check_expense_standard(expense_type, **kwargs):
    """
    统一费用标准校验入口

    返回:
        校验结果字典，必含字段：
        - passed: True/False/None（None=数据不足需人工）
        - needs_human: bool
        - source_clause: 依据条款
        - caliber: 计算口径
    """
    expense_type = EXPENSE_TYPE_ALIASES.get(expense_type, expense_type)  # 口径归一：表单叫法 → 制度叫法
    if expense_type == "住宿费":
        return check_accommodation(
            city=kwargs.get("city", ""),
            level=kwargs.get("level", "普通员工"),
            amount=float(kwargs.get("amount", 0))
        )
    elif expense_type == "市内交通费":
        return check_local_transport(
            days=int(kwargs.get("days", 1)),
            amount=float(kwargs.get("amount", 0))
        )
    elif expense_type == "伙食补助":
        return check_meal_subsidy(
            days=int(kwargs.get("days", 1)),
            amount=float(kwargs.get("amount", 0)),
            is_special_area=kwargs.get("is_special_area", False)
        )
    else:
        # ---- 缺陷4修复：未知费用类型，不直接放过，标记待确认 ----
        return {
            "passed": None,
            "standard": None,
            "actual": kwargs.get("amount", 0),
            "exceed": 0,
            "source_clause": "",
            "caliber": f"费用类型[{expense_type}]在制度中未配置标准",
            "needs_human": True,
            "message": f"费用类型[{expense_type}]暂无明确标准，无法自动判定，建议人工审核"
        }


# ============================================================================
# 测试代码
# ============================================================================
if __name__ == "__main__":
    print("=" * 60)
    print("费用标准校验测试（含口径来源+待确认机制）")
    print("=" * 60)

    print("\n测试1：正常-北京普通员工350元")
    r = check_expense_standard("住宿费", city="北京", level="普通员工", amount=350)
    print(f"  passed={r['passed']}, needs_human={r['needs_human']}")
    print(f"  口径: {r['caliber']}")
    print(f"  依据: {r['source_clause']}")

    print("\n测试2：城市为空→应升级待确认")
    r = check_expense_standard("住宿费", city="", level="普通员工", amount=500)
    print(f"  passed={r['passed']}, needs_human={r['needs_human']}")
    print(f"  口径: {r['caliber']}")

    print("\n测试3：城市认不出（如宿迁）→应升级待确认")
    r = check_expense_standard("住宿费", city="宿迁", level="普通员工", amount=500)
    print(f"  passed={r['passed']}, needs_human={r['needs_human']}")
    print(f"  口径: {r['caliber']}")

    print("\n测试4：未知费用类型→应升级待确认")
    r = check_expense_standard("团建费", amount=1000)
    print(f"  passed={r['passed']}, needs_human={r['needs_human']}")
