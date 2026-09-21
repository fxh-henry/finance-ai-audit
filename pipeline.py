# -*- coding: utf-8 -*-
# ============================================================================
# 审核流程编排（pipeline.py）
# ============================================================================
# 作用：串联"识别 → 基础校验"完整流程，返回标准化结果
# 位置：项目主目录，因为它是顶层流程，不属于任何子模块
# 调用方：app.py（Streamlit界面）、测试脚本
# ============================================================================

import sys
from pathlib import Path

# 把项目根目录加入Python搜索路径
# 这样下面的 from recognizers.xxx import 才能找到模块
sys.path.insert(0, str(Path(__file__).parent))

# 导入识别层：统一识别入口
from recognizers.invoice_recognizer import recognize_invoice

# 导入校验层：基础合规校验
from audit.basic_check import basic_check

# 导入审查Agent
from agent.review_agent import ReviewAgent

# 导入风控检测
from audit.anomaly_detect import run_all_anomaly_checks

# 导入数据库（获取默认员工ID）
from database.db import get_default_employee_id

# 导入日志工具
from rag.utils.logger import info, success, error, warn


# ============================================================================
# 函数1：执行"识别 + 基础校验"完整流程
# ============================================================================
def run_basic_check_pipeline(file_path):
    """
    执行完整的基础审核流程：识别 → 基础校验

    参数：
        file_path: 发票文件路径（支持XML/PDF/JPG/PNG）

    返回：
        标准化结果字典，包含：
        - status: 整体状态 success/failed
        - stage: 执行到哪一步 recognize/basic_check
        - invoice_data: 识别出的发票数据（识别成功时才有）
        - check_results: 基础校验的逐条结果
        - summary: 校验汇总统计
        - error: 错误信息（识别失败时才有）
    """
    info("=" * 60)
    info("【审核流程】开始执行：识别 + 基础校验")
    info("=" * 60)

    # ========================================================================
    # 第1步：发票识别
    # ========================================================================
    info(f"【第1步/2】开始识别发票: {file_path}")

    try:
        # 调用统一识别入口，自动判断文件类型（XML/PDF/图片）
        # recognize_invoice 内部会根据文件后缀和文件头选择对应的解析器
        invoice_data = recognize_invoice(file_path)

        # 识别失败的情况：recognize_invoice 返回 {"error": "错误信息"}
        if "error" in invoice_data:
            error(f"发票识别失败: {invoice_data['error']}")
            return {
                "status": "failed",           # 整体状态：失败
                "stage": "recognize",         # 失败发生在识别阶段
                "error": {
                    "code": "RECOGNIZE_FAILED",
                    "message": invoice_data["error"]
                }
            }

        success(f"发票识别成功，发票号码: {invoice_data.get('发票号码', '未知')}")

    except Exception as e:
        # 识别过程中发生异常（文件损坏、解析错误等）
        error(f"发票识别异常: {e}")
        return {
            "status": "failed",
            "stage": "recognize",
            "error": {
                "code": "RECOGNIZE_EXCEPTION",
                "message": str(e)
            }
        }

    # ========================================================================
    # 第2步：基础合规校验
    # ========================================================================
    info("【第2步/2】开始基础合规校验")

    try:
        # 调用基础校验函数，传入识别出的发票数据
        # basic_check 会执行12项规则，返回逐条校验结果
        check_result = basic_check(invoice_data)

        success("基础合规校验完成")

    except Exception as e:
        # 校验过程中发生异常
        error(f"基础校验异常: {e}")
        return {
            "status": "failed",
            "stage": "basic_check",
            "invoice_data": invoice_data,    # 识别数据还是要返回，方便用户查看
            "error": {
                "code": "CHECK_EXCEPTION",
                "message": str(e)
            }
        }

    # ========================================================================
    # 组装最终返回结果
    # ========================================================================
    # basic_check 返回的格式：
    #   all_passed: 是否全部通过（无error）
    #   error_count: error级别数量
    #   warning_count: warning级别数量
    #   results: 逐条校验结果，每条含 pass/level/check_name/message
    # 这里我们组装成统一的summary格式
    total = len(check_result["results"])
    passed_count = sum(1 for r in check_result["results"] if r["pass"])
    failed_count = total - passed_count

    result = {
        "status": "success",                  # 整体状态：成功（流程跑通了，不代表校验通过）
        "stage": "basic_check",               # 当前阶段：基础校验完成
        "invoice_data": invoice_data,         # 识别出的发票数据
        "check_results": check_result["results"],  # 逐条校验结果
        "summary": {
            "total": total,                   # 总检查项数
            "passed": passed_count,           # 通过项数
            "failed": failed_count,           # 不通过项数
            "error_count": check_result["error_count"],   # error级别数量
            "warning_count": check_result["warning_count"],  # warning级别数量
            "final_result": "通过" if check_result["all_passed"] else "不通过"
        }
    }

    info("=" * 60)
    success(f"【审核流程】执行完成，校验结果: {result['summary']['final_result']}")
    info("=" * 60)

    return result


