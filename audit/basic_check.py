# -*- coding: utf-8 -*-
# ============================================================================
# 基础合规校验模块
# ============================================================================
# 作用：发票识别完成后，第一道校验，纯代码规则，毫秒级，100%确定
# 与RAG制度校验的区别：
#   基础合规校验 = 客观事实判断（金额对不对、抬头对不对）
#   RAG制度校验   = 制度规则判断（住宿费超不超标、招待费合不合规）
# ============================================================================

import sys
from pathlib import Path

# 把项目根目录加入Python搜索路径（解决直接运行本文件时的导入问题）
# Path(__file__).parent = audit/
# Path(__file__).parent.parent = 项目根目录
sys.path.insert(0, str(Path(__file__).parent.parent))

import re
from datetime import datetime, timedelta
from database.db import is_invoice_exists
from config.settings import COMPANY_NAME, COMPANY_TAX_ID
from rag.utils.logger import info, success, warn, error


# ============================================================================
# 配置：校验标准（公司信息已移到 config/settings.py 统一管理）
# ============================================================================
# 报销期限：发票日期超过多少天不能报销（180天）
REIMBURSEMENT_DAYS = 180
# 法定税率列表（用于税率合法性校验）
VALID_TAX_RATES = ["13%", "9%", "6%", "3%", "1%", "0%", "免税", "***", ""]


# ============================================================================
# 工具函数：安全的数值转换
# ============================================================================
def safe_float(val):
    """
    安全转换为float，仅处理免税发票的标准星号格式

    合法的税额格式：
    - 正常数字："226.00"、"0.00"、"13.5"
    - 免税发票："***"（三个星号，标准免税格式）
    - 机动车免税发票："******"（六个星号）

    其他任何格式（空字符串、乱码、"-"、"无"等）都抛异常，
    由调用方捕获并返回"金额格式错误"，避免静默放过问题发票。

    参数：
        val: 待转换的值（字符串/数字/None）

    返回：
        float类型的数值

    抛出：
        ValueError: 格式不合法时抛出
    """
    if val is None:
        raise ValueError("值为None")
    s = str(val).strip()
    # 仅免税发票的标准星号格式当作0
    if s in ("***", "******"):
        return 0.0
    # 其他情况必须是合法数字，否则抛异常
    return float(s)


# ============================================================================
# 工具函数：构造校验结果
# ============================================================================
def make_result(check_name, passed, message, level="error"):
    """
    构造统一格式的校验结果

    参数：
        check_name: 校验项名称，如"金额勾稽校验"
        passed: 是否通过，True/False
        message: 校验结果描述
        level: 级别，"error"=必须修正，"warning"=提醒注意

    返回：
        字典，包含校验结果
    """
    return {
        "check_name": check_name,
        "pass": passed,
        "message": message,
        "level": level,
    }


# ============================================================================
# 校验1：必填字段完整性
# ============================================================================
def check_required_fields(invoice_data):
    """检查关键字段是否为空"""
    # 定义必填字段列表
    required_fields = ["发票号码", "开票日期", "购买方名称", "销售方名称", "价税合计小写"]

    # 遍历检查每个字段
    for field in required_fields:
        value = invoice_data.get(field, "")
        # 字段为空或只有空格，都算不通过
        if not value or not str(value).strip():
            return make_result(
                "必填字段完整性",
                False,
                f"必填字段[{field}]为空",
                "error"
            )

    return make_result("必填字段完整性", True, "所有必填字段均已填写")


# ============================================================================
# 校验2：购买方名称校验（抬头校验）
# ============================================================================
def check_buyer_name(invoice_data):
    """检查购买方名称是否为本公司"""
    buyer_name = invoice_data.get("购买方名称", "")

    # 去除空格后比较（避免"有限公司 "和"有限公司"被判定为不同）
    if buyer_name.strip() == COMPANY_NAME.strip():
        return make_result("购买方名称校验", True, f"购买方名称正确：{buyer_name}")
    else:
        return make_result(
            "购买方名称校验",
            False,
            f"购买方名称不符：发票上是[{buyer_name}]，本公司是[{COMPANY_NAME}]",
            "error"
        )


