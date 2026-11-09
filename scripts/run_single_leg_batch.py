"""Run OpenSim 4.5 single-leg CMC from verified prescribed-pelvis SO handoffs."""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET

import numpy as np
import opensim as osim
import pandas as pd


PREFIX = 'SingleLeg_CMC'
WARMUP = .030
DT = .010
TOL = 1e-8
PELVIS = {'pelvis_tx', 'pelvis_ty', 'pelvis_tz', 'pelvis_tilt', 'pelvis_list', 'pelvis_rotation'}
TRANSLATIONS = {'pelvis_tx', 'pelvis_ty', 'pelvis_tz'}
CALF = ('soleus', 'med_gas', 'lat_gas', 'tib_ant', 'tib_post', 'per_long')


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def save(path, data):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    temp.replace(path)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_hashes(protected, cache=None):
    cache = {} if cache is None else cache
    for p, expected in protected.items():
        if p not in cache:
            cache[p] = sha(p)
        if cache[p] != expected:
            raise ValueError(f'Input checksum mismatch: {p}')


def table(path):
    lines = path.read_text(encoding='utf-8-sig').splitlines()
    k = next(i for i, line in enumerate(lines) if line.strip().lower() == 'endheader')
    names = lines[k+1].split()
    values = np.loadtxt(lines[k+2:], ndmin=2)
    if values.shape[1] != len(names) or not np.isfinite(values).all() or (np.diff(values[:, 0]) < -1e-12).any():
        raise ValueError(f'Invalid storage time or data: {path}')
    # Keep the last report at a duplicate native integrator timestamp.
    _, indices = np.unique(values[::-1, 0], return_index=True)
    return names, values[len(values)-1-indices], any('indegrees=yes' in s.replace(' ', '').lower() for s in lines[:k])


def signal(columns, data, name, times):
    return np.interp(times, data[:, 0], data[:, columns.index(name)])


