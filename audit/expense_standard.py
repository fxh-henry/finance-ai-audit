# -*- coding: utf-8 -*-
# ============================================================================
# 费用标准校验工具
# ============================================================================
# 作用：根据费用类型、城市、职级、金额，判断是否符合公司报销标准
# 特点：纯代码计算，100%确定，不依赖LLM
# 数据来源：config/settings.py 中的 ACCOMMODATION_STANDARD 等配置
# ============================================================================

import sys
from pathlib import Path

# 把项目根目录加入Python搜索路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import ACCOMMODATION_STANDARD, FIRST_TIER_CITIES, SECOND_TIER_CITIES


# ============================================================================
# 工具函数：判断城市等级
# ============================================================================
def get_city_tier(city_name):
    """
    根据城市名称判断属于几线城市

    参数：
        city_name: 城市名称，如"北京"、"苏州"、"南京"

    返回：
        "一线城市" / "二线城市" / "其他城市"
    """
    if not city_name:
        return "其他城市"

    # 去掉"市"字，方便匹配（"北京市" → "北京"）
    city = city_name.replace("市", "")

    # 一线城市：北上广深
    if city in FIRST_TIER_CITIES:
        return "一线城市"

    # 二线城市：省会城市和计划单列市（从config配置读取）
    if city in SECOND_TIER_CITIES:
        return "二线城市"

    return "其他城市"


# ============================================================================
# 校验1：住宿费标准
# ============================================================================
def check_accommodation(city, level, amount):
    """
    校验住宿费是否超标

    参数：
        city: 城市名称，如"北京"
        level: 职级，如"普通员工"、"部门经理"、"总经理及以上"
        amount: 住宿费金额（元/天），浮点数

    返回：
        {
            "passed": True/False,
            "standard": 标准金额,
            "actual": 实际金额,
            "exceed": 超标金额（未超标则为0）,
            "city_tier": 城市等级,
            "message": 描述信息
        }
    """
    # 判断城市等级
    city_tier = get_city_tier(city)

    # 从配置中获取该城市等级的标准
    tier_standards = ACCOMMODATION_STANDARD.get(city_tier, {})

    # 总经理及以上是实报实销
    if level == "总经理及以上":
        return {
            "passed": True,
            "standard": "实报实销",
            "actual": amount,
            "exceed": 0,
            "city_tier": city_tier,
            "message": f"总经理及以上实报实销，{city}（{city_tier}），金额{amount}元"
        }

    # 获取对应职级的标准
    standard = tier_standards.get(level, 0)

    if standard == 0:
        return {
            "passed": False,
            "standard": 0,
            "actual": amount,
            "exceed": amount,
            "city_tier": city_tier,
            "message": f"未找到职级[{level}]在{city_tier}的住宿费标准"
        }

    # 判断是否超标
    if amount <= standard:
        return {
            "passed": True,
            "standard": standard,
            "actual": amount,
            "exceed": 0,
            "city_tier": city_tier,
            "message": f"住宿费{amount}元，{city}（{city_tier}）{level}标准{standard}元/天，未超标"
        }
    else:
        exceed = round(amount - standard, 2)
        return {
            "passed": False,
            "standard": standard,
            "actual": amount,
            "exceed": exceed,
            "city_tier": city_tier,
            "message": f"住宿费超标：{city}（{city_tier}）{level}标准{standard}元/天，实际{amount}元，超标{exceed}元"
        }


# ============================================================================
# 校验2：市内交通费标准（80元/天包干）
# ============================================================================
def check_local_transport(days, amount):
    """
    校验市内交通费是否超标（80元/天包干）

    参数：
        days: 出差天数
        amount: 市内交通费总金额

    返回：
        标准校验结果字典
    """
    standard = 80 * days
    if amount <= standard:
        return {
            "passed": True,
            "standard": standard,
            "actual": amount,
            "exceed": 0,
            "message": f"市内交通费{amount}元，{days}天标准{standard}元，未超标"
        }
    else:
        exceed = round(amount - standard, 2)
        return {
            "passed": False,
            "standard": standard,
            "actual": amount,
            "exceed": exceed,
            "message": f"市内交通费超标：{days}天标准{standard}元，实际{amount}元，超标{exceed}元"
        }