# ============================================================================
# 校验3：购买方税号校验
# ============================================================================
def check_buyer_tax_id(invoice_data):
    """检查购买方税号是否为本公司税号"""
    buyer_tax_id = invoice_data.get("购买方税号", "")

    if buyer_tax_id.strip() == COMPANY_TAX_ID.strip():
        return make_result("购买方税号校验", True, "购买方税号正确")
    else:
        return make_result(
            "购买方税号校验",
            False,
            f"购买方税号不符：发票上是[{buyer_tax_id}]，本公司是[{COMPANY_TAX_ID}]",
            "error"
        )


# ============================================================================
# 校验4：金额勾稽校验（不含税金额 + 税额 = 价税合计）
# ============================================================================
def check_amount_consistency(invoice_data):
    """检查不含税金额 + 税额 是否等于 价税合计"""
    # 逐个字段转换，哪个字段出错就把字段名和原始值报出来
    field_names = ["不含税金额", "税额", "价税合计小写"]
    values = {}
    for field in field_names:
        raw_val = invoice_data.get(field, "")
        try:
            values[field] = safe_float(raw_val)
        except (ValueError, TypeError) as e:
            return make_result(
                "金额勾稽校验",
                False,
                f"金额格式错误：字段[{field}]的值为「{raw_val}」，无法解析为数字（{e}）",
                "error"
            )

    amount_without_tax = values["不含税金额"]
    tax_amount = values["税额"]
    total_with_tax = values["价税合计小写"]

    # 计算：不含税 + 税额
    calculated = amount_without_tax + tax_amount
    # 允许误差0.01元（四舍五入）
    if abs(calculated - total_with_tax) <= 0.01:
        return make_result(
            "金额勾稽校验",
            True,
            f"金额勾稽正确：{amount_without_tax} + {tax_amount} = {calculated:.2f}"
        )
    else:
        return make_result(
            "金额勾稽校验",
            False,
            f"金额勾稽错误：不含税{amount_without_tax} + 税额{tax_amount} = {calculated:.2f}，但价税合计是{total_with_tax}",
            "error"
        )


# ============================================================================
# 校验5：明细汇总校验（明细金额之和=总金额，明细税额之和=总税额）
# ============================================================================
def check_detail_summary(invoice_data):
    """检查所有明细的金额之和、税额之和是否等于汇总金额"""
    detail_list = invoice_data.get("明细列表", [])
    if not detail_list:
        return make_result("明细汇总校验", True, "无明细数据，跳过")

    # 逐个明细行转换，出错时显示具体哪一行、哪个字段、什么值
    total_amount = 0.0
    total_tax = 0.0
    for idx, item in enumerate(detail_list, 1):
        # 转换金额
        raw_amount = item.get("金额", "")
        try:
            total_amount += safe_float(raw_amount)
        except (ValueError, TypeError) as e:
            return make_result(
                "明细汇总校验",
                False,
                f"明细金额格式错误：第{idx}行[金额]字段值为「{raw_amount}」，无法解析为数字（{e}）",
                "error"
            )
        # 转换税额
        raw_tax = item.get("税额", "")
        try:
            total_tax += safe_float(raw_tax)
        except (ValueError, TypeError) as e:
            return make_result(
                "明细汇总校验",
                False,
                f"明细税额格式错误：第{idx}行[税额]字段值为「{raw_tax}」，无法解析为数字（{e}）",
                "error"
            )

    # 转换汇总字段
    raw_invoice_total = invoice_data.get("不含税金额", "")
    try:
        invoice_total = safe_float(raw_invoice_total)
    except (ValueError, TypeError) as e:
        return make_result(
            "明细汇总校验",
            False,
            f"汇总金额格式错误：[不含税金额]字段值为「{raw_invoice_total}」，无法解析为数字（{e}）",
            "error"
        )

    raw_invoice_tax = invoice_data.get("税额", "")
    try:
        invoice_tax = safe_float(raw_invoice_tax)
    except (ValueError, TypeError) as e:
        return make_result(
            "明细汇总校验",
            False,
            f"汇总税额格式错误：[税额]字段值为「{raw_invoice_tax}」，无法解析为数字（{e}）",
            "error"
        )

    # 允许误差0.01元
    amount_ok = abs(total_amount - invoice_total) <= 0.01
    tax_ok = abs(total_tax - invoice_tax) <= 0.01

    if amount_ok and tax_ok:
        return make_result(
            "明细汇总校验",
            True,
            f"明细汇总正确：共{len(detail_list)}条明细，金额{total_amount:.2f}，税额{total_tax:.2f}"
        )
    else:
        return make_result(
            "明细汇总校验",
            False,
            f"明细汇总不符：明细金额合计{total_amount:.2f}(发票{invoice_total})，明细税额合计{total_tax:.2f}(发票{invoice_tax})",
            "error"
        )