def preflight(case, base, cache):
    paths = {k: (base/case[k]).resolve(strict=True) for k in ('so_folder', 'contact_audit', 'full_wrenches', 'id_reference')}
    source = paths['so_folder']
    prep, audit = (read(source/n) for n in ('single_leg_prepared.json', 'input_audit.json'))
    side, plate = case['side'], case['plate']
    trial, stance = case['trial'], case['stance']
    assert side in ('L', 'R') and prep['side'] == side and prep['plate'] == plate
    assert audit['target_contact_verified'] and audit['selection']['trial'] == trial and audit['selection']['stance'] == stance
    assert audit['selection']['side'] == side and audit['selection']['plate'] == plate
    model, motion, loads = [Path(prep[k]).resolve(strict=True) for k in ('model', 'motion', 'loads')]
    assert all(p.parent == source for p in (model, motion, loads))
    tree = ET.parse(loads)
    (ext,) = tree.findall('.//ExternalForce')
    assert ext.findtext('applied_to_body') == 'calcn_' + side.lower()
    force_path = (loads.parent/tree.findtext('.//datafile')).resolve(strict=True)
    assert force_path.parent == source
    fn, f, _ = table(force_path)
    target_fz = f'ground_force_{plate}_vy'
    assert target_fz in fn and len(fn) == 10 and all(f'_{plate}_' in n for n in fn[1:])
    for key, prefix in (('force', f'ground_force_{plate}_v'), ('point', f'ground_force_{plate}_p'), ('torque', f'ground_torque_{plate}_')):
        assert ext.findtext(key+'_identifier') == prefix and all(prefix+x in fn for x in 'xyz')
    events = read(paths['contact_audit'])['phases']
    target_event = audit['target_20N_window_s']
    event, = [x for x in events if x['side'] == side and x['plate'] == plate and np.allclose(x['target_window_s'], target_event, atol=TOL, rtol=0)]
    witness = event['contact_witnesses'][side]
    assert not witness['user_swing_assumption'] and not event['event']['censored_start'] and not event['event']['censored_end']
    t = np.asarray(prep['expected_times_s'], float)
    first, last = float(t[0]), float(t[-1])
    rate, origin, first_frame = (audit[k] for k in ('marker_rate_hz','original_time_origin_s','original_first_frame'))
    assert rate > 0 and rate == event['point_rate_hz'] and first_frame == event['source_first_original_frame']
    assert np.allclose(np.diff(t), 1/rate, rtol=0, atol=TOL)
    assert target_event[0]-TOL<=first<last<=target_event[1]+TOL and f[0,0]<=first and f[-1,0]>=last-TOL
    interval, = [x for x in witness['complete_intervals'] if x['start_s']<=first+TOL and x['end_s']>=last-TOL]
    rn, raw, _ = table(paths['full_wrenches'])
    raw_fz = raw[:, rn.index(target_fz)]
    assert raw[0,0]<=first and raw[-1,0]>=last-TOL
    assert (raw_fz[(raw[:,0]>=first-TOL)&(raw[:,0]<=last+TOL)]>=19.99).all()
    warm_start = round(first-WARMUP, 9)
    precontact = (raw[:,0]>=warm_start-TOL)&(raw[:,0]<target_event[0]-TOL)
    context_verified = (interval['start_s']<=warm_start+TOL and f[0,0]<=warm_start and raw[0,0]<=warm_start
                        and (raw_fz[precontact]<20.01).all())
    start = warm_start if context_verified else first
    eligible = t[(t>=start+WARMUP-TOL)&(t<=last-DT+TOL)]
    if len(eligible)<3:
        raise ValueError('Fewer than three common-grid samples after initialization and prediction window')
    mn, motion_table, degrees = table(motion)
    assert degrees and motion_table[0,0]<=start and motion_table[-1,0]>=last-TOL
    frames = first_frame + np.rint((t-origin)*rate).astype(int)
    checked = dict(audit['source_hashes'])
    provenance = [model, motion, loads, force_path, *[p for k,p in paths.items() if k!='so_folder']]
    provenance += [source/n for n in ('input_audit.json','single_leg_prepared.json','quality.json','validated_muscle_curves.csv',
                    'SingleLeg_StaticOptimization_force.sto','SingleLeg_StaticOptimization_activation.sto')]
    # The SO handoff hashes bind the contact audit and wrench to the original C3D/ENF.
    assert str(paths['contact_audit']) in checked and str(paths['full_wrenches']) in checked
    for path in provenance:
        if str(path) not in checked:
            checked[str(path)] = sha(path)
    check_hashes(checked, cache)
    record = dict(trial=trial, stance=stance, side=side, plate=plate, so_folder=str(source),
                  original_20N_window_s=target_event, SO_frames=[int(frames[0]),int(frames[-1])],
                  tool_window_s=[start,last], expected_native_window_s=[start+WARMUP,last-DT],
                  before_window_initialization=bool(context_verified), expected_comparison_times=eligible.tolist(),
                  original_first_frame=first_frame, time_origin_s=origin, point_rate_hz=rate, source_sha256=checked)
    return dict(record=record, source=source, model=model, motion=motion, loads=tree,
                fn=fn, force=f[f[:,0]<=last+TOL], id_reference=paths['id_reference'])


def write_xml(tree, path):
    ET.indent(tree)
    tree.write(path, encoding='utf-8', xml_declaration=True)


