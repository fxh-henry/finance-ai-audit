# -*- coding: utf-8 -*-
"""把应用方案 Markdown 转成可打印的 HTML。封面文字从「封面信息.txt」读取，改完无需改代码。"""
import sys, io, os, re
import markdown

md_path  = sys.argv[1]
out_html = sys.argv[2]
cover_txt = sys.argv[3] if len(sys.argv) > 3 else ""

info = {}
if cover_txt and os.path.exists(cover_txt):
    for line in io.open(cover_txt, encoding="utf-8-sig"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        info[k.strip()] = v.strip()

def g(key, default=""):
    return info.get(key) or default

lines, body, skipped = io.open(md_path, encoding="utf-8").read().split("\n"), [], False
for ln in lines:
    if not skipped:
        if ln.startswith("# "):
            skipped = True
            continue
        if ln.strip() == "" and not body:
            continue
    if ln.startswith(">") and not body:  # 正文开始前的引用行（副标题）不渲染成引用块
        continue
    body.append(ln)
body_md = "[TOC]\n\n" + "\n".join(body).lstrip("\n")

html_body = markdown.markdown(
    body_md,
    extensions=["extra", "tables", "toc", "fenced_code", "sane_lists", "attr_list", "md_in_html"],
    extension_configs={"toc": {"title": "目录", "toc_depth": "1-3"}},
)

# 中间 HTML 会被写到系统临时目录，相对路径会解析到临时目录导致图片全部丢失，
# 因此这里统一把相对路径改写成基于 md 文件所在目录的绝对 file:/// 路径。
_md_dir = os.path.dirname(os.path.abspath(md_path)).replace("\\", "/")


def _to_abs_img(match):  # 逐张图处理
    src = match.group("src")  # 原始路径
    if re.match(r"(?i)^(https?:|data:|file:)", src):  # 网络图 / 内嵌图 / 已是绝对路径
        return match.group(0)  # 原样保留
    return '<img %ssrc="file:///%s/%s"' % (match.group("pre"), _md_dir, src.lstrip("/"))


html_body = re.sub(r'<img (?P<pre>[^>]*?)src="(?P<src>[^"]+)"', _to_abs_img, html_body)

CSS = """
@page { size: A4; margin: 13mm 14mm 15mm 14mm; }
* { box-sizing: border-box; }
body { font-family: "Microsoft YaHei UI","Microsoft YaHei","PingFang SC","SimSun",sans-serif;
  font-size: 10pt; line-height: 1.75; color: #1a1a1a; margin: 0;
  -webkit-print-color-adjust: exact; print-color-adjust: exact; }
.cover { height: 245mm; display: flex; flex-direction: column; justify-content: center;
         align-items: center; text-align: center; page-break-after: always; }
.cover .badge { font-size: 11pt; letter-spacing: 2px; color: #4a6fa5; margin-bottom: 14mm; }
.cover h1 { font-size: 27pt; color: #14315c; border: none; margin: 0 0 6mm 0; padding: 0;
            line-height: 1.4; page-break-before: avoid; }
.cover .sub { font-size: 13pt; color: #4a6fa5; margin-bottom: 16mm; }
.cover .meta { font-size: 11pt; color: #333; line-height: 2.2; }
.cover .meta b { color: #14315c; }
h1 { font-size: 17pt; color: #14315c; border-bottom: 2.5px solid #14315c;
     padding-bottom: 3mm; margin: 0 0 6mm 0; page-break-before: always; page-break-after: avoid; }
h2 { font-size: 13.5pt; color: #1d4b8f; border-bottom: 1px solid #c9d7ea;
     padding-bottom: 1.4mm; margin: 4mm 0 2.8mm 0; page-break-after: avoid; }
h3 { font-size: 11.5pt; color: #24507f; margin: 4mm 0 2mm 0; page-break-after: avoid; }
p { margin: 2.5mm 0; }
ul, ol { margin: 2.5mm 0; padding-left: 7mm; }
li { margin: 1.2mm 0; }
table { border-collapse: collapse; width: 100%; margin: 3.5mm 0; font-size: 8.8pt; page-break-inside: auto; }
th { background: #eaf1fa; color: #14315c; font-weight: 600; text-align: left;
     border: 1px solid #c3d3e8; padding: 1.5mm 1.9mm; }
td { border: 1px solid #d5dee9; padding: 1.5mm 1.9mm; vertical-align: top; }
tbody tr:nth-child(even) { background: #f8fafd; }
tr { page-break-inside: avoid; }
code { font-family: Consolas,"Courier New",monospace; font-size: 9pt;
       background: #f2f5f9; padding: 0.3mm 1mm; border-radius: 2px; }
pre { background: #f6f8fb; border: 1px solid #dde5ef; border-left: 3px solid #7aa2d4;
      padding: 3mm 3.5mm; margin: 3.5mm 0; page-break-inside: avoid; }
pre code { background: none; padding: 0; font-size: 8.0pt; line-height: 1.45; }
blockquote { margin: 3.5mm 0; padding: 2.5mm 4mm; background: #f7f9fc;
             border-left: 3px solid #7aa2d4; color: #33475e; }
blockquote p { margin: 1.2mm 0; }
hr { border: none; border-top: 1px solid #d5dee9; margin: 6mm 0; }
a { color: #1d4b8f; text-decoration: none; }
img { max-width: 85%; height: auto; page-break-inside: avoid;
      display: block; margin: 3mm auto 1mm auto; }
p > em:only-child { display: block; text-align: center; color: #64748b;
      font-size: 9pt; font-style: normal; margin: 0.5mm 0 3mm 0; }
h4 { font-size: 10.5pt; color: #24507f; margin: 4.5mm 0 2mm 0; page-break-after: avoid; }
.toc { background: #f8fafd; border: 1px solid #dde5ef; padding: 4mm 6mm; margin-bottom: 6mm; }
.toc ul { list-style: none; padding-left: 4mm; }
.toc > ul { padding-left: 0; }
.toc > ul { column-count: 2; column-gap: 8mm; }  /* 目录双栏：省版面 */
.toc li { break-inside: avoid; }  /* 不让同一章的条目被拆到两栏 */
.toc li { margin: 1mm 0; font-size: 9.5pt; }
strong { color: #0f2a4d; }
"""

COVER = """
<div class="cover">
  <div class="badge">{badge}</div>
  <h1>{title}</h1>
  <div class="sub">{subtitle}</div>
  <div class="meta">
    <div><b>作品名称：</b>{work}</div>
    <div><b>参赛赛道：</b>{track}</div>
    <div><b>参赛团队：</b>{team}</div>
    <div><b>团队成员：</b>{members}</div>
    <div><b>指导教师：</b>{teacher}</div>
    <div><b>提交日期：</b>{date}</div>
  </div>
</div>
""".format(
    badge=g("赛事名称", "2026 iCAN 大学生创新创业大赛 · AI 应用创新挑战赛"),
    title=g("作品名称", "数电票智能预审 Agent"),
    subtitle=g("副标题", "高校与中小企业财务报销智能审核系统 · 软件赛道"),
    work=g("作品全称", "数电票智能预审 Agent（财务报销智能审核系统）"),
    track=g("参赛赛道", "软件赛道（Agent 智能体 + 网页应用 + 企业服务）"),
    team=g("参赛团队", "＿＿＿＿＿＿＿＿＿＿"),
    members=g("团队成员", "＿＿＿＿＿＿＿＿＿＿"),
    teacher=g("指导教师", "＿＿＿＿＿＿＿＿＿＿"),
    date=g("提交日期", "2026 年 9 月"),
)

html = ('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<title>数电票智能预审 Agent · 应用方案</title><style>' + CSS + '</style></head><body>'
        + COVER + html_body + '</body></html>')
io.open(out_html, "w", encoding="utf-8").write(html)
print("HTML 已生成：", out_html)