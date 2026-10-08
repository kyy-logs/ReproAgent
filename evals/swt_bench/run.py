"""Serial, denominator-preserving runs using the existing reproduction agent."""
import asyncio
import re
import subprocess
from dataclasses import asdict
from pathlib import Path

from reproagent.app import LEGACY_BACKENDS
from reproagent.core.models import INFRASTRUCTURE, BudgetLimits, installed_agentscope_version
from reproagent.core.serialization import canonical_hash
from ..run import run_case
from .io import fresh_dir,seal,verify_seal,write_json
from .prepare import validate_binding,preflight,overlap
from .predictions import candidate_patch,write_predictions
from .provenance import ToolSourceChanged,assert_tool_source,capture_tool_source
from .results import save_summary

# `evals/` is not shipped in the wheel, so a round always runs from a checkout.
TOOL_ROOT=Path(__file__).resolve().parents[2]


async def run_batch(catalog,manifest,bindings,model,output,*,limits=None,model_backend=None,agent_backend=None,
                    run_one=None,cancel_event=None,protected_roots=(),model_name=None,tool_root=None,experience_file=None):
    verify_seal(catalog,'catalog_hash'); verify_seal(manifest,'manifest_hash')
    if manifest['catalog_hash']!=catalog['catalog_hash'] or manifest['source']!=catalog['source']:
        raise ValueError('manifest/catalog mismatch')
    # The two old flags are deprecated aliases of the one infrastructure; both are accepted
    # so a recorded command keeps running, and neither selects a runtime.
    for flag in (model_backend,agent_backend):
        if flag is not None and flag not in LEGACY_BACKENDS: raise ValueError('invalid backend')
    rows={row['instance_id']:row for row in catalog['entries']}
    identities=[case['instance_id'] for case in manifest['cases']]
    if len(set(identities))!=len(identities) or not identities or set(identities)-set(rows) or set(bindings)-set(identities):
        raise ValueError('invalid selected or bound IDs')
    for selected in manifest['cases']:
        if any(rows[selected['instance_id']][key]!=selected[key] for key in ('repo','base_commit','description_hash')):
            raise ValueError('selected source identity mismatch')
    limits=limits or BudgetLimits(); cancel_event=cancel_event or asyncio.Event(); run_one=run_one or run_case
    destination=Path(output).resolve(); model_name=model_name or f'reproagent-{INFRASTRUCTURE}'
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',destination.name) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',model_name):
        raise ValueError('run and model labels must be simple names')
    for binding in bindings.values():
        for key in ('buggy_repo','fixed_repo'):
            if key in binding and overlap(destination,binding[key]): raise ValueError('output overlaps a target repository')
    experience_path = Path(experience_file).resolve() if experience_file is not None else None
    experience_hash = ""
    def experience_changed():
        if experience_path is None:
            return False
        from reproagent.experience import load_experience_snapshot
        current = load_experience_snapshot(experience_path)
        return current.state == "store_error" or current.content_hash != experience_hash
    if experience_path is not None:
        from reproagent.experience import load_experience_snapshot
        if experience_path.is_relative_to(destination) or any(experience_path.is_relative_to(Path(binding[key]).resolve())
            for binding in bindings.values() for key in ("buggy_repo", "fixed_repo") if key in binding):
            raise ValueError("experience library overlaps evaluation or target repository")
        snapshot = load_experience_snapshot(experience_path)
        if snapshot.state == "store_error":
            raise ValueError("experience library unavailable")
        experience_hash = snapshot.content_hash
    output=fresh_dir(destination)
    tool_root=Path(tool_root).resolve() if tool_root is not None else TOOL_ROOT
    tool_source=capture_tool_source(tool_root)
    # The round records what it ran on, not which deprecated flag selected it.  The SDK
    # version is recorded here too, so a summary written later states the version *this*
    # round ran on instead of whatever happens to be installed when it is summarised.
    configuration={'model':asdict(model),'limits':asdict(limits),'model_backend':INFRASTRUCTURE,'agent_backend':INFRASTRUCTURE}
    if experience_path is not None:
        configuration.update(experience_file=str(experience_path), experience_library_hash=experience_hash, learn_experience=False)
    version=installed_agentscope_version()
    if version: configuration['agentscope_version']=version
    round_data={'schema_version':1,'run_id':output.name,'model_name':model_name,'manifest':manifest,
        'catalog_hash':catalog['catalog_hash'],'harness_commit':catalog['source']['harness_commit'],
        'configuration':configuration,'configuration_hash':canonical_hash(configuration),'bindings_hash':canonical_hash(bindings),
        'tool_source':tool_source,'source_comparison_status':'verified','outcomes':{}}
    prepared={}
    for identity in identities:
        outcome={'status':'NOT_RUN','evidence_level':'NONE','official_status':'not_run','official_resolved':None,
                 'human_judgement':None,'export_replayed':None,'cost_kind':'unknown','preparation':{'status':'pending','reason':'environment binding missing'}}
        if identity in bindings:
            try:
                case=validate_binding(rows[identity],bindings[identity],protected_roots=(*protected_roots,output))
                receipt=preflight(case); outcome['preparation']=receipt
                if receipt['status']=='ready' and case.review_status=='approved': prepared[identity]=case
                elif receipt['status']=='ready': outcome['preparation']={'status':'pending','reason':'technical review pending'}
            except (ValueError,KeyError,TypeError,OSError,subprocess.SubprocessError):
                outcome['preparation']={'status':'preparation_error','reason':'invalid or changed environment binding'}
        round_data['outcomes'][identity]=outcome
    write_json(output/'manifest.json',manifest); write_json(output/'bindings.json',bindings)
    def save():
        sealed=seal(round_data,'round_hash'); write_json(output/'round.json',sealed); save_summary(output,sealed)
        return sealed
    save()
    for identity in identities:
        outcome=round_data['outcomes'][identity]
        if cancel_event.is_set(): outcome['status']='CANCELLED'; save(); continue
        try:
            assert_tool_source(tool_root,tool_source)
        except ToolSourceChanged:
            # The tool changed mid-round: keep every case in the denominator, start
            # no later case, and mark the round unusable for version comparison.
            round_data['source_comparison_status']='changed'
            for pending in identities:
                if pending in prepared and round_data['outcomes'][pending]['status']=='NOT_RUN':
                    round_data['outcomes'][pending].update(status='SOURCE_CHANGED',stop_reason='TOOL_SOURCE_CHANGED')
            save(); break
        if experience_changed():
            round_data["source_comparison_status"] = "changed"
            round_data["experience_comparison_status"] = "changed"
            for pending in identities:
                if round_data["outcomes"][pending]["status"] == "NOT_RUN":
                    round_data["outcomes"][pending].update(status="EXPERIENCE_SOURCE_CHANGED",
                        stop_reason="EXPERIENCE_SOURCE_CHANGED")
            save()
            break
        if identity not in prepared: outcome['status']='NOT_PREPARED'; save(); continue
        case=prepared[identity]
        try:
            run_options = dict(limits=limits, model_backend=model_backend, agent_backend=agent_backend, cancel_event=cancel_event)
            if experience_path is not None:
                run_options.update(experience_file=experience_path, learn_experience=False)
            result=await run_one(case,model,output/'cases'/identity,**run_options)
            outcome.update(asdict(result))
            if result.status=='DONE':
                try: outcome['prediction_patch']=candidate_patch(output/'cases'/identity/'task',case)
                except (ValueError,OSError,KeyError): outcome['prediction_export_error']='invalid or changed accepted candidate'
        except asyncio.CancelledError:
            cancel_event.set(); outcome['status']='CANCELLED'
        except Exception:
            outcome.update(status='FAILED',stop_reason='EVALUATION_RUN_ERROR')
        save()
    try:
        assert_tool_source(tool_root,tool_source)
    except ToolSourceChanged:
        round_data['source_comparison_status']='changed'
    if experience_changed():
        round_data['source_comparison_status']='changed'
        round_data['experience_comparison_status']='changed'
    receipt=write_predictions(manifest,round_data['outcomes'],model_name,output/'predictions.jsonl')
    round_data['predictions_hash']=receipt['predictions_hash']
    from .official import official_command
    from reproagent.store import atomic_write
    atomic_write(output/'official-command.sh',official_command(output,round_data).encode())
    return save()
