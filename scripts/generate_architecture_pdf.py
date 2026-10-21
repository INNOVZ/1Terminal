import re
import subprocess
from pathlib import Path
import markdown

SRC_MD = Path("/Users/jithinjacob/.gemini/antigravity-ide/brain/4b665864-2806-4080-92d6-24d463bbc4dd/atlas_screener_architecture.md")
OUT_HTML = Path("/tmp/atlas_screener_architecture.html")
OUT_PDF = Path("/Users/jithinjacob/Desktop/trdindicator/Atlas_Screener_Architecture_Specification.pdf")
ARTIFACT_PDF = Path("/Users/jithinjacob/.gemini/antigravity-ide/brain/4b665864-2806-4080-92d6-24d463bbc4dd/Atlas_Screener_Architecture_Specification.pdf")

def convert_md_to_html(md_text: str) -> str:
    # 1. Extract and convert GitHub alerts: > [!NOTE] etc.
    def replace_alerts(text: str) -> str:
        alert_pattern = re.compile(r'>\s*\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*\n((?:>.*\n?)*)', re.IGNORECASE)
        def alert_repl(match):
            kind = match.group(1).upper()
            body = "\n".join(line.lstrip("> ").rstrip() for line in match.group(2).strip().splitlines())
            return f'<div class="callout callout-{kind.lower()}"><div class="callout-header">{kind}</div><div class="callout-body">{body}</div></div>\n'
        return alert_pattern.sub(alert_repl, text)

    text_with_alerts = replace_alerts(md_text)

    # 2. Convert markdown to HTML
    html_body = markdown.markdown(
        text_with_alerts,
        extensions=["tables", "fenced_code", "toc"]
    )

    # 3. Clean LaTeX formulas into styled math cards
    def clean_math(match):
        eq = match.group(1).strip()
        eq = re.sub(r'\\text\{([^}]+)\}', r'<strong>\1</strong>', eq)
        eq = re.sub(r'\\left\(', '(', eq)
        eq = re.sub(r'\\right\)', ')', eq)
        eq = eq.replace(r'\sum_{i}', '&sum;<sub>i</sub>').replace(r'\cdot', '&middot;').replace(r'\times', '&times;')
        eq = eq.replace(r'\ge', '&ge;').replace(r'\le', '&le;').replace(r'\sim', '&sim;')
        return f'<div class="math-card">{eq}</div>'

    html_body = re.sub(r'\$\$(.*?)\$\$', clean_math, html_body, flags=re.DOTALL)
    html_body = re.sub(r'\$([^$\n]+)\$', lambda m: m.group(1).replace(r'\le', '&le;').replace(r'\ge', '&ge;').replace(r'\times', '&times;').replace(r'\sim', '&sim;').replace(r'\text{ATR}', 'ATR').replace(r'\text{bps}', 'bps').replace(r'\text{R}', 'R'), html_body)

    # 4. Replace fenced code blocks for mermaid: <pre><code class="language-mermaid">...</code></pre> with <div class="mermaid">...</div>
    mermaid_pattern = re.compile(r'<pre><code class="language-mermaid">(.*?)</code></pre>', re.DOTALL)
    def mermaid_repl(match):
        code = match.group(1).replace("&gt;", ">").replace("&lt;", "<").replace("&amp;", "&")
        return f'<div class="mermaid-container"><pre class="mermaid">{code}</pre></div>'
    html_body = mermaid_pattern.sub(mermaid_repl, html_body)

    # Complete document template
    full_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="color-scheme" content="light only">
<title>Atlas Institutional Crypto Screener - Architecture Specification</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<script>
    mermaid.initialize({{
        startOnLoad: true,
        theme: 'default',
        themeVariables: {{
            primaryColor: '#f0f9ff',
            primaryTextColor: '#0f172a',
            primaryBorderColor: '#0284c7',
            lineColor: '#64748b',
            secondaryColor: '#f8fafc',
            tertiaryColor: '#ffffff',
            fontFamily: 'Inter',
            fontSize: '11px'
        }},
        flowchart: {{ useMaxWidth: true, htmlLabels: true, curve: 'basis' }},
        sequence: {{ useMaxWidth: true, showSequenceNumbers: true }}
    }});
