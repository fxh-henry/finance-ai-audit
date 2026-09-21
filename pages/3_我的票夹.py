# -*- coding: utf-8 -*-
"""我的票夹：右上角上传新发票入口 + 发票卡片网格（点击卡片看详情）"""
import importlib  # 用于重新加载数据库模块，避免热重载时用到旧代码

import streamlit as st  # 导入 Streamlit

import database.db as _db  # 引入数据库模块（读取票夹里的发票）
from utils.current_user import get_current_employee_id  # 轻量多员工

_db = importlib.reload(_db)  # 强制重新加载，保证拿到最新版本的函数
list_folder_invoices = _db.list_folder_invoices  # 取出“查询票夹发票列表”的函数
list_expense_forms = _db.list_expense_forms  # 取出“查询报销单列表”的函数（判断哪些发票已填单）

st.set_page_config(page_title="我的票夹", layout="wide")  # 页面配置：标签标题 + 宽屏布局

st.markdown(  # 只注入本页专属的卡片样式（页面留白、标题等公共样式由 app.py 统一控制）
    """
<style>
    /* ---------- 发票卡片：只作用于 key 以 invcard_ 开头的容器 ---------- */
    div[data-testid="stVerticalBlock"][class*="st-key-invcard"] {  /* 精确命中外层卡片容器 */
        border: 1px solid #E2E8F0 !important;  /* 1px 浅灰描边 */
        border-radius: 16px !important;  /* 16px 大圆角 */
        background: #FFFFFF !important;  /* 卡片白底 */
        padding: 1rem 1.15rem 0.85rem 1.15rem !important;  /* 卡片内边距：上 右 下 左 */
        min-height: 205px !important;  /* 固定卡片高度：保证每张发票的方框大小完全一致 */
        justify-content: space-between !important;  /* 纵向两端对齐：让“查看详情”按钮始终贴着卡片底部 */
        transition: border-color 0.15s ease, box-shadow 0.15s ease;  /* 悬停时的过渡动画 */
    }
    div[data-testid="stVerticalBlock"][class*="st-key-invcard"]:hover {  /* 鼠标移动到卡片上 */
        border-color: #5B8DEF !important;  /* 描边变成主题蓝 */
        box-shadow: 0 6px 18px rgba(91, 141, 239, 0.12) !important;  /* 增加一层淡蓝色阴影 */
    }

    /* ---------- 卡片里的三类文字 ---------- */
    .card-seller {  /* 销售方名称（卡片第一行） */
        font-size: 1.05rem;  /* 字号略大于正文 */
        font-weight: 600;  /* 半粗体，作为卡片标题 */
        color: #1E293B;  /* 深灰蓝文字 */
        margin: 0 0 0.35rem 0;  /* 只留底部 0.35rem 间距 */
        line-height: 1.35;  /* 行高 */
        white-space: nowrap;  /* 强制单行显示：名称太长换行会把卡片撑高，导致大小不一致 */
        overflow: hidden;  /* 超出卡片宽度的部分隐藏 */
        text-overflow: ellipsis;  /* 被隐藏的部分用省略号表示 */
    }
    .card-amount {  /* 价税合计金额（卡片第二行） */
        font-size: 1.4rem;  /* 最大的字号，视觉重点 */
        font-weight: 700;  /* 粗体 */
        color: #5B8DEF;  /* 主题蓝，突出金额 */
        margin: 0 0 0.7rem 0;  /* 底部留 0.7rem 间距 */
    }
    .card-meta {  /* 发票号码、上传时间等辅助信息 */
        font-size: 0.82rem;  /* 小字号 */
        color: #94A3B8;  /* 浅灰，弱化显示 */
        margin: 0 0 0.55rem 0;  /* 底部留 0.55rem 间距 */
        line-height: 1.6;  /* 两行信息的行高 */
    }
</style>
""",
    unsafe_allow_html=True,  # 允许渲染上面的 CSS
)


def _format_amount(value):  # 工具函数：把金额格式化成 ¥1,234.00
    if value is None or value == "":  # 金额为空（可能是识别失败）
        return "-"  # 显示一个短横线
    try:  # 正常情况
        return f"¥{float(value):,.2f}"  # 转成浮点数并加千分位、保留两位小数
    except (TypeError, ValueError):  # 金额不是数字（例如写成“壹佰元”）
        return str(value)  # 原样显示，至少不报错


def _short_text(text, limit=18):  # 工具函数：过长的销售方名称截断显示
    text = (text or "").strip()  # 去掉首尾空白；None 时当成空字符串
    if not text:  # 没有名称
        return ""  # 返回空字符串，调用处会再兜底显示“未识别销售方”
    if len(text) <= limit:  # 长度没超过限制
        return text  # 原样返回
    return text[: limit - 1] + "…"  # 超出则截断并加省略号