# ============================================================================
# 校验6：明细单价校验（数量 × 单价 ≈ 金额）
# ============================================================================
def check_detail_unit_price(invoice_data):
    """检查每条明细的 数量 × 单价 是否约等于 金额（误差<1%）"""
    detail_list = invoice_data.get("明细列表", [])
    if not detail_list:
        return make_result("明细单价校验", True, "无明细数据，跳过")

    errors = []
    for i, item in enumerate(detail_list, 1):
        # 逐个字段转换，任何一个字段出错就记录错误并跳过这一行
        try:
            raw_qty = item.get("数量", "")
            raw_price = item.get("单价", "")
            raw_amt = item.get("金额", "")
            quantity = safe_float(raw_qty)
            unit_price = safe_float(raw_price)
            amount = safe_float(raw_amt)
        except (ValueError, TypeError) as e:
            # 找出具体是哪个字段出错了
            bad_field = "未知"
            bad_value = "未知"
            for field, val in [("数量", raw_qty), ("单价", raw_price), ("金额", raw_amt)]:
                try:
                    safe_float(val)
                except (ValueError, TypeError):
                    bad_field = field
                    bad_value = val
                    break
            errors.append(f"第{i}行[{bad_field}]格式错误：值为「{bad_value}」（{e}）")
            continue

        # 数量或单价为0时跳过（可能是免税等特殊情况）
        if quantity == 0 or unit_price == 0:
            continue

        # 计算 数量 × 单价
        calculated = quantity * unit_price
        # 误差小于金额的1%就算通过
        if amount > 0 and abs(calculated - amount) / amount > 0.01:
            errors.append(f"第{i}行：数量{quantity} × 单价{unit_price} = {calculated:.2f}，但金额是{amount}")

    if not errors:
        return make_result("明细单价校验", True, "所有明细单价计算正确")
    else:
        return make_result(
            "明细单价校验",
            False,
            "明细单价不符：" + "；".join(errors),
            "warning"  # 可能是四舍五入导致，设为warning
        )


# ============================================================================
# 校验7：开票日期合理性
# ============================================================================
def check_invoice_date(invoice_data):
    """检查开票日期：不能是未来日期，不能超过报销期限"""
    invoice_date_str = invoice_data.get("开票日期", "")

    # 解析日期，支持 "2026年08月31日" 格式
    try:
        # 用正则提取年月日
        match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", invoice_date_str)
        if not match:
            return make_result("开票日期校验", False, f"无法解析开票日期：{invoice_date_str}", "error")
        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
        invoice_date = datetime(year, month, day)
    except Exception:
        return make_result("开票日期校验", False, f"日期格式错误：{invoice_date_str}", "error")

    # 检查1：不能是未来日期
    today = datetime.now()
    if invoice_date > today:
        return make_result(
            "开票日期校验",
            False,
            f"开票日期{invoice_date_str}是未来日期，不正常",
            "error"
        )

    # 检查2：不能超过报销期限
    deadline = today - timedelta(days=REIMBURSEMENT_DAYS)
    if invoice_date < deadline:
        return make_result(
            "开票日期校验",
            False,
            f"开票日期{invoice_date_str}已超过{REIMBURSEMENT_DAYS}天报销期限",
            "error"
        )

    return make_result("开票日期校验", True, f"开票日期{invoice_date_str}正常")