# ============================================================================
# 校验3：伙食补助费标准（100元/天，特殊地区120元/天）
# ============================================================================
def check_meal_subsidy(days, amount, is_special_area=False):
    """
    校验伙食补助费是否超标

    参数：
        days: 出差天数
        amount: 伙食补助总金额
        is_special_area: 是否特殊地区（西藏、青海、新疆）

    返回：
        标准校验结果字典
    """
    daily_standard = 120 if is_special_area else 100
    standard = daily_standard * days

    if amount <= standard:
        return {
            "passed": True,
            "standard": standard,
            "actual": amount,
            "exceed": 0,
            "message": f"伙食补助{amount}元，{days}天标准{standard}元，未超标"
        }
    else:
        exceed = round(amount - standard, 2)
        return {
            "passed": False,
            "standard": standard,
            "actual": amount,
            "exceed": exceed,
            "message": f"伙食补助超标：{days}天标准{standard}元，实际{amount}元，超标{exceed}元"
        }


# ============================================================================
# 统一入口：根据费用类型自动选择校验函数
# ============================================================================
def check_expense_standard(expense_type, **kwargs):
    """
    统一费用标准校验入口

    参数：
        expense_type: 费用类型，如"住宿费"、"市内交通费"、"伙食补助"
        **kwargs: 各类型需要的参数
            - 住宿费: city, level, amount
            - 市内交通费: days, amount
            - 伙食补助: days, amount, is_special_area

    返回：
        校验结果字典
    """
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
        return {
            "passed": True,
            "standard": "无明确标准",
            "actual": kwargs.get("amount", 0),
            "exceed": 0,
            "message": f"费用类型[{expense_type}]暂无明确标准，跳过校验"
        }


# ============================================================================
# 测试代码
# ============================================================================
if __name__ == "__main__":
    print("=" * 60)
    print("费用标准校验测试")
    print("=" * 60)

    # 测试1：住宿费-未超标
    print("\n测试1：住宿费-北京-普通员工-350元")
    r1 = check_expense_standard("住宿费", city="北京", level="普通员工", amount=350)
    print(f"  结果: {'通过' if r1['passed'] else '不通过'}")
    print(f"  {r1['message']}")

    # 测试2：住宿费-超标
    print("\n测试2：住宿费-上海-普通员工-580元")
    r2 = check_expense_standard("住宿费", city="上海", level="普通员工", amount=580)
    print(f"  结果: {'通过' if r2['passed'] else '不通过'}")
    print(f"  {r2['message']}")

    # 测试3：住宿费-二线城市
    print("\n测试3：住宿费-南京-部门经理-450元")
    r3 = check_expense_standard("住宿费", city="南京", level="部门经理", amount=450)
    print(f"  结果: {'通过' if r3['passed'] else '不通过'}")
    print(f"  {r3['message']}")

    # 测试4：市内交通费
    print("\n测试4：市内交通费-3天-300元")
    r4 = check_expense_standard("市内交通费", days=3, amount=300)
    print(f"  结果: {'通过' if r4['passed'] else '不通过'}")
    print(f"  {r4['message']}")

    # 测试5：伙食补助
    print("\n测试5：伙食补助-5天-600元")
    r5 = check_expense_standard("伙食补助", days=5, amount=600)
    print(f"  结果: {'通过' if r5['passed'] else '不通过'}")
    print(f"  {r5['message']}")

    # 测试6：城市等级判断
    print("\n测试6：城市等级判断")
    for city in ["北京", "苏州", "宿迁", "上海"]:
        print(f"  {city} → {get_city_tier(city)}")