invoices = list_folder_invoices(employee_id=get_current_employee_id())  # 查询当前员工票夹里的发票
form_map = {f["invoice_id"]: f for f in list_expense_forms(employee_id=get_current_employee_id())}  # 发票 id → 报销单

# ---------------------------------------------------------------------------
# 页头：左边标题，右边“上传新的发票”按钮（与其他页面同一套标题样式）
# ---------------------------------------------------------------------------
with st.container(key="pagehead"):  # 统一页头容器：样式由 app.py 统一控制
    head_l, head_r = st.columns([4, 1], vertical_alignment="bottom")  # 左标题、右按钮，两列底部对齐
    with head_l:  # 左侧：标题区
        st.header("我的票夹")  # 统一字号的大标题（原来用 ### 会比其他页面小）
        st.caption(f"共 {len(invoices)} 张发票")  # 灰色小字显示发票总数
    with head_r:  # 右侧：上传入口
        if st.button("＋ 上传新的发票", type="primary", use_container_width=True):  # 主色按钮，撑满整列
            st.switch_page("pages/1_发票上传.py")  # 跳转到「发票上传」页面
    st.divider()  # 灰色横线，间距由全局样式统一控制

# ---------------------------------------------------------------------------
# 卡片网格：每行 3 张卡片
# ---------------------------------------------------------------------------
if not invoices:  # 票夹里一张发票都没有
    st.info("票夹还是空的，点击右上角「上传新的发票」开始添加")  # 蓝色提示条
else:  # 有发票，开始渲染网格
    for row_start in range(0, len(invoices), 3):  # 每次取 3 张，作为一行
        cols = st.columns(3, gap="large")  # 创建 3 列，列间距大一些
        chunk = invoices[row_start : row_start + 3]  # 取出这一行的发票数据
        for col, inv in zip(cols, chunk):  # 把数据和列一一配对
            with col:  # 在当前列里画一张卡片
                seller = _short_text(inv.get("seller_name"), 18) or "未识别销售方"  # 销售方名称（过长截断，空则兜底）
                amount = _format_amount(inv.get("total_amount"))  # 价税合计（格式化）
                inv_no = (inv.get("invoice_number") or "").strip()  # 发票号码（去掉空白）
                no_show = f"…{inv_no[-8:]}" if len(inv_no) > 8 else (inv_no or "无号码")  # 号码太长就只显示后 8 位
                uploaded = inv.get("uploaded_at") or "-"  # 上传时间（为空显示短横线）
                form_tag = "已填写" if form_map.get(inv["id"]) else "未填写"  # 这张发票的报销单填没填

                with st.container(border=True, key=f"invcard_{inv['id']}"):  # 带边框的卡片容器；key 前缀 invcard_ 用于上面的 CSS 定位
                    st.markdown(  # 用 HTML 输出卡片里的三行信息
                        f'<p class="card-seller">{seller}</p>'  # 第 1 行：销售方
                        f'<p class="card-amount">{amount}</p>'  # 第 2 行：金额
                        f'<p class="card-meta">发票号码　{no_show}<br/>上传时间　{uploaded}<br/>报销单　{form_tag}</p>',  # 第 3 行：号码 + 上传时间 + 报销单状态
                        unsafe_allow_html=True,  # 允许渲染上面的 HTML
                    )
                    btn_l, btn_r = st.columns(2)  # 卡片底部并排两个操作按钮
                    with btn_l:  # 左：查看发票详情
                        if st.button("查看详情", key=f"folder_card_{inv['id']}", use_container_width=True):  # 每张卡片按钮的 key 必须唯一
                            st.session_state["folder_invoice_id"] = inv["id"]  # 记录要查看的发票 id
                            st.switch_page("pages/6_发票详情.py")  # 跳转到发票详情页（该页不在侧边栏显示）
                    with btn_r:  # 右：填写 / 查看报销单
                        if form_map.get(inv["id"]):  # 这张发票已经填过报销单
                            if st.button("查看报销单", key=f"folder_form_{inv['id']}", use_container_width=True):  # 按钮 key 唯一
                                st.session_state["expense_form_id"] = form_map[inv["id"]]["id"]  # 记录报销单 id
                                st.switch_page("pages/8_报销单详情.py")  # 跳转到报销单详情页（该页不在侧边栏显示）
                        else:  # 还没填过
                            if st.button("填写报销单", key=f"folder_form_new_{inv['id']}", use_container_width=True):  # 按钮 key 唯一
                                st.session_state["expense_form_invoice_id"] = inv["id"]  # 把发票 id 带给填写页
                                st.switch_page("pages/7_报销单填写.py")  # 跳转到报销单填写页（该页不在侧边栏显示）