# ============================================================================
# 校验8：发票号码格式校验
# ============================================================================
def check_invoice_number_format(invoice_data):
    """检查发票号码格式：数电票为20位数字"""
    invoice_number = invoice_data.get("发票号码", "")

    # 数电票号码是20位数字
    if re.match(r"^\d{20}$", invoice_number):
        return make_result("发票号码格式", True, f"发票号码格式正确：{invoice_number}")
    else:
        return make_result(
            "发票号码格式",
            False,
            f"发票号码格式错误：{invoice_number}（应为20位数字）",
            "error"
        )


# ============================================================================
# 校验9：重复报销校验
# ============================================================================
def check_duplicate_invoice(invoice_data):
    """检查发票号码是否已经报销过（需要数据库支持）"""
    invoice_number = invoice_data.get("发票号码", "")

    if not invoice_number:
        return make_result("重复报销校验", False, "发票号码为空，无法校验", "error")

    # 调用数据库查询函数
    if is_invoice_exists(invoice_number):
        return make_result(
            "重复报销校验",
            False,
            f"发票号码{invoice_number}已存在报销记录，疑似重复报销",
            "error"
        )
    else:
        return make_result("重复报销校验", True, "无重复报销记录")


# ============================================================================
# 校验10：税率合法性校验
# ============================================================================
def check_tax_rate_validity(invoice_data):
    """检查税率是否为法定税率"""
    detail_list = invoice_data.get("明细列表", [])
    if not detail_list:
        # 没有明细时检查发票整体税率
        tax_rate = invoice_data.get("税率", "")
        if tax_rate in VALID_TAX_RATES:
            return make_result("税率合法性校验", True, f"税率{tax_rate}合法")
        else:
            return make_result("税率合法性校验", False, f"税率{tax_rate}不合法", "warning")

    # 检查每条明细的税率
    invalid_rates = []
    for i, item in enumerate(detail_list, 1):
        rate = item.get("税率", "")
        if rate not in VALID_TAX_RATES:
            invalid_rates.append(f"第{i}行税率{rate}")

    if not invalid_rates:
        return make_result("税率合法性校验", True, "所有明细税率均合法")
    else:
        return make_result(
            "税率合法性校验",
            False,
            "存在不合法税率：" + "，".join(invalid_rates),
            "warning"
        )


# ============================================================================
# 校验11：销售方信息完整性
# ============================================================================
def check_seller_info(invoice_data):
    """检查销售方名称和税号是否都填写了"""
    seller_name = invoice_data.get("销售方名称", "")
    seller_tax_id = invoice_data.get("销售方税号", "")

    if not seller_name or not seller_name.strip():
        return make_result("销售方信息完整性", False, "销售方名称为空", "error")
    if not seller_tax_id or not seller_tax_id.strip():
        return make_result("销售方信息完整性", False, "销售方税号为空", "error")

    return make_result("销售方信息完整性", True, f"销售方信息完整：{seller_name}")


# ============================================================================
# 校验12：价税合计大小写一致性
# ============================================================================
def check_amount_case_consistency(invoice_data):
    """检查价税合计大写和小写是否一致（简单校验：非空即可）"""
    amount_lower = invoice_data.get("价税合计小写", "")
    amount_upper = invoice_data.get("价税合计大写", "")

    if not amount_lower or not amount_upper:
        return make_result("价税合计大小写一致性", False, "大写或小写金额为空", "warning")

    # 完整的大写金额转数字校验比较复杂，这里先做非空校验
    # 后续可以加中文大写金额转数字的函数做精确校验
    return make_result(
        "价税合计大小写一致性",
        True,
        f"大小写金额均已填写：{amount_upper}（¥{amount_lower}）"
    )


