# -*- coding: utf-8 -*-
"""财务智能报销系统：应用入口 + 侧边栏导航（多页面路由）+ 全站统一页面风格"""
import sys  # 用于设置Python模块搜索路径
from pathlib import Path  # 用于获取项目根目录

# 把项目根目录加入Python搜索路径，确保从任何目录运行都能找到utils、audit等包
sys.path.insert(0, str(Path(__file__).parent))

import streamlit as st  # 导入 Streamlit，后面所有界面元素都通过 st 调用

from utils.current_user import render_employee_selector  # 轻量多员工：侧边栏身份切换

# ===========================================================================
# 一、页面全局配置（必须放在最前面，且只能设置一次）
# ===========================================================================
st.set_page_config(  # 设置浏览器标签标题、页面布局、侧边栏初始状态
    page_title="财务智能报销系统",  # 浏览器标签页上显示的名字
    layout="wide",  # 宽屏布局：内容占满浏览器宽度（默认为居中窄栏）
    initial_sidebar_state="expanded",  # 侧边栏默认展开，一进来就能看到 5 个功能项
)

# ===========================================================================
# 二、侧边栏样式（CSS）：把功能项字号放大、加圆角、加悬停与选中高亮
#     st.markdown 只是把这段样式“挂”到页面上，页面本身看不到它
# ===========================================================================
st.markdown(  # 注入 HTML/CSS，用于美化侧边栏导航
    """
<style>
    /* ---------- 单个功能项（一行可点击的导航条目） ---------- */
    section[data-testid="stSidebar"] [data-testid="stSidebarNavLink"] {  /* 只作用于侧边栏里的导航链接 */
        padding: 0.6rem 0.75rem !important;  /* 内边距：把可点击区域撑大，更好点中 */
        margin: 0.15rem 0.4rem !important;  /* 外边距：条目之间留出一点缝隙 */
        border-radius: 10px !important;  /* 圆角：悬停/选中时的色块变成圆角按钮 */
        gap: 0.65rem !important;  /* 图标与文字之间的距离 */
        transition: background-color 0.15s ease, color 0.15s ease;  /* 悬停时颜色平滑过渡 */
    }

    /* ---------- 功能项的文字：字号整体放大 ---------- */
    section[data-testid="stSidebar"] [data-testid="stSidebarNavLink"] span {  /* 文字所在的 span */
        font-size: 1.05rem !important;  /* 字号从默认 0.875rem 放大到 1.05rem */
        line-height: 1.65 !important;  /* 行高调大，文字不拥挤 */
        letter-spacing: 0.2px;  /* 字间距略微加宽，看起来更舒展 */
    }

    /* ---------- 功能项的图标：放大并统一成主题蓝 ---------- */
    section[data-testid="stSidebar"] [data-testid="stSidebarNavLink"] span[data-testid="stIconMaterial"] {
        font-size: 1.45rem !important;  /* 图标字号（比文字再大一点，作为视觉锚点） */
        color: #5B8DEF;  /* 图标颜色：与全站主色一致的蓝色 */
    }

    /* ---------- 鼠标悬停某个功能项时：浅蓝色背景提示 ---------- */
    section[data-testid="stSidebar"] [data-testid="stSidebarNavLink"]:hover {
        background-color: rgba(91, 141, 239, 0.10) !important;  /* 10% 透明度的蓝色底 */
    }

    /* ---------- 当前正在浏览的功能项：背景底色更深一点 ---------- */
    section[data-testid="stSidebar"] [data-testid="stSidebarNavLink"][aria-current="page"] {
        background-color: rgba(91, 141, 239, 0.14) !important;  /* 14% 透明度的蓝色底 */
    }

    /* ---------- 当前功能项的文字：加深加粗，一眼看出在哪一页 ---------- */
    section[data-testid="stSidebar"] [data-testid="stSidebarNavLink"][aria-current="page"] span {
        color: #2C5FD6 !important;  /* 更深一档的蓝色文字 */
        font-weight: 600 !important;  /* 半粗体 */
    }
</style>
""",
    unsafe_allow_html=True,  # 必须为 True，否则上面的 CSS 会被当成普通文字显示在页面上
)

