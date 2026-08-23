"""Self-contained public report form for the moderated submission API."""
from __future__ import annotations

import html
import json

from .catalog import MODEL_CATALOG, variant_breakdown
from .detect import Assessment, Complaint, TAXONOMY


def render_report_form(
        models: dict[str, tuple[list[Complaint], list[Assessment]]],
        out_path: str) -> None:
    families = "".join(
        f'<option value="{html.escape(key)}">'
        f'{html.escape(meta["lab"])} · {html.escape(meta["label"])}</option>'
        for key, meta in MODEL_CATALOG.items()
    )
    exact_names = {
        item["label"]
        for family, (complaints, _) in models.items()
        for item in variant_breakdown(family, complaints)
        if item["monitored"]
    }
    for meta in MODEL_CATALOG.values():
        exact_names.update(meta["recognised_terms"])
    exact_options = "".join(
        f'<option value="{html.escape(name, quote=True)}"></option>'
        for name in sorted(exact_names)
    )
    model_to_family = {}
    for family, meta in MODEL_CATALOG.items():
        names = {meta["label"], *meta["recognised_terms"]}
        names.update(item["label"] for item in meta.get("tracked_variants", ()))
        for name in names:
            model_to_family[name.lower()] = family
    prefill_json = json.dumps(model_to_family, separators=(",", ":"))
    category_labels = {
        "quality": "Overall quality dropped",
        "sluggish": "Slow or laggy",
        "lazy": "Incomplete or unusually minimal",
        "refusals": "Unexpected refusals",
        "length": "Responses became shorter",
        "other": "Something else",
    }
    categories = "".join(
        f'<option value="{key}">{html.escape(category_labels.get(key, key.title()))}</option>'
        for key in (*TAXONOMY.keys(), "other")
    )
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Report model weather — The Barometer</title>
<style>
:root{{--ink:#e7ecf1;--muted:#98a4af;--paper:#0b0f14;--panel:#121821;--line:#293541;--blue:#7eb2c8;--amber:#e8b85b;--bad:#ef8f63}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif}}
.shell{{width:min(820px,calc(100% - 36px));margin:auto}} header{{padding:28px 0;border-bottom:1px solid rgba(255,255,255,.07)}}
.brand{{color:var(--ink);font-weight:800;text-decoration:none}} .brand span{{color:var(--blue)}}
main{{padding:58px 0 80px}} .eyebrow{{color:var(--blue);font-size:11px;font-weight:800;letter-spacing:.14em;text-transform:uppercase}}
h1{{font-size:clamp(38px,7vw,64px);line-height:1;letter-spacing:-.05em;margin:12px 0 18px}} .intro{{color:#aeb8c2;font-size:17px;max-width:680px;margin-bottom:32px}}
.boundary{{background:#111b22;border:1px solid #29404d;border-radius:13px;padding:15px 17px;color:#aebdca;font-size:13px;margin-bottom:18px}}
.boundary b{{color:#d5e9f1}} form{{background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:26px;box-shadow:0 18px 44px rgba(0,0,0,.22)}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:18px}} .field{{margin-bottom:18px}} .field.full{{grid-column:1/-1}}
label,.legend{{display:block;font-weight:730;margin-bottom:7px}} .hint{{display:block;color:var(--muted);font-size:11px;font-weight:400;margin-top:2px}}
input,select,textarea{{width:100%;background:#0d131a;color:var(--ink);border:1px solid #34414e;border-radius:10px;padding:11px 12px;font:inherit;outline:none}}
input:focus,select:focus,textarea:focus{{border-color:var(--blue);box-shadow:0 0 0 3px rgba(126,178,200,.12)}} textarea{{resize:vertical;min-height:120px}}
.consent{{display:grid;grid-template-columns:auto 1fr;gap:10px;align-items:start;color:#b4bec7;font-size:13px;margin:7px 0 22px}} .consent input{{width:auto;margin-top:4px}}
.actions{{display:flex;align-items:center;gap:16px}} button{{border:0;border-radius:10px;padding:12px 17px;background:var(--blue);color:#091016;font-weight:800;cursor:pointer}}
button:disabled{{opacity:.55;cursor:wait}} .status{{color:var(--muted);font-size:13px}} .status.error{{color:#ffad8b}} .status.success{{color:#9ed4af}}
.privacy{{color:#6f7b86;font-size:12px;margin-top:20px}} .honeypot{{position:absolute!important;left:-10000px!important;width:1px!important;height:1px!important;overflow:hidden!important}}
@media(max-width:650px){{.grid{{grid-template-columns:1fr}}form{{padding:20px}}}}
</style></head><body>
<header><div class="shell"><a class="brand" href="index.html"><span>☂</span> The Barometer</a></div></header>
<main class="shell"><div class="eyebrow">Community observation</div><h1>Report unusual model weather.</h1>
<p class="intro">Tell us what changed from your side of the glass. One report is a clue, not a verdict; it will be reviewed before it can influence anything public.</p>
<div class="boundary"><b>Private moderation boundary:</b> your report enters a separate pending queue. It is not posted publicly, does not enter the detector automatically, and cannot by itself change a model’s status.</div>
<form id="report-form">
  <div class="grid">
    <div class="field"><label for="family">Model family</label><select id="family" name="family" required><option value="">Choose one</option>{families}</select></div>
    <div class="field"><label for="model-name">Exact model <span class="hint">Optional; leave blank if you genuinely don’t know</span></label><input id="model-name" name="model_name" list="known-models" maxlength="80" placeholder="e.g. Claude Opus 5 or GPT-5.6"><datalist id="known-models">{exact_options}</datalist></div>
    <div class="field"><label for="category">What changed?</label><select id="category" name="category" required><option value="">Choose the closest match</option>{categories}</select></div>
    <div class="field"><label for="surface">Where did you use it?</label><select id="surface" name="surface" required><option value="">Choose one</option><option value="web">Web app</option><option value="mobile">Mobile app</option><option value="desktop">Desktop app</option><option value="api">API</option><option value="unknown">Not sure</option></select></div>
    <div class="field"><label for="timing">When did you notice it?</label><select id="timing" name="timing" required><option value="">Choose one</option><option value="now">Within the last few hours</option><option value="today">Today</option><option value="this-week">Within the last week</option><option value="unsure">Not sure</option></select></div>
    <div class="field full"><label for="description">What did you observe? <span class="hint">Optional · describe behaviour, not private prompts or personal information</span></label><textarea id="description" name="description" maxlength="600" placeholder="For example: responses became much shorter across several unrelated tasks."></textarea></div>
  </div>
  <div class="honeypot" aria-hidden="true"><label for="website">Website</label><input id="website" name="website" tabindex="-1" autocomplete="off"></div>
  <label class="consent"><input type="checkbox" name="consent" required><span>I understand this report will be privately moderated and may contribute only to non-identifying aggregate statistics. I have not included personal, confidential, or sensitive information.</span></label>
  <div class="actions"><button type="submit">Send report</button><span class="status" id="form-status" role="status" aria-live="polite"></span></div>
  <p class="privacy">No account, email address, or persistent IP address is requested or stored. Raw reports are scheduled for deletion after 30 days. Public output is aggregate-only.</p>
</form></main>
<script>
(() => {{
  const form = document.querySelector('#report-form');
  const button = form.querySelector('button');
  const status = document.querySelector('#form-status');
  const requestedModel = new URLSearchParams(location.search).get('model');
  if (requestedModel) {{
    const familyByModel = {prefill_json};
    document.querySelector('#model-name').value = requestedModel;
    document.querySelector('#family').value = familyByModel[requestedModel.toLowerCase()] || '';
  }}
  form.addEventListener('submit', async event => {{
    event.preventDefault(); button.disabled = true; status.className = 'status'; status.textContent = 'Sending…';
    const data = new FormData(form);
    const payload = Object.fromEntries(data.entries());
    payload.consent = data.get('consent') === 'on';
    try {{
      const response = await fetch('/api/reports', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(payload)}});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'The report could not be accepted.');
      form.reset(); status.className = 'status success'; status.textContent = `Received for moderation · ${{result.id}}`;
    }} catch (error) {{ status.className = 'status error'; status.textContent = error.message; }}
    finally {{ button.disabled = false; }}
  }});
}})();
</script></body></html>"""
    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write(page)