def prepare(job, out, templates):
    r = job['record']
    model = ET.parse(job['model'])
    (out/'Geometry').mkdir(parents=True)
    for el in model.iter('mesh_file'):
        mesh = (job['model'].parent/el.text.strip()).resolve()
        if not mesh.is_file():
            mesh = job['model'].parent/'Geometry'/mesh.name
        if not mesh.is_file():
            raise FileNotFoundError(mesh)
        r['source_sha256'][str(mesh)] = sha(mesh)
        shutil.copy2(mesh, out/'Geometry'/mesh.name)
        el.text = mesh.name
    write_xml(model, out/'single_leg_prescribed.osim')
    shutil.copy2(job['motion'], out/'single_leg_motion.mot')
    with (out/'target_plate.mot').open('w',encoding='utf-8') as f:
        f.write(f'TargetPlate\nnRows={len(job["force"])}\nnColumns={len(job["fn"])}\ninDegrees=no\nendheader\n')
        f.write('\t'.join(job['fn'])+'\n')
        np.savetxt(f,job['force'],fmt='%.15g',delimiter='\t')
    job['loads'].find('.//datafile').text='target_plate.mot'
    write_xml(job['loads'],out/'target_loads.xml')
    m=osim.Model(str(out/'single_leg_prescribed.osim')); s=m.initSystem(); cs=m.getCoordinateSet()
    side=r['side'].lower()
    active={cs.get(i).getName() for i in range(cs.getSize()) if not cs.get(i).isConstrained(s)}
    expect={name+'_'+side for name in ('hip_flexion','hip_adduction','hip_rotation','knee_angle','ankle_angle','subtalar_angle')}
    assert active==expect and all(cs.get(n).isPrescribed(s) for n in PELVIS)
    muscles={m.getMuscles().get(i).getName() for i in range(m.getMuscles().getSize())}
    assert len(muscles)==46 and all(n.endswith('_'+side) for n in muscles)
    reserves={m.getForceSet().get(i).getName() for i in range(m.getForceSet().getSize())}-muscles
    assert reserves=={'reserve_'+n for n in active}
    tasks=ET.parse(templates/'gait2392_CMC_Tasks.xml'); objects=tasks.find('./rdCMC_TaskSet/objects')
    for el in list(objects):
        if el.findtext('coordinate').strip() not in active:
            objects.remove(el)
    assert {el.findtext('coordinate').strip() for el in objects}==active
    write_xml(tasks,out/'CMC_Tasks.xml')
    constraints=ET.parse(templates/'gait2392_CMC_ControlConstraints.xml'); objects=constraints.find('./ControlSet/objects')
    for el in list(objects):
        n=el.get('name').removesuffix('.excitation')
        if n in muscles:
            continue
        if n.endswith('_reserve') and n.removesuffix('_reserve') in active:
            el.set('name','reserve_'+n.removesuffix('_reserve')+'.excitation')
        else:
            objects.remove(el)
    assert {el.get('name') for el in objects}=={n+'.excitation' for n in muscles|reserves}
    write_xml(constraints,out/'CMC_ControlConstraints.xml')
    setup=ET.parse(templates/'subject01_Setup_CMC.xml'); tool=setup.find('CMCTool'); tool.set('name',PREFIX)
    for key,value in dict(model_file='single_leg_prescribed.osim',force_set_files='',results_directory='results',
                          initial_time=r['tool_window_s'][0],final_time=r['tool_window_s'][1],external_loads_file='target_loads.xml',
                          desired_kinematics_file='single_leg_motion.mot',task_set_file='CMC_Tasks.xml',
                          constraints_file='CMC_ControlConstraints.xml',lowpass_cutoff_frequency=-1,cmc_time_window=DT).items():
        tool.find(key).text=str(value)
    for el in tool.findall('./AnalysisSet/objects/*/step_interval'):
        el.text='1'
    write_xml(setup,out/'CMC_Setup.xml')
    r.update(active_coordinates=sorted(active),muscles=sorted(muscles),prepared_model_sha256=sha(out/'single_leg_prescribed.osim'))
    save(out/'input_audit.json',r)
    return out/'CMC_Setup.xml'