# ============================================================================
# 函数2：把标准化JSON转成用户友好的文本格式
# ============================================================================
def format_for_user(result):
    """
    把标准化JSON结果转成用户易读的文本格式

    参数：
        result: run_basic_check_pipeline 返回的标准化字典

    返回：
        友好文本字符串，用于界面显示
    """
    # 如果整体流程失败（识别失败等）
    if result["status"] == "failed":
        error_info = result.get("error", {})
        text = "❌ 处理失败\n"
        text += f"   阶段: {result.get('stage', '未知')}\n"
        text += f"   原因: {error_info.get('message', '未知错误')}\n"
        return text

    # 流程成功，读取校验汇总
    summary = result["summary"]
    total = summary["total"]        # 总检查项数
    passed = summary["passed"]      # 通过项数
    failed = summary["failed"]      # 不通过项数
    final = summary["final_result"] # 最终结论

    # 先输出整体结论
    if final == "通过":
        text = f"✅ 基础校验通过\n"
        text += f"   共{total}项检查，全部通过\n"
    else:
        text = f"❌ 基础校验未通过\n"
        text += f"   共{total}项检查，{passed}项通过，{failed}项不通过\n"

    # 分离不通过项和警告项
    failed_items = []   # 不通过（error级别）
    warning_items = []  # 警告（warning级别）

    for item in result["check_results"]:
        # basic_check里用的是 "pass" 字段，不是 "passed"
        if not item["pass"]:
            if item.get("level") == "warning":
                warning_items.append(item)
            else:
                failed_items.append(item)

    # 输出不通过项
    if failed_items:
        text += "\n❌ 不通过项：\n"
        for i, item in enumerate(failed_items, 1):
            # basic_check里用的是 "check_name" 字段
            text += f"   {i}. {item['check_name']}\n"
            # 如果有期望值和实际值，显示出来
            if "expected" in item and "actual" in item:
                text += f"      应为: {item['expected']}\n"
                text += f"      实际: {item['actual']}\n"
            # 显示错误信息
            text += f"      {item['message']}\n"
            # 如果有修复建议，显示出来
            if item.get("suggestion"):
                text += f"      建议: {item['suggestion']}\n"

    # 输出警告项
    if warning_items:
        text += "\n⚠️ 警告项：\n"
        for i, item in enumerate(warning_items, 1):
            text += f"   {i}. {item['check_name']}\n"
            text += f"      {item['message']}\n"
            if item.get("suggestion"):
                text += f"      建议: {item['suggestion']}\n"

    return text


