import os
import re
import base64
import subprocess

# Define file paths
workspace_dir = os.path.dirname(os.path.abspath(__file__))
arch_md_path = "/Users/sonneygeorge/.gemini/antigravity-ide/brain/eebc334b-f6c4-4c8f-b9b3-020528b29284/architecture_and_workflow.md"
img_arch_path = os.path.join(workspace_dir, "slides", "high_level_architecture.png")
img_workflow_path = os.path.join(workspace_dir, "slides", "backend_graph_workflow.png")
img_loop_path = os.path.join(workspace_dir, "slides", "system_signal_flow_loop.png")

output_html = os.path.join(workspace_dir, "architecture_and_workflow.html")
output_docx = os.path.join(workspace_dir, "architecture_and_workflow.docx")

# Verify local slides exist or fallback to brain paths
if not os.path.exists(img_arch_path):
    img_arch_path = "/Users/sonneygeorge/.gemini/antigravity-ide/brain/eebc334b-f6c4-4c8f-b9b3-020528b29284/high_level_architecture_1784291350898.png"
if not os.path.exists(img_workflow_path):
    img_workflow_path = "/Users/sonneygeorge/.gemini/antigravity-ide/brain/eebc334b-f6c4-4c8f-b9b3-020528b29284/backend_graph_workflow_1783230599635.png"
if not os.path.exists(img_loop_path):
    img_loop_path = "/Users/sonneygeorge/.gemini/antigravity-ide/brain/eebc334b-f6c4-4c8f-b9b3-020528b29284/system_signal_flow_loop_v2_1784294766996.png"

print("Reading and encoding images to Base64...")
with open(img_arch_path, "rb") as f:
    img_arch_b64 = base64.b64encode(f.read()).decode("utf-8")

with open(img_workflow_path, "rb") as f:
    img_workflow_b64 = base64.b64encode(f.read()).decode("utf-8")

with open(img_loop_path, "rb") as f:
    img_loop_b64 = base64.b64encode(f.read()).decode("utf-8")

print("Reading Markdown content...")
with open(arch_md_path, "r", encoding="utf-8") as f:
    md_content = f.read()

# Simple Markdown to HTML converter
def md_to_html(md):
    # Split by code blocks first
    parts = re.split(r'(```[\s\S]*?```)', md)
    html_parts = []
    
    for part in parts:
        if part.startswith('```'):
            # Code block
            lines = part.split('\n')
            lang = lines[0].replace('```', '').strip()
            code_content = '\n'.join(lines[1:-1])
            html_parts.append(f"<pre><code class='language-{lang}'>{re.sub('<', '&lt;', code_content)}</code></pre>")
        else:
            # Inline text parsing
            text = part
            # Headers
            text = re.sub(r'^# (.*)$', r'<h1>\1</h1>', text, flags=re.MULTILINE)
            text = re.sub(r'^## (.*)$', r'<h2>\1</h2>', text, flags=re.MULTILINE)
            text = re.sub(r'^### (.*)$', r'<h3>\1</h3>', text, flags=re.MULTILINE)
            # Bold
            text = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', text)
            # Inline code
            text = re.sub(r'`(.*?)`', r'<code>\1</code>', text)
            # Horizontal rule
            text = re.sub(r'^---$', r'<hr>', text, flags=re.MULTILINE)
            # Bullet lists
            def replace_list(match):
                items = match.group(0).strip().split('\n')
                li_html = ''.join(f"<li>{item.replace('* ', '').strip()}</li>" for item in items)
                return f"<ul>{li_html}</ul>"
            text = re.sub(r'(?:^\* .*$\n?)+', replace_list, text, flags=re.MULTILINE)
            # Ordered lists
            def replace_ord_list(match):
                items = match.group(0).strip().split('\n')
                li_html = ''.join(f"<li>{re.sub(r'^\d+\. ', '', item).strip()}</li>" for item in items)
                return f"<ol>{li_html}</ol>"
            text = re.sub(r'(?:^\d+\. .*$\n?)+', replace_ord_list, text, flags=re.MULTILINE)
            
            # Paragraphs
            paragraphs = text.split('\n\n')
            p_html = []
            for p in paragraphs:
                p = p.strip()
                if p and not p.startswith('<h') and not p.startswith('<u') and not p.startswith('<o') and not p.startswith('<h') and not p.startswith('<h') and not p.startswith('<d') and not p.startswith('<h') and not p.startswith('<l'):
                    p_html.append(f"<p>{p.replace('\n', '<br>')}</p>")
                else:
                    p_html.append(p)
            text = '\n'.join(p_html)
            html_parts.append(text)
            
    html_body = '\n'.join(html_parts)
    
    # Replace markdown images with Base64 embedded tags
    html_body = re.sub(
        r'!\[High Level System Architecture Diagram\]\(.*?\)',
        f'<div class="img-container"><img src="data:image/png;base64,{img_arch_b64}" alt="High Level System Architecture" /></div>',
        html_body
    )
    
    html_body = re.sub(
        r'!\[Backend ADK Graph Workflow Diagram\]\(.*?\)',
        f'<div class="img-container"><img src="data:image/png;base64,{img_workflow_b64}" alt="Backend ADK Graph Workflow" /></div>',
        html_body
    )
    
    html_body = re.sub(
        r'!\[System Signal Flow Loop Diagram\]\(.*?\)',
        f'<div class="img-container"><img src="data:image/png;base64,{img_loop_b64}" alt="System Signal Flow Loop Diagram" /></div>',
        html_body
    )
    
    return html_body

body_content = md_to_html(md_content)

html_template = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>SignalSense AI: Architecture & Backend Workflow</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 900px;
            margin: 40px auto;
            padding: 0 20px;
        }}
        h1 {{
            border-bottom: 2px solid #eaecef;
            padding-bottom: 0.3em;
            color: #1a0dab;
        }}
        h2 {{
            border-bottom: 1px solid #eaecef;
            padding-bottom: 0.3em;
            color: #24292e;
            margin-top: 24px;
        }}
        h3 {{
            color: #24292e;
        }}
        p, li {{
            font-size: 15px;
            color: #24292e;
        }}
        code {{
            font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
            background-color: rgba(27,31,35,0.05);
            padding: 0.2em 0.4em;
            border-radius: 3px;
            font-size: 85%;
        }}
        pre {{
            background-color: #f6f8fa;
            border-radius: 3px;
            padding: 16px;
            overflow: auto;
        }}
        pre code {{
            background-color: transparent;
            padding: 0;
            font-size: 100%;
        }}
        .img-container {{
            text-align: center;
            margin: 30px 0;
            padding: 10px;
            background: #0d1117;
            border-radius: 8px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        }}
        .img-container img {{
            max-width: 100%;
            height: auto;
            border-radius: 4px;
        }}
        @media print {{
            body {{
                margin: 20px;
            }}
            .img-container {{
                box-shadow: none;
                background: none;
                padding: 0;
            }}
        }}
    </style>
</head>
<body>
    {body_content}
</body>
</html>
"""

print("Writing self-contained HTML file...")
with open(output_html, "w", encoding="utf-8") as f:
    f.write(html_template)
print(f"HTML presentation successfully saved to: {output_html}")

# Try to use macOS textutil to convert HTML to Word Docx
try:
    print("Converting HTML to DOCX using macOS textutil...")
    subprocess.run(["textutil", "-convert", "docx", output_html, "-output", output_docx], check=True)
    print(f"Word Document successfully saved to: {output_docx}")
except Exception as e:
    print(f"Note: Could not run textutil for Docx conversion ({e})")