def review(job, out, log):
    r=job['record']; src=job['source']; native=out/'results'
    sc,states,_=table(native/f'{PREFIX}_states.sto')
    fc,force,_=table(native/f'{PREFIX}_Actuation_force.sto')
    qc,q,deg=table(native/f'{PREFIX}_Kinematics_q.sto')
    table(native/f'{PREFIX}_controls.sto')
    assert (native/f'{PREFIX}_controls.xml').stat().st_size>0
    ic,ik,ikdeg=table(out/'single_leg_motion.mot')
    start,end=states[[0,-1],0]
    assert deg and ikdeg and np.allclose([start,end],r['expected_native_window_s'],atol=1e-6,rtol=0)
    assert all(np.allclose(a[[0,-1],0],[start,end],atol=1e-6,rtol=0) for a in (q,force))
    source=pd.read_csv(src/'validated_muscle_curves.csv')
    assert source.groupby('time_s').size().eq(46).all()
    valid=source.groupby('time_s').valid.all()
    grid=np.asarray(r['expected_comparison_times'])
    times=valid.index[valid].to_numpy()
    times=times[np.isin(times.round(8),grid.round(8))]
    if len(times)<3:
        raise ValueError('Fewer than three valid SO/CMC comparison samples')
    frames=r['original_first_frame']+np.rint((times-r['time_origin_s'])*r['point_rate_hz']).astype(int)
    lo,hi=r['original_20N_window_s']; phase=100*(times-lo)/(hi-lo)
    sa,soact,_=table(src/'SingleLeg_StaticOptimization_activation.sto')
    sf,soforce,_=table(src/'SingleLeg_StaticOptimization_force.sto')
    idc,idd,_=table(job['id_reference'])
    assert all(np.isin(times.round(8),a[:,0].round(8)).all() for a in (soact,soforce,idd))
    curves=[]; muscles=[]; reserves=[]; track=[]
    peak=lambda x:float(np.max(np.abs(x)))
    rms=lambda x:float(np.sqrt(np.mean(x*x)))
    for name in r['muscles']:
        label,=[x for x in sc if x.endswith('/'+name+'/activation')]
        ca=signal(sc,states,label,times); cf=signal(fc,force,name,times)
        oa=signal(sa,soact,name,times); of=signal(sf,soforce,name,times)
        for t,f,p,a,b,c,d in zip(times,frames,phase,oa,ca,of,cf):
            curves.append(dict(time_s=t,original_frame=f,original_stance_percent=p,muscle=name,
                               SO_activation=a,CMC_activation=b,SO_force_N=c,CMC_force_N=d))
        muscles.append(dict(muscle=name,SO_peak_N=float(of.max()),CMC_peak_N=float(cf.max()),force_RMSE_N=rms(cf-of),
                            SO_peak_activation_percent=float(100*oa.max()),CMC_peak_activation_percent=float(100*ca.max()),
                            activation_RMSE_pp=rms((ca-oa)*100),SO_99p99_samples=int(np.sum(oa>=.9999)),
                            CMC_99p99_samples=int(np.sum(ca>=.9999))))
    for name in r['active_coordinates']:
        d=signal(qc,q,name,times)-signal(ic,ik,name,times)
        track.append(dict(coordinate=name,unit='deg',RMS=rms(d),peak=peak(d)))
        key='reserve_'+name; x=signal(fc,force,key,times); y=signal(sf,soforce,key,times)
        ref=peak(signal(idc,idd,name+'_moment',times))
        reserves.append(dict(coordinate=name,CMC_peak_Nm=peak(x),CMC_RMS_Nm=rms(x),SO_peak_Nm=peak(y),ID_peak_Nm=ref,
                             CMC_percent_ID=100*peak(x)/ref if ref>1 else None,
                             CMC_peak_frame=int(frames[np.argmax(abs(x))])))
    m=osim.Model(str(out/'single_leg_prescribed.osim')); cs=m.getCoordinateSet(); pelvis={}
    for name in sorted(PELVIS):
        f=cs.get(name).getPrescribedFunction()
        ref=np.array([f.calcValue(osim.Vector(1,float(t))) for t in q[:,0]])
        is_translation=name in TRANSLATIONS
        diff=(q[:,qc.index(name)]-ref)*1000 if is_translation else q[:,qc.index(name)]-np.rad2deg(ref)
        pelvis[name]=peak(diff)
        assert pelvis[name] < (.1 if is_translation else .01), name
    for name,rows in [('same_window_curves.csv',curves),('muscle_comparison.csv',muscles),('reserve_comparison.csv',reserves),('tracking_errors.csv',track)]:
        pd.DataFrame(rows).to_csv(out/name,index=False,encoding='utf-8-sig')
    ratio_flags=[x for x in reserves if x['CMC_percent_ID'] is not None and x['CMC_percent_ID']>=10]
    track_flags=[x for x in track if x['RMS']>(1 if x['coordinate'].startswith(('ankle','subtalar')) else 2)]
    saturated=[x['muscle'] for x in muscles if x['CMC_99p99_samples']]
    so_gaps=grid[~np.isin(grid.round(8),times.round(8))]
    summary=dict(native_window_s=[float(start),float(end)],common_samples=len(times),
                 common_frames=[int(frames[0]),int(frames[-1])],common_phase_percent=[float(phase[0]),float(phase[-1])],
                 common_SO_missing_frames=(r['original_first_frame']+np.rint((so_gaps-r['time_origin_s'])*r['point_rate_hz']).astype(int)).tolist(),
                 CMC_saturated_muscles=saturated,SO_saturated_muscles=[x['muscle'] for x in muscles if x['SO_99p99_samples']],
                 reserve_flags=ratio_flags,tracking_flags=track_flags,pelvis_prescription_peak=pelvis,
                 reserves=reserves,tracking=track,minimum_excitation_resets=log.count('has been reset to 0.02'),
                 six_muscles=[x for x in muscles if x['muscle'][:-2] in CALF])
    save(out/'comparison.json',summary)
    return summary


