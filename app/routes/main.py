import os
import re
from flask import Blueprint, render_template

bp = Blueprint('main', __name__)

# SOP 文件路径（项目根目录，从 app/routes/main.py 向上 4 级）
_SOP_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
                         'SOP_BOM核对操作指南.md')


def _markdown_to_html(text):
    """极简 Markdown → HTML 转换（仅处理 SOP 用到的语法）"""
    lines = text.split('\n')
    out = []
    in_code = False
    in_table = False

    for line in lines:
        # 代码块
        if line.startswith('```'):
            if in_code:
                out.append('</code></pre>')
                in_code = False
            else:
                out.append('<pre class="bg-slate-900 text-slate-200 p-4 rounded-lg overflow-x-auto text-xs leading-relaxed"><code>')
                in_code = True
            continue
        if in_code:
            out.append(line)
            continue

        # 表格
        if line.startswith('|') and line.strip().endswith('|'):
            cells = [c.strip() for c in line.strip().strip('|').split('|')]
            if all(c.replace('-', '').replace(' ', '') == '' for c in cells if c):
                # 分隔行，跳过
                continue
            tag = 'th' if not in_table else 'td'
            row = ''.join(f'<{tag} class="border border-slate-300 px-3 py-1.5 text-xs">{c}</{tag}>' for c in cells)
            if not in_table:
                out.append('<table class="w-full border-collapse border border-slate-300 mb-4"><thead>')
                out.append(f'<tr class="bg-slate-100">{row}</tr>')
                out.append('</thead><tbody>')
                in_table = True
            else:
                out.append(f'<tr class="even:bg-slate-50">{row}</tr>')
            continue
        elif in_table:
            out.append('</tbody></table>')
            in_table = False

        # 标题
        if line.startswith('### '):
            out.append(f'<h3 class="text-base font-bold text-slate-800 mt-6 mb-2">{line[4:]}</h3>')
            continue
        if line.startswith('## '):
            out.append(f'<h2 class="text-lg font-bold text-slate-900 mt-8 mb-3 pb-1 border-b border-slate-200">{line[3:]}</h2>')
            continue
        if line.startswith('# '):
            out.append(f'<h1 class="text-xl font-bold text-slate-900 mt-6 mb-4">{line[2:]}</h1>')
            continue

        # 引用块
        if line.startswith('> '):
            out.append(f'<blockquote class="border-l-4 border-blue-400 bg-blue-50 pl-4 py-1 my-2 text-xs text-slate-600">{line[2:]}</blockquote>')
            continue

        # 粗体
        line = re.sub(r'\*\*(.+?)\*\*', r'<strong class="font-semibold">\1</strong>', line)

        # 行内代码
        line = re.sub(r'`([^`]+)`', r'<code class="bg-slate-100 text-red-600 px-1 py-0.5 rounded text-xs">\1</code>', line)

        # 空行
        if not line.strip():
            out.append('')
            continue

        # 普通段落
        out.append(f'<p class="text-sm text-slate-700 mb-2 leading-relaxed">{line}</p>')

    if in_code:
        out.append('</code></pre>')
    if in_table:
        out.append('</tbody></table>')

    return '\n'.join(out)


@bp.route('/sop')
def sop_page():
    """操作指南 SOP 页面"""
    if os.path.exists(_SOP_PATH):
        with open(_SOP_PATH, 'r', encoding='utf-8') as f:
            md_content = f.read()
        html_body = _markdown_to_html(md_content)
    else:
        html_body = '<p class="text-red-500">SOP 文件未找到，请联系管理员。</p>'
    return render_template('sop.html', content=html_body)


@bp.route('/')
def index():
    return render_template('index.html')


@bp.route('/bom_list')
def bom_list():
    return render_template('bom_list.html')