# ============================================================================
# 函数3：完整审核流程（识别 → 基础校验 → Agent深度审核）
# ============================================================================
def run_full_pipeline(file_path, expense_form=None):
    """
    执行完整审核流程：识别 → 基础校验 → (通过后) Agent深度审核

    流程：
    1. 发票识别
    2. 基础合规校验（12项）
    3. 如果基础校验通过，调用Agent进行深度审核
    4. 如果基础校验不通过，不调用Agent，直接返回

    参数：
        file_path: 发票文件路径（支持XML/PDF/JPG/PNG）
        expense_form: 报销单数据（可选）。传了就把「发票 + 报销单」一起交给 Agent 深度审核，不传则只审发票（兼容上传页老流程）

    返回：
        标准化结果字典，包含invoice_data、basic_check、agent_result
        agent_result中的agent是ReviewAgent实例，可用于多轮对话
    """
    info("=" * 70)
    info("【完整审核流程】开始：识别 → 基础校验 → Agent深度审核")
    info("=" * 70)

    # 第1步：识别 + 基础校验（复用已有的函数）
    basic_result = run_basic_check_pipeline(file_path)

    # 如果识别或基础校验流程失败，直接返回
    if basic_result["status"] == "failed":
        error("【完整审核流程】基础流程失败，终止")
        return {
            "status": "failed",
            "stage": basic_result.get("stage", "recognize"),
            "invoice_data": basic_result.get("invoice_data"),
            "basic_check": None,
            "agent_result": None,
            "error": basic_result.get("error", {
                "code": "UNKNOWN",
                "message": "未知错误"
            })
        }

    # 取出识别数据和基础校验结果
    invoice_data = basic_result["invoice_data"]
    summary = basic_result["summary"]

    # 第2步：判断基础校验是否通过
    if summary["final_result"] != "通过":
        warn(f"【完整审核流程】基础校验未通过（{summary['failed']}项），跳过Agent审核")
        return {
            "status": "success",
            "stage": "basic_check",
            "invoice_data": basic_result["invoice_data"],
            "basic_check": {
                "check_results": basic_result["check_results"],
                "summary": basic_result["summary"]
            },
            "agent_result": None,
            "message": "基础校验未通过，未进入Agent深度审核"
        }

    # 第3步：基础校验通过，调用Agent深度审核
    info("【完整审核流程】基础校验通过，开始Agent深度审核")

    try:
        # 创建ReviewAgent实例（绑定发票数据）
        agent = ReviewAgent(invoice_data, expense_form=expense_form)
        # 第一轮：让Agent审核这张发票
        agent_answer = agent.chat("请审核这张发票，给出审核结论。")
        success("【完整审核流程】Agent深度审核完成")

    except Exception as e:
        error(f"【完整审核流程】Agent审核异常: {e}")
        return {
            "status": "failed",
            "stage": "agent_check",
            "invoice_data": invoice_data,
            "basic_check": {
                "check_results": basic_result["check_results"],
                "summary": summary
            },
            "error": {"code": "AGENT_EXCEPTION", "message": str(e)}
        }

    # 组装最终返回结果
    result = {
        "status": "success",
        "stage": "agent_check",
        "invoice_data": invoice_data,
        "basic_check": {
            "check_results": basic_result["check_results"],
            "summary": summary
        },
        "agent_result": {
            "answer": agent_answer,    # Agent的审核结果文本
            "agent": agent             # ReviewAgent实例，供后续多轮对话
        }
    }

    info("=" * 70)
    success("【完整审核流程】全部执行完成")
    info("=" * 70)

    return result