def execute(job, out, template, cli, timeout):
    r=job['record']; path=out/'cases'/r['trial']/r['stance']
    setup=prepare(job,path,template)
    return solve(job, path, setup, cli, timeout)


def solve(job, path, setup, cli, timeout):
    r=job['record']
    env=os.environ.copy();env['PATH']=str(cli.parent)+os.pathsep+env.get('PATH','')
    began=time.monotonic()
    with (path/'CMC_console.log').open('w',encoding='utf-8') as log_file:
        with subprocess.Popen([str(cli),'run-tool',setup.name],cwd=path,env=env,stdout=log_file,stderr=subprocess.STDOUT) as proc:
            save(path/'run_status.json',dict(status='running',pid=proc.pid))
            try:
                code=proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill();proc.wait()
                raise RuntimeError(f'Native CMC timed out at {timeout} s')
    log=(path/'CMC_console.log').read_text(encoding='utf-8',errors='replace')
    if code or 'Finished tracking the specified kinematics.' not in log or re.search(r'\[error\]|OPTIMIZATION FAILED',log,re.I):
        msg=' | '.join(line.strip() for line in log.splitlines()[-14:] if line.strip())
        raise RuntimeError(f'CMC stopped (exit {code}): {msg}')
    check_hashes(r['source_sha256'])
    result=review(job,path,log)
    status=dict(status='completed',seconds=time.monotonic()-began,output=str(path),**result)
    save(path/'run_status.json',status)
    return status


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('config',type=Path)
    parser.add_argument('--check-only',action='store_true');args=parser.parse_args()
    base=args.config.resolve().parent;cfg=read(args.config)
    outer = cfg.get('outer_search', False)
    if outer:
        from export_quality_dashboard import check_dashboard_runtime, export_dashboard
        from outer_search import execute_search, configure_strength, write_batch_metrics
        strength = configure_strength(cfg, base)
        dashboard_runtime = check_dashboard_runtime()
    out=(base/cfg['output']).resolve();cli=(base/cfg['opensim_cmd']).resolve(strict=True)
    templates=(base/cfg['gait2392_templates']).resolve(strict=True)
    assert '4.5' in subprocess.check_output([str(cli),'-V'],text=True) and '4.5' in osim.GetVersionAndDate()
    workers=cfg.get('workers',1);timeout=cfg.get('timeout_s',420)
    assert isinstance(workers,int) and 1<=workers<=int(os.environ.get('NUMBER_OF_PROCESSORS','1')) and timeout>0
    hashes={str(p):sha(p) for p in [templates/n for n in ('gait2392_CMC_Tasks.xml','gait2392_CMC_ControlConstraints.xml','subject01_Setup_CMC.xml')]}
    osim.Logger.removeFileSink();osim.Logger.setLevelString('error')
    exclusions=cfg.get('exclude_trial_prefixes',[])
    assert isinstance(exclusions,list) and all(isinstance(x,str) and x.strip() for x in exclusions)
    excluded_prefixes=tuple(x.casefold() for x in exclusions)
    jobs=[];rows=[];cache={};seen=set()
    for case in cfg['cases']:
        key=case['trial'],case['stance']
        identity=tuple(part.casefold() for part in key)
        assert identity not in seen and all(re.fullmatch(r'[A-Za-z0-9_-]+',part) for part in key)
        seen.add(identity)
        if identity[0].startswith(excluded_prefixes):
            rows.append(dict(trial=key[0],stance=key[1],status='excluded_by_config'));continue
        source=(base/case['so_folder']).resolve()
        if out==source or source in out.parents or out in source.parents:
            raise ValueError('Keep the CMC output tree separate from the prepared SO input tree')
        try:
            job=preflight(case,base,cache);job['record']['source_sha256'].update(hashes);jobs.append(job)
        except Exception as error:
            rows.append(dict(trial=key[0],stance=key[1],side=case['side'],status='blocked',reason=repr(error)))
    print('Preflight',len(jobs),'ready',[(r['trial'],r.get('reason')) for r in rows],flush=True)
    if args.check_only:
        return 2 if any(r['status']=='blocked' for r in rows) else 0
    out.mkdir(parents=True,exist_ok=False)
    save(out/'preflight.json',dict(ready=[{k:v for k,v in job['record'].items() if k!='source_sha256'} for job in jobs],not_started=rows))
    shutil.copy2(args.config,out/'requested_config.json')
    with ThreadPoolExecutor(max_workers=workers) as pool:
        if outer:
            pending={pool.submit(execute_search,job,out,templates,cli,timeout,strength):job['record'] for job in jobs}
        else:
            pending={pool.submit(execute,job,out,templates,cli,timeout):job['record'] for job in jobs}
        for future in as_completed(pending):
            r=pending[future];ident={k:r[k] for k in ('trial','stance','side','plate','SO_frames','before_window_initialization')}
            try:
                result=future.result()
            except Exception as error:
                path=out/'cases'/r['trial']/r['stance']
                result=dict(status='failed',reason=repr(error),output=str(path))
                if path.is_dir():
                    save(path/'run_status.json',result)
            rows.append({**ident,**result})
            save(out/'batch_status.json',dict(status='running',total=len(cfg['cases']),rows=rows))
            print(len(rows),len(cfg['cases']),r['trial'],r['stance'],result['status'],result.get('reason',''),flush=True)
    rows.sort(key=lambda r:(r['trial'].lower(),r['stance']))
    counts=dict(Counter(r['status'] for r in rows))
    final = dict(status='finished',total=len(cfg['cases']),counts=counts,rows=rows)
    if outer:
        final['quality_workbook'] = {'status':'pending'}
    save(out/'batch_status.json',final)
    export_failed = False
    if outer:
        write_batch_metrics(out, rows)
        try:
            dashboard = export_dashboard(out, dashboard_runtime)
            final['quality_workbook'] = {'status':'completed','output':str(dashboard)}
            print('CMC quality workbook', dashboard, flush=True)
        except (OSError, subprocess.SubprocessError) as error:
            export_failed = True
            final['quality_workbook'] = {'status':'failed','reason':str(error)}
            print('CMC quality workbook failed', error, flush=True)
        save(out/'batch_status.json',final)
    print('Finished',counts,flush=True)
    return 2 if counts.get('blocked') or counts.get('failed') or export_failed else 0


if __name__=='__main__':
    raise SystemExit(main())
