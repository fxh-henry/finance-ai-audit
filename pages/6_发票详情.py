# -*- coding: utf-8 -*-
"""发票详情：从票夹卡片点击“查看详情”后跳转进来的页面（不在侧边栏显示）"""
import importlib  # 用于重新加载数据库模块，避免热重载时用到旧代码

import streamlit as st  # 导入 Streamlit

import database.db as _db  # 引入数据库模块（查单张发票详情 / 删除 / 找原文件）
from utils.audit_verdict import normalize_audit  # 引入结论规范化工具（基础校验通过 ≠ 最终通过）

_db = importlib.reload(_db)  # 强制重新加载，保证拿到最新的函数
discard_folder_invoice = _db.discard_folder_invoice  # 取出“从票夹移除”的函数
get_invoice_detail = _db.get_invoice_detail  # 取出“查询单张发票详情”的函数
resolve_invoice_file_path = _db.resolve_invoice_file_path  # 取出“把库里的路径还原成磁盘路径”的函数
get_expense_form_by_invoice = _db.get_expense_form_by_invoice  # 按发票查报销单（判断是否已填写）

st.set_page_config(page_title="发票详情", layout="wide")  # 页面配置：标签标题 + 宽屏布局


def _fmt_amount(value):  # 工具函数：把金额格式化成 ¥1,234.00（页头要用，所以先定义）
    if value is None or value == "":  # 金额为空（可能是识别失败）
        return "-"  # 显示一个短横线
    try:  # 正常情况是数字
        return f"¥{float(value):,.2f}"  # 千分位 + 保留两位小数
    except (TypeError, ValueError):  # 不是数字（例如写成“壹佰元”）
        return str(value)  # 原样显示，至少不报错


invoice_id = st.session_state.get("folder_invoice_id")  # 取票夹页面存下的发票 id（没有就是 None）
detail = get_invoice_detail(invoice_id) if invoice_id else None  # 有 id 才去数据库查，避免无意义查询

# ---------------------------------------------------------------------------
# 页头：左边标题 + 摘要，右边“返回票夹”按钮（与其他页面同一套标题样式）
# ---------------------------------------------------------------------------
with st.container(key="pagehead"):  # 统一页头容器：标题样式与灰线间距由 app.py 统一控制
    head_l, head_r = st.columns([4, 1], vertical_alignment="bottom")  # 左标题、右按钮，两列底部对齐
    with head_l:  # 左侧：标题与摘要
        st.header("发票详情")  # 统一字号的大标题
        if detail:  # 查到发票：显示“销售方 · 金额 · 上传时间”摘要
            seller = detail.get("seller_name") or "未识别销售方"  # 销售方名称，空的就兜底提示
            st.caption(f"{seller} · {_fmt_amount(detail.get('total_amount'))} · 上传于 {detail.get('uploaded_at') or '-'}")  # 灰色摘要文字
        else:  # 没查到：只显示通用说明
            st.caption("查看发票识别结果、商品明细与原文件")  # 灰色说明文字
    with head_r:  # 右侧：返回按钮
        if st.button("← 返回票夹", use_container_width=True):  # 点击返回
            st.switch_page("pages/3_我的票夹.py")  # 跳回我的票夹页
    st.divider()  # 灰色横线，间距由全局样式统一控制

if not invoice_id:  # 没有带发票 id 进来（例如直接访问本页）
    st.warning("未选择发票，请从票夹进入。")  # 黄色提示
    st.stop()  # 停止继续渲染本页，避免下面查不到数据报错

if not detail:  # 有 id 但查不到（可能已被删除）
    st.error("发票不存在或已删除。")  # 红色错误提示
    st.stop()  # 停止渲染

st.subheader("基本信息")  # 小标题：基本信息
c1, c2, c3 = st.columns(3)  # 分成三列展示字段
with c1:  # 第一列：发票本身
    st.write("发票类型：", detail.get("invoice_type") or "-")  # 发票类型
    st.write("发票号码：", detail.get("invoice_number") or "-")  # 发票号码
    st.write("开票日期：", detail.get("invoice_date") or "-")  # 开票日期