# ===========================================================================
# 三、页面内容区统一样式：所有功能页面共用（标题大小、顶部留白、灰线间距）
# ===========================================================================
st.markdown(  # 再注入一段内容区样式，保证 5 个功能页面“长得一样”
    """
<style>
    /* ---------- 1) 内容区容器：把大标题整体向上移，并统一内容宽度 ---------- */
    [data-testid="stMainBlockContainer"] {
        padding-top: 4.4rem !important;  /* 顶部留白：默认 6rem 太空，改成 4.4rem（顶部工具栏高 3.75rem，不会被遮住） */
        max-width: 1120px !important;  /* 内容最大宽度：所有页面统一，读起来更集中 */
        margin-left: auto !important;  /* 限宽后左右自动留白 → 内容水平居中 */
        margin-right: auto !important;  /* 同上 */
    }

    /* ---------- 2) 页面元素之间的默认间距：略微收紧，整体更简洁 ---------- */
    [data-testid="stMainBlockContainer"] [data-testid="stVerticalBlock"] {
        gap: 0.85rem !important;  /* 元素间距：默认 1rem，改成 0.85rem */
    }

    /* ---------- 3) 说明文字：取消 Streamlit 默认的 -1rem 负外边距 ---------- */
    [data-testid="stMainBlockContainer"] [data-testid="stCaptionContainer"] {
        margin-bottom: 0 !important;  /* 否则会和下一个元素“叠”在一起，导致灰线贴太近或重叠 */
    }

    /* =======================================================================
       4) ★★★ 页头间距“总控开关”：想调哪段距离，就改下面这三个数值 ★★★
          位置：本文件 app.py 的这一段 CSS 里。改完保存，首页 + 5 个功能页同时生效
          （每个页面都用 st.container(key="pagehead") 包住标题区，所以共用这一套）
       ======================================================================= */
    .st-key-pagehead {  /* 把三个变量挂在页头容器上，容器里所有元素都能继承到 */
        --ph-gap: 1.4rem;        /* ① 大标题 ↔ 灰色小字 的距离（越大越松，改 0 就是紧贴） */
        --ph-rule-gap: 0.6rem;   /* ② 灰色小字 ↔ 灰色横线 的距离 */
        --ph-body-gap: 0.9rem;   /* ③ 灰色横线 ↔ 下方正文 的距离 */
    }

    /* ---------- 5) 关掉默认间距，避免和 ①②③ 叠加 ---------- */
    .st-key-pagehead[data-testid="stVerticalBlock"],  /* 页头容器本身 */
    .st-key-pagehead [data-testid="stVerticalBlock"] {  /* 页头里“列表格”内部的容器 */
        gap: 0 !important;  /* 归零后，间距只由 ①②③ 控制，指哪打哪 */
    }

    /* ---------- 6) 大标题：字号、字重、颜色、位置全站统一 ---------- */
    .st-key-pagehead h2 {  /* st.header 渲染出来的就是 h2 */
        font-size: 1.75rem !important;  /* 统一字号：解决“我的票夹”标题比别的页面小的问题 */
        font-weight: 700 !important;  /* 统一加粗 */
        color: #0F172A !important;  /* 统一深色（近黑） */
        letter-spacing: 0 !important;  /* 取消默认的负字距，中文更端正 */
        line-height: 1.35 !important;  /* 行高 */
        margin: 0 !important;  /* 标题自身不留边距，① 那段距离交给下面的灰色小字 */
        padding: 0 !important;  /* 去掉默认的上下各 1rem 内边距（这是标题上方空白的主要来源） */
    }

    /* ---------- 7) 标题下的灰色小字 ---------- */
    .st-key-pagehead [data-testid="stCaptionContainer"] {
        color: #64748B !important;  /* 统一的灰蓝色 */
        font-size: 0.9rem !important;  /* 统一的字号 */
        opacity: 1 !important;  /* 关掉默认半透明，颜色更可控 */
        margin: 0 !important;  /* 外边距归零：Streamlit 默认的负外边距会把间距“折叠”掉 */
        padding: var(--ph-gap) 0 0 0 !important;  /* ① 与上面大标题的距离；用 padding 不会被折叠，改多少就是多少 */
    }

    /* ---------- 8) 灰色横线：上下间距由 ②③ 两个变量控制 ---------- */
    .st-key-pagehead hr {
        margin: var(--ph-rule-gap) 0 var(--ph-body-gap) 0 !important;  /* ② 上边距 / ③ 下边距 */
        height: 1px !important;  /* 线条粗细：默认 2px，改细更精致 */
        border: none !important;  /* 去掉浏览器自带的边框 */
        background-color: #E2E8F0 !important;  /* 统一的浅灰线颜色 */
    }
</style>
""",
    unsafe_allow_html=True,  # 允许渲染上面的 CSS
)


