#!/usr/bin/env python3
"""Export read-only run evidence for a local presentation; standard library only."""
import argparse
import collections
import csv
import hashlib
import html
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo('Asia/Shanghai')

def read(path, default=None):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {} if default is None else default


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def stamp(ts):
    return datetime.fromtimestamp(ts, TZ).isoformat(timespec='milliseconds') if ts else ''


def events(root):
    path = root / 'team.sqlite'
    if path.exists():
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
            rows = db.execute('SELECT seq,timestamp,kind,role,job,detail FROM events ORDER BY seq').fetchall()
        return [dict(seq=s, timestamp=t, event=k, agent=a, job=j, detail=json.loads(d)) for s,t,k,a,j,d in rows]
    return [dict(seq=e['seq'], timestamp=e['timestamp'], event=e['event'], agent=e.get('agent'), job=e.get('job'),
                 detail={k:v for k,v in e.items() if k not in {'seq','timestamp','event','agent','job','trace_id'}})
            for e in (json.loads(line) for line in (root/'events.jsonl').read_text().splitlines() if line.strip())]


class Export:
    def __init__(self, dest):
        self.dest = dest
        self.manifest = []
        self.secrets = [v for k,v in os.environ.items() if any(x in k.upper() for x in ('API_KEY','TOKEN','SECRET')) and len(v)>7]
        self.secrets += [v for v in read(ROOT/'config/resource-secrets.json').values() if isinstance(v,str) and v]

    def clean(self, text):
        for value in self.secrets:
            text = text.replace(value, '[REDACTED]')
        text = re.sub(r'\b(?:sk-[\w-]+|gh[pousr]_[\w]+|github_pat_[\w]+)', '[REDACTED]', text)
        text = re.sub(r'(?i)(Bearer\s+)[A-Za-z0-9_.+/=-]+', r'\1[REDACTED]', text)
        text = re.sub(r'(?i)((?:api_key|access_token|password|secret)["\s]*[:=]["\s]*)[^\s",}]+', r'\1[REDACTED]', text)
        return text.replace(str(ROOT), '<PROJECT>').replace(str(Path.home()), '<HOME>')

    def write(self, name, value, source=None, note='derived'):
        text = value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,indent=2)+'\n'
        text = self.clean(text)
        path=self.dest/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_text(text,encoding='utf-8')
        self.manifest.append({'file':name,'export_sha256':sha(text.encode()),'source':str(source) if source else None,
                              'source_sha256':sha(source.read_bytes()) if source and source.is_file() else None,'note':note})

    def copy(self, source, target):
        if source.is_file():self.write(target,source.read_text(encoding='utf-8',errors='replace'),source,'redacted copy')