with c2:  # 第二列：双方与金额
    st.write("购买方：", detail.get("buyer_name") or "-")  # 购买方名称
    st.write("销售方：", detail.get("seller_name") or "-")  # 销售方名称
    st.write("价税合计：", _fmt_amount(detail.get("total_amount")))  # 价税合计金额
with c3:  # 第三列：文件与状态
    st.write("原文件：", detail.get("original_filename") or "-")  # 用户上传时的原始文件名
    st.write("识别状态：", "成功" if detail.get("parse_status") == "success" else "失败")  # 识别是否成功
    st.write("上传时间：", detail.get("uploaded_at") or "-")  # 存入票夹的时间

if detail.get("parse_status") != "success" and detail.get("parse_error"):  # 当时识别失败且记录了错误
    st.error(f"识别失败：{detail['parse_error']}")  # 红色错误框显示失败原因

# ---------------------------------------------------------------------------
# Agent 审核结果：读取“存入票夹”时一起写库的那份审核记录
# ---------------------------------------------------------------------------
audit = normalize_audit(detail.get("audit")) or {}  # 审核记录字典；顺手纠正结论，旧数据可能把“基础校验通过”误记成最终通过
final_result = audit.get("final_result")  # 审核结论：通过 / 不通过 / None（未审核）

st.subheader("审核结果")  # 小标题：审核结果（发票层是基础校验，报销单层才是 Agent 终审）
if final_result == "通过":  # 审核通过
    st.success(f"已审核 · 通过　·　审核时间：{audit.get('checked_at') or '-'}")  # 绿色结论条：明确“已审核”
elif final_result == "不通过":  # 审核不通过
    st.error(f"已审核 · 未通过　·　审核时间：{audit.get('checked_at') or '-'}")  # 红色结论条：已审核但没通过
else:  # 还没有审核记录（例如旧数据，或识别失败后直接存的）
    st.warning("暂无审核记录，请先在「发票上传」页对本张发票完成智能预审")  # 黄色提示

if audit.get("stage") == "basic_check" and final_result in ("通过", "不通过"):  # 这份结论来自基础校验
    st.caption("以上为发票初审结论（基础合规校验）；填写报销单后还会再送 Agent 做财务终审。")  # 说明结论来源


if audit.get("agent_answer"):  # 有 AI 深度审核文本时才显示
    st.write(audit["agent_answer"])  # 直接展示 Agent 的审核结论原文（不再加“AI审核结论”小标题）

if audit.get("detail_text"):  # 有完整审核明细时才显示
    with st.expander("完整审核明细"):  # 折叠面板，默认收起
        st.code(audit["detail_text"], language=None)  # 等宽文本显示，保持原有排版

items = detail.get("items") or []  # 取出商品明细列表（没有就是空列表）
if items:  # 有明细才显示
    st.subheader("商品明细")  # 小标题：商品明细
    st.dataframe(  # 用表格展示明细
        [  # 逐行整理成“中文表头 → 值”的字典
            {  # 一行明细
                "行号": it.get("line_no"),  # 行号
                "项目名称": it.get("item_name"),  # 项目名称
                "规格型号": it.get("spec"),  # 规格型号
                "单位": it.get("unit"),  # 单位
                "数量": it.get("quantity"),  # 数量
                "单价": it.get("unit_price"),  # 单价
                "金额": it.get("amount"),  # 金额
                "税率": it.get("tax_rate"),  # 税率
                "税额": it.get("tax_amount"),  # 税额
            }
            for it in items  # 遍历每一条明细
        ],
        use_container_width=True,  # 表格撑满宽度
        hide_index=True,  # 隐藏表格左侧默认的行号列
    )

