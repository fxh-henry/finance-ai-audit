# -*- coding: utf-8 -*-
"""发票上传：一次可选多张 → 逐张识别 + 基础合规校验（不调用Agent）→ 折叠卡片逐张操作"""
import hashlib  # 用来给上传的每个文件算指纹，判断哪些是新文件
import importlib  # 用来重新加载模块，避免 Streamlit 热重载时用到旧代码
import os  # 用来取文件扩展名（临时文件后缀用）
import tempfile  # 用来生成临时文件（识别接口需要一个磁盘路径）
from datetime import datetime  # 用来记录校验时间

import streamlit as st  # 导入 Streamlit

from pipeline import run_basic_check_pipeline, format_for_user  # 只引入“识别 + 基础校验”流水线（本页不加载 Agent，页面打开更快）
from utils.audit_verdict import extract_verdict  # 引入结论解析工具
from utils.current_user import get_current_employee_id  # 轻量多员工

import database.db as _db  # 引入数据库模块（用于“存入票夹”）

_db = importlib.reload(_db)  # 强制重新加载 db 模块，保证用的是最新代码
save_invoice_to_folder = _db.save_invoice_to_folder  # 取出“保存发票到票夹”的函数备用


# ============================================================================
# 档位定义：把每张发票归到 4 档之一。档位数字越小越需要用户关注，列表里越靠上
#   0 = 识别失败       （系统连字段都没读出来）
#   1 = 基础校验不通过 （12 项里有 error，不能填报销单）
#   2 = 通过但有警告   （没有 error 但有 warning，可以填报销单但要留意）
#   3 = 完全通过
# ============================================================================
TIER_ICON = {0: "⛔", 1: "❌", 2: "⚠️", 3: "✅"}  # 每档的图标，拼在折叠卡片标题最前面
TIER_TEXT = {0: "识别失败", 1: "校验不通过", 2: "通过（有警告）", 3: "通过"}  # 每档的文字说明


# ============================================================================
# 演示样例：评委手边没有发票时，一键载入随仓库分发的样例（与自行上传走同一条流程）
#   样例放在项目根目录的「演示素材」下；该目录未随部署提供时，界面上的按钮自动置灰
# ============================================================================
DEMO_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "演示素材")  # 项目根 / 演示素材
DEMO_FILE_NAMES = [  # 演示样例：两张数电票 XML（其一金额勾稽不符）+ 两张版式 PDF
    "02_数电票-金额勾稽不符.xml",  # 故意做错金额勾稽：落入「校验不通过」，同时演示填单闸门
    "样例-1-数电票原件.xml",
    "样例-2-版式PDF-印刷费.pdf",
    "样例-3-版式PDF-再生资源.pdf",
]


class _DemoFile:  # 伪上传对象
    """把磁盘上的样例文件伪装成上传对象：只要具备 name 与 getvalue()，下游无需区分来源"""

    def __init__(self, name, data):  # 文件名 + 二进制内容
        self.name = name  # 下游用它显示文件名、取后缀
        self._data = data  # 文件字节

    def getvalue(self):  # 与 Streamlit 上传对象保持同一接口
        return self._data  # 返回字节


def _demo_paths():  # 本次部署里实际存在的样例文件
    """项目带了哪些样例就载入哪些；一个都没有时返回空列表（按钮置灰）"""
    return [p for p in (os.path.join(DEMO_DIR, n) for n in DEMO_FILE_NAMES) if os.path.exists(p)]


_DEMO_PATH_LIST = _demo_paths()  # 启动时算一次，按钮与渲染共用


def _tier_of(result):  # 判断一张发票落在哪一档
    """只看基础校验结果，判断这张发票属于哪一档（不涉及 Agent）"""
    if not result or result.get("status") == "failed":  # 没有结果，或流程在识别阶段就失败了
        return 0  # 归到「识别失败」
    summary = result.get("summary") or {}  # 基础校验汇总
    if summary.get("error_count", 0) > 0:  # 有 error 级问题
        return 1  # 归到「校验不通过」
    if summary.get("warning_count", 0) > 0:  # 没有 error，但有 warning
        return 2  # 归到「通过但有警告」
    return 3  # 12 项全部通过


