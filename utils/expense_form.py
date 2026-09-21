# -*- coding: utf-8 -*-
"""
报销单配置与工具（utils/expense_form.py）
================================================================
设计形态：统一主表 + 分类扩展
    统一主表：所有费用类型共有的字段（行程号 / 费用类型 / 发生日期 / 事由 / 金额 / 参与人）
    分类扩展：不同费用类型特有的字段（住宿费要填晚数、间数；餐饮费要填就餐人数……）
    分类字段统一存进 expense_form.ext_fields（JSON 列），
    以后新增费用类型只改这份配置，不用动数据库表结构。

为什么单独抽一个文件：
    报销单填写页（写入）、我的报销单页（列表）、报销单详情页（展示）、
    Agent 终审（拼提示词）四处都要用同一份定义，放一起才不会各写各的。

关系约定：
    1 张发票 : 1 张报销单；发票号码由系统从票夹带出，不让用户手填。
    同一趟出差的多张单子填同一个「行程号」，即可按行程分组查看与汇总。
================================================================
"""

NL = chr(10)  # 换行符：直接用 chr(10) 而不是转义写法，源码更直观

from rag.utils.logger import info, success, warn, error  # 日志工具


# ---------------------------------------------------------------------------
# 一、费用类型 → 分类扩展字段定义
#     fields 里每项的 type 取值：
#       text   → 单行文本
#       int    → 整数
#       float  → 小数
#       select → 下拉选择（需要 options）
# ---------------------------------------------------------------------------
EXPENSE_CATEGORIES = {
    "住宿费": {
        "icon": "🏨",
        "desc": "酒店、民宿等住宿支出",
        "fields": [
            {"key": "city", "label": "入住城市", "type": "text", "default": "", "help": "用于套用出差住宿费职级标准"},
            {"key": "nights", "label": "住宿晚数", "type": "int", "default": 1, "help": "一共住了几晚"},
            {"key": "rooms", "label": "房间数", "type": "int", "default": 1, "help": "一共开了几间房"},
            {"key": "guests", "label": "入住人数", "type": "int", "default": 1, "help": "一共几人入住"},
            {"key": "guest_names", "label": "入住人姓名", "type": "text", "default": "", "help": "多人用顿号分隔"},
        ],
    },
    "餐饮费": {
        "icon": "🍽️",
        "desc": "业务招待、工作餐等餐饮支出",
        "fields": [
            {"key": "diners", "label": "就餐人数", "type": "int", "default": 1, "help": "一共几人用餐"},
            {"key": "meal_count", "label": "就餐次数", "type": "int", "default": 1, "help": "一共几顿"},
            {"key": "guest_names", "label": "招待对象", "type": "text", "default": "", "help": "外部人员姓名或单位"},
        ],
    },
    "交通费": {
        "icon": "🚄",
        "desc": "飞机、高铁、打车等交通支出",
        "fields": [
            {"key": "from_city", "label": "出发地", "type": "text", "default": ""},
            {"key": "to_city", "label": "目的地", "type": "text", "default": ""},
            {"key": "vehicle", "label": "交通方式", "type": "select", "default": "高铁/动车",
             "options": ["飞机", "高铁/动车", "火车", "长途汽车", "出租车/网约车", "自驾", "其他"]},
            {"key": "passengers", "label": "乘车人数", "type": "int", "default": 1},
        ],
    },
    "材料费": {
        "icon": "📦",
        "desc": "采购原材料、耗材等支出",
        "fields": [
            {"key": "purpose", "label": "材料用途", "type": "text", "default": ""},
            {"key": "receiver", "label": "领用人", "type": "text", "default": ""},
            {"key": "use_dept", "label": "使用部门", "type": "text", "default": ""},
        ],
    },
    "办公用品": {
        "icon": "🖊️",
        "desc": "办公文具、设备耗材等支出",
        "fields": [
            {"key": "purpose", "label": "用途", "type": "text", "default": ""},
            {"key": "use_dept", "label": "使用部门", "type": "text", "default": ""},
        ],
    },
    "其他费用": {
        "icon": "📄",
        "desc": "不属于以上分类的其他支出",
        "fields": [
            {"key": "purpose", "label": "费用说明", "type": "text", "default": ""},
        ],
    },
}