# ===========================================================================
# 四、首页内容（写成函数，交给下面的 st.navigation 当作一个“页面”）
# ===========================================================================
def home():  # 定义首页函数；函数名随意，只要在 st.navigation 里引用它即可
    """首页：功能总览"""
    with st.container(key="pagehead"):  # 统一页头容器：标题 + 说明 + 灰线
        st.header("财务智能报销系统")  # 统一字号的大标题
        st.caption("请选择下方功能开始使用")  # 标题下的灰色说明文字
        st.divider()  # 灰色横线（间距由上面的样式统一控制）

    col1, col2, col3, col4 = st.columns(4)  # 把这一行切成 4 列，每列放一张功能卡片

    with col1:  # ---------- 第 1 列：发票上传 ----------
        with st.container(border=True, height=200):  # 带边框、固定高度 200px 的卡片容器
            st.markdown("#### 发票上传")  # 卡片标题
            st.write("上传XML、PDF、图片发票，自动识别，AI预审，发起报销申请")  # 卡片说明文字
            st.page_link("pages/1_发票上传.py", label="进入")  # 站内链接：跳到发票上传页

    with col2:  # ---------- 第 2 列：我的报销单 ----------
        with st.container(border=True, height=200):  # 同款卡片容器
            st.markdown("#### 我的报销单")  # 卡片标题
            st.write("查看提交单据，跟踪审核进度，查看驳回原因")  # 卡片说明文字
            st.page_link("pages/2_我的报销单.py", label="进入")  # 站内链接：跳到我的报销单页

    with col3:  # ---------- 第 3 列：我的票夹 ----------
        with st.container(border=True, height=200):  # 同款卡片容器
            st.markdown("#### 我的票夹")  # 卡片标题
            st.write("归集暂存发票，支持多张发票合并提交报销")  # 卡片说明文字
            st.page_link("pages/3_我的票夹.py", label="进入")  # 站内链接：跳到我的票夹页

    with col4:  # ---------- 第 4 列：帮助中心 ----------
        with st.container(border=True, height=200):  # 同款卡片容器
            st.markdown("#### 帮助中心")  # 卡片标题
            st.write("智能检索报销制度，解答报销相关疑问")  # 卡片说明文字
            st.page_link("pages/4_帮助中心.py", label="进入")  # 站内链接：跳到帮助中心页


# ===========================================================================
# 五、侧边栏功能列表（5 个功能项）
#     - title 决定侧边栏显示的名字，顺序决定显示顺序
#     - default=True 的那一项是打开网站时的默认页面
#     - visibility="hidden" 表示该页存在但不出现在侧边栏（发票详情就是这种）
# ===========================================================================
nav = st.navigation(  # 注册全部页面，并渲染左侧功能列表
    [
        st.Page(home, title="功能选项", icon=":material/apps:", default=True),  # 首页（原来的 app 改名而来）
        st.Page("pages/1_发票上传.py", title="发票上传", icon=":material/upload_file:"),  # 上传与智能预审
        st.Page("pages/2_我的报销单.py", title="我的报销单", icon=":material/receipt_long:"),  # 报销单列表
        st.Page("pages/3_我的票夹.py", title="我的票夹", icon=":material/folder_open:"),  # 发票票夹
        st.Page("pages/4_帮助中心.py", title="帮助中心", icon=":material/help:"),  # 制度问答
        st.Page("pages/6_发票详情.py", title="发票详情", visibility="hidden"),  # 跳转页：不在侧边栏显示
        st.Page("pages/7_报销单填写.py", title="报销单填写", visibility="hidden"),  # 跳转页：从发票详情/票夹进入，不在侧边栏显示
        st.Page("pages/8_报销单详情.py", title="报销单详情", visibility="hidden"),  # 跳转页：从我的报销单进入，不在侧边栏显示
    ]
)

# ===========================================================================
# 六、侧边栏：员工身份切换（轻量多员工，不做登录）
# ===========================================================================
with st.sidebar:
    st.markdown("---")  # 分隔线
    render_employee_selector()  # 渲染员工切换下拉框

nav.run()  # 运行当前选中的页面（用户点哪个功能项，就执行哪个页面脚本）