def _widget_key(signature):  # 把长文件指纹压成一个短且稳定的字符串
    """用来拼各个组件的 key（Streamlit 要求同一次运行里组件 key 不能重复）"""
    return hashlib.sha1(signature.encode("utf-8")).hexdigest()[:12]  # 取 SHA1 前 12 位，够用且短


def _card_title(idx, item, tier):  # 拼折叠卡片的标题文字
    """标题 = 图标 + 编号 + 档位 + 销售方（或文件名）+ 金额 + 是否已存入"""
    result = item["result"]  # 这一张的识别 + 校验结果
    parts = [f"{TIER_ICON[tier]}", TIER_TEXT[tier]]  # 开头是图标、档位（去掉编号）
    if result.get("status") == "success":  # 识别成功：给销售方和金额，用户才好认出这是哪张发票
        data = result.get("invoice_data") or {}  # 识别出的字段
        seller = (data.get("销售方名称") or "").strip()  # 销售方名称
        if len(seller) > 22:  # 太长会把标题撑爆
            seller = seller[:22] + "…"  # 截断并加省略号
        parts.append(seller or "未识别到销售方")  # 销售方为空时给个占位说明
        amount = (data.get("价税合计小写") or "").strip()  # 价税合计
        if amount:  # 有金额才显示
            parts.append(f"¥{amount}")  # 加上金额
    else:  # 识别失败：给文件名和失败原因，用户才知道是哪张出了问题
        parts.append(item["name"])  # 原始文件名
        parts.append(str((result.get("error") or {}).get("message") or "未知原因"))  # 失败原因
    if item.get("saved_id"):  # 这张已经存进票夹了
        parts.append("已存入票夹")  # 加个标记
    return " · ".join(parts)  # 用间隔号把各部分串起来


def build_audit_record(result, agent_answer=None):  # 把基础校验结果整理成可 JSON 存储的审核记录
    """
    把基础校验结果整理成可 JSON 存储的审核记录

    result: run_basic_check_pipeline 的返回（check_results 和 summary 在顶层）
    agent_answer: AI 咨询的回答（可选，本次流程一般为 None）
    """
    summary = result.get("summary") or {}  # 基础校验汇总

    if agent_answer:  # 有 AI 回答时，结论以 AI 为准
        final_result = extract_verdict(agent_answer) or summary.get("final_result") or "未审核"
    else:  # 没有 AI 回答：本次只跑到基础校验，结论就是基础校验的结论
        final_result = summary.get("final_result", "未审核")

    return {
        "final_result": final_result,  # 结论：通过 / 不通过 / 未审核
        "agent_answer": agent_answer,  # AI 回答原文（本流程一般为 None）
        "detail_text": format_for_user(result),  # 基础校验明细文本（发票详情页会展示）
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),  # 校验时间
        "stage": "basic_check",  # 标记结论来源：基础校验（不是 Agent 深度审核）
    }


def _notify(success, message):  # 轻提示：显示在页面右上角，几秒后自动消失，不打断操作
    """用轻提示把「存入票夹」的结果告诉用户（成功 / 失败都会说清楚）"""
    st.toast(message, icon="✅" if success else "❌")  # 成功给绿色对勾，失败给红色叉号


def _alive_saved_id(item):  # 内部函数：取这张发票记过的 id，并确认它现在还在票夹里
    """多张上传后，每张卡片各存各的，所以「已存入」标记挂在每张卡片自己身上"""
    saved_id = item.get("saved_id")  # 这张发票在本次会话里存过的话，id 会记在这里
    if not saved_id:  # 没存过（或已清空）
        return None  # 直接返回空，让上层重新走存入流程
    detail = _db.get_invoice_detail(saved_id)  # 去数据库查这条发票记录还在不在
    if detail and detail.get("folder_status") == "in_folder":  # 记录存在，而且还在票夹里
        return saved_id  # 可以放心复用这个 id
    item["saved_id"] = None  # 记录没了或已被移出票夹：清掉这张卡片上的过期标记
    return None  # 返回空，让上层重新存入