parsed = detail.get("parsed") or {}  # 取出当初保存的完整解析 JSON
if parsed:  # 有原始解析数据才显示
    with st.expander("完整解析数据"):  # 折叠面板，默认收起
        st.json(parsed)  # 以 JSON 树的形式展示全部字段（排查问题时很有用）

# ---------------------------------------------------------------------------
# 提交报销单：先「填写报销单」，再把发票 + 报销单一起交给 Agent 做最终审核
#   - 规则：必须基础校验通过，才允许填写报销单
#   - 这里只负责带路，真正的终审在「报销单填写」页完成
# ---------------------------------------------------------------------------
existing_form = get_expense_form_by_invoice(invoice_id)  # 查这张发票是否已经生成过报销单
if existing_form:  # 已经填过了：只给查看入口，避免重复填写
    st.caption(f"本张发票已生成报销单 {existing_form.get('form_no') or ''}（状态：{existing_form.get('status') or '-'}）")  # 灰色提示
    st.caption("如果要重新填写：进入「查看报销单」→ 详情页底部删除这张单（未通过的单据可删），发票会重新变成「未填写」。")  # 指引重填路径
    if st.button("查看报销单", type="primary", use_container_width=True):  # 主色按钮
        st.session_state["expense_form_id"] = existing_form["id"]  # 记录要查看的报销单 id
        st.switch_page("pages/8_报销单详情.py")  # 跳转到报销单详情页（不在侧边栏显示）
else:  # 还没填过：进入报销单填写页
    if final_result == "通过":  # 只有基础校验通过才放行
        if st.button("填写报销单，进入财务终审", type="primary", use_container_width=True):  # 主色按钮
            st.session_state["expense_form_invoice_id"] = invoice_id  # 把当前发票 id 传给填写页
            st.switch_page("pages/7_报销单填写.py")  # 跳转到报销单填写页（不在侧边栏显示）
    else:  # 基础校验没通过：按钮禁用
        st.button("填写报销单，进入财务终审", type="primary", use_container_width=True, disabled=True)  # 置灰
        st.caption("发票初审（基础校验）未通过，无法填写报销单")  # 灰色小字说明原因

if final_result == "通过":  # 发票初审通过
    st.success("发票初审通过，可以填写报销单")  # 绿色提示
elif final_result == "不通过":  # 发票初审不通过
    st.warning("发票初审未通过，暂时无法填写报销单；可以先在「帮助中心 → 我的发票咨询」里问 AI 审核员")  # 黄色提示
else:  # 还没有审核记录
    st.warning("这张发票还没有审核记录，建议先在「发票上传」页完成基础校验")  # 黄色提示



st.divider()  # 分隔线：把“提交”和下面的次要操作分开
btn1, btn2 = st.columns(2)  # 底部两个次要操作：左下载原文件、右从票夹移除
with btn1:  # 左侧
    file_path = resolve_invoice_file_path(detail.get("file_path"))  # 把数据库里存的相对路径还原成真实文件路径
    if file_path is not None:  # 文件确实存在
        st.download_button(  # 下载按钮
            "下载原文件",  # 按钮文字
            data=file_path.read_bytes(),  # 文件内容（读成二进制）
            file_name=detail.get("original_filename") or file_path.name,  # 下载后的文件名
            mime="application/octet-stream",  # 通用二进制类型，浏览器直接下载
            use_container_width=True,  # 按钮撑满整列
        )
    else:  # 文件丢了（例如被手工删除）
        st.caption("原文件不可用")  # 灰色小字提示
with btn2:  # 右侧
    if st.button("从票夹移除", use_container_width=True, type="primary"):  # 主色按钮：把这张发票从票夹删掉
        if discard_folder_invoice(invoice_id):  # 删除成功
            st.session_state.pop("folder_invoice_id", None)  # 清掉会话里记录的发票 id
            st.success("已移除")  # 绿色提示
            st.switch_page("pages/3_我的票夹.py")  # 跳回票夹页
        else:  # 删除失败
            st.error("移除失败")  # 红色提示