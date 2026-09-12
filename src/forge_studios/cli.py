from __future__ import annotations
import argparse, getpass, json, os
from .animator import AnimatorService
from .director import plan_work
from .filmmaker import render, write_edit_plan
from .io import load_package, save_json
from .package_ops import approve_asset, reject_asset
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

def _storyboard(args):
    print(build_storyboard(load_package(args.package),args.out))

def _generate(args):
    p=load_package(args.package); service=AnimatorService(_provider(args.provider,args.output_dir),TelemetrySink(args.telemetry)); assets=service.generate(p,args.shot_id,role=args.role); save_json(args.package,p); print(json.dumps([a.model_dump(mode='json') for a in assets],indent=2))

def _approve(args):
    p=load_package(args.package); approve_asset(p,args.shot_id,args.kind,args.asset_id,TelemetrySink(args.telemetry)); save_json(args.package,p); print(args.asset_id)

def _reject(args):
    p=load_package(args.package); reject_asset(p,args.shot_id,args.asset_id,TelemetrySink(args.telemetry)); save_json(args.package,p); print(args.asset_id)

def _plan(args):
    print(json.dumps([w.__dict__ for w in plan_work(load_package(args.package))],indent=2))

def _physical(args):
    p=load_package(args.package); command=args.command or os.getenv('FORGE_PUPPETEER_CMD')
    if not command: raise RuntimeError('Set --command or FORGE_PUPPETEER_CMD')
    asset=dispatch(p,args.shot_id,command=command,output=args.out,telemetry=TelemetrySink(args.telemetry)); save_json(args.package,p); print(asset.asset_id)

def _edit_plan(args): print(write_edit_plan(load_package(args.package),args.out))
def _render(args): print(render(load_package(args.package),args.out,ffmpeg=args.ffmpeg,telemetry=TelemetrySink(args.telemetry)))

def build_parser():
    p=argparse.ArgumentParser(prog='forge-studios'); sub=p.add_subparsers(dest='cmd',required=True)
    k=sub.add_parser('keys'); ks=k.add_subparsers(dest='action',required=True); st=ks.add_parser('set'); st.add_argument('name'); st.add_argument('value',nargs='?'); ks.add_parser('list'); k.set_defaults(func=_keys)
    v=sub.add_parser('validate'); v.add_argument('path'); v.set_defaults(func=_package)
    sb=sub.add_parser('storyboard'); sb.add_argument('--package',required=True); sb.add_argument('--out',required=True); sb.set_defaults(func=_storyboard)
    g=sub.add_parser('generate'); g.add_argument('--package',required=True); g.add_argument('--shot-id',required=True); g.add_argument('--role',choices=['storyboard','start_frame','end_frame','video'],default='storyboard'); g.add_argument('--provider',choices=['mock','fal'],default='mock'); g.add_argument('--output-dir',default='outputs'); g.add_argument('--telemetry',default='.agenticforge/telemetry.jsonl'); g.set_defaults(func=_generate)
    a=sub.add_parser('approve'); a.add_argument('--package',required=True); a.add_argument('--shot-id',required=True); a.add_argument('--kind',choices=['storyboard','start_frame','end_frame','clip','take'],required=True); a.add_argument('--asset-id',required=True); a.add_argument('--telemetry',default='.agenticforge/telemetry.jsonl'); a.set_defaults(func=_approve)
    rj=sub.add_parser('reject'); rj.add_argument('--package',required=True); rj.add_argument('--shot-id',required=True); rj.add_argument('--asset-id',required=True); rj.add_argument('--telemetry',default='.agenticforge/telemetry.jsonl'); rj.set_defaults(func=_reject)
    d=sub.add_parser('plan'); d.add_argument('--package',required=True); d.set_defaults(func=_plan)
    ph=sub.add_parser('physical'); ph.add_argument('--package',required=True); ph.add_argument('--shot-id',required=True); ph.add_argument('--command'); ph.add_argument('--out',required=True); ph.add_argument('--telemetry',default='.agenticforge/telemetry.jsonl'); ph.set_defaults(func=_physical)
    ep=sub.add_parser('edit-plan'); ep.add_argument('--package',required=True); ep.add_argument('--out',required=True); ep.set_defaults(func=_edit_plan)
    rr=sub.add_parser('render'); rr.add_argument('--package',required=True); rr.add_argument('--out',required=True); rr.add_argument('--ffmpeg',default='ffmpeg'); rr.add_argument('--telemetry',default='.agenticforge/telemetry.jsonl'); rr.set_defaults(func=_render)
    return p

def main(argv=None):
    args=build_parser().parse_args(argv); return args.func(args) or 0
if __name__=='__main__': raise SystemExit(main())