def _find_in_folder(invoice_number):  # 内部函数：按发票号码在当前员工的票夹里找这张发票
    """返回该发票号码在当前员工票夹中的发票 id；没找到就返回 None"""
    if not invoice_number:  # 号码为空时不做匹配
        return None  # 避免误命中票夹里其他没识别出号码的发票
    for inv in _db.list_folder_invoices(employee_id=get_current_employee_id()):  # 只查当前员工的票夹
        if (inv.get("invoice_number") or "").strip() == invoice_number:  # 号码完全一致
            return inv["id"]  # 返回它在库里的 id
    return None  # 转完一圈没找到


def _save_to_folder(item, quiet=False):  # 内部函数：把「这一张」发票存进票夹
    """把当前卡片对应的发票存进票夹。

    返回：成功 → 发票 id；失败 → None（失败时用轻提示说明原因）
    quiet=True 时不提示，用于「填写报销单」前的静默自动存入和批量存入"""
    saved_id = _alive_saved_id(item)  # 先看这张卡片是否已经存过（并确认它还真的在票夹里）
    if saved_id:  # 已经存过
        if not quiet:  # 需要提示时才提示
            _notify(True, "该发票已在票夹中，无需重复存入")  # 轻提示重复存入
        return saved_id  # 直接复用已有 id，不再写库

    result = item["result"]  # 这张卡片的识别 + 校验结果
    invoice_data = result.get("invoice_data") or {}  # 识别出的发票字段（中文键）
    file_bytes = item.get("bytes")  # 原始文件字节（解析时就缓存好了）
    file_name = item.get("name")  # 原始文件名
    if not file_bytes:  # 找不到原始文件（例如用户刚清空过缓存）
        if not quiet:  # 需要提示时才提示
            _notify(False, "存入不成功：找不到原始文件，请重新上传")  # 轻提示失败原因
        return None  # 没法存，返回空

    save_result = save_invoice_to_folder(  # 调用数据库模块的保存函数，把文件 + 解析结果 + 校验结果一起写库
        file_bytes=file_bytes,  # 原始文件字节
        original_filename=file_name or "发票",  # 原始文件名
        invoice_data=invoice_data,  # 识别出来的发票字段（中文键）
        parse_status="success",  # 标记为识别成功
        audit_result=build_audit_record(result),  # 基础校验结果一并写库，发票详情页要用
        employee_id=get_current_employee_id(),  # 轻量多员工：用当前切换的身份
    )

    if save_result.get("ok"):  # 写库成功
        item["saved_id"] = save_result["id"]  # 记下发票 id，后面填写报销单直接复用
        if not quiet:  # 需要提示时才提示
            _notify(True, "存入成功")  # 轻提示「存入成功」
        return save_result["id"]  # 把 id 返回给调用方

    number = (invoice_data.get("发票号码") or "").strip()  # 写库失败：先拿到这张发票的号码备用
    existing_id = _find_in_folder(number)  # 去当前员工的票夹里找找，是不是早前就已经存过这张发票了
    if not existing_id:  # 当前员工票夹里没有 → 直接显示数据库返回的错误原因
        if not quiet:  # 需要提示时才提示
            _notify(False, "存入不成功：" + str(save_result.get("error") or "未知错误"))  # 轻提示失败原因
        return None  # 返回空表示失败

    item["saved_id"] = existing_id  # 票夹里已经有了：直接复用它的 id
    if not quiet:  # 需要提示时才提示
        _notify(True, "存入成功：该发票已存在于票夹中")  # 轻提示已经在票夹里了
    return existing_id  # 把已有发票的 id 返回给调用方


# ============================================================================
# 页面主体
# ============================================================================
st.set_page_config(page_title="发票上传", layout="wide")  # 设置浏览器标签标题和宽屏布局