# ============================================================================
# 主函数：运行所有12项基础合规校验
# ============================================================================
def basic_check(invoice_data):
    """
    运行所有12项基础合规校验，返回汇总结果

    参数：
        invoice_data: 发票识别结果字典

    返回：
        {
            "all_passed": True/False,  # 是否全部通过
            "error_count": N,          # 错误数量
            "warning_count": N,        # 警告数量
            "results": [校验结果列表]
        }
    """
    # 按顺序运行12项校验
    check_functions = [
        check_required_fields,       # 1. 必填字段完整性
        check_buyer_name,            # 2. 购买方名称校验
        check_buyer_tax_id,          # 3. 购买方税号校验
        check_amount_consistency,    # 4. 金额勾稽校验
        check_detail_summary,        # 5. 明细汇总校验
        check_detail_unit_price,     # 6. 明细单价校验
        check_invoice_date,          # 7. 开票日期合理性
        check_invoice_number_format, # 8. 发票号码格式
        check_duplicate_invoice,     # 9. 重复报销校验
        check_tax_rate_validity,     # 10. 税率合法性
        check_seller_info,           # 11. 销售方信息完整性
        check_amount_case_consistency,  # 12. 价税合计大小写一致
    ]

    # 运行所有校验
    results = []
    for idx, check_func in enumerate(check_functions, 1):
        # 取校验说明（函数docstring第一行，说明这个校验在做什么）
        doc = (check_func.__doc__ or "无说明").strip().split("\n")[0]

        # 打印输入参数（关键字段摘要，便于后端追踪每一步拿到什么数据）
        input_summary = {
            "发票号码": invoice_data.get("发票号码", ""),
            "购买方": invoice_data.get("购买方名称", ""),
            "销售方": invoice_data.get("销售方名称", ""),
            "价税合计": invoice_data.get("价税合计小写", ""),
        }
        info(f"【基础校验{idx}/12】开始：{check_func.__name__}（{doc}）")
        info(f"    输入参数: 发票号码={input_summary['发票号码']}, 购买方={input_summary['购买方']}, 销售方={input_summary['销售方']}, 价税合计={input_summary['价税合计']}")

        # 执行该校验函数
        result = check_func(invoice_data)
        results.append(result)

        # 打印校验结果
        if result["pass"]:
            success(f"【基础校验{idx}/12】通过：{result['check_name']} → {result['message']}")
        elif result["level"] == "error":
            error(f"【基础校验{idx}/12】不通过：{result['check_name']} → {result['message']}")
        else:
            warn(f"【基础校验{idx}/12】警告：{result['check_name']} → {result['message']}")

    # 统计错误和警告数量
    error_count = sum(1 for r in results if not r["pass"] and r["level"] == "error")
    warning_count = sum(1 for r in results if not r["pass"] and r["level"] == "warning")

    # 全部通过的条件：没有error（warning可以通过）
    all_passed = error_count == 0

    return {
        "all_passed": all_passed,
        "error_count": error_count,
        "warning_count": warning_count,
        "results": results,
    }


# ============================================================================
# 测试代码
# ============================================================================
if __name__ == "__main__":
    import sys
    from pathlib import Path

    # 把项目根目录加入Python搜索路径（解决直接运行子目录文件时的导入问题）
    # Path(__file__).parent.parent = 项目根目录
    project_root = Path(__file__).parent.parent
    sys.path.insert(0, str(project_root))

    import json
    from recognizers.xml_parser import extract_from_xml

    # 测试XML发票
    xml_path = project_root / "发票材料" / "xml" / "dzfp_26322000007174489411_20260831104050.xml"
    invoice_data = extract_from_xml(xml_path)

    # 运行基础校验
    result = basic_check(invoice_data)

    print("=" * 60)
    print("基础合规校验结果")
    print("=" * 60)
    print(f"是否全部通过：{result['all_passed']}")
    print(f"错误数量：{result['error_count']}")
    print(f"警告数量：{result['warning_count']}")
    print()

    for r in result["results"]:
        status = "✅" if r["pass"] else "❌"
        print(f"{status} [{r['level']}] {r['check_name']}：{r['message']}")
