"""Evidence-led exclusive SME scope. Unknown is never a positive legal premise.

A generic legal clause, subcontractor quota or security percentage does not
establish the category of this procurement. Contradictory explicit facts are
retained for review; this module never modifies historical decisions.
"""
import json
import re
from v331_broad_legal import verified
from v314_eis_metadata import table_pairs

SME = r'(?:субъект\w*\s+(?:мал\w*\s+и\s+средн\w*\s+предпринимательств\w*|мсп)|мсп|смсп|сумсп)'
EXCLUSIVE = re.compile(r'(?:только|исключительно)\s+(?:для\s+)?'+SME, re.I)
CONDITIONAL = re.compile(r'\b(?:если|в\s+случае|при\s+проведении|при\s+осуществлении|в\s+соответствии\s+с|согласно)\b', re.I)
NEGATIVE = re.compile(r'(?:не\s+огранич\w*\s+(?:участи\w*\s+)?'+SME+r'|не\s+(?:является|относится).{0,65}(?:только|исключительно)\s+'+SME+r')', re.I)
LABEL = re.compile(r'участник\w*\s+(?:настоящ\w*\s+)?закупк\w*\s+могут\s+быть\s+только\s+'+SME, re.I)
DECLARATION = re.compile(r'(?:участник\w*\s+(?:настоящ\w*\s+)?(?:закупк\w*|аукцион\w*|конкурс\w*)\s+(?:могут\s+быть|являются)|(?:настоящ\w*|данн\w*|эта)\s+(?:закупк\w*|аукцион\w*|конкурс\w*).{0,45}(?:проводится|проводятся))',re.I)
SME_TYPES = {'purchaseNoticeKESMBO','purchaseNoticeAESMBO','purchaseNoticeZPESMBO','purchaseNoticeZKESMBO'}


def saved_card(p):
    raw=p.get('card_fields_json') or {}
    try: raw=json.loads(raw) if isinstance(raw,str) else raw
    except (ValueError,TypeError): return {}
    return raw if isinstance(raw,dict) else {}


def resolve(p, docs=()):
    evidence=[]
    def add(value, source, text='', **extra):
        evidence.append(dict(value=value,source=source,evidence=text[:650],**extra))
    card=saved_card(p)
    for facts in (card.get('fact_evidence') or {}, p.get('fact_evidence') or {}):
        fact=facts.get('sme_only') or {}
        # The aggregate is not a new independent authoritative fact.
        if fact.get('source') in {'official_eis_card','official_eis_getDocsIP'} and isinstance(fact.get('value'),bool):
            evidence.append(dict(fact))
    xml=card.get('soap_metadata') or {}
    if xml.get('source')=='official_eis_getDocsIP' and xml.get('xml_type') in SME_TYPES and xml.get('xml_sha256'):
        add(True,'official_eis_getDocsIP',xml['xml_type'],**{k:v for k,v in xml.items() if k not in ('source','evidence','value')})
    method=' '.join(str(p.get('method') or '').split())
    if EXCLUSIVE.search(method) and not CONDITIONAL.search(method) and not re.search(r'\bне\b',method,re.I):
        add(True,'official_eis_method',method)
    for doc in list(docs)[:650]:
        if not isinstance(doc,dict) or not verified(doc):continue
        body=str(doc.get('text') or '')[:750000].replace('\xa0',' ')
        if not re.search(SME,body,re.I):continue
        body=re.sub(r'\n\s*(Да|Нет)\b',r' | \1',body,flags=re.I)
        body=re.sub(r'((?:если|в случае|при проведении|при осуществлении)[^\n.!?;]*)\n',r'\1 ',body,flags=re.I)
        pairs=table_pairs(body)
        lines=body.splitlines()
        for i,line in enumerate(lines):
            match=re.match(r'^\s*(.{3,160}?)\s*[:：]\s*(.*)$',line)
            if match:pairs.append([match[1],match[2],line])
            if i+1<len(lines):pairs.append([line,lines[i+1],line+' '+lines[i+1]])
        for label,value,*rest in pairs:
            if not re.fullmatch(r'\s*(?:участники\s+(?:настоящей\s+)?закупки|категория\s+участников)\s*',label,re.I):continue
            value=' '.join(value.split())
            meta=doc.get('meta') or {}
            extra=dict(document=doc.get('name'),source_url=meta.get('source_url'),sha256=meta.get('sha256'))
            if EXCLUSIVE.search(value) and re.search(r'\(\s*нет\s*\)\s*$',value,re.I):
                add(False,'official_eis_document',label+': '+value,**extra)
            elif NEGATIVE.search(value):add(False,'official_eis_document',label+': '+value,**extra)
            elif EXCLUSIVE.search(value) and not CONDITIONAL.search(value) and not re.search(r'\bне\b',value,re.I):
                add(True,'official_eis_document',label+': '+value,**extra)
        # Sentence/line boundaries prevent unrelated distant clauses from
        # turning a conditional legal quotation into a tender declaration.
        for raw in re.split(r'[\n\r.!?;]+',body):
            line=' '.join(raw.split())
            if not re.search(SME,line,re.I) or len(line)>2000:continue
            meta=doc.get('meta') or {}
            extra=dict(document=doc.get('name'),source_url=meta.get('source_url'),sha256=meta.get('sha256'))
            if CONDITIONAL.search(line):continue
            label=LABEL.search(line)
            if label:
                suffix=re.sub(r'\([^)]*\)', '', line[label.end():]).strip(' :|–—-').lower()
                if re.match(r'нет\b',suffix):
                    add(False,'official_eis_document',line,**extra);continue
                if re.match(r'да\b',suffix):
                    add(True,'official_eis_document',line,**extra);continue
                if not re.search(r'\b(?:настоящ\w*|данн\w*|эта)\b',line,re.I):continue
            if re.search(r'\b(?:иных|других|таких|любых)\s+(?:процедур\w*|закуп\w*)',line,re.I):continue
            if NEGATIVE.search(line):
                add(False,'official_eis_document',line,**extra);continue
            if EXCLUSIVE.search(line) and DECLARATION.search(line) and not re.search(r'\bне\b',line,re.I):
                add(True,'official_eis_document',line,**extra)
    unique={json.dumps(x,sort_keys=True,ensure_ascii=False):x for x in evidence}
    evidence=list(unique.values())
    values={x['value'] for x in evidence}
    state='conflict' if len(values)>1 else 'yes' if values=={True} else 'no' if values=={False} else 'unknown'
    return dict(value=True if state=='yes' else False if state=='no' else None,
                state=state,source='msp_scope_resolver',evidence=evidence)


def display(p):
    scope=(saved_card(p).get('fact_evidence') or {}).get('sme_scope') or resolve(p)
    return {'yes':'Да, только МСП','no':'Нет, участие не ограничено МСП',
            'conflict':'Противоречивые сведения — требуется проверка',
            'unknown':'Не установлено по официальным сведениям'}.get(scope.get('state'),'Не установлено')