# ============================================================================
# 函数4：报销单终审（风控检测 → Agent终审）
# ============================================================================
def run_expense_form_audit(invoice_data, expense_form, employee_id=None, exclude_form_id=None):
    """
    报销单提交时调用：先跑风控检测，再把发票+报销单+风控结果一起交给Agent终审。

    流程：
    1. 风控检测（纯代码，6项全跑）：重复/连号/拆分/金额临界/高频/行为画像
    2. Agent终审：结合风控结果和报销制度，给出最终结论

    参数：
        invoice_data: 发票数据字典（中文键，来自识别结果）
        expense_form: 报销单数据字典（含 trip_no/expense_type/ext_fields/detail_items 等）
        employee_id: 员工ID，用于风控检测（不传则用默认员工）
        exclude_form_id: 当前正在提交的报销单ID，风控检测时排除自己，避免重复报销误判

    返回：
        {
            "answer": Agent结论文本,
            "agent": ReviewAgent实例（可继续追问）,
            "anomaly_check": 风控检测标准化结果
        }
    """
    info("=" * 70)
    info("【报销单终审】开始：风控检测 → Agent终审")
    info("=" * 70)

    # ========================================================================
    # 第1步：风控检测（纯代码，6项全跑）
    # ========================================================================
    info("【第1步/2】开始风控异常检测")
    info(f"  员工ID: {employee_id}")
    info(f"  发票号码: {invoice_data.get('发票号码', '未知')}")
    info(f"  排除当前报销单ID: {exclude_form_id}")

    if employee_id is None:
        employee_id = get_default_employee_id()
        info(f"  未指定员工ID，使用默认员工: {employee_id}")

    try:
        # 一次性跑全部6项风控检测，返回标准化结果
        # exclude_form_id 排除当前正在提交的报销单，避免把自己判为重复报销
        anomaly_result = run_all_anomaly_checks(employee_id, invoice_data, exclude_form_id=exclude_form_id)
        if anomaly_result["has_risk"]:
            warn(f"  风控检测完成，发现 {anomaly_result['risk_count']} 项风险")
            for item in anomaly_result["risk_items"]:
                warn(f"    [{item['level']}] {item['type']}: {item['message']}")
        else:
            success("  风控检测完成，未发现异常")
            info("  6项检测全部通过：重复报销/连号/拆分/金额临界/高频/行为画像")
    except Exception as e:
        # 风控检测异常不阻断流程，记录错误后继续
        error(f"  风控检测异常: {e}，继续执行Agent审核")
        anomaly_result = {
            "has_risk": False,
            "risk_count": 0,
            "risk_items": [],
            "all_results": {},
            "error": str(e)
        }

    # ========================================================================
    # 第2步：Agent终审（结合风控结果）
    # ========================================================================
    info("【第2步/2】开始Agent终审")
    info("  正在将风控结果和报销制度上下文注入Agent...")

    # 把风控结果格式化成文本，作为Agent的上下文
    anomaly_context = format_anomaly_for_agent(anomaly_result)
    info(f"  风控上下文: {anomaly_context[:100]}...")

    # 创建绑定了报销单的Agent（提示词里会多出「报销单信息」段落）
    agent = ReviewAgent(invoice_data, expense_form=expense_form)
    success("  Agent初始化成功")

    # 构造初始审核指令，包含风控结果
    initial_prompt = (
        "请审核这张发票及其对应的报销单，给出最终审核结论。\n\n"
        "【风控检测结果】\n"
        f"{anomaly_context}\n\n"
        "请结合以上风控检测结果和报销制度，给出最终审核结论。"
        "如果风控检测发现风险，请在结论中重点说明。"
    )
    info("  正在调用LLM生成审核结论...")

    # 单轮审核：让Agent给出最终结论
    answer = agent.chat(initial_prompt)
    success("  Agent终审完成")

    success("【报销单终审】全部完成（风控检测 + Agent终审）")
    return {
        "answer": answer,
        "agent": agent,
        "anomaly_check": anomaly_result
    }


# ============================================================================
# 函数5：把风控结果格式化成Agent可读的文本
# ============================================================================
def format_anomaly_for_agent(anomaly_result):
    """
    把风控检测结果转成简洁文本，作为上下文传给Agent

    参数：
        anomaly_result: run_all_anomaly_checks 的返回
    返回：
        文本字符串
    """
    if not anomaly_result.get("has_risk"):
        return "未发现风控异常。"

    lines = [f"共发现 {anomaly_result['risk_count']} 项风险："]
    for i, item in enumerate(anomaly_result["risk_items"], 1):
        lines.append(f"{i}. [{item['level']}] {item['type']}：{item['message']}")
    return "\n".join(lines)