with st.container(key="pagehead"):  # 统一页头容器（样式由 app.py 统一控制，其他页面也一样）
    st.header("发票上传")  # 统一字号的大标题
    st.caption("支持一次选择多张发票，自动逐张识别并执行基础合规校验")  # 标题下的灰色说明
    st.divider()  # 灰色横线，与标题、正文的间距已统一收紧

# ---------- 会话状态初始化 ----------
if "batch" not in st.session_state:  # batch 用来缓存本次会话里所有已解析的发票
    st.session_state["batch"] = {}  # 结构：{文件指纹: {name, bytes, result, saved_id}}
if "uploader_version" not in st.session_state:  # 上传控件的版本号
    st.session_state["uploader_version"] = 0  # 递增它就能把上传控件整个换掉，已选文件会被真正清空

uploaded_files = st.file_uploader(  # 文件上传控件：开了多选后返回一个文件列表
    "选择发票文件，可一次选多张；支持XML、PDF、PNG、JPG格式",  # 控件上方的提示文字
    type=["xml", "pdf", "png", "jpg", "jpeg"],  # 只允许这些后缀，其他文件点不上
    accept_multiple_files=True,  # 关键改动：允许多选
    key=f"uploader_{st.session_state['uploader_version']}",  # 用版本号做 key，点「清空」时递增即可真正清掉已选文件
)

uploaded_files = uploaded_files or []  # 没选文件时可能是 None，统一成空列表方便后面处理

# ---------- 演示样例入口：评委没有发票也能体验 ----------
if "demo_loaded" not in st.session_state:  # 是否已载入演示样例
    st.session_state["demo_loaded"] = False  # 默认未载入
_dc1, _dc2 = st.columns([1.6, 4.4], vertical_alignment="center")  # 左：按钮；右：说明
with _dc1:  # 一键载入
    if st.button("载入演示样例", use_container_width=True, key="load_demo", disabled=not _DEMO_PATH_LIST):  # 样例文件缺失时置灰
        st.session_state["demo_loaded"] = True  # 标记已载入，本次运行随即进入解析流程
with _dc2:  # 说明文字
    if _DEMO_PATH_LIST:  # 样例可用
        st.caption(f"手边没有发票也能体验：点击后载入 {len(_DEMO_PATH_LIST)} 张随仓库分发的样例（数电票 XML 两张、版式 PDF 两张，其中一张金额勾稽不符），与自行上传走同一条识别与校验流程。")
    else:  # 样例没随部署提供
        st.caption("本次部署未包含演示样例文件，请自行上传发票体验。")

# ---------- 给当前上传控件里的每个文件算指纹 ----------
current = {}  # {指纹: (文件对象, 文件字节)}；dict 保持插入顺序，正好等于用户在上传控件里看到的顺序
for _f in uploaded_files:  # 逐个文件
    _bytes = _f.getvalue()  # 读成二进制（写临时文件和存票夹都要用）
    _sig = f"{_f.name}|{len(_bytes)}|{hashlib.sha256(_bytes).hexdigest()}"  # 文件名 + 大小 + 内容哈希
    current[_sig] = (_f, _bytes)  # 记下来

# ---------- 已载入的演示样例：与上传文件同等对待 ----------
if st.session_state.get("demo_loaded"):  # 用户点过「载入演示样例」
    for _dp in _DEMO_PATH_LIST:  # 逐个样例文件
        _dname = os.path.basename(_dp)  # 样例文件名
        with open(_dp, "rb") as _fh:  # 从磁盘读取（样例随仓库部署，本地与线上路径一致）
            _dbytes = _fh.read()  # 二进制内容
        _dsig = f"{_dname}|{len(_dbytes)}|{hashlib.sha256(_dbytes).hexdigest()}"  # 与上传文件同一套指纹算法
        current[_dsig] = (_DemoFile(_dname, _dbytes), _dbytes)  # 并入待解析集合，后续流程完全复用

batch = st.session_state["batch"]  # 取出本次会话的解析缓存

