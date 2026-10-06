import argparse
import asyncio
import json
import sys
from pathlib import Path

from reproagent.core.models import BudgetLimits,ModelConfig
from .io import read_json,write_json,fresh_dir


def main(argv=None):
    parser=argparse.ArgumentParser(prog='python -m evals.swt_bench')
    commands=parser.add_subparsers(dest='command',required=True)
    fetch=commands.add_parser('fetch'); fetch.add_argument('--output',required=True); fetch.add_argument('--decoder-python')
    convert=commands.add_parser('import'); convert.add_argument('--snapshot',required=True); convert.add_argument('--filter',required=True)
    convert.add_argument('--source',required=True); convert.add_argument('--output',required=True)
    run=commands.add_parser('run'); run.add_argument('--catalog',required=True); run.add_argument('--manifest',required=True)
    run.add_argument('--bindings',required=True); run.add_argument('--model-config',required=True); run.add_argument('--output',required=True)
    run.add_argument('--model-backend',choices=('native','agentscope'),default='native'); run.add_argument('--agent-backend',choices=('native','agentscope'),default='native')
    run.add_argument('--limits'); run.add_argument('--name')
    preflight=commands.add_parser('preflight'); preflight.add_argument('--catalog',required=True); preflight.add_argument('--manifest',required=True)
    preflight.add_argument('--bindings',required=True); preflight.add_argument('--output',required=True)
    reports=commands.add_parser('import-reports'); reports.add_argument('--round',required=True); reports.add_argument('--reports',required=True); reports.add_argument('--receipt')
    official=commands.add_parser('official-run'); official.add_argument('--round',required=True); official.add_argument('--harness',required=True); official.add_argument('--python'); official.add_argument('--snapshot',required=True)
    summarize=commands.add_parser('summarize'); summarize.add_argument('--round',required=True)
    args=parser.parse_args(argv)
    try:
        if args.command=='fetch':
            from ..datasets.fetch_swt import fetch_snapshot
            print(json.dumps(fetch_snapshot(args.output,args.decoder_python)))
        elif args.command=='import':
            from ..datasets.swt_bench import load_snapshot,select_dev
            catalog=load_snapshot(args.snapshot,args.filter,read_json(args.source)); manifest=select_dev(catalog)
            output=fresh_dir(args.output); write_json(output/'catalog.json',catalog); write_json(output/'dev20.json',manifest)
            print(json.dumps({'total':catalog['total_rows'],'eligible':len(catalog['entries']),'selected':len(manifest['cases'])}))
        elif args.command in ('run','preflight'):
            bindings_path=Path(args.bindings).resolve(); raw=read_json(bindings_path)
            bindings={}
            for identity,binding in raw.items():
                binding=dict(binding)
                for key in ('buggy_repo','fixed_repo','buggy_python','fixed_python'):
                    if key in binding: binding[key]=str((bindings_path.parent/binding[key]).resolve())
                bindings[identity]=binding
            if args.command=='preflight':
                from .prepare import inspect_bindings,overlap
                output=Path(args.output).resolve()
                if any(overlap(output,binding[key]) for binding in bindings.values() for key in ('buggy_repo','fixed_repo') if key in binding):
                    raise ValueError('output overlaps target repository')
                receipts=inspect_bindings(read_json(args.catalog),read_json(args.manifest),bindings,
                    protected_roots=(Path(args.catalog).resolve().parent,bindings_path,output))
                output=fresh_dir(output); write_json(output/'preparation.json',receipts)
                print(json.dumps({'all_tasks':len(receipts),'ready':sum(value['status']=='ready' for value in receipts.values())}))
                return 0
            from .run import run_batch
            model=ModelConfig(**read_json(args.model_config)); limits=BudgetLimits(**read_json(args.limits)) if args.limits else BudgetLimits()
            data=asyncio.run(run_batch(read_json(args.catalog),read_json(args.manifest),bindings,model,args.output,
                limits=limits,model_backend=args.model_backend,agent_backend=args.agent_backend,model_name=args.name,
                protected_roots=(Path(args.catalog).resolve().parent,bindings_path)))
            from .results import summarize_round
            print(json.dumps(summarize_round(data),ensure_ascii=False))
        elif args.command=='import-reports':
            from .results import import_reports,summarize_round
            print(json.dumps(summarize_round(import_reports(args.round,args.reports,read_json(args.receipt) if args.receipt else None))))
        elif args.command=='official-run':
            from .official import run_official
            run_official(args.round,args.harness,args.python,args.snapshot)
        else:
            from .results import save_summary
            from .io import verify_seal
            data=read_json(Path(args.round)/'round.json'); verify_seal(data,'round_hash')
            print(json.dumps(save_summary(args.round,data)))
        return 0
    except KeyboardInterrupt: return 130
    except (ValueError,KeyError,TypeError,OSError) as error:
        print('evaluation configuration or input failed: '+type(error).__name__,file=sys.stderr)
        return 2


if __name__=='__main__': sys.exit(main())