# 费用类型名称列表（下拉框的选项顺序就是这里的顺序）
EXPENSE_TYPE_NAMES = list(EXPENSE_CATEGORIES.keys())

# 统一主表里的字段名（写入页面用它区分「主表字段」和「明细行」）
BASE_FIELD_KEYS = ["trip_no", "expense_type", "occur_date", "reason", "total_amount", "participants"]


# ---------------------------------------------------------------------------
# 二、读取分类扩展字段定义
# ---------------------------------------------------------------------------
def get_category_fields(expense_type):
    """取某个费用类型的分类扩展字段定义（没有该类型就返回空列表）"""
    category = EXPENSE_CATEGORIES.get(expense_type) or {}
    return list(category.get("fields") or [])


def default_ext_fields(expense_type):
    """取某个费用类型分类扩展字段的默认值字典"""
    values = {}
    for field in get_category_fields(expense_type):
        values[field["key"]] = field.get("default")
    return values


def normalize_ext_fields(expense_type, ext_fields):
    """
    把存库的 ext_fields（JSON 字典）整理成「标签 + 值」列表，供展示和提示词使用。
    只返回有值的字段，避免页面上出现一堆空行。
    """
    data = ext_fields or {}
    rows = []
    for field in get_category_fields(expense_type):
        value = data.get(field["key"])
        if value is None or value == "":
            continue
        rows.append({"label": field["label"], "value": value})
    return rows


def ext_fields_summary(expense_type, ext_fields, sep=" · "):
    """把分类扩展字段压成一行文字，用于列表页的灰色小字"""
    rows = normalize_ext_fields(expense_type, ext_fields)
    return sep.join(row["label"] + "：" + str(row["value"]) for row in rows)


# ---------------------------------------------------------------------------
# 三、根据发票内容猜一个费用类型（只是给下拉框一个默认选中项，用户可以改）
# ---------------------------------------------------------------------------
KEYWORD_RULES = [
    (("住宿", "房费", "酒店", "客房", "宾馆"), "住宿费"),
    (("餐饮", "餐费", "食品", "招待", "宴请", "饭店"), "餐饮费"),
    (("交通", "车费", "打车", "客运", "机票", "航空", "铁路", "高铁", "加油", "过路"), "交通费"),
    (("材料", "钢材", "原料", "耗材", "五金", "配件"), "材料费"),
    (("办公", "文具", "纸张", "打印", "用品"), "办公用品"),
]


def guess_expense_type(invoice_data):
    """按关键词从发票内容里猜费用类型，猜不出来就归到「其他费用」"""
    data = invoice_data or {}
    parts = [
        str(data.get("项目名称") or ""),
        str(data.get("发票种类") or ""),
        str(data.get("发票类型") or ""),
    ]
    for item in (data.get("明细列表") or []):
        if isinstance(item, dict):
            parts.append(str(item.get("项目名称") or ""))
    text = " ".join(parts)
    for keywords, expense_type in KEYWORD_RULES:
        for keyword in keywords:
            if keyword in text:
                return expense_type
    return "其他费用"