# ---------- 第1步：上传控件里已经删掉的文件，缓存里也要删掉 ----------
for _sig in list(batch.keys()):  # 遍历缓存的 key（用 list 包一层，避免遍历时改动字典）
    if _sig not in current:  # 这个指纹已经不在当前上传列表里了
        batch.pop(_sig)  # 清掉它的解析结果，避免页面还显示已经不存在的文件

# ---------- 第2步：只解析新增的文件（增量解析，已解析过的直接复用，不重跑） ----------
new_sigs = [s for s in current if s not in batch]  # 找出还没解析过的文件
if new_sigs:  # 有新文件才解析
    _bar = st.progress(0.0, text="准备解析…")  # 进度条：多张时用户需要知道进行到哪了
    for _i, _sig in enumerate(new_sigs, 1):  # 逐张解析
        _f, _bytes = current[_sig]  # 取出文件对象和文件字节
        _bar.progress((_i - 1) / len(new_sigs), text=f"正在解析 {_i}/{len(new_sigs)}：{_f.name}")  # 更新进度
        with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(_f.name)[1]) as _tmp:  # 建临时文件
            _tmp.write(_bytes)  # 把上传内容写进临时文件
            _tmp_path = _tmp.name  # 记下临时文件路径，交给识别器使用
        try:  # 单张解析失败不能连累其他张
            _result = run_basic_check_pipeline(_tmp_path)  # 识别发票 + 12 项基础校验（到此为止，不调用 Agent）
        except Exception as _e:  # 兜底：出现意外异常时，也包成标准的失败结果
            _result = {"status": "failed", "stage": "recognize", "error": {"code": "UNEXPECTED", "message": str(_e)}}
        batch[_sig] = {"name": _f.name, "bytes": _bytes, "result": _result, "saved_id": None}  # 存进缓存
    _bar.progress(1.0, text="解析完成")  # 进度条走满
    _bar.empty()  # 然后把进度条收掉，不留在页面上

# ---------- 第3步：渲染 ----------
if not current:  # 一张文件都没有
    st.info("还没有选择文件。可上传发票，或点上方「载入演示样例」一键载入随仓库分发的样例，系统会自动识别并做基础合规校验。")  # 蓝色引导条
