from __future__ import annotations
import argparse, getpass, json, os
from pathlib import Path
from .animator import AnimatorService
from .director import AutonomyPolicy, DirectorService, plan_work
from .filmmaker import render, write_edit_plan
from .io import load_package, save_json
from .package_ops import (
    add_reference, approve_asset, register_asset, reject_asset, remove_reference,
    set_frame_plan, set_prompt,
)
from .providers import FalProvider, MockProvider
from .local_config import LocalSecretStore
from .storyboard import build_storyboard
from .telemetry import TelemetrySink
from .puppeteer_bridge import dispatch

def _provider(name: str, output_dir: str):
    if name=='mock': return MockProvider(output_dir)
    if name=='fal': return FalProvider()
    raise ValueError(name)

def _keys(args):
    s=LocalSecretStore()
    if args.action=='set':
        value=args.value or getpass.getpass(f'{args.name}: '); print(f'stored {args.name} in {s.set(args.name,value)}')
    else:
        for name in s.list_names(): print(name)

def _package(args):
    p=load_package(args.path); print(f'valid {p.package_version}: {p.episode_id}; {sum(len(s.shots) for s in p.scenes)} shots')

def _storyboard(args): print(build_storyboard(load_package(args.package),args.out))

def _generate(args):
    p=load_package(args.package); service=AnimatorService(_provider(args.provider,args.output_dir),TelemetrySink(args.telemetry)); assets=service.generate(p,args.shot_id,role=args.role); save_json(args.package,p); print(json.dumps([a.model_dump(mode='json') for a in assets],indent=2))

def _approve(args):
    p=load_package(args.package); approve_asset(p,args.shot_id,args.kind,args.asset_id,TelemetrySink(args.telemetry)); save_json(args.package,p); print(args.asset_id)

def _reject(args):
    p=load_package(args.package); reject_asset(p,args.shot_id,args.asset_id,TelemetrySink(args.telemetry)); save_json(args.package,p); print(args.asset_id)

def _plan(args): print(json.dumps([w.__dict__ for w in plan_work(load_package(args.package))],indent=2))

def _physical(args):
    p=load_package(args.package); command=args.command or os.getenv('FORGE_PUPPETEER_CMD')
    if not command: raise RuntimeError('Set --command or FORGE_PUPPETEER_CMD')
    asset=dispatch(p,args.shot_id,command=command,output=args.out,telemetry=TelemetrySink(args.telemetry)); save_json(args.package,p); print(asset.asset_id)

def _auto(args):
    p=load_package(args.package)
    sink=TelemetrySink(args.telemetry)
    animator=AnimatorService(_provider(args.provider,args.output_dir),sink)
    command=args.puppeteer_command or os.getenv('FORGE_PUPPETEER_CMD')
    physical_executor=None
    if command:
        def physical_executor(package,shot_id):
            out=Path(args.output_dir)/'physical'/f'{shot_id}.json'
            return dispatch(package,shot_id,command=command,output=out,telemetry=sink).asset_id
    director=DirectorService(animator,telemetry=sink,physical_executor=physical_executor)
    policy=AutonomyPolicy(
        auto_approve_storyboards=args.auto_approve_storyboards,
        auto_approve_frames=args.auto_approve_frames,
        auto_approve_clips=args.auto_approve_clips,
        auto_approve_takes=args.auto_approve_takes,
        allow_generated_video=args.allow_video,
        allow_physical_execution=args.allow_physical,
        max_actions=args.max_actions,
    )
    result=director.run_until_blocked(p,policy)
    save_json(args.package,p)
    payload={'status':result.status,'actions_completed':result.actions_completed,'next_work':result.next_work.__dict__ if result.next_work else None}
    print(json.dumps(payload,indent=2))

def _edit_plan(args): print(write_edit_plan(load_package(args.package),args.out))
def _render(args): print(render(load_package(args.package),args.out,ffmpeg=args.ffmpeg,telemetry=TelemetrySink(args.telemetry)))

def _show_shot(args): print(load_package(args.package).find_shot(args.shot_id).model_dump_json(indent=2))

def _set_prompt(args):
    p=load_package(args.package); set_prompt(p,args.shot_id,args.role,args.text); save_json(args.package,p); print(args.shot_id)

def _set_frame(args):
    p=load_package(args.package); set_frame_plan(p,args.shot_id,args.mode,chain_from_shot_id=args.chain_from,start_asset_id=args.start_asset_id,end_asset_id=args.end_asset_id); save_json(args.package,p); print(args.shot_id)

def _asset_add(args):
    p=load_package(args.package); asset=register_asset(p,asset_id=args.asset_id,uri=args.uri,kind=args.kind,status=args.status,authority=args.authority); save_json(args.package,p); print(asset.asset_id)

def _asset_list(args):
    p=load_package(args.package); print(json.dumps([a.model_dump(mode='json') for a in p.assets],indent=2))

def _reference(args):
    p=load_package(args.package)
    (add_reference if args.action=='add' else remove_reference)(p,args.shot_id,args.asset_id)
    save_json(args.package,p); print(args.shot_id)

