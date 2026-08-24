"""Local-only classifier review application and read-only source loader."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from .catalog import MODEL_CATALOG
from .classifier import (
    CLASSIFIER_VERSION,
    attribution_review_status,
    classify_report,
    mentioned_families,
    mentioned_variants,
    normalise_report_text,
)
from .reviews import ReviewStore, review_unit_id, source_fingerprint
from .vocabulary import (
    VALID_CHANGES,
    VALID_ELICITATION_CONTEXTS,
    VALID_EVENT_STATES,
    VALID_STATES,
    VALID_SUSPECTED_LAYERS,
    VALID_VALENCES,
    load_vocabulary,
)


def _json_observation(observation) -> dict:
    payload = asdict(observation)
    payload["suspected_layers"] = list(payload["suspected_layers"])
    payload["qualifiers"] = list(payload["qualifiers"])
    return payload


def load_source_reports(path: str | Path) -> list[dict]:
    """Read retained reports without allowing SQLite to create or mutate files."""
    database = Path(path).resolve()
    connection = sqlite3.connect(
        f"file:{database.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        rows = connection.execute(
            "SELECT id,ts,source,model,variant,text,url "
            "FROM complaints ORDER BY ts DESC"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def _variant_family(variant_key: str) -> str | None:
    for family, entry in MODEL_CATALOG.items():
        if any(
            variant["key"] == variant_key
            for variant in entry.get("tracked_variants", ())
        ):
            return family
    return None


def review_targets(row: dict) -> list[tuple[str, str | None]]:
    """Return one stable model target per review slice for a source report."""
    families = mentioned_families(row["text"])
    variants = mentioned_variants(row["text"])
    variant_families = {
        family for family in (_variant_family(item) for item in variants)
        if family is not None
    }
    targets: list[tuple[str, str | None]] = []
    for family, entry in MODEL_CATALOG.items():
        for variant in entry.get("tracked_variants", ()):
            if variant["key"] in variants:
                targets.append((family, variant["key"]))
        if family in families and family not in variant_families:
            targets.append((family, None))
    if not targets:
        stored_family = row.get("model")
        stored_variant = row.get("variant")
        if stored_family in MODEL_CATALOG:
            targets.append((stored_family, stored_variant))
    return targets


def _target_label(family: str, variant_key: str | None) -> str:
    entry = MODEL_CATALOG[family]
    if variant_key:
        for variant in entry.get("tracked_variants", ()):
            if variant["key"] == variant_key:
                return variant["label"]
    return entry["label"]


def build_review_items(
    source_db: str | Path,
    review_db: str | Path,
) -> list[dict]:
    decisions = {}
    review_path = Path(review_db)
    review_path.parent.mkdir(parents=True, exist_ok=True)
    with ReviewStore(str(review_path)) as store:
        decisions = store.all()

    items = []
    for row in load_source_reports(source_db):
        result = classify_report(row["text"])
        fingerprint = source_fingerprint(row["id"], row["text"])
        targets = review_targets(row)
        attribution_status = attribution_review_status(
            row["text"], row["model"], row["variant"])
        split_required = len(targets) > 1
        for index, (seed_family, seed_variant) in enumerate(targets, start=1):
            unit_id = review_unit_id(row["id"], seed_family, seed_variant)
            decision = decisions.get(unit_id)
            stale = bool(
                decision and decision["source_fingerprint"] != fingerprint)
            if stale:
                decision = {**decision, "stale": True}
            if split_required:
                proposal_observations = []
                proposal_novelty = []
                abstention_reason = (
                    "source-level behaviour was detected, but each model target "
                    "requires separate human attribution")
            else:
                proposal_observations = [
                    _json_observation(item) for item in result.observations]
                proposal_novelty = list(result.novelty_candidates)
                abstention_reason = result.abstention_reason
            items.append({
                "review_unit_id": unit_id,
                "report_id": row["id"],
                "received_at": datetime.fromtimestamp(
                    row["ts"], tz=timezone.utc).isoformat(),
                "source": row["source"],
                "stored_family": row["model"],
                "stored_variant": row["variant"],
                "seed_family": seed_family,
                "seed_variant": seed_variant,
                "target_label": _target_label(seed_family, seed_variant),
                "target_index": index,
                "target_count": len(targets),
                "source_url": row["url"],
                "text": normalise_report_text(row["text"]),
                "source_fingerprint": fingerprint,
                "mentioned_families": sorted(mentioned_families(row["text"])),
                "mentioned_variants": sorted(mentioned_variants(row["text"])),
                "attribution_status": attribution_status,
                "proposal": {
                    "classifier_version": CLASSIFIER_VERSION,
                    "eligibility": result.eligibility,
                    "onset_precision": result.onset_precision,
                    "observations": proposal_observations,
                    "novelty_candidates": proposal_novelty,
                    "abstention_reason": abstention_reason,
                    "source_observation_count": len(result.observations),
                    "source_novelty_candidates": list(
                        result.novelty_candidates),
                    "target_attribution_required": split_required,
                },
                "decision": decision,
            })
    return items


def review_metadata() -> dict:
    concepts = []
    for concept in load_vocabulary():
        concepts.append({
            "id": concept.id,
            "label": concept.public_label,
            "shape": concept.shape,
            "parent": concept.parent,
            "coding_scope": concept.coding_scope,
            "reporting_layer": concept.reporting_layer,
            "allowed_states": list(concept.allowed_states),
            "allowed_changes": list(concept.allowed_changes),
            "allowed_event_states": list(concept.allowed_event_states),
            "allowed_qualifiers": list(concept.allowed_qualifiers),
        })
    families = {
        family: {
            "label": entry["label"],
            "lab": entry["lab"],
            "variants": list(entry.get("tracked_variants", ())),
        }
        for family, entry in MODEL_CATALOG.items()
    }
    return {
        "classifier_version": CLASSIFIER_VERSION,
        "concepts": concepts,
        "families": families,
        "options": {
            "states": sorted(VALID_STATES),
            "changes": sorted(VALID_CHANGES),
            "event_states": sorted(VALID_EVENT_STATES),
            "valences": sorted(VALID_VALENCES),
            "suspected_layers": sorted(VALID_SUSPECTED_LAYERS),
            "elicitation_contexts": sorted(VALID_ELICITATION_CONTEXTS),
        },
    }


def render_review_page(csrf_token: str) -> bytes:
    token_json = json.dumps(csrf_token)
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Classifier review · The Barometer</title>
<style>
:root{{--ink:#edf2f4;--muted:#98a7af;--paper:#081016;--panel:#101b23;--panel2:#14232d;--line:#263a46;--blue:#81c2d4;--amber:#efc36d;--green:#8dd3aa;--red:#ee957f}}
*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 80% -20%,#17303b 0,transparent 34%),var(--paper);color:var(--ink);font:14px/1.45 Inter,ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif}}
button,input,select,textarea{{font:inherit}}button{{cursor:pointer}}.shell{{width:min(1500px,calc(100% - 30px));margin:auto}}
header{{padding:20px 0;border-bottom:1px solid rgba(255,255,255,.08)}}.brand{{font-weight:850;letter-spacing:-.02em}}.brand span{{color:var(--blue)}}.sub{{color:var(--muted);font-size:12px;margin-top:3px}}
.warning{{margin:18px 0;padding:12px 14px;border:1px solid #4f4832;background:#1e1b12;border-radius:12px;color:#dbc894}}
.metrics{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-bottom:14px}}.metric{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px}}.metric b{{display:block;font-size:22px}}.metric span{{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.08em}}
.filters{{display:flex;gap:7px;flex-wrap:wrap;margin-bottom:14px}}.filters button{{border:1px solid var(--line);background:var(--panel);color:var(--muted);padding:7px 10px;border-radius:999px}}.filters button.active{{background:#173441;color:var(--ink);border-color:#3f7485}}
.layout{{display:grid;grid-template-columns:330px minmax(0,1fr);gap:14px;padding-bottom:48px}}.queue,.detail{{background:rgba(16,27,35,.94);border:1px solid var(--line);border-radius:16px;min-height:580px}}.queue{{padding:8px;max-height:calc(100vh - 220px);overflow:auto}}
.qitem{{display:block;width:100%;text-align:left;background:transparent;color:var(--ink);border:0;border-bottom:1px solid rgba(255,255,255,.06);padding:12px;border-radius:9px}}.qitem:hover,.qitem.active{{background:#172832}}.qtop{{display:flex;justify-content:space-between;gap:8px}}.qtarget{{font-weight:800;color:var(--blue)}}.qid{{font-family:ui-monospace,monospace;font-size:10px;color:var(--muted);margin-top:2px}}.qtext{{color:#c3cdd2;margin-top:6px;font-size:12px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}}
.pill{{display:inline-flex;align-items:center;border:1px solid #35505d;border-radius:999px;padding:3px 7px;color:#b9cad1;font-size:10px;letter-spacing:.04em}}.pill.warn{{color:var(--amber);border-color:#665a38}}.pill.good{{color:var(--green);border-color:#39664d}}.pill.bad{{color:var(--red);border-color:#74483f}}
.detail{{padding:22px}}.empty{{display:grid;place-items:center;color:var(--muted)}}.eyebrow{{color:var(--blue);font-size:10px;font-weight:800;letter-spacing:.13em;text-transform:uppercase}}h1{{font-size:26px;margin:6px 0 8px}}.badges{{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:16px}}
.report{{white-space:pre-wrap;background:#0b151b;border:1px solid #243843;border-radius:13px;padding:16px;color:#d7e0e4;max-height:270px;overflow:auto}}.splitnote{{margin:12px 0;padding:11px 13px;border-radius:10px;border:1px solid #665a38;background:#1e1b12;color:#dbc894;font-size:12px}}.section{{margin-top:20px;padding-top:18px;border-top:1px solid rgba(255,255,255,.08)}}.section h2{{font-size:15px;margin:0 0 10px}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}label{{display:block;color:#b5c1c7;font-size:12px;margin-bottom:5px}}select,input,textarea{{width:100%;background:#0b151b;color:var(--ink);border:1px solid #314753;border-radius:9px;padding:9px}}textarea{{min-height:74px;resize:vertical}}
.obs{{background:var(--panel2);border:1px solid #2d4653;border-radius:12px;padding:12px;margin:9px 0}}.obshead{{display:flex;justify-content:space-between;gap:10px;align-items:center}}.obsgrid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin-top:10px}}.remove{{border:0;background:transparent;color:var(--red)}}
.addrow{{display:grid;grid-template-columns:1fr auto;gap:8px}}.addrow button,.actions button{{border:1px solid #3d5966;background:#18313c;color:var(--ink);border-radius:9px;padding:9px 12px;font-weight:750}}.actions{{display:flex;gap:8px;flex-wrap:wrap;margin-top:18px}}.actions .approve{{background:#173a2a;border-color:#38694f}}.actions .correct{{background:#173441;border-color:#3f7485}}.actions .reject{{background:#3a201b;border-color:#74483f}}.status{{margin-left:auto;color:var(--muted);align-self:center}}.status.error{{color:var(--red)}}.status.ok{{color:var(--green)}}
@media(max-width:850px){{.metrics{{grid-template-columns:1fr 1fr}}.layout{{grid-template-columns:1fr}}.queue{{max-height:260px;min-height:0}}.detail{{min-height:500px}}.obsgrid{{grid-template-columns:1fr 1fr}}}}
@media(max-width:520px){{.shell{{width:min(100% - 18px,1500px)}}.grid,.obsgrid{{grid-template-columns:1fr}}.detail{{padding:16px}}.metrics{{grid-template-columns:1fr 1fr}}}}
</style></head><body><header><div class="shell"><div class="brand"><span>☂</span> The Barometer · classifier review</div><div class="sub">Private local surface · nothing here changes public weather</div></div></header>
<main class="shell"><div class="warning">This is a human coding workspace. Retained rows may be synthetic, legacy, misrouted, or ordinary chatter. Approval records a review decision only; it does not activate the classifier.</div>
<div class="metrics" id="metrics"></div><div class="filters" id="filters"></div><div class="layout"><aside class="queue" id="queue"></aside><section class="detail empty" id="detail">Loading review queue…</section></div></main>
<script>
const CSRF={token_json};let data={{items:[],meta:null}},filter='pending',selected=null;
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
const label=(id)=>data.meta.concepts.find(c=>c.id===id)?.label||id;
const decisionStatus=i=>i.decision&&!i.decision.stale?i.decision.status:'pending';
const clone=v=>JSON.parse(JSON.stringify(v));
function metrics(){{const counts={{pending:0,approved:0,corrected:0,rejected:0,deferred:0}},sources=new Set(data.items.map(i=>i.report_id)),splitSources=new Set(data.items.filter(i=>i.target_count>1).map(i=>i.report_id)),novelSources=new Set(data.items.filter(i=>i.proposal.source_novelty_candidates.length).map(i=>i.report_id));data.items.forEach(i=>counts[decisionStatus(i)]++);document.querySelector('#metrics').innerHTML=[['Source reports',sources.size],['Pending model slices',counts.pending],['Split comparisons',splitSources.size],['Novelty sources',novelSources.size]].map(([k,v])=>`<div class="metric"><b>${{v}}</b><span>${{k}}</span></div>`).join('')}}
function renderFilters(){{const names=['pending','all','approved','corrected','rejected','deferred'];document.querySelector('#filters').innerHTML=names.map(n=>`<button class="${{filter===n?'active':''}}" data-filter="${{n}}">${{n}}</button>`).join('');document.querySelectorAll('[data-filter]').forEach(b=>b.onclick=()=>{{filter=b.dataset.filter;selected=null;render()}})}}
function visible(){{return data.items.filter(i=>filter==='all'||decisionStatus(i)===filter)}}
function renderQueue(){{const items=visible();if(!items.some(i=>i.review_unit_id===selected))selected=items[0]?.review_unit_id||null;document.querySelector('#queue').innerHTML=items.length?items.map(i=>`<button class="qitem ${{selected===i.review_unit_id?'active':''}}" data-id="${{esc(i.review_unit_id)}}"><div class="qtop"><span class="qtarget">${{esc(i.target_label)}}</span><span class="pill ${{decisionStatus(i)==='pending'?'warn':'good'}}">${{esc(decisionStatus(i))}}</span></div><div class="qid">${{esc(i.report_id)}}${{i.target_count>1?` · target ${{i.target_index}}/${{i.target_count}}`:''}}</div><div class="qtext">${{esc(i.text)}}</div></button>`).join(''):'<div class="empty" style="min-height:180px">Nothing in this view.</div>';document.querySelectorAll('.qitem').forEach(b=>b.onclick=()=>{{selected=b.dataset.id;render()}})}}
function options(values,current){{return values.map(v=>`<option value="${{esc(v)}}" ${{v===current?'selected':''}}>${{esc(v)}}</option>`).join('')}}
function defaultObservation(c){{const layer=c.reporting_layer==='product'?'product':c.reporting_layer==='cross_layer'?'unknown':'model';return{{concept_id:c.id,specificity:c.coding_scope,state:c.shape==='dimension'?'uncertain':null,change:c.shape==='dimension'?'uncertain':null,event_state:c.shape==='event'?'uncertain':null,valence:'unstated',claim_status:'reported',suspected_layers:[layer],elicitation_context:'ordinary',qualifiers:[]}}}}
function obsEditor(obs,index){{const c=data.meta.concepts.find(x=>x.id===obs.concept_id);const shape=c.shape;return`<div class="obs" data-obs="${{index}}"><div class="obshead"><b>${{esc(c.label)}}</b><button class="remove" data-remove="${{index}}">Remove</button></div><div class="obsgrid">${{shape==='dimension'?`<div><label>Current state</label><select data-field="state">${{options(c.allowed_states,obs.state)}}</select></div><div><label>Change</label><select data-field="change">${{options(c.allowed_changes,obs.change)}}</select></div>`:`<div><label>Event state</label><select data-field="event_state">${{options(c.allowed_event_states,obs.event_state)}}</select></div>`}}<div><label>Reporter valence</label><select data-field="valence">${{options(data.meta.options.valences,obs.valence)}}</select></div><div><label>Suspected layer</label><select data-field="suspected_layer">${{options(data.meta.options.suspected_layers,obs.suspected_layers[0])}}</select></div><div><label>Elicitation</label><select data-field="elicitation_context">${{options(data.meta.options.elicitation_contexts,obs.elicitation_context)}}</select></div>${{c.allowed_qualifiers.length?`<div><label>Qualifier</label><select data-field="qualifier"><option value="">Unresolved</option>${{options(c.allowed_qualifiers,obs.qualifiers[0]||'')}}</select></div>`:''}}</div></div>`}}
function variantOptions(family,current){{const variants=data.meta.families[family]?.variants||[];return'<option value="">Family only / unresolved</option>'+variants.map(v=>`<option value="${{esc(v.key)}}" ${{v.key===current?'selected':''}}>${{esc(v.label)}}</option>`).join('')}}
function renderDetail(){{const i=data.items.find(x=>x.review_unit_id===selected),d=document.querySelector('#detail');if(!i){{d.className='detail empty';d.textContent='Choose a report.';return}}d.className='detail';const saved=i.decision&&!i.decision.stale?i.decision:null;i._edit=i._edit||{{family:saved?.target_family??i.seed_family,variant:saved?.target_variant??i.seed_variant,observations:clone(saved?.observations??i.proposal.observations),novelty:clone(saved?.novelty_candidates??i.proposal.novelty_candidates),note:saved?.review_note||''}};const e=i._edit;const attrBad=i.attribution_status!=='single_family'&&i.attribution_status!=='single_variant',split=i.target_count>1;d.innerHTML=`<div class="eyebrow">${{esc(i.proposal.classifier_version)}} · ${{esc(i.received_at)}}</div><h1>${{esc(i.target_label)}} · ${{esc(i.source)}} report</h1><div class="badges"><span class="pill">stored: ${{esc(i.stored_variant||i.stored_family)}}</span><span class="pill ${{attrBad?'warn':'good'}}">${{esc(i.attribution_status)}}</span><span class="pill">${{esc(i.proposal.eligibility)}}</span></div>${{split?`<div class="splitnote">This source mentions ${{i.target_count}} model targets. You are coding only <b>${{esc(i.target_label)}}</b> in this slice. The source-level classifier found ${{i.proposal.source_observation_count}} governed observation(s), but none were copied across models because direction and valence may differ.</div>`:(i.proposal.abstention_reason?`<div class="splitnote">${{esc(i.proposal.abstention_reason)}}</div>`:'')}}<div class="report">${{esc(i.text)}}</div><div class="section"><h2>Attribution for this slice</h2><div class="grid"><div><label>Target family</label><select id="family">${{Object.entries(data.meta.families).map(([k,v])=>`<option value="${{k}}" ${{k===e.family?'selected':''}}>${{esc(v.lab)}} · ${{esc(v.label)}}</option>`).join('')}}</select></div><div><label>Exact model</label><select id="variant">${{variantOptions(e.family,e.variant)}}</select></div></div></div><div class="section"><h2>Governed observations for ${{esc(i.target_label)}}</h2><div id="observations">${{e.observations.map(obsEditor).join('')||'<div class="sub">No governed observations selected for this model yet.</div>'}}</div><div class="addrow"><select id="add-concept"><option value="">Add a governed concept…</option>${{data.meta.concepts.filter(c=>!e.observations.some(o=>o.concept_id===c.id)).map(c=>`<option value="${{c.id}}">${{esc(c.label)}} · ${{esc(c.parent||c.reporting_layer)}}</option>`).join('')}}</select><button id="add">Add</button></div></div><div class="section"><h2>Unresolved novelty for this target</h2><label>Comma-separated private candidate labels</label><input id="novelty" value="${{esc(e.novelty.join(', '))}}"><label style="margin-top:10px">Reviewer note</label><textarea id="note">${{esc(e.note)}}</textarea></div><div class="actions">${{split?'':`<button class="approve" data-save="approved">Approve proposal</button>`}}<button class="correct" data-save="corrected">Save coding</button><button data-save="deferred">Defer</button><button class="reject" data-save="rejected">No attributable report</button><span class="status" id="save-status"></span></div>`;
document.querySelector('#family').onchange=ev=>{{e.family=ev.target.value;e.variant=null;renderDetail()}};document.querySelector('#variant').onchange=ev=>e.variant=ev.target.value||null;document.querySelector('#add').onclick=()=>{{const id=document.querySelector('#add-concept').value;if(!id)return;const c=data.meta.concepts.find(x=>x.id===id);e.observations.push(defaultObservation(c));renderDetail()}};document.querySelectorAll('[data-remove]').forEach(b=>b.onclick=()=>{{e.observations.splice(Number(b.dataset.remove),1);renderDetail()}});document.querySelectorAll('.obs').forEach(el=>el.querySelectorAll('[data-field]').forEach(input=>input.onchange=()=>{{const o=e.observations[Number(el.dataset.obs)],f=input.dataset.field;if(f==='suspected_layer')o.suspected_layers=[input.value];else if(f==='qualifier')o.qualifiers=input.value?[input.value]:[];else o[f]=input.value}}));document.querySelectorAll('[data-save]').forEach(b=>b.onclick=()=>save(i,b.dataset.save));}}
async function save(i,status){{const e=i._edit,s=document.querySelector('#save-status');e.novelty=document.querySelector('#novelty').value.split(',').map(v=>v.trim()).filter(Boolean);e.note=document.querySelector('#note').value;s.className='status';s.textContent='Saving…';const payload={{status,target_family:e.family,target_variant:e.variant,observations:status==='rejected'?[]:e.observations,novelty_candidates:status==='rejected'?[]:e.novelty,review_note:e.note,source_fingerprint:i.source_fingerprint}};try{{const response=await fetch('/api/reviews/'+encodeURIComponent(i.review_unit_id),{{method:'POST',headers:{{'Content-Type':'application/json','X-Review-Token':CSRF}},body:JSON.stringify(payload)}});const result=await response.json();if(!response.ok)throw new Error(result.error||'Could not save review');i.decision=result.decision;i._edit=null;s.className='status ok';s.textContent='Saved';metrics();renderFilters();setTimeout(render,250)}}catch(error){{s.className='status error';s.textContent=error.message}}}}
function render(){{metrics();renderFilters();renderQueue();renderDetail()}}
fetch('/api/bootstrap',{{cache:'no-store'}}).then(r=>r.json()).then(v=>{{data=v;render()}}).catch(e=>{{document.querySelector('#detail').textContent=e.message}});
</script></body></html>"""
    return page.encode("utf-8")