# ============================================================================
# 函数6：把风控结果格式化成用户友好文本（用于界面展示）
# ============================================================================
def format_anomaly_for_user(anomaly_result):
    """
    把风控检测结果转成用户易读的文本格式

    参数：
        anomaly_result: run_all_anomaly_checks 的返回
    返回：
        友好文本字符串
    """
    if anomaly_result.get("error"):
        return f"风控检测异常：{anomaly_result['error']}"

    if not anomaly_result["has_risk"]:
        return "风控检测通过，未发现异常报销行为。"

    text = f"风控检测发现 {anomaly_result['risk_count']} 项异常：\n"

    # 按风险等级分组
    high_items = [i for i in anomaly_result["risk_items"] if i["level"] == "high"]
    medium_items = [i for i in anomaly_result["risk_items"] if i["level"] == "medium"]
    low_items = [i for i in anomaly_result["risk_items"] if i["level"] == "low"]

    if high_items:
        text += "\n【高风险】\n"
        for i, item in enumerate(high_items, 1):
            text += f"  {i}. {item['type']}\n"
            text += f"     {item['message']}\n"

    if medium_items:
        text += "\n【中风险】\n"
        for i, item in enumerate(medium_items, 1):
            text += f"  {i}. {item['type']}\n"
            text += f"     {item['message']}\n"

    if low_items:
        text += "\n【低风险】\n"
        for i, item in enumerate(low_items, 1):
            text += f"  {i}. {item['type']}\n"
            text += f"     {item['message']}\n"

    return text



def format_full_result(result):
    """
    把完整审核流程的结果转成用户友好文本
    """
    print(result)
    print(result["status"])
    if result["status"] == "failed":
        error_info = result.get("error", {})
        return f"处理失败\n   阶段: {result.get('stage', '未知')}\n   原因: {error_info.get('message', '未知错误')}\n"

    text = ""

    # ===== 基础校验部分（详细输出） =====
    if "basic_check" in result:
        basic = result["basic_check"]
        summary = basic["summary"]
        check_results = basic["check_results"]



        if summary["final_result"] == "通过":
            text += f"基础校验通过（共{summary['total']}项，全部通过）\n"
        else:
            text += f"基础校验未通过（共{summary['total']}项，{summary['passed']}项通过，{summary['failed']}项不通过）\n"

            # 分离不通过项和警告项
            failed_items = []
            warning_items = []
            for item in check_results:
                if not item["pass"]:
                    if item.get("level") == "warning":
                        warning_items.append(item)
                    else:
                        failed_items.append(item)

            # 输出不通过项
            if failed_items:
                text += "\n不通过项：\n"
                for i, item in enumerate(failed_items, 1):
                    text += f"   {i}. {item['check_name']}\n"
                    if "expected" in item and "actual" in item:
                        text += f"      应为: {item['expected']}\n"
                        text += f"      实际: {item['actual']}\n"
                    text += f"      {item['message']}\n"
                    if item.get("suggestion"):
                        text += f"      建议: {item['suggestion']}\n"

            # 输出警告项
            if warning_items:
                text += "\n警告项：\n"
                for i, item in enumerate(warning_items, 1):
                    text += f"   {i}. {item['check_name']}\n"
                    text += f"      {item['message']}\n"
                    if item.get("suggestion"):
                        text += f"      建议: {item['suggestion']}\n"

    # ===== Agent审核部分 =====
    if result.get("agent_result"):
        text += "\n" + "=" * 50 + "\n"
        text += "AI深度审核结果\n"
        text += "=" * 50 + "\n"
        text += result["agent_result"]["answer"]
    elif result.get("message"):
        text += f"\n{result['message']}\n"
    print(text)
    return text

# ============================================================================
# 测试代码
# ============================================================================
if __name__ == "__main__":
    # 测试XML发票（购买方是本公司，基础校验能通过）
    print("=" * 60)
    print("测试：完整审核流程（识别 + 基础校验 + Agent深度审核）")
    print("=" * 60)

    xml_path = r"D:\agent\财务报销项目\发票材料\xml\dzfp_26322000007174489411_20260831104050.xml"
    result = run_full_pipeline(xml_path)

    print(result)

    # 打印用户友好文本
    print("\n----- 审核结果 -----")
    print(format_full_result(result))

    # 如果有Agent实例，测试多轮对话
    if result.get("agent_result") and result["agent_result"].get("agent"):
        agent = result["agent_result"]["agent"]
        print("\n----- 多轮对话测试 -----")
        print("\n员工：这张发票有什么需要注意的？")
        print(f"Agent：{agent.chat('这张发票有什么需要注意的？')}")