# ---------------------------------------------------------------------------
# 四、把报销单拼成给 Agent 看的文字（终审提示词要用）
# ---------------------------------------------------------------------------
def build_agent_text(expense_form):
    """把一张报销单整理成多行文字，注入 Agent 的系统提示词"""
    form = expense_form or {}
    lines = []
    lines.append("- 报销单号：" + str(form.get("form_no") or "未编号"))
    lines.append("- 行程号：" + str(form.get("trip_no") or "未填写"))
    lines.append("- 费用类型：" + str(form.get("expense_type") or "未填写"))
    lines.append("- 费用发生日期：" + str(form.get("occur_date") or "未填写"))
    lines.append("- 报销金额：" + str(form.get("total_amount")) + "元")
    lines.append("- 参与人：" + str(form.get("participants") or "未填写"))
    lines.append("- 报销事由：" + str(form.get("reason") or "未填写"))

    expense_type = form.get("expense_type") or ""
    for row in normalize_ext_fields(expense_type, form.get("ext_fields")):
        lines.append("- " + str(row["label"]) + "：" + str(row["value"]))

    # 住宿费额外折算「每晚每间单价」，Agent 拿它去比对职级标准最方便
    ext = form.get("ext_fields") or {}
    try:
        amount = float(form.get("total_amount") or 0)
        nights = int(ext.get("nights") or 0)
        rooms = int(ext.get("rooms") or 0)
        if nights > 0 and rooms > 0:
            per_night = round(amount / nights / rooms, 2)
            lines.append(
                "- 折算每晚每间单价：" + str(per_night)
                + "元（= 报销金额 ÷ 住宿晚数 ÷ 房间数，用于比对住宿费标准）"
            )
    except (TypeError, ValueError):
        pass

    items = form.get("detail_items") or []
    if items:
        lines.append("- 报销明细：")
        for item in items:
            if not isinstance(item, dict):
                continue
            lines.append(
                "    · 项目名称 " + str(item.get("项目名称") or "-")
                + " / 规格 " + str(item.get("规格型号") or "-")
                + " / 单位 " + str(item.get("单位") or "-")
                + " / 数量 " + str(item.get("数量") or "-")
                + " / 单价 " + str(item.get("单价") or "-")
                + " / 金额 " + str(item.get("金额") or "-")
            )
    return NL.join(lines)


# ---------------------------------------------------------------------------
# 五、把数据库里的发票详情还原成「中文键」的发票数据字典
#     （Agent 提示词、费用类型猜测用的都是中文键，这里统一转换一次）
# ---------------------------------------------------------------------------
def invoice_data_from_detail(detail):
    """数据库发票详情 → 识别阶段那种中文键发票数据字典"""
    detail = detail or {}
    info(f"[发票数据转换] 输入: invoice_number={detail.get('invoice_number')}, item_name={detail.get('item_name')}, parsed_keys={list((detail.get('parsed') or {}).keys())}")
    parsed = detail.get("parsed") or {}
    if parsed.get("发票号码"):
        # 识别时存的完整JSON，也做一次标准化（确保项目名称在顶层）
        result = parsed
    else:
        items = []
        for item in (detail.get("items") or []):
            items.append({
                "项目名称": item.get("item_name") or "",
                "规格型号": item.get("spec") or "",
                "单位": item.get("unit") or "",
                "数量": item.get("quantity") or "",
                "单价": item.get("unit_price") or "",
                "金额": item.get("amount"),
                "税率": item.get("tax_rate") or "",
                "税额": item.get("tax_amount"),
            })

        result = {
            "发票类型": detail.get("invoice_type") or "",
            "发票种类": detail.get("invoice_species") or "",
            "发票号码": detail.get("invoice_number") or "",
            "开票日期": detail.get("invoice_date") or "",
            "购买方名称": detail.get("buyer_name") or "",
            "购买方税号": detail.get("buyer_tax_id") or "",
            "销售方名称": detail.get("seller_name") or "",
            "销售方税号": detail.get("seller_tax_id") or "",
            "不含税金额": detail.get("amount_without_tax"),
            "税额": detail.get("tax_amount"),
            "税率": detail.get("tax_rate") or "",
            "价税合计小写": detail.get("total_amount"),
            "价税合计大写": detail.get("total_amount_cn") or "",
            "开票人": detail.get("drawer") or "",
            "备注": detail.get("remark") or "",
            "明细列表": items,
        }

    # 标准化：确保项目名称在顶层（从明细列表第一条取）
    if not result.get("项目名称"):
        detail_list = result.get("明细列表", [])
        if detail_list and isinstance(detail_list, list) and len(detail_list) > 0:
            first_item = detail_list[0]
            if isinstance(first_item, dict) and first_item.get("项目名称"):
                result["项目名称"] = first_item["项目名称"]

    success(f"[发票数据转换] 输出: 项目名称={result.get('项目名称')}, 发票号码={result.get('发票号码')}, 明细条数={len(result.get('明细列表', []))}")
    return result