else:  # 有文件：先渲染汇总行，再渲染卡片列表
    _counts = {0: 0, 1: 0, 2: 0, 3: 0}  # 四个档位各有多少张
    for _sig in current:  # 逐张统计
        _counts[_tier_of(batch[_sig]["result"])] += 1  # 落到对应档位

    _sum_parts = [f"{TIER_ICON[t]} {_counts[t]} 张{TIER_TEXT[t]}" for t in (0, 1, 2, 3) if _counts[t]]  # 只列出数量不为 0 的档
    _saveable = sum(1 for _sig in current if batch[_sig]["result"].get("status") == "success")  # 识别成功、能存入票夹的张数

    _c1, _c2, _c3 = st.columns([4, 1.5, 0.7], vertical_alignment="center")  # 左：汇总文字；中：批量存入；右：清空
    with _c1:  # 左侧：汇总文字
        st.write(f"共 {len(current)} 张：" + " · ".join(_sum_parts))  # 例如「共 5 张：⛔ 1 张识别失败 · ✅ 4 张通过」
    with _c2:  # 中间：批量存入票夹
        if st.button(f"全部存入票夹（{_saveable} 张）", use_container_width=True, disabled=_saveable == 0, key="save_all"):
            _ok = _fail = _skip = 0  # 分别统计成功 / 失败 / 跳过
            for _sig in current:  # 逐张存
                _item = batch[_sig]  # 取出这一张
                if _item["result"].get("status") != "success":  # 识别失败的没有数据可存
                    _skip += 1  # 计入跳过
                    continue  # 处理下一张
                if _save_to_folder(_item, quiet=True):  # 静默存入，结果最后由一句轻提示统一说明
                    _ok += 1  # 成功
                else:  # 存入失败
                    _fail += 1  # 失败
            _msg = f"成功存入 {_ok} 张"  # 提示文字开头
            if _fail:  # 有失败
                _msg += f"，{_fail} 张失败"  # 补上失败数
            if _skip:  # 有跳过
                _msg += f"，{_skip} 张因识别失败未存入"  # 说明跳过原因
            _notify(_fail == 0, _msg)  # 一次轻提示把结果说清楚
    with _c3:  # 右侧：清空
        if st.button("清空", use_container_width=True, key="clear_all"):  # 点它清掉全部已上传发票
            st.session_state["batch"] = {}  # 清掉解析缓存
            st.session_state["demo_loaded"] = False  # 演示样例也一并清掉
            st.session_state["uploader_version"] += 1  # 换掉上传控件的 key，让已选文件真正被清空
            st.rerun()  # 立刻重跑一次，页面回到「还没有选择文件」的状态

    _pos = {s: i for i, s in enumerate(current)}  # 每张发票在上传控件里的原始位置（用于显示编号和稳定排序）
    _ordered = sorted(current.keys(), key=lambda s: (_tier_of(batch[s]["result"]), _pos[s]))  # 按档位排序：失败和不通过的置顶，同档内保持上传顺序

    for _sig in _ordered:  # 逐张渲染折叠卡片
        _item = batch[_sig]  # 这一张的缓存
        _result = _item["result"]  # 它的识别 + 校验结果
        _tier = _tier_of(_result)  # 它的档位
        _k = _widget_key(_sig)  # 组件 key 的后缀
        _idx = _pos[_sig] + 1  # 显示的编号，跟上传控件里的顺序一致

        with st.expander(_card_title(_idx, _item, _tier), expanded=(_tier <= 1), key=f"card_{_k}"):  # 折叠卡片：失败和不通过的默认展开
            st.caption(f"文件名：{_item['name']}")  # 灰色小字标出原始文件名，方便对上上传列表

            # ---------- ① 需要你处理：把问题摆在最前面 ----------
            if _result.get("status") != "success":  # 识别阶段就失败了
                _err = _result.get("error") or {}  # 错误信息
                st.error(f"识别失败：{_err.get('message', '未知原因')}")  # 红色错误框
                st.caption(f"失败阶段：{_result.get('stage', '未知')}")  # 灰色小字说明卡在哪一步
            else:  # 识别成功：展示基础校验的问题
                _checks = _result.get("check_results") or []  # 12 项校验的逐条结果
                _errors = [c for c in _checks if not c["pass"] and c.get("level") != "warning"]  # 不通过项（error 级）
                _warns = [c for c in _checks if not c["pass"] and c.get("level") == "warning"]  # 警告项（warning 级）
                if not _errors and not _warns:  # 12 项全过
                    st.success(f"基础校验通过：共 {len(_checks)} 项检查全部通过")  # 绿色提示
                    for _c in _checks:
                        st.markdown(f"✅ {_c['check_name']}：{_c['message']}")
                for _c in _errors:  # 逐条列出不通过项
                    st.error(f"{_c['check_name']}：{_c['message']}")  # 红色错误框
                    if _c.get("expected") and _c.get("actual"):  # 有期望值和实际值时补一行对照
                        st.caption(f"应为：{_c['expected']}　实际：{_c['actual']}")  # 灰色小字
                for _c in _warns:  # 逐条列出警告项
                    st.warning(f"{_c['check_name']}：{_c['message']}")  # 黄色警告框

            # ---------- ② 发票识别信息 ----------
            if _result.get("status") == "success":  # 识别成功才有识别信息可看
                _data = _result["invoice_data"]  # 识别出的发票字段
                st.markdown("**发票识别信息**")  # 加粗小标题
                _col_l, _col_r = st.columns(2)  # 左右两列，信息更紧凑
                with _col_l:  # 左列：发票自身信息
                    st.write("发票类型：", _data.get("发票类型", ""))  # 显示发票类型
                    st.write("发票号码：", _data.get("发票号码", ""))  # 显示发票号码
                    st.write("开票日期：", _data.get("开票日期", ""))  # 显示开票日期
                with _col_r:  # 右列：双方与金额
                    st.write("购买方：", _data.get("购买方名称", ""))  # 显示购买方名称
                    st.write("销售方：", _data.get("销售方名称", ""))  # 显示销售方名称
                    st.write("价税合计：", _data.get("价税合计小写", ""))  # 显示价税合计金额

                if _data.get("明细列表"):  # 如果解析出了商品明细
                    st.markdown("**商品明细**")  # 加粗小标题
                    st.dataframe(_data["明细列表"])  # 用表格展示明细行

                if st.toggle("查看全部校验明细", key=f"detail_{_k}"):  # 默认收起，展开才渲染（也省一点性能）
                    st.code(format_for_user(_result), language=None)  # 等宽文本块，保持原有排版
                if st.toggle("查看识别数据（JSON）", key=f"json_{_k}"):  # 默认收起
                    st.json(_data)  # 用 JSON 树展示识别出的所有字段
                    st.caption("以上内容即存入数据库 parsed_json 字段的原始数据，供核对解析字段是否完整。")  # 灰色说明

                # ---------- ③ 逐张的操作入口 ----------
                st.divider()  # 分隔线：把信息区和操作区隔开
                _passed = (_result.get("summary") or {}).get("error_count", 0) == 0  # 基础校验是否通过（warning 不影响）
                _op1, _op2 = st.columns(2)  # 第一行两个操作
                _op3, _op4 = st.columns(2)  # 第二行两个操作
                with _op1:  # 操作1：咨询AI审核员（带着这张发票的信息 + 基础校验结果跳到帮助中心）
                    if st.button("咨询AI审核员", use_container_width=True, key=f"consult_{_k}"):  # 点击后跳转
                        st.session_state["pending_consult"] = {  # 把本次要咨询的材料打包存好
                            "invoice_data": _data,  # 发票识别结果（中文键）
                            "basic_check_text": format_for_user(_result),  # 基础校验结果明细文本
                            "final_result": (_result.get("summary") or {}).get("final_result", "未审核"),  # 基础校验结论
                            "invoice_number": _data.get("发票号码", ""),  # 发票号码（帮助中心顶部展示用）
                            "seller_name": _data.get("销售方名称", ""),  # 销售方（展示用）
                            "total_amount": _data.get("价税合计小写", ""),  # 金额（展示用）
                        }
                        st.session_state["pending_consult_asked"] = False  # 让帮助中心重新自动发起第一问
                        st.session_state["pending_consult_history"] = []  # 清空上一轮咨询对话
                        st.session_state["pending_consult_agent"] = None  # 强制重新创建 Agent（绑定新发票）
                        st.switch_page("pages/4_帮助中心.py")  # 跳转到帮助中心
                with _op2:  # 操作2：存入票夹（只存这一张）
                    if st.button("存入票夹", use_container_width=True, key=f"save_{_k}"):  # 点击后存库
                        _save_to_folder(_item)  # 成功 / 失败都由函数内部用轻提示告诉用户
                with _op3:  # 操作3：填写报销单（必须基础校验通过；进入前自动存入票夹）
                    if st.button("填写报销单", type="primary", use_container_width=True, disabled=not _passed, key=f"form_{_k}"):  # 未通过时按钮禁用
                        _invoice_id = _save_to_folder(_item, quiet=True)  # 先静默存入票夹，拿到发票 id
                        if _invoice_id:  # 存入成功才跳转
                            st.session_state["expense_form_invoice_id"] = _invoice_id  # 把发票 id 传给报销单填写页
                            st.session_state["expense_form_flash"] = "发票已自动存入票夹"  # 到填写页再弹一次提示
                            st.switch_page("pages/7_报销单填写.py")  # 跳转到报销单填写页
                    if not _passed:  # 按钮被禁用时说明原因
                        st.caption("基础校验未通过，无法填写报销单")  # 灰色小字
                with _op4:  # 操作4：联系人工财务（暂未实现）
                    st.button("联系人工财务", use_container_width=True, disabled=True, key=f"human_{_k}")  # 置灰占位
                    st.caption("功能开发中，暂未开放")  # 灰色小字说明