</script>
<style>
    :root {{
        color-scheme: light only;
    }}

    * {{
        box-sizing: border-box;
    }}

    body {{
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
        font-size: 9pt;
        line-height: 1.55;
        color: #1e293b;
        background: #ffffff;
        margin: 0;
        padding: 0;
    }}

    .doc-header {{
        border-bottom: 2px solid #0f172a;
        padding-bottom: 12px;
        margin-bottom: 20px;
        display: flex;
        justify-content: space-between;
        align-items: flex-end;
    }}

    .doc-title {{
        font-size: 20pt;
        font-weight: 800;
        letter-spacing: -0.5px;
        color: #0f172a;
        margin: 0;
    }}

    .doc-subtitle {{
        font-size: 10pt;
        font-weight: 500;
        color: #475569;
        margin-top: 4px;
    }}

    .doc-meta {{
        text-align: right;
        font-size: 8pt;
        color: #64748b;
    }}

    .badge {{
        display: inline-block;
        background: #0284c7;
        color: #ffffff;
        font-size: 7.5pt;
        font-weight: 700;
        padding: 2px 8px;
        border-radius: 4px;
        letter-spacing: 0.5px;
        text-transform: uppercase;
        margin-bottom: 4px;
    }}

    h1, h2, h3, h4 {{
        color: #0f172a;
        font-weight: 700;
        letter-spacing: -0.3px;
        margin-top: 18px;
        margin-bottom: 8px;
    }}

    h2 {{
        font-size: 12.5pt;
        border-bottom: 1px solid #e2e8f0;
        padding-bottom: 5px;
        margin-top: 22px;
        display: flex;
        align-items: center;
        gap: 6px;
    }}

    h3 {{
        font-size: 10.5pt;
        color: #1e293b;
        margin-top: 14px;
    }}

    p {{
        margin: 0 0 8px 0;
    }}

    ul, ol {{
        margin: 0 0 10px 0;
        padding-left: 20px;
    }}

    li {{
        margin-bottom: 3px;
    }}

    strong {{
        font-weight: 600;
        color: #0f172a;
    }}

    code {{
        font-family: 'JetBrains Mono', monospace;
        font-size: 8pt;
        background: #f1f5f9;
        color: #0f172a;
        padding: 1px 4px;
        border-radius: 3px;
        border: 1px solid #e2e8f0;
    }}

    pre {{
        background: #0f172a;
        color: #f8fafc;
        padding: 10px 14px;
        border-radius: 6px;
        font-family: 'JetBrains Mono', monospace;
        font-size: 8pt;
        overflow-x: auto;
        margin: 10px 0;
    }}

    pre code {{
        background: transparent;
        color: inherit;
        padding: 0;
        border: none;
    }}

    table {{
        width: 100%;
        border-collapse: collapse;
        margin: 12px 0 16px 0;
        font-size: 8.5pt;
        background: #ffffff;
        border: 1px solid #cbd5e1;
        border-radius: 4px;
    }}

    th {{
        background: #f8fafc;
        color: #334155;
        font-weight: 700;
        text-align: left;
        padding: 7px 10px;
        border-bottom: 2px solid #cbd5e1;
        font-size: 8pt;
        letter-spacing: 0.3px;
        text-transform: uppercase;
    }}

    td {{
        padding: 6px 10px;
        border-bottom: 1px solid #e2e8f0;
        vertical-align: middle;
    }}

    tr:nth-child(even) td {{
        background: #f8fafc;
    }}

    .callout {{
        border-left: 4px solid #0284c7;
        background: #f0f9ff;
        padding: 10px 14px;
        border-radius: 0 6px 6px 0;
        margin: 12px 0 16px 0;
        font-size: 9pt;
    }}

    .callout-header {{
        font-size: 7.5pt;
        font-weight: 800;
        letter-spacing: 0.5px;
        text-transform: uppercase;
        color: #0369a1;
        margin-bottom: 4px;
    }}

    .callout-body {{
        color: #0c4a6e;
    }}

    .callout-warning {{
        border-left-color: #f59e0b;
        background: #fffbeb;
    }}
    .callout-warning .callout-header {{
        color: #b45309;
    }}
    .callout-warning .callout-body {{
        color: #78350f;
    }}

    .callout-note {{
        border-left-color: #6366f1;
        background: #eef2ff;
    }}
    .callout-note .callout-header {{
        color: #4338ca;
    }}
    .callout-note .callout-body {{
        color: #312e81;
    }}

    .mermaid-container {{
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 6px;
        padding: 12px;
        margin: 14px 0;
        display: flex;
        justify-content: center;
        page-break-inside: avoid;
    }}

    .mermaid {{
        font-family: 'Inter', sans-serif !important;
        font-size: 8pt !important;
    }}

    .math-card {{
        background: #f8fafc;
        border: 1px solid #cbd5e1;
        border-left: 4px solid #0284c7;
        padding: 8px 12px;
        border-radius: 4px;
        margin: 10px 0;
        font-family: 'JetBrains Mono', monospace;
        font-size: 8.5pt;
        color: #0f172a;
        page-break-inside: avoid;
    }}

    .page-break {{
        page-break-before: always;
    }}

    @media print {{
        body {{
            -webkit-print-color-adjust: exact;
            print-color-adjust: exact;
        }}
        .mermaid-container, table, .callout {{
            page-break-inside: avoid;
        }}
        h2 {{
            page-break-after: avoid;
        }}
    }}
</style>
</head>
<body>

<div class="doc-header">
    <div>
        <div class="badge">Production Architecture • V2.5</div>
        <h1 class="doc-title">Atlas Screener Architecture Specification</h1>
        <div class="doc-subtitle">Institutional Multi-Timeframe Crypto Quantitative Screening Engine</div>
    </div>
    <div class="doc-meta">
        <div><strong>Venue:</strong> Binance USD-M Futures</div>
        <div><strong>Deployment:</strong> GCP Production Atlas-1</div>
        <div><strong>Status:</strong> Active & Aligned</div>
    </div>
</div>

{html_body}

</body>
</html>
"""
    return full_html

def main():
    print(f"Reading markdown from {SRC_MD}...")
    md_text = SRC_MD.read_text(encoding="utf-8")
    
    print("Converting markdown to styled HTML...")
    html_text = convert_md_to_html(md_text)
    OUT_HTML.write_text(html_text, encoding="utf-8")
    print(f"Wrote HTML to {OUT_HTML}")

    print("Generating PDF via Google Chrome headless...")
    cmd = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "--headless",
        "--disable-gpu",
        "--no-pdf-header-footer",
        "--virtual-time-budget=6000",
        "--run-all-compositor-stages-before-draw",
        f"--print-to-pdf={OUT_PDF}",
        f"file://{OUT_HTML.resolve()}",
    ]
    subprocess.run(cmd, check=True)
    print(f"Generated PDF at {OUT_PDF} ({OUT_PDF.stat().st_size:,} bytes)")

    # Copy to artifacts directory
    ARTIFACT_PDF.write_bytes(OUT_PDF.read_bytes())
    print(f"Copied PDF to artifact at {ARTIFACT_PDF}")

if __name__ == "__main__":
    main()