def build_parser():
    p=argparse.ArgumentParser(prog='forge-studios'); sub=p.add_subparsers(dest='cmd',required=True)
    k=sub.add_parser('keys'); ks=k.add_subparsers(dest='action',required=True); st=ks.add_parser('set'); st.add_argument('name'); st.add_argument('value',nargs='?'); ks.add_parser('list'); k.set_defaults(func=_keys)
    v=sub.add_parser('validate'); v.add_argument('path'); v.set_defaults(func=_package)
    sb=sub.add_parser('storyboard'); sb.add_argument('--package',required=True); sb.add_argument('--out',required=True); sb.set_defaults(func=_storyboard)
    g=sub.add_parser('generate'); g.add_argument('--package',required=True); g.add_argument('--shot-id',required=True); g.add_argument('--role',choices=['storyboard','start_frame','end_frame','video'],default='storyboard'); g.add_argument('--provider',choices=['mock','fal'],default='mock'); g.add_argument('--output-dir',default='outputs'); g.add_argument('--telemetry',default='.agenticforge/telemetry.jsonl'); g.set_defaults(func=_generate)
    a=sub.add_parser('approve'); a.add_argument('--package',required=True); a.add_argument('--shot-id',required=True); a.add_argument('--kind',choices=['storyboard','start_frame','end_frame','clip','take'],required=True); a.add_argument('--asset-id',required=True); a.add_argument('--telemetry',default='.agenticforge/telemetry.jsonl'); a.set_defaults(func=_approve)
    rj=sub.add_parser('reject'); rj.add_argument('--package',required=True); rj.add_argument('--shot-id',required=True); rj.add_argument('--asset-id',required=True); rj.add_argument('--telemetry',default='.agenticforge/telemetry.jsonl'); rj.set_defaults(func=_reject)
    d=sub.add_parser('plan'); d.add_argument('--package',required=True); d.set_defaults(func=_plan)
    au=sub.add_parser('auto'); au.add_argument('--package',required=True); au.add_argument('--provider',choices=['mock','fal'],default='mock'); au.add_argument('--output-dir',default='outputs'); au.add_argument('--telemetry',default='.agenticforge/telemetry.jsonl'); au.add_argument('--auto-approve-storyboards',action='store_true'); au.add_argument('--auto-approve-frames',action='store_true'); au.add_argument('--auto-approve-clips',action='store_true'); au.add_argument('--auto-approve-takes',action='store_true'); au.add_argument('--allow-video',action='store_true'); au.add_argument('--allow-physical',action='store_true'); au.add_argument('--puppeteer-command'); au.add_argument('--max-actions',type=int,default=100); au.set_defaults(func=_auto)
    ph=sub.add_parser('physical'); ph.add_argument('--package',required=True); ph.add_argument('--shot-id',required=True); ph.add_argument('--command'); ph.add_argument('--out',required=True); ph.add_argument('--telemetry',default='.agenticforge/telemetry.jsonl'); ph.set_defaults(func=_physical)
    ep=sub.add_parser('edit-plan'); ep.add_argument('--package',required=True); ep.add_argument('--out',required=True); ep.set_defaults(func=_edit_plan)
    rr=sub.add_parser('render'); rr.add_argument('--package',required=True); rr.add_argument('--out',required=True); rr.add_argument('--ffmpeg',default='ffmpeg'); rr.add_argument('--telemetry',default='.agenticforge/telemetry.jsonl'); rr.set_defaults(func=_render)

    ss=sub.add_parser('show-shot'); ss.add_argument('--package',required=True); ss.add_argument('--shot-id',required=True); ss.set_defaults(func=_show_shot)
    sp=sub.add_parser('set-prompt'); sp.add_argument('--package',required=True); sp.add_argument('--shot-id',required=True); sp.add_argument('--role',choices=['image','storyboard','start_frame','end_frame','video'],required=True); sp.add_argument('--text',required=True); sp.set_defaults(func=_set_prompt)
    sf=sub.add_parser('set-frame-plan'); sf.add_argument('--package',required=True); sf.add_argument('--shot-id',required=True); sf.add_argument('--mode',choices=['still','start_only','start_and_end','chained_start'],required=True); sf.add_argument('--chain-from'); sf.add_argument('--start-asset-id'); sf.add_argument('--end-asset-id'); sf.set_defaults(func=_set_frame)
    aa=sub.add_parser('asset-add'); aa.add_argument('--package',required=True); aa.add_argument('--asset-id',required=True); aa.add_argument('--uri',required=True); aa.add_argument('--kind',default='reference_image'); aa.add_argument('--status',default='canon'); aa.add_argument('--authority',default='locked'); aa.set_defaults(func=_asset_add)
    al=sub.add_parser('asset-list'); al.add_argument('--package',required=True); al.set_defaults(func=_asset_list)
    rf=sub.add_parser('reference'); rf.add_argument('action',choices=['add','remove']); rf.add_argument('--package',required=True); rf.add_argument('--shot-id',required=True); rf.add_argument('--asset-id',required=True); rf.set_defaults(func=_reference)
    return p

def main(argv=None):
    args=build_parser().parse_args(argv); return args.func(args) or 0
if __name__=='__main__': raise SystemExit(main())