def md_table(head, rows):
    def cell(v):return str(v if v is not None else '—').replace('|','\\|').replace('\n',' ')
    return '\n'.join(['| '+' | '.join(head)+' |','| '+' | '.join(['---']*len(head))+' |']+
                     ['| '+' | '.join(map(cell,row))+' |' for row in rows])+'\n'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs', nargs='+', help='Run IDs; defaults to the latest four local runs')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if not args.runs:
        roots = sorted((p for p in (ROOT/'runs').glob('run-*') if (p/'status.json').is_file()),
                       key=lambda p: read(p/'status.json').get('updated', 0), reverse=True)
        args.runs = [p.name for p in roots[:4]]
    if not args.runs:
        parser.error('No local runs found; generate a run before exporting evidence.')
    for rid in args.runs:
        if not re.fullmatch(r'run-[a-zA-Z0-9-]+', rid) or not (ROOT/'runs'/rid).is_dir():
            parser.error(f'Run not found or invalid ID: {rid}')
    now=datetime.now(TZ)
    dest=(args.output or ROOT/'runs'/('evidence-demo-'+now.strftime('%Y%m%d-%H%M%S'))).resolve()
    dest.mkdir(parents=True,exist_ok=False)
    exp=Export(dest); overview=[]; pages=[]; all_counts=collections.Counter()
    for rid in args.runs:
        if not re.fullmatch(r'run-[a-zA-Z0-9-]+',rid):raise ValueError('Invalid run ID')
        root=ROOT/'runs'/rid
        ev=events(root); counts=collections.Counter(e['detail'].get('skill') for e in ev if e['event']=='skill.invoked')
        all_counts.update(counts)
        status=read(root/'status.json'); runtime=read(root/'runtime-report.json'); spec=read(root/'pipeline-spec.json'); metrics=read(root/'metrics.json')
        provenance=[]; attempts=[]
        for directory in sorted((root/'agents').glob('*/*')):
            if not directory.is_dir():continue
            input_path=directory/'input.json'; payload=read(input_path); prov=payload.get('provenance',{})
            if prov:provenance.append(prov)
            relative=directory.relative_to(root).as_posix()
            exp.write(f'{rid}/{relative}/input-summary.json', {k:payload[k] for k in ('job_id','trace_id','input_keys','allow_custom','provenance') if k in payload}, input_path if input_path.exists() else None,'projection; samples, prompts and catalog omitted')
            for name in ('output.json','transport.json','stderr.log'):
                exp.copy(directory/name, f'{rid}/{relative}/{name}')
            output=read(directory/'output.json'); transport=read(directory/'transport.json')
            output_hash=sha(json.dumps(output,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()) if output else None
            matched=[e['seq'] for e in ev if e['event']=='agent.completed' and e['job']==directory.parent.name and e['detail'].get('output_hash')==output_hash]
            attempts.append({'job':directory.parent.name,'attempt':directory.name,'role':transport.get('identity'),
                             'model':transport.get('model'),'exit_code':transport.get('exit_code'),
                             'duration_ms':transport.get('duration_ms'),'output_hash':output_hash,'matching_event_seq':matched})
            eventpath=directory/'codex-events.jsonl'
            if eventpath.exists():
                trace=[]
                for n,line in enumerate(eventpath.read_text(errors='replace').splitlines(),1):
                    try:item=json.loads(line)
                    except ValueError:continue
                    entry={'source_line':n,'type':item.get('type')}
                    for k in ('thread_id','usage','error','message'):
                        if k in item:entry[k]=item[k]
                    if isinstance(item.get('item'),dict):
                        entry['item_type']=item['item'].get('type')
                        entry['item_id']=item['item'].get('id')
                    trace.append(entry)
                exp.write(f'{rid}/{relative}/codex-trace.json',trace,eventpath,'event metadata; item text/reasoning/commands omitted; validated final output is output.json')
        exp.write(f'{rid}/agent-attempts.json',attempts)
        for name in ('status.json','plan.json','bindings.json','static-validation.json','pipeline-spec.json','pipeline.py','run_pipeline.py',
                     'runtime-report.json','verification.json','metrics.json','traces.json','result.json','integrity.json','revision.json'):
            exp.copy(root/name,f'{rid}/{name}')
        for path in (root/'custom').glob('*.py'):exp.copy(path,f'{rid}/custom/{path.name}')
        exp.write(f'{rid}/events.json',ev, note='read-only SQLite snapshot; seq retained, detail redacted')
        exp.write(f'{rid}/skill-events.json',[e for e in ev if e['event']=='skill.invoked'])
        exp.write(f'{rid}/tool-events.json',[e for e in ev if e['event']=='tool.called'])
        skill_rows=[]
        for path in sorted((ROOT/'.agents/skills').glob('*/SKILL.md')):
            skill=path.parent.name
            historical=sorted({p['skills'][skill] for p in provenance if skill in p.get('skills',{})})
            current_hash=sha(path.read_bytes())
            skill_rows.append({'skill':skill,'invocations':counts[skill],'recorded_hashes':historical,'current_hash':current_hash,
                               'matches_current':bool(historical) and historical==[current_hash]})
        exp.write(f'{rid}/skill-versions.json',skill_rows)
        buf=io.StringIO(); writer=csv.writer(buf); writer.writerow(['seq','time_Asia_Shanghai','agent','job','event','detail'])
        for e in ev:writer.writerow([e['seq'],stamp(e['timestamp']),e['agent'],e['job'],e['event'],json.dumps(e['detail'],ensure_ascii=False)])
        exp.write(f'{rid}/timeline.csv',buf.getvalue())
        # Derive attempt intervals only from matching starts and finishes in events.
        open_jobs={}; intervals=[]
        for e in ev:
            if e['event']=='agent.started':open_jobs[e['job']]=e
            if e['event'] in ('agent.completed','agent.failed') and e['job'] in open_jobs:
                start=open_jobs.pop(e['job']); intervals.append({'job':e['job'],'role':e['agent'],'start_seq':start['seq'],'end_seq':e['seq'],
                    'start':start['timestamp'],'end':e['timestamp'],'outcome':e['event']})
        overlaps=[(a['job'],b['job']) for i,a in enumerate(intervals) for b in intervals[i+1:]
                  if a['role']==b['role']=='operator_specialist' and a['start']<b['end'] and b['start']<a['end']]
        exp.write(f'{rid}/agent-intervals.json',{'intervals':intervals,'overlapping_specialist_jobs':overlaps})
        summary={'run_id':rid,'state':status.get('state'),'backend':next((p.get('backend') for p in provenance if p.get('backend')),status.get('backend','unknown')),
                 'events':len(ev),'skill_invocations':sum(counts.values()),'agent_completed':sum(e['event']=='agent.completed' for e in ev),
                 'agent_failed':sum(e['event']=='agent.failed' for e in ev),'runtime':runtime.get('status','not recorded'),
                 'output_rows':runtime.get('rows'),'metrics':metrics,'skill_counts':dict(counts),'overlap_pairs':len(overlaps)}
        overview.append(summary)
        details=[]
        for e in ev:
            if e['event'] in ('skill.invoked','agent.started','agent.completed','agent.failed','workflow.state','execution.requested'):
                detail=e['detail']; label=detail.get('skill') or detail.get('state') or detail.get('error') or detail.get('output_hash','')
                details.append([e['seq'],stamp(e['timestamp']),e['agent'],e['job'],e['event'],str(label)[:240]])
        md=f'# {rid}\n\n状态：**{summary["state"]}**；backend：{summary["backend"]}。采集时间：{now.isoformat()}。\n\n'
        md+='## Skill 调用与版本\n\n'+md_table(['Skill','skill.invoked 次数','运行记录 SHA-256','与当前文件一致'],[[x['skill'],x['invocations'],', '.join(x['recorded_hashes']),x['matches_current']] for x in skill_rows])
        md+='\n## Agent 轨迹\n\n'+md_table(['Job','Attempt','角色','模型','退出码','耗时 ms','匹配的完成事件 seq'],[[a['job'],a['attempt'],a['role'],a['model'],a['exit_code'],a['duration_ms'],a['matching_event_seq']] for a in attempts])
        md+='\n输出通过 canonical JSON SHA-256 与 agent.completed.output_hash 关联。空匹配不声称对应历史事件；同一 job/attempt 在 resume 时可能被覆盖，保留事件记录与当前文件的区别。\n'
        md+=f'\n检测到 {len(overlaps)} 对 Specialist 执行区间重叠，详见 [agent-intervals.json](agent-intervals.json)。耗时来自真实时间戳，没有推算“效率提升百分比”。\n'
        md+='\n## 关键事件\n\n'+md_table(['seq','北京时间','Agent','Job','事件','摘要（截取）'],details)
        md+='\n完整事件与错误：[events.json](events.json)；可导入表格的时间线：[timeline.csv](timeline.csv)。\n'
        exp.write(f'{rid}/README.md',md)
        def h(v):return html.escape(str(v if v is not None else '—'))
        rows=''.join('<tr>'+''.join(f'<td>{h(v)}</td>' for v in row)+'</tr>' for row in details)
        skillhtml=''.join(f'<tr><td>{h(x["skill"])}</td><td>{x["invocations"]}</td><td><code>{h(", ".join(x["recorded_hashes"]))}</code></td></tr>' for x in skill_rows)
        links=' · '.join(f'<a href="{rid}/{n}">{n}</a>' for n in ('README.md','skill-events.json','timeline.csv','agent-attempts.json','skill-versions.json'))
        span_chart=''
        if intervals:
            start=min(x['start'] for x in intervals); duration=max(x['end'] for x in intervals)-start or 1
            for x in intervals:
                span_chart+=f'<div class="span-row"><span>{h(x["job"])} · #{x["start_seq"]}–{x["end_seq"]}</span><div class="track"><i style="left:{100*(x["start"]-start)/duration:.2f}%;width:{max(.3,100*(x["end"]-x["start"])/duration):.2f}%;background:{"#dc7356" if x["outcome"]=="agent.failed" else "#368579"}"></i></div></div>'
        pages.append(f'<section id="{rid}"><h2>{rid} <small>{h(summary["state"])}</small></h2><p>{links}</p><p>模型进程成功事件 {summary["agent_completed"]} · 失败尝试 {summary["agent_failed"]} · Skill 事件 {summary["skill_invocations"]} · Runtime {h(summary["runtime"])} · 输出 {h(summary["output_rows"])} 行</p><h3>Agent 执行区间（真实事件时间戳）</h3>{span_chart}<h3>Skills 与版本</h3><table><tr><th>Skill</th><th>次数</th><th>运行时 SHA-256</th></tr>{skillhtml}</table><details><summary>展开关键事件与失败证据</summary><table><tr><th>seq</th><th>北京时间</th><th>Agent</th><th>Job</th><th>事件</th><th>摘要</th></tr>{rows}</table></details></section>')
    for path in sorted((ROOT/'.agents/skills').glob('*/SKILL.md')):exp.copy(path,'skills-current/'+path.parent.name+'/SKILL.md')
    logpaths=[Path('/tmp/dataflow-mul-agents-web.log'),Path('/tmp/dataflow-http-fix.log')]+sorted((ROOT/'runs/.service-logs').glob('*.log'))
    log_summary=[]
    for i,path in enumerate(logpaths):
        if not path.exists():continue
        lines=path.read_text(errors='replace').splitlines()
        errors=[(j+1,line) for j,line in enumerate(lines) if re.search(r'ERROR|Traceback|Exception|" 5\d\d ',line)]
        exp.write(f'logs/{i}-{path.name}.tail.txt','\n'.join(f'{j+1}: {line}' for j,line in enumerate(lines) if j>=len(lines)-200),path,'last 200 lines with original line numbers')
        exp.write(f'logs/{i}-{path.name}.errors.txt','\n'.join(f'{j}: {line}' for j,line in errors[-100:]),path,'last 100 matching error lines; not full stack traces')
        log_summary.append({'source':str(path),'lines':len(lines),'matching_errors':len(errors)})
    exp.write('logs/index.json',log_summary)
    exp.write('overview.json',overview)
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    exp.write('collection.json',{'collected_at':now.isoformat(),'git_head_at_collection':head,'git_status':subprocess.check_output(['git','status','--short'],cwd=ROOT,text=True),
                               'run_ids':args.runs,'source':'read-only SQLite snapshots; no runs executed or modified','timezone':'Asia/Shanghai'})
    report='''# DataFlow 多 Agent 真实运行证据包

本包来自本地历史运行，只读采集，没有为展示新造执行结果。打开 **index.html** 可离线展示时间线，点击链接可查看事件与结构化 Agent 输出。

## 建议演示顺序

1. 打开每个 Run 的当前状态、Agent 时间区间和 Skill 调用次数，说明规划、并行绑定与汇合。
2. 查看 `agent-attempts.json`，从输出 hash 追溯到 `agent.completed` 事件和当前 `output.json`。
3. 有实际执行时展示 `runtime-report.json`；只有 status=passed、executed=true 才能声称执行成功。
4. 如有失败，展开对应事件与传输日志；如有 Verifier，展示其真实 verdict。不要从缺失文件推断成功。

## 证据如何串起来

`team.sqlite/events.seq` → `skill.invoked` / `agent.started` → `agents/<job>/<attempt>/input-summary.json` 中 Skill hash → `transport.json` 的模型、退出码与耗时 → `codex-trace.json` 的原始行号与事件类型 → `output.json` → `agent.completed.output_hash` → 静态检查 / runtime / verification。

- Skill 次数严格按 `skill.invoked` 事件统计，不把 `tool.called`、模型重试、缓存命中混算成 Skill 调用。
- `skill.invoked` 是编排器记录的工作流事件，不是 Codex 原生技能工具的调用回执。backend.py 实际读取 SKILL.md 并拼入角色指令；input-summary 中 provenance 提供该次运行所记录的版本 hash。
- `operator-scaffolding` 事件在自定义源码落盘时记录；不能从该时间戳推断独立模型调用开始。
- `tool.called` 在本实现中也是编排器工具边界事件，不声称是 Codex 内部 shell/MCP 调用。
- 默认 Web 生成流程止于 READY；手动执行成功为 EXECUTED，不会自动调用 Verifier。是否调用 verification-evidence 以及是否 VERIFIED，应逐个 Run 核对。加速比与语义准确率不能仅由这些事件证明。
- Agent latency 是调用耗时之和，并行调用会重叠，不能直接当作端到端耗时。token 数按已有 metrics 原样保留，不等同于供应商账单。

## 快照与脱敏

仅导出白名单产物；未复制密钥注册表、认证目录、SQLite 原库、完整输入数据或完整 prompt。Codex 轨迹仅保留类型、usage、错误、原始行号，隐藏 item 文本/命令及内部推理；角色最终结构化输出单独保留。输出及 plan 仍可能包含业务示例和模型生成内容，并非全面匿名化。日志是带原始行号的摘录。

`manifest.json` 记录导出文件 hash；原文件 hash 与脱敏文件 hash 分开保存。SQLite 事件为查询快照，没有虚构原始文件 hash。`skills-current/` 是采集时的本地 Skill 文件，只有与运行记录 hash 一致时才能作为对应版本。

## 运行概览

'''
    report+=md_table(['Run','Backend','当前状态','Skill 事件','Agent 完成/失败','Runtime','输出行数'],[[x['run_id'],x['backend'],x['state'],x['skill_invocations'],f'{x["agent_completed"]}/{x["agent_failed"]}',x['runtime'],x['output_rows']] for x in overview])
    report+='\n## 索引\n\n'+''.join(f'- [{x["run_id"]}：时间线、Skill hash、Agent 轨迹]({x["run_id"]}/README.md)\n' for x in overview)
    report+='\n- [后端日志摘录索引](logs/index.json)\n- [采集信息](collection.json)\n- [文件校验清单](manifest.json)\n\n重新采集：`.venv/bin/python scripts/export_demo_evidence.py`。默认导出到被 Git 忽略的 runs/evidence-demo-*，不上传 GitHub。\n'
    exp.write('README.md',report)
    css='''body{font:15px/1.6 system-ui,sans-serif;color:#233244;background:#edf1f4;margin:0}main{max-width:1180px;margin:auto;padding:36px}h1{font-size:32px}h2{font-size:20px}small{color:#527777}section,.intro{background:white;border-radius:12px;padding:24px;margin:22px 0;box-shadow:0 4px 20px #2341}a{color:#226c97}table{border-collapse:collapse;width:100%;font-size:12px;table-layout:fixed}td,th{padding:9px;text-align:left;border-bottom:1px solid #ddd;overflow-wrap:anywhere}code{font-size:11px}summary{cursor:pointer;font-weight:600;margin-top:16px}.span-row{display:grid;grid-template-columns:40% 1fr;gap:10px;font-size:11px;padding:3px 0}.track{position:relative;background:#edf2f2;height:17px}.track i{position:absolute;height:17px;border-radius:3px}.tag{display:inline-block;background:#dae9e6;padding:5px 12px;margin:4px;border-radius:20px}@media print{body{background:white}section{break-inside:avoid;box-shadow:none}details{display:none}}'''
    intro='<div class="intro"><h1>DataFlow · 真实协作证据</h1><p>历史运行快照 · '+now.strftime('%Y-%m-%d %H:%M %Z')+'</p><p>规划 → 并发算子绑定 → 字段整合 → 执行证据；各阶段是否发生以实际事件为准。</p><p><strong>READY ≠ EXECUTED ≠ VERIFIED。</strong>Skill 调用为编排器记录；不展示内部推理，不伪造四角色全部成功。</p><a href="README.md">讲解稿、证据口径及文件索引</a><p>'+''.join(f'<span class="tag">{html.escape(k)} · {v}</span>' for k,v in sorted(all_counts.items()))+'</p></div>'
    exp.write('index.html','<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>DataFlow 运行证据</title><style>'+css+'</style><main>'+intro+''.join(pages)+'</main></html>')
    (dest/'manifest.json').write_text(exp.clean(json.dumps(exp.manifest,ensure_ascii=False,indent=2))+'\n',encoding='utf-8')
    archive=dest.with_suffix('.zip')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for path in sorted(dest.rglob('*')):
            if path.is_file():z.write(path,str(path.relative_to(dest.parent)))
    print(json.dumps({'directory':str(dest),'zip':str(archive),'files':len(exp.manifest)+1,'skill_event_counts':dict(all_counts)},ensure_ascii=False))


if __name__=='__main__':main()
